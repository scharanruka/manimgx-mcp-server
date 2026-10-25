"""MCP prompt templates."""

from __future__ import annotations

import ast
from typing import Any

import db


def _skeleton(code: str) -> str:
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return code[:4000].rstrip() + "\n# ... (truncated, invalid Python)"

    lines: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            lines.append(ast.unparse(node))
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(base) for base in node.bases)
            lines.append(f"class {node.name}({bases}):")
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    returns = f" -> {ast.unparse(item.returns)}" if item.returns else ""
                    lines.append(f"    def {item.name}({ast.unparse(item.args)}){returns}: ...")
                else:
                    lines.append("    ...")
        elif isinstance(node, ast.FunctionDef):
            returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
            lines.append(f"def {node.name}({ast.unparse(node.args)}){returns}: ...")
    return "\n".join(lines) or "# empty scene"


def _lint_lines(report: dict[str, Any] | None) -> str:
    if not report:
        return "- none recorded"
    issues = report.get("issues") or []
    if not issues:
        return "- none"
    lines = []
    for issue in issues:
        line = f"line {issue.get('line')}: " if issue.get("line") else ""
        lines.append(
            f"- [{issue.get('rule')}] {line}{issue.get('message')} Fix: {issue.get('fix')}"
        )
    return "\n".join(lines)


def _inspect_lines(report: dict[str, Any] | None) -> str:
    if not report:
        return "- none recorded"
    issues = report.get("issues") or []
    if not issues:
        return "- none"
    return "\n".join(f"- {issue.get('message')}" for issue in issues)


def build_scene_edit_prompt(
    project_id: str,
    scene_id: str,
    rejection_reason: str | None = None,
) -> str:
    """Build a compact edit prompt from stored scene state, never chat history."""
    detail = db.get_scene_detail(project_id, scene_id)
    config = db.get_project_config(project_id)
    reason = (rejection_reason or detail.rejection_reason or "").strip()
    if not reason:
        reason = "No rejection reason supplied."

    code = detail.code or "# no code written yet"
    shown_code = _skeleton(code) if len(code.splitlines()) > 80 else code
    code_note = "AST skeleton (file exceeds 80 lines)" if shown_code != code else "current code"

    lint_report = detail.artifacts.get("lint")
    inspect_report = detail.artifacts.get("inspect")
    lint_text = _lint_lines(lint_report if isinstance(lint_report, dict) else None)
    inspect_text = _inspect_lines(inspect_report if isinstance(inspect_report, dict) else None)

    return f"""You are editing one ManimGX scene. Use DeepSeek Flash.
Return only the complete replacement Python file, with no prose or code fence.

Scene: {detail.scene_id} — {detail.title}
Scene plan:
{detail.plan.model_dump_json(indent=2)}

Project config:
{config.model_dump_json(indent=2)}

Human rejection reason:
{reason}

Lint issues:
{lint_text}

Inspect layout issues:
{inspect_text}

{code_note}:
```python
{shown_code}
```

Rules:
- Use `import manimgx as m`; import `math` only if needed.
  Never import manim, os, sys, subprocess, socket, requests, urllib, pathlib, or shutil.
- Use `m.Typst` and `m.MathTypst` only. Do not use `m.Tex` or `m.MathTex`.
- Define exactly one class inheriting `m.Scene` with `def construct(self) -> None:`.
- Do not set resolution or FPS in code.
- Stay inside the safe area from the project config.
- Use `self.play(...)` and `self.wait(...)` only for time.
  Keep the scene at most 45 seconds and under 120 lines.
- Fix every listed lint and layout issue plus the rejection reason. Preserve the scene's intent.
"""
