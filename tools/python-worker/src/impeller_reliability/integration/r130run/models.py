from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

UPSTREAM_REPOSITORY = "https://github.com/vitalcc55/R130SH"
UPSTREAM_COMMIT = "01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63"
CONTRACT_SCHEMA = "r130sh.run-package.v1"
VALIDATION_LEVEL = "producer_m9a_contract"
VALIDATOR_VERSION = "m03b.1"

StructuralVerdict = Literal["passed", "failed"]
SemanticVerdict = Literal["passed", "partial", "failed", "not_available"]
SemanticCoverageStatus = Literal["covered", "not_available", "contract_gap"]
FindingSeverity = Literal["error", "warning", "info"]


class RunPackageProducer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    buildId: str = Field(min_length=1, max_length=128)
    gitCommit: str = Field(min_length=1, max_length=128)


class RunPackageSemanticCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    area: str = Field(min_length=1, max_length=96)
    status: SemanticCoverageStatus
    contractSource: str = Field(min_length=1, max_length=160)


class RunPackageFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: str = Field(min_length=1, max_length=96, pattern=r"^[a-z0-9_]+$")
    severity: FindingSeverity
    location: str = Field(min_length=1, max_length=512)
    message: str = Field(min_length=1, max_length=512)
    contractSource: str = Field(min_length=1, max_length=160)


class RunPackageFindingCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    error: int = Field(ge=0)
    warning: int = Field(ge=0)
    info: int = Field(ge=0)
    total: int = Field(ge=0)
    truncated: bool


class RunPackageValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    validatorVersion: Literal["m03b.1"] = "m03b.1"
    validationLevel: Literal["producer_m9a_contract"] = "producer_m9a_contract"
    upstreamRepository: Literal["https://github.com/vitalcc55/R130SH"] = "https://github.com/vitalcc55/R130SH"
    upstreamCommit: Literal["01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63"] = "01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63"
    contractSchema: Literal["r130sh.run-package.v1"] = "r130sh.run-package.v1"
    sourceFileName: str = Field(min_length=1, max_length=255)
    outerPackageSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    outerSizeBytes: int = Field(gt=0)
    packageId: str | None
    exportRevision: int | None = Field(default=None, ge=1)
    runId: str | None
    packageKind: Literal["final", "diagnostic_partial"] | None
    producer: RunPackageProducer | None
    entryCount: int = Field(ge=0)
    declaredPayloadBytes: int = Field(ge=0)
    validatedPayloadBytes: int = Field(ge=0)
    structuralVerdict: StructuralVerdict
    semanticVerdict: SemanticVerdict
    semanticCoverage: list[RunPackageSemanticCoverage]
    findingCounts: RunPackageFindingCounts
    findings: list[RunPackageFinding] = Field(max_length=200)
    startedAtUtc: str
    finishedAtUtc: str


JobState = Literal["queued", "running", "cancelling", "completed", "failed", "cancelled"]
JobPhase = Literal[
    "source_check",
    "outer_hash",
    "zip_index",
    "manifest",
    "payload_integrity",
    "semantic_validation",
    "finalizing",
]
ProgressKind = Literal["known", "unknown"]
JobErrorCode = Literal["cancelled", "timeout", "source_changed", "storage_error", "validation_error"]


class RunPackageValidationProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: ProgressKind
    completedBytes: int = Field(ge=0)
    totalBytes: int = Field(ge=0)
    completedEntries: int = Field(ge=0)
    totalEntries: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counters(self) -> RunPackageValidationProgress:
        if self.totalBytes > 0 and self.completedBytes > self.totalBytes:
            raise ValueError("completed_bytes_exceed_total")
        if self.totalEntries > 0 and self.completedEntries > self.totalEntries:
            raise ValueError("completed_entries_exceed_total")
        return self


class RunPackageValidationJobError(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: JobErrorCode
    message: str = Field(min_length=1, max_length=512)
    retryable: bool


class RunPackageValidationJobSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jobId: str
    state: JobState
    phase: JobPhase
    progress: RunPackageValidationProgress
    startedAtUtc: str | None
    finishedAtUtc: str | None
    report: RunPackageValidationReport | None
    typedError: RunPackageValidationJobError | None

    @model_validator(mode="after")
    def validate_state_payload(self) -> RunPackageValidationJobSnapshot:
        if self.state == "completed":
            if self.report is None or self.typedError is not None or self.finishedAtUtc is None:
                raise ValueError("completed_job_payload_invalid")
        elif self.state in {"failed", "cancelled"}:
            if self.report is not None or self.typedError is None or self.finishedAtUtc is None:
                raise ValueError("failed_job_payload_invalid")
        elif self.report is not None or self.typedError is not None or self.finishedAtUtc is not None:
            raise ValueError("active_job_payload_invalid")
        return self


class RunPackageValidationDiscardResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jobId: str
    discarded: Literal[True] = True
