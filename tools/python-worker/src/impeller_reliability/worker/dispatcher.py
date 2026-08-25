from importlib.metadata import version
from pathlib import Path
import platform

from impeller_reliability import __version__
from impeller_reliability.application.project_service import ProjectService
from impeller_reliability.persistence.sqlite_health import SCHEMA_VERSION, check_storage
from impeller_reliability.protocol.envelopes import (
    HandshakeRequest,
    HandshakeResult,
    Operation,
    PingRequest,
    PingResult,
    ProjectBackupResult,
    ProjectCloseRequest,
    ProjectCloseResult,
    ProjectCreateBackupRequest,
    ProjectCreateRequest,
    ProjectGetOverviewRequest,
    ProjectOpenRequest,
    ProjectOverviewResult,
    ProjectUpdateMetadataRequest,
    RequestEnvelope,
    ShutdownRequest,
    ShutdownResult,
    StorageHealthRequest,
    StorageHealthResult,
    SuccessResponse,
    SuccessResponseType,
)
from impeller_reliability.worker.deadline import RequestDeadline

CAPABILITIES: list[Operation] = [
    "system.handshake",
    "system.ping",
    "system.shutdown",
    "storage.health",
    "project.create",
    "project.open",
    "project.close",
    "project.getOverview",
    "project.updateMetadata",
    "project.createBackup",
]


class Dispatcher:
    def __init__(self, state_directory: Path) -> None:
        self._state_directory = state_directory
        self._projects = ProjectService()
        self.shutdown_requested = False

    def dispatch(
        self,
        request: RequestEnvelope,
        deadline: RequestDeadline | None = None,
    ) -> SuccessResponseType:
        active_deadline = deadline or RequestDeadline.start(request.deadlineMs)
        active_deadline.check("dispatch")
        if isinstance(request, HandshakeRequest):
            return SuccessResponse[HandshakeResult](
                requestId=request.requestId,
                revision=request.revision,
                result=HandshakeResult(
                    workerVersion=__version__,
                    protocolVersions=[1],
                    pythonVersion=platform.python_version(),
                    numpyVersion=version("numpy"),
                    scipyVersion=version("scipy"),
                    databaseSchemaVersions=[SCHEMA_VERSION],
                    algorithmVersions={},
                    supportedRunPackageSchemas=[],
                    supportedPlanSchemas=[],
                    capabilities=CAPABILITIES,
                ),
            )
        if isinstance(request, PingRequest):
            return SuccessResponse[PingResult](
                requestId=request.requestId,
                revision=request.revision,
                result=PingResult(),
            )
        if isinstance(request, ShutdownRequest):
            self._projects.close(deadline=active_deadline)
            self.shutdown_requested = True
            return SuccessResponse[ShutdownResult](
                requestId=request.requestId,
                revision=request.revision,
                result=ShutdownResult(),
            )
        if isinstance(request, StorageHealthRequest):
            return SuccessResponse[StorageHealthResult](
                requestId=request.requestId,
                revision=request.revision,
                result=StorageHealthResult.model_validate(check_storage(self._state_directory / "health.sqlite")),
            )
        if isinstance(request, ProjectCreateRequest):
            overview = self._projects.create(
                path=request.payload.path,
                application_instance_id=request.payload.applicationInstanceId,
                application_version=request.payload.applicationVersion,
                name=request.payload.draft.name,
                project_number=request.payload.draft.projectNumber,
                description=request.payload.draft.description,
                status=request.payload.draft.status,
                deadline=active_deadline,
            )
            return self._overview_response(request.requestId, request.revision, overview)
        if isinstance(request, ProjectOpenRequest):
            overview = self._projects.open(
                path=request.payload.path,
                application_instance_id=request.payload.applicationInstanceId,
                deadline=active_deadline,
            )
            return self._overview_response(request.requestId, request.revision, overview)
        if isinstance(request, ProjectCloseRequest):
            return SuccessResponse[ProjectCloseResult](
                requestId=request.requestId,
                revision=request.revision,
                result=ProjectCloseResult(closed=self._projects.close(deadline=active_deadline)),
            )
        if isinstance(request, ProjectGetOverviewRequest):
            return self._overview_response(
                request.requestId,
                request.revision,
                self._projects.get_overview(deadline=active_deadline),
            )
        match request:
            case ProjectUpdateMetadataRequest():
                metadata = request.payload.metadata
                overview = self._projects.update_metadata(
                    expected_revision=request.payload.expectedRevision,
                    name=metadata.name,
                    project_number=metadata.projectNumber,
                    description=metadata.description,
                    status=metadata.status,
                    deadline=active_deadline,
                )
                return self._overview_response(request.requestId, request.revision, overview)
            case ProjectCreateBackupRequest():
                backup_path, sha256, created_at = self._projects.create_backup(deadline=active_deadline)
                return SuccessResponse[ProjectBackupResult](
                    requestId=request.requestId,
                    revision=request.revision,
                    result=ProjectBackupResult(fileName=backup_path.name, sha256=sha256, createdAtUtc=created_at),
                )

    def close(self) -> None:
        self._projects.close()

    @staticmethod
    def _overview_response(request_id: str, revision: int, overview: object) -> SuccessResponse[ProjectOverviewResult]:
        from impeller_reliability.persistence.project_session import ProjectOverview

        if not isinstance(overview, ProjectOverview):
            raise AssertionError("invalid_project_overview")
        return SuccessResponse[ProjectOverviewResult](
            requestId=request_id,
            revision=revision,
            result=ProjectOverviewResult(
                projectId=overview.project_id,
                path=overview.path,
                name=overview.name,
                projectNumber=overview.project_number,
                description=overview.description,
                status=overview.status,
                recordRevision=overview.record_revision,
                createdAtUtc=overview.created_at_utc,
                updatedAtUtc=overview.updated_at_utc,
                createdWithApplicationVersion=overview.created_with_application_version,
                schemaVersion=overview.schema_version,
            ),
        )
