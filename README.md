# manimgx-mcp-server

[![CI](https://github.com/scharanruka/manimgx-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/scharanruka/manimgx-mcp-server/actions/workflows/ci.yml)

An open-source, self-hostable MCP server for generating short technical animation
clips with [ManimGX](https://github.com/ManimGX/ManimGX). An MCP client plans a
storyboard, writes ManimGX scenes, lints and inspects them, renders previews,
collects approvals, renders final clips, and stitches an export.

> **ManimGX is not ManimCE.** Do not install or import `manim`. Scenes use
> `import manimgx as m`. Typesetting is **Typst only** — no LaTeX installation is
> required.

## Features

- **Fast previews** at 480×854, 15 fps
- **Final renders** at 1080×1920, 60 fps
- **Single-process batch rendering** — one ManimGX import and GPU initialization
  for a whole storyboard
- **Typst-only scenes** with no LaTeX dependency
- **Deterministic AST linting** before code execution
- **Layout inspection** with structured problem reports and storyboard frames
- **Bounded review state machine** with rejection reasons
- **SQLite persistence** that survives restarts and stateless MCP connections
- **Stitching, frame extraction, and mpv previews**
- **Strict path handling** — project/scene IDs cannot traverse directories or
  follow symlinks out of the project

All milestones in [`PLAN.md`](PLAN.md) are implemented.

## Requirements

- Python 3.13–3.14
- [uv](https://docs.astral.sh/uv/)
- Vulkan-capable GPU, or Mesa lavapipe for CPU fallback (ManimGX bundles a
  lavapipe fallback)
- `ffmpeg` and `ffprobe` for stitching and frame extraction
- `mpv` optionally, for desktop previews

On Arch Linux:

```bash
sudo pacman -S --needed uv mpv ffmpeg git base-devel \
  vulkan-icd-loader vulkan-headers vulkan-tools mesa
```

On Debian/Ubuntu:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt-get install -y ffmpeg libvulkan1 mesa-vulkan-drivers
```

Do **not** install `texlive`, `texlive-most`, or the `typst` CLI. ManimGX includes
fonts, Typst, and MP4 encoding.

## Install and run

### From a published package

```bash
uvx manimgx-mcp-server
```

The package exposes both `manimgx-mcp-server` and `manimgx-mcp` commands.

### From a local wheel

```bash
uv build
uvx --from dist/*.whl manimgx-mcp
```

### From source

```bash
git clone https://github.com/scharanruka/manimgx-mcp-server.git
cd manimgx-mcp-server
uv sync
uv run manimgx-mcp
```

Verify the environment:

```bash
uv run manimgx --version
uv run python scripts/check_connection.py
```

The connection script starts the real server over stdio, lists every tool,
prompt, and resource template, then creates a temporary project. Expected output
ends with `Connection test passed.`

## MCP client configuration

Recommended, using `uvx`:

```json
{
  "mcpServers": {
    "manimgx": {
      "command": "uvx",
      "args": ["manimgx-mcp-server"]
    }
  }
}
```

Local development:

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

The server speaks MCP over stdio. Logs go to stderr; stdout is reserved for the
protocol.

## Quick workflow

```text
project_create
  → storyboard_save
      → scene_write
          → scene_lint
              → scene_inspect
                  → scene_render_preview
                      → scene_review(approve)
                          → scene_render_final
                              → project_stitch
```

For 10–20 clips, use `project_render_batch` instead of rendering each clip
individually. It launches one worker process for the whole batch.

## Tools

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
| `project_render_batch` | Render many preview or final clips in one worker. |
| `scene_render_final` | Render a 1080p60 final; requires approval or `force`. |
| `scene_review` | Approve or reject a previewed scene with a reason. |
| `project_stitch` | Concatenate final clips into one exported MP4. |
| `scene_extract_frames` | Extract PNG review frames at requested times. |
| `scene_open_mpv` | Open a preview or final video in detached mpv. |

## MCP resources

Resources are cheaper than tools for reading context on demand.

| URI | Returns |
|---|---|
| `manimgx://projects/{project_id}/config` | Project config JSON |
| `manimgx://projects/{project_id}/storyboard` | Storyboard JSON |
| `manimgx://projects/{project_id}/scenes/{scene_id}/code` | Scene Python code |
| `manimgx://projects/{project_id}/scenes/{scene_id}/report` | Last lint/inspect/render reports |

## MCP prompts

The server does not call an LLM. Prompts tell the host client which model to use
and provide a compact, stable context. They never include chat history.

| Prompt | Model hint | Purpose |
|---|---|---|
| `storyboard_plan_prompt` | DeepSeek Pro | Produce Storyboard JSON only |
| `scene_generate_prompt` | DeepSeek Flash | Produce one complete ManimGX scene file |
| `scene_edit_prompt` | DeepSeek Flash | Repair a rejected scene from structured errors |

`scene_edit_prompt` sends the scene plan, config, current code (or an AST skeleton
when the file exceeds 80 lines), lint issues, inspect issues, and the rejection
reason.

## Storage

Projects live under `MANIM_MCP_HOME` (default `~/.manimgx-mcp/projects`).

```text
<MANIM_MCP_HOME>/<project_id>/
├── project.db
├── scenes/
├── renders/
│   ├── preview/
│   ├── final/
│   └── storyboards/
├── frames/
├── exports/
└── logs/
```

`project.db` is the source of truth. It stores projects, scenes, artifacts, and an
append-only event log. Project IDs are a sanitized slug plus a short UUID suffix.
Project and scene IDs are validated as single path components; traversal,
absolute paths, and symlink escapes are rejected.

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

## Rendering quality gates

`storyboard_save` requires 1–100 clips, each 1–30 seconds, with a total of 10–90
seconds. Scene IDs must be unique safe slugs.

`scene_lint` never executes scene code. It rejects:

- Forbidden imports: `manim`, `os`, `sys`, `subprocess`, `socket`, `requests`,
  `urllib`, `pathlib`, `shutil`
- Forbidden calls: `open`, `eval`, `exec`, `compile`, `__import__`, `globals`,
  `locals`, `input`, `breakpoint`
- Missing or multiple scene classes
- Missing or malformed `construct(self) -> None`
- Infinite `while True` without a break
- Scenes over 200 lines or 45 estimated seconds
- LaTeX typesetting in Typst projects
- Missing `m.Typst` / `m.MathTypst`

Safe-area coordinate problems are warnings, not failures. Reports are capped at 10
structured issues.

## Performance

Measured on the development machine with ManimGX 0.1.1:

```text
20 preview clips, 6.66s of video, 0.76s wall, 60s budget
final render: 1080×1920, H.264, 60 fps
```

Batch rendering imports ManimGX once, initializes the GPU once, and renders every
scene sequentially in one subprocess. It does not launch one `manimgx` process per
clip.

Run the benchmark:

```bash
uv run pytest tests/bench_batch.py -q -s
```

## Security

- Generated scene code is untrusted.
- Lint runs before execution; render workers run in subprocesses, never in the MCP
  server process.
- Every render has a timeout, and the batch worker has an additional per-scene
  timeout.
- Scene subprocesses run with the project directory as their working directory.
- Project and scene IDs are validated before path joins.
- Artifact paths are resolved back inside the project before deletion.
- The server writes logs to stderr only.

For higher isolation, run the server inside a container or wrap the render worker
with `bwrap` on systems that provide it.

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Skip the slow benchmark:

```bash
uv run pytest -m "not slow"
```

Build and smoke-test the wheel:

```bash
uv build
uvx --from dist/*.whl manimgx-mcp
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`AGENTS.md`](AGENTS.md).

### Releases

The release workflow runs on `v*` tags, builds the wheel and sdist, and publishes
them to PyPI through trusted publishing. Configure a PyPI trusted publisher for
the repository and the `pypi` GitHub environment before tagging.

## Project layout

```text
src/                 flat server modules (no package subdirectory)
examples/scenes/     example ManimGX scene
scripts/             stdio connection smoke client
tests/               unit and integration tests
.github/workflows/   CI and release workflows
PLAN.md              build specification and milestones
```

## License

MIT
