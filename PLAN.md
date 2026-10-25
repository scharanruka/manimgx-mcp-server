# Build Spec: `manimgx-mcp-server`

## 0. Project goal

Build an open-source, self-hostable MCP server named **`manimgx-mcp-server`** that lets an MCP client (opencode) generate short technical animation clips by writing ManimGX Python scenes, inspecting them, rendering previews at 480p, reviewing/rejecting, rendering final 1080p, and stitching clips.

Primary engine: **ManimGX** (`import manimgx as m`), not ManimCE. Do **not** install or import `manim`. ManimGX renders with a Rust + wgpu GPU core and is benchmarked at **90.6× faster than ManimCE**; 36.2 seconds of 1080p60 video rendered in **3.08 seconds** across five scenes on an Apple M4 Pro. On Linux it uses **Vulkan**, with CPU fallback through Mesa lavapipe when no GPU is present.

Typesetting: **Typst only**. ManimGX has Typst built in and needs **no LaTeX installation**; `m.Typst` and `m.MathTypst` compile Typst directly. Do not emit `m.MathTex`/`m.Tex` unless the project explicitly requests LaTeX compatibility.

Target rendering budget: **10–20 preview clips in under 60 seconds** after code is generated, and faster if the GPU allows. LLM generation time is separate; use Flash for code so each clip is 1–3 seconds of generation.

---

## 1. Dependencies — install these first

### 1.1 Arch Linux system packages

```bash
# Required
sudo pacman -S --needed uv mpv ffmpeg git base-devel

# Vulkan / GPU
sudo pacman -S --needed vulkan-icd-loader vulkan-headers vulkan-tools mesa
# NVIDIA proprietary users:
# sudo pacman -S --needed nvidia-utils
# CPU-only CI/container fallback:
# sudo pacman -S --needed vulkan-swrast

# Verify GPU
vulkaninfo --summary
```

- `mpv` is for viewing previews on a desktop.
- `ffmpeg` is for post-processing only: extracting review frames and stitching clips. ManimGX already encodes MP4 itself.
- `vulkan-tools` provides `vulkaninfo`.
- Do **not** install `texlive`, `texlive-most`, or the `typst` CLI. ManimGX includes fonts, Typst, and video encoding.

### 1.2 Python

ManimGX requires **Python 3.13 or 3.14**. Use Python 3.14.

```bash
uv python install 3.14
```

### 1.3 Python project dependencies

```toml
# pyproject.toml
[project]
name = "manimgx-mcp-server"
version = "0.1.0"
description = "Blazing-fast ManimGX MCP server for short technical animation clips"
readme = "README.md"
requires-python = ">=3.13,<3.15"
license = { text = "MIT" }
dependencies = [
    "fastmcp>=3.0",
    "manimgx>=0.1.1",
    "pydantic>=2.0",
]

[project.scripts]
manimgx-mcp = "server:main"

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "ruff>=0.8",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

No SQLite dependency is needed; use Python’s built-in `sqlite3`.

---

## 2. Scaffold with `uv`

Run these commands exactly:

```bash
uv init --package manimgx-mcp-server
cd manimgx-mcp-server
uv python pin 3.14
uv add fastmcp manimgx pydantic
uv add --dev pytest pytest-asyncio ruff
uv sync
uv run manimgx --version
```

The `uv run manimgx --version` check must succeed before writing server code. If it fails, fix the Python version or ManimGX installation first.

---

## 3. Repository layout

```text
manimgx-mcp-server/
├── pyproject.toml
├── README.md
├── LICENSE                         # MIT
├── CONTRIBUTING.md
├── AGENTS.md
├── src/                            # flat modules (no package subdirectory)
│   ├── server.py                   # FastMCP instance, tool/resource/prompt registration
│   ├── config.py                   # env, paths, defaults
│   ├── models.py                   # Pydantic models
│   ├── db.py                       # SQLite schema and queries
│   ├── paths.py                    # project/scene path resolution, sanitization
│   ├── lint.py                     # AST allowlist and scene checks
│   ├── inspection.py               # manimgx inspect wrapper and parser
│   ├── render.py                   # preview/final CLI wrappers
│   ├── batch_worker.py             # single-process batch renderer
│   ├── post.py                     # ffmpeg stitch, frame extraction, mpv
│   └── prompts.py                  # MCP prompt templates
├── examples/
│   ├── demo_project.json
│   └── scenes/
│       └── scene_01.py
├── scripts/
│   └── check_connection.py         # stdio connection smoke client
├── tests/
│   ├── test_lint.py
│   ├── test_db.py
│   ├── test_inspect_parser.py
│   ├── test_paths.py
│   └── test_integration_slow.py
└── .github/workflows/
    ├── ci.yml
    └── release.yml
