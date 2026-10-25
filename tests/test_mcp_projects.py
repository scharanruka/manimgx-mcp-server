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
    expected = {
        "project_create",
        "project_get",
        "project_update_theme",
        "storyboard_save",
        "scene_write",
        "scene_list",
        "scene_get",
        "scene_lint",
        "scene_inspect",
        "scene_render_preview",
    }

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


@pytest.mark.asyncio
async def test_scene_tools_via_mcp(projects_home: Path) -> None:
    code = (
        "import manimgx as m\n"
        "\n"
        "class Scene01(m.Scene):\n"
        "    def construct(self) -> None:\n"
        '        text = m.Typst("Hello")\n'
        "        self.play(m.Write(text))\n"
    )

    async with Client(mcp) as client:
        created = await client.call_tool("project_create", {"name": "MCP Scenes"})
        project_id = _payload(created)["project_id"]

        saved = await client.call_tool(
            "storyboard_save",
            {
                "project_id": project_id,
                "storyboard": {
                    "project_id": project_id,
                    "clips": [
                        {
                            "scene_id": "scene_01",
                            "title": "Hello",
                            "target_seconds": 10,
                            "visual": "A word appears",
                            "key_objects": ["text"],
                            "animation_notes": "Write the word.",
                            "typst_expressions": ["Hello"],
                        }
                    ],
                    "total_seconds": 10,
                },
            },
        )
        assert not saved.is_error
        assert _payload(saved)["clips"][0]["scene_id"] == "scene_01"

        written = await client.call_tool(
            "scene_write",
            {"project_id": project_id, "scene_id": "scene_01", "code": code},
        )
        assert not written.is_error
        assert _payload(written)["status"] == "code_written"

        linted = await client.call_tool(
            "scene_lint", {"project_id": project_id, "scene_id": "scene_01"}
        )
        assert not linted.is_error
        assert _payload(linted)["ok"] is True

        listed = await client.call_tool("scene_list", {"project_id": project_id})
        assert not listed.is_error
        assert "scene_01" in str(listed.structured_content)

        fetched = await client.call_tool(
            "scene_get", {"project_id": project_id, "scene_id": "scene_01"}
        )
        assert not fetched.is_error
        detail = _payload(fetched)
        assert detail["status"] == "lint_passed"
        assert detail["code"] == code

        inspected = await client.call_tool(
            "scene_inspect",
            {"project_id": project_id, "scene_id": "scene_01", "times": [0.5, "end"]},
        )
        assert not inspected.is_error
        assert _payload(inspected)["ok"] is True

        previewed = await client.call_tool(
            "scene_render_preview",
            {"project_id": project_id, "scene_id": "scene_01"},
        )
        assert not previewed.is_error
        preview = _payload(previewed)
        assert preview["ok"] is True
        assert preview["width"] == 480
        assert preview["height"] == 854
        assert preview["fps"] == 15
