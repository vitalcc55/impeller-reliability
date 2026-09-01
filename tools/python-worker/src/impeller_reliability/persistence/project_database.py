from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import sqlite3
from typing import Final
from urllib.parse import quote

from impeller_reliability.persistence.analyst_dossier import validate_dossier_evidence
from impeller_reliability.persistence.audit import insert_audit
from impeller_reliability.persistence.case_documents import validate_case_document_evidence
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_manifest import ProjectManifest
from impeller_reliability.persistence.project_schema import (
    PROJECT_SCHEMA_VERSION,
    create_schema_v1_objects,
    validate_project_evidence,
    validate_published_schema,
)
from impeller_reliability.persistence.r130sh_sources import validate_r130sh_source_evidence
from impeller_reliability.persistence.reliability_domain import validate_reliability_evidence
from impeller_reliability.persistence.sqlite_deadline import sqlite_deadline_guard
from impeller_reliability.persistence.timestamps import utc_now
from impeller_reliability.worker.deadline import RequestDeadline

PROJECT_APPLICATION_ID: Final = 0x49525043
MIN_SUPPORTED_PROJECT_SCHEMA_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class ProjectDatabaseIdentity:
    application_id: int
    schema_version: int


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
    deadline: RequestDeadline | None = None,
) -> ProjectDatabaseIdentity:
    connection: sqlite3.Connection | None = None
    try:
        wal_path = database_path.with_name(f"{database_path.name}-wal")
        read_only_mode = "mode=ro" if wal_path.is_file() and wal_path.stat().st_size > 0 else "mode=ro&immutable=1"
        connection = sqlite3.connect(_sqlite_uri(database_path, read_only_mode), uri=True)
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
        validate_published_schema(connection, schema_version, deadline)
        validate_project_evidence(
            connection,
            manifest.projectId,
            manifest.createdAtUtc,
            manifest.createdWithApplicationVersion,
            deadline,
        )
        validate_dossier_evidence(connection, manifest.projectId, deadline)
        validate_case_document_evidence(connection, deadline)
        validate_r130sh_source_evidence(connection, deadline)
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
            "schemaVersion": 1,
        },
    )


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection, ProjectManifest, ProjectMetadataSeed | None], None]


MIGRATIONS: tuple[Migration, ...] = (Migration(1, "create_project_database", _migration_0001),)


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
        return backup

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
) -> Path:
    _check_deadline(deadline, "backup_prepare")
    backups_path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backups_path / f"project-v{schema_version}-{stamp}.sqlite"
    backup_created = False
    try:
        descriptor = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
        os.close(descriptor)
        backup_created = True
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
            quick_check = quick_check_with_deadline(check, deadline, stage="backup_verify")
        finally:
            check.close()
        if quick_check != "ok":
            raise ProjectOperationError("storage_error", "Проверка backup project.sqlite завершилась ошибкой.")
        _check_deadline(deadline, "backup_complete")
        return backup_path
    except Exception:
        if backup_created:
            remove_backup(backup_path)
        raise


def remove_backup(backup_path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar_path = backup_path.with_name(f"{backup_path.name}{suffix}")
        if sidecar_path.is_file() and not sidecar_path.is_symlink():
            sidecar_path.unlink()
    if backup_path.is_file() and not backup_path.is_symlink():
        backup_path.unlink()


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
    expected_database_identity: ProjectDatabaseIdentity,
    manifest: ProjectManifest,
    *,
    connection_factory: Callable[[str], sqlite3.Connection] | None = None,
    deadline: RequestDeadline | None = None,
) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    connect = connection_factory or _connect_read_write
    try:
        connection = connect(_sqlite_uri(database_path, "mode=rw"))
        _validate_open_connection_identity(
            connection,
            expected_database_identity,
            manifest,
            deadline,
        )
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
    expected_identity: ProjectDatabaseIdentity,
    manifest: ProjectManifest,
    deadline: RequestDeadline | None,
) -> None:
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if application_id != expected_identity.application_id or schema_version != expected_identity.schema_version:
        raise ProjectOperationError("corrupt_project", "SQLite identity изменился между read-only probe и write open.")
    validate_published_schema(connection, schema_version, deadline)
    validate_project_evidence(
        connection,
        manifest.projectId,
        manifest.createdAtUtc,
        manifest.createdWithApplicationVersion,
        deadline,
    )
    validate_dossier_evidence(connection, manifest.projectId, deadline)
    validate_case_document_evidence(connection, deadline)
    validate_r130sh_source_evidence(connection, deadline)
    validate_reliability_evidence(connection, deadline)


def validate_project_database(
    connection: sqlite3.Connection,
    manifest: ProjectManifest,
    deadline: RequestDeadline | None = None,
) -> None:
    try:
        with sqlite_deadline_guard(connection, deadline, "project_integrity"):
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            has_foreign_key_error = connection.execute("PRAGMA foreign_key_check").fetchone() is not None
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.DatabaseError as error:
        raise ProjectOperationError("corrupt_project", "Структура project.sqlite повреждена.") from error
    if quick_check != "ok" or has_foreign_key_error:
        raise ProjectOperationError("corrupt_project", "Проверка целостности project.sqlite завершилась ошибкой.")
    if application_id != PROJECT_APPLICATION_ID or schema_version != PROJECT_SCHEMA_VERSION:
        raise ProjectOperationError("corrupt_project", "Версия или application_id project.sqlite не согласованы.")
    validate_published_schema(connection, schema_version, deadline)
    validate_project_evidence(
        connection,
        manifest.projectId,
        manifest.createdAtUtc,
        manifest.createdWithApplicationVersion,
        deadline,
    )
    validate_dossier_evidence(connection, manifest.projectId, deadline)
    validate_case_document_evidence(connection, deadline)
    validate_r130sh_source_evidence(connection, deadline)
    validate_reliability_evidence(connection, deadline)


def quick_check_with_deadline(
    connection: sqlite3.Connection,
    deadline: RequestDeadline | None,
    *,
    stage: str = "quick_check",
    progress_steps: int = 1_000,
) -> str:
    with sqlite_deadline_guard(connection, deadline, stage, progress_steps=progress_steps):
        return str(connection.execute("PRAGMA quick_check").fetchone()[0])


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
