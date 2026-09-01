from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from impeller_reliability.integration.r130run.import_models import (
    ImportedRunDetailModel,
    ImportedRunSummaryModel,
    ImportedRunVerifyResult,
    RunPackageImportDiscardResult,
    RunPackageImportJobSnapshot,
    SpecimenBindingModel,
)
from impeller_reliability.integration.r130run.models import (
    RunPackageValidationDiscardResult,
    RunPackageValidationJobSnapshot,
)
from impeller_reliability.persistence.analyst_dossier import canonical_date, canonical_decimal, canonical_uuid4
from impeller_reliability.persistence.project_values import (
    require_application_version,
    require_canonical_project_id,
)
from impeller_reliability.persistence.timestamps import require_canonical_utc_timestamp

Operation = Literal[
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
    "reliabilityExecution.listByWheel",
]

ProjectStatus = Literal["draft", "active", "completed", "archived"]
CanonicalUtcTimestamp = Annotated[str, AfterValidator(require_canonical_utc_timestamp)]
ApplicationVersion = Annotated[str, AfterValidator(require_application_version)]
ProjectId = Annotated[str, AfterValidator(require_canonical_project_id)]
EntityId = Annotated[str, AfterValidator(canonical_uuid4)]
CanonicalDecimal = Annotated[str | None, AfterValidator(canonical_decimal)]
CanonicalDate = Annotated[str | None, AfterValidator(canonical_date)]
DocumentKind = Literal[
    "technical_specification",
    "individual_test_method",
    "typical_test_method",
    "customer_requirement",
    "test_request",
    "operational_documentation",
    "standard",
    "drawing",
    "measurement_or_attestation_record",
    "other",
]
IntegrityStatus = Literal["not_attached", "verified", "missing", "modified", "verification_error"]


class EmptyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RequestBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    protocolVersion: Literal[1]
    requestId: str = Field(min_length=1, max_length=128)
    kind: Literal["request"]
    revision: int = Field(ge=0)
    deadlineMs: int = Field(gt=0, le=30_000)


class HandshakeRequest(RequestBase):
    operation: Literal["system.handshake"]
    payload: EmptyPayload


class PingRequest(RequestBase):
    operation: Literal["system.ping"]
    payload: EmptyPayload


class ShutdownRequest(RequestBase):
    operation: Literal["system.shutdown"]
    payload: EmptyPayload


class StorageHealthRequest(RequestBase):
    operation: Literal["storage.health"]
    payload: EmptyPayload


class ProjectDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=200)
    projectNumber: str = Field(max_length=100)
    description: str = Field(max_length=4000)
    status: ProjectStatus

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if normalized == "":
            raise ValueError("project_name_blank")
        return normalized

    @field_validator("projectNumber", "description")
    @classmethod
    def normalize_optional_text(cls, value: str) -> str:
        return value.strip()


class ProjectCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(min_length=1, max_length=32_767)
    applicationInstanceId: str = Field(min_length=1, max_length=128)
    applicationVersion: ApplicationVersion
    draft: ProjectDraft


class ProjectOpenPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(min_length=1, max_length=32_767)
    applicationInstanceId: str = Field(min_length=1, max_length=128)


class ProjectUpdateMetadataPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expectedRevision: int = Field(ge=1, le=9_007_199_254_740_991)
    metadata: ProjectDraft


class ProjectCreateRequest(RequestBase):
    operation: Literal["project.create"]
    payload: ProjectCreatePayload


class ProjectOpenRequest(RequestBase):
    operation: Literal["project.open"]
    payload: ProjectOpenPayload


class ProjectCloseRequest(RequestBase):
    operation: Literal["project.close"]
    payload: EmptyPayload


class ProjectGetOverviewRequest(RequestBase):
    operation: Literal["project.getOverview"]
    payload: EmptyPayload


class ProjectUpdateMetadataRequest(RequestBase):
    operation: Literal["project.updateMetadata"]
    payload: ProjectUpdateMetadataPayload


