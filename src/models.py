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


class ScenePlan(BaseModel):
    """One clip in a storyboard."""

    model_config = ConfigDict(extra="forbid")

    scene_id: str
    title: str = Field(min_length=1)
    target_seconds: float = Field(ge=1, le=30)
    visual: str = Field(min_length=1)
    key_objects: list[str]
    animation_notes: str = Field(min_length=1)
    typst_expressions: list[str] = Field(default_factory=list)

    @field_validator("scene_id")
    @classmethod
    def _check_scene_id(cls, value: str) -> str:
        from paths import validate_scene_id

        return validate_scene_id(value)


class Storyboard(BaseModel):
    """Validated storyboard produced by the host LLM."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    clips: list[ScenePlan] = Field(min_length=1, max_length=100)
    total_seconds: float = Field(ge=10, le=90)

    @model_validator(mode="after")
    def _validate_clips(self) -> Storyboard:
        scene_ids = [clip.scene_id for clip in self.clips]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("scene_id values must be unique within a storyboard")
        clip_total = sum(clip.target_seconds for clip in self.clips)
        if abs(clip_total - self.total_seconds) > 0.05:
            raise ValueError(
                f"total_seconds ({self.total_seconds}) must match the sum of "
                f"clip target_seconds ({clip_total})"
            )
        return self


class SceneSummary(BaseModel):
    """Compact scene metadata for project listings."""

    project_id: str
    scene_id: str
    title: str
    status: str
    target_seconds: float | None = None
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
