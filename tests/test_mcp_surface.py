from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

import db
from models import Storyboard
from server import mcp, project_create, scene_lint, scene_write, storyboard_save

CODE = """import manimgx as m


class Scene01(m.Scene):
    def construct(self) -> None:
        text = m.Typst("Resource")
        self.play(m.Write(text))
"""


@pytest.fixture
def projects_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "projects"
    monkeypatch.setenv("MANIM_MCP_HOME", str(home))
    return home


@pytest.fixture
def project(projects_home: Path):
    project = project_create("MCP Surface")
    storyboard_save(
        project.project_id,
        Storyboard.model_validate(
            {
                "project_id": project.project_id,
                "clips": [
                    {
                        "scene_id": "scene_01",
                        "title": "Surface clip",
                        "beats": [
                            {"action": "write the word Resource", "objects": ["text"]}
                        ],
                        "key_objects": ["text"],
                        "typst_expressions": ["Resource"],
                    }
                ],
            }
        ),
    )
    scene_write(project.project_id, "scene_01", CODE)
    scene_lint(project.project_id, "scene_01")
    return project


def _text(contents: list[Any]) -> str:
    item = contents[0]
    return item.text


def _prompt_text(result: Any) -> str:
    return result.messages[0].content.text


@pytest.mark.asyncio
async def test_resources_and_prompts_are_registered(project) -> None:
    project_id = project.project_id
    async with Client(mcp) as client:
        templates = {template.uri_template for template in await client.list_resource_templates()}
        assert "manimgx://projects/{project_id}/config" in templates
        assert "manimgx://projects/{project_id}/storyboard" in templates
        assert "manimgx://projects/{project_id}/scenes/{scene_id}/code" in templates
        assert "manimgx://projects/{project_id}/scenes/{scene_id}/report" in templates

        prompts = {prompt.name for prompt in await client.list_prompts()}
        assert {
            "storyboard_plan_prompt",
            "scene_generate_prompt",
            "scene_edit_prompt",
        } <= prompts

        config = json.loads(
            _text(await client.read_resource(f"manimgx://projects/{project_id}/config"))
        )
        assert config["aspect_ratio"] == "9:16"

        storyboard = json.loads(
            _text(await client.read_resource(f"manimgx://projects/{project_id}/storyboard"))
        )
        assert storyboard["clips"][0]["scene_id"] == "scene_01"

        code = _text(
            await client.read_resource(f"manimgx://projects/{project_id}/scenes/scene_01/code")
        )
        assert code == CODE

        report = json.loads(
            _text(
                await client.read_resource(
                    f"manimgx://projects/{project_id}/scenes/scene_01/report"
                )
            )
        )
        assert report["status"] == "lint_passed"
        assert report["reports"]["lint"]["ok"] is True


@pytest.mark.asyncio
async def test_prompts_contain_project_context(project) -> None:
    project_id = project.project_id
    async with Client(mcp) as client:
        storyboard = _prompt_text(
            await client.get_prompt(
                "storyboard_plan_prompt",
                {
                    "project_id": project_id,
                    "brief": "Explain the chain rule",
                    "clip_count": 2,
                    "seconds_per_clip": 10,
                },
            )
        )
        assert "DeepSeek Pro" in storyboard
        assert '"project_id"' in storyboard
        assert "scene_02" in storyboard
        assert "Typst" in storyboard

        generated = _prompt_text(
            await client.get_prompt(
                "scene_generate_prompt",
                {"project_id": project_id, "scene_id": "scene_01"},
            )
        )
        assert "DeepSeek Flash" in generated
        assert "class Scene01(m.Scene)" in generated
        assert "m.MathTypst" in generated

        db.review_scene(project_id, "scene_01", "rejected", "Fix the layout")
        edited = _prompt_text(
            await client.get_prompt(
                "scene_edit_prompt",
                {"project_id": project_id, "scene_id": "scene_01"},
            )
        )
        assert "Fix the layout" in edited
        assert CODE.strip() in edited


@pytest.mark.asyncio
async def test_build_tools_are_registered() -> None:
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    assert {"project_build", "project_export"} <= set(tools)
    schema = tools["project_build"].input_schema["properties"]
    assert {"project_id", "code", "review", "inspect", "force"} <= set(schema)


@pytest.mark.asyncio
async def test_scene_prompt_uses_beats_not_seconds(project) -> None:
    async with Client(mcp) as client:
        prompt = _prompt_text(
            await client.get_prompt(
                "scene_generate_prompt",
                {"project_id": project.project_id, "scene_id": "scene_01"},
            )
        )
    assert "The clip's duration is whatever the beats produce" in prompt
    assert "Do not add self.wait() longer than 1 second" in prompt
    assert "45 seconds" not in prompt
    assert "target_seconds" not in prompt
