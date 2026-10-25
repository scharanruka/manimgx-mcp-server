from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import post
from models import Storyboard
from paths import PathSecurityError
from server import (
    project_create,
    project_stitch,
    scene_extract_frames,
    scene_open_mpv,
    scene_render_final,
    scene_render_preview,
    scene_write,
    storyboard_save,
)

CODE = """import manimgx as m


class Scene01(m.Scene):
    def construct(self) -> None:
        text = m.Typst("Post")
        self.play(m.Write(text), run_time=0.2)
        self.wait(0.1)
"""


@pytest.fixture
def projects_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "projects"
    monkeypatch.setenv("MANIM_MCP_HOME", str(home))
    return home


def make_project(name: str, count: int):
    project = project_create(name)
    clips = [
        {
            "scene_id": f"scene_{index:02d}",
            "title": f"Clip {index}",
            "target_seconds": 10,
            "visual": "Text",
            "key_objects": ["text"],
            "animation_notes": "Write it.",
            "typst_expressions": ["Post"],
        }
        for index in range(1, count + 1)
    ]
    storyboard_save(
        project.project_id,
        Storyboard.model_validate(
            {
                "project_id": project.project_id,
                "clips": clips,
                "total_seconds": 10 * count,
            }
        ),
    )
    for index in range(1, count + 1):
        scene_write(project.project_id, f"scene_{index:02d}", CODE)
    return project


def probe(path: Path) -> dict:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_stitch_final_renders(projects_home: Path) -> None:
    project = make_project("Stitch", 2)
    for index in range(1, 3):
        assert scene_render_final(project.project_id, f"scene_{index:02d}", force=True).ok

    report = project_stitch(project.project_id, "episode")

    assert report.ok
    assert report.export_path is not None
    export = Path(report.export_path)
    assert export.is_file()
    assert export.suffix == ".mp4"
    assert report.scene_ids == ["scene_01", "scene_02"]
    assert report.duration_seconds is not None
    assert report.duration_seconds > 0
    assert report.size_bytes and report.size_bytes > 0

    if shutil.which("ffprobe"):
        data = probe(export)
        assert data["streams"][0]["width"] == 1080
        assert data["streams"][0]["height"] == 1920
        assert data["streams"][0]["r_frame_rate"] == "60/1"


def test_stitch_without_final_renders(projects_home: Path) -> None:
    project = make_project("No Finals", 1)
    report = project_stitch(project.project_id)
    assert not report.ok
    assert "no final-rendered" in report.error


def test_stitch_reports_missing_requested_scene(projects_home: Path) -> None:
    project = make_project("Partial Finals", 2)
    assert scene_render_final(project.project_id, "scene_01", force=True).ok

    report = project_stitch(project.project_id, scene_ids=["scene_01", "scene_02"])

    assert not report.ok
    assert "scene_02" in report.error


@pytest.mark.parametrize("name", ["../evil.mp4", "/tmp/evil.mp4", "nested/final.mp4", ""])
def test_stitch_rejects_unsafe_names(projects_home: Path, name: str) -> None:
    project = make_project("Unsafe Export", 1)
    with pytest.raises(PathSecurityError):
        project_stitch(project.project_id, name)


def test_extract_frames(projects_home: Path) -> None:
    project = make_project("Frames", 1)
    assert scene_render_preview(project.project_id, "scene_01").ok

    report = scene_extract_frames(project.project_id, "scene_01", [0.0, 0.2])

    assert report.ok
    assert len(report.frame_paths) == 2
    for path in report.frame_paths:
        frame = Path(path)
        assert frame.is_file()
        assert frame.read_bytes().startswith(b"\x89PNG")


def test_extract_frames_from_final_when_available(projects_home: Path) -> None:
    project = make_project("Final Frames", 1)
    assert scene_render_preview(project.project_id, "scene_01").ok
    assert scene_render_final(project.project_id, "scene_01", force=True).ok

    report = scene_extract_frames(project.project_id, "scene_01", [0.0])

    assert report.ok
    assert report.source_video is not None
    assert "renders/final" in report.source_video


def test_extract_frames_requires_video(projects_home: Path) -> None:
    project = make_project("No Video", 1)
    report = scene_extract_frames(project.project_id, "scene_01", [0.0])
    assert not report.ok
    assert report.errors


def test_extract_frames_rejects_bad_times(projects_home: Path) -> None:
    project = make_project("Bad Frames", 1)
    with pytest.raises(ValueError, match="must not be empty"):
        scene_extract_frames(project.project_id, "scene_01", [])
    with pytest.raises(ValueError, match="non-negative"):
        scene_extract_frames(project.project_id, "scene_01", [-1])


def test_open_mpv_is_non_blocking(projects_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = make_project("mpv", 1)
    assert scene_render_preview(project.project_id, "scene_01").ok

    calls: list[list[str]] = []

    class FakeProcess:
        pid = 4321

    def fake_popen(command, **kwargs):
        calls.append(command)
        return FakeProcess()

    monkeypatch.setattr(post.shutil, "which", lambda name: "/usr/bin/mpv")
    monkeypatch.setattr(post.subprocess, "Popen", fake_popen)

    report = scene_open_mpv(project.project_id, "scene_01")

    assert report.ok
    assert report.pid == 4321
    assert calls and calls[0][1] == "--loop-file=inf"


def test_open_mpv_missing(projects_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = make_project("No mpv", 1)
    assert scene_render_preview(project.project_id, "scene_01").ok
    monkeypatch.setattr(post.shutil, "which", lambda name: None)

    report = scene_open_mpv(project.project_id, "scene_01")

    assert not report.ok
    assert "mpv is not installed" in report.error
