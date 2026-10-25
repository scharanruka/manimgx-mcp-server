"""One-call project build: split code, lint, render, stitch, bundle, purge."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import db
import post
from inspection import run_inspect
from lint import find_scene_class_name, lint_scene
from models import (
    BuildResult,
    LintIssue,
    SceneBuildResult,
    Storyboard,
)
from paths import (
    PathSecurityError,
    atomic_write_text,
    delete_artifact_files,
    persistent_export_dir,
    project_paths,
    resolve_project_path,
    scene_file,
)
from pipeline import run_project_batch

_MARKER_RE = re.compile(r"^#\s*manimgx:\s*scene_id\s*=\s*([a-z0-9][a-z0-9_-]{0,63})\s*$")
_SCENE_CLASS_RE = re.compile(r"^Scene(\d+)$")
_SCENE_NAME_RE = re.compile(r"^Scene([A-Za-z0-9]+)$")

BUNDLE_VIDEO = "video.mp4"
BUNDLE_CODE = "code.py"
BUNDLE_STORYBOARD = "storyboard.json"
BUNDLE_REPORT = "report.json"


@dataclass(slots=True)
class SceneSource:
    """One scene class parsed out of a combined ``code.py``."""

    class_name: str
    source: str
    scene_id: str | None = None
    header: str = ""
    body: str = ""


def _is_scene_class(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if (
            isinstance(base, ast.Attribute)
            and isinstance(base.value, ast.Name)
            and base.value.id == "m"
            and base.attr in {"Scene", "ThreeDScene"}
        ):
            return True
    return False


def _marker_above(lines: list[str], lineno: int) -> str | None:
    """Read ``# manimgx: scene_id=...`` from the comments above a class."""
    lowest = max(0, lineno - 5)
    for index in range(lineno - 2, lowest - 1, -1):
        line = lines[index].strip()
        if not line:
            continue
        marker = _MARKER_RE.match(line)
        if marker is not None:
            return marker.group(1)
        if not line.startswith("#"):
            break
    return None


def _derive_scene_id(class_name: str) -> str | None:
    numbered = _SCENE_CLASS_RE.match(class_name)
    if numbered is not None:
        return f"scene_{int(numbered.group(1)):02d}"
    named = _SCENE_NAME_RE.match(class_name)
    if named is not None:
        return f"scene_{named.group(1).lower()}"
    return None


def split_scenes(code: str) -> list[SceneSource]:
    """Split a combined file into self-contained scene sources.

    Module-level imports and constants are repeated in every scene so each file
    can be rendered on its own.
    """
    tree = ast.parse(code)
    header: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            break
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                continue
        segment = ast.get_source_segment(code, node)
        if segment:
            header.append(segment)
    header_text = "\n\n".join(part for part in header if part.strip())
    lines = code.splitlines()

    scenes: list[SceneSource] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or not _is_scene_class(node):
            continue
        body = ast.get_source_segment(code, node)
        if not body:
            continue
        source = f"{header_text}\n\n\n{body}".strip() + "\n" if header_text else body + "\n"
        scenes.append(
            SceneSource(
                class_name=node.name,
                source=source,
                scene_id=_marker_above(lines, node.lineno),
                header=header_text,
                body=body.strip() + "\n",
            )
        )
    return scenes


def _map_scenes(sources: list[SceneSource], order: list[str]) -> dict[str, str]:
    """Map each parsed class to a storyboard scene id."""
    mapping: dict[str, str] = {}
    for index, source in enumerate(sources):
        scene_id = source.scene_id or _derive_scene_id(source.class_name)
        if scene_id is None and index < len(order):
            scene_id = order[index]
        if scene_id is None or scene_id not in order:
            raise ValueError(
                f"class {source.class_name!r} does not match a scene "
                f"(known scenes: {', '.join(order)})"
            )
        if scene_id in mapping:
            raise ValueError(f"more than one class maps to {scene_id!r}")
        mapping[scene_id] = source.source
    return mapping


