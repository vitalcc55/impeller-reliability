from importlib.metadata import version
from pathlib import Path
import platform

from impeller_reliability import __version__
from impeller_reliability.application.project_service import ProjectService
from impeller_reliability.integration.r130run.import_jobs import RunPackageImportJobManager
from impeller_reliability.integration.r130run.import_models import (
    ImportedRunVerifyResult,
    imported_run_detail_model,
    imported_run_summary_model,
    specimen_binding_model,
)
from impeller_reliability.integration.r130run.jobs import RunPackageValidationJobManager
from impeller_reliability.integration.r130run.m9a import M9aPackageFacts
from impeller_reliability.integration.r130run.models import RunPackageValidationReport
from impeller_reliability.persistence.r130sh_sources import ImportedRunSummary
from impeller_reliability.persistence.sqlite_health import SCHEMA_VERSION, check_storage
from impeller_reliability.protocol.envelopes import (
    AnalystDocumentSnapshotResult,
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
    FailureObservationResult,
    HandshakeRequest,
    HandshakeResult,
    ImportedRunApplyEnrichmentResolutionRequest,
    ImportedRunBindSpecimenRequest,
    ImportedRunGetRequest,
    ImportedRunGetResolutionStateRequest,
    ImportedRunListRequest,
    ImportedRunListResult,
    ImportedRunVerifySourceRequest,
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
    ReliabilityDatasetCreateVersionRequest,
    ReliabilityDatasetGetVersionRequest,
    ReliabilityDatasetListPageRequest,
    ReliabilityDatasetMemberResult,
    ReliabilityDatasetPageResult,
    ReliabilityDatasetSummaryResult,
    ReliabilityDatasetVersionResult,
    ReliabilityDatasetWriteResultModel,
    ReliabilityExecutionGetDetailRequest,
    ReliabilityExecutionListPageRequest,
    ReliabilityExecutionMaterializeRequest,
    ReliabilityExecutionPageResult,
    ReliabilityExecutionResult,
    ReliabilityExecutionSummaryResult,
    ReliabilityObservationCreateVersionRequest,
    ReliabilityObservationGetVersionRequest,
    ReliabilityObservationListVersionsRequest,
    ReliabilityObservationVersionListResult,
    ReliabilityObservationVersionResult,
    ReliabilityObservationWriteResultModel,
    RequestEnvelope,
    RunPackageImportCancelRequest,
    RunPackageImportDiscardRequest,
    RunPackageImportGetRequest,
    RunPackageImportStartRequest,
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
    "runPackageImport.start",
    "runPackageImport.get",
    "runPackageImport.cancel",
    "runPackageImport.discard",
    "importedRun.list",
    "importedRun.get",
    "importedRun.verifySource",
    "importedRun.getResolutionState",
    "importedRun.bindSpecimen",
    "importedRun.applyEnrichmentResolution",
    "reliabilityExecution.materialize",
    "reliabilityExecution.listPage",
    "reliabilityExecution.getDetail",
    "reliabilityObservation.listVersions",
    "reliabilityObservation.getVersion",
    "reliabilityObservation.createVersion",
    "reliabilityDataset.listPage",
    "reliabilityDataset.getVersion",
    "reliabilityDataset.createVersion",
]


