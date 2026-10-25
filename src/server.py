"""FastMCP entry point for the ManimGX MCP server."""

from __future__ import annotations

import hashlib
import logging
import math
import shutil
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastmcp import Context, FastMCP
from mcp_types import ElicitRequest, ElicitRequestFormParams, InputRequiredResult

import cleanup
import db
import post
from builder import build_project, export_existing
from inspection import run_inspect
from lint import find_scene_class_name, lint_scene
from models import (
    AspectChoice,
    BatchReport,
    BuildResult,
    ClipCountChoice,
    ExportReport,
    FrameReport,
    InspectReport,
    LayoutIssue,
    LintIssue,
    LintReport,
    MpvReport,
    ProjectConfig,
    ProjectSummary,
    RenderReport,
    SceneDetail,
    SceneSummary,
    StartRequest,
    StartResult,
    Storyboard,
    apply_theme,
)
from paths import (
    PathSecurityError,
    atomic_write_text,
    create_project_dir,
    delete_artifact_files,
    export_file,
    new_project_id,
    project_dir,
    project_paths,
    resolve_project_path,
    scene_file,
)
from pipeline import run_project_batch
from prompts import (
    build_scene_edit_prompt,
    build_scene_generate_prompt,
    build_storyboard_plan_prompt,
)
from render import run_preview

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: Any) -> AsyncIterator[dict[str, Any]]:
    """Sweep stale state on start and remove this session on disconnect."""
    owner = cleanup.session_id()
    cleanup.sweep_orphans()
    cleanup.sweep_exports()
    logger.info("session %s started", owner)
    try:
        yield {"session_id": owner}
    finally:
        removed = cleanup.cleanup_session(owner)
        logger.info("session %s closed; removed %d ephemeral project(s)", owner, len(removed))


mcp = FastMCP(
    name="manimgx",
    instructions=(
        "Generate short technical animation clips with ManimGX. "
        "Use Typst for all typesetting; never use LaTeX or ManimCE."
    ),
    lifespan=_lifespan,
)


def _create_project(
    name: str,
    aspect_ratio: Literal["9:16", "16:9"] = "9:16",
    theme: dict[str, Any] | None = None,
    *,
    brief: str | None = None,
    clip_count: int | None = None,
    ephemeral: bool = False,
) -> ProjectSummary:
    """Create a project directory and its database row."""
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
        db.create_project_record(
            project_id,
            clean_name,
            config,
            brief=brief,
            clip_count=clip_count,
            session_id=cleanup.session_id() if ephemeral else None,
            ephemeral=ephemeral,
        )
    except BaseException:
        shutil.rmtree(project_dir(project_id), ignore_errors=True)
        raise

    logger.info("created project %s", project_id)
    return db.get_project_summary(project_id)


@mcp.tool
def project_create(
    name: str,
    aspect_ratio: Literal["9:16", "16:9"] = "9:16",
    theme: dict[str, Any] | None = None,
) -> ProjectSummary:
    """Create an animation project and return its paths and config."""
    return _create_project(name, aspect_ratio, theme)


def _missing_schema(aspect_missing: bool, clip_missing: bool) -> dict[str, Any]:
    """Return the elicitation schema for whichever start parameters are missing."""
    if aspect_missing and clip_missing:
        return StartRequest.model_json_schema()
    if aspect_missing:
        return AspectChoice.model_json_schema()
    return ClipCountChoice.model_json_schema()


