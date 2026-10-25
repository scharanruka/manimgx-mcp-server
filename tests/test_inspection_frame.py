from __future__ import annotations

from pathlib import Path

import pytest

import inspection
from models import LayoutIssue, Storyboard
from server import project_create, scene_write, storyboard_save

CODE = """import manimgx as m


class Scene01(m.Scene):
    def construct(self) -> None:
        text = m.Typst("Frame")
        self.play(m.Write(text))
"""


@pytest.fixture
def projects_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "projects"
    monkeypatch.setenv("MANIM_MCP_HOME", str(home))
    return home


def test_offset_issues_maps_back_to_source() -> None:
    issues = [LayoutIssue(message="text overlap (inspect-scene_01.py:20)", line=20)]

    shifted = inspection._offset_issues(issues, "scene_01")

    assert shifted[0].line == 20 - inspection._FRAME_PREAMBLE_LINES
    assert "inspect-scene_01.py" not in shifted[0].message
    assert "scene_01.py" in shifted[0].message


def test_framed_copy_sets_portrait_frame(projects_home: Path) -> None:
    project = project_create("Frame Copy")
    storyboard_save(
        project.project_id,
        Storyboard.model_validate(
            {
                "project_id": project.project_id,
                "clips": [
                    {
                        "scene_id": "scene_01",
                        "title": "Frame",
                        "target_seconds": 10,
                        "visual": "Text",
                        "key_objects": ["text"],
                        "animation_notes": "Write it.",
                    }
                ],
                "total_seconds": 10,
            }
        ),
    )
    scene_write(project.project_id, "scene_01", CODE)
    base = Path(project.paths.project_dir)

    copy = inspection._framed_copy(project.project_id, base, "scene_01", 480, 854)

    assert copy is not None
    text = copy.read_text(encoding="utf-8")
    assert "m.config.pixel_width = 480" in text
    assert "m.config.pixel_height = 854" in text
    assert "class Scene01" in text
    assert len(text.splitlines()) == len(CODE.splitlines()) + inspection._FRAME_PREAMBLE_LINES
