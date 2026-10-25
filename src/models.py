from __future__ import annotations

from typing import Any, Literal

from manimgx import ManimColor
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from config import default_config

AspectRatio = Literal["9:16", "16:9"]
MathSyntax = Literal["typst", "latex"]
X264Preset = Literal[
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
]


def _validate_color(value: str) -> str:
    try:
        ManimColor(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid color: {value!r}") from exc
    return value


class SafeArea(BaseModel):
    """Scene-coordinate bounds in which generated content must stay."""

    model_config = ConfigDict(extra="forbid")

    x_min: float = Field(default=-4.0, ge=-20.0, le=20.0)
    x_max: float = Field(default=4.0, ge=-20.0, le=20.0)
    y_min: float = Field(default=-7.0, ge=-20.0, le=20.0)
    y_max: float = Field(default=7.0, ge=-20.0, le=20.0)

    @model_validator(mode="after")
    def _validate_bounds(self) -> SafeArea:
        if self.x_min >= self.x_max:
            raise ValueError("safe_area.x_min must be less than safe_area.x_max")
        if self.y_min >= self.y_max:
            raise ValueError("safe_area.y_min must be less than safe_area.y_max")
        return self


class FontSizes(BaseModel):
    """Font sizes used by generated scenes."""

    model_config = ConfigDict(extra="forbid")

    title: int = Field(default=64, ge=8, le=400)
    body: int = Field(default=32, ge=8, le=400)
    caption: int = Field(default=24, ge=8, le=400)


class ProjectConfig(BaseModel):
    """Validated project configuration stored in SQLite as JSON."""

    model_config = ConfigDict(extra="forbid")

    aspect_ratio: AspectRatio = "9:16"
    final_width: int = Field(default=1080, ge=2, le=7680)
    final_height: int = Field(default=1920, ge=2, le=7680)
    final_fps: int = Field(default=60, ge=1, le=240)
    preview_width: int = Field(default=480, ge=2, le=3840)
    preview_height: int = Field(default=854, ge=2, le=3840)
    preview_fps: int = Field(default=15, ge=1, le=120)
    preview_preset: X264Preset = "ultrafast"
    preview_crf: float = Field(default=30.0, ge=0, le=51)
    final_preset: X264Preset = "medium"
    final_crf: float = Field(default=18.0, ge=0, le=51)
    background_color: str = "BLACK"
    text_color: str = "WHITE"
    accent_color: str = "BLUE"
    math_syntax: MathSyntax = "typst"
    safe_area: SafeArea = Field(default_factory=SafeArea)
    font_sizes: FontSizes = Field(default_factory=FontSizes)

    @field_validator("background_color", "text_color", "accent_color")
    @classmethod
    def _check_color(cls, value: str) -> str:
        return _validate_color(value)

    @model_validator(mode="after")
    def _validate_resolution(self) -> ProjectConfig:
        for label, width, height in (
            ("final", self.final_width, self.final_height),
            ("preview", self.preview_width, self.preview_height),
        ):
            if width % 2 or height % 2:
                raise ValueError(f"{label} width and height must be even for H.264")
            if self.aspect_ratio == "9:16" and width >= height:
                raise ValueError(f"{label} resolution must be portrait for 9:16")
            if self.aspect_ratio == "16:9" and width <= height:
                raise ValueError(f"{label} resolution must be landscape for 16:9")
        return self

    @classmethod
    def default_for(cls, aspect_ratio: AspectRatio) -> ProjectConfig:
        return cls.model_validate(default_config(aspect_ratio))


class ThemeUpdate(BaseModel):
    """Allowed keys for a project theme update."""

    model_config = ConfigDict(extra="forbid")

    background_color: str | None = None
    text_color: str | None = None
    accent_color: str | None = None
    font_sizes: FontSizes | None = None
    safe_area: SafeArea | None = None
    preview_width: int | None = Field(default=None, ge=2, le=3840)
    preview_height: int | None = Field(default=None, ge=2, le=3840)
    preview_fps: int | None = Field(default=None, ge=1, le=120)
    preview_preset: X264Preset | None = None
    preview_crf: float | None = Field(default=None, ge=0, le=51)
    final_width: int | None = Field(default=None, ge=2, le=7680)
    final_height: int | None = Field(default=None, ge=2, le=7680)
    final_fps: int | None = Field(default=None, ge=1, le=240)
    final_preset: X264Preset | None = None
    final_crf: float | None = Field(default=None, ge=0, le=51)

    @field_validator("background_color", "text_color", "accent_color")
    @classmethod
    def _check_color(cls, value: str | None) -> str | None:
        return None if value is None else _validate_color(value)


def apply_theme(config: ProjectConfig, theme: dict[str, Any] | None) -> ProjectConfig:
    """Merge a validated theme update into a project config."""
    if theme is None:
        return config

    update = ThemeUpdate.model_validate(theme)
    data = config.model_dump(mode="json")

    if update.safe_area is not None and "safe_area" in update.model_fields_set:
        data["safe_area"] = {
            **data["safe_area"],
            **update.safe_area.model_dump(exclude_unset=True),
        }
    if update.font_sizes is not None and "font_sizes" in update.model_fields_set:
        data["font_sizes"] = {
            **data["font_sizes"],
            **update.font_sizes.model_dump(exclude_unset=True),
        }

    patch = update.model_dump(exclude_unset=True, exclude_none=True)
    patch.pop("safe_area", None)
    patch.pop("font_sizes", None)
    data.update(patch)
    return ProjectConfig.model_validate(data)


class ProjectPaths(BaseModel):
    """Absolute paths for a project's storage layout."""

    project_dir: str
    db_path: str
    storyboard_path: str
    scenes_dir: str
    renders_dir: str
    preview_dir: str
    final_dir: str
    storyboards_dir: str
    frames_dir: str
    exports_dir: str
    logs_dir: str


class Beat(BaseModel):
    """One animated beat: a single transition or a short sequence."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1)
    objects: list[str] = Field(default_factory=list)
    typst: str | None = None


class ScenePlan(BaseModel):
    """One clip in a storyboard, described by beats rather than seconds."""

    model_config = ConfigDict(extra="forbid")

    scene_id: str
    title: str = Field(min_length=1)
    beats: list[Beat] = Field(default_factory=list, max_length=12)
    key_objects: list[str] = Field(default_factory=list)
    typst_expressions: list[str] = Field(default_factory=list)
    notes: str | None = None
    # Deprecated: the clip's duration is whatever its beats produce.
    target_seconds: float | None = None
    visual: str | None = None
    animation_notes: str | None = None

    @field_validator("scene_id")
    @classmethod
    def _check_scene_id(cls, value: str) -> str:
        from paths import validate_scene_id

        return validate_scene_id(value)

    @model_validator(mode="after")
    def _require_content(self) -> ScenePlan:
        if not self.beats and not (self.visual or self.animation_notes):
            raise ValueError("a clip needs at least one beat")
        return self


class Storyboard(BaseModel):
    """Validated storyboard produced by the host LLM."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    clips: list[ScenePlan] = Field(min_length=1, max_length=100)
    # Deprecated: ignored. Duration is an output, never an input.
    total_seconds: float | None = None

    @model_validator(mode="after")
    def _validate_clips(self) -> Storyboard:
        scene_ids = [clip.scene_id for clip in self.clips]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("scene_id values must be unique within a storyboard")
        return self


class SceneSummary(BaseModel):
    """Compact scene metadata for project listings."""

    project_id: str
    scene_id: str
    title: str
    status: str
    target_seconds: float | None = None
    rendered_seconds: float | None = None
    code_hash: str | None = None
    code_lines: int | None = None
    rejection_reason: str | None = None
    artifacts: dict[str, Any] = Field(default_factory=dict)
    updated_at: str


class SceneDetail(SceneSummary):
    """A scene with its full plan, source code, and code path."""

    plan: ScenePlan
    code: str | None = None
    code_path: str


class LintIssue(BaseModel):
    """One deterministic lint or layout warning."""

    rule: str
    line: int | None = Field(default=None, ge=1)
    message: str
    fix: str
    severity: Literal["error", "warning"] = "error"


class LintReport(BaseModel):
    """Compact scene lint result."""

    ok: bool
    issues: list[LintIssue] = Field(default_factory=list)
    truncated: bool = False
    estimated_duration: float | None = None
    class_name: str | None = None


class LayoutIssue(BaseModel):
    """One layout problem or note reported by ManimGX inspect."""

    number: int | None = None
    message: str
    line: int | None = Field(default=None, ge=1)
    note: bool = False


class InspectReport(BaseModel):
    """Result of running ManimGX inspect without rendering video."""

    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    storyboard_paths: list[str] = Field(default_factory=list)
    issues: list[LayoutIssue] = Field(default_factory=list)
    duration_seconds: float | None = None


class RenderReport(BaseModel):
    """Result of rendering preview or final video."""

    ok: bool
    exit_code: int
    video_path: str | None = None
    duration_seconds: float | None = None
    width: int
    height: int
    fps: int
    storyboard_paths: list[str] = Field(default_factory=list)
    stdout: str
    stderr: str


class BatchSceneResult(BaseModel):
    """One scene's result inside a batch render."""

    scene_id: str
    ok: bool
    duration_seconds: float | None = None
    video_path: str | None = None
    elapsed_seconds: float = 0.0
    error: str | None = None


class BatchReport(BaseModel):
    """Result of one batch worker process rendering many scenes."""

    ok: bool
    mode: Literal["preview", "final"]
    scene_count: int
    total_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    budget_seconds: float
    within_budget: bool
    results: list[BatchSceneResult] = Field(default_factory=list)
    job_path: str = ""
    result_path: str = ""
    stdout: str = ""
    stderr: str = ""


class ExportReport(BaseModel):
    """Result of stitching final clips into one MP4."""

    ok: bool
    export_path: str | None = None
    scene_ids: list[str] = Field(default_factory=list)
    duration_seconds: float | None = None
    size_bytes: int | None = None
    reencoded: bool = False
    error: str | None = None
    stdout: str = ""
    stderr: str = ""


class FrameReport(BaseModel):
    """Result of extracting review frames from a rendered scene."""

    ok: bool
    frame_paths: list[str] = Field(default_factory=list)
    times: list[float] = Field(default_factory=list)
    source_video: str | None = None
    errors: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


class MpvReport(BaseModel):
    """Result of opening a rendered scene in mpv."""

    ok: bool
    pid: int | None = None
    video_path: str | None = None
    error: str | None = None


class ProjectSummary(BaseModel):
    """Project config, paths, storyboard, scenes, and status counts."""

    project_id: str
    name: str
    config: ProjectConfig
    paths: ProjectPaths
    storyboard: Storyboard | None = None
    scenes: list[SceneSummary] = Field(default_factory=list)
    scene_count: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class SceneBuildResult(BaseModel):
    """One scene's outcome inside a project build."""

    scene_id: str
    ok: bool
    changed: bool = False
    seconds: float | None = None
    status: str = ""
    video_path: str | None = None
    code_path: str | None = None
    error: str | None = None
    warnings: list[LintIssue] = Field(default_factory=list)


class BuildResult(BaseModel):
    """A built project: the video, the editable code, and the scene reports."""

    ok: bool
    project_id: str
    mode: Literal["final", "preview"] = "final"
    video_path: str | None = None
    code_path: str | None = None
    storyboard_path: str | None = None
    report_path: str | None = None
    bundle_dir: str | None = None
    code: str | None = None
    duration_seconds: float | None = None
    width: int
    height: int
    fps: int
    scenes: list[SceneBuildResult] = Field(default_factory=list)
    issues: list[LintIssue] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
    approval_required: bool = False


class StartRequest(BaseModel):
    """Parameters the server asks for when a start request omits them."""

    aspect_ratio: Literal["9:16", "16:9"]
    clip_count: int = Field(ge=1, le=12)


class StartResult(BaseModel):
    """Result of starting an animation project."""

    status: Literal["started", "needs_input"]
    brief: str
    project: ProjectSummary | None = None
    clip_count: int | None = None
    missing: list[str] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)
    question: str | None = None
    plan_prompt: str | None = None
    next: str | None = None
