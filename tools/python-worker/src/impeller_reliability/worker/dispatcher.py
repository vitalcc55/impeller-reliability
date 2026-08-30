from importlib.metadata import version
from pathlib import Path
import platform

from impeller_reliability import __version__
from impeller_reliability.application.project_service import ProjectService
from impeller_reliability.integration.r130run.jobs import RunPackageValidationJobManager
from impeller_reliability.persistence.sqlite_health import SCHEMA_VERSION, check_storage
from impeller_reliability.protocol.envelopes import (
    CaseDocumentArchiveRequest,
    CaseDocumentAttachFileRequest,
    CaseDocumentCreateRequest,
    CaseDocumentCreateWithFileRequest,
    CaseDocumentFileResult,
    CaseDocumentGetRequest,
    CaseDocumentListRequest,
    CaseDocumentListResult,
    CaseDocumentResolveFileRequest,
    CaseDocumentResolveFileResult,
    CaseDocumentRestoreRequest,
    CaseDocumentResult,
    CaseDocumentSummaryResult,
    CaseDocumentUpdateRequest,
    CaseDocumentVerifyFileRequest,
    CustomerGetRequest,
    CustomerGetResult,
    CustomerProfileResult,
    CustomerUpsertRequest,
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
    RunPackageValidationCancelRequest,
    RunPackageValidationDiscardRequest,
    RunPackageValidationGetRequest,
    RunPackageValidationStartRequest,
    ShutdownRequest,
    ShutdownResult,
    SpecimenArchiveRequest,
    SpecimenCreateRequest,
    SpecimenGetRequest,
    SpecimenListRequest,
    SpecimenListResult,
    SpecimenRestoreRequest,
    SpecimenResult,
    SpecimenSummaryResult,
    SpecimenUpdateRequest,
    StorageHealthRequest,
    StorageHealthResult,
    SuccessResponse,
    SuccessResponseType,
    WheelModelArchiveRequest,
    WheelModelCreateRequest,
    WheelModelGetRequest,
    WheelModelListRequest,
    WheelModelListResult,
    WheelModelRestoreRequest,
    WheelModelResult,
    WheelModelSummaryResult,
    WheelModelUpdateRequest,
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
    "caseCustomer.get",
    "caseCustomer.upsert",
    "wheelModel.create",
    "wheelModel.list",
    "wheelModel.get",
    "wheelModel.update",
    "wheelModel.archive",
    "wheelModel.restore",
    "specimen.create",
    "specimen.list",
    "specimen.get",
    "specimen.update",
    "specimen.archive",
    "specimen.restore",
    "caseDocument.create",
    "caseDocument.createWithFile",
    "caseDocument.list",
    "caseDocument.get",
    "caseDocument.update",
    "caseDocument.attachFile",
    "caseDocument.verifyFile",
    "caseDocument.archive",
    "caseDocument.restore",
    "caseDocument.resolveFile",
    "runPackageValidation.start",
    "runPackageValidation.get",
    "runPackageValidation.cancel",
    "runPackageValidation.discard",
]


