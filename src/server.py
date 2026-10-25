"""FastMCP entry point for the ManimGX MCP server."""

from __future__ import annotations

import logging
import shutil
import sys
from typing import Any, Literal

from fastmcp import FastMCP

import db
from models import ProjectConfig, ProjectSummary, apply_theme
from paths import create_project_dir, new_project_id, project_dir

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="manimgx",
    instructions=(
        "Generate short technical animation clips with ManimGX. "
        "Use Typst for all typesetting; never use LaTeX or ManimCE."
    ),
)


@mcp.tool
def project_create(
    name: str,
    aspect_ratio: Literal["9:16", "16:9"] = "9:16",
    theme: dict[str, Any] | None = None,
) -> ProjectSummary:
    """Create an animation project and return its paths and config."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("project name must not be empty")
    if len(clean_name) > 120:
        raise ValueError("project name must be at most 120 characters")

    config = apply_theme(ProjectConfig.default_for(aspect_ratio), theme)

    project_id = ""
    for _ in range(5):
        project_id = new_project_id(clean_name)
        try:
            create_project_dir(project_id)
        except FileExistsError:
            continue
        break
    else:
        raise RuntimeError("could not allocate a unique project id")

    try:
        db.create_project_record(project_id, clean_name, config)
    except BaseException:
        shutil.rmtree(project_dir(project_id), ignore_errors=True)
        raise

    logger.info("created project %s", project_id)
    return db.get_project_summary(project_id)


@mcp.tool
def project_get(project_id: str) -> ProjectSummary:
    """Get a project with config, storyboard, scenes, and status counts."""
    return db.get_project_summary(project_id)


@mcp.tool
def project_update_theme(project_id: str, theme: dict[str, Any]) -> ProjectSummary:
    """Update validated project theme and render settings."""
    current = db.get_project_summary(project_id)
    config = apply_theme(current.config, theme)
    db.update_project_config(project_id, config)
    logger.info("updated theme for project %s", project_id)
    return db.get_project_summary(project_id)


def main() -> None:
    """Run the MCP server over stdio. stdout is reserved for the protocol."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("starting manimgx MCP server")
    mcp.run()


if __name__ == "__main__":
    main()
