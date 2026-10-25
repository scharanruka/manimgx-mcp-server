from __future__ import annotations

from pathlib import Path

import pytest

from lint import lint_scene
from models import ProjectConfig

ROOT = Path(__file__).resolve().parents[1]


def scene(body: str, imports: str = "import manimgx as m") -> str:
    lines = [imports, "", "class Scene01(m.Scene):", "    def construct(self) -> None:"]
    for line in body.splitlines():
        lines.append(f"        {line}")
    return "\n".join(lines) + "\n"


@pytest.fixture
def config() -> ProjectConfig:
    return ProjectConfig.default_for("9:16")


def test_example_scene_passes(config: ProjectConfig) -> None:
    code = (ROOT / "examples/scenes/scene_01.py").read_text()
    report = lint_scene(code, config)
    assert report.ok
    assert report.class_name == "Scene01"
    assert report.issues == []
    assert report.estimated_duration is not None


@pytest.mark.parametrize(
    ("bad_import", "rule"),
    [
        ("import os", "forbidden_import"),
        ("import sys", "forbidden_import"),
        ("import subprocess", "forbidden_import"),
        ("import socket", "forbidden_import"),
        ("import requests", "forbidden_import"),
        ("import urllib.request", "forbidden_import"),
        ("import pathlib", "forbidden_import"),
        ("import shutil", "forbidden_import"),
        ("import manim", "forbidden_import"),
        ("import numpy", "disallowed_import"),
    ],
)
def test_forbidden_imports(bad_import: str, rule: str, config: ProjectConfig) -> None:
    report = lint_scene(scene('text = m.Typst("x")', f"import manimgx as m\n{bad_import}"), config)
    assert not report.ok
    assert any(issue.rule == rule for issue in report.issues)


@pytest.mark.parametrize(
    "call", ["open", "eval", "exec", "compile", "__import__", "globals", "locals"]
)
def test_forbidden_calls(call: str, config: ProjectConfig) -> None:
    report = lint_scene(scene(f'{call}("x")\ntext = m.Typst("x")'), config)
    assert not report.ok
    assert any(issue.rule == "forbidden_call" for issue in report.issues)


def test_rejects_mathtex_for_typst_project(config: ProjectConfig) -> None:
    report = lint_scene(scene('formula = m.MathTex("x")'), config)
    assert not report.ok
    assert {issue.rule for issue in report.issues} >= {"latex_not_allowed", "missing_typst"}


def test_allows_mathtex_when_latex_is_configured(config: ProjectConfig) -> None:
    latex_config = config.model_copy(update={"math_syntax": "latex"})
    report = lint_scene(scene('formula = m.MathTex("x")'), latex_config)
    assert report.ok, report.issues


def test_requires_typst(config: ProjectConfig) -> None:
    report = lint_scene(scene('text = "plain"'), config)
    assert not report.ok
    assert any(issue.rule == "missing_typst" for issue in report.issues)


def test_requires_manimgx_alias(config: ProjectConfig) -> None:
    code = scene('text = m.Typst("x")').replace("import manimgx as m", "import manimgx")
    report = lint_scene(code, config)
    assert not report.ok
    assert any(issue.rule == "missing_manimgx_alias" for issue in report.issues)


def test_requires_exactly_one_scene_class(config: ProjectConfig) -> None:
    no_scene = 'import manimgx as m\n\ntext = m.Typst("x")\n'
    report = lint_scene(no_scene, config)
    assert not report.ok
    assert any(issue.rule == "missing_scene_class" for issue in report.issues)

    two_scenes = scene('text = m.Typst("x")') + "\n\nclass Scene02(m.Scene):\n    pass\n"
    report = lint_scene(two_scenes, config)
    assert not report.ok
    assert any(issue.rule == "multiple_scene_classes" for issue in report.issues)


def test_requires_construct_with_none_return(config: ProjectConfig) -> None:
    missing = "import manimgx as m\n\nclass Scene01(m.Scene):\n    pass\n"
    report = lint_scene(missing, config)
    assert any(issue.rule == "missing_construct" for issue in report.issues)

    bad_return = scene('text = m.Typst("x")').replace("-> None", "-> int")
    report = lint_scene(bad_return, config)
    assert any(issue.rule == "construct_return" for issue in report.issues)


def test_rejects_infinite_while(config: ProjectConfig) -> None:
    body = 'while True:\n    self.wait(1)\ntext = m.Typst("x")'
    report = lint_scene(scene(body), config)
    assert any(issue.rule == "infinite_loop" for issue in report.issues)

    body = 'while True:\n    self.wait(1)\n    break\ntext = m.Typst("x")'
    report = lint_scene(scene(body), config)
    assert not any(issue.rule == "infinite_loop" for issue in report.issues)


def test_long_scene_is_not_an_error(config: ProjectConfig) -> None:
    report = lint_scene(scene('text = m.Typst("x")\nself.wait(50)'), config)
    assert report.ok
    assert report.estimated_duration == 50
    assert any(
        issue.rule == "long_wait" and issue.severity == "warning" for issue in report.issues
    )


def test_long_run_time_warns(config: ProjectConfig) -> None:
    report = lint_scene(
        scene('text = m.Typst("x")\nself.play(m.Write(text), run_time=3.0)'), config
    )
    assert report.ok
    assert any(
        issue.rule == "long_run_time" and issue.severity == "warning" for issue in report.issues
    )


def test_safe_area_is_a_warning(config: ProjectConfig) -> None:
    body = 'text = m.Typst("x")\ntext.move_to([10, 0, 0])'
    report = lint_scene(scene(body), config)
    assert report.ok
    warnings = [issue for issue in report.issues if issue.rule == "safe_area"]
    assert warnings and warnings[0].severity == "warning"


def test_line_limit(config: ProjectConfig) -> None:
    code = scene('text = m.Typst("x")') + "\n" * 210
    report = lint_scene(code, config)
    assert any(issue.rule == "too_long" for issue in report.issues)


def test_syntax_error_is_structured(config: ProjectConfig) -> None:
    report = lint_scene("def broken(:\n", config)
    assert not report.ok
    assert report.issues[0].rule == "syntax_error"


def test_issue_cap(config: ProjectConfig) -> None:
    bad_imports = "\n".join(["import os"] * 12)
    report = lint_scene(scene('text = m.Typst("x")', bad_imports), config)
    assert not report.ok
    assert report.truncated
    assert len(report.issues) == 10
