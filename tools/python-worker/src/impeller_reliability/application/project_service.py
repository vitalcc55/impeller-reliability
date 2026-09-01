from __future__ import annotations

import os
from pathlib import Path
import shutil
from uuid import uuid4

from impeller_reliability.integration.r130run.m9a import M9aPackageFacts
from impeller_reliability.integration.r130run.models import RunPackageValidationReport
from impeller_reliability.persistence.analyst_dossier import CustomerProfile, Specimen, WheelModel
from impeller_reliability.persistence.case_documents import CaseDocument
from impeller_reliability.persistence.project_database import (
    ProjectMetadataSeed,
    ProjectMigrator,
    create_project_database,
    open_project_database,
    probe_project_database_identity,
    validate_project_database,
)
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_lock import ProjectLock, current_lock_owner
from impeller_reliability.persistence.project_manifest import PROJECT_DATABASE_FILE, ProjectManifest, read_manifest, write_manifest
from impeller_reliability.persistence.project_paths import (
    inspect_reserved_directory,
    validate_project_container,
)
from impeller_reliability.persistence.project_session import ProjectOverview, ProjectSession
from impeller_reliability.persistence.r130sh_sources import (
    ImportedRunDetail,
    ImportedRunSummary,
    SourceIntegrityStatus,
    SpecimenBinding,
)
from impeller_reliability.persistence.reliability_domain import ReliabilityDataset, TestExecution
from impeller_reliability.persistence.timestamps import utc_now
from impeller_reliability.worker.deadline import RequestDeadline


