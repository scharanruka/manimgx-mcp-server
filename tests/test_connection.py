from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_stdio_connection(tmp_path: Path) -> None:
    env = {**os.environ, "MANIM_MCP_HOME": str(tmp_path / "projects")}
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "server"],
        env=env,
        cwd=str(ROOT),
    )

    async with Client(transport) as client:
        tools = {tool.name for tool in await client.list_tools()}
        assert "project_create" in tools
        assert "scene_render_preview" in tools

        created = await client.call_tool("project_create", {"name": "Connection Test"})
        assert not created.is_error
        project_id = created.structured_content["project_id"]

        fetched = await client.call_tool("project_get", {"project_id": project_id})
        assert not fetched.is_error
        assert fetched.structured_content["project_id"] == project_id
