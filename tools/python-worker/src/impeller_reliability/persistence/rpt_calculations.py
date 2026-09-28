from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import asdict, dataclass
import hashlib
import json
import sqlite3
from typing import Final, Literal, cast
from uuid import UUID

from pydantic import ValidationError

from impeller_reliability.calculations.exact import ExactRationalValue
from impeller_reliability.calculations.exact_result_snapshot import ExactRationalResultModel
from impeller_reliability.calculations.rpt import (
    ALGORITHM_ID,
    ALGORITHM_VERSION,
    NUMERIC_POLICY,
    RptCalculationError,
    RptFailureInput,
    RptReferenceInput,
    calculate_rpt_reference,
    compare_rpt_lower_point,
    failure_reason_for_applicability,
    validate_rpt_reference_input,
)
from impeller_reliability.calculations.rpt_input_snapshot import (
    RptFailureApplicability,
    RptInputField,
    RptInputSnapshotModel,
    RptOperationSnapshotModel,
)
from impeller_reliability.calculations.rpt_result_snapshot import RptReferenceResultModel
from impeller_reliability.integration.r130run.m9a import canonical_json
from impeller_reliability.persistence.audit import audit_now, insert_audit
from impeller_reliability.persistence.calculation_write_lock import begin_calculation_write
from impeller_reliability.persistence.case_documents import case_document_for_new_decision
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.r130sh_sources import RptPlanSourceSnapshot, plan_source_field_reference
from impeller_reliability.persistence.reliability_domain import bounded_text
from impeller_reliability.worker.deadline import RequestDeadline

RptInputOrigin = Literal["source", "manual"]
WriteDisposition = Literal["created", "existing"]

_FIELD_UNITS: Final[dict[RptInputField, str]] = {
    "nominal_rpm": "rpm",
    "design_cycles": "cycle",
    "reserve_factor": "1",
    "acceleration_duration_s": "s",
    "steady_duration_s": "s",
    "deceleration_duration_s": "s",
}
_FIELD_ORDER: Final[tuple[RptInputField, ...]] = tuple(_FIELD_UNITS)
_SNAPSHOT_MAX_BYTES: Final = 65_536


@dataclass(frozen=True, slots=True)
class RptEvidenceReference:
    document_id: str
    document_record_revision: int
    document_locator: str


@dataclass(frozen=True, slots=True)
class RptFieldSelection:
    field: RptInputField
    origin: RptInputOrigin
    manual_value: str | None = None
    basis: str = ""
    evidence: RptEvidenceReference | None = None


@dataclass(frozen=True, slots=True)
class RptFailureEvidence:
    applicability: RptFailureApplicability
    duration_to_failure_s: str | None
    basis: str = ""
    evidence: RptEvidenceReference | None = None


@dataclass(frozen=True, slots=True)
class RptAnalysisInputSnapshot:
    analysis_input_snapshot_id: str
    execution_id: str
    input_snapshot: dict[str, object]
    content_sha256: str
    operation_sha256: str
    actor: str
    decision_reason: str
    created_at_utc: str


@dataclass(frozen=True, slots=True)
class RptCalculationSnapshot:
    calculation_snapshot_id: str
    analysis_input_snapshot_id: str
    execution_id: str
    algorithm_id: Literal["rpt_reference"]
    algorithm_version: Literal["1.0.0"]
    numeric_policy: Literal["exact_fraction_v1"]
    result_snapshot: dict[str, object]
    input_content_sha256: str
    content_sha256: str
    operation_sha256: str
    created_at_utc: str


@dataclass(frozen=True, slots=True)
class RptCalculationDetail:
    input_snapshot: RptAnalysisInputSnapshot
    calculation_snapshot: RptCalculationSnapshot


@dataclass(frozen=True, slots=True)
class RptCalculationWriteResult:
    disposition: WriteDisposition
    detail: RptCalculationDetail


@dataclass(frozen=True, slots=True)
class RptCalculationSummary:
    calculation_snapshot_id: str
    analysis_input_snapshot_id: str
    execution_id: str
    wheel_model_id: str
    required_cycles_exact: str
    failure_status: Literal["calculated", "not_applicable"]
    created_at_utc: str


@dataclass(frozen=True, slots=True)
class RptCalculationPage:
    items: tuple[RptCalculationSummary, ...]
    next_cursor: str | None


def _operation_payload(
    *,
    analysis_input_snapshot_id: str,
    calculation_snapshot_id: str,
    execution_id: str,
    plan_selection: str,
    selections: tuple[RptFieldSelection, ...],
    failure: RptFailureEvidence | None,
    actor: str,
    reason: str,
) -> dict[str, object]:
    if plan_selection not in {"original", "effective"}:
        raise ProjectOperationError("validation_error", "Редакция плана РПТ не поддерживается.")
    return {
        "schemaVersion": 1,
        "analysisInputSnapshotId": _uuid4(analysis_input_snapshot_id),
        "calculationSnapshotId": _uuid4(calculation_snapshot_id),
        "executionId": _uuid4(execution_id),
        "planSelection": plan_selection,
        "selections": [asdict(item) for item in selections],
        "failureEvidence": None if failure is None else asdict(failure),
        "actor": bounded_text(actor, 200, "Автор расчёта"),
        "reason": bounded_text(reason, 2000, "Основание расчёта", multiline=True),
        "algorithmId": ALGORITHM_ID,
        "algorithmVersion": ALGORITHM_VERSION,
        "numericPolicy": NUMERIC_POLICY,
    }


