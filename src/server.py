"""FastMCP entry point for the ManimGX MCP server."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP

import db
from config import BATCH_BUDGET_SECONDS
from inspection import run_inspect
from lint import find_scene_class_name, lint_scene
from models import (
    BatchReport,
    BatchSceneResult,
    InspectReport,
    LayoutIssue,
    LintIssue,
    LintReport,
    ProjectConfig,
    ProjectSummary,
    RenderReport,
    SceneDetail,
    SceneSummary,
    Storyboard,
    apply_theme,
)
from paths import (
    PathSecurityError,
    create_project_dir,
    delete_artifact_files,
    new_project_id,
    project_dir,
    resolve_project_path,
    scene_file,
)
from render import run_preview, run_worker

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="manimgx",
    instructions=(
        "Generate short technical animation clips with ManimGX. "
        "Use Typst for all typesetting; never use LaTeX or ManimCE."
    ),
)


@mcp.tool
def project_create(
    name: str,
    aspect_ratio: Literal["9:16", "16:9"] = "9:16",
    theme: dict[str, Any] | None = None,
) -> ProjectSummary:
    """Create an animation project and return its paths and config."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("project name must not be empty")
    if len(clean_name) > 120:
        raise ValueError("project name must be at most 120 characters")

    config = apply_theme(ProjectConfig.default_for(aspect_ratio), theme)

    project_id = ""
    for _ in range(5):
        project_id = new_project_id(clean_name)
        try:
            create_project_dir(project_id)
        except FileExistsError:
            continue
        break
    else:
        raise RuntimeError("could not allocate a unique project id")

    try:
        db.create_project_record(project_id, clean_name, config)
    except BaseException:
        shutil.rmtree(project_dir(project_id), ignore_errors=True)
        raise

    logger.info("created project %s", project_id)
    return db.get_project_summary(project_id)


@mcp.tool
def project_get(project_id: str) -> ProjectSummary:
    """Get a project with config, storyboard, scenes, and status counts."""
    return db.get_project_summary(project_id)


@mcp.tool
def project_update_theme(project_id: str, theme: dict[str, Any]) -> ProjectSummary:
    """Update validated project theme and render settings."""
    current = db.get_project_summary(project_id)
    config = apply_theme(current.config, theme)
    db.update_project_config(project_id, config)
    logger.info("updated theme for project %s", project_id)
    return db.get_project_summary(project_id)


@mcp.tool
def storyboard_save(project_id: str, storyboard: Storyboard) -> Storyboard:
    """Validate and persist a storyboard and its planned scenes."""
    if storyboard.project_id != project_id:
        raise ValueError("storyboard project_id must match the project_id argument")
    saved = db.save_storyboard(project_id, storyboard)
    logger.info("saved storyboard for %s (%d clips)", project_id, len(saved.clips))
    return saved


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


@mcp.tool
def scene_write(project_id: str, scene_id: str, code: str) -> SceneSummary:
    """Write scene source, reset its status, and clear stale artifacts."""
    previous = db.get_scene_row(project_id, scene_id)
    path = scene_file(project_id, scene_id)
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()

    _atomic_write_text(path, code)
    try:
        result = db.update_scene_code(project_id, scene_id, code, code_hash)
    except BaseException:
        if previous["code"] is None:
            path.unlink(missing_ok=True)
        else:
            _atomic_write_text(path, previous["code"])
        raise

    if result["changed"] and result["stale_artifacts"]:
        try:
            delete_artifact_files(project_id, result["stale_artifacts"])
        except (PathSecurityError, OSError) as exc:
            logger.warning("could not clear stale artifacts for %s: %s", scene_id, exc)

    logger.info("wrote scene %s for %s", scene_id, project_id)
    return db.get_scene_summary(project_id, scene_id)


@mcp.tool
def scene_list(project_id: str) -> list[SceneSummary]:
    """List scenes in storyboard order."""
    return db.list_scene_summaries(project_id)


@mcp.tool
def scene_get(project_id: str, scene_id: str) -> SceneDetail:
    """Get a scene's plan, code, status, and artifacts."""
    return db.get_scene_detail(project_id, scene_id)


