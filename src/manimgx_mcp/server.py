"""FastMCP entry point for the ManimGX MCP server."""

from __future__ import annotations

import logging
import sys

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="manimgx",
    instructions=(
        "Generate short technical animation clips with ManimGX. "
        "Use Typst for all typesetting; never use LaTeX or ManimCE."
    ),
)


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
