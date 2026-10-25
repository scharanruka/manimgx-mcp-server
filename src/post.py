"""ffmpeg stitching, frame extraction, and mpv preview helpers."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

from config import POST_TIMEOUT_SECONDS, RENDER_STDOUT_TAIL
from models import ExportReport, FrameReport, MpvReport


def _tail(text: str) -> str:
    return text[-RENDER_STDOUT_TAIL:] if text else ""


def _last_line(text: str) -> str:
    return next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")


def _run(command: list[str]) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=POST_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out after {POST_TIMEOUT_SECONDS}s"
    except OSError as exc:
        return 127, "", f"could not start command: {exc}"
    return completed.returncode, completed.stdout, completed.stderr


def probe_duration(video: Path) -> float | None:
    """Read a video's duration with ffprobe when it is available."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    code, stdout, _ = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ]
    )
    if code != 0:
        return None
    try:
        return round(float(stdout.strip()), 3)
    except ValueError:
        return None


def _concat_line(path: Path) -> str:
    escaped = str(path).replace("'", "'\\''")
    return f"file '{escaped}'\n"


def _probe_fps(video: Path) -> int | None:
    """Read a video's frame rate with ffprobe when it is available."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    code, stdout, _ = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ]
    )
    if code != 0:
        return None
    try:
        numerator, _, denominator = stdout.strip().partition("/")
        value = float(numerator) / float(denominator or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return round(value) or None


def stitch(
    videos: list[Path],
    scene_ids: list[str],
    output: Path,
    work_dir: Path,
    *,
    preset: str = "medium",
    crf: float = 18.0,
    fps: int | None = None,
) -> ExportReport:
    """Concatenate videos with ffmpeg and re-time them to a constant frame rate.

    Stream copy is not an option: ManimGX writes a still hold as one frame with a
    long duration, and the concat demuxer cuts each segment at its last packet PTS,
    which silently drops the trailing hold of the final clip.  Re-encoding with
    ``-fps_mode cfr`` rebuilds the timeline from each input's real duration.
    """
    if not videos:
        return ExportReport(ok=False, scene_ids=scene_ids, error="no final videos to stitch")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return ExportReport(
            ok=False,
            scene_ids=scene_ids,
            error="ffmpeg is not installed or not on PATH",
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    list_path = work_dir / f".stitch-{uuid.uuid4().hex[:8]}.txt"
    list_path.write_text("".join(_concat_line(path) for path in videos), encoding="utf-8")
    output.unlink(missing_ok=True)

    frame_rate = fps or _probe_fps(videos[0])
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        f"{crf:g}",
        "-pix_fmt",
        "yuv420p",
        "-fps_mode",
        "cfr",
        "-c:a",
        "aac",
    ]
    if frame_rate is not None:
        command.extend(["-r", str(frame_rate)])
    command.extend(["-movflags", "+faststart", str(output)])

    try:
        code, stdout, stderr = _run(command)

        if code != 0 or not output.is_file() or output.stat().st_size == 0:
            output.unlink(missing_ok=True)
            return ExportReport(
                ok=False,
                scene_ids=scene_ids,
                reencoded=True,
                error=_last_line(stderr) or "ffmpeg failed to stitch the videos",
                stdout=_tail(stdout),
                stderr=_tail(stderr),
            )

        duration = probe_duration(output)
        return ExportReport(
            ok=True,
            export_path=str(output),
            scene_ids=scene_ids,
            duration_seconds=duration,
            size_bytes=output.stat().st_size,
            reencoded=True,
            stdout=_tail(stdout),
            stderr=_tail(stderr),
        )
    finally:
        list_path.unlink(missing_ok=True)


def _frame_times(video: Path) -> list[float]:
    """Return the presentation times of every video frame, oldest first."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return []
    code, stdout, _ = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=pts_time",
            "-of",
            "csv=p=0",
            str(video),
        ]
    )
    if code != 0:
        return []
    times: list[float] = []
    for line in stdout.splitlines():
        try:
            times.append(float(line.strip().strip(",")))
        except ValueError:
            continue
    return times


def _clamp_to_frame(moment: float, frame_times: list[float]) -> float:
    """Pick the last frame shown at or before ``moment``.

    A still hold is one frame with a long duration, so seeking to a time inside
    the hold finds no packet at or after it.  Seeking to the frame's own PTS does.
    """
    if not frame_times:
        return moment
    earlier = [value for value in frame_times if value <= moment + 1e-6]
    return earlier[-1] if earlier else frame_times[0]


def extract_frames(video: Path, output_dir: Path, times: list[float]) -> FrameReport:
    """Extract one PNG per requested time with ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return FrameReport(
            ok=False,
            times=times,
            source_video=str(video),
            errors=["ffmpeg is not installed or not on PATH"],
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("frame_*.png"):
        old.unlink(missing_ok=True)

    frame_times = _frame_times(video)
    frame_paths: list[str] = []
    errors: list[str] = []
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    for index, moment in enumerate(times):
        frame = output_dir / f"frame_{index:02d}_{moment:g}.png"
        at = _clamp_to_frame(moment, frame_times)
        code, stdout, stderr = _run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(video),
                "-ss",
                f"{at:.6f}",
                "-frames:v",
                "1",
                "-update",
                "1",
                str(frame),
            ]
        )
        stdout_parts.append(stdout)
        stderr_parts.append(stderr)
        if code == 0 and frame.is_file() and frame.stat().st_size > 0:
            frame_paths.append(str(frame))
        else:
            reason = _last_line(stderr) or "ffmpeg produced no frame"
            errors.append(f"t={moment:g}: {reason}")

    stdout = "\n".join(part for part in stdout_parts if part)
    stderr = "\n".join(part for part in stderr_parts if part)
    return FrameReport(
        ok=not errors and len(frame_paths) == len(times),
        frame_paths=frame_paths,
        times=times,
        source_video=str(video),
        errors=errors,
        stdout=_tail(stdout),
        stderr=_tail(stderr),
    )


def open_mpv(video: Path) -> MpvReport:
    """Open a video in a detached, looping mpv window."""
    mpv = shutil.which("mpv")
    if mpv is None:
        return MpvReport(
            ok=False,
            video_path=str(video),
            error="mpv is not installed or not on PATH",
        )
    try:
        process = subprocess.Popen(
            [mpv, "--loop-file=inf", "--really-quiet", str(video)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return MpvReport(ok=False, video_path=str(video), error=f"could not start mpv: {exc}")
    return MpvReport(ok=True, pid=process.pid, video_path=str(video))