@mcp.tool
def scene_lint(project_id: str, scene_id: str) -> LintReport:
    """Lint scene code without executing it."""
    detail = db.get_scene_detail(project_id, scene_id)
    if detail.code is None:
        return LintReport(
            ok=False,
            issues=[
                LintIssue(
                    rule="missing_code",
                    line=1,
                    message="scene has no code yet",
                    fix="call scene_write before scene_lint",
                )
            ],
        )

    report = lint_scene(detail.code, db.get_project_config(project_id))
    db.record_lint_result(project_id, scene_id, report)
    logger.info("lint %s for %s", "passed" if report.ok else "failed", scene_id)
    return report


def _inspect_failure(message: str, exit_code: int = 1) -> InspectReport:
    return InspectReport(
        ok=False,
        exit_code=exit_code,
        stdout="",
        stderr=message,
        issues=[LayoutIssue(message=message)],
    )


@mcp.tool
def scene_inspect(
    project_id: str,
    scene_id: str,
    times: list[float | Literal["end"]] | None = None,
) -> InspectReport:
    """Inspect layout at preview frames without rendering video."""
    detail = db.get_scene_detail(project_id, scene_id)
    if detail.code is None:
        return _inspect_failure("scene has no code yet; call scene_write first")
    class_name = find_scene_class_name(detail.code)
    if class_name is None:
        return _inspect_failure("scene must define exactly one class inheriting m.Scene")

    try:
        report = run_inspect(project_id, scene_id, class_name, times)
    except ValueError as exc:
        return _inspect_failure(str(exc))

    db.record_inspect_result(project_id, scene_id, report)
    logger.info("inspect %s for %s", "passed" if report.ok else "failed", scene_id)
    return report


def _render_failure(config: ProjectConfig, message: str, exit_code: int = 1) -> RenderReport:
    return RenderReport(
        ok=False,
        exit_code=exit_code,
        width=config.preview_width,
        height=config.preview_height,
        fps=config.preview_fps,
        stdout="",
        stderr=message,
    )


@mcp.tool
def scene_render_preview(project_id: str, scene_id: str) -> RenderReport:
    """Render a 480p preview and return its report."""
    detail = db.get_scene_detail(project_id, scene_id)
    config = db.get_project_config(project_id)
    if detail.code is None:
        return _render_failure(config, "scene has no code yet; call scene_write first")
    class_name = find_scene_class_name(detail.code)
    if class_name is None:
        return _render_failure(config, "scene must define exactly one class inheriting m.Scene")

    report = run_preview(project_id, scene_id, class_name, config)
    db.record_preview_result(project_id, scene_id, report)
    logger.info("preview %s for %s", "rendered" if report.ok else "failed", scene_id)
    return report


def _last_line(text: str) -> str:
    return next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")


