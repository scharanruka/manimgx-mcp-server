"""Connect to the MCP server over stdio and list its tools."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="manimgx-mcp-") as home:
        env = {**os.environ, "MANIM_MCP_HOME": str(Path(home) / "projects")}
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "server"],
            env=env,
            cwd=str(ROOT),
        )

        async with Client(transport) as client:
            tools = sorted(tool.name for tool in await client.list_tools())
            prompts = sorted(prompt.name for prompt in await client.list_prompts())
            print(f"Connected to manimgx MCP server ({len(tools)} tools, {len(prompts)} prompts)")
            for name in tools:
                print(f"  - {name}")
            for name in prompts:
                print(f"  prompt: {name}")

            created = await client.call_tool("project_create", {"name": "Connection Test"})
            if created.is_error:
                raise RuntimeError("project_create failed")
            project_id = created.structured_content["project_id"]
            print(f"Created project: {project_id}")
            print("Connection test passed.")


if __name__ == "__main__":
    asyncio.run(main())