```

---

## 4. Persistence and project model

### 4.1 Project home

Use an environment variable:

```text
MANIM_MCP_HOME=~/.manimgx-mcp/projects
```

Each project is a directory:

```text
<MANIM_MCP_HOME>/<project_id>/
├── project.db
├── storyboard.json
├── scenes/
│   └── scene_01.py
├── renders/
│   ├── preview/
│   ├── final/
│   └── storyboards/
├── frames/
├── exports/
└── logs/
```

`project_id` is a sanitized slug plus a short UUID suffix, for example `derivative-intro-a1b2c3`. Never allow `..`, absolute paths, or path separators in IDs.

### 4.2 SQLite schema

`project.db` is the source of truth. Do **not** rely on FastMCP `ctx.set_state` for cross-call state: FastMCP request state is request-scoped and does not survive from one tool call to the next. SQLite survives server restarts and works with stateless MCP connections.

```sql
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    storyboard_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE scenes (
    project_id TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    title TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    code TEXT,
    code_hash TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    -- planned | code_written | lint_passed | inspected | preview_rendered
    -- | approved | rejected | final_rendered
    rejection_reason TEXT,
    artifacts_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, scene_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    scene_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL
);
```

### 4.3 Default project config

Store this as JSON in `projects.config_json`:

```json
{
  "aspect_ratio": "9:16",
  "final_width": 1080,
  "final_height": 1920,
  "final_fps": 60,
  "preview_width": 480,
  "preview_height": 854,
  "preview_fps": 15,
  "preview_preset": "ultrafast",
  "preview_crf": 30,
  "final_preset": "medium",
  "final_crf": 18,
  "background_color": "BLACK",
  "text_color": "WHITE",
  "accent_color": "BLUE",
  "math_syntax": "typst",
  "safe_area": {"x_min": -4, "x_max": 4, "y_min": -7.0, "y_max": 7.0}
}
```

For landscape projects, use `1920×1080` final and `854×480` preview, `16:9`.

---

## 5. MCP tool surface

Keep tool descriptions short. Every tool schema is injected into every LLM turn, so avoid 30 granular tools. Expose about **15 tools**, plus resources and prompts.

### 5.1 Project tools

#### `project_create`

```python
def project_create(
    name: str,
    aspect_ratio: Literal["9:16", "16:9"] = "9:16",
    theme: dict | None = None,
) -> ProjectSummary
```

Creates the project directory and `project.db`; inserts default config; returns `project_id`, paths, and config.

#### `project_get`

```python
def project_get(project_id: str) -> ProjectSummary
```

Returns config, storyboard metadata, scene list, and status counts.

#### `project_update_theme`

```python
def project_update_theme(project_id: str, theme: dict) -> ProjectSummary
```

Only allows known keys: colors, font sizes, background color, safe area, preview/final settings. Validate values.

### 5.2 Storyboard and scene storage tools

#### `storyboard_save`

```python
def storyboard_save(project_id: str, storyboard: Storyboard) -> Storyboard
```

The host LLM generates the storyboard using `storyboard_plan_prompt`; this tool validates and persists it. Storyboard schema:

```python
class ScenePlan(BaseModel):
    scene_id: str
    title: str
    target_seconds: float
    visual: str
    key_objects: list[str]
    animation_notes: str
    typst_expressions: list[str] = []

class Storyboard(BaseModel):
    project_id: str
    clips: list[ScenePlan]
    total_seconds: float
