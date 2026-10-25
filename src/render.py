"""ManimGX preview rendering wrapper."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import BATCH_TIMEOUT_SECONDS, RENDER_STDOUT_TAIL, RENDER_TIMEOUT_SECONDS
from models import ProjectConfig, RenderReport
from paths import project_dir, project_paths, scene_file

_RENDER_RE = re.compile(r"(\d+(?:\.\d+)?) s,\s*(\d+)x(\d+) at (\d+) fps")


def _tail(text: str, size: int) -> str:
    return text[-size:] if text else ""


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _run(command: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=RENDER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return (
            124,
            _as_text(exc.stdout),
            f"render timed out after {RENDER_TIMEOUT_SECONDS}s\n{_as_text(exc.stderr)}",
        )
    except OSError as exc:
        return 127, "", f"could not start manimgx render: {exc}"
    return completed.returncode, completed.stdout, completed.stderr


def _storyboard_paths(directory: Path, scene_id: str) -> list[str]:
    if not directory.is_dir():
        return []
    prefix = f"{scene_id}.storyboard"
    matches = [
        path
        for path in directory.glob(f"{prefix}*.png")
        if path.stem == prefix or path.stem.startswith(f"{prefix}-")
    ]
    return [str(path) for path in sorted(matches)]


def run_preview(
    project_id: str,
    scene_id: str,
    class_name: str,
    config: ProjectConfig,
) -> RenderReport:
    """Render a scene at preview resolution with the project's preview settings."""
    base = project_dir(project_id)
    paths = project_paths(project_id)
    scene = scene_file(project_id, scene_id).relative_to(base)
    output = Path(paths.preview_dir) / f"{scene_id}.mp4"
    relative_output = output.relative_to(base)

    output.unlink(missing_ok=True)
    preview_dir = Path(paths.preview_dir)
    for path in _storyboard_paths(preview_dir, scene_id):
        Path(path).unlink(missing_ok=True)

    command = [
        sys.executable,
        "-m",
        "manimgx",
        "render",
        str(scene),
        class_name,
        "-o",
        str(relative_output),
        "-r",
        f"{config.preview_width}x{config.preview_height}",
        "--fps",
        str(config.preview_fps),
        "--preset",
        config.preview_preset,
        "--crf",
        f"{config.preview_crf:g}",
    ]

    exit_code, stdout, stderr = _run(command, base)
    match = _RENDER_RE.search(stdout)
    duration = float(match.group(1)) if match else None
    width = int(match.group(2)) if match else config.preview_width
    height = int(match.group(3)) if match else config.preview_height
    fps = int(match.group(4)) if match else config.preview_fps
    ok = exit_code == 0 and output.is_file() and output.stat().st_size > 0

    return RenderReport(
        ok=ok,
        exit_code=exit_code,
        video_path=str(output) if ok else None,
        duration_seconds=duration,
        width=width,
        height=height,
        fps=fps,
        storyboard_paths=_storyboard_paths(preview_dir, scene_id),
        stdout=_tail(stdout, RENDER_STDOUT_TAIL),
        stderr=_tail(stderr, RENDER_STDOUT_TAIL),
    )


@dataclass
class WorkerRun:
    """Raw result of one batch worker process."""

    results: list[dict[str, Any]] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    exit_code: int = 1
    stdout: str = ""
    stderr: str = ""
    job_path: Path | None = None
    result_path: Path | None = None
    timed_out: bool = False


def run_worker(
    project: Path,
    jobs: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    timeout: float = BATCH_TIMEOUT_SECONDS,
) -> WorkerRun:
    """Run all jobs in one batch_worker process and read its result JSON."""
    logs = project / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = uuid.uuid4().hex[:8]
    job_path = logs / f"batch-{stamp}.json"
    result_path = logs / f"batch-{stamp}.results.json"

    payload = {
        "project_dir": str(project),
        **settings,
        "jobs": jobs,
    }
    job_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    command = [
        sys.executable,
        str(Path(__file__).with_name("batch_worker.py")),
        str(job_path),
        str(result_path),
    ]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=project,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = _as_text(exc.stdout)
        stderr = f"batch render timed out after {timeout:g}s\n{_as_text(exc.stderr)}"
        timed_out = True
    except OSError as exc:
        exit_code = 127
        stdout = ""
        stderr = f"could not start batch worker: {exc}"
        timed_out = False

    elapsed = time.perf_counter() - started
    results: list[dict[str, Any]] = []
    if result_path.is_file():
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("results"), list):
                results = data["results"]
        except (OSError, ValueError):
            pass

    return WorkerRun(
        results=results,
        elapsed_seconds=round(elapsed, 3),
        exit_code=exit_code,
        stdout=_tail(stdout, RENDER_STDOUT_TAIL),
        stderr=_tail(stderr, RENDER_STDOUT_TAIL),
        job_path=job_path,
        result_path=result_path,
        timed_out=timed_out,
    )
