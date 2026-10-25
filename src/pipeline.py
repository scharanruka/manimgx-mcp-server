"""Project-level batch rendering, shared by the tools and the builder."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import db
from config import BATCH_BUDGET_SECONDS
from lint import find_scene_class_name
from models import BatchReport, BatchSceneResult
from paths import PathSecurityError, project_dir, resolve_project_path
from render import run_worker


def _last_line(text: str) -> str:
    return next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")


def run_project_batch(
    project_id: str,
    scene_ids: list[str] | None,
    mode: Literal["preview", "final"],
    *,
    force: bool = False,
) -> BatchReport:
    """Render many scenes in one worker process."""
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
