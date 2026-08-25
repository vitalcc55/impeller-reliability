from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Operation = Literal["system.handshake", "system.ping", "system.shutdown", "storage.health"]


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


type RequestEnvelope = Annotated[
    HandshakeRequest | PingRequest | ShutdownRequest | StorageHealthRequest,
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


type SuccessResponseType = SuccessResponse[HandshakeResult] | SuccessResponse[PingResult] | SuccessResponse[ShutdownResult] | SuccessResponse[StorageHealthResult]


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    protocolVersion: Literal[1] = 1
    requestId: str
    revision: int = Field(ge=0)
    kind: Literal["response"] = "response"
    ok: Literal[False] = False
    error: ErrorPayload


type ProtocolResponse = SuccessResponseType | ErrorResponse
