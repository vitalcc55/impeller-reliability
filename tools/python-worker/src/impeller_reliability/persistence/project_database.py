from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Final
from uuid import uuid4

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_manifest import ProjectManifest

PROJECT_APPLICATION_ID: Final = 0x49525043
PROJECT_SCHEMA_VERSION: Final = 1


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def configure_project_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
    if journal_mode != "wal":
        raise ProjectOperationError("storage_error", "Не удалось включить WAL для project.sqlite.")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA busy_timeout = 5000")


def _migration_0001(connection: sqlite3.Connection, manifest: ProjectManifest) -> None:
    connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, applied_at_utc TEXT NOT NULL)")
    connection.execute(
        """
        CREATE TABLE project_metadata (
            project_id TEXT PRIMARY KEY,
            name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
            project_number TEXT NOT NULL CHECK (length(project_number) <= 100),
            description TEXT NOT NULL CHECK (length(description) <= 4000),
            status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'completed', 'archived')),
            record_revision INTEGER NOT NULL CHECK (record_revision >= 1),
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            created_with_application_version TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE project_audit_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            occurred_at_utc TEXT NOT NULL,
            actor_kind TEXT NOT NULL CHECK (actor_kind IN ('application', 'user')),
            payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
        )
        """
    )
    connection.execute("CREATE TRIGGER project_audit_events_no_update BEFORE UPDATE ON project_audit_events BEGIN SELECT RAISE(ABORT, 'project_audit_append_only'); END")
    connection.execute("CREATE TRIGGER project_audit_events_no_delete BEFORE DELETE ON project_audit_events BEGIN SELECT RAISE(ABORT, 'project_audit_append_only'); END")
    now = manifest.createdAtUtc
    default_name = "Новый проект"
    connection.execute(
        """
        INSERT INTO project_metadata (
            project_id, name, project_number, description, status, record_revision,
            created_at_utc, updated_at_utc, created_with_application_version
        ) VALUES (?, ?, '', '', 'draft', 1, ?, ?, ?)
        """,
        (manifest.projectId, default_name, now, now, manifest.createdWithApplicationVersion),
    )
    insert_audit(
        connection,
        event_type="project.created",
        actor_kind="application",
        occurred_at_utc=now,
        payload={"projectId": manifest.projectId, "schemaVersion": PROJECT_SCHEMA_VERSION},
    )


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection, ProjectManifest], None]


MIGRATIONS: tuple[Migration, ...] = (Migration(1, "create_project_container", _migration_0001),)


class ProjectMigrator:
    def __init__(self, migrations: Sequence[Migration] = MIGRATIONS) -> None:
        self._migrations = tuple(migrations)
        self.latest_version = max((migration.version for migration in self._migrations), default=0)

    def initialize(self, connection: sqlite3.Connection, manifest: ProjectManifest) -> None:
        connection.execute(f"PRAGMA application_id = {PROJECT_APPLICATION_ID}")
        self._apply_pending(connection, manifest, current_version=0)

    def migrate_existing(self, connection: sqlite3.Connection, database_path: Path, backups_path: Path, manifest: ProjectManifest) -> Path | None:
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        if application_id != PROJECT_APPLICATION_ID:
            raise ProjectOperationError("corrupt_project", "project.sqlite имеет неверный application_id.")
        current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current_version > self.latest_version:
            raise ProjectOperationError(
                "incompatible_schema",
                "Проект создан более новой версией приложения.",
                details={"projectSchemaVersion": current_version, "supportedSchemaVersion": self.latest_version},
            )
        if current_version == self.latest_version:
            return None
        backup_path = create_verified_backup(connection, database_path, backups_path, current_version)
        self._apply_pending(connection, manifest, current_version=current_version)
        return backup_path

    def _apply_pending(self, connection: sqlite3.Connection, manifest: ProjectManifest, *, current_version: int) -> None:
        for migration in self._migrations:
            if migration.version <= current_version:
                continue
            try:
                connection.execute("BEGIN IMMEDIATE")
                migration.apply(connection, manifest)
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at_utc) VALUES (?, ?, ?)",
                    (migration.version, migration.name, utc_now()),
                )
                connection.execute(f"PRAGMA user_version = {migration.version}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise


def create_verified_backup(
    source: sqlite3.Connection,
    database_path: Path,
    backups_path: Path,
    schema_version: int,
) -> Path:
    backups_path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backups_path / f"project-v{schema_version}-{stamp}.sqlite"
    try:
        target = sqlite3.connect(backup_path)
        try:
            source.backup(target)
        finally:
            target.close()
        check = sqlite3.connect(f"file:{backup_path.as_posix()}?mode=ro", uri=True)
        try:
            quick_check = str(check.execute("PRAGMA quick_check").fetchone()[0])
        finally:
            check.close()
        if quick_check != "ok":
            raise ProjectOperationError("storage_error", "Проверка backup project.sqlite завершилась ошибкой.")
        return backup_path
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise


def open_project_database(database_path: Path) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        configure_project_connection(connection)
        return connection
    except (OSError, sqlite3.DatabaseError) as error:
        if connection is not None:
            connection.close()
        raise ProjectOperationError("corrupt_project", "project.sqlite повреждён или недоступен.") from error


def validate_project_database(connection: sqlite3.Connection, manifest: ProjectManifest) -> None:
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        rows = connection.execute("SELECT project_id FROM project_metadata").fetchall()
    except sqlite3.DatabaseError as error:
        raise ProjectOperationError("corrupt_project", "Структура project.sqlite повреждена.") from error
    if quick_check != "ok" or foreign_key_errors:
        raise ProjectOperationError("corrupt_project", "Проверка целостности project.sqlite завершилась ошибкой.")
    if application_id != PROJECT_APPLICATION_ID or schema_version != PROJECT_SCHEMA_VERSION:
        raise ProjectOperationError("corrupt_project", "Версия или application_id project.sqlite не согласованы.")
    if len(rows) != 1 or str(rows[0]["project_id"]) != manifest.projectId:
        raise ProjectOperationError("corrupt_project", "projectId в manifest и project.sqlite не совпадает.")


def insert_audit(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    actor_kind: str,
    occurred_at_utc: str,
    payload: dict[str, object],
) -> None:
    connection.execute(
        "INSERT INTO project_audit_events (event_id, event_type, occurred_at_utc, actor_kind, payload_json) VALUES (?, ?, ?, ?, ?)",
        (
            str(uuid4()),
            event_type,
            occurred_at_utc,
            actor_kind,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        ),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
