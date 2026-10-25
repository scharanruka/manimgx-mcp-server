from __future__ import annotations

from pathlib import Path

import pytest

import db
from models import Storyboard
from prompts import build_scene_edit_prompt
from server import (
    project_create,
    project_get,
    scene_get,
    scene_review,
    scene_write,
    storyboard_save,
)

CODE = """import manimgx as m


class Scene01(m.Scene):
    def construct(self) -> None:
        text = m.Typst("Review")
        self.play(m.Write(text))
"""


@pytest.fixture
def projects_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "projects"
    monkeypatch.setenv("MANIM_MCP_HOME", str(home))
    return home


@pytest.fixture
def project(projects_home: Path):
    project = project_create("Review Tests")
    storyboard_save(
        project.project_id,
        Storyboard.model_validate(
            {
                "project_id": project.project_id,
                "clips": [
                    {
                        "scene_id": "scene_01",
                        "title": "Review clip",
                        "target_seconds": 10,
                        "visual": "Text",
                        "key_objects": ["text"],
                        "animation_notes": "Write it.",
                        "typst_expressions": ["Review"],
                    }
                ],
                "total_seconds": 10,
            }
        ),
    )
    scene_write(project.project_id, "scene_01", CODE)
    return project


def set_status(project, status: str, reason: str | None = None) -> None:
    with db.project_connection(project.project_id) as connection:
        connection.execute(
            """
            UPDATE scenes SET status = ?, rejection_reason = ?
            WHERE project_id = ? AND scene_id = ?
            """,
            (status, reason, project.project_id, "scene_01"),
        )


def test_reject_stores_reason_and_approve_clears_it(project) -> None:
    set_status(project, "preview_rendered")

    rejected = scene_review(
        project.project_id,
        "scene_01",
        "reject",
        reason="The formula is too small and overlaps the title",
    )
    assert rejected.status == "rejected"
    assert rejected.rejection_reason == "The formula is too small and overlaps the title"
    assert scene_get(project.project_id, "scene_01").rejection_reason == rejected.rejection_reason
    assert project_get(project.project_id).status_counts["rejected"] == 1

    prompt = build_scene_edit_prompt(project.project_id, "scene_01")
    assert "The formula is too small and overlaps the title" in prompt
    assert "class Scene01(m.Scene)" in prompt

    set_status(project, "preview_rendered")
    approved = scene_review(project.project_id, "scene_01", "approve")
    assert approved.status == "approved"
    assert approved.rejection_reason is None


def test_approve_accepts_inspected_status(project) -> None:
    set_status(project, "inspected")
    approved = scene_review(project.project_id, "scene_01", "approve")
    assert approved.status == "approved"


@pytest.mark.parametrize("status", ["planned", "code_written", "lint_passed", "rejected"])
def test_approve_rejects_wrong_status(project, status: str) -> None:
    set_status(project, status)
    with pytest.raises(ValueError, match="cannot be approved"):
        scene_review(project.project_id, "scene_01", "approve")


def test_reject_requires_reason(project) -> None:
    set_status(project, "preview_rendered")
    with pytest.raises(ValueError, match="requires a reason"):
        scene_review(project.project_id, "scene_01", "reject")
    with pytest.raises(ValueError, match="requires a reason"):
        scene_review(project.project_id, "scene_01", "reject", reason="   ")
    with pytest.raises(ValueError, match="at most 2000"):
        scene_review(project.project_id, "scene_01", "reject", reason="x" * 2001)


def test_invalid_decision_is_rejected(project) -> None:
    with pytest.raises(ValueError, match="invalid review decision"):
        scene_review(project.project_id, "scene_01", "maybe")  # type: ignore[arg-type]


def test_rewriting_code_clears_rejection_reason(project) -> None:
    set_status(project, "preview_rendered")
    scene_review(project.project_id, "scene_01", "reject", reason="Fix the layout")

    scene_write(project.project_id, "scene_01", CODE + "\n# revised\n")

    detail = scene_get(project.project_id, "scene_01")
    assert detail.status == "code_written"
    assert detail.rejection_reason is None
