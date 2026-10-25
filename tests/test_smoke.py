from __future__ import annotations

import importlib.metadata

import manimgx as m

from manimgx_mcp.server import mcp


def test_manimgx_exposes_typst_scene_api() -> None:
    assert hasattr(m, "Typst")
    assert hasattr(m, "MathTypst")
    assert hasattr(m, "Scene")


def test_manimgx_is_not_manim() -> None:
    assert m.__name__ == "manimgx"
    version = importlib.metadata.version("manimgx")
    assert tuple(int(part) for part in version.split(".")[:3]) >= (0, 1, 1)


def test_mcp_server_instance() -> None:
    assert mcp.name == "manimgx"
