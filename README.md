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
- Milestone 5 complete: single-process batch preview rendering and final
  1080×1920, 60 fps output.
- Milestone 6 complete: review rejection loop, stitching, frame extraction, mpv
  preview, and the scene edit prompt.

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
| `project_render_batch` | Render many preview or final clips in one worker. |
| `scene_render_final` | Render a 1080p60 final; requires approval or `force`. |
| `scene_review` | Approve or reject a previewed scene with a reason. |
| `project_stitch` | Concatenate final clips into one exported MP4. |
| `scene_extract_frames` | Extract PNG review frames at requested times. |
| `scene_open_mpv` | Open a preview or final video in detached mpv. |

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

`project_render_batch` renders every requested clip in **one** `batch_worker.py`
subprocess: ManimGX is imported once and the GPU is initialized once, then each
scene is rendered sequentially. Preview batches write to `renders/preview/`,
final batches to `renders/final/`. Final rendering requires scenes to be
approved unless `scene_render_final(..., force=True)` is used.

Measured benchmark on the development machine:

```text
20 preview clips, 6.66s of video, 0.76s wall, budget 60s
```

Run the benchmark directly with:

```bash
uv run pytest tests/bench_batch.py -q -s
```

## Review and post-processing

```text
preview_rendered ──> scene_review(approve) ──> approved ──> scene_render_final
                 └─> scene_review(reject, reason) ──> rejected
                                                     └─> scene_edit_prompt
```

- `scene_review` is the only way scenes become `approved` or `rejected`.
- Rejections require a reason, stored on the scene and included in
  `scene_edit_prompt`.
- `project_stitch` concatenates final renders in storyboard order into
  `exports/<name>.mp4`, using stream copy and falling back to H.264 re-encoding.
- `scene_extract_frames` writes PNGs to `frames/<scene_id>/`, preferring the final
  video when one exists.
- `scene_open_mpv` starts a detached, looping mpv window; it returns a clear error
  when mpv is unavailable.

The MCP server also exposes the `scene_edit_prompt` prompt. It sends the scene
plan, config, current code (or an AST skeleton over 80 lines), lint issues,
inspect issues, and the rejection reason—not the chat history.

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