class ProjectCreateBackupRequest(RequestBase):
    operation: Literal["project.createBackup"]
    payload: EmptyPayload


class CustomerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fullName: str = Field(min_length=1, max_length=300)
    legalAddress: str = Field(max_length=1_000)
    actualAddress: str = Field(max_length=1_000)
    notes: str = Field(max_length=4_000)

    @field_validator("fullName")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if normalized == "":
            raise ValueError("customer_name_blank")
        return normalized

    @field_validator("legalAddress", "actualAddress", "notes")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()


class CustomerUpsertPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expectedRevision: int | None = Field(default=None, ge=1)
    customer: CustomerDraft


class WheelModelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fullName: str = Field(min_length=1, max_length=300)
    designation: str = Field(max_length=200)
    nominalDiameterMm: CanonicalDecimal = None
    nominalSpeedRpm: int | None = Field(default=None, gt=0, le=9_007_199_254_740_991)
    bladeCount: int | None = Field(default=None, gt=0, le=9_007_199_254_740_991)
    geometryDescription: str = Field(max_length=4_000)
    compositionDescription: str = Field(max_length=4_000)
    materialDescription: str = Field(max_length=4_000)
    notes: str = Field(max_length=4_000)

    @field_validator("fullName")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if normalized == "":
            raise ValueError("wheel_name_blank")
        return normalized

    @field_validator("designation", "geometryDescription", "compositionDescription", "materialDescription", "notes")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()


class WheelModelCreatePayload(WheelModelDraft):
    wheelModelId: EntityId


class WheelModelIdPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    wheelModelId: EntityId


class WheelModelListPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    includeArchived: bool = False


class WheelModelUpdatePayload(WheelModelIdPayload):
    expectedRevision: int = Field(ge=1)
    wheelModel: WheelModelDraft


class EntityRevisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expectedRevision: int = Field(ge=1)


class WheelModelRevisionPayload(WheelModelIdPayload, EntityRevisionPayload):
    pass


class SpecimenDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    wheelModelId: EntityId
    identificationNumber: str = Field(min_length=1, max_length=200)
    batchNumber: str = Field(max_length=200)
    marking: str = Field(max_length=500)
    manufacturedOn: CanonicalDate = None
    receivedOn: CanonicalDate = None
    workingDiameterMm: CanonicalDecimal = None
    initialConditionNotes: str = Field(max_length=4_000)
    notes: str = Field(max_length=4_000)

    @field_validator("identificationNumber")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if normalized == "":
            raise ValueError("specimen_identifier_blank")
        return normalized

    @field_validator("batchNumber", "marking", "initialConditionNotes", "notes")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()


class SpecimenCreatePayload(SpecimenDraft):
    specimenId: EntityId


class SpecimenIdPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    specimenId: EntityId


class SpecimenListPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    includeArchived: bool = False


class SpecimenUpdatePayload(SpecimenIdPayload):
    expectedRevision: int = Field(ge=1)
    specimen: SpecimenDraft


class SpecimenRevisionPayload(SpecimenIdPayload, EntityRevisionPayload):
    pass


class CaseDocumentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    documentKind: DocumentKind
    title: str = Field(min_length=1, max_length=300)
    designation: str = Field(max_length=200)
    revisionLabel: str = Field(max_length=200)
    documentDate: CanonicalDate = None
    issuer: str = Field(max_length=300)
    notes: str = Field(max_length=4_000)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if normalized == "":
            raise ValueError("case_document_title_blank")
        return normalized

    @field_validator("designation", "revisionLabel", "issuer", "notes")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()


class CaseDocumentIdPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    caseDocumentId: EntityId


class CaseDocumentCreatePayload(CaseDocumentIdPayload):
    document: CaseDocumentDraft
    wheelModelIds: list[EntityId]
    specimenIds: list[EntityId]


class CaseDocumentCreateWithFilePayload(CaseDocumentCreatePayload):
    sourcePath: str = Field(min_length=1, max_length=32_767)


class CaseDocumentListPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    includeArchived: bool = False
    documentKind: DocumentKind | None = None


class CaseDocumentUpdatePayload(CaseDocumentCreatePayload):
    expectedRevision: int = Field(ge=1)


class CaseDocumentAttachFilePayload(CaseDocumentIdPayload, EntityRevisionPayload):
    sourcePath: str = Field(min_length=1, max_length=32_767)


class CaseDocumentRevisionPayload(CaseDocumentIdPayload, EntityRevisionPayload):
    pass


class RunPackageValidationStartPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jobId: EntityId
    replaceJobId: EntityId | None = None
    sourcePath: str = Field(min_length=1, max_length=32_767)
    validationBudgetMs: int = Field(ge=1_000, le=1_800_000)


class RunPackageValidationJobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jobId: EntityId


class RunPackageImportStartPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jobId: EntityId
    replaceJobId: EntityId | None = None
    sourcePath: str = Field(min_length=1, max_length=32_767)
    allowDiagnosticPartial: bool


class RunPackageImportJobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jobId: EntityId


class ImportedRunIdPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    localImportId: EntityId


class ImportedRunBindingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sourceSpecimenId: str = Field(min_length=1, max_length=200)
    localSpecimenId: EntityId | None
    expectedRevision: int = Field(ge=1, le=9_007_199_254_740_991)
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2_000)


class ImportedRunResolutionStatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sourceSpecimenId: str = Field(min_length=1, max_length=200)


class ImportedRunEnrichmentResolutionPayload(ImportedRunIdPayload):
    resolutionId: EntityId
    sourcePayloadPath: str = Field(min_length=1, max_length=512)
    sourceField: str = Field(min_length=1, max_length=200)
    targetEntityType: Literal["customer_profile", "wheel_model", "specimen"]
    targetEntityId: str = Field(min_length=1, max_length=200)
    targetField: str = Field(min_length=1, max_length=100)
    decision: Literal["use_source", "use_analyst", "copied_to_analyst"]
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(max_length=2_000)
    expectedTargetRevision: int | None = Field(ge=1, le=9_007_199_254_740_991)


class ReliabilityExecutionListByWheelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    wheelModelId: EntityId


class CustomerGetRequest(RequestBase):
    operation: Literal["caseCustomer.get"]
    payload: EmptyPayload


class CustomerUpsertRequest(RequestBase):
    operation: Literal["caseCustomer.upsert"]
    payload: CustomerUpsertPayload


class WheelModelCreateRequest(RequestBase):
    operation: Literal["wheelModel.create"]
    payload: WheelModelCreatePayload


class WheelModelListRequest(RequestBase):
    operation: Literal["wheelModel.list"]
    payload: WheelModelListPayload


class WheelModelGetRequest(RequestBase):
    operation: Literal["wheelModel.get"]
    payload: WheelModelIdPayload


class WheelModelUpdateRequest(RequestBase):
    operation: Literal["wheelModel.update"]
    payload: WheelModelUpdatePayload


class WheelModelArchiveRequest(RequestBase):
    operation: Literal["wheelModel.archive"]
    payload: WheelModelRevisionPayload


class WheelModelRestoreRequest(RequestBase):
    operation: Literal["wheelModel.restore"]
    payload: WheelModelRevisionPayload


class SpecimenCreateRequest(RequestBase):
    operation: Literal["specimen.create"]
    payload: SpecimenCreatePayload


class SpecimenListRequest(RequestBase):
    operation: Literal["specimen.list"]
    payload: SpecimenListPayload


class SpecimenGetRequest(RequestBase):
    operation: Literal["specimen.get"]
    payload: SpecimenIdPayload


class SpecimenUpdateRequest(RequestBase):
    operation: Literal["specimen.update"]
    payload: SpecimenUpdatePayload


class SpecimenArchiveRequest(RequestBase):
    operation: Literal["specimen.archive"]
    payload: SpecimenRevisionPayload


