from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import db
from models import Storyboard
from paths import (
    PathSecurityError,
    SceneNotFoundError,
)
from server import (
    project_create,
    project_get,
    scene_get,
    scene_lint,
    scene_list,
    scene_write,
    storyboard_save,
)

SCENE_CODE = (Path(__file__).resolve().parents[1] / "examples/scenes/scene_01.py").read_text()


@pytest.fixture
def projects_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "projects"
    monkeypatch.setenv("MANIM_MCP_HOME", str(home))
    return home


@pytest.fixture
def project(projects_home: Path):
    return project_create("Scene Tests")


def clip(scene_id: str = "scene_01", seconds: float = 10, **overrides) -> dict:
    data = {
        "scene_id": scene_id,
        "title": f"Clip {scene_id}",
        "target_seconds": seconds,
        "visual": "A short visual description",
        "key_objects": ["formula"],
        "animation_notes": "Write the formula, then wait.",
        "typst_expressions": ["x^2 + y^2"],
    }
    data.update(overrides)
    return data


def storyboard(project_id: str, clips: list[dict], total: float | None = None) -> Storyboard:
    return Storyboard.model_validate(
        {
            "project_id": project_id,
            "clips": clips,
            "total_seconds": total
            if total is not None
            else sum(item["target_seconds"] for item in clips),
        }
    )


def test_storyboard_and_scene_lifecycle(project) -> None:
    board = storyboard(
        project.project_id,
        [clip("scene_01"), clip("scene_02", title="Second clip")],
    )
    saved = storyboard_save(project.project_id, board)
    assert saved == board

    refreshed = project_get(project.project_id)
    assert refreshed.storyboard == board
    assert refreshed.scene_count == 2
    assert refreshed.status_counts["planned"] == 2

    scenes = scene_list(project.project_id)
    assert [item.scene_id for item in scenes] == ["scene_01", "scene_02"]
    assert all(item.status == "planned" for item in scenes)
    assert scenes[0].target_seconds == 10

    detail = scene_get(project.project_id, "scene_01")
    assert detail.code is None
    assert detail.plan.target_seconds == 10
    assert detail.code_path.endswith("scenes/scene_01.py")

    written = scene_write(project.project_id, "scene_01", SCENE_CODE)
    assert written.status == "code_written"
    assert written.code_hash is not None
    assert len(written.code_hash) == 64
    assert written.code_lines == len(SCENE_CODE.splitlines())
    assert Path(detail.code_path).read_text() == SCENE_CODE

    report = scene_lint(project.project_id, "scene_01")
    assert report.ok
    assert report.class_name == "Scene01"

    detail = scene_get(project.project_id, "scene_01")
    assert detail.status == "lint_passed"
    assert detail.artifacts["lint"]["ok"] is True

    bad_code = SCENE_CODE.replace("import manimgx as m", "import os\nimport manimgx as m")
    written = scene_write(project.project_id, "scene_01", bad_code)
    assert written.status == "code_written"
    assert written.artifacts == {}

    report = scene_lint(project.project_id, "scene_01")
    assert not report.ok
    assert any(issue.rule == "forbidden_import" for issue in report.issues)
    assert scene_get(project.project_id, "scene_01").status == "code_written"

    event_types = [event["event_type"] for event in db.fetch_events(project.project_id)]
    assert "storyboard_saved" in event_types
    assert "scene_written" in event_types
    assert "lint_passed" in event_types
    assert "lint_failed" in event_types


def test_resaving_storyboard_preserves_code_and_status(project) -> None:
    board = storyboard(project.project_id, [clip("scene_01")])
    storyboard_save(project.project_id, board)
    scene_write(project.project_id, "scene_01", SCENE_CODE)
    scene_lint(project.project_id, "scene_01")

    updated = storyboard(project.project_id, [clip("scene_01", title="Renamed clip")])
    storyboard_save(project.project_id, updated)

    detail = scene_get(project.project_id, "scene_01")
    assert detail.title == "Renamed clip"
    assert detail.status == "lint_passed"
    assert detail.code == SCENE_CODE


def test_write_same_code_keeps_artifacts_and_new_code_clears_them(project) -> None:
    board = storyboard(project.project_id, [clip("scene_01")])
    storyboard_save(project.project_id, board)
    scene_write(project.project_id, "scene_01", SCENE_CODE)

    preview = Path(project.paths.preview_dir) / "scene_01.mp4"
    preview.write_bytes(b"stale")
    with db.project_connection(project.project_id) as connection:
        connection.execute(
            "UPDATE scenes SET artifacts_json = ? WHERE project_id = ? AND scene_id = ?",
            (json.dumps({"preview_video": str(preview)}), project.project_id, "scene_01"),
        )

    scene_write(project.project_id, "scene_01", SCENE_CODE)
    assert preview.is_file()
    assert db.get_scene_summary(project.project_id, "scene_01").artifacts != {}

    scene_write(project.project_id, "scene_01", SCENE_CODE + "\n# revised\n")
    assert not preview.exists()
    assert db.get_scene_summary(project.project_id, "scene_01").artifacts == {}


def test_scene_lint_without_code(project) -> None:
    storyboard_save(project.project_id, storyboard(project.project_id, [clip()]))
    report = scene_lint(project.project_id, "scene_01")
    assert not report.ok
    assert report.issues[0].rule == "missing_code"
    assert scene_get(project.project_id, "scene_01").status == "planned"


def test_unknown_scene(project) -> None:
    storyboard_save(project.project_id, storyboard(project.project_id, [clip()]))
    with pytest.raises(SceneNotFoundError):
        scene_get(project.project_id, "scene_99")
    with pytest.raises(SceneNotFoundError):
        scene_write(project.project_id, "scene_99", SCENE_CODE)


def test_scene_path_traversal_is_rejected(project) -> None:
    with pytest.raises(PathSecurityError):
        scene_get(project.project_id, "../scene_01")
    with pytest.raises(PathSecurityError):
        scene_write(project.project_id, "../scene_01", SCENE_CODE)


def test_storyboard_project_mismatch(project) -> None:
    board = storyboard(project.project_id, [clip()])
    with pytest.raises(ValueError, match="project_id"):
        storyboard_save("different-abcdef", board)


def test_storyboard_validation(project) -> None:
    # Durations are deprecated inputs: totals that disagree are ignored.
    board = Storyboard.model_validate(
        {
            "project_id": project.project_id,
            "clips": [clip("scene_01")],
            "total_seconds": 9,
        }
    )
    assert board.clips[0].target_seconds == 10
    with pytest.raises(ValidationError):
        storyboard(project.project_id, [clip("scene_01"), clip("scene_01")])
    with pytest.raises(ValidationError):
        storyboard(project.project_id, [clip("Scene_01")])
    with pytest.raises(ValidationError):
        Storyboard.model_validate(
            {
                "project_id": project.project_id,
                "clips": [
                    {
                        "scene_id": "scene_01",
                        "title": "No content",
                        "key_objects": [],
                    }
                ],
            }
        )


def test_storyboard_accepts_beats_without_durations(project) -> None:
    board = Storyboard.model_validate(
        {
            "project_id": project.project_id,
            "clips": [
                {
                    "scene_id": "scene_01",
                    "title": "Beats only",
                    "beats": [
                        {"action": "draw the grid", "objects": ["grid"]},
                        {"action": "apply A", "typst": "A = mat(1, 0.5; 0.2, 0.8)"},
                    ],
                }
            ],
        }
    )
    assert board.total_seconds is None
    assert len(board.clips[0].beats) == 2
    assert board.clips[0].target_seconds is None
