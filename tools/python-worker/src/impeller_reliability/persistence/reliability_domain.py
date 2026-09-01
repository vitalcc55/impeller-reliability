from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Literal, cast
from uuid import UUID, uuid4

from impeller_reliability.persistence.audit import audit_now, insert_audit
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.r130sh_sources import ImportedRunDetail, ImportedRunSummary
from impeller_reliability.worker.deadline import RequestDeadline

LifecycleStatus = Literal["completed", "interrupted", "failed"]


@dataclass(frozen=True, slots=True)
class FailureObservation:
    failure_id: str
    failure_type: Literal["specimen_outcome", "technical_interruption"]
    subject_kind: Literal["specimen", "equipment", "unknown"]
    source_event_reference: str
    source_field_reference: str
    cycles_at_failure: int | None
    duration_s: str | None
    rpm: str | None
    vibration_summary: dict[str, object]
    observed_at_utc: str | None
    source_outer_package_sha256: str


@dataclass(frozen=True, slots=True)
class TestExecution:
    execution_id: str
    local_import_id: str
    local_specimen_id: str
    source_specimen_id: str
    method: Literal["rbd", "rpt", "pmn"]
    lifecycle_status: LifecycleStatus
    planned_parameters_snapshot: dict[str, object]
    result_summary: dict[str, object]
    source_outer_package_sha256: str
    materialized_at_utc: str
    failure_observations: tuple[FailureObservation, ...]


@dataclass(frozen=True, slots=True)
class ReliabilityDataset:
    dataset_id: str
    life_metric_unit: Literal["cycles", "hours", "unknown"]
    censoring_policy: Literal["not_classified", "explicit"]
    execution_ids: tuple[str, ...]
    failure_ids: tuple[str, ...]
    created_at_utc: str


class ReliabilityDomainRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def materialize_execution(
        self,
        detail: ImportedRunDetail,
        *,
        source_integrity: str,
        deadline: RequestDeadline | None,
    ) -> TestExecution:
        _check_deadline(deadline, "reliability_materialize")
        if source_integrity != "verified":
            raise ProjectOperationError(
                "validation_error",
                "Для создания аналитического исполнения нужен проверенный imported source.",
            )
        summary = detail.summary
        if summary.local_specimen_id is None:
            raise ProjectOperationError(
                "validation_error",
                "Сначала явно свяжите исходный образец с локальным Specimen.",
            )
        specimen_row = self._connection.execute(
            "SELECT archived_at_utc FROM specimens WHERE specimen_id=?",
            (summary.local_specimen_id,),
        ).fetchone()
        if specimen_row is None:
            raise ProjectOperationError("corrupt_project", "Связанный local Specimen отсутствует.")
        if specimen_row[0] is not None:
            raise ProjectOperationError(
                "entity_archived",
                "Архивный Specimen нельзя использовать для нового TestExecution.",
            )
        existing = self._execution_by_import(summary.local_import_id, deadline)
        if existing is not None:
            return existing

        projection = detail.projection
        method = parse_test_method(summary.mode)
        lifecycle_status = _lifecycle_status(
            summary.technical_status,
            summary.specimen_outcome,
        )
        planned = _planned_snapshot(projection)
        result = _result_summary(projection, summary)
        now = audit_now(self._connection)
        execution_id = str(uuid4())
        observations = _observations(
            lifecycle_status=lifecycle_status,
            result=result,
            source_outer_package_sha256=summary.outer_package_sha256,
        )
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                """
                INSERT INTO reliability_test_executions (
                    execution_id, local_import_id, local_specimen_id, source_specimen_id,
                    method, lifecycle_status, planned_parameters_snapshot_json,
                    result_summary_json, source_outer_package_sha256, source_payload_path,
                    source_field_reference, materialized_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    summary.local_import_id,
                    summary.local_specimen_id,
                    summary.source_specimen_id,
                    method,
                    lifecycle_status,
                    _json(planned),
                    _json(result),
                    summary.outer_package_sha256,
                    "run-summary.json",
                    "#/run_id",
                    now,
                ),
            )
            for observation in observations:
                self._connection.execute(
                    """
                    INSERT INTO failure_observations (
                        failure_id, execution_id, failure_type, subject_kind,
                        source_event_reference, source_field_reference,
                        cycles_at_failure, duration_s, rpm, vibration_summary_json,
                        observed_at_utc, source_outer_package_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        observation.failure_id,
                        execution_id,
                        observation.failure_type,
                        observation.subject_kind,
                        observation.source_event_reference,
                        observation.source_field_reference,
                        observation.cycles_at_failure,
                        observation.duration_s,
                        observation.rpm,
                        _json(observation.vibration_summary),
                        observation.observed_at_utc,
                        observation.source_outer_package_sha256,
                    ),
                )
            insert_audit(
                self._connection,
                event_type="reliability_execution.materialized",
                actor_kind="application",
                occurred_at_utc=now,
                payload={
                    "executionId": execution_id,
                    "localImportId": summary.local_import_id,
                    "localSpecimenId": summary.local_specimen_id,
                    "sourceSpecimenId": summary.source_specimen_id,
                    "method": method,
                    "sourceOuterPackageSha256": summary.outer_package_sha256,
                    "plannedParametersSnapshotSha256": _snapshot_sha256(planned),
                    "resultSummarySha256": _snapshot_sha256(result),
                    "failureObservationIds": [item.failure_id for item in observations],
                    "failureObservationsSha256": _observations_sha256(observations),
                    "snapshotSha256": _execution_snapshot_sha256(
                        execution_id=execution_id,
                        local_import_id=summary.local_import_id,
                        local_specimen_id=summary.local_specimen_id,
                        source_specimen_id=summary.source_specimen_id,
                        method=method,
                        lifecycle_status=lifecycle_status,
                        planned_parameters_snapshot=planned,
                        result_summary=result,
                        source_outer_package_sha256=summary.outer_package_sha256,
                        source_payload_path="run-summary.json",
                        source_field_reference="#/run_id",
                        materialized_at_utc=now,
                        failure_observations_sha256=_observations_sha256(observations),
                    ),
                },
            )
            _check_deadline(deadline, "reliability_materialize_commit")
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            existing = self._execution_by_import(summary.local_import_id, deadline)
            if existing is not None:
                return existing
            raise ProjectOperationError(
                "import_integrity_conflict",
                "Не удалось сохранить TestExecution.",
            ) from error
        except Exception:
            self._connection.rollback()
            raise
        return TestExecution(
            execution_id=execution_id,
            local_import_id=summary.local_import_id,
            local_specimen_id=summary.local_specimen_id,
            source_specimen_id=summary.source_specimen_id,
            method=method,
            lifecycle_status=lifecycle_status,
            planned_parameters_snapshot=planned,
            result_summary=result,
            source_outer_package_sha256=summary.outer_package_sha256,
            materialized_at_utc=now,
            failure_observations=observations,
        )

    def list_by_wheel_model(
        self,
        wheel_model_id: str,
        deadline: RequestDeadline | None,
    ) -> tuple[TestExecution, ...]:
        _check_deadline(deadline, "reliability_list")
        wheel_model_id = _uuid4(wheel_model_id)
        rows = self._connection.execute(
            """
            SELECT e.execution_id, e.local_import_id, e.local_specimen_id,
                   e.source_specimen_id, e.method, e.lifecycle_status,
                   e.planned_parameters_snapshot_json, e.result_summary_json,
                   e.source_outer_package_sha256, e.materialized_at_utc
            FROM reliability_test_executions e
            JOIN specimens s ON s.specimen_id=e.local_specimen_id
            WHERE s.wheel_model_id=?
            ORDER BY e.materialized_at_utc DESC, e.execution_id
            """,
            (wheel_model_id,),
        ).fetchall()
        return tuple(self._execution_from_row(row, deadline) for row in rows)

    def create_dataset(
        self,
        *,
        dataset_id: str,
        life_metric_unit: str,
        censoring_policy: str,
        execution_ids: tuple[str, ...],
        failure_ids: tuple[str, ...],
        deadline: RequestDeadline | None,
    ) -> ReliabilityDataset:
        dataset_id = _uuid4(dataset_id)
        unit = parse_life_metric_unit(life_metric_unit)
        policy = parse_censoring_policy(censoring_policy)
        normalized_executions = _unique_ids(execution_ids, "execution")
        normalized_failures = _unique_ids(failure_ids, "failure")
        if not normalized_executions:
            raise ProjectOperationError("validation_error", "ReliabilityDataset требует хотя бы один TestExecution.")
        now = audit_now(self._connection)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            for execution_id in normalized_executions:
                if self._connection.execute("SELECT 1 FROM reliability_test_executions WHERE execution_id=?", (execution_id,)).fetchone() is None:
                    raise ProjectOperationError("entity_not_found", "TestExecution для dataset не найден.")
            for failure_id in normalized_failures:
                row = self._connection.execute(
                    "SELECT execution_id FROM failure_observations WHERE failure_id=?",
                    (failure_id,),
                ).fetchone()
                if row is None:
                    raise ProjectOperationError("entity_not_found", "FailureObservation для dataset не найден.")
                if str(row[0]) not in normalized_executions:
                    raise ProjectOperationError(
                        "validation_error",
                        "FailureObservation должен принадлежать выбранному TestExecution.",
                    )
            self._connection.execute(
                "INSERT INTO reliability_datasets (dataset_id, life_metric_unit, censoring_policy, created_at_utc) VALUES (?, ?, ?, ?)",
                (dataset_id, unit, policy, now),
            )
            for execution_id in normalized_executions:
                self._connection.execute(
                    "INSERT INTO reliability_dataset_executions (dataset_id, execution_id, censoring_type, inclusion_reason) VALUES (?, ?, 'not_classified', 'Создано без расчётной классификации.')",
                    (dataset_id, execution_id),
                )
            for failure_id in normalized_failures:
                self._connection.execute(
                    "INSERT INTO reliability_dataset_observations (dataset_id, failure_id) VALUES (?, ?)",
                    (dataset_id, failure_id),
                )
            insert_audit(
                self._connection,
                event_type="reliability_dataset.created",
                actor_kind="user",
                occurred_at_utc=now,
                payload={
                    "datasetId": dataset_id,
                    "lifeMetricUnit": unit,
                    "censoringPolicy": policy,
                    "executionIds": sorted(normalized_executions),
                    "failureIds": sorted(normalized_failures),
                    "snapshotSha256": _dataset_snapshot_sha256(
                        dataset_id=dataset_id,
                        life_metric_unit=unit,
                        censoring_policy=policy,
                        created_at_utc=now,
                        execution_memberships=tuple((execution_id, "not_classified", "Создано без расчётной классификации.") for execution_id in normalized_executions),
                        failure_ids=normalized_failures,
                    ),
                },
            )
            _check_deadline(deadline, "reliability_dataset_commit")
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise ProjectOperationError(
                "duplicate_entity",
                "ReliabilityDataset с таким ID уже существует.",
            ) from error
        except Exception:
            self._connection.rollback()
            raise
        return ReliabilityDataset(dataset_id, unit, policy, normalized_executions, normalized_failures, now)

    def _execution_by_import(self, local_import_id: str, deadline: RequestDeadline | None) -> TestExecution | None:
        row = self._connection.execute(
            """
            SELECT execution_id, local_import_id, local_specimen_id, source_specimen_id,
                   method, lifecycle_status, planned_parameters_snapshot_json,
                   result_summary_json, source_outer_package_sha256, materialized_at_utc
            FROM reliability_test_executions WHERE local_import_id=?
            """,
            (local_import_id,),
        ).fetchone()
        return None if row is None else self._execution_from_row(row, deadline)

    def _execution_from_row(self, row: sqlite3.Row, deadline: RequestDeadline | None) -> TestExecution:
        execution_id = _uuid4(str(row[0]))
        observation_rows = self._connection.execute(
            """
            SELECT failure_id, failure_type, subject_kind, source_event_reference,
                   source_field_reference, cycles_at_failure, duration_s, rpm,
                   vibration_summary_json, observed_at_utc, source_outer_package_sha256
            FROM failure_observations WHERE execution_id=? ORDER BY failure_id
            """,
            (execution_id,),
        ).fetchall()
        _check_deadline(deadline, "reliability_list_observations")
        observations = tuple(
            FailureObservation(
                failure_id=_uuid4(str(item[0])),
                failure_type=parse_failure_type(str(item[1])),
                subject_kind=parse_subject_kind(str(item[2])),
                source_event_reference=_bounded_reference(str(item[3])),
                source_field_reference=_bounded_reference(str(item[4])),
                cycles_at_failure=None if item[5] is None else int(item[5]),
                duration_s=None if item[6] is None else str(item[6]),
                rpm=None if item[7] is None else str(item[7]),
                vibration_summary=_json_object(str(item[8])),
                observed_at_utc=None if item[9] is None else str(item[9]),
                source_outer_package_sha256=_sha256(str(item[10])),
            )
            for item in observation_rows
        )
        return TestExecution(
            execution_id=execution_id,
            local_import_id=_uuid4(str(row[1])),
            local_specimen_id=_uuid4(str(row[2])),
            source_specimen_id=_bounded_reference(str(row[3])),
            method=parse_test_method(str(row[4])),
            lifecycle_status=parse_lifecycle_status(str(row[5])),
            planned_parameters_snapshot=_json_object(str(row[6])),
            result_summary=_json_object(str(row[7])),
            source_outer_package_sha256=_sha256(str(row[8])),
            materialized_at_utc=str(row[9]),
            failure_observations=observations,
        )