def _run_batch(
    project_id: str,
    scene_ids: list[str] | None,
    mode: Literal["preview", "final"],
    *,
    force: bool = False,
) -> BatchReport:
    config = db.get_project_config(project_id)
    if scene_ids is None:
        ids = [scene.scene_id for scene in db.list_scene_summaries(project_id)]
    else:
        ids = list(scene_ids)
        if not ids:
            raise ValueError("scene_ids must not be empty")
        if len(ids) != len(set(ids)):
            raise ValueError("scene_ids must not contain duplicates")

    if not ids:
        raise ValueError("project has no scenes; save a storyboard first")

    base = project_dir(project_id)
    jobs: list[dict[str, Any]] = []
    failed: dict[str, BatchSceneResult] = {}

    for scene_id in ids:
        detail = db.get_scene_detail(project_id, scene_id)
        if detail.code is None:
            failed[scene_id] = BatchSceneResult(
                scene_id=scene_id, ok=False, error="scene has no code"
            )
            continue
        class_name = find_scene_class_name(detail.code)
        if class_name is None:
            failed[scene_id] = BatchSceneResult(
                scene_id=scene_id,
                ok=False,
                error="scene must define exactly one class inheriting m.Scene",
            )
            continue
        if (
            mode == "final"
            and not force
            and detail.status
            not in {
                "approved",
                "final_rendered",
            }
        ):
            failed[scene_id] = BatchSceneResult(
                scene_id=scene_id,
                ok=False,
                error="scene is not approved for final rendering",
            )
            continue
        jobs.append(
            {
                "scene_id": scene_id,
                "file": f"scenes/{scene_id}.py",
                "class": class_name,
                "video": f"renders/{mode}/{scene_id}.mp4",
            }
        )

    settings: dict[str, Any]
    if mode == "preview":
        settings = {
            "width": config.preview_width,
            "height": config.preview_height,
            "fps": config.preview_fps,
            "preset": config.preview_preset,
            "crf": config.preview_crf,
            "background_color": config.background_color,
        }
    else:
        settings = {
            "width": config.final_width,
            "height": config.final_height,
            "fps": config.final_fps,
            "preset": config.final_preset,
            "crf": config.final_crf,
            "background_color": config.background_color,
        }

    worker = None
    if jobs:
        worker = run_worker(base, jobs, settings)

    by_scene = dict(failed)
    raw_results = {
        str(item.get("scene_id")): item
        for item in (worker.results if worker else [])
        if isinstance(item, dict)
    }
    for job in jobs:
        scene_id = str(job["scene_id"])
        raw = raw_results.get(scene_id)
        if raw is None:
            suffix = ""
            if worker is not None:
                line = _last_line(worker.stderr)
                suffix = f" (exit {worker.exit_code}{': ' + line if line else ''})"
            by_scene[scene_id] = BatchSceneResult(
                scene_id=scene_id,
                ok=False,
                error=f"batch worker returned no result{suffix}",
            )
            continue

        ok = bool(raw.get("ok"))
        error = raw.get("error")
        video_path = None
        if ok:
            try:
                video_path = str(
                    resolve_project_path(project_id, str(raw.get("video") or job["video"]))
                )
            except (PathSecurityError, ValueError) as exc:
                ok = False
                error = f"worker returned an unsafe video path: {exc}"
            else:
                path = Path(video_path)
                if not path.is_file() or path.stat().st_size == 0:
                    ok = False
                    error = "worker reported success but the video is missing"
                    video_path = None

        duration = raw.get("duration_seconds")
        elapsed = raw.get("elapsed_seconds")
        by_scene[scene_id] = BatchSceneResult(
            scene_id=scene_id,
            ok=ok,
            duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
            video_path=video_path,
            elapsed_seconds=float(elapsed) if isinstance(elapsed, (int, float)) else 0.0,
            error=str(error) if error else None,
        )

    results = [by_scene[scene_id] for scene_id in ids]
    for result in results:
        if result.ok:
            db.record_batch_scene_result(project_id, result.scene_id, mode, result)

    elapsed_seconds = worker.elapsed_seconds if worker else 0.0
    return BatchReport(
        ok=all(result.ok for result in results),
        mode=mode,
        scene_count=len(ids),
        total_seconds=round(
            sum(result.duration_seconds or 0.0 for result in results if result.ok), 3
        ),
        elapsed_seconds=elapsed_seconds,
        budget_seconds=BATCH_BUDGET_SECONDS,
        within_budget=elapsed_seconds <= BATCH_BUDGET_SECONDS,
        results=results,
        job_path=str(worker.job_path) if worker and worker.job_path else "",
        result_path=str(worker.result_path) if worker and worker.result_path else "",
        stdout=worker.stdout if worker else "",
        stderr=worker.stderr if worker else "",
    )


@mcp.tool
def project_render_batch(
    project_id: str,
    scene_ids: list[str] | None = None,
    mode: Literal["preview", "final"] = "preview",
) -> BatchReport:
    """Render many scenes in one worker process."""
    report = _run_batch(project_id, scene_ids, mode)
    logger.info(
        "batch %s: %d/%d scenes in %.2fs",
        mode,
        sum(1 for result in report.results if result.ok),
        report.scene_count,
        report.elapsed_seconds,
    )
    return report


@mcp.tool
def scene_render_final(project_id: str, scene_id: str, force: bool = False) -> RenderReport:
    """Render approved final video at 1080p60."""
    batch = _run_batch(project_id, [scene_id], "final", force=force)
    config = db.get_project_config(project_id)
    result = batch.results[0]
    return RenderReport(
        ok=result.ok,
        exit_code=0 if result.ok else 1,
        video_path=result.video_path,
        duration_seconds=result.duration_seconds,
        width=config.final_width,
        height=config.final_height,
        fps=config.final_fps,
        stdout=batch.stdout,
        stderr=result.error or batch.stderr,
    )


def main() -> None:
    """Run the MCP server over stdio. stdout is reserved for the protocol."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("starting manimgx MCP server")
    mcp.run()


if __name__ == "__main__":
    main()
