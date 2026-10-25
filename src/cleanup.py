"""Session-scoped cleanup for ephemeral projects and finished exports."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta

import db
from config import SESSION_ID_ENV, export_ttl_days, exports_home
from paths import list_project_ids, project_dir


def session_id() -> str:
    """Return this server process's session key."""
    raw = os.environ.get(SESSION_ID_ENV)
    return raw.strip() if raw and raw.strip() else f"pid-{os.getpid()}"


def _age_hours(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        seen = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    return (datetime.now(UTC) - seen).total_seconds() / 3600.0


def cleanup_session(key: str | None = None) -> list[str]:
    """Delete every ephemeral project owned by a finished session."""
    owner = key or session_id()
    removed: list[str] = []
    for project_id in list_project_ids():
        try:
            meta = db.get_project_meta(project_id)
        except (FileNotFoundError, ValueError):
            continue
        if not meta["ephemeral"] or meta["session_id"] != owner:
            continue
        shutil.rmtree(project_dir(project_id), ignore_errors=True)
        removed.append(project_id)
    return removed


def sweep_orphans(max_age_hours: float = 24.0) -> list[str]:
    """Delete ephemeral projects abandoned by a crashed session."""
    removed: list[str] = []
    for project_id in list_project_ids():
        try:
            meta = db.get_project_meta(project_id)
        except (FileNotFoundError, ValueError):
            continue
        if not meta["ephemeral"]:
            continue
        age = _age_hours(meta["last_seen"])
        if age is None or age < max_age_hours:
            continue
        shutil.rmtree(project_dir(project_id), ignore_errors=True)
        removed.append(project_id)
    return removed


def sweep_exports(days: int | None = None) -> list[str]:
    """Delete export bundles older than the configured TTL."""
    ttl = export_ttl_days() if days is None else max(0, days)
    root = exports_home()
    if not root.is_dir():
        return []
    cutoff = datetime.now(UTC) - timedelta(days=ttl)
    removed: list[str] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            resolved = entry.resolve(strict=False)
        except OSError:
            continue
        if resolved.parent != root.resolve(strict=False):
            continue
        modified = datetime.fromtimestamp(entry.stat().st_mtime, tz=UTC)
        if modified >= cutoff:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed.append(entry.name)
    return removed
