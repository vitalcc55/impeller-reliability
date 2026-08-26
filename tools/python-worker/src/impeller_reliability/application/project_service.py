from __future__ import annotations

import os
from pathlib import Path
import shutil
from uuid import uuid4

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
from impeller_reliability.persistence.timestamps import utc_now
from impeller_reliability.worker.deadline import RequestDeadline


class ProjectService:
    def __init__(self) -> None:
        self._session: ProjectSession | None = None
        self._migrator = ProjectMigrator()

    @property
    def has_active_session(self) -> bool:
        return self._session is not None

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
            )
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