def validate_reliability_evidence(
    connection: sqlite3.Connection,
    deadline: RequestDeadline | None = None,
) -> None:
    _check_deadline(deadline, "reliability_evidence")
    rows = connection.execute(
        """
        SELECT e.execution_id, e.local_import_id, e.local_specimen_id,
               e.source_specimen_id, e.method, e.lifecycle_status,
               e.planned_parameters_snapshot_json, e.result_summary_json,
               e.source_outer_package_sha256, e.source_payload_path,
               e.source_field_reference, e.materialized_at_utc,
               s.outer_package_sha256
        FROM reliability_test_executions e
        JOIN r130sh_sources s ON s.local_import_id=e.local_import_id
        JOIN specimens sp ON sp.specimen_id=e.local_specimen_id
        """,
    ).fetchall()
    audit_rows = connection.execute(
        "SELECT payload_json FROM project_audit_events WHERE event_type='reliability_execution.materialized'",
    ).fetchall()
    audit_by_execution: dict[str, dict[str, object]] = {}
    for row in audit_rows:
        payload = _json_object(str(row[0]))
        execution_id = _uuid4(_required_string(payload.get("executionId")))
        if execution_id in audit_by_execution:
            raise _corrupt_evidence()
        audit_by_execution[execution_id] = payload
    if len(rows) != len(audit_by_execution):
        raise _corrupt_evidence()
    for row in rows:
        _check_deadline(deadline, "reliability_evidence_execution")
        execution_id = _uuid4(str(row[0]))
        planned = _json_object(str(row[6]))
        result = _json_object(str(row[7]))
        source_sha = _sha256(str(row[8]))
        if source_sha != _sha256(str(row[12])):
            raise _corrupt_evidence()
        audit_payload = audit_by_execution.get(execution_id)
        if audit_payload is None:
            raise _corrupt_evidence()
        if (
            audit_payload.get("localImportId") != str(row[1])
            or audit_payload.get("localSpecimenId") != str(row[2])
            or audit_payload.get("method") != str(row[4])
            or audit_payload.get("sourceOuterPackageSha256") != source_sha
            or audit_payload.get("plannedParametersSnapshotSha256") != _snapshot_sha256(planned)
            or audit_payload.get("resultSummarySha256") != _snapshot_sha256(result)
            or audit_payload.get("failureObservationsSha256") != _execution_observations_sha256(connection, execution_id)
            or audit_payload.get("snapshotSha256")
            != _execution_snapshot_sha256(
                execution_id=execution_id,
                local_import_id=str(row[1]),
                local_specimen_id=str(row[2]),
                source_specimen_id=_bounded_reference(str(row[3])),
                method=str(row[4]),
                lifecycle_status=str(row[5]),
                planned_parameters_snapshot=planned,
                result_summary=result,
                source_outer_package_sha256=source_sha,
                source_payload_path=_bounded_reference(str(row[9])),
                source_field_reference=_bounded_reference(str(row[10])),
                materialized_at_utc=str(row[11]),
                failure_observations_sha256=_execution_observations_sha256(connection, execution_id),
            )
        ):
            raise _corrupt_evidence()
    invalid_membership = connection.execute(
        """
        SELECT 1
        FROM reliability_dataset_observations o
        JOIN failure_observations f ON f.failure_id=o.failure_id
        WHERE NOT EXISTS (
            SELECT 1 FROM reliability_dataset_executions e
            WHERE e.dataset_id=o.dataset_id AND e.execution_id=f.execution_id
        )
        LIMIT 1
        """,
    ).fetchone()
    if invalid_membership is not None:
        raise _corrupt_evidence()
    dataset_rows = connection.execute(
        "SELECT dataset_id, life_metric_unit, censoring_policy, created_at_utc FROM reliability_datasets",
    ).fetchall()
    dataset_audits: dict[str, dict[str, object]] = {}
    for row in connection.execute(
        "SELECT payload_json FROM project_audit_events WHERE event_type='reliability_dataset.created'",
    ).fetchall():
        payload = _json_object(str(row[0]))
        dataset_id = _uuid4(_required_string(payload.get("datasetId")))
        if dataset_id in dataset_audits:
            raise _corrupt_evidence()
        dataset_audits[dataset_id] = payload
    if len(dataset_rows) != len(dataset_audits):
        raise _corrupt_evidence()
    for row in dataset_rows:
        dataset_id = _uuid4(str(row[0]))
        audit_payload = dataset_audits.get(dataset_id)
        if audit_payload is None:
            raise _corrupt_evidence()
        execution_memberships = [
            (str(item[0]), str(item[1]), str(item[2]))
            for item in connection.execute(
                "SELECT execution_id, censoring_type, inclusion_reason FROM reliability_dataset_executions WHERE dataset_id=? ORDER BY execution_id",
                (dataset_id,),
            ).fetchall()
        ]
        failure_ids = [
            str(item[0])
            for item in connection.execute(
                "SELECT failure_id FROM reliability_dataset_observations WHERE dataset_id=? ORDER BY failure_id",
                (dataset_id,),
            ).fetchall()
        ]
        if (
            audit_payload.get("lifeMetricUnit") != str(row[1])
            or audit_payload.get("censoringPolicy") != str(row[2])
            or audit_payload.get("executionIds") != [item[0] for item in execution_memberships]
            or audit_payload.get("failureIds") != failure_ids
            or audit_payload.get("snapshotSha256")
            != _dataset_snapshot_sha256(
                dataset_id=dataset_id,
                life_metric_unit=str(row[1]),
                censoring_policy=str(row[2]),
                created_at_utc=str(row[3]),
                execution_memberships=tuple(execution_memberships),
                failure_ids=tuple(failure_ids),
            )
        ):
            raise _corrupt_evidence()


