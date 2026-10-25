from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import db
from models import Storyboard
from paths import SceneNotFoundError
from server import (
    project_create,
    project_render_batch,
    scene_get,
    scene_render_final,
    scene_write,
    storyboard_save,
)

TINY_SCENE = """import manimgx as m


class Scene01(m.Scene):
    def construct(self) -> None:
        text = m.Typst("Scene")
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
    seconds = 10 if count == 1 else 5
    clips = [
        {
            "scene_id": f"scene_{index:02d}",
            "title": f"Clip {index}",
            "target_seconds": seconds,
            "visual": "A word",
            "key_objects": ["text"],
            "animation_notes": "Write it.",
            "typst_expressions": ["Scene"],
        }
        for index in range(1, count + 1)
    ]
    board = Storyboard.model_validate(
        {
            "project_id": project.project_id,
            "clips": clips,
            "total_seconds": seconds * count,
        }
    )
    storyboard_save(project.project_id, board)
    return project


def write_all(project, count: int, code: str = TINY_SCENE) -> None:
    for index in range(1, count + 1):
        scene_write(project.project_id, f"scene_{index:02d}", code)


def approve(project, scene_id: str) -> None:
    with db.project_connection(project.project_id) as connection:
        connection.execute(
            "UPDATE scenes SET status = 'approved' WHERE project_id = ? AND scene_id = ?",
            (project.project_id, scene_id),
        )


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
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)["streams"][0]


def test_ten_preview_clips_in_one_batch(projects_home: Path) -> None:
    project = make_project("Ten Clip Batch", 10)
    write_all(project, 10)

    report = project_render_batch(project.project_id)

    assert report.ok
    assert report.mode == "preview"
    assert report.scene_count == 10
    assert len(report.results) == 10
    assert all(result.ok for result in report.results)
    assert report.elapsed_seconds < 60
    assert report.within_budget

    job = json.loads(Path(report.job_path).read_text())
    assert len(job["jobs"]) == 10
    assert job["width"] == 480
    assert job["height"] == 854
    assert job["fps"] == 15

    for result in report.results:
        assert result.video_path is not None
        assert Path(result.video_path).is_file()
    assert scene_get(project.project_id, "scene_01").status == "preview_rendered"

    if shutil.which("ffprobe"):
        stream = probe(Path(report.results[0].video_path))
        assert stream["width"] == 480
        assert stream["height"] == 854
        assert stream["r_frame_rate"] == "15/1"


def test_final_requires_approval_unless_forced(projects_home: Path) -> None:
    project = make_project("Final Gate", 1)
    write_all(project, 1)

    blocked = scene_render_final(project.project_id, "scene_01")
    assert not blocked.ok
    assert "not approved" in blocked.stderr

    forced = scene_render_final(project.project_id, "scene_01", force=True)
    assert forced.ok
    assert forced.video_path is not None
    assert forced.width == 1080
    assert forced.height == 1920
    assert forced.fps == 60
    assert scene_get(project.project_id, "scene_01").status == "final_rendered"

    if shutil.which("ffprobe"):
        stream = probe(Path(forced.video_path))
        assert stream["width"] == 1080
        assert stream["height"] == 1920
        assert stream["r_frame_rate"] == "60/1"


def test_final_batch_renders_approved_scenes(projects_home: Path) -> None:
    project = make_project("Final Batch", 2)
    write_all(project, 2)
    approve(project, "scene_01")
    approve(project, "scene_02")

    report = project_render_batch(project.project_id, mode="final")

    assert report.ok
    assert report.mode == "final"
    assert all(result.ok for result in report.results)
    assert all(Path(result.video_path).is_file() for result in report.results)
    assert scene_get(project.project_id, "scene_01").status == "final_rendered"

    job = json.loads(Path(report.job_path).read_text())
    assert job["width"] == 1080
    assert job["height"] == 1920
    assert job["fps"] == 60
    assert job["preset"] == "medium"
    assert job["crf"] == 18


def test_batch_reports_missing_code_without_blocking_others(projects_home: Path) -> None:
    project = make_project("Partial Batch", 2)
    scene_write(project.project_id, "scene_01", TINY_SCENE)

    report = project_render_batch(project.project_id)

    assert not report.ok
    by_id = {result.scene_id: result for result in report.results}
    assert by_id["scene_01"].ok
    assert not by_id["scene_02"].ok
    assert "no code" in by_id["scene_02"].error


def test_batch_rejects_duplicate_or_unknown_scenes(projects_home: Path) -> None:
    project = make_project("Batch Errors", 1)
    with pytest.raises(ValueError, match="duplicates"):
        project_render_batch(project.project_id, ["scene_01", "scene_01"])
    with pytest.raises(ValueError, match="must not be empty"):
        project_render_batch(project.project_id, [])
    with pytest.raises(SceneNotFoundError):
        project_render_batch(project.project_id, ["scene_99"])