class ProjectService:
    def __init__(self) -> None:
        self._session: ProjectSession | None = None
        self._migrator = ProjectMigrator()

    @property
    def has_active_session(self) -> bool:
        return self._session is not None

    @property
    def active_project_path(self) -> Path:
        return self._require_session().path

    def create(
        self,
        *,
        path: str,
        application_instance_id: str,
        application_version: str,
        name: str,
        project_number: str,
        description: str,
        status: str,
        deadline: RequestDeadline | None = None,
    ) -> ProjectOverview:
        _check_deadline(deadline, "project_create_start")
        self._require_no_session()
        final_path = self._validate_container_path(path)
        if final_path.exists():
            raise ProjectOperationError("storage_error", "Выбранный проект уже существует.")
        if not final_path.parent.is_dir():
            raise ProjectOperationError("storage_error", "Родительский каталог проекта не существует.")
        project_id = str(uuid4())
        created_at = utc_now()
        manifest = ProjectManifest(
            projectId=project_id,
            createdAtUtc=created_at,
            createdWithApplicationVersion=application_version,
        )
        staging = final_path.with_name(f"{final_path.name}.creating-{uuid4()}")
        final_created = False
        try:
            _check_deadline(deadline, "project_create_staging")
            (staging / "assets" / "documents").mkdir(parents=True)
            (staging / "backups").mkdir()
            write_manifest(staging / "project-manifest.json", manifest)
            connection = create_project_database(staging / PROJECT_DATABASE_FILE)
            try:
                self._migrator.initialize(
                    connection,
                    manifest,
                    ProjectMetadataSeed(
                        name=name.strip(),
                        project_number=project_number.strip(),
                        description=description.strip(),
                        status=status,
                    ),
                    deadline=deadline,
                )
                validate_project_database(connection, manifest, deadline)
            finally:
                connection.close()
            _check_deadline(deadline, "project_create_publish")
            staging.rename(final_path)
            final_created = True
            return self.open(
                path=str(final_path),
                application_instance_id=application_instance_id,
                deadline=deadline,
            )
        except Exception as error:
            self.close()
            if staging.exists():
                shutil.rmtree(staging)
            if final_created and final_path.exists():
                shutil.rmtree(final_path)
            if isinstance(error, ProjectOperationError):
                raise
            raise ProjectOperationError("storage_error", "Не удалось атомарно создать проект.") from error

    def open(
        self,
        *,
        path: str,
        application_instance_id: str,
        deadline: RequestDeadline | None = None,
    ) -> ProjectOverview:
        _check_deadline(deadline, "project_open_start")
        self._require_no_session()
        project_path = self._validate_container_path(path)
        inspect_reserved_directory(project_path, ".irproj")
        manifest = read_manifest(project_path / "project-manifest.json", deadline)
        validate_project_container(project_path, manifest)
        database_identity = probe_project_database_identity(
            project_path / manifest.databaseFile,
            manifest,
            self._migrator.latest_version,
            deadline,
        )
        _check_deadline(deadline, "project_identity_probe")
        acquired_at = utc_now()
        project_lock = ProjectLock.acquire(
            project_path / ".project.lock",
            current_lock_owner(manifest.projectId, application_instance_id, acquired_at),
        )
        connection = None
        try:
            _check_deadline(deadline, "project_open_database")
            connection = open_project_database(
                project_path / manifest.databaseFile,
                database_identity,
                manifest,
                deadline=deadline,
            )
            self._migrator.migrate_existing(
                connection,
                project_path / manifest.databaseFile,
                project_path / "backups",
                manifest,
                deadline=deadline,
            )
            validate_project_database(connection, manifest, deadline)
            _check_deadline(deadline, "project_open_session")
            session = ProjectSession(
                project_path,
                manifest,
                connection,
                project_lock,
                deadline,
            )
            _check_deadline(deadline, "project_open_recovery")
            overview = session.overview()
            self._session = session
            return overview
        except Exception as error:
            if connection is not None:
                connection.close()
            project_lock.release()
            if isinstance(error, ProjectOperationError):
                raise
            raise ProjectOperationError("storage_error", "Не удалось открыть или мигрировать проект.") from error

    def get_overview(self, *, deadline: RequestDeadline | None = None) -> ProjectOverview:
        _check_deadline(deadline, "project_overview")
        return self._require_session().overview()

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
        return self._require_session().update_metadata(
            expected_revision=expected_revision,
            name=name,
            project_number=project_number,
            description=description,
            status=status,
            deadline=deadline,
        )

    def create_backup(
        self,
        *,
        deadline: RequestDeadline | None = None,
    ) -> tuple[Path, str, str]:
        return self._require_session().create_backup(deadline=deadline)

    def get_customer(self, deadline: RequestDeadline | None = None) -> CustomerProfile | None:
        return self._require_session().get_customer(deadline)

    def upsert_customer(self, *, expected_revision: int | None, values: dict[str, object], deadline: RequestDeadline | None) -> CustomerProfile:
        return self._require_session().upsert_customer(expected_revision=expected_revision, values=values, deadline=deadline)

    def create_wheel(self, values: dict[str, object], deadline: RequestDeadline | None) -> WheelModel:
        return self._require_session().create_wheel(values, deadline)

    def list_wheels(self, include_archived: bool, deadline: RequestDeadline | None = None) -> tuple[WheelModel, ...]:
        return self._require_session().list_wheels(include_archived, deadline)

    def get_wheel(self, wheel_id: str, deadline: RequestDeadline | None = None) -> WheelModel:
        return self._require_session().get_wheel(wheel_id, deadline)

    def update_wheel(self, wheel_id: str, expected_revision: int, values: dict[str, object], deadline: RequestDeadline | None) -> WheelModel:
        return self._require_session().update_wheel(wheel_id, expected_revision, values, deadline)

    def set_wheel_archived(self, wheel_id: str, expected_revision: int, archived: bool, deadline: RequestDeadline | None) -> WheelModel:
        return self._require_session().set_wheel_archived(wheel_id, expected_revision, archived, deadline)

    def create_specimen(self, values: dict[str, object], deadline: RequestDeadline | None) -> Specimen:
        return self._require_session().create_specimen(values, deadline)

    def list_specimens(self, include_archived: bool, deadline: RequestDeadline | None = None) -> tuple[Specimen, ...]:
        return self._require_session().list_specimens(include_archived, deadline)

    def get_specimen(self, specimen_id: str, deadline: RequestDeadline | None = None) -> Specimen:
        return self._require_session().get_specimen(specimen_id, deadline)

    def update_specimen(self, specimen_id: str, expected_revision: int, values: dict[str, object], deadline: RequestDeadline | None) -> Specimen:
        return self._require_session().update_specimen(specimen_id, expected_revision, values, deadline)

    def set_specimen_archived(self, specimen_id: str, expected_revision: int, archived: bool, deadline: RequestDeadline | None) -> Specimen:
        return self._require_session().set_specimen_archived(specimen_id, expected_revision, archived, deadline)

    def create_case_document(self, document_id: str, values: dict[str, object], wheel_model_ids: tuple[str, ...], specimen_ids: tuple[str, ...], deadline: RequestDeadline | None) -> CaseDocument:
        return self._require_session().create_case_document(document_id, values, wheel_model_ids, specimen_ids, deadline)

    def create_case_document_with_file(
        self, document_id: str, values: dict[str, object], wheel_model_ids: tuple[str, ...], specimen_ids: tuple[str, ...], source_path: Path, deadline: RequestDeadline | None
    ) -> CaseDocument:
        return self._require_session().create_case_document_with_file(document_id, values, wheel_model_ids, specimen_ids, source_path, deadline)

    def list_case_documents(self, include_archived: bool, document_kind: str | None, deadline: RequestDeadline | None = None) -> tuple[CaseDocument, ...]:
        return self._require_session().list_case_documents(include_archived, document_kind, deadline)

    def get_case_document(self, document_id: str, deadline: RequestDeadline | None = None) -> CaseDocument:
        return self._require_session().get_case_document(document_id, deadline)

    def update_case_document(
        self, document_id: str, expected_revision: int, values: dict[str, object], wheel_model_ids: tuple[str, ...], specimen_ids: tuple[str, ...], deadline: RequestDeadline | None
    ) -> CaseDocument:
        return self._require_session().update_case_document(document_id, expected_revision, values, wheel_model_ids, specimen_ids, deadline)

    def attach_case_document_file(self, document_id: str, expected_revision: int, source_path: Path, deadline: RequestDeadline | None) -> CaseDocument:
        return self._require_session().attach_case_document_file(document_id, expected_revision, source_path, deadline)

    def verify_case_document_file(self, document_id: str, deadline: RequestDeadline | None) -> CaseDocument:
        return self._require_session().verify_case_document_file(document_id, deadline)

    def set_case_document_archived(self, document_id: str, expected_revision: int, archived: bool, deadline: RequestDeadline | None) -> CaseDocument:
        return self._require_session().set_case_document_archived(document_id, expected_revision, archived, deadline)

    def resolve_case_document_file(self, document_id: str, deadline: RequestDeadline | None) -> Path:
        return self._require_session().resolve_case_document_file(document_id, deadline)

    def register_imported_run(
        self,
        *,
        local_import_id: str,
        staged_path: Path,
        facts: M9aPackageFacts,
        report: RunPackageValidationReport,
        deadline: RequestDeadline | None,
    ) -> ImportedRunSummary:
        return self._require_session().register_imported_run(
            local_import_id=local_import_id,
            staged_path=staged_path,
            facts=facts,
            report=report,
            deadline=deadline,
        )

    def list_imported_runs(self, deadline: RequestDeadline | None = None) -> tuple[ImportedRunSummary, ...]:
        return self._require_session().list_imported_runs(deadline)

    def get_imported_run(self, local_import_id: str, deadline: RequestDeadline | None = None) -> ImportedRunDetail:
        return self._require_session().get_imported_run(local_import_id, deadline)

    def verify_imported_run_source(self, local_import_id: str, deadline: RequestDeadline | None = None) -> SourceIntegrityStatus:
        return self._require_session().verify_imported_run_source(local_import_id, deadline)

    def get_imported_run_binding(
        self,
        source_specimen_id: str,
        deadline: RequestDeadline | None = None,
    ) -> SpecimenBinding:
        return self._require_session().get_imported_run_binding(source_specimen_id, deadline)

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
        return self._require_session().bind_imported_run_specimen(
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
        return self._require_session().record_imported_run_resolution(
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

    def materialize_reliability_execution(
        self,
        local_import_id: str,
        deadline: RequestDeadline | None,
    ) -> TestExecution:
        return self._require_session().materialize_reliability_execution(local_import_id, deadline)

    def list_reliability_executions(
        self,
        wheel_model_id: str,
        deadline: RequestDeadline | None,
    ) -> tuple[TestExecution, ...]:
        return self._require_session().list_reliability_executions(wheel_model_id, deadline)

    def create_reliability_dataset(
        self,
        *,
        dataset_id: str,
        life_metric_unit: str,
        censoring_policy: str,
        execution_ids: tuple[str, ...],
        failure_ids: tuple[str, ...],
        deadline: RequestDeadline | None,
    ) -> ReliabilityDataset:
        return self._require_session().create_reliability_dataset(
            dataset_id=dataset_id,
            life_metric_unit=life_metric_unit,
            censoring_policy=censoring_policy,
            execution_ids=execution_ids,
            failure_ids=failure_ids,
            deadline=deadline,
        )

    def close(self, *, deadline: RequestDeadline | None = None) -> bool:
        _check_deadline(deadline, "project_close")
        session = self._session
        if session is None:
            return False
        self._session = None
        session.close()
        return True

    def _require_no_session(self) -> None:
        if self._session is not None:
            raise ProjectOperationError("project_locked", "В worker уже открыт проект.")

    def _require_session(self) -> ProjectSession:
        if self._session is None:
            raise ProjectOperationError("storage_error", "Проект не открыт.")
        return self._session

    @staticmethod
    def _validate_container_path(raw_path: str) -> Path:
        path = Path(raw_path)
        if not path.is_absolute() or path.suffix.lower() != ".irproj":
            raise ProjectOperationError("storage_error", "Путь проекта должен быть абсолютным каталогом *.irproj.")
        return Path(os.path.abspath(path))


def _check_deadline(deadline: RequestDeadline | None, stage: str) -> None:
    if deadline is not None:
        deadline.check(stage)