def _store_scene(project_id: str, scene_id: str, source: str) -> None:
    """Write one scene file and update its stored code, rolling back on failure."""
    code_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    previous = db.get_scene_row(project_id, scene_id)
    if previous["code_hash"] == code_hash:
        return

    path = scene_file(project_id, scene_id)
    atomic_write_text(path, source)
    try:
        result = db.update_scene_code(project_id, scene_id, source, code_hash)
    except BaseException:
        if previous["code"] is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_text(path, previous["code"])
        raise

    if result["changed"] and result["stale_artifacts"]:
        try:
            delete_artifact_files(project_id, result["stale_artifacts"])
        except (PathSecurityError, OSError):
            pass


def _final_video(project_id: str, scene_id: str) -> Path | None:
    detail = db.get_scene_detail(project_id, scene_id)
    value = detail.artifacts.get("final_video")
    if not isinstance(value, str):
        return None
    try:
        path = resolve_project_path(project_id, value)
    except (PathSecurityError, ValueError):
        return None
    return path if path.is_file() and path.stat().st_size > 0 else None


def _needs_render(project_id: str, scene_id: str, force: bool) -> bool:
    if force:
        return True
    video = _final_video(project_id, scene_id)
    if video is None:
        return True
    scene = scene_file(project_id, scene_id)
    return video.stat().st_mtime < scene.stat().st_mtime


def _bundle_code(project_id: str, order: list[str]) -> str:
    """Assemble the editable ``code.py`` that mirrors the rendered scene files."""
    blocks: list[str] = []
    header = ""
    for scene_id in order:
        detail = db.get_scene_detail(project_id, scene_id)
        if detail.code is None:
            continue
        parsed = split_scenes(detail.code)
        if not parsed:
            continue
        if not header:
            header = parsed[0].header
        blocks.append(f"# manimgx: scene_id={scene_id}\n{parsed[0].body}")

    banner = (
        "# Generated by manimgx-mcp.\n"
        "# Edit this file and pass it to project_build(project_id, code=...) to rebuild.\n"
        "# Each block starts with a scene_id marker so classes map back to clips.\n"
    )
    parts = [banner]
    if header:
        parts.append(header + "\n")
    parts.extend(blocks)
    return "\n".join(parts).rstrip() + "\n"


def _write_bundle(
    project_id: str,
    *,
    duration_seconds: float | None,
    elapsed_seconds: float,
    order: list[str],
) -> dict[str, str]:
    """Write the bundle next to the project and copy it to the durable export dir."""
    config = db.get_project_config(project_id)
    paths = project_paths(project_id)
    exports = Path(paths.exports_dir)
    exports.mkdir(parents=True, exist_ok=True)

    storyboard = db.get_project_summary(project_id).storyboard
    storyboard_text = (
        storyboard.model_dump_json(indent=2) if isinstance(storyboard, Storyboard) else "{}"
    )
    atomic_write_text(exports / BUNDLE_STORYBOARD, storyboard_text)

    report = {
        "project_id": project_id,
        "duration_seconds": duration_seconds,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "aspect_ratio": config.aspect_ratio,
        "width": config.final_width,
        "height": config.final_height,
        "fps": config.final_fps,
        "scenes": [
            {
                "scene_id": scene_id,
                "seconds": db.get_scene_summary(project_id, scene_id).rendered_seconds,
            }
            for scene_id in order
        ],
    }
    atomic_write_text(exports / BUNDLE_REPORT, json.dumps(report, indent=2))

    atomic_write_text(exports / BUNDLE_CODE, _bundle_code(project_id, order))

    durable = persistent_export_dir(project_id)
    durable.mkdir(parents=True, exist_ok=True)
    for name in (BUNDLE_VIDEO, BUNDLE_CODE, BUNDLE_STORYBOARD, BUNDLE_REPORT):
        source = exports / name
        if source.is_file():
            shutil.copy2(source, durable / name)

    return {
        "bundle_dir": str(durable),
        "video_path": str(durable / BUNDLE_VIDEO),
        "code_path": str(durable / BUNDLE_CODE),
        "storyboard_path": str(durable / BUNDLE_STORYBOARD),
        "report_path": str(durable / BUNDLE_REPORT),
    }