```

Enforce `1 <= target_seconds <= 30`; total can be 10–90 seconds depending on project.

#### `scene_write`

```python
def scene_write(project_id: str, scene_id: str, code: str) -> SceneSummary
```

Writes `scenes/<scene_id>.py`, computes `code_hash`, sets status to `code_written`, and clears stale artifacts if the code changed. This is the only way scene code enters the system.

#### `scene_list`

```python
def scene_list(project_id: str) -> list[SceneSummary]
```

#### `scene_get`

```python
def scene_get(project_id: str, scene_id: str) -> SceneDetail
```

### 5.3 Deterministic quality tools

#### `scene_lint`

```python
def scene_lint(project_id: str, scene_id: str) -> LintReport
```

Pure Python, no LLM. Uses `ast` to enforce:

- File imports `manimgx as m`.
- No `import manim`, `os`, `sys`, `subprocess`, `socket`, `requests`, `urllib`, `pathlib`, `shutil`.
- No `open`, `eval`, `exec`, `compile`, `__import__`, `globals`, `locals`.
- Exactly one class inheriting `m.Scene` (or `m.ThreeDScene`).
- Class has `construct(self) -> None`.
- File length ≤ 200 lines.
- No infinite `while True` without break.
- Duration estimate ≤ 45 seconds.
- Uses `m.Typst` or `m.MathTypst`; rejects `m.MathTex`/`m.Tex` unless project `math_syntax == "latex"`.
- Warn if coordinates fall outside safe area.

Return structured issues:

```json
{
  "ok": false,
  "issues": [
    {"rule": "forbidden_import", "line": 3, "message": "do not import os", "fix": "remove import os"}
  ]
}
```

Do not return raw Python tracebacks to the LLM. Parse and compact.

#### `scene_inspect`

```python
def scene_inspect(
    project_id: str,
    scene_id: str,
    times: list[float | Literal["end"]] | None = None,
) -> InspectReport
```

Wraps:

```bash
manimgx inspect scenes/<scene_id>.py <ClassName> -o renders/storyboards/<scene_id>.png
# with times:
manimgx inspect scenes/<scene_id>.py <ClassName> -t 1.5,end -o renders/storyboards/<scene_id>.png
```

ManimGX `inspect` runs the scene without video, prints the timeline and layout checks, and exits `1` for layout problems such as clipped content, overlapping text, or small labels. Use this **before** rendering previews. Return:

```python
class InspectReport(BaseModel):
    ok: bool
    exit_code: int
    stdout: str          # truncated to last 4 KB
    stderr: str          # truncated to last 4 KB
    storyboard_paths: list[str]
    issues: list[LayoutIssue]