def _source_payload(source: RptPlanSourceSnapshot) -> dict[str, object]:
    return {
        "executionId": source.execution_id,
        "localImportId": source.local_import_id,
        "packageId": source.package_id,
        "runId": source.run_id,
        "exportRevision": source.export_revision,
        "outerPackageSha256": source.outer_package_sha256,
        "sourceSnapshotSha256": source.source_snapshot_sha256,
        "producer": {
            "name": source.producer_name,
            "version": source.producer_version,
            "buildId": source.producer_build_id,
            "gitCommit": source.producer_git_commit,
        },
        "planSelection": source.selection,
        "payloadPath": source.payload_path,
        "payloadSha256": source.payload_sha256,
        "planId": source.plan_id,
        "planRevision": source.plan_revision,
        "sourceValues": asdict(source.source_values),
        "methodicalRequirements": asdict(source.methodical_requirements),
        "executionTargets": asdict(source.execution_targets),
    }


def _sha256_json(value: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _bounded_json(value: dict[str, object]) -> str:
    encoded = canonical_json(value)
    if len(encoded.encode("utf-8")) > _SNAPSHOT_MAX_BYTES:
        raise ProjectOperationError("validation_error", "Снимок РПТ превышает предел UTF-8 payload.")
    return encoded


def _uuid4(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ProjectOperationError("validation_error", "Local ID должен быть canonical UUID v4.") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ProjectOperationError("validation_error", "Local ID должен быть canonical UUID v4.")
    return value


def _check_deadline(deadline: RequestDeadline | None, stage: str) -> None:
    if deadline is not None:
        deadline.check(stage)


def _corrupt() -> ProjectOperationError:
    return ProjectOperationError("corrupt_project", "Снимки расчёта РПТ повреждены.")


def _row_integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _corrupt()
    return value


def _exact_value_from_model(value: ExactRationalResultModel) -> ExactRationalValue:
    return ExactRationalValue(value.numerator, value.denominator, value.decimal, value.decimal_preview)


def _load_snapshot(value: object) -> dict[str, object]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > _SNAPSHOT_MAX_BYTES:
        raise _corrupt()
    try:
        decoded: object = json.loads(value)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise _corrupt() from error
    if not isinstance(decoded, dict):
        raise _corrupt()
    payload = cast(dict[str, object], decoded)
    if canonical_json(payload) != value:
        raise _corrupt()
    return payload


def _validate_saved_field_links(payload: RptInputSnapshotModel) -> None:
    operation = payload.operation
    source = payload.source
    if [item.field for item in payload.fieldSelections] != list(_FIELD_ORDER):
        raise _corrupt()
    choices = {item.field: item for item in operation.selections}
    for selected in payload.fieldSelections:
        choice = choices[selected.field]
        raw = getattr(source.sourceValues, selected.field)
        if (
            selected.rawSourceValue != raw
            or selected.sourceReference != plan_source_field_reference(source.planSelection, selected.field)
            or selected.unit != _FIELD_UNITS[selected.field]
            or selected.origin != choice.origin
        ):
            raise _corrupt()
        if choice.origin == "source":
            if raw is None or selected.value != raw or selected.basis or selected.document is not None or choice.manual_value is not None or choice.basis or choice.evidence is not None:
                raise _corrupt()
        elif selected.value != choice.manual_value or selected.basis != choice.basis.replace("\r\n", "\n").replace("\r", "\n").strip() or not selected.basis:
            raise _corrupt()
        _validate_saved_document_link(choice.evidence, selected.document)


def _validate_saved_failure_link(payload: RptInputSnapshotModel, result: RptReferenceResultModel) -> None:
    operation_failure = payload.operation.failureEvidence
    saved = payload.failureEvidence
    if operation_failure is None:
        if saved is not None or result.failure_result.status != "not_applicable" or result.failure_result.reason_code != "failure_duration_unavailable":
            raise _corrupt()
        return
    if saved is None or saved.applicability != operation_failure.applicability or saved.durationToFailureS != operation_failure.duration_to_failure_s:
        raise _corrupt()
    if saved.basis != operation_failure.basis.replace("\r\n", "\n").replace("\r", "\n").strip():
        raise _corrupt()
    _validate_saved_document_link(operation_failure.evidence, saved.document)
    if operation_failure.applicability == "exact_supported":
        if saved.document is None or saved.durationToFailureS is None or not saved.basis or result.failure_result.status != "calculated":
            raise _corrupt()
    elif result.failure_result.status != "not_applicable" or result.failure_result.reason_code != failure_reason_for_applicability(operation_failure.applicability):
        raise _corrupt()


def _validate_saved_document_link(reference: object, saved: object) -> None:
    from impeller_reliability.calculations.document_snapshot import DocumentEvidenceSnapshotModel
    from impeller_reliability.calculations.rpt_input_snapshot import RptOperationEvidenceReferenceModel

    if reference is None:
        if saved is not None:
            raise _corrupt()
        return
    if not isinstance(reference, RptOperationEvidenceReferenceModel) or not isinstance(saved, DocumentEvidenceSnapshotModel):
        raise _corrupt()
    if saved.documentId != reference.document_id or saved.recordRevision != reference.document_record_revision or saved.locator != reference.document_locator.strip():
        raise _corrupt()


def _failure_status(value: str) -> Literal["calculated", "not_applicable"]:
    if value == "calculated":
        return "calculated"
    if value == "not_applicable":
        return "not_applicable"
    raise _corrupt()


def _encode_cursor(wheel_model_id: str, ceiling: int, after: int) -> str:
    data = {"v": 1, "kind": "rptCalculation", "wheelModelId": wheel_model_id, "ceiling": ceiling, "after": after}
    return urlsafe_b64encode(canonical_json(data).encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, wheel_model_id: str) -> tuple[int, int]:
    if not cursor or len(cursor) > 512 or not cursor.isascii():
        raise ProjectOperationError("validation_error", "Cursor страницы РПТ имеет неверный формат.")
    try:
        decoded: object = json.loads(urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise ProjectOperationError("validation_error", "Cursor страницы РПТ имеет неверный формат.") from error
    if not isinstance(decoded, dict):
        raise ProjectOperationError("validation_error", "Cursor страницы РПТ имеет неверную структуру.")
    data = cast(dict[str, object], decoded)
    ceiling = data.get("ceiling")
    after = data.get("after")
    if (
        set(data) != {"v", "kind", "wheelModelId", "ceiling", "after"}
        or data["v"] != 1
        or data["kind"] != "rptCalculation"
        or data["wheelModelId"] != wheel_model_id
        or not isinstance(ceiling, int)
        or isinstance(ceiling, bool)
        or not isinstance(after, int)
        or isinstance(after, bool)
        or ceiling < 0
        or not 0 < after <= ceiling + 1
    ):
        raise ProjectOperationError("validation_error", "Cursor не относится к выбранному списку РПТ.")
    return ceiling, after


class RptCalculationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def operation_sha256(
        self,
        *,
        analysis_input_snapshot_id: str,
        calculation_snapshot_id: str,
        execution_id: str,
        plan_selection: str,
        selections: tuple[RptFieldSelection, ...],
        failure: RptFailureEvidence | None,
        actor: str,
        reason: str,
    ) -> str:
        return _sha256_json(
            _operation_payload(
                analysis_input_snapshot_id=analysis_input_snapshot_id,
                calculation_snapshot_id=calculation_snapshot_id,
                execution_id=execution_id,
                plan_selection=plan_selection,
                selections=selections,
                failure=failure,
                actor=actor,
                reason=reason,
            )
        )

    def resolve_idempotent_retry(
        self,
        analysis_input_snapshot_id: str,
        calculation_snapshot_id: str,
        operation_sha256: str,
        deadline: RequestDeadline | None,
    ) -> RptCalculationWriteResult | None:
        analysis_input_snapshot_id = _uuid4(analysis_input_snapshot_id)
        calculation_snapshot_id = _uuid4(calculation_snapshot_id)
        input_row = self._connection.execute(
            "SELECT operation_sha256 FROM rpt_analysis_input_snapshots WHERE analysis_input_snapshot_id=?",
            (analysis_input_snapshot_id,),
        ).fetchone()
        calculation_row = self._connection.execute(
            "SELECT analysis_input_snapshot_id, operation_sha256 FROM rpt_calculation_snapshots WHERE calculation_snapshot_id=?",
            (calculation_snapshot_id,),
        ).fetchone()
        _check_deadline(deadline, "rpt_retry_lookup")
        if input_row is None and calculation_row is None:
            return None
        if (
            input_row is None
            or calculation_row is None
            or str(calculation_row[0]) != analysis_input_snapshot_id
            or str(input_row[0]) != operation_sha256
            or str(calculation_row[1]) != operation_sha256
        ):
            raise ProjectOperationError("revision_conflict", "Идентификаторы расчёта РПТ уже использованы с другими входами.")
        detail = self.get_detail(calculation_snapshot_id, deadline)
        if detail.input_snapshot.analysis_input_snapshot_id != analysis_input_snapshot_id:
            raise ProjectOperationError("revision_conflict", "Идентификаторы относятся к другой паре снимков РПТ.")
        return RptCalculationWriteResult("existing", detail)

    def _execution_tuple(
        self,
        source: RptPlanSourceSnapshot,
        deadline: RequestDeadline | None,
    ) -> tuple[str, str, str]:
        row = self._connection.execute(
            """
            SELECT e.wheel_model_id, e.local_specimen_id, e.source_specimen_id
            FROM reliability_test_executions e
            JOIN r130sh_sources s ON s.local_import_id=e.local_import_id
            WHERE e.execution_id=? AND e.local_import_id=? AND e.method='rpt'
              AND e.source_outer_package_sha256=s.outer_package_sha256
              AND s.run_id=? AND s.export_revision=? AND s.outer_package_sha256=?
              AND s.source_snapshot_sha256=?
            """,
            (
                source.execution_id,
                source.local_import_id,
                source.run_id,
                source.export_revision,
                source.outer_package_sha256,
                source.source_snapshot_sha256,
            ),
        ).fetchone()
        _check_deadline(deadline, "rpt_execution_source_tuple")
        if row is None:
            raise ProjectOperationError("revision_conflict", "Исполнение или revision источника РПТ изменились.")
        return _uuid4(str(row[0])), _uuid4(str(row[1])), str(row[2])

    def _document_snapshot(
        self,
        reference: RptEvidenceReference | None,
        execution: tuple[str, str, str],
        deadline: RequestDeadline | None,
    ) -> dict[str, object] | None:
        if reference is None:
            return None
        document_id = _uuid4(reference.document_id)
        if reference.document_record_revision < 1:
            raise ProjectOperationError("validation_error", "Нужна exact revision документа РПТ.")
        document = case_document_for_new_decision(
            self._connection,
            document_id,
            execution[0],
            execution[1],
            deadline,
        )
        if document is None or document.record_revision != reference.document_record_revision:
            raise ProjectOperationError("validation_error", "Exact revision документа недоступна или неприменима.")
        locator = bounded_text(reference.document_locator, 1000, "Локатор документа")
        if not locator:
            raise ProjectOperationError("validation_error", "Нужен локатор точного места в документе.")
        return {
            "documentId": document_id,
            "recordRevision": document.record_revision,
            "documentKind": document.document_kind,
            "title": document.title,
            "designation": document.designation,
            "revisionLabel": document.revision_label,
            "fileSha256": document.file_sha256,
            "locator": locator,
        }

    def _resolve_fields(
        self,
        source: RptPlanSourceSnapshot,
        selections: tuple[RptFieldSelection, ...],
        execution: tuple[str, str, str],
        deadline: RequestDeadline | None,
    ) -> tuple[dict[RptInputField, str], list[dict[str, object]]]:
        if len(selections) != len(_FIELD_ORDER) or {item.field for item in selections} != set(_FIELD_ORDER):
            raise ProjectOperationError("validation_error", "Нужно явно выбрать происхождение шести входов РПТ.")
        source_values = asdict(source.source_values)
        chosen: dict[RptInputField, str] = {}
        snapshots: list[dict[str, object]] = []
        for field in _FIELD_ORDER:
            choice = next(item for item in selections if item.field == field)
            raw_source = source_values[field]
            if raw_source is not None and not isinstance(raw_source, str):
                raise _corrupt()
            if choice.origin == "source":
                if raw_source is None:
                    raise ProjectOperationError("validation_error", f"{field}: значение отсутствует в выбранном плане.")
                if choice.manual_value is not None or choice.basis or choice.evidence is not None:
                    raise ProjectOperationError("validation_error", f"{field}: ручные сведения недопустимы при выборе source.")
                value = raw_source
                basis = ""
                document = None
            elif choice.origin == "manual":
                if choice.manual_value is None:
                    raise ProjectOperationError("validation_error", f"{field}: ручное значение не задано.")
                value = choice.manual_value
                basis = bounded_text(choice.basis, 2000, f"{field}: основание", multiline=True)
                if not basis:
                    raise ProjectOperationError("validation_error", f"{field}: требуется основание ручного дополнения.")
                document = self._document_snapshot(choice.evidence, execution, deadline)
            else:
                raise ProjectOperationError("validation_error", f"{field}: происхождение не поддерживается.")
            chosen[field] = value
            snapshots.append(
                {
                    "field": field,
                    "unit": _FIELD_UNITS[field],
                    "origin": choice.origin,
                    "value": value,
                    "rawSourceValue": raw_source,
                    "sourceReference": plan_source_field_reference(source.selection, field),
                    "basis": basis,
                    "document": document,
                }
            )
        return chosen, snapshots

    def _resolve_failure(
        self,
        failure: RptFailureEvidence | None,
        execution: tuple[str, str, str],
        deadline: RequestDeadline | None,
    ) -> tuple[dict[str, object] | None, RptFailureInput | None]:
        if failure is None:
            return None, None
        basis = bounded_text(failure.basis, 2000, "Основание применимости таблицы 4", multiline=True)
        if failure.applicability == "exact_supported" and (failure.duration_to_failure_s is None or not basis or failure.evidence is None):
            raise ProjectOperationError(
                "validation_error",
                "Для таблицы 4 нужны точное T_ОТК, основание и документ с началом отсчёта.",
            )
        document = self._document_snapshot(failure.evidence, execution, deadline)
        return (
            {
                "applicability": failure.applicability,
                "durationToFailureS": failure.duration_to_failure_s,
                "basis": basis,
                "document": document,
            },
            RptFailureInput(failure.applicability, failure.duration_to_failure_s),
        )

    def create(
        self,
        *,
        analysis_input_snapshot_id: str,
        calculation_snapshot_id: str,
        source: RptPlanSourceSnapshot,
        selections: tuple[RptFieldSelection, ...],
        failure: RptFailureEvidence | None,
        actor: str,
        reason: str,
        operation_sha256: str,
        deadline: RequestDeadline | None,
    ) -> RptCalculationWriteResult:
        analysis_input_snapshot_id = _uuid4(analysis_input_snapshot_id)
        calculation_snapshot_id = _uuid4(calculation_snapshot_id)
        actor = bounded_text(actor, 200, "Автор расчёта")
        reason = bounded_text(reason, 2000, "Основание расчёта", multiline=True)
        operation_payload = _operation_payload(
            analysis_input_snapshot_id=analysis_input_snapshot_id,
            calculation_snapshot_id=calculation_snapshot_id,
            execution_id=source.execution_id,
            plan_selection=source.selection,
            selections=selections,
            failure=failure,
            actor=actor,
            reason=reason,
        )
        if _sha256_json(operation_payload) != operation_sha256:
            raise ProjectOperationError("validation_error", "Operation hash расчёта РПТ не соответствует команде.")
        try:
            RptOperationSnapshotModel.model_validate_json(canonical_json(operation_payload))
        except ValidationError as error:
            raise ProjectOperationError("validation_error", "Команда расчёта РПТ не прошла строгую проверку.") from error
        execution = self._execution_tuple(source, deadline)
        chosen, field_snapshots = self._resolve_fields(source, selections, execution, deadline)
        failure_payload, failure_input = self._resolve_failure(failure, execution, deadline)
        try:
            result = calculate_rpt_reference(
                RptReferenceInput(
                    nominal_rpm=chosen["nominal_rpm"],
                    design_cycles=chosen["design_cycles"],
                    reserve_factor=chosen["reserve_factor"],
                    acceleration_duration_s=chosen["acceleration_duration_s"],
                    steady_duration_s=chosen["steady_duration_s"],
                    deceleration_duration_s=chosen["deceleration_duration_s"],
                    failure=failure_input,
                )
            )
        except RptCalculationError as error:
            raise ProjectOperationError("validation_error", str(error), details={"reasonCode": error.reason_code}) from error
        input_payload: dict[str, object] = {
            "schemaVersion": 1,
            "operation": operation_payload,
            "source": _source_payload(source),
            "fieldSelections": field_snapshots,
            "failureEvidence": failure_payload,
        }
        result_payload = cast(dict[str, object], asdict(result))
        result_payload["lower_point_comparison"] = asdict(
            compare_rpt_lower_point(
                result.minimum_rpm,
                source.source_values.lower_point_policy,
                source.execution_targets.lower_point_policy,
                source.source_values.explicit_lower_rpm,
                source.execution_targets.lower_rpm,
            )
        )
        input_json = _bounded_json(input_payload)
        result_json = _bounded_json(result_payload)
        try:
            RptInputSnapshotModel.model_validate_json(input_json)
            RptReferenceResultModel.model_validate_json(result_json)
        except ValidationError as error:
            raise ProjectOperationError("validation_error", "Снимок расчёта РПТ не прошёл строгую проверку.") from error
        required_cycles = result.required_cycles_exact.decimal
        if required_cycles is None:
            raise ProjectOperationError("domain_error", "Требуемое число циклов РПТ должно иметь конечную десятичную запись.")
        now = audit_now(self._connection)
        input_content_sha256 = _sha256_json(
            {
                "analysisInputSnapshotId": analysis_input_snapshot_id,
                "executionId": source.execution_id,
                "input": input_payload,
                "actor": actor,
                "reason": reason,
                "createdAtUtc": now,
            }
        )
        calculation_content_sha256 = _sha256_json(
            {
                "calculationSnapshotId": calculation_snapshot_id,
                "analysisInputSnapshotId": analysis_input_snapshot_id,
                "inputContentSha256": input_content_sha256,
                "result": result_payload,
                "createdAtUtc": now,
            }
        )
        begin_calculation_write(self._connection, deadline, "rpt_calculation_write_lock")
        try:
            existing = self.resolve_idempotent_retry(analysis_input_snapshot_id, calculation_snapshot_id, operation_sha256, deadline)
            if existing is not None:
                self._connection.rollback()
                return existing
            self._execution_tuple(source, deadline)
            self._connection.execute(
                """
                INSERT INTO rpt_analysis_input_snapshots (
                    analysis_input_snapshot_id, execution_id, local_import_id,
                    wheel_model_id, local_specimen_id, source_specimen_id, source_run_id,
                    export_revision, plan_selection, plan_id, plan_revision,
                    plan_payload_path, plan_payload_sha256, source_outer_package_sha256,
                    source_snapshot_sha256, operation_sha256, input_snapshot_json,
                    content_sha256, actor, decision_reason, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_input_snapshot_id,
                    source.execution_id,
                    source.local_import_id,
                    execution[0],
                    execution[1],
                    execution[2],
                    source.run_id,
                    source.export_revision,
                    source.selection,
                    source.plan_id,
                    source.plan_revision,
                    source.payload_path,
                    source.payload_sha256,
                    source.outer_package_sha256,
                    source.source_snapshot_sha256,
                    operation_sha256,
                    input_json,
                    input_content_sha256,
                    actor,
                    reason,
                    now,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO rpt_calculation_snapshots (
                    calculation_snapshot_id, analysis_input_snapshot_id, execution_id,
                    wheel_model_id, algorithm_id, algorithm_version, numeric_policy,
                    required_cycles_exact, failure_status, result_snapshot_json,
                    input_content_sha256, operation_sha256, content_sha256, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    calculation_snapshot_id,
                    analysis_input_snapshot_id,
                    source.execution_id,
                    execution[0],
                    ALGORITHM_ID,
                    ALGORITHM_VERSION,
                    NUMERIC_POLICY,
                    required_cycles,
                    result.failure_result.status,
                    result_json,
                    input_content_sha256,
                    operation_sha256,
                    calculation_content_sha256,
                    now,
                ),
            )
            insert_audit(
                self._connection,
                event_type="rpt_calculation.created",
                actor_kind="user",
                occurred_at_utc=now,
                payload={
                    "analysisInputSnapshotId": analysis_input_snapshot_id,
                    "calculationSnapshotId": calculation_snapshot_id,
                    "executionId": source.execution_id,
                    "localImportId": source.local_import_id,
                    "wheelModelId": execution[0],
                    "planSelection": source.selection,
                    "inputContentSha256": input_content_sha256,
                    "calculationContentSha256": calculation_content_sha256,
                    "operationSha256": operation_sha256,
                },
            )
            _check_deadline(deadline, "rpt_calculation_commit")
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise ProjectOperationError("revision_conflict", "Идентификаторы снимков РПТ уже используются.") from error
        except Exception:
            self._connection.rollback()
            raise
        return RptCalculationWriteResult("created", self.get_detail(calculation_snapshot_id, deadline))

    def get_detail(
        self,
        calculation_snapshot_id: str,
        deadline: RequestDeadline | None,
    ) -> RptCalculationDetail:
        calculation_snapshot_id = _uuid4(calculation_snapshot_id)
        calculation_row = self._connection.execute(
            """
            SELECT calculation_snapshot_id, analysis_input_snapshot_id, execution_id,
                   wheel_model_id, algorithm_id, algorithm_version, numeric_policy,
                   required_cycles_exact, failure_status,
                   CASE WHEN typeof(result_snapshot_json)='text'
                              AND length(CAST(result_snapshot_json AS BLOB))<=65536
                        THEN result_snapshot_json END,
                   input_content_sha256, operation_sha256, content_sha256, created_at_utc
            FROM rpt_calculation_snapshots WHERE calculation_snapshot_id=?
            """,
            (calculation_snapshot_id,),
        ).fetchone()
        _check_deadline(deadline, "rpt_detail_result")
        if calculation_row is None:
            raise ProjectOperationError("entity_not_found", "Сохранённый расчёт РПТ не найден.")
        input_row = self._connection.execute(
            """
            SELECT analysis_input_snapshot_id, execution_id, local_import_id,
                   wheel_model_id, local_specimen_id, source_specimen_id, source_run_id,
                   export_revision, plan_selection, plan_id, plan_revision,
                   plan_payload_path, plan_payload_sha256, source_outer_package_sha256,
                   source_snapshot_sha256, operation_sha256,
                   CASE WHEN typeof(input_snapshot_json)='text'
                              AND length(CAST(input_snapshot_json AS BLOB))<=65536
                        THEN input_snapshot_json END,
                   content_sha256, actor, decision_reason, created_at_utc
            FROM rpt_analysis_input_snapshots WHERE analysis_input_snapshot_id=?
            """,
            (str(calculation_row[1]),),
        ).fetchone()
        _check_deadline(deadline, "rpt_detail_input")
        if input_row is None:
            raise _corrupt()
        input_payload = _load_snapshot(input_row[16])
        result_payload = _load_snapshot(calculation_row[9])
        try:
            typed_input = RptInputSnapshotModel.model_validate(input_payload)
            typed_result = RptReferenceResultModel.model_validate(result_payload)
        except ValidationError as error:
            raise _corrupt() from error
        self._validate_saved_detail(input_row, calculation_row, typed_input, typed_result, deadline)
        return RptCalculationDetail(
            input_snapshot=RptAnalysisInputSnapshot(
                analysis_input_snapshot_id=str(input_row[0]),
                execution_id=str(input_row[1]),
                input_snapshot=input_payload,
                content_sha256=str(input_row[17]),
                operation_sha256=str(input_row[15]),
                actor=str(input_row[18]),
                decision_reason=str(input_row[19]),
                created_at_utc=str(input_row[20]),
            ),
            calculation_snapshot=RptCalculationSnapshot(
                calculation_snapshot_id=str(calculation_row[0]),
                analysis_input_snapshot_id=str(calculation_row[1]),
                execution_id=str(calculation_row[2]),
                algorithm_id="rpt_reference",
                algorithm_version="1.0.0",
                numeric_policy="exact_fraction_v1",
                result_snapshot=result_payload,
                input_content_sha256=str(calculation_row[10]),
                content_sha256=str(calculation_row[12]),
                operation_sha256=str(calculation_row[11]),
                created_at_utc=str(calculation_row[13]),
            ),
        )

    def _validate_saved_detail(
        self,
        input_row: sqlite3.Row | tuple[object, ...],
        calculation_row: sqlite3.Row | tuple[object, ...],
        typed_input: RptInputSnapshotModel,
        typed_result: RptReferenceResultModel,
        deadline: RequestDeadline | None,
    ) -> None:
        operation = typed_input.operation
        source = typed_input.source
        if (
            str(input_row[0]) != operation.analysisInputSnapshotId
            or str(calculation_row[0]) != operation.calculationSnapshotId
            or str(input_row[1]) != operation.executionId
            or str(calculation_row[2]) != operation.executionId
            or source.executionId != operation.executionId
            or source.localImportId != str(input_row[2])
            or str(input_row[8]) != operation.planSelection
            or source.planSelection != operation.planSelection
            or str(input_row[6]) != source.runId
            or _row_integer(input_row[7]) != source.exportRevision
            or str(input_row[9]) != source.planId
            or _row_integer(input_row[10]) != source.planRevision
            or str(input_row[11]) != source.payloadPath
            or str(input_row[12]) != source.payloadSha256
            or str(input_row[13]) != source.outerPackageSha256
            or str(input_row[14]) != source.sourceSnapshotSha256
            or str(input_row[18]) != operation.actor
            or str(input_row[19]) != operation.reason
            or str(calculation_row[1]) != str(input_row[0])
            or str(calculation_row[3]) != str(input_row[3])
            or str(calculation_row[4]) != ALGORITHM_ID
            or str(calculation_row[5]) != ALGORITHM_VERSION
            or str(calculation_row[6]) != NUMERIC_POLICY
            or str(calculation_row[7]) != typed_result.required_cycles_exact.decimal
            or str(calculation_row[8]) != typed_result.failure_result.status
            or str(input_row[15]) != str(calculation_row[11])
            or str(input_row[17]) != str(calculation_row[10])
            or str(input_row[20]) != str(calculation_row[13])
        ):
            raise _corrupt()
        _validate_saved_field_links(typed_input)
        _validate_saved_failure_link(typed_input, typed_result)
        chosen = {item.field: item.value for item in typed_input.fieldSelections}
        failure = typed_input.failureEvidence
        try:
            validate_rpt_reference_input(
                RptReferenceInput(
                    nominal_rpm=chosen["nominal_rpm"],
                    design_cycles=chosen["design_cycles"],
                    reserve_factor=chosen["reserve_factor"],
                    acceleration_duration_s=chosen["acceleration_duration_s"],
                    steady_duration_s=chosen["steady_duration_s"],
                    deceleration_duration_s=chosen["deceleration_duration_s"],
                    failure=None if failure is None else RptFailureInput(failure.applicability, failure.durationToFailureS),
                )
            )
        except RptCalculationError as error:
            raise _corrupt() from error
        comparison = typed_result.lower_point_comparison
        if (
            comparison.source_policy != source.sourceValues.lower_point_policy
            or comparison.target_policy != source.executionTargets.lower_point_policy
            or comparison.source_explicit_lower_rpm != source.sourceValues.explicit_lower_rpm
            or comparison.target_lower_rpm != source.executionTargets.lower_rpm
            or comparison.model_dump(mode="json")
            != asdict(
                compare_rpt_lower_point(
                    _exact_value_from_model(typed_result.minimum_rpm),
                    source.sourceValues.lower_point_policy,
                    source.executionTargets.lower_point_policy,
                    source.sourceValues.explicit_lower_rpm,
                    source.executionTargets.lower_rpm,
                )
            )
        ):
            raise _corrupt()
        expected_operation_hash = _sha256_json(cast(dict[str, object], operation.model_dump(mode="json")))
        expected_input_hash = _sha256_json(
            {
                "analysisInputSnapshotId": str(input_row[0]),
                "executionId": str(input_row[1]),
                "input": cast(dict[str, object], typed_input.model_dump(mode="json")),
                "actor": str(input_row[18]),
                "reason": str(input_row[19]),
                "createdAtUtc": str(input_row[20]),
            }
        )
        expected_result_hash = _sha256_json(
            {
                "calculationSnapshotId": str(calculation_row[0]),
                "analysisInputSnapshotId": str(input_row[0]),
                "inputContentSha256": expected_input_hash,
                "result": cast(dict[str, object], typed_result.model_dump(mode="json")),
                "createdAtUtc": str(calculation_row[13]),
            }
        )
        if expected_operation_hash != str(input_row[15]) or expected_input_hash != str(input_row[17]) or expected_result_hash != str(calculation_row[12]):
            raise _corrupt()
        self._validate_saved_source(input_row, source, deadline)
        audits = self._connection.execute(
            """
            SELECT occurred_at_utc, actor_kind, payload_json
            FROM project_audit_events
            WHERE event_type='rpt_calculation.created'
              AND json_extract(payload_json, '$.calculationSnapshotId')=?
            LIMIT 2
            """,
            (str(calculation_row[0]),),
        ).fetchall()
        _check_deadline(deadline, "rpt_detail_audit")
        expected_audit = {
            "analysisInputSnapshotId": str(input_row[0]),
            "calculationSnapshotId": str(calculation_row[0]),
            "executionId": str(input_row[1]),
            "localImportId": str(input_row[2]),
            "wheelModelId": str(input_row[3]),
            "planSelection": str(input_row[8]),
            "inputContentSha256": expected_input_hash,
            "calculationContentSha256": expected_result_hash,
            "operationSha256": expected_operation_hash,
        }
        if len(audits) != 1 or str(audits[0][0]) != str(input_row[20]) or str(audits[0][1]) != "user" or str(audits[0][2]) != canonical_json(expected_audit):
            raise _corrupt()

    def _validate_saved_source(
        self,
        input_row: sqlite3.Row | tuple[object, ...],
        source: object,
        deadline: RequestDeadline | None,
    ) -> None:
        from impeller_reliability.calculations.rpt_input_snapshot import RptSourceSnapshotModel

        if not isinstance(source, RptSourceSnapshotModel):
            raise _corrupt()
        row = self._connection.execute(
            """
            SELECT e.method, e.wheel_model_id, e.local_specimen_id, e.source_specimen_id,
                   e.source_outer_package_sha256, s.package_id, s.run_id,
                   s.export_revision, s.outer_package_sha256, s.source_snapshot_sha256,
                   s.producer_name, s.producer_version, s.producer_build_id,
                   s.producer_git_commit, p.mode, p.original_plan_id,
                   p.original_plan_revision, p.original_plan_sha256,
                   p.effective_plan_id, p.effective_plan_revision, p.effective_plan_sha256
            FROM reliability_test_executions e
            JOIN r130sh_sources s ON s.local_import_id=e.local_import_id
            JOIN r130sh_run_projections p ON p.local_import_id=s.local_import_id
            WHERE e.execution_id=? AND e.local_import_id=?
            """,
            (source.executionId, source.localImportId),
        ).fetchone()
        _check_deadline(deadline, "rpt_saved_source_link")
        if row is None:
            raise _corrupt()
        plan_offset = 15 if source.planSelection == "original" else 18
        if (
            str(row[0]) != "rpt"
            or str(row[1]) != str(input_row[3])
            or str(row[2]) != str(input_row[4])
            or str(row[3]) != str(input_row[5])
            or str(row[4]) != source.outerPackageSha256
            or str(row[5]) != source.packageId
            or str(row[6]) != source.runId
            or int(row[7]) != source.exportRevision
            or str(row[8]) != source.outerPackageSha256
            or str(row[9]) != source.sourceSnapshotSha256
            or (str(row[10]), str(row[11]), str(row[12]), str(row[13])) != (source.producer.name, source.producer.version, source.producer.buildId, source.producer.gitCommit)
            or str(row[14]) != "rpt"
            or str(row[plan_offset]) != source.planId
            or int(row[plan_offset + 1]) != source.planRevision
            or str(row[plan_offset + 2]) != source.payloadSha256
        ):
            raise _corrupt()

    def list_page(
        self,
        wheel_model_id: str,
        cursor: str | None,
        limit: int,
        deadline: RequestDeadline | None,
    ) -> RptCalculationPage:
        wheel_model_id = _uuid4(wheel_model_id)
        if isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ProjectOperationError("validation_error", "Размер страницы расчётов РПТ должен быть от 1 до 50.")
        if cursor is None:
            ceiling_row = self._connection.execute(
                "SELECT coalesce(max(rowid), 0) FROM rpt_calculation_snapshots WHERE wheel_model_id=?",
                (wheel_model_id,),
            ).fetchone()
            ceiling = int(ceiling_row[0]) if ceiling_row is not None else 0
            after = ceiling + 1
        else:
            ceiling, after = _decode_cursor(cursor, wheel_model_id)
        rows = self._connection.execute(
            """
            SELECT rowid, calculation_snapshot_id, analysis_input_snapshot_id,
                   execution_id, wheel_model_id, required_cycles_exact,
                   failure_status, created_at_utc
            FROM rpt_calculation_snapshots
            WHERE wheel_model_id=? AND rowid<=? AND rowid<?
            ORDER BY rowid DESC LIMIT ?
            """,
            (wheel_model_id, ceiling, after, limit + 1),
        ).fetchall()
        _check_deadline(deadline, "rpt_history_page")
        visible = rows[:limit]
        summaries = tuple(
            RptCalculationSummary(
                calculation_snapshot_id=str(row[1]),
                analysis_input_snapshot_id=str(row[2]),
                execution_id=str(row[3]),
                wheel_model_id=str(row[4]),
                required_cycles_exact=str(row[5]),
                failure_status=_failure_status(str(row[6])),
                created_at_utc=str(row[7]),
            )
            for row in visible
        )
        next_cursor = _encode_cursor(wheel_model_id, ceiling, int(visible[-1][0])) if len(rows) > limit else None
        return RptCalculationPage(summaries, next_cursor)


def validate_rpt_calculation_evidence(connection: sqlite3.Connection, deadline: RequestDeadline | None) -> None:
    input_count_row = connection.execute("SELECT count(*) FROM rpt_analysis_input_snapshots").fetchone()
    result_count_row = connection.execute("SELECT count(*) FROM rpt_calculation_snapshots").fetchone()
    audit_count_row = connection.execute("SELECT count(*) FROM project_audit_events WHERE event_type='rpt_calculation.created'").fetchone()
    if input_count_row is None or result_count_row is None or audit_count_row is None:
        raise _corrupt()
    if int(input_count_row[0]) != int(result_count_row[0]) or int(result_count_row[0]) != int(audit_count_row[0]):
        raise _corrupt()
    repository = RptCalculationRepository(connection)
    for row in connection.execute("SELECT calculation_snapshot_id FROM rpt_calculation_snapshots ORDER BY rowid"):
        repository.get_detail(str(row[0]), deadline)
        _check_deadline(deadline, "rpt_saved_evidence_scan")
