"""Deterministic AST checks for generated ManimGX scenes."""

from __future__ import annotations

import ast
from typing import Literal

from models import LintIssue, LintReport, ProjectConfig

MAX_LINES = 200
MAX_ISSUES = 10
MAX_DURATION_SECONDS = 45.0

FORBIDDEN_MODULES = {
    "manim",
    "os",
    "sys",
    "subprocess",
    "socket",
    "requests",
    "urllib",
    "pathlib",
    "shutil",
}
FORBIDDEN_CALLS = {
    "open",
    "eval",
    "exec",
    "compile",
    "__import__",
    "globals",
    "locals",
    "input",
    "breakpoint",
}
SCENE_BASE_NAMES = {"Scene", "ThreeDScene"}
TYPST_NAMES = {"Typst", "MathTypst"}
LATEX_NAMES = {"Tex", "MathTex"}


class _IssueCollector:
    def __init__(self) -> None:
        self.issues: list[LintIssue] = []

    def add(
        self,
        rule: str,
        message: str,
        fix: str,
        *,
        line: int | None = None,
        severity: Literal["error", "warning"] = "error",
    ) -> None:
        self.issues.append(
            LintIssue(
                rule=rule,
                line=line,
                message=message,
                fix=fix,
                severity=severity,
            )
        )


def _check_imports(tree: ast.Module, issues: _IssueCollector) -> None:
    has_manimgx_m = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_MODULES:
                    message = (
                        "do not import manim; use manimgx as m"
                        if root == "manim"
                        else f"do not import {root}"
                    )
                    issues.add(
                        "forbidden_import",
                        message,
                        f"remove `import {alias.name}`",
                        line=node.lineno,
                    )
                elif alias.name == "manimgx":
                    if alias.asname == "m":
                        has_manimgx_m = True
                    else:
                        issues.add(
                            "missing_manimgx_alias",
                            "import manimgx with the alias m",
                            "change to `import manimgx as m`",
                            line=node.lineno,
                        )
                elif root == "math":
                    continue
                else:
                    issues.add(
                        "disallowed_import",
                        f"import {alias.name} is not allowed in scenes",
                        "remove it; scenes may import only manimgx as m and math",
                        line=node.lineno,
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".")[0]
            if root in FORBIDDEN_MODULES:
                message = (
                    "do not import manim; use manimgx as m"
                    if root == "manim"
                    else f"do not import {root}"
                )
                issues.add(
                    "forbidden_import",
                    message,
                    f"remove `from {module} import ...`",
                    line=node.lineno,
                )
            elif root in {"__future__", "math"}:
                continue
            else:
                issues.add(
                    "disallowed_import",
                    f"import from {module or '?'} is not allowed in scenes",
                    "import manimgx as m and math only",
                    line=node.lineno,
                )

    if not has_manimgx_m:
        issues.add(
            "missing_manimgx_import",
            "scene must import manimgx as m",
            "add `import manimgx as m`",
            line=1,
        )


def _is_scene_base(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "m"
        and node.attr in SCENE_BASE_NAMES
    )


def _scene_classes(tree: ast.Module) -> list[ast.ClassDef]:
    return [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and any(_is_scene_base(base) for base in node.bases)
    ]


def _construct_method(scene: ast.ClassDef) -> ast.FunctionDef | None:
    for node in scene.body:
        if isinstance(node, ast.FunctionDef) and node.name == "construct":
            return node
    return None


def _check_scene_class(tree: ast.Module, issues: _IssueCollector) -> ast.ClassDef | None:
    classes = _scene_classes(tree)
    if not classes:
        issues.add(
            "missing_scene_class",
            "file must define one class inheriting m.Scene",
            "add `class Scene01(m.Scene):`",
            line=1,
        )
        return None
    if len(classes) > 1:
        issues.add(
            "multiple_scene_classes",
            f"file defines {len(classes)} scene classes; exactly one is allowed",
            "keep one class inheriting m.Scene and move helpers elsewhere",
            line=classes[1].lineno,
        )

    scene = classes[0]
    construct = _construct_method(scene)
    if construct is None:
        issues.add(
            "missing_construct",
            f"{scene.name} has no construct method",
            "add `def construct(self) -> None:`",
            line=scene.lineno,
        )
        return scene

    positional = construct.args.posonlyargs + construct.args.args
    if (
        len(positional) != 1
        or positional[0].arg != "self"
        or construct.args.vararg is not None
        or construct.args.kwarg is not None
    ):
        issues.add(
            "construct_signature",
            "construct must take only self",
            "use `def construct(self) -> None:`",
            line=construct.lineno,
        )

    returns = construct.returns
    valid_return = isinstance(returns, ast.Constant) and returns.value in {None, "None"}
    if not valid_return:
        issues.add(
            "construct_return",
            "construct must be annotated `-> None`",
            "use `def construct(self) -> None:`",
            line=construct.lineno,
        )
    return scene


def _check_forbidden_calls(tree: ast.Module, issues: _IssueCollector) -> None:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in FORBIDDEN_CALLS
        ):
            issues.add(
                "forbidden_call",
                f"do not call {node.func.id}() in scene code",
                f"remove {node.func.id}()",
                line=node.lineno,
            )


