"""ManimGX inspect wrapper and output parser."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from config import INSPECT_STDOUT_TAIL, RENDER_TIMEOUT_SECONDS
from models import InspectReport, LayoutIssue
from paths import project_dir, project_paths, scene_file

_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?) s,")
_PROBLEM_RE = re.compile(r"^\s*\[(\d+)\]\s+(.*)$")
_FOLDED_RE = re.compile(r"^\s*\[(\d+)[–-](\d+)\]\s+(.*)$")
_NOTE_RE = re.compile(r"^\s*note:\s+(.*)$")
_SOURCE_RE = re.compile(r"\(([^()]+\.py):(\d+)\)")

# `manimgx inspect` has no resolution flag: it renders at the scene's configured
# size, which defaults to 1920x1080 even for a portrait project.  Inspecting a
# generated copy with a frame preamble makes the layout checks match the render.
_FRAME_PREAMBLE = """import manimgx as m

m.config.pixel_width = {width}
m.config.pixel_height = {height}

"""
_FRAME_PREAMBLE_LINES = _FRAME_PREAMBLE.count("\n")


def _tail(text: str, size: int) -> str:
    return text[-size:] if text else ""


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def format_times(times: list[float | str] | None) -> str | None:
    """Format inspect times as the CLI's comma-separated value."""
    if not times:
        return None
    values: list[str] = []
    for value in times:
        if value == "end":
            values.append("end")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("inspect times must be non-negative numbers or 'end'")
        if value < 0:
            raise ValueError("inspect times must be non-negative")
        values.append(f"{float(value):g}")
    return ",".join(values)


def _storyboard_paths(project_id: str, scene_id: str) -> list[str]:
    directory = Path(project_paths(project_id).storyboards_dir)
    if not directory.is_dir():
        return []
    matches = [
        path
        for path in directory.glob(f"{scene_id}*.png")
        if path.stem == scene_id or path.stem.startswith(f"{scene_id}-")
    ]
    return [str(path) for path in sorted(matches)]


def _clear_storyboards(project_id: str, scene_id: str) -> None:
    for path in _storyboard_paths(project_id, scene_id):
        Path(path).unlink(missing_ok=True)


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
            f"inspect timed out after {RENDER_TIMEOUT_SECONDS}s\n{_as_text(exc.stderr)}",
        )
    except OSError as exc:
        return 127, "", f"could not start manimgx inspect: {exc}"
    return completed.returncode, completed.stdout, completed.stderr


def parse_inspect_output(stdout: str, exit_code: int) -> tuple[list[LayoutIssue], float | None]:
    """Parse the layout section of ManimGX inspect output."""
    issues: list[LayoutIssue] = []
    in_layout = False
    duration_match = _DURATION_RE.search(stdout)
    duration = float(duration_match.group(1)) if duration_match else None

    for line in stdout.splitlines():
        if line.startswith("layout:"):
            in_layout = True
            continue
        if not in_layout:
            continue

        folded = _FOLDED_RE.match(line)
        if folded:
            issues.append(
                LayoutIssue(
                    number=int(folded.group(1)),
                    message=f"same problem for {folded.group(3)}",
                )
            )
            continue

        note = _NOTE_RE.match(line)
        if note:
            message = note.group(1).strip()
            source = _SOURCE_RE.search(message)
            issues.append(
                LayoutIssue(
                    message=message,
                    line=int(source.group(2)) if source else None,
                    note=True,
                )
            )
            continue

        problem = _PROBLEM_RE.match(line)
        if problem:
            message = problem.group(2).strip()
            source = _SOURCE_RE.search(message)
            issues.append(
                LayoutIssue(
                    number=int(problem.group(1)),
                    message=message,
                    line=int(source.group(2)) if source else None,
                )
            )

    if exit_code != 0 and not issues:
        fallback = next(
            (line.strip() for line in reversed(stdout.splitlines()) if line.strip()),
            "inspect failed; see stderr",
        )
        issues.append(LayoutIssue(message=fallback))

    return issues, duration


def _offset_issues(issues: list[LayoutIssue], scene_id: str) -> list[LayoutIssue]:
    """Map issue line numbers from the framed copy back to the scene source."""
    shifted: list[LayoutIssue] = []
    for issue in issues:
        line = issue.line
        if line is not None:
            line = max(1, line - _FRAME_PREAMBLE_LINES)
        shifted.append(
            issue.model_copy(
                update={
                    "line": line,
                    "message": issue.message.replace(
                        f"inspect-{scene_id}.py", f"{scene_id}.py"
                    ),
                }
            )
        )
    return shifted


def _framed_copy(project_id: str, base: Path, scene_id: str, width: int, height: int) -> Path | None:
    """Write ``logs/inspect-<scene>.py`` with the project's frame size set."""
    source = scene_file(project_id, scene_id)
    try:
        code = source.read_text(encoding="utf-8")
    except OSError:
        return None
    copy = base / "logs" / f"inspect-{scene_id}.py"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(
        _FRAME_PREAMBLE.format(width=width, height=height) + code,
        encoding="utf-8",
    )
    return copy


def run_inspect(
    project_id: str,
    scene_id: str,
    class_name: str,
    times: list[float | str] | None = None,
    width: int | None = None,
    height: int | None = None,
) -> InspectReport:
    """Run ``manimgx inspect`` for one scene and return a compact report."""
    base = project_dir(project_id)
    scene = scene_file(project_id, scene_id).relative_to(base)
    output = Path(project_paths(project_id).storyboards_dir) / f"{scene_id}.png"
    relative_output = output.relative_to(base)

    framed: Path | None = None
    if width and height:
        framed = _framed_copy(project_id, base, scene_id, width, height)
        if framed is not None:
            scene = framed.relative_to(base)

    formatted_times = format_times(times)

    _clear_storyboards(project_id, scene_id)

    command = [
        sys.executable,
        "-m",
        "manimgx",
        "inspect",
        str(scene),
        class_name,
        "-o",
        str(relative_output),
    ]
    if formatted_times is not None:
        command.extend(["-t", formatted_times])

    try:
        exit_code, stdout, stderr = _run(command, base)
        issues, duration = parse_inspect_output(stdout, exit_code)
    finally:
        if framed is not None:
            framed.unlink(missing_ok=True)

    if framed is not None:
        issues = _offset_issues(issues, scene_id)
    ok = exit_code == 0 and not any(not issue.note for issue in issues)

    return InspectReport(
        ok=ok,
        exit_code=exit_code,
        stdout=_tail(stdout, INSPECT_STDOUT_TAIL),
        stderr=_tail(stderr, INSPECT_STDOUT_TAIL),
        storyboard_paths=_storyboard_paths(project_id, scene_id),
        issues=issues,
        duration_seconds=duration,
    )