def purge_intermediates(project_id: str) -> None:
    """Delete previews, storyboards, frames and worker scratch files."""
    paths = project_paths(project_id)
    for directory, patterns in (
        (Path(paths.preview_dir), ("*.mp4",)),
        (Path(paths.storyboards_dir), ("*.png",)),
        (Path(paths.frames_dir), ("**/*.png",)),
        (Path(paths.logs_dir), ("batch-*.json", ".stitch-*.txt", "inspect-*.py")),
    ):
        if not directory.is_dir():
            continue
        for pattern in patterns:
            for file in directory.glob(pattern):
                if file.is_file():
                    file.unlink(missing_ok=True)
    frames_root = Path(paths.frames_dir)
    if frames_root.is_dir():
        for child in frames_root.iterdir():
            if child.is_dir() and not any(child.iterdir()):
                child.rmdir()


def _empty_result(
    project_id: str,
    *,
    ok: bool,
    issues: list[LintIssue] | None = None,
    scenes: list[SceneBuildResult] | None = None,
    elapsed: float = 0.0,
    mode: str = "final",
) -> BuildResult:
    config = db.get_project_config(project_id)
    return BuildResult(
        ok=ok,
        project_id=project_id,
        mode="preview" if mode == "preview" else "final",
        width=config.final_width,
        height=config.final_height,
        fps=config.final_fps,
        issues=issues or [],
        scenes=scenes or [],
        elapsed_seconds=round(elapsed, 3),
    )