def _planned_snapshot(projection: dict[str, object]) -> dict[str, object]:
    return {
        "originalPlan": projection["original_plan_summary"],
        "effectivePlan": projection["effective_plan_summary"],
        "sourceReferences": ["plan/original.json", "plan/effective.json"],
    }


def _result_summary(
    projection: dict[str, object],
    summary: ImportedRunSummary,
) -> dict[str, object]:
    return {
        "technicalStatus": summary.technical_status,
        "terminationReason": summary.termination_reason,
        "specimenOutcome": summary.specimen_outcome,
        "runValidity": summary.run_validity,
        "dataCompleteness": summary.data_completeness,
        "startedAtUtc": projection["started_at_utc"],
        "finishedAtUtc": projection["finished_at_utc"],
        "acceptedElapsedS": projection["accepted_elapsed_s"],
        "measurementCount": projection["measurement_count"],
        "acceptedMeasurementCount": projection["accepted_measurement_count"],
    }


def _observations(
    *,
    lifecycle_status: LifecycleStatus,
    result: dict[str, object],
    source_outer_package_sha256: str,
) -> tuple[FailureObservation, ...]:
    duration_s = _nullable_text(result["acceptedElapsedS"])
    observed_at_utc = _nullable_text(result["finishedAtUtc"])
    vibration_summary: dict[str, object] = {
        "sourcePayloadPath": "measurements.csv",
        "available": False,
        "reason": "M04A хранит только fact-level reference; физический ряд остаётся immutable source evidence.",
    }
    observations: list[FailureObservation] = []
    if result["specimenOutcome"] == "failed":
        observations.append(
            FailureObservation(
                failure_id=str(uuid4()),
                failure_type="specimen_outcome",
                subject_kind="specimen",
                source_event_reference="run-summary.json#/laboratory_conclusion",
                source_field_reference="#/specimen_outcome",
                cycles_at_failure=None,
                duration_s=duration_s,
                rpm=None,
                vibration_summary=vibration_summary,
                observed_at_utc=observed_at_utc,
                source_outer_package_sha256=source_outer_package_sha256,
            )
        )
    if result["technicalStatus"] in {"interrupted", "error"}:
        observations.append(
            FailureObservation(
                failure_id=str(uuid4()),
                failure_type="technical_interruption",
                subject_kind="unknown",
                source_event_reference="run-summary.json#/termination_reason",
                source_field_reference="#/technical_status",
                cycles_at_failure=None,
                duration_s=duration_s,
                rpm=None,
                vibration_summary=vibration_summary,
                observed_at_utc=observed_at_utc,
                source_outer_package_sha256=source_outer_package_sha256,
            )
        )
    return tuple(observations)


