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


def build_storyboard_plan_prompt(
    project_id: str,
    brief: str,
    clip_count: int = 6,
    seconds_per_clip: float = 12,
    style: str = "technical",
) -> str:
    """Build a storyboard planning prompt with a strict JSON output contract."""
    brief = brief.strip()
    if not brief:
        raise ValueError("brief must not be empty")
    if not 1 <= clip_count <= 100:
        raise ValueError("clip_count must be between 1 and 100")

    project = db.get_project_row(project_id)
    config = db.get_project_config(project_id)
    area = config.safe_area
    safe_area = f"x=[{area.x_min:g}, {area.x_max:g}], y=[{area.y_min:g}, {area.y_max:g}]"
    return f"""You are planning a ManimGX animation storyboard. Use DeepSeek Pro.
Return only valid Storyboard JSON — no prose, no Markdown fence, no Python.

Project: {project["name"]} ({project_id})
Brief:
{brief}

Style: {style}
Clip count: {clip_count}
Aspect ratio: {config.aspect_ratio}
Safe area: {safe_area}
Math syntax: Typst (never LaTeX).

Describe each clip as a list of beats, never as a duration. The clip's length is
whatever the beats produce — do not plan seconds or try to hit a target length.

Output schema:
{{
  "project_id": "{project_id}",
  "clips": [
    {{
      "scene_id": "scene_01",
      "title": "Short clip title",
      "beats": [
        {{"action": "draw the coordinate grid and the unit square",
          "objects": ["grid", "unit square"]}},
        {{"action": "apply A to the grid, shearing it into a parallelogram",
          "objects": ["grid", "matrix"],
          "typst": "A = mat(1, 0.5; 0.2, 0.8)"}},
        {{"action": "reveal the rank badge and the takeaway line",
          "objects": ["rank badge", "label"]}}
      ],
      "key_objects": ["grid", "matrix", "rank badge"],
      "typst_expressions": ["A = mat(1, 0.5; 0.2, 0.8)"],
      "notes": "Optional staging note"
    }}
  ]
}}

Rules:
- Produce exactly {clip_count} clips with unique slug scene_ids: scene_01, scene_02, ...
- Give each clip 3 to 6 beats. Each beat is one self.play(...) transition or one short sequence.
- Keep beat actions short and concrete. Order them as they should appear.
- Use Typst math expressions such as `sum_(k=1)^n k`, not LaTeX.
- Do not include target_seconds or total_seconds.
- Do not write Python code in this step.
"""


def build_scene_generate_prompt(project_id: str, scene_id: str) -> str:
    """Build a one-scene code generation prompt with a strict file contract."""
    detail = db.get_scene_detail(project_id, scene_id)
    config = db.get_project_config(project_id)
    area = config.safe_area
    safe_area = f"x=[{area.x_min:g}, {area.x_max:g}], y=[{area.y_min:g}, {area.y_max:g}]"
    parts = scene_id.replace("-", "_").split("_")
    if parts[0] == "scene":
        parts = parts[1:]
    class_name = "Scene" + "".join(part.title() for part in parts)
    beat_count = len(detail.plan.beats)
    duration_rule = (
        f"Build the scene around the {beat_count} beats in the plan. Each beat is one "
        "self.play(...) transition or one short sequence. Do not add self.wait() longer "
        "than 1 second. Do not use run_time greater than 2.0 unless the beat explicitly "
        "requires it. The clip's duration is whatever the beats produce — do not try to "
        "hit a specific length."
    )
    return f"""You are writing one ManimGX scene file. Use DeepSeek Flash.
Return only the complete Python file — no prose and no Markdown fence.

{duration_rule}

Scene plan:
{detail.plan.model_dump_json(indent=2, exclude_none=True)}

Project config:
{config.model_dump_json(indent=2)}

File contract:
```python
import manimgx as m

class {class_name}(m.Scene):
    def construct(self) -> None:
        ...
```

Rules:
- Use `m.Typst` and `m.MathTypst` only. Do not use `m.Tex` or `m.MathTex`.
- Import only `manimgx as m` and, if needed, `math`.
- Do not import manim, os, sys, subprocess, socket, requests, urllib, pathlib, or shutil.
- Define exactly one class inheriting `m.Scene` with `def construct(self) -> None:`.
- Do not set resolution or FPS in code; the renderer sets them.
- Stay inside the safe area: {safe_area}.
- Keep the file under 150 lines.
- Use `self.play(...)` and `self.wait(...)` only for time; never pad with waits.

Typst examples:
```python
formula = m.MathTypst("sum_(k=1)^n k = (n(n+1))/2", font_size=72)
text = m.Typst("*Bold*, _italic_, and math: $sum_(k=1)^n k$")
```
"""


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
{detail.plan.model_dump_json(indent=2, exclude_none=True)}

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
  Keep the scene under 150 lines and do not pad with waits to reach a length.
- Fix every listed lint and layout issue plus the rejection reason. Preserve the scene's intent.
"""
