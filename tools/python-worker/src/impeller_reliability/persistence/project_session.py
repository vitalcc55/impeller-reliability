from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Literal, Self

from impeller_reliability.persistence.project_database import create_verified_backup, insert_audit, sha256_file, utc_now, validate_project_database
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_lock import ProjectLock
from impeller_reliability.persistence.project_manifest import ProjectManifest


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
    def __init__(self, path: Path, manifest: ProjectManifest, connection: sqlite3.Connection, project_lock: ProjectLock) -> None:
        self.path = path
        self.manifest = manifest
        self._connection = connection
        self._lock = project_lock
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
        return ProjectOverview(
            project_id=str(row["project_id"]),
            path=str(self.path),
            name=str(row["name"]),
            project_number=str(row["project_number"]),
            description=str(row["description"]),
            status=_parse_project_status(str(row["status"])),
            record_revision=int(row["record_revision"]),
            created_at_utc=str(row["created_at_utc"]),
            updated_at_utc=str(row["updated_at_utc"]),
            created_with_application_version=str(row["created_with_application_version"]),
            schema_version=int(self._connection.execute("PRAGMA user_version").fetchone()[0]),
        )

    def update_metadata(
        self,
        *,
        expected_revision: int,
        name: str,
        project_number: str,
        description: str,
        status: str,
    ) -> ProjectOverview:
        current = self.overview()
        if current.record_revision != expected_revision:
            raise ProjectOperationError(
                "revision_conflict",
                "Проект был изменён после открытия формы. Перечитайте данные и повторите изменение.",
                details={"expectedRevision": expected_revision, "actualRevision": current.record_revision},
            )
        now = utc_now()
        new_revision = current.record_revision + 1
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE project_metadata
                SET name = ?, project_number = ?, description = ?, status = ?, record_revision = ?, updated_at_utc = ?
                WHERE project_id = ? AND record_revision = ?
                """,
                (name.strip(), project_number.strip(), description.strip(), status, new_revision, now, current.project_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ProjectOperationError("revision_conflict", "Редакция проекта изменилась во время сохранения.")
            insert_audit(
                self._connection,
                event_type="project.metadata_updated",
                actor_kind="user",
                occurred_at_utc=now,
                payload={"fromRevision": expected_revision, "toRevision": new_revision},
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return self.overview()

    def create_backup(self) -> tuple[Path, str, str]:
        created_at = utc_now()
        schema_version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        backup_path = create_verified_backup(
            self._connection,
            self.path / "project.sqlite",
            self.path / "backups",
            schema_version,
        )
        return (backup_path, sha256_file(backup_path), created_at)

    def validate(self) -> None:
        validate_project_database(self._connection, self.manifest)

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
