from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Final
from urllib.parse import quote
from uuid import uuid4

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_manifest import ProjectManifest
from impeller_reliability.persistence.project_paths import PathIdentity, inspect_reserved_file
from impeller_reliability.persistence.project_schema import (
    PROJECT_SCHEMA_VERSION,
    create_schema_v1_objects,
    validate_project_evidence,
    validate_published_schema,
)
from impeller_reliability.persistence.timestamps import utc_now
from impeller_reliability.worker.deadline import RequestDeadline

PROJECT_APPLICATION_ID: Final = 0x49525043
MIN_SUPPORTED_PROJECT_SCHEMA_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class ProjectDatabaseIdentity:
    application_id: int
    schema_version: int


@dataclass(frozen=True, slots=True)
class VerifiedBackup:
    path: Path
    identity: PathIdentity


def configure_project_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
    if journal_mode != "wal":
        raise ProjectOperationError("storage_error", "Не удалось включить WAL для project.sqlite.")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA busy_timeout = 5000")


def probe_project_database_identity(
    database_path: Path,
    manifest: ProjectManifest,
    supported_schema_version: int,
) -> ProjectDatabaseIdentity:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(_sqlite_uri(database_path, "mode=ro&immutable=1"), uri=True)
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if application_id != PROJECT_APPLICATION_ID:
            raise ProjectOperationError("corrupt_project", "project.sqlite имеет неверный application_id.")
        if schema_version < MIN_SUPPORTED_PROJECT_SCHEMA_VERSION:
            raise ProjectOperationError("corrupt_project", "Версия schema project.sqlite не относится к опубликованному формату проекта.")
        if schema_version > supported_schema_version:
            raise ProjectOperationError(
                "incompatible_schema",
                "Проект создан более новой версией приложения.",
                details={
                    "projectSchemaVersion": schema_version,
                    "supportedSchemaVersion": supported_schema_version,
                },
            )
        validate_published_schema(connection, schema_version)
        if schema_version == PROJECT_SCHEMA_VERSION:
            validate_project_evidence(connection, manifest.projectId, manifest.createdAtUtc)
        return ProjectDatabaseIdentity(
            application_id=application_id,
            schema_version=schema_version,
        )
    except ProjectOperationError:
        raise
    except (OSError, sqlite3.DatabaseError) as error:
        raise ProjectOperationError("corrupt_project", "project.sqlite не прошёл read-only identity probe.") from error
    finally:
        if connection is not None:
            connection.close()


@dataclass(frozen=True, slots=True)
class ProjectMetadataSeed:
    name: str
    project_number: str
    description: str
    status: str


def _default_metadata_seed() -> ProjectMetadataSeed:
    return ProjectMetadataSeed(name="Новый проект", project_number="", description="", status="draft")


