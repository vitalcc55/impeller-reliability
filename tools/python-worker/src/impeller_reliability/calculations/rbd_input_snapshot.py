from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

EntityId = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"),
]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RbdInputField = Literal[
    "base_cycles",
    "reserve_factor",
    "nominal_rpm",
    "acceleration_duration_s",
    "deceleration_duration_s",
]
RbdFailureApplicability = Literal[
    "exact_supported",
    "unavailable",
    "interval_endpoint",
    "right_censored",
    "unsupported_phase",
    "ambiguous_pauses",
]


class RbdOperationEvidenceReferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: EntityId | None
    document_record_revision: int | None = Field(ge=1)
    document_locator: str = Field(max_length=1000)
    observation_version_id: EntityId | None


class RbdOperationFieldSelectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: RbdInputField
    origin: Literal["source", "manual"]
    manual_value: str | None = Field(max_length=64)
    basis: str = Field(max_length=2000)
    evidence: RbdOperationEvidenceReferenceModel | None


class RbdOperationFailureEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    applicability: RbdFailureApplicability
    duration_to_failure_s: str | None = Field(max_length=64)
    basis: str = Field(max_length=2000)
    failure_observation_ids: list[EntityId] = Field(max_length=64)
    evidence: RbdOperationEvidenceReferenceModel | None


class RbdOperationSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schemaVersion: Literal[1]
    analysisInputSnapshotId: EntityId
    calculationSnapshotId: EntityId
    executionId: EntityId
    planSelection: Literal["original", "effective"]
    selections: list[RbdOperationFieldSelectionModel] = Field(min_length=5, max_length=5)
    failureEvidence: RbdOperationFailureEvidenceModel | None
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(max_length=2000)
    algorithmId: Literal["rbd_reference"]
    algorithmVersion: Literal["1.0.0"]
    numericPolicy: Literal["exact_fraction_v1"]


class RbdSourceProducerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)
    buildId: str = Field(min_length=1, max_length=200)
    gitCommit: str = Field(min_length=1, max_length=200)


class RbdSourceMethodicalRequirementsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    required_cycles_exact: str = Field(max_length=128)
    required_steady_duration_s_exact: str = Field(max_length=128)


class RbdSourceExecutionTargetsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_cycles: str = Field(pattern=r"^(?:0|[1-9][0-9]{0,63})$")
    target_steady_duration_s: str = Field(max_length=128)
    total_duration_s: str = Field(max_length=128)
    rounding_policy: str = Field(min_length=1, max_length=512)


class RbdSourceSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    executionId: EntityId
    localImportId: EntityId
    packageId: str = Field(min_length=1, max_length=200)
    runId: str = Field(min_length=1, max_length=200)
    exportRevision: int = Field(ge=1, le=9_007_199_254_740_991)
    outerPackageSha256: Sha256
    sourceSnapshotSha256: Sha256
    producer: RbdSourceProducerModel
    planSelection: Literal["original", "effective"]
    payloadPath: Literal["plan/original.json", "plan/effective.json"]
    payloadSha256: Sha256
    planId: str = Field(min_length=1, max_length=200)
    planRevision: int = Field(ge=1, le=9_007_199_254_740_991)
    methodicalRequirements: RbdSourceMethodicalRequirementsModel
    executionTargets: RbdSourceExecutionTargetsModel


class RbdDocumentEvidenceSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    documentId: EntityId
    recordRevision: int = Field(ge=1)
    documentKind: Literal[
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
    title: str = Field(max_length=300)
    designation: str = Field(max_length=200)
    revisionLabel: str = Field(max_length=200)
    fileSha256: Sha256 | None
    locator: str = Field(max_length=1000)


class RbdObservationEvidenceSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    observationVersionId: EntityId
    versionNumber: int = Field(ge=1)
    classification: Literal["failure", "right_censored", "withdrawn", "invalid"]
    endpointKind: Literal["exact", "right_bound", "interval", "unavailable"]
    metricKind: Literal["rbd_steady_rotation_time", "rpt_start_stop_cycles"] | None
    metricUnit: Literal["hours", "count"] | None
    lowerValue: str | None = Field(max_length=64)
    upperValue: str | None = Field(max_length=64)
    contentSha256: Sha256


class RbdSavedEvidenceSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document: RbdDocumentEvidenceSnapshotModel | None
    observation: RbdObservationEvidenceSnapshotModel | None


class RbdSavedFieldSelectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: RbdInputField
    unit: Literal["cycle", "1", "rpm", "s"]
    origin: Literal["source", "manual"]
    value: str = Field(min_length=1, max_length=64)
    rawSourceValue: str | None = Field(max_length=128)
    sourceReference: str = Field(min_length=1, max_length=200)
    basis: str = Field(max_length=2000)
    evidence: RbdSavedEvidenceSnapshotModel | None


class RbdSavedFailureObservationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    failureObservationId: EntityId
    failureType: Literal["specimen_outcome", "technical_interruption"]
    subjectKind: Literal["specimen", "equipment", "unknown"]
    sourceEventReference: str = Field(min_length=1, max_length=512)
    sourceFieldReference: str = Field(min_length=1, max_length=512)
    durationS: str | None = Field(max_length=64)
    rpm: str | None = Field(max_length=64)
    observedAtUtc: str | None = Field(max_length=64)
    sourceOuterPackageSha256: Sha256


class RbdSavedFailureEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    applicability: RbdFailureApplicability
    durationToFailureS: str | None = Field(max_length=64)
    basis: str = Field(max_length=2000)
    failureObservations: list[RbdSavedFailureObservationModel] = Field(max_length=64)
    evidence: RbdSavedEvidenceSnapshotModel | None


class RbdInputSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schemaVersion: Literal[1]
    operation: RbdOperationSnapshotModel
    source: RbdSourceSnapshotModel
    fieldSelections: list[RbdSavedFieldSelectionModel] = Field(min_length=5, max_length=5)
    failureEvidence: RbdSavedFailureEvidenceModel | None