```

If parsing fails, return the raw stdout as fallback rather than crashing.

### 5.4 Render tools

#### `scene_render_preview`

```python
def scene_render_preview(project_id: str, scene_id: str) -> RenderReport
```

Renders at **480×854, 15 fps**, using `--preset ultrafast --crf 30`. Return video path, duration, storyboard paths, and the last 2 KB of CLI output.

#### `scene_render_final`

```python
def scene_render_final(project_id: str, scene_id: str, force: bool = False) -> RenderReport
```

Allowed only when scene status is `approved`, unless `force=True`. Renders at **1080×1920, 60 fps**, `--preset medium --crf 18`.

#### `project_render_batch`

```python
def project_render_batch(
    project_id: str,
    scene_ids: list[str] | None = None,
    mode: Literal["preview", "final"] = "preview",
) -> BatchReport
```

**Critical performance rule:** do not spawn one `manimgx` process per clip. Use `batch_worker.py` in a single subprocess. The worker imports ManimGX once, imports each scene module, and calls `Scene().render(video=..., preset=..., crf=...)` sequentially. ManimGX’s Python API exposes `Scene.render()` and writes the MP4 directly.

Batch job JSON:

```json
{
  "project_dir": "/path/to/project",
  "width": 480,
  "height": 854,
  "fps": 15,
  "preset": "ultrafast",
  "crf": 30,
  "jobs": [
    {"scene_id": "scene_01", "file": "scenes/scene_01.py", "class": "Scene01", "video": "renders/preview/scene_01.mp4"},
    {"scene_id": "scene_02", "file": "scenes/scene_02.py", "class": "Scene02", "video": "renders/preview/scene_02.mp4"}
  ]
}
```

Worker sets `m.config.pixel_width`, `m.config.pixel_height`, and `m.config.frame_rate` before instantiating each scene. It writes a result JSON with per-scene success, duration, video path, and error.

### 5.5 Review tools

#### `scene_review`

```python
def scene_review(
    project_id: str,
    scene_id: str,
    decision: Literal["approve", "reject"],
    reason: str | None = None,
) -> SceneSummary
```

- `approve` requires status `preview_rendered` or `inspected`; sets `approved`.
- `reject` requires a reason; sets `rejected` and stores `rejection_reason` for the next edit prompt.

This is the bounded state machine. Do not allow free-form status strings.

### 5.6 Post-processing tools

#### `project_stitch`

```python
def project_stitch(
    project_id: str,
    output_name: str = "final.mp4",
    scene_ids: list[str] | None = None,
) -> ExportReport
```

Concatenate approved final renders in storyboard order using `ffmpeg -f concat -safe 0 -i list.txt -c copy`. If codecs differ, re-encode. Write to `exports/<output_name>`.

#### `scene_extract_frames`

```python
def scene_extract_frames(
    project_id: str,
    scene_id: str,
    times: list[float],
) -> FrameReport
```

Use `ffmpeg -ss <t> -i <video> -frames:v 1 <png>`. Return PNG paths for human or vision review.

#### `scene_open_mpv`

```python
def scene_open_mpv(
    project_id: str,
    scene_id: str,
    mode: Literal["preview", "final"] = "preview",
) -> MpvReport
```

Non-blocking `mpv --loop-file=inf <path>`. Return PID. Desktop-only; if `mpv` is missing, return a clear error.

#### `project_export_subtitles` (optional)

```python
def project_export_subtitles(project_id: str) -> SubtitleReport
```

ManimGX `render` writes an `.srt` automatically when the scene uses `say` or captions. Since voiceover/subtitles are a later phase, expose this as optional and do not implement TTS in v1.

---

## 6. MCP resources

Resources are cheaper than tools for reading context on demand. Implement:

| Resource URI | Returns |
|---|---|
| `manimgx://projects/{project_id}/config` | Project config JSON |
| `manimgx://projects/{project_id}/storyboard` | Storyboard JSON |
| `manimgx://projects/{project_id}/scenes/{scene_id}/code` | Scene Python code |
| `manimgx://projects/{project_id}/scenes/{scene_id}/report` | Last lint/inspect/render report |

Use `@mcp.resource("manimgx://...")`. Keep resource responses small and JSON-serializable.

---

## 7. MCP prompts and model routing

The MCP server does **not** call DeepSeek in v1. opencode is the LLM. The server provides prompts that tell opencode which model to use.

### `storyboard_plan_prompt`

- Model hint: **DeepSeek Pro**
- Inputs: `project_id`, `brief`, `clip_count`, `seconds_per_clip`, `style`
- Output schema: `Storyboard` JSON only, no prose.
- Instructions:
  - Produce 10–20 clips unless asked otherwise.
  - Each clip 10–30 seconds.
  - Use `scene_id` slugs.
  - Use Typst math expressions, not LaTeX.
  - Keep visual descriptions short.
  - Do not write Python code in this step.

### `scene_generate_prompt`

- Model hint: **DeepSeek Flash**
- Inputs: `project_id`, `scene_id`
- Reads storyboard plan and project config via resources.
- Output: one Python file only.
- Contract:

```python
import manimgx as m

class Scene01(m.Scene):
    def construct(self) -> None:
        ...
```

Rules:

- Use `m.Typst` and `m.MathTypst` exclusively.
- Do not set resolution or FPS in code; the CLI/worker sets them.
- Do not import anything except `manimgx as m` and `math`.
- Stay inside the safe area from project config.
- Keep the file under 120 lines.
- Use `self.play(...)` and `self.wait(...)` only for time.
- No `os`, `sys`, `subprocess`, `open`, network, or file I/O.

Typst examples to include in the prompt:

