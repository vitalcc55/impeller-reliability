from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Literal, Self

from impeller_reliability.integration.r130run.m9a import M9aPackageFacts
from impeller_reliability.integration.r130run.models import RunPackageValidationReport
from impeller_reliability.persistence.analyst_dossier import (
    AnalystDossierRepository,
    CustomerProfile,
    Specimen,
    WheelModel,
)
from impeller_reliability.persistence.audit import audit_now, insert_audit
from impeller_reliability.persistence.case_documents import CaseDocument, CaseDocumentRepository
from impeller_reliability.persistence.project_database import (
    create_verified_backup,
    remove_backup,
    sha256_file,
    validate_project_database,
)
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_lock import ProjectLock
from impeller_reliability.persistence.project_manifest import ProjectManifest
from impeller_reliability.persistence.project_paths import inspect_reserved_directory
from impeller_reliability.persistence.project_values import (
    require_application_version,
    require_canonical_project_id,
    require_project_metadata_value,
)
from impeller_reliability.persistence.r130sh_sources import (
    ImportedRunDetail,
    ImportedRunSummary,
    R130shSourceRepository,
    SourceIntegrityStatus,
    SpecimenBinding,
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
        deadline: RequestDeadline | None = None,
    ) -> None:
        self.path = path
        self.manifest = manifest
        self._connection = connection
        self._lock = project_lock
        self._closed = False
        self._dossier = AnalystDossierRepository(connection, manifest.projectId)
        self._case_documents = CaseDocumentRepository(connection, path)
        self._case_documents.recover_managed_files(deadline)
        self._r130sh_sources = R130shSourceRepository(connection, path)
        self._r130sh_sources.recover_managed_files(deadline)

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
        now = audit_now(self._connection)
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
        inspect_reserved_directory(self.path / "backups", "backups/")
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
            digest = sha256_file(backup, deadline)
            _check_deadline(deadline, "backup_finalize")
            return (backup, digest, created_at)
        except Exception:
            remove_backup(backup)
            raise

    def get_customer(self, deadline: RequestDeadline | None = None) -> CustomerProfile | None:
        return self._dossier.get_customer(deadline)

    def upsert_customer(
        self,
        *,
        expected_revision: int | None,
        values: dict[str, object],
        deadline: RequestDeadline | None,
    ) -> CustomerProfile:
        return self._dossier.upsert_customer(
            expected_revision=expected_revision,
            full_name=str(values["fullName"]),
            legal_address=str(values["legalAddress"]),
            actual_address=str(values["actualAddress"]),
            notes=str(values["notes"]),
            deadline=deadline,
        )

    def create_wheel(self, values: dict[str, object], deadline: RequestDeadline | None) -> WheelModel:
        return self._dossier.create_wheel(values, deadline)

    def list_wheels(self, include_archived: bool, deadline: RequestDeadline | None = None) -> tuple[WheelModel, ...]:
        return self._dossier.list_wheels(include_archived, deadline)

    def get_wheel(self, wheel_id: str, deadline: RequestDeadline | None = None) -> WheelModel:
        return self._dossier.get_wheel(wheel_id, deadline)

    def update_wheel(self, wheel_id: str, expected_revision: int, values: dict[str, object], deadline: RequestDeadline | None) -> WheelModel:
        return self._dossier.update_wheel(wheel_id, expected_revision, values, deadline)

    def set_wheel_archived(self, wheel_id: str, expected_revision: int, archived: bool, deadline: RequestDeadline | None) -> WheelModel:
        return self._dossier.set_wheel_archived(wheel_id, expected_revision, archived, deadline)

    def create_specimen(self, values: dict[str, object], deadline: RequestDeadline | None) -> Specimen:
        return self._dossier.create_specimen(values, deadline)

    def list_specimens(self, include_archived: bool, deadline: RequestDeadline | None = None) -> tuple[Specimen, ...]:
        return self._dossier.list_specimens(include_archived, deadline)

    def get_specimen(self, specimen_id: str, deadline: RequestDeadline | None = None) -> Specimen:
        return self._dossier.get_specimen(specimen_id, deadline)

    def update_specimen(self, specimen_id: str, expected_revision: int, values: dict[str, object], deadline: RequestDeadline | None) -> Specimen:
        return self._dossier.update_specimen(specimen_id, expected_revision, values, deadline)

    def set_specimen_archived(self, specimen_id: str, expected_revision: int, archived: bool, deadline: RequestDeadline | None) -> Specimen:
        return self._dossier.set_specimen_archived(specimen_id, expected_revision, archived, deadline)

    def create_case_document(
        self,
        document_id: str,
        values: dict[str, object],
        wheel_model_ids: tuple[str, ...],
        specimen_ids: tuple[str, ...],
        deadline: RequestDeadline | None,
    ) -> CaseDocument:
        return self._case_documents.create(
            document_id=document_id,
            values=values,
            wheel_model_ids=wheel_model_ids,
            specimen_ids=specimen_ids,
            deadline=deadline,
        )

    def create_case_document_with_file(
        self,
        document_id: str,
        values: dict[str, object],
        wheel_model_ids: tuple[str, ...],
        specimen_ids: tuple[str, ...],
        source_path: Path,
        deadline: RequestDeadline | None,
    ) -> CaseDocument:
        return self._case_documents.create_with_file(
            document_id=document_id,
            values=values,
            wheel_model_ids=wheel_model_ids,
            specimen_ids=specimen_ids,
            source_path=source_path,
            deadline=deadline,
        )

    def list_case_documents(
        self,
        include_archived: bool,
        document_kind: str | None,
        deadline: RequestDeadline | None = None,
    ) -> tuple[CaseDocument, ...]:
        return self._case_documents.list(
            include_archived=include_archived,
            document_kind=document_kind,
            deadline=deadline,
        )

    def get_case_document(
        self,
        document_id: str,
        deadline: RequestDeadline | None = None,
    ) -> CaseDocument:
        return self._case_documents.get(document_id, deadline)

    def update_case_document(
        self,
        document_id: str,
        expected_revision: int,
        values: dict[str, object],
        wheel_model_ids: tuple[str, ...],
        specimen_ids: tuple[str, ...],
        deadline: RequestDeadline | None,
    ) -> CaseDocument:
        return self._case_documents.update(
            document_id=document_id,
            expected_revision=expected_revision,
            values=values,
            wheel_model_ids=wheel_model_ids,
            specimen_ids=specimen_ids,
            deadline=deadline,
        )

    def attach_case_document_file(
        self,
        document_id: str,
        expected_revision: int,
        source_path: Path,
        deadline: RequestDeadline | None,
    ) -> CaseDocument:
        return self._case_documents.attach_file(
            document_id=document_id,
            expected_revision=expected_revision,
            source_path=source_path,
            deadline=deadline,
        )

    def verify_case_document_file(
        self,
        document_id: str,
        deadline: RequestDeadline | None,
    ) -> CaseDocument:
        return self._case_documents.verify_file(document_id, deadline)

    def set_case_document_archived(
        self,
        document_id: str,
        expected_revision: int,
        archived: bool,
        deadline: RequestDeadline | None,
    ) -> CaseDocument:
        return self._case_documents.set_archived(
            document_id=document_id,
            expected_revision=expected_revision,
            archived=archived,
            deadline=deadline,
        )

    def resolve_case_document_file(
        self,
        document_id: str,
        deadline: RequestDeadline | None,
    ) -> Path:
        return self._case_documents.resolve_file(document_id, deadline)

    def register_imported_run(
        self,
        *,
        local_import_id: str,
        staged_path: Path,
        facts: M9aPackageFacts,
        report: RunPackageValidationReport,
        deadline: RequestDeadline | None,
    ) -> ImportedRunSummary:
        return self._r130sh_sources.register(
            local_import_id=local_import_id,
            staged_path=staged_path,
            facts=facts,
            report=report,
            deadline=deadline,
        )

    def list_imported_runs(self, deadline: RequestDeadline | None = None) -> tuple[ImportedRunSummary, ...]:
        return self._r130sh_sources.list(deadline)

    def get_imported_run(self, local_import_id: str, deadline: RequestDeadline | None = None) -> ImportedRunDetail:
        return self._r130sh_sources.get(local_import_id, deadline=deadline)

    def verify_imported_run_source(self, local_import_id: str, deadline: RequestDeadline | None = None) -> SourceIntegrityStatus:
        return self._r130sh_sources.verify_source(local_import_id, deadline=deadline)

    def get_imported_run_binding(
        self,
        source_specimen_id: str,
        deadline: RequestDeadline | None = None,
    ) -> SpecimenBinding:
        return self._r130sh_sources.get_binding(source_specimen_id, deadline)

    def bind_imported_run_specimen(
        self,
        *,
        source_specimen_id: str,
        local_specimen_id: str | None,
        expected_revision: int,
        actor: str,
        reason: str,
        deadline: RequestDeadline | None,
    ) -> SpecimenBinding:
        return self._r130sh_sources.bind_specimen(
            source_specimen_id=source_specimen_id,
            local_specimen_id=local_specimen_id,
            expected_revision=expected_revision,
            actor=actor,
            reason=reason,
            deadline=deadline,
        )

    def record_imported_run_resolution(
        self,
        *,
        resolution_id: str,
        local_import_id: str,
        source_payload_path: str,
        source_field: str,
        target_entity_type: str,
        target_entity_id: str,
        target_field: str,
        decision: str,
        actor: str,
        reason: str,
        expected_target_revision: int | None,
        deadline: RequestDeadline | None,
    ) -> ImportedRunDetail:
        return self._r130sh_sources.record_enrichment_resolution(
            resolution_id=resolution_id,
            local_import_id=local_import_id,
            source_payload_path=source_payload_path,
            source_field=source_field,
            target_entity_type=target_entity_type,
            target_entity_id=target_entity_id,
            target_field=target_field,
            decision=decision,
            actor=actor,
            reason=reason,
            expected_target_revision=expected_target_revision,
            deadline=deadline,
        )

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
