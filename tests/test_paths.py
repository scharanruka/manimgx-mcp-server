from __future__ import annotations

from pathlib import Path

import pytest

from paths import (
    PathSecurityError,
    create_project_dir,
    new_project_id,
    project_dir,
    scene_file,
    slugify,
    validate_scene_id,
)


@pytest.fixture
def projects_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "projects"
    monkeypatch.setenv("MANIM_MCP_HOME", str(home))
    return home


def test_generated_project_id_is_safe() -> None:
    project_id = new_project_id("Derivative Intro!")
    assert project_id.startswith("derivative-intro-")
    assert len(project_id.rsplit("-", 1)[1]) == 6
    assert project_dir(project_id).name == project_id


def test_slugify_falls_back_for_non_ascii_names() -> None:
    assert slugify("日本語") == "project"
    assert slugify("  --Hello, World!!  ") == "hello-world"


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "..",
        "../evil-abcdef",
        "evil-abcdef/..",
        "/tmp/evil-abcdef",
        "evil-abcdef\\x",
        "evil-abcdef\x00",
        "EVIL-abcdef",
        "evil",
        "evil-abc",
    ],
)
def test_project_id_traversal_is_rejected(bad_id: str) -> None:
    with pytest.raises(PathSecurityError):
        project_dir(bad_id)


def test_project_directory_symlink_is_rejected(projects_home: Path) -> None:
    projects_home.mkdir(parents=True)
    outside = projects_home.parent / "outside"
    outside.mkdir()
    (projects_home / "evil-abcdef").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathSecurityError):
        project_dir("evil-abcdef")


@pytest.mark.parametrize(
    "bad_id",
    ["", "..", "../scene_01", "scene/01", "scene_01.py", "Scene_01", "scene 01"],
)
def test_scene_id_traversal_is_rejected(bad_id: str) -> None:
    with pytest.raises(PathSecurityError):
        validate_scene_id(bad_id)


def test_scene_file_stays_inside_scenes(projects_home: Path) -> None:
    project_id = new_project_id("Scene Paths")
    create_project_dir(project_id)

    path = scene_file(project_id, "scene_01")
    assert path.parent == Path(project_dir(project_id)) / "scenes"
    assert path.name == "scene_01.py"
