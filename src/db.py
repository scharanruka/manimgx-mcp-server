"""SQLite persistence for projects, scenes, and events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import PROJECT_STATUSES
from models import (
    LintReport,
    ProjectConfig,
    ProjectSummary,
    SceneDetail,
    ScenePlan,
    SceneSummary,
    Storyboard,
)
from paths import (
    ProjectNotFoundError,
    SceneNotFoundError,
    project_dir,
    project_paths,
    scene_file,
    validate_scene_id,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    storyboard_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenes (
    project_id TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    title TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    code TEXT,
    code_hash TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    rejection_reason TEXT,
    artifacts_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, scene_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    scene_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scenes_project ON scenes(project_id);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id, id);
"""


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat()


def db_path_for(project_id: str) -> Path:
    """Return the project's SQLite database path."""
    return Path(project_paths(project_id).db_path)


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(db_path, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def project_connection(project_id: str) -> Iterator[sqlite3.Connection]:
    """Open a project database, refusing unknown projects."""
    path = db_path_for(project_id)
    if not path.is_file():
        raise ProjectNotFoundError(f"project not found: {project_id}")
    with _connect(path) as connection:
        yield connection


def create_project_record(project_id: str, name: str, config: ProjectConfig) -> None:
    """Insert a project row into an already-created project directory."""
    path = db_path_for(project_id)
    now = utc_now()
    with _connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """
            INSERT INTO projects (project_id, name, config_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                project_id,
                name,
                json.dumps(config.model_dump(mode="json")),
                now,
                now,
            ),
        )
        _insert_event(
            connection,
            project_id,
            None,
            "project_created",
            {"name": name, "config": config.model_dump(mode="json")},
            now,
        )


def _insert_event(
    connection: sqlite3.Connection,
    project_id: str,
    scene_id: str | None,
    event_type: str,
    payload: dict[str, Any] | None,
    created_at: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO events (project_id, scene_id, event_type, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            project_id,
            scene_id,
            event_type,
            None if payload is None else json.dumps(payload),
            created_at or utc_now(),
        ),
    )


def record_event(
    project_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    scene_id: str | None = None,
) -> None:
    """Append an event to a project's audit log."""
    with project_connection(project_id) as connection:
        _insert_event(connection, project_id, scene_id, event_type, payload)


def update_project_config(project_id: str, config: ProjectConfig) -> None:
    """Persist a validated config and record the change."""
    now = utc_now()
    with project_connection(project_id) as connection:
        cursor = connection.execute(
            "UPDATE projects SET config_json = ?, updated_at = ? WHERE project_id = ?",
            (json.dumps(config.model_dump(mode="json")), now, project_id),
        )
        if cursor.rowcount != 1:
            raise ProjectNotFoundError(f"project not found: {project_id}")
        _insert_event(
            connection,
            project_id,
            None,
            "theme_updated",
            {"config": config.model_dump(mode="json")},
            now,
        )


def get_project_row(project_id: str) -> dict[str, Any]:
    """Load a project row or raise a clear not-found error."""
    with project_connection(project_id) as connection:
        row = connection.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
    if row is None:
        raise ProjectNotFoundError(f"project not found: {project_id}")
    return dict(row)


def _parse_artifacts(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _parse_plan(raw: str, *, strict: bool) -> ScenePlan | None:
    try:
        return ScenePlan.model_validate_json(raw)
    except ValueError as exc:
        if strict:
            raise ValueError("scene plan is corrupt") from exc
        return None


def _scene_summary(row: sqlite3.Row) -> SceneSummary:
    plan = _parse_plan(row["plan_json"], strict=False)
    code = row["code"]
    return SceneSummary(
        project_id=row["project_id"],
        scene_id=row["scene_id"],
        title=row["title"],
        status=row["status"],
        target_seconds=plan.target_seconds if plan else None,
        code_hash=row["code_hash"],
        code_lines=None if code is None else len(code.splitlines()),
        rejection_reason=row["rejection_reason"],
        artifacts=_parse_artifacts(row["artifacts_json"]),
        updated_at=row["updated_at"],
    )


def _scene_row(connection: sqlite3.Connection, project_id: str, scene_id: str) -> sqlite3.Row:
    validate_scene_id(scene_id)
    row = connection.execute(
        "SELECT * FROM scenes WHERE project_id = ? AND scene_id = ?",
        (project_id, scene_id),
    ).fetchone()
    if row is None:
        raise SceneNotFoundError(f"scene not found: {scene_id}")
    return row


def get_scene_row(project_id: str, scene_id: str) -> dict[str, Any]:
    """Load one scene row or raise a clear not-found error."""
    with project_connection(project_id) as connection:
        return dict(_scene_row(connection, project_id, scene_id))


def get_scene_summary(project_id: str, scene_id: str) -> SceneSummary:
    """Return compact metadata for one scene."""
    with project_connection(project_id) as connection:
        return _scene_summary(_scene_row(connection, project_id, scene_id))


def list_scene_summaries(project_id: str) -> list[SceneSummary]:
    """List scenes in storyboard insertion order."""
    with project_connection(project_id) as connection:
        rows = connection.execute(
            "SELECT * FROM scenes WHERE project_id = ? ORDER BY rowid", (project_id,)
        ).fetchall()
    return [_scene_summary(row) for row in rows]


def get_scene_detail(project_id: str, scene_id: str) -> SceneDetail:
    """Return full scene metadata, plan, and code."""
    with project_connection(project_id) as connection:
        row = _scene_row(connection, project_id, scene_id)

    summary = _scene_summary(row)
    plan = _parse_plan(row["plan_json"], strict=True)
    if plan is None:
        raise ValueError(f"scene plan is corrupt: {scene_id}")
    return SceneDetail(
        **summary.model_dump(),
        plan=plan,
        code=row["code"],
        code_path=str(scene_file(project_id, scene_id)),
    )


def save_storyboard(project_id: str, storyboard: Storyboard) -> Storyboard:
    """Persist a storyboard and upsert its planned scenes."""
    now = utc_now()
    with project_connection(project_id) as connection:
        cursor = connection.execute(
            "UPDATE projects SET storyboard_json = ?, updated_at = ? WHERE project_id = ?",
            (storyboard.model_dump_json(), now, project_id),
        )
        if cursor.rowcount != 1:
            raise ProjectNotFoundError(f"project not found: {project_id}")

        for clip in storyboard.clips:
            connection.execute(
                """
                INSERT INTO scenes (
                    project_id, scene_id, title, plan_json, code, code_hash,
                    status, rejection_reason, artifacts_json, updated_at
                )
                VALUES (?, ?, ?, ?, NULL, NULL, 'planned', NULL, '{}', ?)
                ON CONFLICT(project_id, scene_id) DO UPDATE SET
                    title = excluded.title,
                    plan_json = excluded.plan_json,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    clip.scene_id,
                    clip.title,
                    clip.model_dump_json(),
                    now,
                ),
            )

        _insert_event(
            connection,
            project_id,
            None,
            "storyboard_saved",
            {"clip_count": len(storyboard.clips), "total_seconds": storyboard.total_seconds},
            now,
        )
    return storyboard


def update_scene_code(project_id: str, scene_id: str, code: str, code_hash: str) -> dict[str, Any]:
    """Store scene code, reset its status, and report stale artifacts."""
    now = utc_now()
    with project_connection(project_id) as connection:
        row = _scene_row(connection, project_id, scene_id)
        changed = row["code_hash"] != code_hash
        stale_artifacts = _parse_artifacts(row["artifacts_json"]) if changed else {}
        connection.execute(
            """
            UPDATE scenes SET
                code = ?, code_hash = ?, status = 'code_written',
                rejection_reason = NULL, artifacts_json = ?, updated_at = ?
            WHERE project_id = ? AND scene_id = ?
            """,
            (
                code,
                code_hash,
                json.dumps({} if changed else _parse_artifacts(row["artifacts_json"])),
                now,
                project_id,
                scene_id,
            ),
        )
        _insert_event(
            connection,
            project_id,
            scene_id,
            "scene_written",
            {"code_hash": code_hash, "changed": changed, "lines": len(code.splitlines())},
            now,
        )
    return {"changed": changed, "stale_artifacts": stale_artifacts}


def record_lint_result(project_id: str, scene_id: str, report: LintReport) -> SceneSummary:
    """Store a lint report and advance or hold the scene status."""
    now = utc_now()
    with project_connection(project_id) as connection:
        row = _scene_row(connection, project_id, scene_id)
        artifacts = _parse_artifacts(row["artifacts_json"])
        artifacts["lint"] = report.model_dump(mode="json")
        status = "lint_passed" if report.ok else "code_written"
        connection.execute(
            """
            UPDATE scenes SET status = ?, artifacts_json = ?, updated_at = ?
            WHERE project_id = ? AND scene_id = ?
            """,
            (status, json.dumps(artifacts), now, project_id, scene_id),
        )
        _insert_event(
            connection,
            project_id,
            scene_id,
            "lint_passed" if report.ok else "lint_failed",
            {
                "ok": report.ok,
                "issue_count": len(report.issues),
                "truncated": report.truncated,
            },
            now,
        )
        row = _scene_row(connection, project_id, scene_id)
    return _scene_summary(row)


def get_project_config(project_id: str) -> ProjectConfig:
    """Load a project's validated config."""
    return ProjectConfig.model_validate_json(get_project_row(project_id)["config_json"])


def get_project_summary(project_id: str) -> ProjectSummary:
    """Return a project with config, storyboard, scenes, and status counts."""
    row = get_project_row(project_id)
    with project_connection(project_id) as connection:
        scene_rows = connection.execute(
            "SELECT * FROM scenes WHERE project_id = ? ORDER BY rowid", (project_id,)
        ).fetchall()

    config = ProjectConfig.model_validate_json(row["config_json"])
    try:
        storyboard = (
            None
            if row["storyboard_json"] is None
            else Storyboard.model_validate_json(row["storyboard_json"])
        )
    except ValueError as exc:
        raise ValueError(f"corrupt storyboard for project {project_id}") from exc

    scenes = [_scene_summary(scene) for scene in scene_rows]
    counts = {status: 0 for status in PROJECT_STATUSES}
    for scene in scenes:
        counts[scene.status] = counts.get(scene.status, 0) + 1

    return ProjectSummary(
        project_id=row["project_id"],
        name=row["name"],
        config=config,
        paths=project_paths(project_id),
        storyboard=storyboard,
        scenes=scenes,
        scene_count=len(scenes),
        status_counts=counts,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def fetch_events(project_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return recent events, newest first."""
    with project_connection(project_id) as connection:
        rows = connection.execute(
            """
            SELECT id, project_id, scene_id, event_type, payload_json, created_at
            FROM events WHERE project_id = ? ORDER BY id DESC LIMIT ?
            """,
            (project_id, limit),
        ).fetchall()
    events = []
    for row in rows:
        event = dict(row)
        payload_json = event.pop("payload_json")
        event["payload"] = None if payload_json is None else json.loads(payload_json)
        events.append(event)
    return events


def project_exists(project_id: str) -> bool:
    """Return whether a project database exists."""
    try:
        return db_path_for(project_id).is_file()
    except ValueError:
        return False


def project_directory(project_id: str) -> Path:
    """Return the project directory (validated)."""
    return project_dir(project_id)
