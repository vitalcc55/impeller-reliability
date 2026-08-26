from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Literal, Self

from impeller_reliability.persistence.project_database import (
    create_verified_backup,
    insert_audit,
    remove_owned_backup,
    sha256_file,
    validate_project_database,
)
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_lock import ProjectLock
from impeller_reliability.persistence.project_manifest import ProjectManifest
from impeller_reliability.persistence.project_paths import PathIdentity, inspect_reserved_directory
from impeller_reliability.persistence.project_values import (
    require_application_version,
    require_canonical_project_id,
    require_project_metadata_value,
)
from impeller_reliability.persistence.timestamps import require_canonical_utc_timestamp, utc_now
from impeller_reliability.worker.deadline import RequestDeadline


@dataclass(frozen=True, slots=True)
class ProjectOverview:
    project_id: str
    path: str
    name: str
    project_number: str
    description: str
    status: Literal["draft", "active", "completed", "archived"]
    record_revision: int
    created_at_utc: str
    updated_at_utc: str
    created_with_application_version: str
    schema_version: int


class ProjectSession:
    def __init__(
        self,
        path: Path,
        manifest: ProjectManifest,
        connection: sqlite3.Connection,
        project_lock: ProjectLock,
        backups_identity: PathIdentity,
    ) -> None:
        self.path = path
        self.manifest = manifest
        self._connection = connection
        self._lock = project_lock
        self._backups_identity = backups_identity
        self._closed = False

    def overview(self) -> ProjectOverview:
        row = self._connection.execute(
            """
            SELECT project_id, name, project_number, description, status, record_revision,
                   created_at_utc, updated_at_utc, created_with_application_version
            FROM project_metadata
            """
        ).fetchone()
        if row is None:
            raise ProjectOperationError("corrupt_project", "Метаданные проекта отсутствуют.")
        try:
            return ProjectOverview(
                project_id=require_canonical_project_id(_require_text(row["project_id"])),
                path=str(self.path),
                name=require_project_metadata_value("name", row["name"]),
                project_number=require_project_metadata_value("projectNumber", row["project_number"]),
                description=require_project_metadata_value("description", row["description"]),
                status=_parse_project_status(require_project_metadata_value("status", row["status"])),
                record_revision=_require_revision(row["record_revision"]),
                created_at_utc=require_canonical_utc_timestamp(_require_text(row["created_at_utc"])),
                updated_at_utc=require_canonical_utc_timestamp(_require_text(row["updated_at_utc"])),
                created_with_application_version=require_application_version(_require_text(row["created_with_application_version"])),
                schema_version=int(self._connection.execute("PRAGMA user_version").fetchone()[0]),
            )
        except (TypeError, ValueError) as error:
            raise ProjectOperationError("corrupt_project", "Метаданные проекта повреждены.") from error

    def update_metadata(
        self,
        *,
        expected_revision: int,
        name: str,
        project_number: str,
        description: str,
        status: str,
        deadline: RequestDeadline | None = None,
    ) -> ProjectOverview:
        _check_deadline(deadline, "metadata_read")
        current = self.overview()
        if current.record_revision != expected_revision:
            raise ProjectOperationError(
                "revision_conflict",
                "Проект был изменён после открытия формы. Перечитайте данные и повторите изменение.",
                details={"expectedRevision": expected_revision, "actualRevision": current.record_revision},
            )
        normalized = {
            "name": name.strip(),
            "projectNumber": project_number.strip(),
            "description": description.strip(),
            "status": status,
        }
        before = {
            "name": current.name,
            "projectNumber": current.project_number,
            "description": current.description,
            "status": current.status,
        }
        changed_fields = [field for field in before if before[field] != normalized[field]]
        if not changed_fields:
            return current
        now = max(current.updated_at_utc, utc_now())
        new_revision = current.record_revision + 1
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE project_metadata
                SET name = ?, project_number = ?, description = ?, status = ?, record_revision = ?, updated_at_utc = ?
                WHERE project_id = ? AND record_revision = ?
                """,
                (
                    normalized["name"],
                    normalized["projectNumber"],
                    normalized["description"],
                    normalized["status"],
                    new_revision,
                    now,
                    current.project_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ProjectOperationError("revision_conflict", "Редакция проекта изменилась во время сохранения.")
            insert_audit(
                self._connection,
                event_type="project.metadata_updated",
                actor_kind="user",
                occurred_at_utc=now,
                payload={
                    "entityType": "project",
                    "entityId": current.project_id,
                    "fromRevision": expected_revision,
                    "toRevision": new_revision,
                    "changedFields": changed_fields,
                    "changes": {field: {"before": before[field], "after": normalized[field]} for field in changed_fields},
                },
            )
            _check_deadline(deadline, "metadata_commit")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return self.overview()

    def create_backup(
        self,
        *,
        deadline: RequestDeadline | None = None,
    ) -> tuple[Path, str, str]:
        _check_deadline(deadline, "backup_start")
        current_backups_identity = inspect_reserved_directory(self.path / "backups", "backups/")
        if current_backups_identity != self._backups_identity:
            raise ProjectOperationError("corrupt_project", "Каталог backups/ был подменён после открытия проекта.")
        created_at = utc_now()
        schema_version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        backup = create_verified_backup(
            self._connection,
            self.path / "project.sqlite",
            self.path / "backups",
            schema_version,
            deadline=deadline,
        )
        try:
            digest = sha256_file(backup.path, deadline)
            _check_deadline(deadline, "backup_finalize")
            return (backup.path, digest, created_at)
        except Exception:
            remove_owned_backup(backup)
            raise

    def validate(self, deadline: RequestDeadline | None = None) -> None:
        validate_project_database(self._connection, self.manifest, deadline)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._connection.close()
        finally:
            self._lock.release()
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _exc_type: object, _exc_value: object, _traceback: object) -> None:
        self.close()


def _parse_project_status(value: str) -> Literal["draft", "active", "completed", "archived"]:
    if value == "draft":
        return "draft"
    if value == "active":
        return "active"
    if value == "completed":
        return "completed"
    if value == "archived":
        return "archived"
    raise ProjectOperationError("corrupt_project", "Статус проекта в project.sqlite не поддерживается.")


def _require_text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("project_metadata_not_text")
    return value


def _require_revision(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("project_revision_invalid")
    return value


def _check_deadline(deadline: RequestDeadline | None, stage: str) -> None:
    if deadline is not None:
        deadline.check(stage)
