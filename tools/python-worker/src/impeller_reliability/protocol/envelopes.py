from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, field_validator

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
]

ProjectStatus = Literal["draft", "active", "completed", "archived"]
CanonicalUtcTimestamp = Annotated[str, AfterValidator(require_canonical_utc_timestamp)]


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
    applicationVersion: str = Field(min_length=1, max_length=64)
    draft: ProjectDraft


class ProjectOpenPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(min_length=1, max_length=32_767)
    applicationInstanceId: str = Field(min_length=1, max_length=128)


class ProjectUpdateMetadataPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expectedRevision: int = Field(ge=1)
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
    | ProjectCreateBackupRequest,
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

    projectId: str
    path: str
    name: str
    projectNumber: str
    description: str
    status: ProjectStatus
    recordRevision: int = Field(ge=1)
    createdAtUtc: CanonicalUtcTimestamp
    updatedAtUtc: CanonicalUtcTimestamp
    createdWithApplicationVersion: str
    schemaVersion: int = Field(ge=1)


class ProjectCloseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    closed: bool


class ProjectBackupResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fileName: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    createdAtUtc: CanonicalUtcTimestamp


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
        "internal_error",
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