```python
formula = m.MathTypst("sum_(k=1)^n k = (n(n+1))/2", font_size=72)
text = m.Typst("*Bold*, _italic_, and math: $sum_(k=1)^n k$")
```

These are valid ManimGX Typst forms.

### `scene_edit_prompt`

- Model hint: **DeepSeek Flash**
- Inputs: `project_id`, `scene_id`, `rejection_reason`
- Reads:
  - current code, or an **AST skeleton** if code > 80 lines
  - last lint issues
  - last inspect issues
  - rejection reason
- Output: full replacement Python file only.
- Do not include the full conversation history.

This is the main token-saving mechanism: the model sees a compact error report and a skeleton, not a long chat transcript.

---

## 8. Rendering pipeline and performance budget

### 8.1 Preview

```bash
manimgx render scenes/<scene_id>.py <ClassName> \
  -o renders/preview/<scene_id>.mp4 \
  -r 480x854 --fps 15 --preset ultrafast --crf 30
```

`-r` and `--fps` are documented CLI options. ManimGX’s default is 1920×1080 at 60 fps, so always pass preview flags explicitly.

### 8.2 Final

```bash
manimgx render scenes/<scene_id>.py <ClassName> \
  -o renders/final/<scene_id>.mp4 \
  -r 1080x1920 --fps 60 --preset medium --crf 18
```

### 8.3 Batch rendering

For 10–20 clips, use one `batch_worker.py` subprocess, not 20 CLI calls. The ManimGX benchmark includes process launch in its 3.08-second total for five scenes, but per-clip launches still add overhead; batching amortizes imports and GPU initialization.

Performance budget on a machine with a working Vulkan GPU:

| Stage | Target |
|---|---|
| `scene_lint` | < 50 ms |
| `scene_inspect` | < 500 ms per clip |
| 20 preview clips, 480p15, batched | < 30 s |
| 20 final clips, 1080p60, batched | < 60 s |
| Stitch 20 clips | < 5 s |

If the machine falls back to CPU lavapipe, expect 2–4× slower; still log the measured time and pass/fail against the budget.

Add a benchmark script `tests/bench_batch.py` that creates 20 tiny scenes and measures batch render time. Mark it `slow` and `gpu`.

---

## 9. Iteration loop and token strategy

The scene state machine is bounded:

```text
planned
  → scene_write
  → scene_lint
      ├─ fail → scene_edit_prompt (lint issues) → scene_write
      └─ pass → scene_inspect
                  ├─ fail → scene_edit_prompt (layout issues) → scene_write
                  └─ pass → scene_render_preview
                              → human/vision review
                                  ├─ reject → scene_review(reject, reason)
                                  │            → scene_edit_prompt(reason)
                                  └─ approve → scene_review(approve)
                                               → scene_render_final
```

Token rules:

1. **Never send the full chat history** to `scene_generate_prompt` or `scene_edit_prompt`.
2. Send the scene plan, project config, current code or AST skeleton, and structured errors only.
3. Keep tool descriptions under 20 words each.
4. Use resources to fetch config and code on demand.
5. Use Pro only for `storyboard_plan_prompt`.
6. Use Flash for all code generation and edits.
7. Keep the system prompt and tool schemas byte-stable across turns to preserve prefix caching.
8. Return compact JSON from deterministic tools, not raw tracebacks.
9. Cap `inspect` stdout to the last 4 KB.
10. Reject scenes with at most 10 structured issues per report.

---

## 10. Security

Generated Python code is untrusted. Enforce all of the following:

1. **AST allowlist** in `scene_lint` before writing or executing.
2. Run all renders in a **subprocess**, never `exec` in the MCP server process.
3. Use a per-render timeout, e.g. 120 seconds.
4. Set `cwd` to the project directory.
5. Do not expose network access to the worker by default.
6. Optional on Arch: wrap the worker in `bwrap` (bubblewrap) with a read-only root and a writable project directory.
7. Sanitize `project_id`, `scene_id`, and output paths. Reject `..`, absolute paths, and symlinks.

---

## 11. Build milestones and acceptance criteria

### Milestone 1 — Scaffold and ManimGX smoke test

