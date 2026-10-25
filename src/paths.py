"""Project and scene path resolution with strict identifier validation."""

from __future__ import annotations

import re
import unicodedata
import uuid
from pathlib import Path

from config import projects_home
from models import ProjectPaths


class PathSecurityError(ValueError):
    """Raised when an identifier or path could escape its project directory."""


class ProjectNotFoundError(FileNotFoundError):
    """Raised when a project database does not exist."""


_PROJECT_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?-[0-9a-f]{6}")
_SCENE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def slugify(name: str) -> str:
    """Convert a project name into a short ASCII slug."""
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:48].strip("-")
    return slug or "project"


def new_project_id(name: str) -> str:
    """Return a fresh project id: a slug plus a short random suffix."""
    return f"{slugify(name)}-{uuid.uuid4().hex[:6]}"


def validate_project_id(project_id: str) -> str:
    """Validate a project id without ever joining it to a path first."""
    if not isinstance(project_id, str) or not _PROJECT_ID_RE.fullmatch(project_id):
        raise PathSecurityError(f"invalid project_id: {project_id!r}")
    return project_id


def validate_scene_id(scene_id: str) -> str:
    """Validate a scene id as a single safe path component."""
    if not isinstance(scene_id, str) or not _SCENE_ID_RE.fullmatch(scene_id):
        raise PathSecurityError(f"invalid scene_id: {scene_id!r}")
    return scene_id


def projects_root() -> Path:
    """Return the resolved root directory that holds all projects."""
    return projects_home()


def project_dir(project_id: str) -> Path:
    """Resolve a project directory, rejecting traversal and symlink escapes."""
    validate_project_id(project_id)
    root = projects_root()
    candidate = root / project_id
    if candidate.is_symlink():
        raise PathSecurityError(f"project directory may not be a symlink: {project_id!r}")
    resolved = candidate.resolve(strict=False)
    if resolved.parent != root:
        raise PathSecurityError(f"project path escapes the project home: {project_id!r}")
    return candidate


def project_paths(project_id: str) -> ProjectPaths:
    """Return the absolute storage paths for a project."""
    base = project_dir(project_id)
    renders = base / "renders"
    return ProjectPaths(
        project_dir=str(base),
        db_path=str(base / "project.db"),
        storyboard_path=str(base / "storyboard.json"),
        scenes_dir=str(base / "scenes"),
        renders_dir=str(renders),
        preview_dir=str(renders / "preview"),
        final_dir=str(renders / "final"),
        storyboards_dir=str(renders / "storyboards"),
        frames_dir=str(base / "frames"),
        exports_dir=str(base / "exports"),
        logs_dir=str(base / "logs"),
    )


def create_project_dir(project_id: str) -> ProjectPaths:
    """Create the project directory tree; the root must already exist or be creatable."""
    root = projects_root()
    root.mkdir(parents=True, exist_ok=True)
    base = project_dir(project_id)
    base.mkdir(exist_ok=False)

    paths = project_paths(project_id)
    for directory in (
        paths.scenes_dir,
        paths.renders_dir,
        paths.preview_dir,
        paths.final_dir,
        paths.storyboards_dir,
        paths.frames_dir,
        paths.exports_dir,
        paths.logs_dir,
    ):
        Path(directory).mkdir(parents=True, exist_ok=True)
    return paths


def scene_dir(project_id: str) -> Path:
    """Return the scene directory after verifying it stays inside the project."""
    base = project_dir(project_id)
    directory = base / "scenes"
    if directory.is_symlink():
        raise PathSecurityError("scene directory may not be a symlink")
    if directory.resolve(strict=False).parent != base:
        raise PathSecurityError("scene directory escapes the project directory")
    return directory


def scene_file(project_id: str, scene_id: str) -> Path:
    """Resolve ``scenes/<scene_id>.py``, rejecting traversal and symlinks."""
    validate_scene_id(scene_id)
    directory = scene_dir(project_id)
    path = directory / f"{scene_id}.py"
    if path.is_symlink():
        raise PathSecurityError(f"scene file may not be a symlink: {scene_id!r}")
    if path.resolve(strict=False).parent != directory.resolve(strict=False):
        raise PathSecurityError(f"scene path escapes the project: {scene_id!r}")
    return path
