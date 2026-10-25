from __future__ import annotations

from pathlib import Path

import pytest

from builder import split_scenes
from models import Storyboard
from server import project_build, project_create, project_export, storyboard_save

CODE = """import manimgx as m


# manimgx: scene_id=scene_01
class Scene01(m.Scene):
    def construct(self) -> None:
        text = m.Typst("One")
        self.play(m.Write(text), run_time=0.3)
        self.wait(0.2)


# manimgx: scene_id=scene_02
class Scene02(m.Scene):
    def construct(self) -> None:
        text = m.Typst("Two")
        self.play(m.Write(text), run_time=0.3)
        self.wait(0.2)
"""


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    projects = tmp_path / "projects"
    exports = tmp_path / "exports"
    monkeypatch.setenv("MANIM_MCP_HOME", str(projects))
    monkeypatch.setenv("MANIM_MCP_EXPORT_HOME", str(exports))
    return exports


def make_project(name: str = "Build Tests"):
    project = project_create(name, "9:16")
    storyboard_save(
        project.project_id,
        Storyboard.model_validate(
            {
                "project_id": project.project_id,
                "clips": [
                    {"scene_id": "scene_01", "title": "One", "beats": [{"action": "write One"}]},
                    {"scene_id": "scene_02", "title": "Two", "beats": [{"action": "write Two"}]},
                ],
            }
        ),
    )
    return project


def test_split_scenes_reads_markers() -> None:
    parsed = split_scenes(CODE)
    assert [(scene.scene_id, scene.class_name) for scene in parsed] == [
        ("scene_01", "Scene01"),
        ("scene_02", "Scene02"),
    ]
    assert "class Scene01(m.Scene)" in parsed[0].source
    assert parsed[0].header == "import manimgx as m"


def test_split_scenes_derives_ids_from_class_names() -> None:
    code = """import manimgx as m


class Scene07(m.Scene):
    def construct(self) -> None:
        self.play(m.FadeIn(m.Typst("x")))
"""
    parsed = split_scenes(code)
    assert parsed[0].scene_id is None
    assert parsed[0].class_name == "Scene07"


def test_build_rejects_unmapped_class(homes: Path) -> None:
    project = make_project("Unmapped")
    result = project_build(
        project.project_id,
        code="""import manimgx as m


class Scene99(m.Scene):
    def construct(self) -> None:
        self.play(m.FadeIn(m.Typst("x")))
""",
    )
    assert not result.ok
    assert any(issue.rule == "scene_mapping" for issue in result.issues)


@pytest.mark.gpu
def test_build_writes_bundle_and_skips_unchanged(homes: Path) -> None:
    project = make_project("Bundled")
    result = project_build(project.project_id, code=CODE)

    assert result.ok
    assert result.duration_seconds and result.duration_seconds > 0
    assert result.video_path and Path(result.video_path).is_file()
    assert result.code_path and Path(result.code_path).is_file()
    assert Path(result.code_path).parent.parent == homes
    code = Path(result.code_path).read_text(encoding="utf-8")
    assert "# manimgx: scene_id=scene_01" in code
    assert "# manimgx: scene_id=scene_02" in code
    assert [scene.changed for scene in result.scenes] == [True, True]
    assert {scene.status for scene in result.scenes} == {"final_rendered"}

    again = project_build(project.project_id)
    assert again.ok
    assert [scene.changed for scene in again.scenes] == [False, False]

    edited = code.replace('m.Typst("Two")', 'm.Typst("Two edited")')
    rebuilt = project_build(project.project_id, code=edited)
    assert rebuilt.ok
    assert [scene.changed for scene in rebuilt.scenes] == [False, True]


@pytest.mark.gpu
def test_build_review_returns_previews(homes: Path) -> None:
    project = make_project("Review")
    result = project_build(project.project_id, code=CODE, review=True)

    assert result.ok
    assert result.mode == "preview"
    assert result.approval_required
    assert result.video_path is None
    assert all(scene.video_path and Path(scene.video_path).is_file() for scene in result.scenes)


@pytest.mark.gpu
def test_project_export_returns_bundle(homes: Path) -> None:
    project = make_project("Export")
    assert project_build(project.project_id, code=CODE).ok

    result = project_export(project.project_id)

    assert result.ok
    assert result.bundle_dir and Path(result.bundle_dir).is_dir()
    for name in ("video.mp4", "code.py", "storyboard.json", "report.json"):
        assert (Path(result.bundle_dir) / name).is_file()