class Dispatcher:
    def __init__(self, state_directory: Path) -> None:
        self._state_directory = state_directory
        self._projects = ProjectService()
        self._run_package_jobs = RunPackageValidationJobManager()
        self._run_package_jobs_shutdown = False
        self._import_jobs = RunPackageImportJobManager()
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
                    supportedRunPackageSchemas=["r130sh.run-package.v1"],
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
            self._import_jobs.shutdown(active_deadline.remaining_seconds(1.5))
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
            self._reset_import_jobs(active_deadline)
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
            self._reset_import_jobs(active_deadline)
            overview = self._projects.open(
                path=request.payload.path,
                application_instance_id=request.payload.applicationInstanceId,
                deadline=active_deadline,
            )
            return self._overview_response(request.requestId, request.revision, overview)
        if isinstance(request, ProjectCloseRequest):
            self._reset_import_jobs(active_deadline)
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
        if self._import_jobs.has_active_job and request.operation not in {
            "runPackageImport.get",
            "runPackageImport.cancel",
            "runPackageImport.discard",
            "importedRun.list",
            "importedRun.get",
            "importedRun.verifySource",
            "importedRun.getResolutionState",
            "reliabilityExecution.listPage",
            "reliabilityExecution.getDetail",
            "reliabilityObservation.listVersions",
            "reliabilityObservation.getVersion",
            "reliabilityDataset.listPage",
            "reliabilityDataset.getVersion",
        }:
            from impeller_reliability.persistence.project_errors import ProjectOperationError

            raise ProjectOperationError(
                "operation_in_progress",
                "Изменение проекта недоступно до завершения импорта R130SH.",
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
            case RunPackageImportStartRequest():
                return SuccessResponse(
                    requestId=request.requestId,
                    revision=request.revision,
                    result=self._import_jobs.start(
                        job_id=request.payload.jobId,
                        project_path=self._projects.active_project_path,
                        source_path=Path(request.payload.sourcePath),
                        allow_diagnostic_partial=request.payload.allowDiagnosticPartial,
                        replace_job_id=request.payload.replaceJobId,
                    ),
                )
            case RunPackageImportGetRequest():
                return SuccessResponse(
                    requestId=request.requestId,
                    revision=request.revision,
                    result=self._import_jobs.get(
                        request.payload.jobId,
                        finalize=self._finalize_import,
                        deadline=active_deadline,
                    ),
                )
            case RunPackageImportCancelRequest():
                return SuccessResponse(
                    requestId=request.requestId,
                    revision=request.revision,
                    result=self._import_jobs.cancel(request.payload.jobId),
                )
            case RunPackageImportDiscardRequest():
                return SuccessResponse(
                    requestId=request.requestId,
                    revision=request.revision,
                    result=self._import_jobs.discard(request.payload.jobId),
                )
            case ImportedRunListRequest():
                return SuccessResponse[ImportedRunListResult](
                    requestId=request.requestId,
                    revision=request.revision,
                    result=ImportedRunListResult(
                        items=[imported_run_summary_model(item) for item in self._projects.list_imported_runs(active_deadline)],
                    ),
                )
            case ImportedRunGetRequest():
                return SuccessResponse(
                    requestId=request.requestId,
                    revision=request.revision,
                    result=imported_run_detail_model(
                        self._projects.get_imported_run(request.payload.localImportId, active_deadline),
                    ),
                )
            case ImportedRunVerifySourceRequest():
                return SuccessResponse(
                    requestId=request.requestId,
                    revision=request.revision,
                    result=ImportedRunVerifyResult(
                        localImportId=request.payload.localImportId,
                        sourceIntegrity=self._projects.verify_imported_run_source(
                            request.payload.localImportId,
                            active_deadline,
                        ),
                    ),
                )
            case ImportedRunGetResolutionStateRequest():
                return SuccessResponse(
                    requestId=request.requestId,
                    revision=request.revision,
                    result=specimen_binding_model(
                        self._projects.get_imported_run_binding(
                            request.payload.sourceSpecimenId,
                            active_deadline,
                        ),
                    ),
                )
            case ImportedRunBindSpecimenRequest():
                return SuccessResponse(
                    requestId=request.requestId,
                    revision=request.revision,
                    result=specimen_binding_model(
                        self._projects.bind_imported_run_specimen(
                            source_specimen_id=request.payload.sourceSpecimenId,
                            local_specimen_id=request.payload.localSpecimenId,
                            expected_revision=request.payload.expectedRevision,
                            actor=request.payload.actor,
                            reason=request.payload.reason,
                            deadline=active_deadline,
                        ),
                    ),
                )
            case ImportedRunApplyEnrichmentResolutionRequest():
                return SuccessResponse(
                    requestId=request.requestId,
                    revision=request.revision,
                    result=imported_run_detail_model(
                        self._projects.record_imported_run_resolution(
                            resolution_id=request.payload.resolutionId,
                            local_import_id=request.payload.localImportId,
                            source_payload_path=request.payload.sourcePayloadPath,
                            source_field=request.payload.sourceField,
                            target_entity_type=request.payload.targetEntityType,
                            target_entity_id=request.payload.targetEntityId,
                            target_field=request.payload.targetField,
                            decision=request.payload.decision,
                            actor=request.payload.actor,
                            reason=request.payload.reason,
                            expected_target_revision=request.payload.expectedTargetRevision,
                            deadline=active_deadline,
                        ),
                    ),
                )
            case ReliabilityExecutionMaterializeRequest():
                return SuccessResponse[ReliabilityExecutionResult](
                    requestId=request.requestId,
                    revision=request.revision,
                    result=self._reliability_execution_result(
                        self._projects.materialize_reliability_execution(
                            request.payload.localImportId,
                            active_deadline,
                        ),
                    ),
                )
            case ReliabilityExecutionListPageRequest():
                page = self._projects.list_reliability_execution_page(
                    request.payload.wheelModelId,
                    request.payload.cursor,
                    request.payload.limit,
                    active_deadline,
                )
                return SuccessResponse[ReliabilityExecutionPageResult](
                    requestId=request.requestId,
                    revision=request.revision,
                    result=ReliabilityExecutionPageResult(
                        items=[self._reliability_execution_summary_result(item) for item in page.items],
                        nextCursor=page.next_cursor,
                    ),
                )
            case ReliabilityExecutionGetDetailRequest():
                return SuccessResponse[ReliabilityExecutionResult](
                    requestId=request.requestId,
                    revision=request.revision,
                    result=self._reliability_execution_result(self._projects.get_reliability_execution(request.payload.executionId, active_deadline)),
                )
            case ReliabilityObservationListVersionsRequest():
                return SuccessResponse[ReliabilityObservationVersionListResult](
                    requestId=request.requestId,
                    revision=request.revision,
                    result=ReliabilityObservationVersionListResult(
                        items=[self._reliability_observation_version_result(item) for item in self._projects.list_reliability_observation_versions(request.payload.executionId, active_deadline)]
                    ),
                )
            case ReliabilityObservationGetVersionRequest():
                return SuccessResponse[ReliabilityObservationVersionResult](
                    requestId=request.requestId,
                    revision=request.revision,
                    result=self._reliability_observation_version_result(self._projects.get_reliability_observation_version(request.payload.observationVersionId, active_deadline)),
                )
            case ReliabilityObservationCreateVersionRequest():
                written = self._projects.create_reliability_observation_version(
                    observation_id=request.payload.observationId,
                    observation_version_id=request.payload.observationVersionId,
                    execution_id=request.payload.executionId,
                    expected_previous_version_id=request.payload.expectedPreviousVersionId,
                    classification=request.payload.classification,
                    endpoint_kind=request.payload.endpointKind,
                    metric_kind=request.payload.metricKind,
                    metric_unit=request.payload.metricUnit,
                    metric_origin=request.payload.metricOrigin,
                    lower_value=request.payload.lowerValue,
                    upper_value=request.payload.upperValue,
                    origin_basis=request.payload.originBasis,
                    endpoint_basis=request.payload.endpointBasis,
                    document_id=request.payload.documentId,
                    document_locator=request.payload.documentLocator,
                    failure_ids=tuple(request.payload.failureIds),
                    actor=request.payload.actor,
                    reason=request.payload.reason,
                    deadline=active_deadline,
                )
                return SuccessResponse[ReliabilityObservationWriteResultModel](
                    requestId=request.requestId,
                    revision=request.revision,
                    result=ReliabilityObservationWriteResultModel(
                        disposition=written.disposition,
                        version=self._reliability_observation_version_result(written.version),
                    ),
                )
            case ReliabilityDatasetListPageRequest():
                dataset_page = self._projects.list_reliability_dataset_page(
                    request.payload.wheelModelId,
                    request.payload.cursor,
                    request.payload.limit,
                    active_deadline,
                )
                return SuccessResponse[ReliabilityDatasetPageResult](
                    requestId=request.requestId,
                    revision=request.revision,
                    result=ReliabilityDatasetPageResult(
                        items=[self._reliability_dataset_summary_result(item) for item in dataset_page.items],
                        nextCursor=dataset_page.next_cursor,
                    ),
                )
            case ReliabilityDatasetGetVersionRequest():
                return SuccessResponse[ReliabilityDatasetVersionResult](
                    requestId=request.requestId,
                    revision=request.revision,
                    result=self._reliability_dataset_version_result(self._projects.get_reliability_dataset_version(request.payload.datasetVersionId, active_deadline)),
                )
            case ReliabilityDatasetCreateVersionRequest():
                written_dataset = self._projects.create_reliability_dataset_version(
                    dataset_id=request.payload.datasetId,
                    dataset_version_id=request.payload.datasetVersionId,
                    wheel_model_id=request.payload.wheelModelId,
                    expected_previous_version_id=request.payload.expectedPreviousVersionId,
                    title=request.payload.title,
                    method=request.payload.method,
                    metric_kind=request.payload.metricKind,
                    metric_unit=request.payload.metricUnit,
                    population_basis=request.payload.populationBasis,
                    methodology_basis=request.payload.methodologyBasis,
                    comparability_basis=request.payload.comparabilityBasis,
                    decisions=tuple(item.model_dump() for item in request.payload.decisions),
                    actor=request.payload.actor,
                    reason=request.payload.reason,
                    deadline=active_deadline,
                )
                return SuccessResponse[ReliabilityDatasetWriteResultModel](
                    requestId=request.requestId,
                    revision=request.revision,
                    result=ReliabilityDatasetWriteResultModel(
                        disposition=written_dataset.disposition,
                        version=self._reliability_dataset_version_result(written_dataset.version),
                    ),
                )

    def close(self) -> None:
        if not self._run_package_jobs_shutdown:
            self._run_package_jobs.shutdown()
            self._run_package_jobs_shutdown = True
        self._import_jobs.shutdown()
        self._projects.close()

    def _reset_import_jobs(self, deadline: RequestDeadline) -> None:
        self._import_jobs.shutdown(deadline.remaining_seconds(1.5))
        self._import_jobs = RunPackageImportJobManager()

    def _finalize_import(
        self,
        local_import_id: str,
        staged_path: Path,
        facts: M9aPackageFacts,
        report: RunPackageValidationReport,
        deadline: RequestDeadline | None,
    ) -> ImportedRunSummary:
        return self._projects.register_imported_run(
            local_import_id=local_import_id,
            staged_path=staged_path,
            facts=facts,
            report=report,
            deadline=deadline,
        )

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
    def _reliability_execution_summary_result(item: object) -> ReliabilityExecutionSummaryResult:
        from impeller_reliability.persistence.reliability_domain import ReliabilityExecutionSummary

        if not isinstance(item, ReliabilityExecutionSummary):
            raise AssertionError("invalid_reliability_execution_summary")
        return ReliabilityExecutionSummaryResult(
            executionId=item.execution_id,
            localSpecimenId=item.local_specimen_id,
            sourceSpecimenId=item.source_specimen_id,
            sourceRunId=item.source_run_id,
            exportRevision=item.export_revision,
            packageKind=item.package_kind,
            method=item.method,
            lifecycleStatus=item.lifecycle_status,
            technicalStatus=item.technical_status,
            specimenOutcome=item.specimen_outcome,
            runValidity=item.run_validity,
            dataCompleteness=item.data_completeness,
            materializedAtUtc=item.materialized_at_utc,
            failureObservationCount=item.failure_observation_count,
            currentObservationVersionId=item.current_observation_version_id,
            currentObservationVersionNumber=item.current_observation_version_number,
            currentClassification=item.current_classification,
        )

    @staticmethod
    def _reliability_observation_version_result(item: object) -> ReliabilityObservationVersionResult:
        from impeller_reliability.persistence.reliability_domain import ReliabilityObservationVersion

        if not isinstance(item, ReliabilityObservationVersion):
            raise AssertionError("invalid_reliability_observation_version")
        document = item.document_snapshot
        return ReliabilityObservationVersionResult(
            observationId=item.observation_id,
            observationVersionId=item.observation_version_id,
            executionId=item.execution_id,
            versionNumber=item.version_number,
            previousVersionId=item.previous_version_id,
            classification=item.classification,
            endpointKind=item.endpoint_kind,
            metricKind=item.metric_kind,
            metricUnit=item.metric_unit,
            metricOrigin=item.metric_origin,
            lowerValue=item.lower_value,
            upperValue=item.upper_value,
            originBasis=item.origin_basis,
            endpointBasis=item.endpoint_basis,
            documentSnapshot=AnalystDocumentSnapshotResult(
                documentId=document.document_id,
                documentKind=document.document_kind,
                title=document.title,
                designation=document.designation,
                revisionLabel=document.revision_label,
                recordRevision=document.record_revision,
                managedFileSha256=document.managed_file_sha256,
            ),
            documentLocator=item.document_locator,
            failureIds=list(item.failure_ids),
            actor=item.actor,
            decisionReason=item.decision_reason,
            createdAtUtc=item.created_at_utc,
            contentSha256=item.content_sha256,
        )

    @staticmethod
    def _reliability_dataset_version_result(item: object) -> ReliabilityDatasetVersionResult:
        from impeller_reliability.persistence.reliability_domain import ReliabilityDatasetVersion

        if not isinstance(item, ReliabilityDatasetVersion):
            raise AssertionError("invalid_reliability_dataset_version")
        return ReliabilityDatasetVersionResult(
            datasetId=item.dataset_id,
            datasetVersionId=item.dataset_version_id,
            wheelModelId=item.wheel_model_id,
            versionNumber=item.version_number,
            previousVersionId=item.previous_version_id,
            policyId=item.policy_id,
            title=item.title,
            method=item.method,
            metricKind=item.metric_kind,
            metricUnit=item.metric_unit,
            populationBasis=item.population_basis,
            methodologyBasis=item.methodology_basis,
            comparabilityBasis=item.comparability_basis,
            members=[
                ReliabilityDatasetMemberResult(
                    observationVersionId=member.observation_version_id,
                    executionId=member.execution_id,
                    localSpecimenId=member.local_specimen_id,
                    sourceRunId=member.source_run_id,
                    policyEligibility=member.policy_eligibility,
                    policyReason=member.policy_reason,
                    decision=member.decision,
                    inclusionReason=member.inclusion_reason,
                )
                for member in item.members
            ],
            actor=item.actor,
            decisionReason=item.decision_reason,
            createdAtUtc=item.created_at_utc,
            contentSha256=item.content_sha256,
        )

    @staticmethod
    def _reliability_dataset_summary_result(item: object) -> ReliabilityDatasetSummaryResult:
        from impeller_reliability.persistence.reliability_domain import ReliabilityDatasetSummary

        if not isinstance(item, ReliabilityDatasetSummary):
            raise AssertionError("invalid_reliability_dataset_summary")
        return ReliabilityDatasetSummaryResult(
            datasetId=item.dataset_id,
            wheelModelId=item.wheel_model_id,
            latestVersionId=item.latest_version_id,
            latestVersionNumber=item.latest_version_number,
            title=item.title,
            method=item.method,
            metricKind=item.metric_kind,
            metricUnit=item.metric_unit,
            includedCount=item.included_count,
            excludedCount=item.excluded_count,
            createdAtUtc=item.created_at_utc,
        )

    @staticmethod
    def _reliability_execution_result(execution: object) -> ReliabilityExecutionResult:
        from impeller_reliability.persistence.reliability_domain import TestExecution

        if not isinstance(execution, TestExecution):
            raise AssertionError("invalid_reliability_execution")
        return ReliabilityExecutionResult(
            executionId=execution.execution_id,
            localImportId=execution.local_import_id,
            localSpecimenId=execution.local_specimen_id,
            wheelModelId=execution.wheel_model_id,
            sourceSpecimenId=execution.source_specimen_id,
            sourceRunId=execution.source_run_id,
            exportRevision=execution.export_revision,
            packageKind=execution.package_kind,
            method=execution.method,
            lifecycleStatus=execution.lifecycle_status,
            plannedParametersSnapshot=execution.planned_parameters_snapshot,
            resultSummary=execution.result_summary,
            sourceOuterPackageSha256=execution.source_outer_package_sha256,
            materializedAtUtc=execution.materialized_at_utc,
            failureObservations=[
                FailureObservationResult(
                    failureId=item.failure_id,
                    failureType=item.failure_type,
                    subjectKind=item.subject_kind,
                    sourceEventReference=item.source_event_reference,
                    sourceFieldReference=item.source_field_reference,
                    cyclesAtFailure=item.cycles_at_failure,
                    durationS=item.duration_s,
                    rpm=item.rpm,
                    vibrationSummary=item.vibration_summary,
                    observedAtUtc=item.observed_at_utc,
                    sourceOuterPackageSha256=item.source_outer_package_sha256,
                )
                for item in execution.failure_observations
            ],
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
