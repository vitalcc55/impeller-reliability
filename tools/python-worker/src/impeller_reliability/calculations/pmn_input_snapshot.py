from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from impeller_reliability.calculations.document_snapshot import DocumentEvidenceSnapshotModel, EntityId, Sha256
from impeller_reliability.calculations.pmn import PmnFailureApplicability

PmnInputField = Literal[
    "nominal_rpm",
    "speed_factor",
    "target_cycles",
    "acceleration_duration_s",
    "steady_duration_s",
    "deceleration_duration_s",
]
_FIELDS = ("nominal_rpm", "speed_factor", "target_cycles", "acceleration_duration_s", "steady_duration_s", "deceleration_duration_s")


class PmnOperationEvidenceReferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: EntityId
    document_record_revision: int = Field(ge=1)
    document_locator: str = Field(min_length=1, max_length=1000)


class PmnOperationFieldSelectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: PmnInputField
    origin: Literal["source", "manual"]
    manual_value: str | None = Field(max_length=64)
    basis: str = Field(max_length=2000)
    evidence: PmnOperationEvidenceReferenceModel | None


class PmnOperationFailureEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    applicability: PmnFailureApplicability
    duration_to_failure_s: str | None = Field(max_length=64)
    basis: str = Field(max_length=2000)
    evidence: PmnOperationEvidenceReferenceModel | None


class PmnOperationSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schemaVersion: Literal[1]
    analysisInputSnapshotId: EntityId
    calculationSnapshotId: EntityId
    executionId: EntityId
    planSelection: Literal["original", "effective"]
    selections: list[PmnOperationFieldSelectionModel] = Field(min_length=6, max_length=6)
    failureEvidence: PmnOperationFailureEvidenceModel | None
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(max_length=2000)
    algorithmId: Literal["pmn_reference"]
    algorithmVersion: Literal["1.0.0"]
    numericPolicy: Literal["exact_fraction_v1"]

    @model_validator(mode="after")
    def validate_selection_fields(self) -> PmnOperationSnapshotModel:
        if {item.field for item in self.selections} != set(_FIELDS):
            raise ValueError("pmn_selection_fields_invalid")
        return self


class PmnSourceProducerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)
    buildId: str = Field(min_length=1, max_length=200)
    gitCommit: str = Field(min_length=1, max_length=200)


class PmnSourceValuesModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    nominal_rpm: str | None = Field(max_length=512)
    speed_factor: str | None = Field(max_length=512)
    target_cycles: str | None = Field(max_length=512)
    acceleration_duration_s: str | None = Field(max_length=512)
    steady_duration_s: str | None = Field(max_length=512)
    deceleration_duration_s: str | None = Field(max_length=512)


class PmnSourceMethodicalRequirementsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_max_rpm_exact: str = Field(max_length=512)
    cycle_duration_s_exact: str = Field(max_length=512)
    total_duration_s_exact: str = Field(max_length=512)


class PmnSourceExecutionTargetsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_max_rpm: str = Field(max_length=512)
    target_cycles: str = Field(pattern=r"^(?:0|[1-9][0-9]{0,63})$")
    cycle_duration_s: str = Field(max_length=512)
    total_duration_s: str = Field(max_length=512)


class PmnSourceSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    executionId: EntityId
    localImportId: EntityId
    packageId: str = Field(min_length=1, max_length=200)
    runId: str = Field(min_length=1, max_length=200)
    exportRevision: int = Field(ge=1, le=9_007_199_254_740_991)
    outerPackageSha256: Sha256
    sourceSnapshotSha256: Sha256
    producer: PmnSourceProducerModel
    planSelection: Literal["original", "effective"]
    payloadPath: Literal["plan/original.json", "plan/effective.json"]
    payloadSha256: Sha256
    planId: str = Field(min_length=1, max_length=200)
    planRevision: int = Field(ge=1, le=9_007_199_254_740_991)
    sourceValues: PmnSourceValuesModel
    methodicalRequirements: PmnSourceMethodicalRequirementsModel
    executionTargets: PmnSourceExecutionTargetsModel

    @model_validator(mode="after")
    def validate_plan_path(self) -> PmnSourceSnapshotModel:
        expected = "plan/original.json" if self.planSelection == "original" else "plan/effective.json"
        if self.payloadPath != expected:
            raise ValueError("pmn_plan_path_mismatch")
        return self


class PmnSavedFieldSelectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: PmnInputField
    unit: Literal["rpm", "cycle", "1", "s"]
    origin: Literal["source", "manual"]
    value: str = Field(min_length=1, max_length=64)
    rawSourceValue: str | None = Field(max_length=512)
    sourceReference: str = Field(min_length=1, max_length=200)
    basis: str = Field(max_length=2000)
    document: DocumentEvidenceSnapshotModel | None


class PmnSavedFailureEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    applicability: PmnFailureApplicability
    durationToFailureS: str | None = Field(max_length=64)
    basis: str = Field(max_length=2000)
    document: DocumentEvidenceSnapshotModel | None


class PmnInputSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schemaVersion: Literal[1]
    operation: PmnOperationSnapshotModel
    source: PmnSourceSnapshotModel
    fieldSelections: list[PmnSavedFieldSelectionModel] = Field(min_length=6, max_length=6)
    failureEvidence: PmnSavedFailureEvidenceModel | None

    @model_validator(mode="after")
    def validate_field_set(self) -> PmnInputSnapshotModel:
        if {item.field for item in self.fieldSelections} != set(_FIELDS):
            raise ValueError("pmn_saved_fields_invalid")
        return self
