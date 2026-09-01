from __future__ import annotations

from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from impeller_reliability.integration.r130run.models import (
    RunPackageFinding,
    RunPackageSemanticCoverage,
)
from impeller_reliability.persistence.r130sh_sources import (
    ImportedRunDetail,
    ImportedRunSummary,
    SpecimenBinding,
)

ImportJobState = Literal[
    "queued",
    "validating",
    "copying",
    "revalidating",
    "registering",
    "completed",
    "failed",
    "cancelling",
    "cancelled",
]
ImportJobPhase = Literal[
    "queued",
    "source_validation",
    "streaming_copy",
    "staged_validation",
    "database_registration",
    "terminal",
]
ImportJobErrorCode = Literal[
    "cancelled",
    "timeout",
    "source_changed",
    "storage_error",
    "validation_error",
    "diagnostic_confirmation_required",
    "import_integrity_conflict",
    "project_not_open",
    "interrupted",
]


class ImportedRunSummaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    localImportId: str
    packageId: str
    exportRevision: int = Field(ge=1, le=9_007_199_254_740_991)
    outerPackageSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runId: str
    packageKind: Literal["final", "diagnostic_partial"]
    packageSchema: Literal["r130sh.run-package.v1"]
    packageCreatedAtUtc: str
    sourceSnapshotSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    producerName: str
    producerVersion: str
    producerBuildId: str
    producerGitCommit: str
    outerSizeBytes: int = Field(gt=0, le=9_007_199_254_740_991)
    importedAtUtc: str
    validatorVersion: str
    validationContractCommit: str
    structuralVerdict: Literal["passed"]
    semanticVerdict: Literal["passed", "passed_with_warnings"]
    sourceIntegrity: Literal["verified", "missing", "modified", "verification_error"]
    sourceSpecimenId: str
    localSpecimenId: str | None
    bindingRevision: int = Field(ge=1, le=9_007_199_254_740_991)
    mode: Literal["pmn", "rpt", "rbd"]
    technicalStatus: str | None
    terminationReason: str | None
    specimenOutcome: str | None
    runValidity: str | None
    dataCompleteness: str | None
    importedExisting: bool


class ImportedRunPlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    planId: str
    planRevision: int = Field(ge=1, le=9_007_199_254_740_991)
    mode: Literal["pmn", "rpt", "rbd"]
    specimenId: str
    wheelIdentifier: str
    laboratoryCaseReference: str
    customerOrderReference: str
    nominalRpm: str | None
    targetCycles: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    targetMaxRpm: str | None
    lowerRpm: str | None
    upperRpm: str | None
    targetSteadyDurationS: str | None
    totalDurationS: str | None
    lowerPointPolicy: str | None
    roundingPolicy: str | None
    requiredCyclesExact: str | None
    requiredSteadyDurationSExact: str | None
    requiredTotalDurationSExact: str | None
    cycleDurationSExact: str | None
    targetMaxRpmExact: str | None


class ImportedRunEnvironmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: str | None
    temperatureC: str | None
    humidityPct: str | None
    pressureKpa: str | None
    source: str | None
    deviationCount: int = Field(ge=0, le=9_007_199_254_740_991)
    confirmationActor: str | None
    confirmationReason: str | None


class ImportedRunProvenanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    producerName: str | None
    appVersion: str | None
    buildId: str | None
    gitCommit: str | None
    databaseSchemaVersion: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    standName: str | None
    standSerialNumber: str | None
    timeSource: str | None


class ImportedRunProjectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    startedAtUtc: str
    finishedAtUtc: str | None
    resumeAvailable: bool
    partialReasons: list[Annotated[str, Field(min_length=1, max_length=512)]] = Field(
        max_length=64,
    )
    customerFullName: str | None
    customerAddress: str | None
    customerOrderReference: str | None
    wheelFullName: str | None
    wheelIdentifier: str | None
    workingDiameterMm: str | None
    sampleLabel: str | None
    originalPlan: ImportedRunPlanModel
    effectivePlan: ImportedRunPlanModel
    environment: ImportedRunEnvironmentModel
    provenance: ImportedRunProvenanceModel
    measurementCount: int = Field(ge=0, le=9_007_199_254_740_991)
    acceptedMeasurementCount: int = Field(ge=0, le=9_007_199_254_740_991)
    eventCount: int = Field(ge=0, le=9_007_199_254_740_991)
    inspectionCount: int = Field(ge=0, le=9_007_199_254_740_991)
    attachmentCount: int = Field(ge=0, le=9_007_199_254_740_991)
    amendmentCount: int = Field(ge=0, le=9_007_199_254_740_991)
    creditingPolicy: str | None
    acceptedElapsedS: str | None


class ImportedRunInventoryItemModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str
    mediaType: str
    sizeBytes: int = Field(ge=0, le=9_007_199_254_740_991)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rowCount: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    semanticCoverage: Literal["covered", "structural_only"]


class EnrichmentResolutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    resolutionId: str
    sourcePayloadPath: str
    sourceField: str
    targetEntityType: Literal["customer_profile", "wheel_model", "specimen"]
    targetEntityId: str
    targetField: str
    decision: Literal["use_source", "use_analyst", "copied_to_analyst"]
    actor: str
    occurredAtUtc: str
    reason: str


class ImportedRunDetailModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: ImportedRunSummaryModel
    projection: ImportedRunProjectionModel
    inventory: list[ImportedRunInventoryItemModel] = Field(max_length=4096)
    semanticCoverage: list[RunPackageSemanticCoverage] = Field(max_length=32)
    validationFindings: list[RunPackageFinding] = Field(max_length=200)
    enrichmentResolutions: list[EnrichmentResolutionModel] = Field(max_length=32)


class SpecimenBindingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sourceSpecimenId: str
    localSpecimenId: str | None
    recordRevision: int = Field(ge=1, le=9_007_199_254_740_991)
    updatedByActor: str | None
    reason: str
    createdAtUtc: str
    updatedAtUtc: str


class RunPackageImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    disposition: Literal["created", "existing"]
    importedRun: ImportedRunSummaryModel