@mcp.tool
async def project_start(
    brief: str,
    aspect_ratio: Literal["9:16", "16:9"] | None = None,
    clip_count: int | None = None,
    style: str = "technical",
    theme: dict[str, Any] | None = None,
    ctx: Context = None,  # type: ignore[assignment]
) -> StartResult | InputRequiredResult:
    """Start a project, asking the user for any missing format parameters."""
    clean_brief = brief.strip()
    if not clean_brief:
        raise ValueError("brief must not be empty")
    if clip_count is not None and not 1 <= clip_count <= 12:
        raise ValueError("clip_count must be between 1 and 12")

    responses = ctx.input_responses if ctx is not None else None
    answered = False
    if responses and "start" in responses:
        answered = True
        payload = getattr(responses["start"], "content", None) or {}
        choice = payload.get("aspect_ratio")
        if choice in {"9:16", "16:9"}:
            aspect_ratio = choice
        count = payload.get("clip_count")
        if isinstance(count, int) and 1 <= count <= 12:
            clip_count = count
        elif isinstance(count, str) and count.isdigit() and 1 <= int(count) <= 12:
            clip_count = int(count)

    aspect_missing = aspect_ratio is None
    clip_missing = clip_count is None
    if aspect_missing or clip_missing:
        if ctx is not None and not answered:
            return InputRequiredResult(
                input_requests={
                    "start": ElicitRequest(
                        params=ElicitRequestFormParams(
                            message=(
                                "Which aspect ratio and how many clips should this animation have?"
                            ),
                            requestedSchema=_missing_schema(aspect_missing, clip_missing),
                        )
                    )
                },
                request_state="{}",
            )
        return StartResult(
            status="needs_input",
            brief=clean_brief,
            clip_count=clip_count,
            missing=[
                name
                for name, missing in (
                    ("aspect_ratio", aspect_missing),
                    ("clip_count", clip_missing),
                )
                if missing
            ],
            defaults={
                "aspect_ratio": aspect_ratio or "9:16",
                "clip_count": clip_count or 3,
            },
            question="Which aspect ratio and how many clips should this animation have?",
            next="call project_start again with aspect_ratio and clip_count",
        )

    project = _create_project(
        clean_brief[:80],
        aspect_ratio,
        theme,
        brief=clean_brief,
        clip_count=clip_count,
        ephemeral=True,
    )
    plan_prompt = build_storyboard_plan_prompt(
        project.project_id, clean_brief, clip_count, style=style
    )
    return StartResult(
        status="started",
        brief=clean_brief,
        project=project,
        clip_count=clip_count,
        plan_prompt=plan_prompt,
        next=(
            "save the plan with storyboard_save, write the scene code, then call "
            "project_build(project_id, code=...) for the video and code.py"
        ),
    )


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


@mcp.tool
def scene_write(project_id: str, scene_id: str, code: str) -> SceneSummary:
    """Write scene source, reset its status, and clear stale artifacts."""
    previous = db.get_scene_row(project_id, scene_id)
    path = scene_file(project_id, scene_id)
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()

    atomic_write_text(path, code)
    try:
        result = db.update_scene_code(project_id, scene_id, code, code_hash)
    except BaseException:
        if previous["code"] is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_text(path, previous["code"])
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
        config = db.get_project_config(project_id)
        report = run_inspect(
            project_id,
            scene_id,
            class_name,
            times,
            width=config.preview_width,
            height=config.preview_height,
        )
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


@mcp.tool
def project_render_batch(
    project_id: str,
    scene_ids: list[str] | None = None,
    mode: Literal["preview", "final"] = "preview",
) -> BatchReport:
    """Render many scenes in one worker process."""
    report = run_project_batch(project_id, scene_ids, mode)
    logger.info(
        "batch %s: %d/%d scenes in %.2fs",
        mode,
        sum(1 for result in report.results if result.ok),
        report.scene_count,
        report.elapsed_seconds,
    )
    return report


@mcp.tool
def project_build(
    project_id: str,
    code: str | None = None,
    review: bool = False,
    inspect: bool = False,
    force: bool = False,
) -> BuildResult:
    """Build scenes, stitch the video, and return clips plus code.py."""
    report = build_project(project_id, code=code, review=review, inspect=inspect, force=force)
    logger.info(
        "build %s: %s in %.2fs",
        project_id,
        "ok" if report.ok else "failed",
        report.elapsed_seconds,
    )
    return report


@mcp.tool
def project_export(project_id: str) -> BuildResult:
    """Return the finished bundle: video, code.py, storyboard, and report."""
    return export_existing(project_id)


