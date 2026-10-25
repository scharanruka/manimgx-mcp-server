from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from db import fetch_events, get_project_row
from paths import (
    PathSecurityError,
    ProjectNotFoundError,
)
from server import project_create, project_get, project_update_theme


@pytest.fixture
def projects_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "projects"
    monkeypatch.setenv("MANIM_MCP_HOME", str(home))
    return home


def test_two_projects_have_isolated_configs(projects_home: Path) -> None:
    portrait = project_create("Portrait Project")
    landscape = project_create(
        "Landscape Project",
        aspect_ratio="16:9",
        theme={"background_color": "#101010", "accent_color": "red"},
    )

    assert portrait.project_id != landscape.project_id
    assert Path(portrait.paths.db_path).is_file()
    assert Path(landscape.paths.db_path).is_file()
    assert portrait.paths.project_dir != landscape.paths.project_dir

    assert portrait.config.aspect_ratio == "9:16"
    assert portrait.config.final_width == 1080
    assert portrait.config.final_height == 1920
    assert portrait.config.background_color == "BLACK"

    assert landscape.config.aspect_ratio == "16:9"
    assert landscape.config.final_width == 1920
    assert landscape.config.final_height == 1080
    assert landscape.config.background_color == "#101010"
    assert landscape.config.accent_color == "red"

    # Reload from SQLite rather than trusting the returned object.
    fresh_portrait = project_get(portrait.project_id)
    fresh_landscape = project_get(landscape.project_id)
    assert fresh_portrait.config == portrait.config
    assert fresh_landscape.config == landscape.config
    assert fresh_portrait.config != fresh_landscape.config


def test_project_creates_expected_layout(projects_home: Path) -> None:
    project = project_create("Layout Check")
    root = Path(project.paths.project_dir)

    for directory in (
        "scenes",
        "renders/preview",
        "renders/final",
        "renders/storyboards",
        "frames",
        "exports",
        "logs",
    ):
        assert (root / directory).is_dir(), directory
    assert (root / "project.db").is_file()


def test_update_theme_persists_and_isolates(projects_home: Path) -> None:
    first = project_create("First")
    second = project_create("Second")

    updated = project_update_theme(
        first.project_id,
        {
            "accent_color": "#00FF00",
            "font_sizes": {"title": 80},
            "safe_area": {"y_max": 6.5},
            "preview_fps": 20,
        },
    )

    assert updated.config.accent_color == "#00FF00"
    assert updated.config.font_sizes.title == 80
    assert updated.config.font_sizes.body == 32
    assert updated.config.safe_area.y_max == 6.5
    assert updated.config.safe_area.y_min == -7.0
    assert updated.config.preview_fps == 20

    untouched = project_get(second.project_id)
    assert untouched.config.accent_color == "BLUE"
    assert untouched.config.font_sizes.title == 64
    assert untouched.config.safe_area.y_max == 7.0
    assert untouched.config.preview_fps == 15


def test_update_theme_rejects_unknown_keys(projects_home: Path) -> None:
    project = project_create("Strict Theme")

    with pytest.raises(ValidationError):
        project_update_theme(project.project_id, {"aspect_ratio": "16:9"})
    with pytest.raises(ValidationError):
        project_update_theme(project.project_id, {"not_a_setting": 1})
    with pytest.raises(ValidationError):
        project_update_theme(project.project_id, {"background_color": "not-a-color"})
    with pytest.raises(ValidationError):
        project_update_theme(project.project_id, {"safe_area": {"x_min": 5, "x_max": 4}})


def test_project_not_found(projects_home: Path) -> None:
    with pytest.raises(ProjectNotFoundError):
        project_get("missing-abcdef")


def test_project_get_rejects_path_traversal(projects_home: Path) -> None:
    with pytest.raises(PathSecurityError):
        project_get("../../etc/passwd")
    with pytest.raises(PathSecurityError):
        project_update_theme("../escape-abcdef", {"accent_color": "red"})


def test_project_events_are_recorded(projects_home: Path) -> None:
    project = project_create("Events")
    project_update_theme(project.project_id, {"accent_color": "red"})

    events = fetch_events(project.project_id)
    event_types = [event["event_type"] for event in events]
    assert event_types[:2] == ["theme_updated", "project_created"]

    row = get_project_row(project.project_id)
    assert row["name"] == "Events"

    with sqlite3.connect(project.paths.db_path) as connection:
        scene_count = connection.execute("SELECT COUNT(*) FROM scenes").fetchone()[0]
    assert scene_count == 0