def build_project(
    project_id: str,
    *,
    code: str | None = None,
    review: bool = False,
    inspect: bool = False,
    force: bool = False,
) -> BuildResult:
    """Split, lint, render and assemble a project, returning the bundle."""
    started = time.perf_counter()
    config = db.get_project_config(project_id)
    order = [scene.scene_id for scene in db.list_scene_summaries(project_id)]
    if not order:
        return _empty_result(
            project_id,
            ok=False,
            issues=[
                LintIssue(
                    rule="no_scenes",
                    message="project has no scenes",
                    fix="save a storyboard first",
                )
            ],
            elapsed=time.perf_counter() - started,
        )

    if code is not None:
        try:
            sources = split_scenes(code)
        except SyntaxError as exc:
            return _empty_result(
                project_id,
                ok=False,
                issues=[
                    LintIssue(
                        rule="syntax_error",
                        line=getattr(exc, "lineno", None),
                        message=f"code.py is not valid Python: {getattr(exc, 'msg', exc)}",
                        fix="fix the syntax error and build again",
                    )
                ],
                elapsed=time.perf_counter() - started,
            )
        if not sources:
            return _empty_result(
                project_id,
                ok=False,
                issues=[
                    LintIssue(
                        rule="missing_scene_class",
                        message="code.py defines no class inheriting m.Scene",
                        fix="add a class like `class Scene01(m.Scene):`",
                    )
                ],
                elapsed=time.perf_counter() - started,
            )
        try:
            mapping = _map_scenes(sources, order)
        except ValueError as exc:
            return _empty_result(
                project_id,
                ok=False,
                issues=[
                    LintIssue(
                        rule="scene_mapping",
                        message=str(exc),
                        fix="rename the class or add a scene_id marker",
                    )
                ],
                elapsed=time.perf_counter() - started,
            )
        for scene_id, source in mapping.items():
            _store_scene(project_id, scene_id, source)

    changed = {scene_id: _needs_render(project_id, scene_id, force) for scene_id in order}

    scene_results: list[SceneBuildResult] = []
    errors: list[LintIssue] = []
    for scene_id in order:
        detail = db.get_scene_detail(project_id, scene_id)
        if detail.code is None:
            scene_results.append(
                SceneBuildResult(scene_id=scene_id, ok=False, error="scene has no code")
            )
            errors.append(
                LintIssue(
                    rule="missing_code",
                    message=f"{scene_id} has no code",
                    fix="write the scene",
                )
            )
            continue
        report = lint_scene(detail.code, config)
        if changed[scene_id]:
            db.record_lint_result(project_id, scene_id, report)
        warnings = [issue for issue in report.issues if issue.severity == "warning"]
        scene_results.append(
            SceneBuildResult(
                scene_id=scene_id,
                ok=report.ok,
                changed=changed[scene_id],
                status="lint_failed" if not report.ok else "lint_passed",
                warnings=warnings,
                error=None
                if report.ok
                else f"{sum(1 for i in report.issues if i.severity == 'error')} lint error(s)",
            )
        )
        errors.extend(issue for issue in report.issues if issue.severity == "error")

    if errors:
        return _empty_result(
            project_id,
            ok=False,
            issues=errors[:20],
            scenes=scene_results,
            elapsed=time.perf_counter() - started,
        )

    if inspect:
        layout_errors: list[LintIssue] = []
        for scene_id in order:
            detail = db.get_scene_detail(project_id, scene_id)
            class_name = find_scene_class_name(detail.code or "")
            if class_name is None:
                continue
            layout = run_inspect(
                project_id,
                scene_id,
                class_name,
                width=config.preview_width,
                height=config.preview_height,
            )
            for issue in layout.issues:
                if issue.note:
                    continue
                layout_errors.append(
                    LintIssue(
                        rule="layout",
                        line=issue.line,
                        message=f"{scene_id}: {issue.message}",
                        fix="move the content back inside the safe area",
                        severity="error",
                    )
                )
        if layout_errors:
            return _empty_result(
                project_id,
                ok=False,
                issues=layout_errors[:20],
                scenes=scene_results,
                elapsed=time.perf_counter() - started,
            )

    targets = [scene_id for scene_id in order if changed[scene_id]]

    if review:
        batch = run_project_batch(project_id, targets or order, "preview", force=True)
        by_scene = {result.scene_id: result for result in batch.results}
        for result in scene_results:
            preview = by_scene.get(result.scene_id)
            if preview is not None:
                result.video_path = preview.video_path
                result.seconds = preview.duration_seconds
                result.error = preview.error
                result.ok = result.ok and preview.ok
        return BuildResult(
            ok=batch.ok,
            project_id=project_id,
            mode="preview",
            width=config.preview_width,
            height=config.preview_height,
            fps=config.preview_fps,
            scenes=scene_results,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            approval_required=True,
        )

    if targets:
        batch = run_project_batch(project_id, targets, "final", force=True)
        if not batch.ok:
            by_scene = {result.scene_id: result for result in batch.results}
            for result in scene_results:
                if result.scene_id in by_scene:
                    result.error = by_scene[result.scene_id].error
            return _empty_result(
                project_id,
                ok=False,
                issues=[
                    LintIssue(
                        rule="render_failed",
                        message=result.error or "render failed",
                        fix="fix the scene",
                    )
                    for result in batch.results
                    if not result.ok
                ],
                scenes=scene_results,
                elapsed=time.perf_counter() - started,
            )

    videos: list[Path] = []
    for scene_id in order:
        video = _final_video(project_id, scene_id)
        if video is None:
            return _empty_result(
                project_id,
                ok=False,
                issues=[
                    LintIssue(
                        rule="missing_video",
                        message=f"final video missing for {scene_id}",
                        fix="render the scene",
                    )
                ],
                scenes=scene_results,
                elapsed=time.perf_counter() - started,
            )
        videos.append(video)

    output = Path(project_paths(project_id).exports_dir) / BUNDLE_VIDEO
    reuse = (
        not targets
        and output.is_file()
        and all(output.stat().st_mtime >= video.stat().st_mtime for video in videos)
    )
    if reuse:
        duration_seconds = post.probe_duration(output)
    else:
        stitched = post.stitch(
            videos,
            order,
            output,
            Path(project_paths(project_id).logs_dir),
            preset=config.final_preset,
            crf=config.final_crf,
            fps=config.final_fps,
        )
        if stitched.ok:
            duration_seconds = stitched.duration_seconds
    if not reuse and not stitched.ok:
        return _empty_result(
            project_id,
            ok=False,
            issues=[
                LintIssue(
                    rule="stitch_failed",
                    message=stitched.error or "stitch failed",
                    fix="check the rendered clips",
                )
            ],
            scenes=scene_results,
            elapsed=time.perf_counter() - started,
        )

    elapsed = time.perf_counter() - started
    bundle = _write_bundle(
        project_id,
        duration_seconds=duration_seconds,
        elapsed_seconds=elapsed,
        order=order,
    )
    for result in scene_results:
        summary = db.get_scene_summary(project_id, result.scene_id)
        result.seconds = summary.rendered_seconds
        result.status = summary.status
        result.code_path = str(scene_file(project_id, result.scene_id))
        video = _final_video(project_id, result.scene_id)
        result.video_path = str(video) if video is not None else None

    code_text = Path(bundle["code_path"]).read_text(encoding="utf-8")
    purge_intermediates(project_id)

    return BuildResult(
        ok=True,
        project_id=project_id,
        mode="final",
        video_path=bundle["video_path"],
        code_path=bundle["code_path"],
        storyboard_path=bundle["storyboard_path"],
        report_path=bundle["report_path"],
        bundle_dir=bundle["bundle_dir"],
        code=code_text,
        duration_seconds=duration_seconds,
        width=config.final_width,
        height=config.final_height,
        fps=config.final_fps,
        scenes=scene_results,
        elapsed_seconds=round(elapsed, 3),
    )


