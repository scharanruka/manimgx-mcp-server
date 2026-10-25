"""Configuration defaults and environment lookups."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final

PROJECTS_HOME_ENV: Final = "MANIM_MCP_HOME"
DEFAULT_PROJECTS_HOME: Final = Path("~/.manimgx-mcp/projects")

PROJECT_STATUSES: Final = (
    "planned",
    "code_written",
    "lint_passed",
    "inspected",
    "preview_rendered",
    "approved",
    "rejected",
    "final_rendered",
)

X264_PRESETS: Final = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
    "placebo",
)

ASPECT_RATIOS: Final = ("9:16", "16:9")
MATH_SYNTAXES: Final = ("typst", "latex")

RENDER_TIMEOUT_SECONDS: Final = 120
INSPECT_STDOUT_TAIL: Final = 4096
RENDER_STDOUT_TAIL: Final = 2048

ASPECT_DEFAULTS: Final[dict[str, dict[str, Any]]] = {
    "9:16": {
        "final_width": 1080,
        "final_height": 1920,
        "preview_width": 480,
        "preview_height": 854,
        "safe_area": {"x_min": -4.0, "x_max": 4.0, "y_min": -7.0, "y_max": 7.0},
    },
    "16:9": {
        "final_width": 1920,
        "final_height": 1080,
        "preview_width": 854,
        "preview_height": 480,
        "safe_area": {"x_min": -7.0, "x_max": 7.0, "y_min": -4.0, "y_max": 4.0},
    },
}


def projects_home() -> Path:
    """Return the configured project home, expanding ``~`` and env overrides."""
    raw = os.environ.get(PROJECTS_HOME_ENV)
    path = Path(raw) if raw else DEFAULT_PROJECTS_HOME
    return path.expanduser().resolve(strict=False)


def default_config(aspect_ratio: str = "9:16") -> dict[str, Any]:
    """Build the default config for an aspect ratio."""
    if aspect_ratio not in ASPECT_DEFAULTS:
        raise ValueError(f"unsupported aspect ratio: {aspect_ratio}")
    dimensions = ASPECT_DEFAULTS[aspect_ratio]
    return {
        "aspect_ratio": aspect_ratio,
        "final_width": dimensions["final_width"],
        "final_height": dimensions["final_height"],
        "final_fps": 60,
        "preview_width": dimensions["preview_width"],
        "preview_height": dimensions["preview_height"],
        "preview_fps": 15,
        "preview_preset": "ultrafast",
        "preview_crf": 30,
        "final_preset": "medium",
        "final_crf": 18,
        "background_color": "BLACK",
        "text_color": "WHITE",
        "accent_color": "BLUE",
        "math_syntax": "typst",
        "safe_area": dict(dimensions["safe_area"]),
        "font_sizes": {"title": 64, "body": 32, "caption": 24},
    }
