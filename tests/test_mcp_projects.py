from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from server import mcp


@pytest.fixture
def projects_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "projects"
    monkeypatch.setenv("MANIM_MCP_HOME", str(home))
    return home


def _payload(result: Any) -> dict[str, Any]:
    assert result.structured_content is not None
    return result.structured_content


@pytest.mark.asyncio
async def test_project_tools_are_registered_and_callable(projects_home: Path) -> None:
    expected = {"project_create", "project_get", "project_update_theme"}

    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert expected <= tools.keys()
        for name in expected:
            description = tools[name].description or ""
            assert len(description.split()) <= 20

        created = await client.call_tool(
            "project_create",
            {"name": "MCP Demo", "aspect_ratio": "9:16"},
        )
        assert not created.is_error
        payload = _payload(created)
        project_id = payload["project_id"]
        assert payload["config"]["aspect_ratio"] == "9:16"

        fetched = await client.call_tool("project_get", {"project_id": project_id})
        assert not fetched.is_error
        assert _payload(fetched)["project_id"] == project_id

        updated = await client.call_tool(
            "project_update_theme",
            {"project_id": project_id, "theme": {"accent_color": "red"}},
        )
        assert not updated.is_error
        assert _payload(updated)["config"]["accent_color"] == "red"
