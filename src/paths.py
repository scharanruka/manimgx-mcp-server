"""Project and scene path resolution with strict identifier validation."""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
import uuid
from pathlib import Path

from config import exports_home, projects_home
from models import ProjectPaths


class PathSecurityError(ValueError):
    """Raised when an identifier or path could escape its project directory."""


class ProjectNotFoundError(FileNotFoundError):
    """Raised when a project database does not exist."""


class SceneNotFoundError(FileNotFoundError):
    """Raised when a scene is not part of the project storyboard."""


_PROJECT_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?-[0-9a-f]{6}")
_SCENE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_EXPORT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,120}\.mp4")


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


def export_file(project_id: str, output_name: str) -> Path:
    """Resolve a safe MP4 export path inside the project's exports directory."""
    name = output_name.strip()
    if not name.lower().endswith(".mp4"):
        name = f"{name}.mp4"
    if not _EXPORT_NAME_RE.fullmatch(name):
        raise PathSecurityError(f"invalid export name: {output_name!r}")
    return Path(project_paths(project_id).exports_dir) / name


def persistent_export_dir(project_id: str) -> Path:
    """Return the durable export directory for a project's finished bundle."""
    validate_project_id(project_id)
    root = exports_home()
    candidate = root / project_id
    resolved = candidate.resolve(strict=False)
    if resolved.parent != root.resolve(strict=False):
        raise PathSecurityError(f"export path escapes the export home: {project_id!r}")
    return candidate


def atomic_write_text(path: Path, text: str) -> None:
    """Write a file atomically, creating its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def resolve_project_path(project_id: str, path_value: str) -> Path:
    """Resolve a path inside a project, rejecting absolute or escaping paths."""
    base = project_dir(project_id).resolve(strict=False)
    raw = Path(path_value)
    candidate = raw if raw.is_absolute() else base / raw
    resolved = candidate.resolve(strict=False)
    if resolved != base and base not in resolved.parents:
        raise PathSecurityError(f"path escapes the project: {path_value!r}")
    return resolved


def _artifact_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        skip = {
            "lint",
            "inspect",
            "preview",
            "final",
            "preview_batch",
            "final_batch",
            "frame_report",
        }
        return [
            item
            for key, nested in value.items()
            if key not in skip
            for item in _artifact_values(nested)
        ]
    if isinstance(value, (list, tuple, set)):
        return [item for nested in value for item in _artifact_values(nested)]
    return []


def delete_artifact_files(project_id: str, artifacts: dict[str, object]) -> list[str]:
    """Delete files referenced by artifact metadata, but only under render roots."""
    base = project_dir(project_id).resolve(strict=False)
    allowed_roots = (base / "renders", base / "frames", base / "exports")
    deleted: list[str] = []
    for value in _artifact_values(artifacts):
        resolved = resolve_project_path(project_id, value)
        if not any(resolved == root or root in resolved.parents for root in allowed_roots):
            continue
        if resolved.is_file():
            resolved.unlink()
            deleted.append(str(resolved))
    return deleted
