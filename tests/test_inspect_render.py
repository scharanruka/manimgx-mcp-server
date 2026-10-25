from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from models import Storyboard
from server import (
    project_create,
    scene_get,
    scene_inspect,
    scene_lint,
    scene_render_preview,
    scene_write,
    storyboard_save,
)

ROOT = Path(__file__).resolve().parents[1]
SCENE_CODE = (ROOT / "examples/scenes/scene_01.py").read_text()
BROKEN_CODE = """import manimgx as m


class Scene01(m.Scene):
    def construct(self) -> None:
        title = m.Typst(
            "This deliberately overlong title should overflow the frame",
            font_size=120,
        )
        self.play(m.Write(title))
        self.wait(0.5)
"""


@pytest.fixture
def projects_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "projects"
    monkeypatch.setenv("MANIM_MCP_HOME", str(home))
    return home


def make_project(projects_home: Path):
    project = project_create("Inspect Render Tests")
    board = Storyboard.model_validate(
        {
            "project_id": project.project_id,
            "clips": [
                {
                    "scene_id": "scene_01",
                    "title": "Test clip",
                    "target_seconds": 10,
                    "visual": "A formula",
                    "key_objects": ["formula"],
                    "animation_notes": "Write it.",
                    "typst_expressions": ["x^2"],
                }
            ],
            "total_seconds": 10,
        }
    )
    storyboard_save(project.project_id, board)
    return project


def test_inspect_valid_scene(projects_home: Path) -> None:
    project = make_project(projects_home)
    scene_write(project.project_id, "scene_01", SCENE_CODE)
    assert scene_lint(project.project_id, "scene_01").ok

    report = scene_inspect(project.project_id, "scene_01", times=[1.5, "end"])

    assert report.ok
    assert report.exit_code == 0
    assert report.issues == []
    assert report.duration_seconds == 4.6
    assert report.storyboard_paths
    assert Path(report.storyboard_paths[0]).is_file()

    detail = scene_get(project.project_id, "scene_01")
    assert detail.status == "inspected"
    assert detail.artifacts["inspect"]["ok"] is True


def test_inspect_reports_layout_problem(projects_home: Path) -> None:
    project = make_project(projects_home)
    scene_write(project.project_id, "scene_01", BROKEN_CODE)

    report = scene_inspect(project.project_id, "scene_01")

    assert not report.ok
    assert report.exit_code == 1
    assert report.issues
    assert any("past the" in issue.message for issue in report.issues)
    assert report.storyboard_paths
    assert Path(report.storyboard_paths[0]).is_file()
    assert scene_get(project.project_id, "scene_01").status == "code_written"


def test_preview_render_is_portrait_and_15_fps(projects_home: Path) -> None:
    project = make_project(projects_home)
    scene_write(project.project_id, "scene_01", SCENE_CODE)
    scene_lint(project.project_id, "scene_01")
    scene_inspect(project.project_id, "scene_01")

    report = scene_render_preview(project.project_id, "scene_01")

    assert report.ok
    assert report.exit_code == 0
    assert report.video_path is not None
    video = Path(report.video_path)
    assert video.is_file()
    assert report.width == 480
    assert report.height == 854
    assert report.fps == 15
    assert report.duration_seconds == 4.6
    assert report.storyboard_paths
    assert Path(report.storyboard_paths[0]).is_file()

    detail = scene_get(project.project_id, "scene_01")
    assert detail.status == "preview_rendered"
    assert detail.artifacts["preview_video"] == str(video)

    if shutil.which("ffprobe"):
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,r_frame_rate",
                "-of",
                "json",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        stream = json.loads(completed.stdout)["streams"][0]
        assert stream["width"] == 480
        assert stream["height"] == 854
        assert stream["r_frame_rate"] == "15/1"


def test_inspect_without_code(projects_home: Path) -> None:
    project = make_project(projects_home)

    report = scene_inspect(project.project_id, "scene_01")
    preview = scene_render_preview(project.project_id, "scene_01")

    assert not report.ok
    assert "no code" in report.stderr
    assert not preview.ok
    assert "no code" in preview.stderr
    assert preview.width == 480
    assert preview.height == 854


def test_inspect_rejects_invalid_times(projects_home: Path) -> None:
    project = make_project(projects_home)
    scene_write(project.project_id, "scene_01", SCENE_CODE)

    report = scene_inspect(project.project_id, "scene_01", times=[-1])

    assert not report.ok
    assert "non-negative" in report.stderr
