from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Operation = Literal["system.handshake", "system.ping", "system.shutdown", "storage.health"]


class RequestEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    protocolVersion: Literal[1]
    requestId: str = Field(min_length=1, max_length=128)
    kind: Literal["request"]
    operation: Operation
    revision: int = Field(ge=0)
    deadlineMs: int = Field(gt=0, le=30_000)
    payload: dict[str, object]


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


class SuccessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    protocolVersion: Literal[1] = 1
    requestId: str
    kind: Literal["response"] = "response"
    ok: Literal[True] = True
    result: dict[str, object]
    evidence: dict[str, object]
    warnings: list[str]


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    protocolVersion: Literal[1] = 1
    requestId: str
    kind: Literal["response"] = "response"
    ok: Literal[False] = False
    error: ErrorPayload
