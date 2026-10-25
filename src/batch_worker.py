"""Single-process batch renderer, imported once per batch."""

from __future__ import annotations

import importlib.util
import json
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import manimgx as m

from config import RENDER_TIMEOUT_SECONDS


def _inside(base: Path, relative: str) -> Path:
    path = (base / relative).resolve()
    if path != base and base not in path.parents:
        raise ValueError(f"path escapes the project: {relative!r}")
    return path


def _load_module(path: Path, scene_id: str) -> Any:
    name = f"_manimgx_batch_{scene_id}"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load scene module: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _error(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    location = ""
    if frames:
        frame = frames[-1]
        location = f" ({Path(frame.filename).name}:{frame.lineno})"
    return f"{type(exc).__name__}: {exc}{location}"


def _timeout(signum: int, frame: object) -> None:
    raise TimeoutError(f"scene render exceeded {RENDER_TIMEOUT_SECONDS}s")


def _configure(settings: dict[str, Any]) -> None:
    m.config.pixel_width = int(settings["width"])
    m.config.pixel_height = int(settings["height"])
    m.config.frame_rate = float(settings["fps"])
    color = settings.get("background_color")
    m.config.background_color = m.ManimColor(color) if color else m.BLACK


def _render_one(project_dir: Path, job: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    scene_id = str(job["scene_id"])
    video = _inside(project_dir, str(job["video"]))
    scene_path = _inside(project_dir, str(job["file"]))
    video.parent.mkdir(parents=True, exist_ok=True)
    video.unlink(missing_ok=True)

    started = time.perf_counter()
    try:
        _configure(settings)
        module = _load_module(scene_path, scene_id)
        scene_class = getattr(module, str(job["class"]))
        if not isinstance(scene_class, type) or not issubclass(scene_class, m.Scene):
            raise TypeError(f"{job['class']} is not a manimgx Scene subclass")
        scene = scene_class()
        if hasattr(signal, "SIGALRM"):
            signal.signal(signal.SIGALRM, _timeout)
            signal.alarm(RENDER_TIMEOUT_SECONDS)
        try:
            scene.render(
                video=str(video),
                preset=str(settings["preset"]),
                crf=float(settings["crf"]),
            )
        finally:
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)
        if not video.is_file() or video.stat().st_size == 0:
            raise RuntimeError("render finished without a video")
        return {
            "scene_id": scene_id,
            "ok": True,
            "duration_seconds": round(float(scene.time), 3),
            "video": str(job["video"]),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error": None,
        }
    except BaseException as exc:
        video.unlink(missing_ok=True)
        return {
            "scene_id": scene_id,
            "ok": False,
            "duration_seconds": None,
            "video": None,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error": _error(exc)[:1000],
        }


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2:
        print("usage: python -m batch_worker JOB_JSON RESULT_JSON", file=sys.stderr)
        return 2

    job_path, result_path = Path(arguments[0]), Path(arguments[1])
    try:
        payload = json.loads(job_path.read_text(encoding="utf-8"))
        project_dir = Path(payload["project_dir"]).resolve()
        settings = payload
        jobs = payload["jobs"]
    except Exception as exc:
        print(f"invalid batch job: {_error(exc)}", file=sys.stderr)
        return 2

    started = time.perf_counter()
    results = [_render_one(project_dir, job, settings) for job in jobs]
    report = {
        "ok": all(result["ok"] for result in results),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "results": results,
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