class SpecimenRestoreRequest(RequestBase):
    operation: Literal["specimen.restore"]
    payload: SpecimenRevisionPayload


class CaseDocumentCreateRequest(RequestBase):
    operation: Literal["caseDocument.create"]
    payload: CaseDocumentCreatePayload


class CaseDocumentCreateWithFileRequest(RequestBase):
    operation: Literal["caseDocument.createWithFile"]
    payload: CaseDocumentCreateWithFilePayload


class CaseDocumentListRequest(RequestBase):
    operation: Literal["caseDocument.list"]
    payload: CaseDocumentListPayload


class CaseDocumentGetRequest(RequestBase):
    operation: Literal["caseDocument.get"]
    payload: CaseDocumentIdPayload


class CaseDocumentUpdateRequest(RequestBase):
    operation: Literal["caseDocument.update"]
    payload: CaseDocumentUpdatePayload


class CaseDocumentAttachFileRequest(RequestBase):
    operation: Literal["caseDocument.attachFile"]
    payload: CaseDocumentAttachFilePayload


class CaseDocumentVerifyFileRequest(RequestBase):
    operation: Literal["caseDocument.verifyFile"]
    payload: CaseDocumentIdPayload


class CaseDocumentArchiveRequest(RequestBase):
    operation: Literal["caseDocument.archive"]
    payload: CaseDocumentRevisionPayload


class CaseDocumentRestoreRequest(RequestBase):
    operation: Literal["caseDocument.restore"]
    payload: CaseDocumentRevisionPayload


class CaseDocumentResolveFileRequest(RequestBase):
    operation: Literal["caseDocument.resolveFile"]
    payload: CaseDocumentIdPayload


class RunPackageValidationStartRequest(RequestBase):
    operation: Literal["runPackageValidation.start"]
    payload: RunPackageValidationStartPayload


class RunPackageValidationGetRequest(RequestBase):
    operation: Literal["runPackageValidation.get"]
    payload: RunPackageValidationJobPayload


class RunPackageValidationCancelRequest(RequestBase):
    operation: Literal["runPackageValidation.cancel"]
    payload: RunPackageValidationJobPayload


class RunPackageValidationDiscardRequest(RequestBase):
    operation: Literal["runPackageValidation.discard"]
    payload: RunPackageValidationJobPayload


class RunPackageImportStartRequest(RequestBase):
    operation: Literal["runPackageImport.start"]
    payload: RunPackageImportStartPayload


class RunPackageImportGetRequest(RequestBase):
    operation: Literal["runPackageImport.get"]
    payload: RunPackageImportJobPayload


class RunPackageImportCancelRequest(RequestBase):
    operation: Literal["runPackageImport.cancel"]
    payload: RunPackageImportJobPayload


class RunPackageImportDiscardRequest(RequestBase):
    operation: Literal["runPackageImport.discard"]
    payload: RunPackageImportJobPayload


class ImportedRunListRequest(RequestBase):
    operation: Literal["importedRun.list"]
    payload: EmptyPayload


class ImportedRunGetRequest(RequestBase):
    operation: Literal["importedRun.get"]
    payload: ImportedRunIdPayload


class ImportedRunVerifySourceRequest(RequestBase):
    operation: Literal["importedRun.verifySource"]
    payload: ImportedRunIdPayload


class ImportedRunGetResolutionStateRequest(RequestBase):
    operation: Literal["importedRun.getResolutionState"]
    payload: ImportedRunResolutionStatePayload


class ImportedRunBindSpecimenRequest(RequestBase):
    operation: Literal["importedRun.bindSpecimen"]
    payload: ImportedRunBindingPayload


class ImportedRunApplyEnrichmentResolutionRequest(RequestBase):
    operation: Literal["importedRun.applyEnrichmentResolution"]
    payload: ImportedRunEnrichmentResolutionPayload