class Dispatcher:
    def __init__(self, state_directory: Path) -> None:
        self._state_directory = state_directory
        self._projects = ProjectService()
        self._run_package_jobs = RunPackageValidationJobManager()
        self._run_package_jobs_shutdown = False
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
            self.shutdown_requested = True
            self._run_package_jobs_shutdown = True
            self._run_package_jobs.shutdown(active_deadline.remaining_seconds(1.5))
            self._projects.close(deadline=active_deadline)
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
            case CustomerGetRequest():
                customer = self._projects.get_customer(active_deadline)
                return SuccessResponse[CustomerGetResult](
                    requestId=request.requestId,
                    revision=request.revision,
                    result=CustomerGetResult(customer=None if customer is None else self._customer_result(customer)),
                )
            case CustomerUpsertRequest():
                customer = self._projects.upsert_customer(
                    expected_revision=request.payload.expectedRevision,
                    values=request.payload.customer.model_dump(mode="python"),
                    deadline=active_deadline,
                )
                return SuccessResponse[CustomerProfileResult](requestId=request.requestId, revision=request.revision, result=self._customer_result(customer))
            case WheelModelCreateRequest():
                wheel = self._projects.create_wheel(request.payload.model_dump(mode="python"), active_deadline)
                return SuccessResponse[WheelModelResult](requestId=request.requestId, revision=request.revision, result=self._wheel_result(wheel))
            case WheelModelListRequest():
                wheels = self._projects.list_wheels(request.payload.includeArchived, active_deadline)
                return SuccessResponse[WheelModelListResult](requestId=request.requestId, revision=request.revision, result=WheelModelListResult(items=[self._wheel_summary(item) for item in wheels]))
            case WheelModelGetRequest():
                return SuccessResponse[WheelModelResult](
                    requestId=request.requestId, revision=request.revision, result=self._wheel_result(self._projects.get_wheel(request.payload.wheelModelId, active_deadline))
                )
            case WheelModelUpdateRequest():
                wheel = self._projects.update_wheel(request.payload.wheelModelId, request.payload.expectedRevision, request.payload.wheelModel.model_dump(mode="python"), active_deadline)
                return SuccessResponse[WheelModelResult](requestId=request.requestId, revision=request.revision, result=self._wheel_result(wheel))
            case WheelModelArchiveRequest() | WheelModelRestoreRequest():
                wheel = self._projects.set_wheel_archived(request.payload.wheelModelId, request.payload.expectedRevision, isinstance(request, WheelModelArchiveRequest), active_deadline)
                return SuccessResponse[WheelModelResult](requestId=request.requestId, revision=request.revision, result=self._wheel_result(wheel))
            case SpecimenCreateRequest():
                specimen = self._projects.create_specimen(request.payload.model_dump(mode="python"), active_deadline)
                return SuccessResponse[SpecimenResult](requestId=request.requestId, revision=request.revision, result=self._specimen_result(specimen))
            case SpecimenListRequest():
                specimens = self._projects.list_specimens(request.payload.includeArchived, active_deadline)
                return SuccessResponse[SpecimenListResult](
                    requestId=request.requestId, revision=request.revision, result=SpecimenListResult(items=[self._specimen_summary(item) for item in specimens])
                )
            case SpecimenGetRequest():
                return SuccessResponse[SpecimenResult](
                    requestId=request.requestId, revision=request.revision, result=self._specimen_result(self._projects.get_specimen(request.payload.specimenId, active_deadline))
                )
            case SpecimenUpdateRequest():
                specimen = self._projects.update_specimen(request.payload.specimenId, request.payload.expectedRevision, request.payload.specimen.model_dump(mode="python"), active_deadline)
                return SuccessResponse[SpecimenResult](requestId=request.requestId, revision=request.revision, result=self._specimen_result(specimen))
            case SpecimenArchiveRequest() | SpecimenRestoreRequest():
                specimen = self._projects.set_specimen_archived(request.payload.specimenId, request.payload.expectedRevision, isinstance(request, SpecimenArchiveRequest), active_deadline)
                return SuccessResponse[SpecimenResult](requestId=request.requestId, revision=request.revision, result=self._specimen_result(specimen))
            case CaseDocumentCreateRequest():
                document = self._projects.create_case_document(
                    request.payload.caseDocumentId,
                    request.payload.document.model_dump(mode="python"),
                    tuple(request.payload.wheelModelIds),
                    tuple(request.payload.specimenIds),
                    active_deadline,
                )
                return self._case_document_response(request.requestId, request.revision, document)
            case CaseDocumentCreateWithFileRequest():
                document = self._projects.create_case_document_with_file(
                    request.payload.caseDocumentId,
                    request.payload.document.model_dump(mode="python"),
                    tuple(request.payload.wheelModelIds),
                    tuple(request.payload.specimenIds),
                    Path(request.payload.sourcePath),
                    active_deadline,
                )
                return self._case_document_response(request.requestId, request.revision, document)
            case CaseDocumentListRequest():
                documents = self._projects.list_case_documents(
                    request.payload.includeArchived,
                    request.payload.documentKind,
                    active_deadline,
                )
                return SuccessResponse[CaseDocumentListResult](
                    requestId=request.requestId,
                    revision=request.revision,
                    result=CaseDocumentListResult(items=[self._case_document_summary(item) for item in documents]),
                )
            case CaseDocumentGetRequest():
                return self._case_document_response(
                    request.requestId,
                    request.revision,
                    self._projects.get_case_document(request.payload.caseDocumentId, active_deadline),
                )
            case CaseDocumentUpdateRequest():
                document = self._projects.update_case_document(
                    request.payload.caseDocumentId,
                    request.payload.expectedRevision,
                    request.payload.document.model_dump(mode="python"),
                    tuple(request.payload.wheelModelIds),
                    tuple(request.payload.specimenIds),
                    active_deadline,
                )
                return self._case_document_response(request.requestId, request.revision, document)
            case CaseDocumentAttachFileRequest():
                document = self._projects.attach_case_document_file(
                    request.payload.caseDocumentId,
                    request.payload.expectedRevision,
                    Path(request.payload.sourcePath),
                    active_deadline,
                )
                return self._case_document_response(request.requestId, request.revision, document)
            case CaseDocumentVerifyFileRequest():
                return self._case_document_response(
                    request.requestId,
                    request.revision,
                    self._projects.verify_case_document_file(
                        request.payload.caseDocumentId,
                        active_deadline,
                    ),
                )
            case CaseDocumentArchiveRequest() | CaseDocumentRestoreRequest():
                document = self._projects.set_case_document_archived(
                    request.payload.caseDocumentId,
                    request.payload.expectedRevision,
                    isinstance(request, CaseDocumentArchiveRequest),
                    active_deadline,
                )
                return self._case_document_response(request.requestId, request.revision, document)
            case CaseDocumentResolveFileRequest():
                resolved = self._projects.resolve_case_document_file(
                    request.payload.caseDocumentId,
                    active_deadline,
                )
                return SuccessResponse[CaseDocumentResolveFileResult](
                    requestId=request.requestId,
                    revision=request.revision,
                    result=CaseDocumentResolveFileResult(absolutePath=str(resolved)),
                )
            case RunPackageValidationStartRequest():
                return SuccessResponse(
                    requestId=request.requestId,
                    revision=request.revision,
                    result=self._run_package_jobs.start(
                        request.payload.jobId,
                        Path(request.payload.sourcePath),
                        request.payload.validationBudgetMs,
                        request.payload.replaceJobId,
                    ),
                )
            case RunPackageValidationGetRequest():
                return SuccessResponse(
                    requestId=request.requestId,
                    revision=request.revision,
                    result=self._run_package_jobs.get(request.payload.jobId),
                )
            case RunPackageValidationCancelRequest():
                return SuccessResponse(
                    requestId=request.requestId,
                    revision=request.revision,
                    result=self._run_package_jobs.cancel(request.payload.jobId),
                )
            case RunPackageValidationDiscardRequest():
                return SuccessResponse(
                    requestId=request.requestId,
                    revision=request.revision,
                    result=self._run_package_jobs.discard(request.payload.jobId),
                )

    def close(self) -> None:
        if not self._run_package_jobs_shutdown:
            self._run_package_jobs.shutdown()
            self._run_package_jobs_shutdown = True
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

    @staticmethod
    def _customer_result(customer: object) -> CustomerProfileResult:
        from impeller_reliability.persistence.analyst_dossier import CustomerProfile

        if not isinstance(customer, CustomerProfile):
            raise AssertionError("invalid_customer")
        return CustomerProfileResult(
            projectId=customer.project_id,
            fullName=customer.full_name,
            legalAddress=customer.legal_address,
            actualAddress=customer.actual_address,
            notes=customer.notes,
            recordRevision=customer.record_revision,
            createdAtUtc=customer.created_at_utc,
            updatedAtUtc=customer.updated_at_utc,
            warnings=list(customer.warnings),
        )

    @staticmethod
    def _wheel_result(wheel: object) -> WheelModelResult:
        from impeller_reliability.persistence.analyst_dossier import WheelModel

        if not isinstance(wheel, WheelModel):
            raise AssertionError("invalid_wheel")
        return WheelModelResult(
            wheelModelId=wheel.wheel_model_id,
            fullName=wheel.full_name,
            designation=wheel.designation,
            nominalDiameterMm=wheel.nominal_diameter_mm,
            nominalSpeedRpm=wheel.nominal_speed_rpm,
            bladeCount=wheel.blade_count,
            geometryDescription=wheel.geometry_description,
            compositionDescription=wheel.composition_description,
            materialDescription=wheel.material_description,
            notes=wheel.notes,
            recordRevision=wheel.record_revision,
            archivedAtUtc=wheel.archived_at_utc,
            createdAtUtc=wheel.created_at_utc,
            updatedAtUtc=wheel.updated_at_utc,
            warnings=list(wheel.warnings),
        )

    @staticmethod
    def _wheel_summary(wheel: object) -> WheelModelSummaryResult:
        from impeller_reliability.persistence.analyst_dossier import WheelModel

        if not isinstance(wheel, WheelModel):
            raise AssertionError("invalid_wheel")
        return WheelModelSummaryResult(
            wheelModelId=wheel.wheel_model_id,
            fullName=wheel.full_name,
            designation=wheel.designation,
            recordRevision=wheel.record_revision,
            archivedAtUtc=wheel.archived_at_utc,
            warnings=list(wheel.warnings),
        )

    @staticmethod
    def _specimen_result(specimen: object) -> SpecimenResult:
        from impeller_reliability.persistence.analyst_dossier import Specimen

        if not isinstance(specimen, Specimen):
            raise AssertionError("invalid_specimen")
        return SpecimenResult(
            specimenId=specimen.specimen_id,
            wheelModelId=specimen.wheel_model_id,
            wheelModelName=specimen.wheel_model_name,
            identificationNumber=specimen.identification_number,
            batchNumber=specimen.batch_number,
            marking=specimen.marking,
            manufacturedOn=specimen.manufactured_on,
            receivedOn=specimen.received_on,
            workingDiameterMm=specimen.working_diameter_mm,
            initialConditionNotes=specimen.initial_condition_notes,
            notes=specimen.notes,
            recordRevision=specimen.record_revision,
            archivedAtUtc=specimen.archived_at_utc,
            createdAtUtc=specimen.created_at_utc,
            updatedAtUtc=specimen.updated_at_utc,
            warnings=list(specimen.warnings),
        )

    @staticmethod
    def _specimen_summary(specimen: object) -> SpecimenSummaryResult:
        from impeller_reliability.persistence.analyst_dossier import Specimen

        if not isinstance(specimen, Specimen):
            raise AssertionError("invalid_specimen")
        return SpecimenSummaryResult(
            specimenId=specimen.specimen_id,
            wheelModelId=specimen.wheel_model_id,
            wheelModelName=specimen.wheel_model_name,
            identificationNumber=specimen.identification_number,
            recordRevision=specimen.record_revision,
            archivedAtUtc=specimen.archived_at_utc,
            warnings=list(specimen.warnings),
        )

    @staticmethod
    def _case_document_response(
        request_id: str,
        revision: int,
        document: object,
    ) -> SuccessResponse[CaseDocumentResult]:
        return SuccessResponse[CaseDocumentResult](
            requestId=request_id,
            revision=revision,
            result=Dispatcher._case_document_result(document),
        )

    @staticmethod
    def _case_document_result(document: object) -> CaseDocumentResult:
        from impeller_reliability.persistence.case_documents import CaseDocument

        if not isinstance(document, CaseDocument):
            raise AssertionError("invalid_case_document")
        file = document.file
        return CaseDocumentResult(
            caseDocumentId=document.case_document_id,
            documentKind=document.document_kind,
            title=document.title,
            designation=document.designation,
            revisionLabel=document.revision_label,
            documentDate=document.document_date,
            issuer=document.issuer,
            notes=document.notes,
            recordRevision=document.record_revision,
            archivedAtUtc=document.archived_at_utc,
            createdAtUtc=document.created_at_utc,
            updatedAtUtc=document.updated_at_utc,
            file=(
                None
                if file is None
                else CaseDocumentFileResult(
                    originalFileName=file.original_file_name,
                    mediaType=file.media_type,
                    sizeBytes=file.size_bytes,
                    sha256=file.sha256,
                    attachedAtUtc=file.attached_at_utc,
                )
            ),
            integrityStatus=document.integrity_status,
            wheelModelIds=list(document.wheel_model_ids),
            specimenIds=list(document.specimen_ids),
            warnings=list(document.warnings),
        )

    @staticmethod
    def _case_document_summary(document: object) -> CaseDocumentSummaryResult:
        from impeller_reliability.persistence.case_documents import CaseDocument

        if not isinstance(document, CaseDocument):
            raise AssertionError("invalid_case_document")
        return CaseDocumentSummaryResult(
            caseDocumentId=document.case_document_id,
            documentKind=document.document_kind,
            title=document.title,
            designation=document.designation,
            recordRevision=document.record_revision,
            archivedAtUtc=document.archived_at_utc,
            warnings=list(document.warnings),
        )
