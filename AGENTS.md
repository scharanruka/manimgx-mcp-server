# Agent build rules

- Use `uv` for everything. Python 3.14. `uv sync` before running tests.
- Keep Python modules flat under `src/`; do not add a package subdirectory.
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

## Scene contract

- Describe clips as beats, never as seconds. Do not plan or chase a duration.
- Each beat is one `self.play(...)` transition or one short sequence.
- `self.wait()` stays at 1 second or less; `run_time` at 2 seconds or less
  unless a beat explicitly needs longer.
- The clip's duration is whatever the beats produce.

## Current milestone

Milestone 8 in progress — beat-driven planning, one-call `project_build`,
`code.py` bundles, and session-scoped cleanup. See `PLAN.md` for the spec.