class ReliabilityExecutionMaterializeRequest(RequestBase):
    operation: Literal["reliabilityExecution.materialize"]
    payload: ImportedRunIdPayload


class ReliabilityExecutionListByWheelRequest(RequestBase):
    operation: Literal["reliabilityExecution.listByWheel"]
    payload: ReliabilityExecutionListByWheelPayload


type RequestEnvelope = Annotated[
    HandshakeRequest
    | PingRequest
    | ShutdownRequest
    | StorageHealthRequest
    | ProjectCreateRequest
    | ProjectOpenRequest
    | ProjectCloseRequest
    | ProjectGetOverviewRequest
    | ProjectUpdateMetadataRequest
    | ProjectCreateBackupRequest
    | CustomerGetRequest
    | CustomerUpsertRequest
    | WheelModelCreateRequest
    | WheelModelListRequest
    | WheelModelGetRequest
    | WheelModelUpdateRequest
    | WheelModelArchiveRequest
    | WheelModelRestoreRequest
    | SpecimenCreateRequest
    | SpecimenListRequest
    | SpecimenGetRequest
    | SpecimenUpdateRequest
    | SpecimenArchiveRequest
    | SpecimenRestoreRequest
    | CaseDocumentCreateRequest
    | CaseDocumentCreateWithFileRequest
    | CaseDocumentListRequest
    | CaseDocumentGetRequest
    | CaseDocumentUpdateRequest
    | CaseDocumentAttachFileRequest
    | CaseDocumentVerifyFileRequest
    | CaseDocumentArchiveRequest
    | CaseDocumentRestoreRequest
    | CaseDocumentResolveFileRequest
    | RunPackageValidationStartRequest
    | RunPackageValidationGetRequest
    | RunPackageValidationCancelRequest
    | RunPackageValidationDiscardRequest
    | RunPackageImportStartRequest
    | RunPackageImportGetRequest
    | RunPackageImportCancelRequest
    | RunPackageImportDiscardRequest
    | ImportedRunListRequest
    | ImportedRunGetRequest
    | ImportedRunVerifySourceRequest
    | ImportedRunGetResolutionStateRequest
    | ImportedRunBindSpecimenRequest
    | ImportedRunApplyEnrichmentResolutionRequest
    | ReliabilityExecutionMaterializeRequest
    | ReliabilityExecutionListByWheelRequest,
    Field(discriminator="operation"),
]
REQUEST_ENVELOPE_ADAPTER: TypeAdapter[RequestEnvelope] = TypeAdapter(RequestEnvelope)


class HandshakeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workerVersion: str
    protocolVersions: list[int]
    pythonVersion: str
    numpyVersion: str
    scipyVersion: str
    databaseSchemaVersions: list[int]
    algorithmVersions: dict[str, str]
    supportedRunPackageSchemas: list[str]
    supportedPlanSchemas: list[str]
    capabilities: list[Operation]


class PingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pong: Literal[True] = True


class ShutdownResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    accepted: Literal[True] = True


class StorageHealthResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["ok", "error"]
    databaseSchemaVersion: int = Field(ge=0)
    quickCheck: str
    foreignKeys: bool
    journalMode: str


class ProjectOverviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    projectId: ProjectId
    path: str
    name: str = Field(min_length=1, max_length=200)
    projectNumber: str = Field(max_length=100)
    description: str = Field(max_length=4_000)
    status: ProjectStatus
    recordRevision: int = Field(ge=1)
    createdAtUtc: CanonicalUtcTimestamp
    updatedAtUtc: CanonicalUtcTimestamp
    createdWithApplicationVersion: ApplicationVersion
    schemaVersion: int = Field(ge=1)


class ProjectCloseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    closed: bool


class ProjectBackupResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fileName: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    createdAtUtc: CanonicalUtcTimestamp


CompletenessWarningCode = Literal[
    "customer_address_missing",
    "wheel_nominal_diameter_missing",
    "wheel_nominal_speed_missing",
    "specimen_working_diameter_missing",
]


class CustomerProfileResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    projectId: ProjectId
    fullName: str
    legalAddress: str
    actualAddress: str
    notes: str
    recordRevision: int = Field(ge=1)
    createdAtUtc: CanonicalUtcTimestamp
    updatedAtUtc: CanonicalUtcTimestamp
    warnings: list[CompletenessWarningCode]


class CustomerGetResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    customer: CustomerProfileResult | None


class WheelModelResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    wheelModelId: EntityId
    fullName: str
    designation: str
    nominalDiameterMm: str | None
    nominalSpeedRpm: int | None
    bladeCount: int | None
    geometryDescription: str
    compositionDescription: str
    materialDescription: str
    notes: str
    recordRevision: int = Field(ge=1)
    archivedAtUtc: CanonicalUtcTimestamp | None
    createdAtUtc: CanonicalUtcTimestamp
    updatedAtUtc: CanonicalUtcTimestamp
    warnings: list[CompletenessWarningCode]


class WheelModelSummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    wheelModelId: EntityId
    fullName: str
    designation: str
    recordRevision: int = Field(ge=1)
    archivedAtUtc: CanonicalUtcTimestamp | None
    warnings: list[CompletenessWarningCode]


class WheelModelListResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[WheelModelSummaryResult]


class SpecimenResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    specimenId: EntityId
    wheelModelId: EntityId
    wheelModelName: str
    identificationNumber: str
    batchNumber: str
    marking: str
    manufacturedOn: str | None
    receivedOn: str | None
    workingDiameterMm: str | None
    initialConditionNotes: str
    notes: str
    recordRevision: int = Field(ge=1)
    archivedAtUtc: CanonicalUtcTimestamp | None
    createdAtUtc: CanonicalUtcTimestamp
    updatedAtUtc: CanonicalUtcTimestamp
    warnings: list[CompletenessWarningCode]


class SpecimenSummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    specimenId: EntityId
    wheelModelId: EntityId
    wheelModelName: str
    identificationNumber: str
    recordRevision: int = Field(ge=1)
    archivedAtUtc: CanonicalUtcTimestamp | None
    warnings: list[CompletenessWarningCode]


class SpecimenListResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[SpecimenSummaryResult]


CaseDocumentWarningCode = Literal[
    "case_document_file_missing",
    "case_document_designation_missing",
    "case_document_revision_missing",
]


class CaseDocumentFileResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    originalFileName: str = Field(min_length=1, max_length=255)
    mediaType: str = Field(min_length=1, max_length=128)
    sizeBytes: int = Field(gt=0, le=100 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attachedAtUtc: CanonicalUtcTimestamp

    @field_validator("originalFileName")
    @classmethod
    def validate_original_file_name(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("invalid_original_file_name")
        return value


class CaseDocumentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    caseDocumentId: EntityId
    documentKind: DocumentKind
    title: str
    designation: str
    revisionLabel: str
    documentDate: CanonicalDate
    issuer: str
    notes: str
    recordRevision: int = Field(ge=1)
    archivedAtUtc: CanonicalUtcTimestamp | None
    createdAtUtc: CanonicalUtcTimestamp
    updatedAtUtc: CanonicalUtcTimestamp
    file: CaseDocumentFileResult | None
    integrityStatus: IntegrityStatus
    wheelModelIds: list[EntityId]
    specimenIds: list[EntityId]
    warnings: list[CaseDocumentWarningCode]


class CaseDocumentSummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    caseDocumentId: EntityId
    documentKind: DocumentKind
    title: str
    designation: str
    recordRevision: int = Field(ge=1)
    archivedAtUtc: CanonicalUtcTimestamp | None
    warnings: list[CaseDocumentWarningCode]


class CaseDocumentListResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[CaseDocumentSummaryResult]


class CaseDocumentResolveFileResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    absolutePath: str = Field(min_length=1, max_length=32_767)


class ImportedRunListResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[ImportedRunSummaryModel]


class FailureObservationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    failureId: EntityId
    failureType: Literal["specimen_outcome", "technical_interruption"]
    subjectKind: Literal["specimen", "equipment", "unknown"]
    sourceEventReference: str = Field(min_length=1, max_length=512)
    sourceFieldReference: str = Field(min_length=1, max_length=512)
    cyclesAtFailure: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    durationS: str | None = Field(default=None, max_length=64)
    rpm: str | None = Field(default=None, max_length=64)
    vibrationSummary: dict[str, object]
    observedAtUtc: str | None


class ReliabilityExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    executionId: EntityId
    localImportId: EntityId
    localSpecimenId: EntityId
    sourceSpecimenId: str = Field(min_length=1, max_length=200)
    method: Literal["rbd", "rpt", "pmn"]
    lifecycleStatus: Literal["completed", "interrupted", "failed"]
    plannedParametersSnapshot: dict[str, object]
    resultSummary: dict[str, object]
    sourceOuterPackageSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    materializedAtUtc: str
    failureObservations: list[FailureObservationResult] = Field(max_length=64)


class ReliabilityExecutionListResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[ReliabilityExecutionResult]


class ErrorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: Literal[
        "contract_error",
        "validation_error",
        "domain_error",
        "storage_error",
        "cancelled",
        "timeout",
        "worker_unavailable",
        "project_locked",
        "corrupt_project",
        "incompatible_schema",
        "revision_conflict",
        "entity_not_found",
        "entity_archived",
        "entity_in_use",
        "duplicate_entity",
        "duplicate_document_content",
        "file_already_attached",
        "unsupported_file_type",
        "file_too_large",
        "file_missing",
        "file_integrity_mismatch",
        "internal_error",
        "operation_in_progress",
        "job_id_conflict",
        "import_integrity_conflict",
        "resolution_conflict",
    ]
    message: str
    details: dict[str, object]
    retryable: bool


class SuccessResponse[ResultT: BaseModel](BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    protocolVersion: Literal[1] = 1
    requestId: str
    revision: int = Field(ge=0)
    kind: Literal["response"] = "response"
    ok: Literal[True] = True
    result: ResultT
    evidence: dict[str, object] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


type SuccessResponseType = (
    SuccessResponse[HandshakeResult]
    | SuccessResponse[PingResult]
    | SuccessResponse[ShutdownResult]
    | SuccessResponse[StorageHealthResult]
    | SuccessResponse[ProjectOverviewResult]
    | SuccessResponse[ProjectCloseResult]
    | SuccessResponse[ProjectBackupResult]
    | SuccessResponse[CustomerGetResult]
    | SuccessResponse[CustomerProfileResult]
    | SuccessResponse[WheelModelResult]
    | SuccessResponse[WheelModelListResult]
    | SuccessResponse[SpecimenResult]
    | SuccessResponse[SpecimenListResult]
    | SuccessResponse[CaseDocumentResult]
    | SuccessResponse[CaseDocumentListResult]
    | SuccessResponse[CaseDocumentResolveFileResult]
    | SuccessResponse[RunPackageValidationJobSnapshot]
    | SuccessResponse[RunPackageValidationDiscardResult]
    | SuccessResponse[RunPackageImportJobSnapshot]
    | SuccessResponse[RunPackageImportDiscardResult]
    | SuccessResponse[ImportedRunListResult]
    | SuccessResponse[ImportedRunDetailModel]
    | SuccessResponse[ImportedRunVerifyResult]
    | SuccessResponse[SpecimenBindingModel]
    | SuccessResponse[ReliabilityExecutionResult]
    | SuccessResponse[ReliabilityExecutionListResult]
)


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    protocolVersion: Literal[1] = 1
    requestId: str
    revision: int = Field(ge=0)
    kind: Literal["response"] = "response"
    ok: Literal[False] = False
    error: ErrorPayload


type ProtocolResponse = SuccessResponseType | ErrorResponse