class RunPackageImportJobError(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: ImportJobErrorCode
    message: str = Field(min_length=1, max_length=512)
    retryable: bool


class RunPackageImportJobSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jobId: str
    state: ImportJobState
    phase: ImportJobPhase
    completedBytes: int = Field(ge=0, le=9_007_199_254_740_991)
    totalBytes: int = Field(ge=0, le=9_007_199_254_740_991)
    completedEntries: int = Field(ge=0, le=9_007_199_254_740_991)
    totalEntries: int = Field(ge=0, le=9_007_199_254_740_991)
    startedAtUtc: str | None
    finishedAtUtc: str | None
    result: RunPackageImportResult | None
    typedError: RunPackageImportJobError | None

    @model_validator(mode="after")
    def validate_state_payload(self) -> Self:
        if self.completedBytes > self.totalBytes > 0:
            raise ValueError("completed_bytes_exceed_total")
        if self.completedEntries > self.totalEntries > 0:
            raise ValueError("completed_entries_exceed_total")
        if self.state == "completed":
            if self.result is None or self.typedError is not None or self.finishedAtUtc is None:
                raise ValueError("completed_import_payload_invalid")
        elif self.state in {"failed", "cancelled"}:
            if self.result is not None or self.typedError is None or self.finishedAtUtc is None:
                raise ValueError("failed_import_payload_invalid")
        elif self.result is not None or self.typedError is not None or self.finishedAtUtc is not None:
            raise ValueError("active_import_payload_invalid")
        return self


class RunPackageImportDiscardResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jobId: str
    discarded: Literal[True] = True


class ImportedRunVerifyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    localImportId: str
    sourceIntegrity: Literal["verified", "missing", "modified", "verification_error"]


def imported_run_summary_model(value: ImportedRunSummary) -> ImportedRunSummaryModel:
    return ImportedRunSummaryModel(
        localImportId=value.local_import_id,
        packageId=value.package_id,
        exportRevision=value.export_revision,
        outerPackageSha256=value.outer_package_sha256,
        runId=value.run_id,
        packageKind=cast(Literal["final", "diagnostic_partial"], value.package_kind),
        packageSchema="r130sh.run-package.v1",
        packageCreatedAtUtc=value.package_created_at_utc,
        sourceSnapshotSha256=value.source_snapshot_sha256,
        producerName=value.producer_name,
        producerVersion=value.producer_version,
        producerBuildId=value.producer_build_id,
        producerGitCommit=value.producer_git_commit,
        outerSizeBytes=value.outer_size_bytes,
        importedAtUtc=value.imported_at_utc,
        validatorVersion=value.validator_version,
        validationContractCommit=value.validation_contract_commit,
        structuralVerdict="passed",
        semanticVerdict=cast(Literal["passed", "passed_with_warnings"], value.semantic_verdict),
        sourceIntegrity=value.source_integrity,
        sourceSpecimenId=value.source_specimen_id,
        localSpecimenId=value.local_specimen_id,
        bindingRevision=value.binding_revision,
        mode=cast(Literal["pmn", "rpt", "rbd"], value.mode),
        technicalStatus=value.technical_status,
        terminationReason=value.termination_reason,
        specimenOutcome=value.specimen_outcome,
        runValidity=value.run_validity,
        dataCompleteness=value.data_completeness,
        importedExisting=value.imported_existing,
    )


def imported_run_detail_model(value: ImportedRunDetail) -> ImportedRunDetailModel:
    projection = value.projection
    original = _plan_model(_mapping(projection["original_plan_summary"]), "original")
    effective = _plan_model(_mapping(projection["effective_plan_summary"]), "effective")
    environment = _environment_model(_mapping(projection["environment_summary"]))
    provenance = _provenance_model(_mapping(projection["provenance_summary"]))
    return ImportedRunDetailModel(
        summary=imported_run_summary_model(value.summary),
        projection=ImportedRunProjectionModel(
            startedAtUtc=_string(projection["started_at_utc"]),
            finishedAtUtc=_optional_string(projection["finished_at_utc"]),
            resumeAvailable=_boolean(projection["resume_available"]),
            partialReasons=[_string(item) for item in _list(projection["partial_reasons"])],
            customerFullName=_optional_string(projection["customer_full_name"]),
            customerAddress=_optional_string(projection["customer_address"]),
            customerOrderReference=_optional_string(projection["customer_order_reference"]),
            wheelFullName=_optional_string(projection["wheel_full_name"]),
            wheelIdentifier=_optional_string(projection["wheel_identifier"]),
            workingDiameterMm=_optional_string(projection["working_diameter_mm"]),
            sampleLabel=_optional_string(projection["sample_label"]),
            originalPlan=original,
            effectivePlan=effective,
            environment=environment,
            provenance=provenance,
            measurementCount=_integer(projection["measurement_count"]),
            acceptedMeasurementCount=_integer(projection["accepted_measurement_count"]),
            eventCount=_integer(projection["event_count"]),
            inspectionCount=_integer(projection["inspection_count"]),
            attachmentCount=_integer(projection["attachment_count"]),
            amendmentCount=_integer(projection["amendment_count"]),
            creditingPolicy=_optional_string(projection["crediting_policy"]),
            acceptedElapsedS=_optional_string(projection["accepted_elapsed_s"]),
        ),
        inventory=[ImportedRunInventoryItemModel.model_validate(item) for item in value.inventory],
        semanticCoverage=[RunPackageSemanticCoverage.model_validate(item) for item in value.semantic_coverage],
        validationFindings=[RunPackageFinding.model_validate(item) for item in value.validation_findings],
        enrichmentResolutions=[EnrichmentResolutionModel.model_validate(item) for item in value.enrichment_resolutions],
    )


def specimen_binding_model(value: SpecimenBinding) -> SpecimenBindingModel:
    return SpecimenBindingModel(
        sourceSpecimenId=value.source_specimen_id,
        localSpecimenId=value.local_specimen_id,
        recordRevision=value.record_revision,
        updatedByActor=value.updated_by_actor,
        reason=value.reason,
        createdAtUtc=value.created_at_utc,
        updatedAtUtc=value.updated_at_utc,
    )


def _plan_model(value: dict[str, object], _label: str) -> ImportedRunPlanModel:
    targets = _mapping(value.get("execution_targets"))
    exact = _mapping(value.get("methodical_requirements"))
    source_values = _mapping(value.get("source_values"))
    return ImportedRunPlanModel(
        planId=_string(value.get("plan_id")),
        planRevision=_integer(value.get("plan_revision")),
        mode=cast(Literal["pmn", "rpt", "rbd"], _string(value.get("mode"))),
        specimenId=_string(value.get("specimen_id")),
        wheelIdentifier=_string(value.get("wheel_identifier")),
        laboratoryCaseReference=_string(value.get("laboratory_case_reference")),
        customerOrderReference=_string(value.get("customer_order_reference")),
        nominalRpm=_optional_scalar_string(source_values.get("nominal_rpm")),
        targetCycles=_optional_integer(targets.get("target_cycles")),
        targetMaxRpm=_optional_scalar_string(targets.get("target_max_rpm")),
        lowerRpm=_optional_scalar_string(targets.get("lower_rpm")),
        upperRpm=_optional_scalar_string(targets.get("upper_rpm")),
        targetSteadyDurationS=_optional_scalar_string(targets.get("target_steady_duration_s")),
        totalDurationS=_optional_scalar_string(targets.get("total_duration_s")),
        lowerPointPolicy=_optional_string(targets.get("lower_point_policy")),
        roundingPolicy=_optional_string(targets.get("rounding_policy")),
        requiredCyclesExact=_optional_string(exact.get("required_cycles_exact")),
        requiredSteadyDurationSExact=_optional_string(exact.get("required_steady_duration_s_exact")),
        requiredTotalDurationSExact=_optional_string(exact.get("required_total_duration_s_exact")),
        cycleDurationSExact=_optional_string(exact.get("cycle_duration_s_exact")),
        targetMaxRpmExact=_optional_string(exact.get("target_max_rpm_exact")),
    )


def _environment_model(value: dict[str, object]) -> ImportedRunEnvironmentModel:
    values = _mapping(value.get("values"))
    deviations = _list(value.get("deviations"))
    confirmation = _mapping(value.get("confirmation"))
    actor = _mapping(confirmation.get("actor"))
    return ImportedRunEnvironmentModel(
        status=_optional_string(value.get("status")),
        temperatureC=_optional_scalar_string(values.get("temperature_c")),
        humidityPct=_optional_scalar_string(values.get("humidity_pct")),
        pressureKpa=_optional_scalar_string(values.get("pressure_kpa")),
        source=_optional_string(values.get("source")),
        deviationCount=len(deviations),
        confirmationActor=_optional_string(actor.get("full_name")),
        confirmationReason=_optional_string(confirmation.get("reason")),
    )


def _provenance_model(value: dict[str, object]) -> ImportedRunProvenanceModel:
    return ImportedRunProvenanceModel(
        producerName=_optional_string(value.get("producer_name")),
        appVersion=_optional_string(value.get("app_version")),
        buildId=_availability_value(value.get("build_id")),
        gitCommit=_availability_value(value.get("git_commit")),
        databaseSchemaVersion=_optional_integer(value.get("database_schema_version")),
        standName=_availability_value(value.get("stand_name")),
        standSerialNumber=_availability_value(value.get("stand_serial_number")),
        timeSource=_optional_string(value.get("time_source")),
    )


def _availability_value(value: object) -> str | None:
    source = _mapping(value)
    return _optional_string(source.get("value")) if source.get("availability") == "available" else None


def _mapping(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("mapping_required")
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("list_required")
    return cast(list[object], value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("string_required")
    return value


def _optional_string(value: object) -> str | None:
    return None if value is None else _string(value)


def _optional_scalar_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("scalar_required")
    return str(value)


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("integer_required")
    return value


def _optional_integer(value: object) -> int | None:
    return None if value is None else _integer(value)


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("boolean_required")
    return value


__all__ = [
    "EnrichmentResolutionModel",
    "ImportedRunDetailModel",
    "ImportedRunEnvironmentModel",
    "ImportedRunInventoryItemModel",
    "ImportedRunPlanModel",
    "ImportedRunProjectionModel",
    "ImportedRunProvenanceModel",
    "ImportedRunSummaryModel",
    "ImportedRunVerifyResult",
    "RunPackageImportDiscardResult",
    "RunPackageImportJobError",
    "RunPackageImportJobSnapshot",
    "RunPackageImportResult",
    "SpecimenBindingModel",
    "imported_run_detail_model",
    "imported_run_summary_model",
    "specimen_binding_model",
]