def _json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _snapshot_sha256(value: dict[str, object]) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _execution_snapshot_sha256(
    *,
    execution_id: str,
    local_import_id: str,
    local_specimen_id: str,
    source_specimen_id: str,
    method: str,
    lifecycle_status: str,
    planned_parameters_snapshot: dict[str, object],
    result_summary: dict[str, object],
    source_outer_package_sha256: str,
    source_payload_path: str,
    source_field_reference: str,
    materialized_at_utc: str,
    failure_observations_sha256: str,
) -> str:
    payload = {
        "executionId": execution_id,
        "localImportId": local_import_id,
        "localSpecimenId": local_specimen_id,
        "sourceSpecimenId": source_specimen_id,
        "method": method,
        "lifecycleStatus": lifecycle_status,
        "plannedParametersSnapshot": planned_parameters_snapshot,
        "resultSummary": result_summary,
        "sourceOuterPackageSha256": source_outer_package_sha256,
        "sourcePayloadPath": source_payload_path,
        "sourceFieldReference": source_field_reference,
        "materializedAtUtc": materialized_at_utc,
        "failureObservationsSha256": failure_observations_sha256,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()


def _dataset_snapshot_sha256(
    *,
    dataset_id: str,
    life_metric_unit: str,
    censoring_policy: str,
    created_at_utc: str,
    execution_memberships: tuple[tuple[str, str, str], ...],
    failure_ids: tuple[str, ...],
) -> str:
    payload = {
        "datasetId": dataset_id,
        "lifeMetricUnit": life_metric_unit,
        "censoringPolicy": censoring_policy,
        "createdAtUtc": created_at_utc,
        "executions": [{"executionId": item[0], "censoringType": item[1], "inclusionReason": item[2]} for item in sorted(execution_memberships)],
        "failureIds": sorted(failure_ids),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()


def _observations_sha256(observations: tuple[FailureObservation, ...]) -> str:
    values = [
        {
            "failureId": item.failure_id,
            "failureType": item.failure_type,
            "subjectKind": item.subject_kind,
            "sourceEventReference": item.source_event_reference,
            "sourceFieldReference": item.source_field_reference,
            "cyclesAtFailure": item.cycles_at_failure,
            "durationS": item.duration_s,
            "rpm": item.rpm,
            "vibrationSummary": item.vibration_summary,
            "observedAtUtc": item.observed_at_utc,
            "sourceOuterPackageSha256": item.source_outer_package_sha256,
        }
        for item in observations
    ]
    serialized = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _execution_observations_sha256(connection: sqlite3.Connection, execution_id: str) -> str:
    rows = connection.execute(
        """
        SELECT failure_id, failure_type, subject_kind, source_event_reference,
               source_field_reference, cycles_at_failure, duration_s, rpm,
               vibration_summary_json, observed_at_utc, source_outer_package_sha256
        FROM failure_observations WHERE execution_id=? ORDER BY failure_id
        """,
        (execution_id,),
    ).fetchall()
    observations = tuple(
        FailureObservation(
            failure_id=_uuid4(str(row[0])),
            failure_type=parse_failure_type(str(row[1])),
            subject_kind=parse_subject_kind(str(row[2])),
            source_event_reference=_bounded_reference(str(row[3])),
            source_field_reference=_bounded_reference(str(row[4])),
            cycles_at_failure=None if row[5] is None else int(row[5]),
            duration_s=None if row[6] is None else str(row[6]),
            rpm=None if row[7] is None else str(row[7]),
            vibration_summary=_json_object(str(row[8])),
            observed_at_utc=None if row[9] is None else str(row[9]),
            source_outer_package_sha256=_sha256(str(row[10])),
        )
        for row in rows
    )
    return _observations_sha256(observations)


def _json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ProjectOperationError("corrupt_project", "Derived analytical snapshot повреждён.") from error
    if not isinstance(parsed, dict):
        raise ProjectOperationError("corrupt_project", "Derived analytical snapshot повреждён.")
    return cast(dict[str, object], parsed)


def _required_string(value: object) -> str:
    if not isinstance(value, str):
        raise _corrupt_evidence()
    return value


def _corrupt_evidence() -> ProjectOperationError:
    return ProjectOperationError("corrupt_project", "Derived analytical evidence повреждён.")


def _lifecycle_status(technical_status: str | None, specimen_outcome: str | None) -> LifecycleStatus:
    if specimen_outcome == "failed":
        return "failed"
    return "completed" if technical_status == "completed" else "interrupted"


def parse_lifecycle_status(value: str) -> LifecycleStatus:
    if value == "completed":
        return "completed"
    if value == "interrupted":
        return "interrupted"
    if value == "failed":
        return "failed"
    raise ProjectOperationError("corrupt_project", "Lifecycle status TestExecution не поддерживается.")


def parse_test_method(value: str) -> Literal["rbd", "rpt", "pmn"]:
    if value == "rbd":
        return "rbd"
    if value == "rpt":
        return "rpt"
    if value == "pmn":
        return "pmn"
    raise ProjectOperationError("corrupt_project", "Метод TestExecution не поддерживается.")


def parse_failure_type(value: str) -> Literal["specimen_outcome", "technical_interruption"]:
    if value == "specimen_outcome":
        return "specimen_outcome"
    if value == "technical_interruption":
        return "technical_interruption"
    raise ProjectOperationError("corrupt_project", "FailureObservation type не поддерживается.")


def parse_subject_kind(value: str) -> Literal["specimen", "equipment", "unknown"]:
    if value == "specimen":
        return "specimen"
    if value == "equipment":
        return "equipment"
    if value == "unknown":
        return "unknown"
    raise ProjectOperationError("corrupt_project", "FailureObservation subject не поддерживается.")


def parse_life_metric_unit(value: str) -> Literal["cycles", "hours", "unknown"]:
    if value == "cycles":
        return "cycles"
    if value == "hours":
        return "hours"
    if value == "unknown":
        return "unknown"
    raise ProjectOperationError("validation_error", "Единица ReliabilityDataset не поддерживается.")


def parse_censoring_policy(value: str) -> Literal["not_classified", "explicit"]:
    if value == "not_classified":
        return "not_classified"
    if value == "explicit":
        return "explicit"
    raise ProjectOperationError("validation_error", "Censoring policy ReliabilityDataset не поддерживается.")


def _unique_ids(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    result = tuple(_uuid4(value) for value in values)
    if len(set(result)) != len(result):
        raise ProjectOperationError("validation_error", f"Повторный {label} ID в ReliabilityDataset не допускается.")
    return result


def _uuid4(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ProjectOperationError("validation_error", "Local ID должен быть canonical UUID v4.") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ProjectOperationError("validation_error", "Local ID должен быть canonical UUID v4.")
    return value


def _bounded_reference(value: str) -> str:
    if not value or len(value.encode("utf-8")) > 512 or any(ord(char) < 32 for char in value):
        raise ProjectOperationError("corrupt_project", "Source reference повреждён.")
    return value


def _sha256(value: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ProjectOperationError("corrupt_project", "Source SHA-256 повреждён.")
    return value


def _nullable_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _check_deadline(deadline: RequestDeadline | None, stage: str) -> None:
    if deadline is not None:
        deadline.check(stage)
