# Contributing

Thanks for helping improve `manimgx-mcp-server`. This project uses **ManimGX**, not
ManimCE. Read [`AGENTS.md`](AGENTS.md) before changing scene-facing code.

## Setup

```bash
git clone https://github.com/scharanruka/manimgx-mcp-server.git
cd manimgx-mcp-server
uv sync
uv run manimgx --version
uv run python scripts/check_connection.py
```

Requirements:

- Python 3.13–3.14
- `ffmpeg` and `ffprobe` for post-processing tests
- Vulkan-capable GPU or Mesa lavapipe
- No LaTeX and no separate `typst` CLI

## Development checks

Run these before opening a pull request:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The complete suite includes real ManimGX renders. To skip the 20-scene benchmark:

```bash
uv run pytest -m "not slow"
```

Build and verify the wheel:

```bash
uv build
uvx --from dist/*.whl manimgx-mcp
```

## Architecture

Modules live flat under `src/` and are installed at the wheel root:

| Module | Responsibility |
|---|---|
| `server.py` | FastMCP instance, tools, resources, prompts, entry point |
| `config.py` | Environment, defaults, limits, statuses |
| `models.py` | Pydantic API models |
| `db.py` | SQLite schema and queries |
| `paths.py` | ID validation and safe path resolution |
| `lint.py` | AST quality gate and scene contract checks |
| `inspection.py` | `manimgx inspect` wrapper and parser |
| `render.py` | Single previews and batch worker orchestration |
| `batch_worker.py` | One-process, multi-scene renderer |
| `post.py` | ffmpeg stitching, frames, mpv |
| `prompts.py` | Compact MCP prompt builders |

Keep `src/` flat. Do not introduce a package subdirectory without updating the
hatch wheel configuration and every console entry point.

## Scene rules

- Use `import manimgx as m`.
- Use `m.Typst` and `m.MathTypst` only.
- Never emit `m.MathTex` or `m.Tex` unless LaTeX support is explicitly requested.
- Scene files define exactly one class inheriting `m.Scene` with
  `construct(self) -> None`.
- Generated scenes must not import `os`, `sys`, `subprocess`, `socket`, `requests`,
  `urllib`, `pathlib`, or `shutil`, and must not call `open`, `eval`, `exec`,
  `compile`, or `__import__`.
- All renders go through `batch_worker.py`; do not add one-process-per-clip render
  paths.
- Return compact structured errors, not raw tracebacks.

## Security expectations

- Validate IDs before joining paths.
- Reject `..`, absolute paths, separators, and symlink escapes.
- Execute untrusted scene code only in subprocesses.
- Keep timeouts on renders and post-processing commands.
- Log to stderr only.

## Pull request checklist

- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pytest` passes
- [ ] New tools/resources/prompts are covered by tests
- [ ] `README.md` and `AGENTS.md` are updated when behavior changes
- [ ] No `manim`, LaTeX, or `typst` CLI dependency is introduced

## Reporting bugs

Include:

- Operating system and GPU/Vulkan provider (`vulkaninfo --summary`)
- `uv run manimgx --version`
- The failing tool call and its compact error report
- The relevant scene file, with secrets removed