def export_existing(project_id: str) -> BuildResult:
    """Return the current bundle, stitching it again when the file is missing."""
    started = time.perf_counter()
    config = db.get_project_config(project_id)
    order = [scene.scene_id for scene in db.list_scene_summaries(project_id)]
    if not order:
        return _empty_result(
            project_id,
            ok=False,
            issues=[
                LintIssue(
                    rule="no_scenes",
                    message="project has no scenes",
                    fix="build the project first",
                )
            ],
            elapsed=time.perf_counter() - started,
        )

    videos: list[Path] = []
    for scene_id in order:
        video = _final_video(project_id, scene_id)
        if video is None:
            return _empty_result(
                project_id,
                ok=False,
                issues=[
                    LintIssue(
                        rule="not_built",
                        message=f"final video missing for {scene_id}",
                        fix="call project_build",
                    )
                ],
                elapsed=time.perf_counter() - started,
            )
        videos.append(video)

    output = Path(project_paths(project_id).exports_dir) / BUNDLE_VIDEO
    duration: float | None = None
    if output.is_file():
        duration = post.probe_duration(output)
    else:
        stitched = post.stitch(
            videos,
            order,
            output,
            Path(project_paths(project_id).logs_dir),
            preset=config.final_preset,
            crf=config.final_crf,
            fps=config.final_fps,
        )
        if not stitched.ok:
            return _empty_result(
                project_id,
                ok=False,
                issues=[
                    LintIssue(
                        rule="stitch_failed",
                        message=stitched.error or "stitch failed",
                        fix="check the rendered clips",
                    )
                ],
                elapsed=time.perf_counter() - started,
            )
        duration = stitched.duration_seconds

    elapsed = time.perf_counter() - started
    bundle = _write_bundle(
        project_id,
        duration_seconds=duration,
        elapsed_seconds=elapsed,
        order=order,
    )
    summary = db.get_project_summary(project_id)
    return BuildResult(
        ok=True,
        project_id=project_id,
        mode="final",
        video_path=bundle["video_path"],
        code_path=bundle["code_path"],
        storyboard_path=bundle["storyboard_path"],
        report_path=bundle["report_path"],
        bundle_dir=bundle["bundle_dir"],
        code=Path(bundle["code_path"]).read_text(encoding="utf-8"),
        duration_seconds=duration,
        width=config.final_width,
        height=config.final_height,
        fps=config.final_fps,
        scenes=[
            SceneBuildResult(
                scene_id=scene.scene_id,
                ok=True,
                status=scene.status,
                seconds=scene.rendered_seconds,
            )
            for scene in summary.scenes
        ],
        elapsed_seconds=round(elapsed, 3),
    )
