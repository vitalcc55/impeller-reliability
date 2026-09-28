from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from impeller_reliability.calculations.document_snapshot import DocumentEvidenceSnapshotModel, EntityId, Sha256

RptInputField = Literal[
    "nominal_rpm",
    "design_cycles",
    "reserve_factor",
    "acceleration_duration_s",
    "steady_duration_s",
    "deceleration_duration_s",
]
RptFailureApplicability = Literal[
    "exact_supported",
    "unavailable",
    "interval_endpoint",
    "right_censored",
    "ambiguous_pauses",
    "variable_cycle",
    "unknown_start",
    "repeated_attempts",
]
RptLowerPointPolicy = Literal["one_percent", "full_stop", "explicit_rpm"]


class RptOperationEvidenceReferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: EntityId
    document_record_revision: int = Field(ge=1)
    document_locator: str = Field(min_length=1, max_length=1000)


class RptOperationFieldSelectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: RptInputField
    origin: Literal["source", "manual"]
    manual_value: str | None = Field(max_length=64)
    basis: str = Field(max_length=2000)
    evidence: RptOperationEvidenceReferenceModel | None


class RptOperationFailureEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    applicability: RptFailureApplicability
    duration_to_failure_s: str | None = Field(max_length=64)
    basis: str = Field(max_length=2000)
    evidence: RptOperationEvidenceReferenceModel | None


class RptOperationSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schemaVersion: Literal[1]
    analysisInputSnapshotId: EntityId
    calculationSnapshotId: EntityId
    executionId: EntityId
    planSelection: Literal["original", "effective"]
    selections: list[RptOperationFieldSelectionModel] = Field(min_length=6, max_length=6)
    failureEvidence: RptOperationFailureEvidenceModel | None
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(max_length=2000)
    algorithmId: Literal["rpt_reference"]
    algorithmVersion: Literal["1.0.0"]
    numericPolicy: Literal["exact_fraction_v1"]

    @model_validator(mode="after")
    def validate_selection_fields(self) -> RptOperationSnapshotModel:
        if {item.field for item in self.selections} != {"nominal_rpm", "design_cycles", "reserve_factor", "acceleration_duration_s", "steady_duration_s", "deceleration_duration_s"}:
            raise ValueError("rpt_selection_fields_invalid")
        return self


class RptSourceProducerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)
    buildId: str = Field(min_length=1, max_length=200)
    gitCommit: str = Field(min_length=1, max_length=200)


class RptSourceValuesModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    nominal_rpm: str | None = Field(max_length=512)
    design_cycles: str | None = Field(max_length=512)
    reserve_factor: str | None = Field(max_length=512)
    acceleration_duration_s: str | None = Field(max_length=512)
    steady_duration_s: str | None = Field(max_length=512)
    deceleration_duration_s: str | None = Field(max_length=512)
    lower_point_policy: RptLowerPointPolicy
    explicit_lower_rpm: str | None = Field(max_length=512)


class RptSourceMethodicalRequirementsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    required_cycles_exact: str = Field(max_length=512)
    cycle_duration_s_exact: str = Field(max_length=512)
    required_total_duration_s_exact: str = Field(max_length=512)


class RptSourceExecutionTargetsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_cycles: str = Field(pattern=r"^(?:0|[1-9][0-9]{0,63})$")
    upper_rpm: str = Field(max_length=512)
    lower_rpm: str = Field(max_length=512)
    cycle_duration_s: str = Field(max_length=512)
    total_duration_s: str = Field(max_length=512)
    lower_point_policy: RptLowerPointPolicy
    rounding_policy: str = Field(min_length=1, max_length=512)


class RptSourceSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    executionId: EntityId
    localImportId: EntityId
    packageId: str = Field(min_length=1, max_length=200)
    runId: str = Field(min_length=1, max_length=200)
    exportRevision: int = Field(ge=1, le=9_007_199_254_740_991)
    outerPackageSha256: Sha256
    sourceSnapshotSha256: Sha256
    producer: RptSourceProducerModel
    planSelection: Literal["original", "effective"]
    payloadPath: Literal["plan/original.json", "plan/effective.json"]
    payloadSha256: Sha256
    planId: str = Field(min_length=1, max_length=200)
    planRevision: int = Field(ge=1, le=9_007_199_254_740_991)
    sourceValues: RptSourceValuesModel
    methodicalRequirements: RptSourceMethodicalRequirementsModel
    executionTargets: RptSourceExecutionTargetsModel


class RptSavedFieldSelectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: RptInputField
    unit: Literal["rpm", "cycle", "1", "s"]
    origin: Literal["source", "manual"]
    value: str = Field(min_length=1, max_length=64)
    rawSourceValue: str | None = Field(max_length=512)
    sourceReference: str = Field(min_length=1, max_length=200)
    basis: str = Field(max_length=2000)
    document: DocumentEvidenceSnapshotModel | None


class RptSavedFailureEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    applicability: RptFailureApplicability
    durationToFailureS: str | None = Field(max_length=64)
    basis: str = Field(max_length=2000)
    document: DocumentEvidenceSnapshotModel | None


class RptInputSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schemaVersion: Literal[1]
    operation: RptOperationSnapshotModel
    source: RptSourceSnapshotModel
    fieldSelections: list[RptSavedFieldSelectionModel] = Field(min_length=6, max_length=6)
    failureEvidence: RptSavedFailureEvidenceModel | None

    @model_validator(mode="after")
    def validate_field_set(self) -> RptInputSnapshotModel:
        if {item.field for item in self.fieldSelections} != {"nominal_rpm", "design_cycles", "reserve_factor", "acceleration_duration_s", "steady_duration_s", "deceleration_duration_s"}:
            raise ValueError("rpt_saved_fields_invalid")
        return self