def _migration_0001(
    connection: sqlite3.Connection,
    manifest: ProjectManifest,
    initial_metadata: ProjectMetadataSeed | None,
) -> None:
    create_schema_v1_objects(connection)
    now = manifest.createdAtUtc
    metadata = initial_metadata or _default_metadata_seed()
    connection.execute(
        """
        INSERT INTO project_metadata (
            project_id, name, project_number, description, status, record_revision,
            created_at_utc, updated_at_utc, created_with_application_version
        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            manifest.projectId,
            metadata.name,
            metadata.project_number,
            metadata.description,
            metadata.status,
            now,
            now,
            manifest.createdWithApplicationVersion,
        ),
    )
    insert_audit(
        connection,
        event_type="project.created",
        actor_kind="application",
        occurred_at_utc=now,
        payload={
            "entityType": "project",
            "entityId": manifest.projectId,
            "toRevision": 1,
            "changedFields": ["name", "projectNumber", "description", "status"],
            "after": {
                "name": metadata.name,
                "projectNumber": metadata.project_number,
                "description": metadata.description,
                "status": metadata.status,
            },
            "schemaVersion": PROJECT_SCHEMA_VERSION,
        },
    )


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection, ProjectManifest, ProjectMetadataSeed | None], None]


MIGRATIONS: tuple[Migration, ...] = (Migration(1, "create_project_container", _migration_0001),)


class ProjectMigrator:
    def __init__(self, migrations: Sequence[Migration] = MIGRATIONS) -> None:
        self._migrations = tuple(migrations)
        self.latest_version = max((migration.version for migration in self._migrations), default=0)

    def initialize(
        self,
        connection: sqlite3.Connection,
        manifest: ProjectManifest,
        initial_metadata: ProjectMetadataSeed,
        deadline: RequestDeadline | None = None,
    ) -> None:
        _check_deadline(deadline, "project_initialize")
        connection.execute(f"PRAGMA application_id = {PROJECT_APPLICATION_ID}")
        self._apply_pending(
            connection,
            manifest,
            current_version=0,
            initial_metadata=initial_metadata,
            deadline=deadline,
        )

    def migrate_existing(
        self,
        connection: sqlite3.Connection,
        database_path: Path,
        backups_path: Path,
        manifest: ProjectManifest,
        deadline: RequestDeadline | None = None,
    ) -> Path | None:
        _check_deadline(deadline, "project_schema_read")
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
        backup = create_verified_backup(
            connection,
            database_path,
            backups_path,
            current_version,
            deadline=deadline,
        )
        self._apply_pending(
            connection,
            manifest,
            current_version=current_version,
            initial_metadata=None,
            deadline=deadline,
        )
        return backup.path

    def _apply_pending(
        self,
        connection: sqlite3.Connection,
        manifest: ProjectManifest,
        *,
        current_version: int,
        initial_metadata: ProjectMetadataSeed | None,
        deadline: RequestDeadline | None,
    ) -> None:
        for migration in self._migrations:
            if migration.version <= current_version:
                continue
            try:
                _check_deadline(deadline, f"migration_{migration.version}_begin")
                connection.execute("BEGIN IMMEDIATE")
                migration.apply(connection, manifest, initial_metadata)
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at_utc) VALUES (?, ?, ?)",
                    (migration.version, migration.name, utc_now()),
                )
                connection.execute(f"PRAGMA user_version = {migration.version}")
                _check_deadline(deadline, f"migration_{migration.version}_commit")
                connection.commit()
            except Exception:
                connection.rollback()
                raise


def create_verified_backup(
    source: sqlite3.Connection,
    database_path: Path,
    backups_path: Path,
    schema_version: int,
    *,
    deadline: RequestDeadline | None = None,
) -> VerifiedBackup:
    _check_deadline(deadline, "backup_prepare")
    backups_path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backups_path / f"project-v{schema_version}-{stamp}.sqlite"
    reserved_identity: PathIdentity | None = None
    try:
        descriptor = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
        os.close(descriptor)
        reserved_identity = inspect_reserved_file(backup_path, backup_path.name)
        if reserved_identity is None:
            raise AssertionError("reserved_backup_identity_missing")
        target = sqlite3.connect(backup_path)
        try:
            source.backup(
                target,
                pages=128,
                progress=lambda _status, _remaining, _total: _check_deadline(
                    deadline,
                    "backup_copy",
                ),
                sleep=0.01,
            )
        finally:
            target.close()
        _check_deadline(deadline, "backup_verify")
        check = sqlite3.connect(_sqlite_uri(backup_path, "mode=ro&immutable=1"), uri=True)
        try:
            quick_check = str(check.execute("PRAGMA quick_check").fetchone()[0])
        finally:
            check.close()
        if quick_check != "ok":
            raise ProjectOperationError("storage_error", "Проверка backup project.sqlite завершилась ошибкой.")
        _check_deadline(deadline, "backup_complete")
        completed_identity = inspect_reserved_file(backup_path, backup_path.name)
        if completed_identity != reserved_identity:
            raise ProjectOperationError("storage_error", "Backup был подменён во время создания.")
        return VerifiedBackup(path=backup_path, identity=reserved_identity)
    except Exception:
        if reserved_identity is not None:
            remove_owned_backup(VerifiedBackup(path=backup_path, identity=reserved_identity))
        raise


def remove_owned_backup(backup: VerifiedBackup) -> bool:
    owned_path = _find_owned_backup_path(backup)
    if owned_path is None:
        return False
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar_path = owned_path.with_name(f"{owned_path.name}{suffix}")
        sidecar_identity = inspect_reserved_file(sidecar_path, sidecar_path.name, allow_missing=True)
        if sidecar_identity is not None:
            sidecar_path.unlink()
    owned_path.unlink()
    return True


def _find_owned_backup_path(backup: VerifiedBackup) -> Path | None:
    candidates = [backup.path, *backup.path.parent.glob("*.sqlite")]
    for candidate in candidates:
        try:
            current_identity = inspect_reserved_file(candidate, candidate.name, allow_missing=True)
        except ProjectOperationError:
            continue
        if current_identity == backup.identity:
            return candidate
    return None


def create_project_database(
    database_path: Path,
    *,
    connection_factory: Callable[[Path], sqlite3.Connection] = sqlite3.connect,
) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        connection = connection_factory(database_path)
        connection.row_factory = sqlite3.Row
        configure_project_connection(connection)
        return connection
    except Exception as error:
        if connection is not None:
            connection.close()
        if isinstance(error, ProjectOperationError):
            raise
        if not isinstance(error, (OSError, sqlite3.DatabaseError)):
            raise
        raise ProjectOperationError("corrupt_project", "project.sqlite повреждён или недоступен.") from error


def open_project_database(
    database_path: Path,
    expected_identity: PathIdentity,
    expected_database_identity: ProjectDatabaseIdentity,
    manifest: ProjectManifest,
    *,
    connection_factory: Callable[[str], sqlite3.Connection] | None = None,
) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    connect = connection_factory or _connect_read_write
    try:
        connection = connect(_sqlite_uri(database_path, "mode=rw"))
        _validate_open_connection_identity(
            connection,
            database_path,
            expected_database_identity,
            manifest,
        )
        current_identity = inspect_reserved_file(database_path, database_path.name)
        if current_identity != expected_identity:
            raise ProjectOperationError("corrupt_project", "project.sqlite был подменён перед открытием на запись.")
        connection.row_factory = sqlite3.Row
        configure_project_connection(connection)
        return connection
    except Exception as error:
        if connection is not None:
            connection.close()
        if isinstance(error, ProjectOperationError):
            raise
        if not isinstance(error, (OSError, sqlite3.DatabaseError)):
            raise
        raise ProjectOperationError("corrupt_project", "project.sqlite повреждён или недоступен.") from error


def _validate_open_connection_identity(
    connection: sqlite3.Connection,
    database_path: Path,
    expected_identity: ProjectDatabaseIdentity,
    manifest: ProjectManifest,
) -> None:
    database_rows = connection.execute("PRAGMA database_list").fetchall()
    main_rows = [row for row in database_rows if str(row[1]) == "main"]
    if len(main_rows) != 1:
        raise ProjectOperationError("corrupt_project", "Write connection не содержит единственную main database.")
    opened_path = Path(str(main_rows[0][2]))
    if os.path.normcase(os.path.abspath(opened_path)) != os.path.normcase(os.path.abspath(database_path)):
        raise ProjectOperationError("corrupt_project", "Write connection открыл другой project.sqlite.")
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if application_id != expected_identity.application_id or schema_version != expected_identity.schema_version:
        raise ProjectOperationError("corrupt_project", "SQLite identity изменился между read-only probe и write open.")
    validate_published_schema(connection, schema_version)
    if schema_version == PROJECT_SCHEMA_VERSION:
        validate_project_evidence(connection, manifest.projectId, manifest.createdAtUtc)


def validate_project_database(connection: sqlite3.Connection, manifest: ProjectManifest) -> None:
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.DatabaseError as error:
        raise ProjectOperationError("corrupt_project", "Структура project.sqlite повреждена.") from error
    if quick_check != "ok" or foreign_key_errors:
        raise ProjectOperationError("corrupt_project", "Проверка целостности project.sqlite завершилась ошибкой.")
    if application_id != PROJECT_APPLICATION_ID or schema_version != PROJECT_SCHEMA_VERSION:
        raise ProjectOperationError("corrupt_project", "Версия или application_id project.sqlite не согласованы.")
    validate_published_schema(connection, schema_version)
    validate_project_evidence(connection, manifest.projectId, manifest.createdAtUtc)


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


def sha256_file(path: Path, deadline: RequestDeadline | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            _check_deadline(deadline, "backup_hash")
            digest.update(chunk)
    return digest.hexdigest()


def _check_deadline(deadline: RequestDeadline | None, stage: str) -> None:
    if deadline is not None:
        deadline.check(stage)


def _connect_read_write(database_uri: str) -> sqlite3.Connection:
    return sqlite3.connect(database_uri, uri=True)


def _sqlite_uri(path: Path, query: str) -> str:
    encoded_path = quote(path.as_posix(), safe="/:")
    return f"file:{encoded_path}?{query}"