@mcp.tool
def scene_render_final(project_id: str, scene_id: str, force: bool = False) -> RenderReport:
    """Render approved final video at 1080p60."""
    batch = run_project_batch(project_id, [scene_id], "final", force=force)
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


@mcp.tool
def scene_review(
    project_id: str,
    scene_id: str,
    decision: Literal["approve", "reject"],
    reason: str | None = None,
) -> SceneSummary:
    """Approve or reject a previewed scene."""
    if decision not in {"approve", "reject"}:
        raise ValueError(f"invalid review decision: {decision}")
    summary = db.get_scene_summary(project_id, scene_id)
    if decision == "approve":
        if summary.status not in {"inspected", "preview_rendered"}:
            raise ValueError(
                f"scene status {summary.status!r} cannot be approved; inspect or preview first"
            )
        result = db.review_scene(project_id, scene_id, "approved")
        logger.info("approved %s for %s", scene_id, project_id)
        return result

    rejection = (reason or "").strip()
    if not rejection:
        raise ValueError("reject requires a reason")
    if len(rejection) > 2000:
        raise ValueError("rejection reason must be at most 2000 characters")
    if summary.status not in {"inspected", "preview_rendered", "approved"}:
        raise ValueError(f"scene status {summary.status!r} cannot be rejected")
    result = db.review_scene(project_id, scene_id, "rejected", rejection)
    logger.info("rejected %s for %s", scene_id, project_id)
    return result


def _artifact_video(
    project_id: str, detail: SceneDetail, mode: Literal["preview", "final"]
) -> Path | None:
    key = "final_video" if mode == "final" else "preview_video"
    value = detail.artifacts.get(key)
    if not isinstance(value, str):
        return None
    try:
        path = resolve_project_path(project_id, value)
    except (PathSecurityError, ValueError) as exc:
        logger.warning("unsafe artifact path for %s: %s", detail.scene_id, exc)
        return None
    if not path.is_file() or path.stat().st_size == 0:
        return None
    return path


@mcp.tool
def project_stitch(
    project_id: str,
    output_name: str = "final.mp4",
    scene_ids: list[str] | None = None,
) -> ExportReport:
    """Stitch final-rendered clips into one MP4."""
    output = export_file(project_id, output_name)

    if scene_ids is None:
        ids = [
            scene.scene_id
            for scene in db.list_scene_summaries(project_id)
            if scene.status == "final_rendered" and scene.artifacts.get("final_video")
        ]
    else:
        ids = list(scene_ids)
        if not ids:
            raise ValueError("scene_ids must not be empty")
        if len(ids) != len(set(ids)):
            raise ValueError("scene_ids must not contain duplicates")

    if not ids:
        return ExportReport(
            ok=False,
            scene_ids=[],
            error="no final-rendered scenes to stitch",
        )

    videos: list[Path] = []
    missing: list[str] = []
    for scene_id in ids:
        detail = db.get_scene_detail(project_id, scene_id)
        video = _artifact_video(project_id, detail, "final")
        if video is None:
            missing.append(scene_id)
        else:
            videos.append(video)

    if missing:
        return ExportReport(
            ok=False,
            scene_ids=ids,
            error=f"final video missing for: {', '.join(missing)}",
        )

    config = db.get_project_config(project_id)
    report = post.stitch(
        videos,
        ids,
        output,
        Path(project_paths(project_id).logs_dir),
        preset=config.final_preset,
        crf=config.final_crf,
        fps=config.final_fps,
    )
    if report.ok:
        db.record_event(
            project_id,
            "project_stitched",
            {
                "export_path": report.export_path,
                "scene_ids": report.scene_ids,
                "duration_seconds": report.duration_seconds,
                "reencoded": report.reencoded,
            },
        )
        logger.info("stitched %d clips to %s", len(ids), report.export_path)
    return report


