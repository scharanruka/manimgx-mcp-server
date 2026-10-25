from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastmcp import Client

import cleanup
from server import mcp, project_create, project_start


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    projects = tmp_path / "projects"
    exports = tmp_path / "exports"
    monkeypatch.setenv("MANIM_MCP_HOME", str(projects))
    monkeypatch.setenv("MANIM_MCP_EXPORT_HOME", str(exports))
    monkeypatch.setenv("MANIM_MCP_SESSION_ID", "test-session")
    return projects, exports


@pytest.mark.asyncio
async def test_start_asks_for_missing_parameters(homes: tuple[Path, Path]) -> None:
    result = await project_start("explain matrix rank", ctx=None)

    assert result.status == "needs_input"
    assert result.missing == ["aspect_ratio", "clip_count"]
    assert result.defaults == {"aspect_ratio": "9:16", "clip_count": 3}
    assert result.project is None
    assert not any(homes[0].glob("*")) if homes[0].exists() else True


@pytest.mark.asyncio
async def test_start_creates_project_when_parameters_given(homes: tuple[Path, Path]) -> None:
    result = await project_start(
        "explain matrix rank", aspect_ratio="9:16", clip_count=3, ctx=None
    )

    assert result.status == "started"
    assert result.project is not None
    assert result.clip_count == 3
    assert result.plan_prompt and "beats, never as a duration" in result.plan_prompt
    assert Path(result.project.paths.project_dir).is_dir()


@pytest.mark.asyncio
async def test_start_elicits_and_cleans_up_on_disconnect(homes: tuple[Path, Path]) -> None:
    async def answer(message, response_type, params, context):
        return {"aspect_ratio": "16:9", "clip_count": 2}

    async with Client(mcp, elicitation_handler=answer) as client:
        response = await client.call_tool("project_start", {"brief": "explain rank"})
        data = response.structured_content
        assert data["status"] == "started"
        assert data["project"]["config"]["aspect_ratio"] == "16:9"
        project_dir = Path(data["project"]["paths"]["project_dir"])
        assert project_dir.is_dir()

    assert not project_dir.exists()


@pytest.mark.asyncio
async def test_declined_elicitation_returns_needs_input(homes: tuple[Path, Path]) -> None:
    async def decline(message, response_type, params, context):
        return None

    async with Client(mcp, elicitation_handler=decline) as client:
        response = await client.call_tool("project_start", {"brief": "explain rank"})
        data = response.structured_content

    assert data["status"] == "needs_input"
    assert data["defaults"]["clip_count"] == 3


def test_cleanup_session_leaves_persistent_projects(homes: tuple[Path, Path]) -> None:
    project = project_create("Persistent")
    project_dir = Path(project.paths.project_dir)
    assert project_dir.is_dir()

    removed = cleanup.cleanup_session("test-session")

    assert removed == []
    assert project_dir.is_dir()


def test_sweep_exports_removes_expired_bundles(homes: tuple[Path, Path]) -> None:
    exports = homes[1]
    old = exports / "old-project-abc123"
    fresh = exports / "fresh-project-abc123"
    for directory in (old, fresh):
        directory.mkdir(parents=True)
        (directory / "video.mp4").write_bytes(b"x")
    stale = time.time() - 10 * 24 * 3600
    os.utime(old, (stale, stale))

    removed = cleanup.sweep_exports(days=7)

    assert removed == ["old-project-abc123"]
    assert fresh.is_dir() and not old.exists()
