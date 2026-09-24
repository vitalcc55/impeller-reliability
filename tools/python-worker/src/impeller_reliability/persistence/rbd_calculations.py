from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
import hashlib
import json
import sqlite3
from typing import Final, Literal, cast
from uuid import UUID

from impeller_reliability.calculations.rbd import (
    ALGORITHM_ID,
    ALGORITHM_VERSION,
    NUMERIC_POLICY,
    RbdCalculationError,
    RbdFailureApplicability,
    RbdFailureInput,
    RbdReferenceInput,
    calculate_rbd_reference,
)
from impeller_reliability.persistence.audit import audit_now, insert_audit
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_schema import MAX_AUDIT_PAYLOAD_BYTES
from impeller_reliability.persistence.r130sh_sources import RbdPlanSourceSnapshot
from impeller_reliability.persistence.reliability_domain import bounded_text
from impeller_reliability.persistence.sqlite_deadline import sqlite_deadline_guard, sqlite_query_rows_with_deadline
from impeller_reliability.persistence.timestamps import require_canonical_utc_timestamp
from impeller_reliability.worker.deadline import RequestDeadline

RbdInputField = Literal[
    "base_cycles",
    "reserve_factor",
    "nominal_rpm",
    "acceleration_duration_s",
    "deceleration_duration_s",
]
RbdInputOrigin = Literal["source", "manual"]
WriteDisposition = Literal["created", "existing"]

_FIELD_UNITS: Final[dict[RbdInputField, str]] = {
    "base_cycles": "cycle",
    "reserve_factor": "1",
    "nominal_rpm": "rpm",
    "acceleration_duration_s": "s",
    "deceleration_duration_s": "s",
}
_FIELD_ORDER: Final[tuple[RbdInputField, ...]] = tuple(_FIELD_UNITS)
_JSON_MAX_BYTES: Final = 65_536


@dataclass(frozen=True, slots=True)
class RbdEvidenceReference:
    document_id: str | None = None
    document_record_revision: int | None = None
    document_locator: str = ""
    observation_version_id: str | None = None


@dataclass(frozen=True, slots=True)
class RbdFieldSelection:
    field: RbdInputField
    origin: RbdInputOrigin
    manual_value: str | None = None
    basis: str = ""
    evidence: RbdEvidenceReference | None = None


@dataclass(frozen=True, slots=True)
class RbdFailureEvidence:
    applicability: RbdFailureApplicability
    duration_to_failure_s: str | None
    basis: str
    failure_observation_ids: tuple[str, ...] = ()
    evidence: RbdEvidenceReference | None = None


@dataclass(frozen=True, slots=True)
class RbdAnalysisInputSnapshot:
    analysis_input_snapshot_id: str
    execution_id: str
    local_import_id: str
    wheel_model_id: str
    local_specimen_id: str
    source_specimen_id: str
    source_run_id: str
    export_revision: int
    plan_selection: Literal["original", "effective"]
    plan_id: str
    plan_revision: int
    plan_payload_path: str
    plan_payload_sha256: str
    source_outer_package_sha256: str
    source_snapshot_sha256: str
    operation_sha256: str
    input_snapshot: dict[str, object]
    content_sha256: str
    actor: str
    decision_reason: str
    created_at_utc: str


@dataclass(frozen=True, slots=True)
class RbdCalculationSnapshot:
    calculation_snapshot_id: str
    analysis_input_snapshot_id: str
    execution_id: str
    wheel_model_id: str
    algorithm_id: Literal["rbd_reference"]
    algorithm_version: Literal["1.0.0"]
    numeric_policy: Literal["exact_fraction_v1"]
    result_snapshot: dict[str, object]
    input_content_sha256: str
    operation_sha256: str
    content_sha256: str
    created_at_utc: str


@dataclass(frozen=True, slots=True)
class RbdCalculationDetail:
    input_snapshot: RbdAnalysisInputSnapshot
    calculation_snapshot: RbdCalculationSnapshot


@dataclass(frozen=True, slots=True)
class RbdCalculationWriteResult:
    disposition: WriteDisposition
    detail: RbdCalculationDetail


@dataclass(frozen=True, slots=True)
class RbdCalculationSummary:
    calculation_snapshot_id: str
    analysis_input_snapshot_id: str
    execution_id: str
    wheel_model_id: str
    plan_selection: Literal["original", "effective"]
    required_cycles: str
    failure_status: Literal["calculated", "not_applicable"]
    created_at_utc: str


@dataclass(frozen=True, slots=True)
class RbdCalculationPage:
    items: tuple[RbdCalculationSummary, ...]
    next_cursor: str | None


class RbdCalculationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def operation_sha256(
        self,
        *,
        analysis_input_snapshot_id: str,
        calculation_snapshot_id: str,
        execution_id: str,
        plan_selection: str,
        selections: tuple[RbdFieldSelection, ...],
        failure: RbdFailureEvidence | None,
        actor: str,
        reason: str,
    ) -> str:
        payload = _operation_payload(
            analysis_input_snapshot_id=analysis_input_snapshot_id,
            calculation_snapshot_id=calculation_snapshot_id,
            execution_id=execution_id,
            plan_selection=plan_selection,
            selections=selections,
            failure=failure,
            actor=actor,
            reason=reason,
        )
        return _sha256_canonical_json(payload)

    def resolve_idempotent_retry(
        self,
        analysis_input_snapshot_id: str,
        calculation_snapshot_id: str,
        operation_sha256: str,
        deadline: RequestDeadline | None,
    ) -> RbdCalculationWriteResult | None:
        analysis_input_snapshot_id = _uuid4(analysis_input_snapshot_id)
        calculation_snapshot_id = _uuid4(calculation_snapshot_id)
        _sha256(operation_sha256, "operation")
        input_row = self._connection.execute(
            "SELECT operation_sha256 FROM rbd_analysis_input_snapshots WHERE analysis_input_snapshot_id=?",
            (analysis_input_snapshot_id,),
        ).fetchone()
        calculation_row = self._connection.execute(
            "SELECT analysis_input_snapshot_id, operation_sha256 FROM rbd_calculation_snapshots WHERE calculation_snapshot_id=?",
            (calculation_snapshot_id,),
        ).fetchone()
        _check_deadline(deadline, "rbd_retry_lookup")
        if input_row is None and calculation_row is None:
            return None
        if (
            input_row is None
            or calculation_row is None
            or str(calculation_row[0]) != analysis_input_snapshot_id
            or str(input_row[0]) != operation_sha256
            or str(calculation_row[1]) != operation_sha256
        ):
            raise ProjectOperationError(
                "revision_conflict",
                "Идентификаторы расчёта уже использованы с другими входами.",
            )
        detail = self.get_detail(calculation_snapshot_id, deadline)
        if detail.input_snapshot.analysis_input_snapshot_id != analysis_input_snapshot_id:
            raise ProjectOperationError("revision_conflict", "Идентификаторы относятся к другой паре снимков.")
        return RbdCalculationWriteResult("existing", detail)

    def create(
        self,
        *,
        analysis_input_snapshot_id: str,
        calculation_snapshot_id: str,
        source: RbdPlanSourceSnapshot,
        selections: tuple[RbdFieldSelection, ...],
        failure: RbdFailureEvidence | None,
        actor: str,
        reason: str,
        operation_sha256: str,
        deadline: RequestDeadline | None,
    ) -> RbdCalculationWriteResult:
        analysis_input_snapshot_id = _uuid4(analysis_input_snapshot_id)
        calculation_snapshot_id = _uuid4(calculation_snapshot_id)
        actor = bounded_text(actor, 200, "Автор расчёта")
        reason = bounded_text(reason, 2000, "Основание расчёта", multiline=True)
        _sha256(operation_sha256, "operation")
        expected_operation_sha256 = self.operation_sha256(
            analysis_input_snapshot_id=analysis_input_snapshot_id,
            calculation_snapshot_id=calculation_snapshot_id,
            execution_id=source.execution_id,
            plan_selection=source.selection,
            selections=selections,
            failure=failure,
            actor=actor,
            reason=reason,
        )
        if operation_sha256 != expected_operation_sha256:
            raise ProjectOperationError("validation_error", "Operation hash расчёта не соответствует команде.")
        execution = self._execution_tuple(source, deadline)
        selected_values, field_snapshots = self._resolve_fields(source, selections, execution, deadline)
        failure_payload, failure_input = self._resolve_failure(source, failure, execution, deadline)
        try:
            result = calculate_rbd_reference(
                RbdReferenceInput(
                    nominal_rpm=selected_values["nominal_rpm"],
                    base_cycles=selected_values["base_cycles"],
                    reserve_factor=selected_values["reserve_factor"],
                    acceleration_duration_s=selected_values["acceleration_duration_s"],
                    deceleration_duration_s=selected_values["deceleration_duration_s"],
                    failure=failure_input,
                )
            )
        except RbdCalculationError as error:
            raise ProjectOperationError(
                "validation_error",
                str(error),
                details={"reasonCode": error.reason_code},
            ) from error
        now = audit_now(self._connection)
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
        input_payload: dict[str, object] = {
            "schemaVersion": 1,
            "operation": operation_payload,
            "source": _source_payload(source),
            "fieldSelections": field_snapshots,
            "failureEvidence": failure_payload,
        }
        result_payload = cast(dict[str, object], asdict(result))
        required_cycles = result.required_cycles_exact.decimal
        if required_cycles is None:
            raise ProjectOperationError("domain_error", "Точное число требуемых циклов не является конечной десятичной дробью.")
        _bounded_json(input_payload, "input snapshot")
        _bounded_json(result_payload, "result snapshot")
        input_content_sha256 = _sha256_canonical_json(
            {
                "analysisInputSnapshotId": analysis_input_snapshot_id,
                "executionId": source.execution_id,
                "input": input_payload,
                "actor": actor,
                "reason": reason,
                "createdAtUtc": now,
            }
        )
        calculation_content_sha256 = _sha256_canonical_json(
            {
                "calculationSnapshotId": calculation_snapshot_id,
                "analysisInputSnapshotId": analysis_input_snapshot_id,
                "inputContentSha256": input_content_sha256,
                "result": result_payload,
                "createdAtUtc": now,
            }
        )
        _begin_immediate_with_deadline(self._connection, deadline)
        try:
            existing = self.resolve_idempotent_retry(
                analysis_input_snapshot_id,
                calculation_snapshot_id,
                operation_sha256,
                deadline,
            )
            if existing is not None:
                self._connection.rollback()
                return existing
            self._execution_tuple(source, deadline)
            self._connection.execute(
                """
                INSERT INTO rbd_analysis_input_snapshots (
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
                    _canonical_json(input_payload),
                    input_content_sha256,
                    actor,
                    reason,
                    now,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO rbd_calculation_snapshots (
                    calculation_snapshot_id, analysis_input_snapshot_id, execution_id,
                    wheel_model_id, algorithm_id, algorithm_version, numeric_policy,
                    required_cycles_exact, failure_status, result_snapshot_json,
                    input_content_sha256, operation_sha256,
                    content_sha256, created_at_utc
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
                    _canonical_json(result_payload),
                    input_content_sha256,
                    operation_sha256,
                    calculation_content_sha256,
                    now,
                ),
            )
            insert_audit(
                self._connection,
                event_type="rbd_calculation.created",
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
            _check_deadline(deadline, "rbd_calculation_commit")
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise ProjectOperationError("revision_conflict", "Идентификаторы снимков уже используются.") from error
        except Exception:
            self._connection.rollback()
            raise
        return RbdCalculationWriteResult("created", self.get_detail(calculation_snapshot_id, deadline))

    def get_detail(
        self,
        calculation_snapshot_id: str,
        deadline: RequestDeadline | None,
    ) -> RbdCalculationDetail:
        calculation_snapshot_id = _uuid4(calculation_snapshot_id)
        with sqlite_deadline_guard(self._connection, deadline, "rbd_calculation_detail"):
            row = self._connection.execute(
                _DETAIL_QUERY + " WHERE c.calculation_snapshot_id=?",
                (calculation_snapshot_id,),
            ).fetchone()
        _check_deadline(deadline, "rbd_calculation_detail")
        if row is None:
            raise ProjectOperationError("entity_not_found", "Снимок расчёта РБД не найден.")
        return _detail_from_row(row)

    def list_page(
        self,
        wheel_model_id: str,
        cursor: str | None,
        limit: int,
        deadline: RequestDeadline | None,
    ) -> RbdCalculationPage:
        wheel_model_id = _uuid4(wheel_model_id)
        if not 1 <= limit <= 50:
            raise ProjectOperationError("validation_error", "Размер страницы должен быть от 1 до 50.")
        if cursor is None:
            with sqlite_deadline_guard(self._connection, deadline, "rbd_calculation_page_boundary"):
                maximum = self._connection.execute(
                    "SELECT max(rowid) FROM rbd_calculation_snapshots WHERE wheel_model_id=?",
                    (wheel_model_id,),
                ).fetchone()
            snapshot_rowid = 0 if maximum is None or maximum[0] is None else int(maximum[0])
            after_time = after_id = None
        else:
            snapshot_rowid, after_time, after_id = _decode_cursor(cursor, wheel_model_id)
        parameters: list[object] = [wheel_model_id, snapshot_rowid]
        after_clause = ""
        if after_time is not None and after_id is not None:
            after_clause = "AND (c.created_at_utc < ? OR (c.created_at_utc = ? AND c.calculation_snapshot_id > ?))"
            parameters.extend((after_time, after_time, after_id))
        parameters.append(limit + 1)
        stream = sqlite_query_rows_with_deadline(
            self._connection,
            f"""
            SELECT c.calculation_snapshot_id, c.analysis_input_snapshot_id,
                   c.execution_id, c.wheel_model_id, i.plan_selection,
                   c.required_cycles_exact, c.failure_status, c.created_at_utc
            FROM rbd_calculation_snapshots c
            JOIN rbd_analysis_input_snapshots i
              ON i.analysis_input_snapshot_id=c.analysis_input_snapshot_id
            WHERE c.wheel_model_id=? AND c.rowid<=? {after_clause}
            ORDER BY c.created_at_utc DESC, c.calculation_snapshot_id ASC
            LIMIT ?
            """,
            tuple(parameters),
            deadline,
            "rbd_calculation_list_page",
        )
        with closing(stream):
            rows = list(stream)
        visible = rows[:limit]
        items = tuple(_summary_from_row(row) for row in visible)
        next_cursor = None
        if len(rows) > limit and visible:
            last = visible[-1]
            next_cursor = _encode_cursor(wheel_model_id, snapshot_rowid, str(last[7]), str(last[0]))
        return RbdCalculationPage(items, next_cursor)

    def _execution_tuple(
        self,
        source: RbdPlanSourceSnapshot,
        deadline: RequestDeadline | None,
    ) -> tuple[str, str, str]:
        row = self._connection.execute(
            """
            SELECT e.wheel_model_id, e.local_specimen_id, e.source_specimen_id
            FROM reliability_test_executions e
            JOIN r130sh_sources s ON s.local_import_id=e.local_import_id
            WHERE e.execution_id=? AND e.local_import_id=? AND e.method='rbd'
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
        _check_deadline(deadline, "rbd_execution_source_tuple")
        if row is None:
            raise ProjectOperationError("revision_conflict", "Исполнение или revision источника изменились.")
        return _uuid4(str(row[0])), _uuid4(str(row[1])), str(row[2])

    def _resolve_fields(
        self,
        source: RbdPlanSourceSnapshot,
        selections: tuple[RbdFieldSelection, ...],
        execution: tuple[str, str, str],
        deadline: RequestDeadline | None,
    ) -> tuple[dict[RbdInputField, str], list[dict[str, object]]]:
        if len(selections) != len(_FIELD_ORDER) or {item.field for item in selections} != set(_FIELD_ORDER):
            raise ProjectOperationError("validation_error", "Нужно явно выбрать происхождение пяти входов РБД.")
        source_values = asdict(source.source_values)
        result: dict[RbdInputField, str] = {}
        snapshots: list[dict[str, object]] = []
        for field in _FIELD_ORDER:
            choice = next(item for item in selections if item.field == field)
            raw_source = source_values[field]
            if raw_source is not None and not isinstance(raw_source, str):
                raise ProjectOperationError("corrupt_project", "Source value РБД повреждено.")
            if choice.origin == "source":
                if raw_source is None:
                    raise ProjectOperationError("validation_error", f"{field}: значение отсутствует в выбранном плане.")
                if choice.manual_value is not None or choice.basis or choice.evidence is not None:
                    raise ProjectOperationError(
                        "validation_error",
                        f"{field}: ручные сведения нельзя передать для выбора исходного значения.",
                    )
                value = raw_source
                basis = ""
                evidence = None
            elif choice.origin == "manual":
                if choice.manual_value is None:
                    raise ProjectOperationError("validation_error", f"{field}: ручное значение не задано.")
                if choice.evidence is not None and choice.evidence.observation_version_id is not None:
                    raise ProjectOperationError(
                        "validation_error",
                        f"{field}: ReliabilityObservation не подтверждает значение параметра плана РБД.",
                    )
                value = choice.manual_value
                basis = bounded_text(choice.basis, 2000, f"{field}: основание", multiline=True)
                evidence = self._evidence_snapshot(
                    choice.evidence,
                    execution,
                    source.execution_id,
                    deadline,
                )
            else:
                raise ProjectOperationError("validation_error", f"{field}: происхождение не поддерживается.")
            result[field] = value
            snapshots.append(
                {
                    "field": field,
                    "unit": _FIELD_UNITS[field],
                    "origin": choice.origin,
                    "value": value,
                    "rawSourceValue": raw_source,
                    "sourceReference": f"{source.payload_path}#/source_values/{field}",
                    "basis": basis,
                    "evidence": evidence,
                }
            )
        return result, snapshots

    def _resolve_failure(
        self,
        source: RbdPlanSourceSnapshot,
        failure: RbdFailureEvidence | None,
        execution: tuple[str, str, str],
        deadline: RequestDeadline | None,
    ) -> tuple[dict[str, object] | None, RbdFailureInput | None]:
        if failure is None:
            return None, None
        basis = bounded_text(failure.basis, 2000, "Основание применимости таблицы 3", multiline=True)
        if len(failure.failure_observation_ids) > 64 or len(set(failure.failure_observation_ids)) != len(failure.failure_observation_ids):
            raise ProjectOperationError("validation_error", "Список наблюдений отказа превышает лимит или содержит повторы.")
        observation_snapshots: list[dict[str, object]] = []
        for failure_id in failure.failure_observation_ids:
            failure_id = _uuid4(failure_id)
            row = self._connection.execute(
                """
                SELECT failure_type, subject_kind, source_event_reference,
                       source_field_reference, duration_s, rpm, observed_at_utc,
                       source_outer_package_sha256
                FROM failure_observations WHERE failure_id=? AND execution_id=?
                """,
                (failure_id, source.execution_id),
            ).fetchone()
            if row is None:
                raise ProjectOperationError("validation_error", "Наблюдение отказа не относится к исполнению.")
            observation_snapshots.append(
                {
                    "failureObservationId": failure_id,
                    "failureType": str(row[0]),
                    "subjectKind": str(row[1]),
                    "sourceEventReference": str(row[2]),
                    "sourceFieldReference": str(row[3]),
                    "durationS": None if row[4] is None else str(row[4]),
                    "rpm": None if row[5] is None else str(row[5]),
                    "observedAtUtc": None if row[6] is None else str(row[6]),
                    "sourceOuterPackageSha256": _sha256(str(row[7]), "failure source"),
                }
            )
        if failure.applicability == "exact_supported":
            if failure.duration_to_failure_s is None:
                raise ProjectOperationError("validation_error", "Для таблицы 3 требуется точное T_OTK.")
            if failure.evidence is None or failure.evidence.document_id is None:
                raise ProjectOperationError(
                    "validation_error",
                    "Для таблицы 3 нужен документ с точным временем до отказа и началом отсчёта.",
                )
            if failure.evidence.observation_version_id is not None:
                raise ProjectOperationError(
                    "validation_error",
                    "M04B observation времени установившегося вращения не является T_OTK.",
                )
            for observed in observation_snapshots:
                if (
                    observed["failureType"] != "specimen_outcome"
                    or observed["subjectKind"] != "specimen"
                    or observed["sourceOuterPackageSha256"] != source.outer_package_sha256
                    or observed["durationS"] != failure.duration_to_failure_s
                ):
                    raise ProjectOperationError(
                        "validation_error",
                        "Выбранное наблюдение отказа не подтверждает точное T_OTK.",
                    )
        evidence = self._evidence_snapshot(
            failure.evidence,
            execution,
            source.execution_id,
            deadline,
        )
        return (
            {
                "applicability": failure.applicability,
                "durationToFailureS": failure.duration_to_failure_s,
                "basis": basis,
                "failureObservations": observation_snapshots,
                "evidence": evidence,
            },
            RbdFailureInput(failure.applicability, failure.duration_to_failure_s),
        )

    def _evidence_snapshot(
        self,
        evidence: RbdEvidenceReference | None,
        execution: tuple[str, str, str],
        execution_id: str,
        deadline: RequestDeadline | None,
    ) -> dict[str, object] | None:
        if evidence is None:
            return None
        document_snapshot: dict[str, object] | None = None
        if evidence.document_id is not None:
            document_id = _uuid4(evidence.document_id)
            if evidence.document_record_revision is None or evidence.document_record_revision < 1:
                raise ProjectOperationError("validation_error", "Нужна exact revision документа.")
            row = self._connection.execute(
                """
                SELECT d.document_kind, d.title, d.designation, d.revision_label,
                       d.record_revision, f.sha256,
                       EXISTS(SELECT 1 FROM case_document_wheel_models w WHERE w.case_document_id=d.case_document_id AND w.wheel_model_id=?),
                       EXISTS(SELECT 1 FROM case_document_specimens s WHERE s.case_document_id=d.case_document_id AND s.specimen_id=?)
                FROM case_documents d
                LEFT JOIN case_document_files f ON f.case_document_id=d.case_document_id
                WHERE d.case_document_id=?
                """,
                (execution[0], execution[1], document_id),
            ).fetchone()
            if row is None or int(row[4]) != evidence.document_record_revision or not (int(row[6]) or int(row[7])):
                raise ProjectOperationError("validation_error", "Exact revision документа недоступна или неприменима.")
            document_snapshot = {
                "documentId": document_id,
                "recordRevision": int(row[4]),
                "documentKind": str(row[0]),
                "title": str(row[1]),
                "designation": str(row[2]),
                "revisionLabel": str(row[3]),
                "fileSha256": None if row[5] is None else _sha256(str(row[5]), "document file"),
                "locator": bounded_text(evidence.document_locator, 1000, "Локатор документа"),
            }
        elif evidence.document_record_revision is not None or evidence.document_locator:
            raise ProjectOperationError("validation_error", "Document provenance задан неполностью.")
        observation_snapshot: dict[str, object] | None = None
        if evidence.observation_version_id is not None:
            observation_version_id = _uuid4(evidence.observation_version_id)
            row = self._connection.execute(
                """
                SELECT v.version_number, v.classification, v.endpoint_kind,
                       v.metric_kind, v.metric_unit, v.lower_value, v.upper_value,
                       v.content_sha256
                FROM reliability_observation_versions v
                JOIN reliability_observations o ON o.observation_id=v.observation_id
                WHERE v.observation_version_id=? AND o.execution_id=?
                """,
                (observation_version_id, execution_id),
            ).fetchone()
            if row is None:
                raise ProjectOperationError("validation_error", "Observation version не относится к исполнению.")
            observation_snapshot = {
                "observationVersionId": observation_version_id,
                "versionNumber": int(row[0]),
                "classification": str(row[1]),
                "endpointKind": str(row[2]),
                "metricKind": None if row[3] is None else str(row[3]),
                "metricUnit": None if row[4] is None else str(row[4]),
                "lowerValue": None if row[5] is None else str(row[5]),
                "upperValue": None if row[6] is None else str(row[6]),
                "contentSha256": _sha256(str(row[7]), "observation"),
            }
        _check_deadline(deadline, "rbd_evidence_snapshot")
        return {"document": document_snapshot, "observation": observation_snapshot}


_DETAIL_QUERY: Final = """
SELECT i.analysis_input_snapshot_id, i.execution_id, i.local_import_id,
       i.wheel_model_id, i.local_specimen_id, i.source_specimen_id, i.source_run_id,
       i.export_revision, i.plan_selection, i.plan_id, i.plan_revision,
       i.plan_payload_path, i.plan_payload_sha256, i.source_outer_package_sha256,
       i.source_snapshot_sha256, i.operation_sha256,
       CASE WHEN typeof(i.input_snapshot_json)='text' AND length(CAST(i.input_snapshot_json AS BLOB))<=65536 THEN i.input_snapshot_json END,
       i.content_sha256, i.actor, i.decision_reason, i.created_at_utc,
       c.calculation_snapshot_id, c.algorithm_id, c.algorithm_version,
       c.numeric_policy,
       CASE WHEN typeof(c.result_snapshot_json)='text' AND length(CAST(c.result_snapshot_json AS BLOB))<=65536 THEN c.result_snapshot_json END,
       c.input_content_sha256, c.operation_sha256, c.content_sha256, c.created_at_utc,
       c.execution_id, c.wheel_model_id, c.required_cycles_exact, c.failure_status
FROM rbd_calculation_snapshots c
JOIN rbd_analysis_input_snapshots i
  ON i.analysis_input_snapshot_id=c.analysis_input_snapshot_id
"""


def _detail_from_row(row: Sequence[object]) -> RbdCalculationDetail:
    input_payload = _load_canonical_json_object(row[16], "input")
    result_payload = _load_canonical_json_object(row[25], "result")
    input_snapshot = RbdAnalysisInputSnapshot(
        analysis_input_snapshot_id=_uuid4(str(row[0])),
        execution_id=_uuid4(str(row[1])),
        local_import_id=_uuid4(str(row[2])),
        wheel_model_id=_uuid4(str(row[3])),
        local_specimen_id=_uuid4(str(row[4])),
        source_specimen_id=str(row[5]),
        source_run_id=str(row[6]),
        export_revision=_positive_integer(row[7]),
        plan_selection=_plan_selection(str(row[8])),
        plan_id=str(row[9]),
        plan_revision=_positive_integer(row[10]),
        plan_payload_path=str(row[11]),
        plan_payload_sha256=_sha256(str(row[12]), "plan payload"),
        source_outer_package_sha256=_sha256(str(row[13]), "source archive"),
        source_snapshot_sha256=_sha256(str(row[14]), "source snapshot"),
        operation_sha256=_sha256(str(row[15]), "operation"),
        input_snapshot=input_payload,
        content_sha256=_sha256(str(row[17]), "input content"),
        actor=str(row[18]),
        decision_reason=str(row[19]),
        created_at_utc=require_canonical_utc_timestamp(str(row[20])),
    )
    calculation_snapshot = RbdCalculationSnapshot(
        calculation_snapshot_id=_uuid4(str(row[21])),
        analysis_input_snapshot_id=input_snapshot.analysis_input_snapshot_id,
        execution_id=input_snapshot.execution_id,
        wheel_model_id=input_snapshot.wheel_model_id,
        algorithm_id=_algorithm_id(str(row[22])),
        algorithm_version=_algorithm_version(str(row[23])),
        numeric_policy=_numeric_policy(str(row[24])),
        result_snapshot=result_payload,
        input_content_sha256=_sha256(str(row[26]), "input content"),
        operation_sha256=_sha256(str(row[27]), "operation"),
        content_sha256=_sha256(str(row[28]), "calculation content"),
        created_at_utc=require_canonical_utc_timestamp(str(row[29])),
    )
    if _uuid4(str(row[30])) != input_snapshot.execution_id or _uuid4(str(row[31])) != input_snapshot.wheel_model_id:
        raise _corrupt()
    _validate_snapshot_shapes(input_payload, result_payload)
    required_cycles = _required_mapping(result_payload["required_cycles_exact"], "required cycles")
    failure = _required_mapping(result_payload["failure_result"], "failure result")
    if required_cycles.get("decimal") != row[32] or failure.get("status") != row[33]:
        raise _corrupt()
    return RbdCalculationDetail(input_snapshot, calculation_snapshot)


def _summary_from_row(row: Sequence[object]) -> RbdCalculationSummary:
    required_cycles = row[5]
    status = row[6]
    if not isinstance(required_cycles, str):
        raise _corrupt()
    if status == "calculated":
        failure_status: Literal["calculated", "not_applicable"] = "calculated"
    elif status == "not_applicable":
        failure_status = "not_applicable"
    else:
        raise _corrupt()
    return RbdCalculationSummary(
        calculation_snapshot_id=_uuid4(str(row[0])),
        analysis_input_snapshot_id=_uuid4(str(row[1])),
        execution_id=_uuid4(str(row[2])),
        wheel_model_id=_uuid4(str(row[3])),
        plan_selection=_plan_selection(str(row[4])),
        required_cycles=required_cycles,
        failure_status=failure_status,
        created_at_utc=require_canonical_utc_timestamp(str(row[7])),
    )


def validate_rbd_calculation_evidence(
    connection: sqlite3.Connection,
    deadline: RequestDeadline | None,
) -> None:
    stream = sqlite_query_rows_with_deadline(
        connection,
        _DETAIL_QUERY + " ORDER BY c.rowid",
        (),
        deadline,
        "rbd_calculation_evidence",
    )
    count = 0
    with closing(stream):
        for row in stream:
            detail = _detail_from_row(row)
            input_snapshot = detail.input_snapshot
            calculation = detail.calculation_snapshot
            if input_snapshot.operation_sha256 != calculation.operation_sha256:
                raise _corrupt()
            if calculation.input_content_sha256 != input_snapshot.content_sha256:
                raise _corrupt()
            expected_input_hash = _sha256_canonical_json(
                {
                    "analysisInputSnapshotId": input_snapshot.analysis_input_snapshot_id,
                    "executionId": input_snapshot.execution_id,
                    "input": input_snapshot.input_snapshot,
                    "actor": input_snapshot.actor,
                    "reason": input_snapshot.decision_reason,
                    "createdAtUtc": input_snapshot.created_at_utc,
                }
            )
            expected_calculation_hash = _sha256_canonical_json(
                {
                    "calculationSnapshotId": calculation.calculation_snapshot_id,
                    "analysisInputSnapshotId": input_snapshot.analysis_input_snapshot_id,
                    "inputContentSha256": input_snapshot.content_sha256,
                    "result": calculation.result_snapshot,
                    "createdAtUtc": calculation.created_at_utc,
                }
            )
            if expected_input_hash != input_snapshot.content_sha256 or expected_calculation_hash != calculation.content_sha256:
                raise _corrupt()
            operation = _required_mapping(input_snapshot.input_snapshot.get("operation"), "operation")
            if _sha256_canonical_json(operation) != input_snapshot.operation_sha256:
                raise _corrupt()
            source_payload = _required_mapping(input_snapshot.input_snapshot.get("source"), "source")
            if (
                source_payload.get("executionId") != input_snapshot.execution_id
                or source_payload.get("localImportId") != input_snapshot.local_import_id
                or source_payload.get("runId") != input_snapshot.source_run_id
                or source_payload.get("exportRevision") != input_snapshot.export_revision
                or source_payload.get("outerPackageSha256") != input_snapshot.source_outer_package_sha256
                or source_payload.get("sourceSnapshotSha256") != input_snapshot.source_snapshot_sha256
                or source_payload.get("planSelection") != input_snapshot.plan_selection
                or source_payload.get("payloadPath") != input_snapshot.plan_payload_path
                or source_payload.get("payloadSha256") != input_snapshot.plan_payload_sha256
                or source_payload.get("planId") != input_snapshot.plan_id
                or source_payload.get("planRevision") != input_snapshot.plan_revision
            ):
                raise _corrupt()
            tuple_row = connection.execute(
                """
                SELECT 1
                FROM reliability_test_executions e
                JOIN r130sh_sources s ON s.local_import_id=e.local_import_id
                WHERE e.execution_id=? AND e.local_import_id=?
                  AND e.wheel_model_id=? AND e.local_specimen_id=?
                  AND e.source_specimen_id=? AND s.run_id=? AND s.export_revision=?
                  AND s.outer_package_sha256=? AND s.source_snapshot_sha256=?
                LIMIT 1
                """,
                (
                    input_snapshot.execution_id,
                    input_snapshot.local_import_id,
                    input_snapshot.wheel_model_id,
                    input_snapshot.local_specimen_id,
                    input_snapshot.source_specimen_id,
                    input_snapshot.source_run_id,
                    input_snapshot.export_revision,
                    input_snapshot.source_outer_package_sha256,
                    input_snapshot.source_snapshot_sha256,
                ),
            ).fetchone()
            if tuple_row is None:
                raise _corrupt()
            audit = connection.execute(
                """
                SELECT actor_kind, occurred_at_utc,
                       CASE WHEN typeof(payload_json)='text'
                             AND length(CAST(payload_json AS BLOB))<=?
                            THEN payload_json END
                FROM project_audit_events
                WHERE event_type='rbd_calculation.created'
                  AND json_extract(payload_json, '$.calculationSnapshotId')=?
                LIMIT 2
                """,
                (MAX_AUDIT_PAYLOAD_BYTES, calculation.calculation_snapshot_id),
            ).fetchall()
            if len(audit) != 1:
                raise _corrupt()
            if audit[0][0] != "user" or audit[0][1] != input_snapshot.created_at_utc:
                raise _corrupt()
            payload = _load_canonical_json_object(audit[0][2], "audit", maximum_bytes=MAX_AUDIT_PAYLOAD_BYTES)
            if (
                payload.get("analysisInputSnapshotId") != input_snapshot.analysis_input_snapshot_id
                or payload.get("inputContentSha256") != input_snapshot.content_sha256
                or payload.get("calculationContentSha256") != calculation.content_sha256
                or payload.get("operationSha256") != input_snapshot.operation_sha256
            ):
                raise _corrupt()
            count += 1
    with sqlite_deadline_guard(connection, deadline, "rbd_calculation_counts"):
        audit_count = connection.execute("SELECT count(*) FROM project_audit_events WHERE event_type='rbd_calculation.created'").fetchone()
        input_count = connection.execute("SELECT count(*) FROM rbd_analysis_input_snapshots").fetchone()
        calculation_count = connection.execute("SELECT count(*) FROM rbd_calculation_snapshots").fetchone()
    if audit_count is None or int(audit_count[0]) != count:
        raise _corrupt()
    if input_count is None or calculation_count is None or int(input_count[0]) != int(calculation_count[0]):
        raise _corrupt()


def _operation_payload(
    *,
    analysis_input_snapshot_id: str,
    calculation_snapshot_id: str,
    execution_id: str,
    plan_selection: str,
    selections: tuple[RbdFieldSelection, ...],
    failure: RbdFailureEvidence | None,
    actor: str,
    reason: str,
) -> dict[str, object]:
    if plan_selection not in {"original", "effective"}:
        raise ProjectOperationError("validation_error", "Редакция плана не поддерживается.")
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


def _source_payload(source: RbdPlanSourceSnapshot) -> dict[str, object]:
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
        "methodicalRequirements": asdict(source.methodical_requirements),
        "executionTargets": asdict(source.execution_targets),
    }


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _sha256_canonical_json(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _bounded_json(value: Mapping[str, object], label: str) -> None:
    if len(_canonical_json(value).encode("utf-8")) > _JSON_MAX_BYTES:
        raise ProjectOperationError("validation_error", f"{label}: превышен предел UTF-8 payload.")


def _load_canonical_json_object(value: object, label: str, *, maximum_bytes: int = _JSON_MAX_BYTES) -> dict[str, object]:
    if not isinstance(value, str):
        raise _corrupt()
    try:
        if len(value.encode("utf-8")) > maximum_bytes:
            raise _corrupt()
        decoded: object = json.loads(value)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise _corrupt() from error
    if not isinstance(decoded, dict):
        raise _corrupt()
    result = cast(dict[str, object], decoded)
    if _canonical_json(result) != value:
        raise _corrupt()
    return result


def _required_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProjectOperationError("corrupt_project", f"{label}: структура повреждена.")
    return cast(dict[str, object], value)


def _validate_snapshot_shapes(input_payload: dict[str, object], result_payload: dict[str, object]) -> None:
    if set(input_payload) != {"schemaVersion", "operation", "source", "fieldSelections", "failureEvidence"}:
        raise _corrupt()
    if input_payload["schemaVersion"] != 1:
        raise _corrupt()
    operation = _required_mapping(input_payload["operation"], "operation")
    if set(operation) != {
        "schemaVersion",
        "analysisInputSnapshotId",
        "calculationSnapshotId",
        "executionId",
        "planSelection",
        "selections",
        "failureEvidence",
        "actor",
        "reason",
        "algorithmId",
        "algorithmVersion",
        "numericPolicy",
    }:
        raise _corrupt()
    source = _required_mapping(input_payload["source"], "source")
    if set(source) != {
        "executionId",
        "localImportId",
        "packageId",
        "runId",
        "exportRevision",
        "outerPackageSha256",
        "sourceSnapshotSha256",
        "producer",
        "planSelection",
        "payloadPath",
        "payloadSha256",
        "planId",
        "planRevision",
        "methodicalRequirements",
        "executionTargets",
    }:
        raise _corrupt()
    fields = input_payload["fieldSelections"]
    if not isinstance(fields, list) or len(cast(list[object], fields)) != len(_FIELD_ORDER):
        raise _corrupt()
    observed_fields: list[object] = []
    for value in cast(list[object], fields):
        field = _required_mapping(value, "field selection")
        if set(field) != {
            "field",
            "unit",
            "origin",
            "value",
            "rawSourceValue",
            "sourceReference",
            "basis",
            "evidence",
        }:
            raise _corrupt()
        observed_fields.append(field["field"])
    if observed_fields != list(_FIELD_ORDER):
        raise _corrupt()
    if set(result_payload) != {
        "algorithm_id",
        "algorithm_version",
        "numeric_policy",
        "maximum_rpm",
        "required_cycles_exact",
        "steady_duration_s_exact",
        "cycle_duration_s_exact",
        "total_duration_s_exact",
        "failure_result",
        "phases",
        "formula_references",
    }:
        raise _corrupt()
    phases = result_payload["phases"]
    if not isinstance(phases, list) or len(cast(list[object], phases)) != 3:
        raise _corrupt()


def _uuid4(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ProjectOperationError("validation_error", "Local ID должен быть canonical UUID v4.") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ProjectOperationError("validation_error", "Local ID должен быть canonical UUID v4.")
    return value


def _sha256(value: str, label: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ProjectOperationError("corrupt_project", f"{label}: SHA-256 повреждён.")
    return value


def _positive_integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise _corrupt()
    return value


def _plan_selection(value: str) -> Literal["original", "effective"]:
    if value == "original":
        return "original"
    if value == "effective":
        return "effective"
    raise _corrupt()


def _algorithm_id(value: str) -> Literal["rbd_reference"]:
    if value != ALGORITHM_ID:
        raise _corrupt()
    return "rbd_reference"


def _algorithm_version(value: str) -> Literal["1.0.0"]:
    if value != ALGORITHM_VERSION:
        raise _corrupt()
    return "1.0.0"


def _numeric_policy(value: str) -> Literal["exact_fraction_v1"]:
    if value != NUMERIC_POLICY:
        raise _corrupt()
    return "exact_fraction_v1"


def _encode_cursor(wheel_model_id: str, snapshot_rowid: int, after_time: str, after_id: str) -> str:
    return (
        urlsafe_b64encode(
            _canonical_json(
                {
                    "v": 1,
                    "kind": "rbdCalculation",
                    "wheelModelId": wheel_model_id,
                    "snapshotRowId": snapshot_rowid,
                    "afterTime": after_time,
                    "afterId": after_id,
                }
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )


def _decode_cursor(cursor: str, wheel_model_id: str) -> tuple[int, str, str]:
    if not cursor or len(cursor) > 512 or not cursor.isascii():
        raise ProjectOperationError("validation_error", "Cursor страницы имеет неверный формат.")
    try:
        decoded: object = json.loads(urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise ProjectOperationError("validation_error", "Cursor страницы имеет неверный формат.") from error
    if not isinstance(decoded, dict):
        raise ProjectOperationError("validation_error", "Cursor страницы имеет неверный формат.")
    payload = cast(dict[str, object], decoded)
    if set(payload) != {"v", "kind", "wheelModelId", "snapshotRowId", "afterTime", "afterId"}:
        raise ProjectOperationError("validation_error", "Cursor страницы имеет неверную структуру.")
    snapshot_rowid = payload["snapshotRowId"]
    if (
        payload["v"] != 1
        or payload["kind"] != "rbdCalculation"
        or payload["wheelModelId"] != wheel_model_id
        or not isinstance(snapshot_rowid, int)
        or isinstance(snapshot_rowid, bool)
        or snapshot_rowid < 0
        or not isinstance(payload["afterTime"], str)
        or not isinstance(payload["afterId"], str)
    ):
        raise ProjectOperationError("validation_error", "Cursor не относится к выбранному списку.")
    return snapshot_rowid, payload["afterTime"], _uuid4(payload["afterId"])


def _corrupt() -> ProjectOperationError:
    return ProjectOperationError("corrupt_project", "Снимки расчёта РБД повреждены.")


def _check_deadline(deadline: RequestDeadline | None, stage: str) -> None:
    if deadline is not None:
        deadline.check(stage)


def _begin_immediate_with_deadline(connection: sqlite3.Connection, deadline: RequestDeadline | None) -> None:
    if deadline is None:
        connection.execute("BEGIN IMMEDIATE")
        return
    stage = "rbd_calculation_write_lock"
    deadline.check(stage)
    previous_timeout = connection.execute("PRAGMA busy_timeout").fetchone()
    if previous_timeout is None:
        raise ProjectOperationError("storage_error", "SQLite busy timeout недоступен.")
    previous_timeout_ms = int(previous_timeout[0])
    remaining_ms = max(1, int(deadline.remaining_seconds(5) * 1000))
    connection.execute(f"PRAGMA busy_timeout = {remaining_ms}")
    acquired = False
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            deadline.check(stage)
            raise ProjectOperationError(
                "storage_error",
                "Невозможно получить блокировку записи проекта.",
                retryable=True,
            ) from error
        acquired = True
        deadline.check(stage)
    except Exception:
        if acquired:
            connection.rollback()
        raise
    finally:
        connection.execute(f"PRAGMA busy_timeout = {previous_timeout_ms}")