def _is_true_constant(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and (node.value is True or node.value == 1)


class _BreakFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found = False
        self._nested_loops = 0

    def visit_Break(self, node: ast.Break) -> None:
        if self._nested_loops == 0:
            self.found = True

    def visit_While(self, node: ast.While) -> None:
        self._nested_loops += 1
        self.generic_visit(node)
        self._nested_loops -= 1

    def visit_For(self, node: ast.For) -> None:
        self._nested_loops += 1
        self.generic_visit(node)
        self._nested_loops -= 1

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit_For(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _body_has_break(body: list[ast.stmt]) -> bool:
    finder = _BreakFinder()
    for statement in body:
        finder.visit(statement)
        if finder.found:
            return True
    return False


def _check_infinite_loops(tree: ast.Module, issues: _IssueCollector) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.While) and _is_true_constant(node.test):
            if not _body_has_break(node.body):
                issues.add(
                    "infinite_loop",
                    "`while True` has no break in its own body",
                    "add a terminating break or use a bounded loop",
                    line=node.lineno,
                )


def _literal_number(node: ast.expr) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            return None
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _literal_number(node.operand)
        return None if value is None else -value
    return None


def _duration_value(node: ast.AST | None, default: float) -> float:
    if node is None:
        return default
    value = _literal_number(node) if isinstance(node, ast.expr) else None
    return default if value is None else max(0.0, value)


def _estimate_duration(scene: ast.ClassDef | None) -> float:
    if scene is None:
        return 0.0
    construct = _construct_method(scene)
    if construct is None:
        return 0.0

    total = 0.0
    for node in ast.walk(construct):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "self":
            continue
        if node.func.attr == "play":
            run_time = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "run_time"),
                None,
            )
            total += _duration_value(run_time, 1.0)
        elif node.func.attr == "wait":
            duration = node.args[0] if node.args else None
            if duration is None:
                duration = next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "duration"),
                    None,
                )
            total += _duration_value(duration, 1.0)
    return total


def _check_typesetting(tree: ast.Module, config: ProjectConfig, issues: _IssueCollector) -> None:
    m_attributes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "m"
    ]
    used_typst = any(node.attr in TYPST_NAMES for node in m_attributes)
    used_latex = [node for node in m_attributes if node.attr in LATEX_NAMES]

    if used_latex and config.math_syntax != "latex":
        for node in used_latex:
            issues.add(
                "latex_not_allowed",
                f"m.{node.attr} is not allowed; this project uses Typst",
                f"replace m.{node.attr} with m.MathTypst or m.Typst",
                line=node.lineno,
            )

    has_typesetting = used_typst or (config.math_syntax == "latex" and bool(used_latex))
    if not has_typesetting:
        issues.add(
            "missing_typst",
            "scene must use m.Typst or m.MathTypst",
            "add m.Typst(...) or m.MathTypst(...) for its text",
            line=1,
        )


def _literal_coordinates(node: ast.expr) -> tuple[float, float] | None:
    if not isinstance(node, (ast.List, ast.Tuple)) or len(node.elts) < 2:
        return None
    x = _literal_number(node.elts[0])
    y = _literal_number(node.elts[1])
    if x is None or y is None:
        return None
    return x, y


def _warn_position(
    x: float, y: float, config: ProjectConfig, line: int, issues: _IssueCollector
) -> None:
    area = config.safe_area
    if x < area.x_min or x > area.x_max:
        issues.add(
            "safe_area",
            f"x={x:g} is outside the safe area [{area.x_min:g}, {area.x_max:g}]",
            "keep generated content inside the project safe area",
            line=line,
            severity="warning",
        )
    if y < area.y_min or y > area.y_max:
        issues.add(
            "safe_area",
            f"y={y:g} is outside the safe area [{area.y_min:g}, {area.y_max:g}]",
            "keep generated content inside the project safe area",
            line=line,
            severity="warning",
        )


def _check_safe_area(tree: ast.Module, config: ProjectConfig, issues: _IssueCollector) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        if method == "move_to" and node.args:
            coordinates = _literal_coordinates(node.args[0])
            if coordinates is not None:
                _warn_position(*coordinates, config, node.lineno, issues)
        elif method in {"set_x", "set_y"} and node.args:
            value = _literal_number(node.args[0])
            if value is None:
                continue
            if method == "set_x":
                _warn_position(value, 0.0, config, node.lineno, issues)
            else:
                _warn_position(0.0, value, config, node.lineno, issues)


def lint_scene(code: str, config: ProjectConfig) -> LintReport:
    """Lint a ManimGX scene without executing it."""
    issues = _IssueCollector()

    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError) as exc:
        line = getattr(exc, "lineno", None) or 1
        message = getattr(exc, "msg", None) or str(exc)
        issues.add(
            "syntax_error",
            f"scene is not valid Python: {message}",
            "fix the syntax error before linting again",
            line=line,
        )
        return LintReport(ok=False, issues=issues.issues[:MAX_ISSUES], truncated=False)

    line_count = len(code.splitlines())
    if line_count > MAX_LINES:
        issues.add(
            "too_long",
            f"scene has {line_count} lines; the limit is {MAX_LINES}",
            "move helpers out and keep the scene under 200 lines",
            line=MAX_LINES + 1,
        )

    _check_imports(tree, issues)
    scene = _check_scene_class(tree, issues)
    _check_forbidden_calls(tree, issues)
    _check_infinite_loops(tree, issues)

    duration = _estimate_duration(scene)
    if duration > MAX_DURATION_SECONDS:
        issues.add(
            "duration",
            f"estimated duration is {duration:.1f}s; the limit is 45s",
            "shorten plays and waits to 45 seconds or less",
            line=1,
        )

    _check_typesetting(tree, config, issues)
    _check_safe_area(tree, config, issues)

    all_issues = issues.issues
    ok = not any(issue.severity == "error" for issue in all_issues)
    return LintReport(
        ok=ok,
        issues=all_issues[:MAX_ISSUES],
        truncated=len(all_issues) > MAX_ISSUES,
        estimated_duration=round(duration, 2),
        class_name=scene.name if scene is not None else None,
    )