- `uv init --package`, pin Python 3.14, add dependencies.
- `uv run manimgx --version` succeeds.
- Create `examples/scenes/scene_01.py` with a Typst formula and render it manually.
- **Acceptance:** `uv run manimgx render examples/scenes/scene_01.py Scene01 -r 480x854 --fps 15 --preset ultrafast` produces an MP4.

### Milestone 2 — SQLite and project tools

- Implement `db.py`, `paths.py`, `project_create`, `project_get`, `project_update_theme`.
- **Acceptance:** create two projects; configs are isolated; path traversal is rejected.

### Milestone 3 — Scene storage and lint

- Implement `storyboard_save`, `scene_write`, `scene_list`, `scene_get`, `scene_lint`.
- **Acceptance:** lint rejects `import os`, `import manim`, `open()`, `eval()`, and `m.MathTex`; accepts a valid Typst scene.

### Milestone 4 — Inspect and preview render

- Implement `scene_inspect` and `scene_render_preview`.
- **Acceptance:** inspect returns exit code and storyboard path; preview is 480×854 at 15 fps; a layout-broken scene returns `ok=false` with issues.

### Milestone 5 — Batch worker and final render

- Implement `batch_worker.py`, `project_render_batch`, and `scene_render_final`.
- **Acceptance:** 10 preview clips render in one worker process in under 60 seconds on GPU; final render is 1080×1920 at 60 fps.

### Milestone 6 — Review loop and post-processing

- Implement `scene_review`, `project_stitch`, `scene_extract_frames`, `scene_open_mpv`.
- **Acceptance:** reject stores reason; next edit prompt includes reason; stitch produces a playable MP4; frame extraction produces PNGs at requested times.

### Milestone 7 — MCP prompts, resources, and open-source release

- Implement `@mcp.resource` and `@mcp.prompt`.
- Add README, MIT license, CONTRIBUTING, AGENTS.md, CI.
- **Acceptance:** MCP Inspector lists all tools/resources/prompts; `uv run pytest` passes; `uv build` and `uvx --from dist/*.whl manimgx-mcp` starts.

---

## 12. Open-source packaging

README must include:

- Arch Linux install one-liner.
- `uvx manimgx-mcp-server` run command.
- MCP client config example.
- Tool/resource/prompt table.
- Performance numbers and GPU requirements.
- “No LaTeX required; Typst built in.”
- “ManimGX is not ManimCE; do not install `manim`.”

MCP client config:

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

For local development:

```json
{
  "mcpServers": {
    "manimgx": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/manimgx-mcp-server", "manimgx-mcp"]
    }
  }
}
```

Use **stderr-only logging**; stdout is reserved for the MCP protocol.

---

## 13. `AGENTS.md` — paste this into the repo

```markdown
# Agent build rules

- Use `uv` for everything. Python 3.14. `uv sync` before running tests.
- Never install or import `manim`. Use `import manimgx as m`.
- Never install LaTeX or the `typst` CLI. ManimGX includes Typst.
- Use `m.Typst` and `m.MathTypst`. Do not emit `m.MathTex` or `m.Tex`.
- Scene files must be one class inheriting `m.Scene` with `construct(self) -> None`.
- No `os`, `sys`, `subprocess`, `open`, `eval`, `exec`, network, or file I/O in generated scenes.
- All renders go through `batch_worker.py`; never spawn one `manimgx` process per clip.
- Preview: `-r 480x854 --fps 15 --preset ultrafast --crf 30`.
- Final: `-r 1080x1920 --fps 60 --preset medium --crf 18`.
- Keep tool descriptions under 20 words. Log to stderr only.
- Run `uv run pytest` before committing.
- Do not add voiceover/TTS in v1. It is a later phase.
```

---

## 14. Final definition of done

The project is done when:

1. `uv run pytest` passes.
2. `uv run manimgx-mcp` starts and MCP Inspector sees the tool/resource/prompt surface.
3. A demo project with **10 clips** goes from storyboard to preview batch in **under 60 seconds of rendering** on a Vulkan GPU.
4. A broken scene is rejected with structured issues, edited, re-inspected, and approved without sending the full chat history.
5. Final 1080p clips stitch into a playable MP4.
6. README, MIT license, CI, and `uvx` smoke test are in place.