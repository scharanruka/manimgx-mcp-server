# manimgx-mcp-server

An open-source, self-hostable MCP server for generating short technical
animation clips with [ManimGX](https://github.com/ManimGX/ManimGX). An MCP client
plans a storyboard, writes ManimGX scenes, lints/inspects/renders previews, and
approves clips for final 1080p rendering.

> **ManimGX is not ManimCE.** Do not install or import `manim`. Scenes use
> `import manimgx as m`. Typesetting is **Typst only** — no LaTeX installation is
> required.

## Status

Milestone 1 complete: project scaffold, Python 3.14 environment, ManimGX smoke
test, and a rendered example clip.

## Requirements

- Python 3.13–3.14 (pinned to 3.14 for development)
- [uv](https://docs.astral.sh/uv/)
- Vulkan-capable GPU, or Mesa lavapipe for CPU fallback (ManimGX bundles a
  lavapipe fallback)
- `ffmpeg` for post-processing (not required for ManimGX encoding)
- `mpv` (optional, for desktop previews)

On Arch Linux:

```bash
sudo pacman -S --needed uv mpv ffmpeg git base-devel \
  vulkan-icd-loader vulkan-headers vulkan-tools mesa
```

## Setup

```bash
uv sync
uv run manimgx --version
```

## Smoke test

Render the example scene at preview quality:

```bash
uv run manimgx render examples/scenes/scene_01.py Scene01 \
  -r 480x854 --fps 15 --preset ultrafast
```

This writes `examples/scenes/Scene01.mp4` and a storyboard PNG beside the scene.
Inspect the scene without encoding video:

```bash
uv run manimgx inspect examples/scenes/scene_01.py Scene01 -t 1.5,end \
  -o examples/scenes/Scene01.inspect.png
```

## Run the MCP server

Local development:

```bash
uv run manimgx-mcp
```

MCP client configuration for local development:

```json
{
  "mcpServers": {
    "manimgx": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/manimgx-mcp-server",
        "manimgx-mcp"
      ]
    }
  }
}
```

The server speaks MCP over stdio. Logs go to stderr so stdout stays reserved for
the protocol.

## Project layout

```text
src/manimgx_mcp/     MCP server package
examples/scenes/     example ManimGX scenes
tests/               test suite
PLAN.md              build specification and milestones
```

## Development

```bash
uv run pytest
uv run ruff check .
```

## License

MIT