@mcp.tool
def scene_extract_frames(project_id: str, scene_id: str, times: list[float]) -> FrameReport:
    """Extract PNG review frames at requested times."""
    if not times:
        raise ValueError("times must not be empty")
    for moment in times:
        if not math.isfinite(moment) or moment < 0:
            raise ValueError("times must be finite non-negative numbers")

    detail = db.get_scene_detail(project_id, scene_id)
    video = _artifact_video(project_id, detail, "final")
    if video is None:
        video = _artifact_video(project_id, detail, "preview")
    if video is None:
        return FrameReport(
            ok=False,
            times=list(times),
            errors=["no preview or final video; render the scene first"],
        )

    output_dir = Path(project_paths(project_id).frames_dir) / scene_id
    report = post.extract_frames(video, output_dir, list(times))
    if report.frame_paths:
        db.record_frames_result(project_id, scene_id, report)
    return report


@mcp.tool
def scene_open_mpv(
    project_id: str,
    scene_id: str,
    mode: Literal["preview", "final"] = "preview",
) -> MpvReport:
    """Open a rendered scene in a detached mpv window."""
    if mode not in {"preview", "final"}:
        raise ValueError(f"invalid video mode: {mode}")
    detail = db.get_scene_detail(project_id, scene_id)
    video = _artifact_video(project_id, detail, mode)
    if video is None:
        return MpvReport(
            ok=False,
            error=f"no {mode} video is recorded for this scene",
        )
    return post.open_mpv(video)


@mcp.prompt
def storyboard_plan_prompt(
    project_id: str,
    brief: str,
    clip_count: int = 6,
    seconds_per_clip: float = 12,
    style: str = "technical",
) -> str:
    """Plan clips as Storyboard JSON with DeepSeek Pro."""
    return build_storyboard_plan_prompt(project_id, brief, clip_count, seconds_per_clip, style)


@mcp.prompt
def scene_generate_prompt(project_id: str, scene_id: str) -> str:
    """Generate one ManimGX scene file with DeepSeek Flash."""
    return build_scene_generate_prompt(project_id, scene_id)


@mcp.prompt
def scene_edit_prompt(
    project_id: str,
    scene_id: str,
    rejection_reason: str | None = None,
) -> str:
    """Build a compact scene repair prompt without chat history."""
    return build_scene_edit_prompt(project_id, scene_id, rejection_reason)


@mcp.resource(
    "manimgx://projects/{project_id}/config",
    name="project_config",
    description="Validated project configuration JSON.",
    mime_type="application/json",
)
def project_config_resource(project_id: str) -> dict[str, Any]:
    return db.get_project_config(project_id).model_dump(mode="json")


@mcp.resource(
    "manimgx://projects/{project_id}/storyboard",
    name="project_storyboard",
    description="Project storyboard JSON.",
    mime_type="application/json",
)
def project_storyboard_resource(project_id: str) -> dict[str, Any]:
    storyboard = db.get_project_summary(project_id).storyboard
    return {} if storyboard is None else storyboard.model_dump(mode="json")


@mcp.resource(
    "manimgx://projects/{project_id}/scenes/{scene_id}/code",
    name="scene_code",
    description="Current scene Python source.",
    mime_type="text/x-python",
)
def scene_code_resource(project_id: str, scene_id: str) -> str:
    return db.get_scene_detail(project_id, scene_id).code or ""


@mcp.resource(
    "manimgx://projects/{project_id}/scenes/{scene_id}/report",
    name="scene_report",
    description="Last lint, inspect, and render reports for a scene.",
    mime_type="application/json",
)
def scene_report_resource(project_id: str, scene_id: str) -> dict[str, Any]:
    detail = db.get_scene_detail(project_id, scene_id)
    keys = (
        "lint",
        "inspect",
        "preview",
        "final",
        "preview_batch",
        "final_batch",
        "frame_report",
    )
    reports = {key: detail.artifacts[key] for key in keys if key in detail.artifacts}
    return {
        "project_id": project_id,
        "scene_id": scene_id,
        "status": detail.status,
        "rejection_reason": detail.rejection_reason,
        "reports": reports,
    }


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
