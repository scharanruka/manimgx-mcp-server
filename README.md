# manimgx-mcp-server

An open-source, self-hostable MCP server for generating short technical
animation clips with [ManimGX](https://github.com/ManimGX/ManimGX). An MCP client
plans a storyboard, writes ManimGX scenes, lints/inspects/renders previews, and
approves clips for final 1080p rendering.

> **ManimGX is not ManimCE.** Do not install or import `manim`. Scenes use
> `import manimgx as m`. Typesetting is **Typst only** — no LaTeX installation is
> required.

## Status

- Milestone 1 complete: project scaffold, Python 3.14 environment, ManimGX smoke
  test, and a rendered example clip.
- Milestone 2 complete: SQLite persistence, project paths with traversal
  protection, and `project_create`, `project_get`, `project_update_theme`.
- Milestone 3 complete: storyboard persistence, scene storage, and deterministic
  AST linting.
- Milestone 4 complete: layout inspection and preview rendering at 480×854,
  15 fps.

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

### Connection test

Run the stdio smoke client to start the server, list every tool, and create a
temporary project:

```bash
uv run python scripts/check_connection.py
```

Expected output includes `Connection test passed.` The same path is covered by
`tests/test_connection.py`.

## Projects

Projects live under `MANIM_MCP_HOME` (default `~/.manimgx-mcp/projects`). Each
project has its own `project.db`, scene directory, render directories, frames,
exports, and logs. Project and scene IDs are validated as single path components,
and traversal or symlink escapes are rejected.

| Tool | Description |
|---|---|
| `project_create` | Create a project with a 9:16 or 16:9 config. |
| `project_get` | Read config, paths, storyboard, scenes, and status counts. |
| `project_update_theme` | Update validated colors, fonts, safe area, and render settings. |
| `storyboard_save` | Validate a storyboard and create its planned scenes. |
| `scene_write` | Write scene code and clear stale artifacts on change. |
| `scene_list` | List scenes with status and code metadata. |
| `scene_get` | Read one scene's plan, code, and artifacts. |
| `scene_lint` | Run deterministic AST checks without executing code. |
| `scene_inspect` | Check layout and generate storyboard frames without video. |
| `scene_render_preview` | Render a 480p15 preview and return its report. |

`project_update_theme` accepts only these keys:

```json
{
  "background_color": "BLACK",
  "text_color": "WHITE",
  "accent_color": "BLUE",
  "font_sizes": {"title": 64, "body": 32, "caption": 24},
  "safe_area": {"x_min": -4, "x_max": 4, "y_min": -7, "y_max": 7},
  "preview_width": 480,
  "preview_height": 854,
  "preview_fps": 15,
  "preview_preset": "ultrafast",
  "preview_crf": 30,
  "final_width": 1080,
  "final_height": 1920,
  "final_fps": 60,
  "final_preset": "medium",
  "final_crf": 18
}
```

Partial updates are merged; nested `font_sizes` and `safe_area` also merge.

## Scene workflow

```text
storyboard_save → scene_write → scene_lint
                                   ├─ fail → edit code → scene_write
                                   └─ pass → scene_inspect
                                               ├─ fail → edit code → scene_write
                                               └─ pass → scene_render_preview
                                                           → preview_rendered
```

`storyboard_save` requires 1–100 clips, each 1–30 seconds, with a total of 10–90
seconds. Scene IDs are unique slugs. `scene_write` stores the code in SQLite and
in `scenes/<scene_id>.py`, hashes it, and resets the status to `code_written`.
Changing the code clears previously recorded artifacts.

`scene_lint` never executes scene code. It rejects forbidden imports and calls,
missing or multiple scene classes, invalid `construct` signatures, infinite
`while True` loops, scenes over 200 lines or 45 estimated seconds, LaTeX
typesetting in Typst projects, and missing Typst content. Safe-area coordinate
problems are warnings rather than failures.

`scene_inspect` runs ManimGX inspect in the project directory and writes
annotated frames to `renders/storyboards/`. It returns the exit code, compact
stdout/stderr, storyboard paths, and parsed layout problems.

`scene_render_preview` uses the project's preview settings (480×854, 15 fps,
`ultrafast`, CRF 30 by default) and writes
`renders/preview/<scene_id>.mp4`.

## Project layout

```text
src/                 server, config, models, paths, and database modules
examples/scenes/     example ManimGX scenes
scripts/             local connection smoke client
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
