from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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
    wheel_model_id: str
    source_specimen_id: str
    source_run_id: str
    export_revision: int
    package_kind: Literal["final", "diagnostic_partial"]
    method: Literal["rbd", "rpt", "pmn"]
    lifecycle_status: LifecycleStatus
    planned_parameters_snapshot: dict[str, object]
    result_summary: dict[str, object]
    source_outer_package_sha256: str
    materialized_at_utc: str
    failure_observations: tuple[FailureObservation, ...]


@dataclass(frozen=True, slots=True)
class ReliabilityExecutionSummary:
    execution_id: str
    local_specimen_id: str
    source_specimen_id: str
    source_run_id: str
    export_revision: int
    package_kind: Literal["final", "diagnostic_partial"]
    method: Literal["rbd", "rpt", "pmn"]
    lifecycle_status: LifecycleStatus
    technical_status: str | None
    specimen_outcome: str | None
    run_validity: str | None
    data_completeness: str | None
    materialized_at_utc: str
    failure_observation_count: int
    current_observation_version_id: str | None
    current_observation_version_number: int | None
    current_classification: Literal["failure", "right_censored", "withdrawn", "invalid"] | None


@dataclass(frozen=True, slots=True)
class ReliabilityExecutionPage:
    items: tuple[ReliabilityExecutionSummary, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class AnalystDocumentSnapshot:
    document_id: str
    document_kind: str
    title: str
    designation: str
    revision_label: str
    record_revision: int
    managed_file_sha256: str | None


@dataclass(frozen=True, slots=True)
class ReliabilityObservationVersion:
    observation_id: str
    observation_version_id: str
    execution_id: str
    version_number: int
    previous_version_id: str | None
    classification: Literal["failure", "right_censored", "withdrawn", "invalid"]
    endpoint_kind: Literal["exact", "right_bound", "interval", "unavailable"]
    metric_kind: Literal["rbd_steady_rotation_time", "rpt_start_stop_cycles"] | None
    metric_unit: Literal["hours", "count"] | None
    metric_origin: Literal["analyst_provided"] | None
    lower_value: str | None
    upper_value: str | None
    origin_basis: str
    endpoint_basis: str
    document_snapshot: AnalystDocumentSnapshot
    document_locator: str
    failure_ids: tuple[str, ...]
    actor: str
    decision_reason: str
    created_at_utc: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ReliabilityObservationWriteResult:
    disposition: Literal["created", "existing"]
    version: ReliabilityObservationVersion


@dataclass(frozen=True, slots=True)
class ReliabilityDatasetMember:
    observation_version_id: str
    execution_id: str
    local_specimen_id: str
    source_run_id: str
    policy_eligibility: Literal["eligible", "ineligible"]
    policy_reason: str
    decision: Literal["included", "excluded"]
    inclusion_reason: str


@dataclass(frozen=True, slots=True)
class ReliabilityDatasetVersion:
    dataset_id: str
    dataset_version_id: str
    wheel_model_id: str
    version_number: int
    previous_version_id: str | None
    policy_id: Literal["life_metric_exact_v1"]
    title: str
    method: Literal["rbd", "rpt"]
    metric_kind: Literal["rbd_steady_rotation_time", "rpt_start_stop_cycles"]
    metric_unit: Literal["hours", "count"]
    population_basis: str
    methodology_basis: str
    comparability_basis: str
    members: tuple[ReliabilityDatasetMember, ...]
    actor: str
    decision_reason: str
    created_at_utc: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ReliabilityDatasetWriteResult:
    disposition: Literal["created", "existing"]
    version: ReliabilityDatasetVersion


@dataclass(frozen=True, slots=True)
class ReliabilityDatasetSummary:
    dataset_id: str
    wheel_model_id: str
    latest_version_id: str
    latest_version_number: int
    title: str
    method: Literal["rbd", "rpt"]
    metric_kind: Literal["rbd_steady_rotation_time", "rpt_start_stop_cycles"]
    metric_unit: Literal["hours", "count"]
    included_count: int
    excluded_count: int
    created_at_utc: str


@dataclass(frozen=True, slots=True)
class ReliabilityDatasetPage:
    items: tuple[ReliabilityDatasetSummary, ...]
    next_cursor: str | None


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
        summary = detail.summary
        existing = self._execution_by_import(summary.local_import_id, deadline)
        if existing is not None:
            return existing
        if source_integrity != "verified":
            raise ProjectOperationError(
                "validation_error",
                "Для создания аналитического исполнения нужен проверенный imported source.",
            )
        if summary.local_specimen_id is None:
            raise ProjectOperationError(
                "validation_error",
                "Сначала явно свяжите исходный образец с локальным Specimen.",
            )
        specimen_row = self._connection.execute(
            "SELECT archived_at_utc, wheel_model_id FROM specimens WHERE specimen_id=?",
            (summary.local_specimen_id,),
        ).fetchone()
        if specimen_row is None:
            raise ProjectOperationError("corrupt_project", "Связанный local Specimen отсутствует.")
        if specimen_row[0] is not None:
            raise ProjectOperationError(
                "entity_archived",
                "Архивный Specimen нельзя использовать для нового TestExecution.",
            )
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
                    execution_id, local_import_id, local_specimen_id, wheel_model_id, source_specimen_id,
                    method, lifecycle_status, planned_parameters_snapshot_json,
                    result_summary_json, source_outer_package_sha256, source_payload_path,
                    source_field_reference, materialized_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    summary.local_import_id,
                    summary.local_specimen_id,
                    str(specimen_row[1]),
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
                    "wheelModelId": str(specimen_row[1]),
                    "sourceSpecimenId": summary.source_specimen_id,
                    "method": method,
                    "sourceOuterPackageSha256": summary.outer_package_sha256,
                    "plannedParametersSnapshotSha256": _snapshot_sha256(planned),
                    "resultSummarySha256": _snapshot_sha256(result),
                    "failureObservationIds": sorted(item.failure_id for item in observations),
                    "failureObservationsSha256": observations_sha256(observations),
                    "snapshotSha256": _execution_snapshot_sha256(
                        execution_id=execution_id,
                        local_import_id=summary.local_import_id,
                        local_specimen_id=summary.local_specimen_id,
                        wheel_model_id=str(specimen_row[1]),
                        source_specimen_id=summary.source_specimen_id,
                        method=method,
                        lifecycle_status=lifecycle_status,
                        planned_parameters_snapshot=planned,
                        result_summary=result,
                        source_outer_package_sha256=summary.outer_package_sha256,
                        source_payload_path="run-summary.json",
                        source_field_reference="#/run_id",
                        materialized_at_utc=now,
                        failure_observations_sha256=observations_sha256(observations),
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
            wheel_model_id=str(specimen_row[1]),
            source_specimen_id=summary.source_specimen_id,
            source_run_id=summary.run_id,
            export_revision=summary.export_revision,
            package_kind=cast(Literal["final", "diagnostic_partial"], summary.package_kind),
            method=method,
            lifecycle_status=lifecycle_status,
            planned_parameters_snapshot=planned,
            result_summary=result,
            source_outer_package_sha256=summary.outer_package_sha256,
            materialized_at_utc=now,
            failure_observations=observations,
        )

    def list_execution_page(
        self,
        wheel_model_id: str,
        cursor: str | None,
        limit: int,
        deadline: RequestDeadline | None,
    ) -> ReliabilityExecutionPage:
        _check_deadline(deadline, "reliability_list")
        wheel_model_id = _uuid4(wheel_model_id)
        if not 1 <= limit <= 50:
            raise ProjectOperationError("validation_error", "Размер страницы должен быть от 1 до 50.")
        if cursor is None:
            maximum_row = self._connection.execute(
                """
                SELECT max(e.rowid)
                FROM reliability_test_executions e
                WHERE e.wheel_model_id=?
                """,
                (wheel_model_id,),
            ).fetchone()
            snapshot_rowid = 0 if maximum_row is None or maximum_row[0] is None else int(maximum_row[0])
            after_time = None
            after_id = None
        else:
            snapshot_rowid, after_time, after_id = _decode_execution_cursor(cursor, wheel_model_id)
        clauses = ["e.wheel_model_id=?", "e.rowid<=?"]
        parameters: list[object] = [wheel_model_id, snapshot_rowid]
        if after_time is not None and after_id is not None:
            clauses.append("(e.materialized_at_utc < ? OR (e.materialized_at_utc = ? AND e.execution_id > ?))")
            parameters.extend((after_time, after_time, after_id))
        parameters.append(limit + 1)
        rows = self._connection.execute(
            f"""
            SELECT e.execution_id, e.local_specimen_id, e.source_specimen_id,
                   src.run_id, src.export_revision, src.package_kind, e.method,
                   e.lifecycle_status, p.technical_status, p.specimen_outcome,
                   p.run_validity, p.data_completeness, e.materialized_at_utc,
                   (SELECT count(*) FROM failure_observations f WHERE f.execution_id=e.execution_id),
                   (SELECT v.observation_version_id
                      FROM reliability_observations o
                      JOIN reliability_observation_versions v ON v.observation_id=o.observation_id
                     WHERE o.execution_id=e.execution_id
                     ORDER BY v.version_number DESC LIMIT 1),
                   (SELECT v.version_number
                      FROM reliability_observations o
                      JOIN reliability_observation_versions v ON v.observation_id=o.observation_id
                     WHERE o.execution_id=e.execution_id
                     ORDER BY v.version_number DESC LIMIT 1),
                   (SELECT v.classification
                      FROM reliability_observations o
                      JOIN reliability_observation_versions v ON v.observation_id=o.observation_id
                     WHERE o.execution_id=e.execution_id
                     ORDER BY v.version_number DESC LIMIT 1)
            FROM reliability_test_executions e
            JOIN r130sh_sources src ON src.local_import_id=e.local_import_id
            JOIN r130sh_run_projections p ON p.local_import_id=e.local_import_id
            WHERE {" AND ".join(clauses)}
            ORDER BY e.materialized_at_utc DESC, e.execution_id ASC
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()
        _check_deadline(deadline, "reliability_list_page")
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = tuple(_execution_summary_from_row(row) for row in visible)
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            next_cursor = _encode_execution_cursor(
                wheel_model_id,
                snapshot_rowid,
                str(last[12]),
                str(last[0]),
            )
        return ReliabilityExecutionPage(items=items, next_cursor=next_cursor)

    def get_execution(self, execution_id: str, deadline: RequestDeadline | None) -> TestExecution:
        execution_id = _uuid4(execution_id)
        row = self._connection.execute(
            """
            SELECT e.execution_id, e.local_import_id, e.local_specimen_id, e.source_specimen_id,
                   e.wheel_model_id, s.run_id, s.export_revision, s.package_kind,
                   e.method, e.lifecycle_status, e.planned_parameters_snapshot_json,
                   e.result_summary_json, e.source_outer_package_sha256, e.materialized_at_utc
            FROM reliability_test_executions e
            JOIN r130sh_sources s ON s.local_import_id=e.local_import_id
            WHERE e.execution_id=?
            """,
            (execution_id,),
        ).fetchone()
        if row is None:
            raise ProjectOperationError("entity_not_found", "TestExecution не найден.")
        return self._execution_from_row(row, deadline)

    def create_observation_version(
        self,
        *,
        observation_id: str,
        observation_version_id: str,
        execution_id: str,
        expected_previous_version_id: str | None,
        classification: str,
        endpoint_kind: str,
        metric_kind: str | None,
        metric_unit: str | None,
        metric_origin: str | None,
        lower_value: str | None,
        upper_value: str | None,
        origin_basis: str,
        endpoint_basis: str,
        document_id: str,
        document_locator: str,
        failure_ids: tuple[str, ...],
        actor: str,
        reason: str,
        deadline: RequestDeadline | None,
    ) -> ReliabilityObservationWriteResult:
        observation_id = _uuid4(observation_id)
        observation_version_id = _uuid4(observation_version_id)
        execution_id = _uuid4(execution_id)
        expected_previous_version_id = None if expected_previous_version_id is None else _uuid4(expected_previous_version_id)
        normalized = normalize_observation_values(
            classification=classification,
            endpoint_kind=endpoint_kind,
            metric_kind=metric_kind,
            metric_unit=metric_unit,
            lower_value=lower_value,
            upper_value=upper_value,
            origin_basis=origin_basis,
            endpoint_basis=endpoint_basis,
            document_locator=document_locator,
            actor=actor,
            reason=reason,
        )
        metric_origin_value = normalize_metric_origin(metric_origin, normalized[2])
        failure_ids = _unique_ids(failure_ids, "failure")
        _check_deadline(deadline, "reliability_observation_prepare")
        existing = self._observation_version_by_id(observation_version_id, deadline)
        if existing is not None:
            _assert_existing_observation_matches(
                existing,
                observation_id=observation_id,
                execution_id=execution_id,
                expected_previous_version_id=expected_previous_version_id,
                normalized=normalized,
                metric_origin=metric_origin_value,
                document_id=document_id,
                failure_ids=failure_ids,
            )
            return ReliabilityObservationWriteResult("existing", existing)
        execution_row = self._connection.execute(
            """
            SELECT e.method, e.local_specimen_id, e.wheel_model_id
            FROM reliability_test_executions e
            WHERE e.execution_id=?
            """,
            (execution_id,),
        ).fetchone()
        if execution_row is None:
            raise ProjectOperationError("entity_not_found", "TestExecution для интерпретации не найден.")
        _validate_metric_for_method(str(execution_row[0]), normalized[2], normalized[3])
        document_snapshot = self._document_snapshot(
            document_id,
            wheel_model_id=str(execution_row[2]),
            specimen_id=str(execution_row[1]),
        )
        for failure_id in failure_ids:
            failure_row = self._connection.execute(
                "SELECT execution_id FROM failure_observations WHERE failure_id=?",
                (failure_id,),
            ).fetchone()
            if failure_row is None:
                raise ProjectOperationError("entity_not_found", "FailureObservation не найден.")
            if str(failure_row[0]) != execution_id:
                raise ProjectOperationError("validation_error", "FailureObservation принадлежит другому исполнению.")
        now = audit_now(self._connection)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            head = self._observation_head(observation_id)
            if head is None:
                if expected_previous_version_id is not None:
                    raise _revision_conflict(expected_previous_version_id, None)
                occupied = self._connection.execute(
                    "SELECT observation_id FROM reliability_observations WHERE execution_id=?",
                    (execution_id,),
                ).fetchone()
                if occupied is not None and str(occupied[0]) != observation_id:
                    raise ProjectOperationError("duplicate_entity", "Для TestExecution уже создана ReliabilityObservation.")
                self._connection.execute(
                    "INSERT INTO reliability_observations (observation_id, execution_id, created_at_utc) VALUES (?, ?, ?)",
                    (observation_id, execution_id, now),
                )
                version_number = 1
            else:
                if head.observation_id != observation_id or head.execution_id != execution_id:
                    raise ProjectOperationError("validation_error", "Observation identity не согласована с TestExecution.")
                if head.observation_version_id != expected_previous_version_id:
                    raise _revision_conflict(expected_previous_version_id, head.observation_version_id)
                version_number = head.version_number + 1
            snapshot_json = _json(_document_snapshot_payload(document_snapshot))
            content_sha256 = _observation_content_sha256(
                observation_id=observation_id,
                observation_version_id=observation_version_id,
                execution_id=execution_id,
                version_number=version_number,
                previous_version_id=expected_previous_version_id,
                normalized=normalized,
                metric_origin=metric_origin_value,
                document_snapshot=document_snapshot,
                document_id=document_id,
                failure_ids=failure_ids,
                created_at_utc=now,
            )
            self._connection.execute(
                """
                INSERT INTO reliability_observation_versions (
                    observation_version_id, observation_id, version_number, previous_version_id,
                    classification, endpoint_kind, metric_kind, metric_unit, metric_origin, lower_value, upper_value,
                    observation_scope, origin_basis, endpoint_basis, document_id, document_locator,
                    document_snapshot_json, actor, decision_reason, created_at_utc, content_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'execution', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_version_id,
                    observation_id,
                    version_number,
                    expected_previous_version_id,
                    *normalized[:4],
                    metric_origin_value,
                    *normalized[4:6],
                    normalized[6],
                    normalized[7],
                    _uuid4(document_id),
                    normalized[8],
                    snapshot_json,
                    normalized[9],
                    normalized[10],
                    now,
                    content_sha256,
                ),
            )
            for failure_id in failure_ids:
                self._connection.execute(
                    "INSERT INTO reliability_observation_failure_refs (observation_version_id, failure_id) VALUES (?, ?)",
                    (observation_version_id, failure_id),
                )
            insert_audit(
                self._connection,
                event_type="reliability_observation.version_created",
                actor_kind="user",
                occurred_at_utc=now,
                payload={
                    "observationId": observation_id,
                    "observationVersionId": observation_version_id,
                    "executionId": execution_id,
                    "versionNumber": version_number,
                    "previousVersionId": expected_previous_version_id,
                    "contentSha256": content_sha256,
                },
            )
            _check_deadline(deadline, "reliability_observation_commit")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        created = self._observation_version_by_id(observation_version_id, deadline)
        if created is None:
            raise ProjectOperationError("storage_error", "Сохранённая интерпретация не найдена.")
        return ReliabilityObservationWriteResult("created", created)

    def list_observation_versions(
        self,
        execution_id: str,
        deadline: RequestDeadline | None,
    ) -> tuple[ReliabilityObservationVersion, ...]:
        execution_id = _uuid4(execution_id)
        rows = self._connection.execute(
            """
            SELECT v.observation_version_id
            FROM reliability_observation_versions v
            JOIN reliability_observations o ON o.observation_id=v.observation_id
            WHERE o.execution_id=?
            ORDER BY v.version_number DESC, v.observation_version_id
            LIMIT 50
            """,
            (execution_id,),
        ).fetchall()
        _check_deadline(deadline, "reliability_observation_list")
        return tuple(self._require_observation_version(str(row[0]), deadline) for row in rows)

    def get_observation_version(
        self,
        observation_version_id: str,
        deadline: RequestDeadline | None,
    ) -> ReliabilityObservationVersion:
        version = self._observation_version_by_id(_uuid4(observation_version_id), deadline)
        if version is None:
            raise ProjectOperationError("entity_not_found", "Версия ReliabilityObservation не найдена.")
        return version

    def create_dataset_version(
        self,
        *,
        dataset_id: str,
        dataset_version_id: str,
        wheel_model_id: str,
        expected_previous_version_id: str | None,
        title: str,
        method: str,
        metric_kind: str,
        metric_unit: str,
        population_basis: str,
        methodology_basis: str,
        comparability_basis: str,
        decisions: tuple[dict[str, object], ...],
        actor: str,
        reason: str,
        deadline: RequestDeadline | None,
    ) -> ReliabilityDatasetWriteResult:
        dataset_id = _uuid4(dataset_id)
        dataset_version_id = _uuid4(dataset_version_id)
        wheel_model_id = _uuid4(wheel_model_id)
        expected_previous_version_id = None if expected_previous_version_id is None else _uuid4(expected_previous_version_id)
        method_value, metric_kind_value, metric_unit_value = _normalize_dataset_metric(method, metric_kind, metric_unit)
        title = _bounded_text(title, 200, "Название выборки")
        population_basis = _bounded_text(population_basis, 2000, "Граница совокупности")
        methodology_basis = _bounded_text(methodology_basis, 2000, "Основание методики")
        comparability_basis = _bounded_text(comparability_basis, 2000, "Основание сопоставимости")
        actor = _bounded_text(actor, 200, "Автор решения")
        reason = _bounded_text(reason, 2000, "Основание версии выборки")
        normalized_decisions = _normalize_dataset_decisions(decisions)
        existing = self._dataset_version_by_id(dataset_version_id, deadline)
        if existing is not None:
            _assert_existing_dataset_matches(
                existing,
                dataset_id=dataset_id,
                wheel_model_id=wheel_model_id,
                expected_previous_version_id=expected_previous_version_id,
                title=title,
                method=method_value,
                metric_kind=metric_kind_value,
                metric_unit=metric_unit_value,
                population_basis=population_basis,
                methodology_basis=methodology_basis,
                comparability_basis=comparability_basis,
                normalized_decisions=normalized_decisions,
                actor=actor,
                reason=reason,
            )
            return ReliabilityDatasetWriteResult("existing", existing)
        if self._connection.execute("SELECT 1 FROM wheel_models WHERE wheel_model_id=?", (wheel_model_id,)).fetchone() is None:
            raise ProjectOperationError("entity_not_found", "WheelModel для выборки не найден.")
        members: list[ReliabilityDatasetMember] = []
        for observation_version_id, decision, inclusion_reason in normalized_decisions:
            observation = self.get_observation_version(observation_version_id, deadline)
            row = self._connection.execute(
                """
                SELECT e.execution_id, e.local_specimen_id, e.method, s.run_id, s.package_kind, e.wheel_model_id
                FROM reliability_observations o
                JOIN reliability_test_executions e ON e.execution_id=o.execution_id
                JOIN r130sh_sources s ON s.local_import_id=e.local_import_id
                WHERE o.observation_id=?
                """,
                (observation.observation_id,),
            ).fetchone()
            if row is None:
                raise ProjectOperationError("corrupt_project", "Связи ReliabilityObservation повреждены.")
            if str(row[5]) != wheel_model_id:
                raise ProjectOperationError("validation_error", "Observation принадлежит другой модели колеса.")
            eligibility, policy_reason = _policy_eligibility(
                observation,
                execution_method=str(row[2]),
                package_kind=str(row[4]),
                dataset_method=method_value,
                dataset_metric_kind=metric_kind_value,
                dataset_metric_unit=metric_unit_value,
            )
            if decision == "included" and eligibility != "eligible":
                raise ProjectOperationError("validation_error", policy_reason)
            members.append(
                ReliabilityDatasetMember(
                    observation_version_id=observation.observation_version_id,
                    execution_id=_uuid4(str(row[0])),
                    local_specimen_id=_uuid4(str(row[1])),
                    source_run_id=_bounded_reference(str(row[3])),
                    policy_eligibility=eligibility,
                    policy_reason=policy_reason,
                    decision=decision,
                    inclusion_reason=inclusion_reason,
                )
            )
        _validate_included_uniqueness(tuple(members))
        now = audit_now(self._connection)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            head = self._dataset_head(dataset_id)
            if head is None:
                if expected_previous_version_id is not None:
                    raise _revision_conflict(expected_previous_version_id, None)
                self._connection.execute(
                    "INSERT INTO reliability_datasets (dataset_id, wheel_model_id, created_at_utc) VALUES (?, ?, ?)",
                    (dataset_id, wheel_model_id, now),
                )
                version_number = 1
            else:
                if head.wheel_model_id != wheel_model_id:
                    raise ProjectOperationError("validation_error", "WheelModel dataset нельзя изменить.")
                if head.dataset_version_id != expected_previous_version_id:
                    raise _revision_conflict(expected_previous_version_id, head.dataset_version_id)
                version_number = head.version_number + 1
            content_sha256 = _dataset_version_content_sha256(
                dataset_id=dataset_id,
                dataset_version_id=dataset_version_id,
                wheel_model_id=wheel_model_id,
                version_number=version_number,
                previous_version_id=expected_previous_version_id,
                title=title,
                method=method_value,
                metric_kind=metric_kind_value,
                metric_unit=metric_unit_value,
                population_basis=population_basis,
                methodology_basis=methodology_basis,
                comparability_basis=comparability_basis,
                members=tuple(members),
                actor=actor,
                reason=reason,
                created_at_utc=now,
            )
            self._connection.execute(
                """
                INSERT INTO reliability_dataset_versions (
                    dataset_version_id, dataset_id, version_number, previous_version_id,
                    policy_id, title, method, metric_kind, metric_unit, population_basis,
                    methodology_basis, comparability_basis, actor, decision_reason,
                    created_at_utc, content_sha256
                ) VALUES (?, ?, ?, ?, 'life_metric_exact_v1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_version_id,
                    dataset_id,
                    version_number,
                    expected_previous_version_id,
                    title,
                    method_value,
                    metric_kind_value,
                    metric_unit_value,
                    population_basis,
                    methodology_basis,
                    comparability_basis,
                    actor,
                    reason,
                    now,
                    content_sha256,
                ),
            )
            for member in members:
                self._connection.execute(
                    """
                    INSERT INTO reliability_dataset_members (
                        dataset_version_id, observation_version_id, execution_id,
                        local_specimen_id, source_run_id, policy_eligibility, policy_reason,
                        inclusion_decision, inclusion_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dataset_version_id,
                        member.observation_version_id,
                        member.execution_id,
                        member.local_specimen_id,
                        member.source_run_id,
                        member.policy_eligibility,
                        member.policy_reason,
                        member.decision,
                        member.inclusion_reason,
                    ),
                )
            insert_audit(
                self._connection,
                event_type="reliability_dataset.version_created",
                actor_kind="user",
                occurred_at_utc=now,
                payload={
                    "datasetId": dataset_id,
                    "datasetVersionId": dataset_version_id,
                    "versionNumber": version_number,
                    "previousVersionId": expected_previous_version_id,
                    "contentSha256": content_sha256,
                },
            )
            _check_deadline(deadline, "reliability_dataset_commit")
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise ProjectOperationError("validation_error", "Состав ReliabilityDataset нарушает ограничение уникальности.") from error
        except Exception:
            self._connection.rollback()
            raise
        created = self._dataset_version_by_id(dataset_version_id, deadline)
        if created is None:
            raise ProjectOperationError("storage_error", "Сохранённая версия выборки не найдена.")
        return ReliabilityDatasetWriteResult("created", created)

    def get_dataset_version(
        self,
        dataset_version_id: str,
        deadline: RequestDeadline | None,
    ) -> ReliabilityDatasetVersion:
        version = self._dataset_version_by_id(_uuid4(dataset_version_id), deadline)
        if version is None:
            raise ProjectOperationError("entity_not_found", "Версия ReliabilityDataset не найдена.")
        return version

    def list_dataset_page(
        self,
        wheel_model_id: str,
        cursor: str | None,
        limit: int,
        deadline: RequestDeadline | None,
    ) -> ReliabilityDatasetPage:
        wheel_model_id = _uuid4(wheel_model_id)
        if not 1 <= limit <= 50:
            raise ProjectOperationError("validation_error", "Размер страницы должен быть от 1 до 50.")
        if cursor is None:
            maximum_row = self._connection.execute(
                "SELECT max(rowid) FROM reliability_datasets WHERE wheel_model_id=?",
                (wheel_model_id,),
            ).fetchone()
            snapshot_rowid = 0 if maximum_row is None or maximum_row[0] is None else int(maximum_row[0])
            after_time = None
            after_id = None
        else:
            snapshot_rowid, after_time, after_id = _decode_page_cursor(cursor, wheel_model_id, "dataset")
        clauses = ["d.wheel_model_id=?", "d.rowid<=?"]
        parameters: list[object] = [wheel_model_id, snapshot_rowid]
        if after_time is not None and after_id is not None:
            clauses.append("(d.created_at_utc < ? OR (d.created_at_utc = ? AND d.dataset_id > ?))")
            parameters.extend((after_time, after_time, after_id))
        parameters.append(limit + 1)
        rows = self._connection.execute(
            f"""
            SELECT d.dataset_id, d.wheel_model_id, v.dataset_version_id,
                   v.version_number, v.title, v.method, v.metric_kind, v.metric_unit,
                   (SELECT count(*) FROM reliability_dataset_members m
                     WHERE m.dataset_version_id=v.dataset_version_id AND m.inclusion_decision='included'),
                   (SELECT count(*) FROM reliability_dataset_members m
                     WHERE m.dataset_version_id=v.dataset_version_id AND m.inclusion_decision='excluded'),
                   d.created_at_utc
            FROM reliability_datasets d
            JOIN reliability_dataset_versions v ON v.dataset_id=d.dataset_id
            WHERE {" AND ".join(clauses)}
              AND v.version_number=(SELECT max(v2.version_number) FROM reliability_dataset_versions v2 WHERE v2.dataset_id=d.dataset_id)
            ORDER BY d.created_at_utc DESC, d.dataset_id ASC
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()
        _check_deadline(deadline, "reliability_dataset_list_page")
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = tuple(
            ReliabilityDatasetSummary(
                dataset_id=_uuid4(str(row[0])),
                wheel_model_id=_uuid4(str(row[1])),
                latest_version_id=_uuid4(str(row[2])),
                latest_version_number=int(row[3]),
                title=str(row[4]),
                method=parse_dataset_method(str(row[5])),
                metric_kind=parse_metric_kind(str(row[6])),
                metric_unit=parse_metric_unit(str(row[7])),
                included_count=int(row[8]),
                excluded_count=int(row[9]),
                created_at_utc=str(row[10]),
            )
            for row in visible
        )
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            next_cursor = _encode_page_cursor(wheel_model_id, snapshot_rowid, str(last[10]), str(last[0]), "dataset")
        return ReliabilityDatasetPage(items=items, next_cursor=next_cursor)

    def _execution_by_import(self, local_import_id: str, deadline: RequestDeadline | None) -> TestExecution | None:
        row = self._connection.execute(
            """
            SELECT e.execution_id, e.local_import_id, e.local_specimen_id, e.source_specimen_id,
                   e.wheel_model_id, s.run_id, s.export_revision, s.package_kind,
                   e.method, e.lifecycle_status, e.planned_parameters_snapshot_json,
                   e.result_summary_json, e.source_outer_package_sha256, e.materialized_at_utc
            FROM reliability_test_executions e
            JOIN r130sh_sources s ON s.local_import_id=e.local_import_id
            WHERE e.local_import_id=?
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
            LIMIT 65
            """,
            (execution_id,),
        ).fetchall()
        if len(observation_rows) > 64:
            raise _corrupt_evidence()
        _check_deadline(deadline, "reliability_list_observations")
        observations = tuple(
            FailureObservation(
                failure_id=_uuid4(str(item[0])),
                failure_type=parse_failure_type(str(item[1])),
                subject_kind=parse_subject_kind(str(item[2])),
                source_event_reference=_bounded_reference(str(item[3])),
                source_field_reference=_bounded_reference(str(item[4])),
                cycles_at_failure=None if item[5] is None else _stored_integer(item[5]),
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
            wheel_model_id=_uuid4(str(row[4])),
            source_specimen_id=_bounded_reference(str(row[3])),
            source_run_id=_bounded_reference(str(row[5])),
            export_revision=_stored_integer(row[6], minimum=1),
            package_kind=cast(Literal["final", "diagnostic_partial"], str(row[7])),
            method=parse_test_method(str(row[8])),
            lifecycle_status=parse_lifecycle_status(str(row[9])),
            planned_parameters_snapshot=_json_object(str(row[10])),
            result_summary=_json_object(str(row[11])),
            source_outer_package_sha256=_sha256(str(row[12])),
            materialized_at_utc=str(row[13]),
            failure_observations=observations,
        )

    def _document_snapshot(
        self,
        document_id: str,
        *,
        wheel_model_id: str,
        specimen_id: str,
    ) -> AnalystDocumentSnapshot:
        document_id = _uuid4(document_id)
        row = self._connection.execute(
            """
            SELECT d.document_kind, d.title, d.designation, d.revision_label,
                   d.record_revision, d.archived_at_utc, f.sha256
            FROM case_documents d
            LEFT JOIN case_document_files f ON f.case_document_id=d.case_document_id
            WHERE d.case_document_id=?
            """,
            (document_id,),
        ).fetchone()
        if row is None:
            raise ProjectOperationError("entity_not_found", "Документ-основание не найден.")
        if row[5] is not None:
            raise ProjectOperationError("entity_archived", "Архивный документ нельзя выбрать для нового решения.")
        link_counts = self._connection.execute(
            """
            SELECT
              (SELECT count(*) FROM case_document_wheel_models WHERE case_document_id=?),
              (SELECT count(*) FROM case_document_specimens WHERE case_document_id=?),
              EXISTS(SELECT 1 FROM case_document_wheel_models WHERE case_document_id=? AND wheel_model_id=?),
              EXISTS(SELECT 1 FROM case_document_specimens WHERE case_document_id=? AND specimen_id=?)
            """,
            (document_id, document_id, document_id, wheel_model_id, document_id, specimen_id),
        ).fetchone()
        if link_counts is None or (int(link_counts[0]) + int(link_counts[1]) > 0 and not bool(link_counts[2]) and not bool(link_counts[3])):
            raise ProjectOperationError("validation_error", "Документ не относится к выбранному исполнению.")
        return AnalystDocumentSnapshot(
            document_id=document_id,
            document_kind=_bounded_text(str(row[0]), 100, "Вид документа"),
            title=_bounded_text(str(row[1]), 300, "Название документа"),
            designation=_bounded_optional_text(str(row[2]), 200, "Обозначение документа"),
            revision_label=_bounded_optional_text(str(row[3]), 200, "Редакция документа"),
            record_revision=int(row[4]),
            managed_file_sha256=None if row[6] is None else _sha256(str(row[6])),
        )

    def _observation_head(self, observation_id: str) -> ReliabilityObservationVersion | None:
        row = self._connection.execute(
            "SELECT observation_version_id FROM reliability_observation_versions WHERE observation_id=? ORDER BY version_number DESC LIMIT 1",
            (observation_id,),
        ).fetchone()
        return None if row is None else self._observation_version_by_id(str(row[0]), None)

    def _require_observation_version(
        self,
        observation_version_id: str,
        deadline: RequestDeadline | None,
    ) -> ReliabilityObservationVersion:
        version = self._observation_version_by_id(observation_version_id, deadline)
        if version is None:
            raise ProjectOperationError("corrupt_project", "Версия ReliabilityObservation отсутствует.")
        return version

    def _observation_version_by_id(
        self,
        observation_version_id: str,
        deadline: RequestDeadline | None,
    ) -> ReliabilityObservationVersion | None:
        row = self._connection.execute(
            """
            SELECT v.observation_id, v.observation_version_id, o.execution_id,
                   v.version_number, v.previous_version_id, v.classification,
                   v.endpoint_kind, v.metric_kind, v.metric_unit, v.metric_origin, v.lower_value,
                   v.upper_value, v.origin_basis, v.endpoint_basis, v.document_id,
                   v.document_locator, v.document_snapshot_json, v.actor,
                   v.decision_reason, v.created_at_utc, v.content_sha256
            FROM reliability_observation_versions v
            JOIN reliability_observations o ON o.observation_id=v.observation_id
            WHERE v.observation_version_id=?
            """,
            (observation_version_id,),
        ).fetchone()
        if row is None:
            return None
        failure_rows = self._connection.execute(
            "SELECT failure_id FROM reliability_observation_failure_refs WHERE observation_version_id=? ORDER BY failure_id LIMIT 65",
            (observation_version_id,),
        ).fetchall()
        if len(failure_rows) > 64:
            raise _corrupt_evidence()
        _check_deadline(deadline, "reliability_observation_read")
        snapshot = _parse_document_snapshot(_json_object(str(row[16])))
        persisted_document_id = _uuid4(str(row[14]))
        if persisted_document_id != snapshot.document_id:
            raise _corrupt_evidence()
        return ReliabilityObservationVersion(
            observation_id=_uuid4(str(row[0])),
            observation_version_id=_uuid4(str(row[1])),
            execution_id=_uuid4(str(row[2])),
            version_number=_stored_integer(row[3], minimum=1),
            previous_version_id=None if row[4] is None else _uuid4(str(row[4])),
            classification=parse_classification(str(row[5])),
            endpoint_kind=parse_endpoint_kind(str(row[6])),
            metric_kind=None if row[7] is None else parse_metric_kind(str(row[7])),
            metric_unit=None if row[8] is None else parse_metric_unit(str(row[8])),
            metric_origin=None if row[9] is None else parse_metric_origin(str(row[9])),
            lower_value=None if row[10] is None else str(row[10]),
            upper_value=None if row[11] is None else str(row[11]),
            origin_basis=str(row[12]),
            endpoint_basis=str(row[13]),
            document_snapshot=snapshot,
            document_locator=str(row[15]),
            failure_ids=tuple(_uuid4(str(item[0])) for item in failure_rows),
            actor=str(row[17]),
            decision_reason=str(row[18]),
            created_at_utc=str(row[19]),
            content_sha256=_sha256(str(row[20])),
        )

    def _dataset_head(self, dataset_id: str) -> ReliabilityDatasetVersion | None:
        row = self._connection.execute(
            "SELECT dataset_version_id FROM reliability_dataset_versions WHERE dataset_id=? ORDER BY version_number DESC LIMIT 1",
            (dataset_id,),
        ).fetchone()
        return None if row is None else self._dataset_version_by_id(str(row[0]), None)

    def _dataset_version_by_id(
        self,
        dataset_version_id: str,
        deadline: RequestDeadline | None,
    ) -> ReliabilityDatasetVersion | None:
        row = self._connection.execute(
            """
            SELECT v.dataset_id, v.dataset_version_id, d.wheel_model_id,
                   v.version_number, v.previous_version_id, v.policy_id, v.title,
                   v.method, v.metric_kind, v.metric_unit, v.population_basis,
                   v.methodology_basis, v.comparability_basis, v.actor,
                   v.decision_reason, v.created_at_utc, v.content_sha256
            FROM reliability_dataset_versions v
            JOIN reliability_datasets d ON d.dataset_id=v.dataset_id
            WHERE v.dataset_version_id=?
            """,
            (dataset_version_id,),
        ).fetchone()
        if row is None:
            return None
        member_rows = self._connection.execute(
            """
            SELECT observation_version_id, execution_id, local_specimen_id,
                   source_run_id, policy_eligibility, policy_reason,
                   inclusion_decision, inclusion_reason
            FROM reliability_dataset_members
            WHERE dataset_version_id=?
            ORDER BY observation_version_id
            LIMIT 101
            """,
            (dataset_version_id,),
        ).fetchall()
        if len(member_rows) > 100:
            raise _corrupt_evidence()
        _check_deadline(deadline, "reliability_dataset_read")
        members = tuple(
            ReliabilityDatasetMember(
                observation_version_id=_uuid4(str(item[0])),
                execution_id=_uuid4(str(item[1])),
                local_specimen_id=_uuid4(str(item[2])),
                source_run_id=_bounded_reference(str(item[3])),
                policy_eligibility=parse_policy_eligibility(str(item[4])),
                policy_reason=str(item[5]),
                decision=parse_inclusion_decision(str(item[6])),
                inclusion_reason=str(item[7]),
            )
            for item in member_rows
        )
        return ReliabilityDatasetVersion(
            dataset_id=_uuid4(str(row[0])),
            dataset_version_id=_uuid4(str(row[1])),
            wheel_model_id=_uuid4(str(row[2])),
            version_number=_stored_integer(row[3], minimum=1),
            previous_version_id=None if row[4] is None else _uuid4(str(row[4])),
            policy_id="life_metric_exact_v1",
            title=str(row[6]),
            method=parse_dataset_method(str(row[7])),
            metric_kind=parse_metric_kind(str(row[8])),
            metric_unit=parse_metric_unit(str(row[9])),
            population_basis=str(row[10]),
            methodology_basis=str(row[11]),
            comparability_basis=str(row[12]),
            members=members,
            actor=str(row[13]),
            decision_reason=str(row[14]),
            created_at_utc=str(row[15]),
            content_sha256=_sha256(str(row[16])),
        )


def validate_reliability_evidence(
    connection: sqlite3.Connection,
    deadline: RequestDeadline | None = None,
) -> None:
    try:
        _validate_reliability_evidence(connection, deadline)
    except ProjectOperationError as error:
        if error.code == "timeout":
            raise
        raise _corrupt_evidence() from error
    except (TypeError, ValueError, OverflowError) as error:
        raise _corrupt_evidence() from error


def _validate_reliability_evidence(
    connection: sqlite3.Connection,
    deadline: RequestDeadline | None = None,
) -> None:
    _check_deadline(deadline, "reliability_evidence")
    rows = connection.execute(
        """
        SELECT e.execution_id, e.local_import_id, e.local_specimen_id,
               e.wheel_model_id, e.source_specimen_id, e.method, e.lifecycle_status,
               e.planned_parameters_snapshot_json, e.result_summary_json,
               e.source_outer_package_sha256, e.source_payload_path,
               e.source_field_reference, e.materialized_at_utc,
               s.outer_package_sha256, sp.wheel_model_id
        FROM reliability_test_executions e
        JOIN r130sh_sources s ON s.local_import_id=e.local_import_id
        JOIN specimens sp ON sp.specimen_id=e.local_specimen_id
        """,
    ).fetchall()
    audit_rows = connection.execute(
        "SELECT payload_json, actor_kind, occurred_at_utc FROM project_audit_events WHERE event_type='reliability_execution.materialized'",
    ).fetchall()
    audit_by_execution: dict[str, dict[str, object]] = {}
    for row in audit_rows:
        payload = _json_object(str(row[0]))
        execution_id = _uuid4(_required_string(payload.get("executionId")))
        if execution_id in audit_by_execution:
            raise _corrupt_evidence()
        audit_by_execution[execution_id] = {
            **payload,
            "__auditActorKind": str(row[1]),
            "__auditOccurredAtUtc": str(row[2]),
        }
    if len(rows) != len(audit_by_execution):
        raise _corrupt_evidence()
    for row in rows:
        _check_deadline(deadline, "reliability_evidence_execution")
        execution_id = _uuid4(str(row[0]))
        planned = _json_object(str(row[7]))
        result = _json_object(str(row[8]))
        source_sha = _sha256(str(row[9]))
        if source_sha != _sha256(str(row[13])):
            raise _corrupt_evidence()
        audit_payload = audit_by_execution.get(execution_id)
        if audit_payload is None:
            raise _corrupt_evidence()
        failure_ids = [
            str(item[0])
            for item in connection.execute(
                "SELECT failure_id FROM failure_observations WHERE execution_id=? ORDER BY failure_id",
                (execution_id,),
            ).fetchall()
        ]
        failure_sha = _execution_observations_sha256(connection, execution_id)
        expected_audit = {
            "executionId": execution_id,
            "localImportId": str(row[1]),
            "localSpecimenId": str(row[2]),
            "wheelModelId": str(row[3]),
            "sourceSpecimenId": _bounded_reference(str(row[4])),
            "method": str(row[5]),
            "sourceOuterPackageSha256": source_sha,
            "plannedParametersSnapshotSha256": _snapshot_sha256(planned),
            "resultSummarySha256": _snapshot_sha256(result),
            "failureObservationIds": failure_ids,
            "failureObservationsSha256": failure_sha,
            "snapshotSha256": _execution_snapshot_sha256(
                execution_id=execution_id,
                local_import_id=str(row[1]),
                local_specimen_id=str(row[2]),
                wheel_model_id=_uuid4(str(row[3])),
                source_specimen_id=_bounded_reference(str(row[4])),
                method=str(row[5]),
                lifecycle_status=str(row[6]),
                planned_parameters_snapshot=planned,
                result_summary=result,
                source_outer_package_sha256=source_sha,
                source_payload_path=_bounded_reference(str(row[10])),
                source_field_reference=_bounded_reference(str(row[11])),
                materialized_at_utc=str(row[12]),
                failure_observations_sha256=failure_sha,
            ),
            "__auditActorKind": "application",
            "__auditOccurredAtUtc": str(row[12]),
        }
        if audit_payload != expected_audit:
            raise _corrupt_evidence()
    repository = ReliabilityDomainRepository(connection)
    observation_roots = connection.execute(
        """
        SELECT o.observation_id, o.created_at_utc,
               (SELECT v.created_at_utc FROM reliability_observation_versions v WHERE v.observation_id=o.observation_id AND v.version_number=1),
               (SELECT count(*) FROM reliability_observation_versions v WHERE v.observation_id=o.observation_id)
        FROM reliability_observations o
        """,
    ).fetchall()
    if any(int(row[3]) < 1 or str(row[1]) != str(row[2]) for row in observation_roots):
        raise _corrupt_evidence()
    observation_audits = _version_audits(connection, "reliability_observation.version_created", "observationVersionId")
    observation_rows = connection.execute(
        "SELECT observation_version_id FROM reliability_observation_versions ORDER BY observation_id, version_number",
    ).fetchall()
    if len(observation_rows) != len(observation_audits):
        raise _corrupt_evidence()
    observation_chains: dict[str, tuple[int, str]] = {}
    for row in observation_rows:
        observation_version = repository.get_observation_version(str(row[0]), deadline)
        previous = observation_chains.get(observation_version.observation_id)
        expected_number = 1 if previous is None else previous[0] + 1
        expected_previous = None if previous is None else previous[1]
        if observation_version.version_number != expected_number or observation_version.previous_version_id != expected_previous:
            raise _corrupt_evidence()
        normalized = (
            observation_version.classification,
            observation_version.endpoint_kind,
            observation_version.metric_kind,
            observation_version.metric_unit,
            observation_version.lower_value,
            observation_version.upper_value,
            observation_version.origin_basis,
            observation_version.endpoint_basis,
            observation_version.document_locator,
            observation_version.actor,
            observation_version.decision_reason,
        )
        if (
            normalize_observation_values(
                classification=observation_version.classification,
                endpoint_kind=observation_version.endpoint_kind,
                metric_kind=observation_version.metric_kind,
                metric_unit=observation_version.metric_unit,
                lower_value=observation_version.lower_value,
                upper_value=observation_version.upper_value,
                origin_basis=observation_version.origin_basis,
                endpoint_basis=observation_version.endpoint_basis,
                document_locator=observation_version.document_locator,
                actor=observation_version.actor,
                reason=observation_version.decision_reason,
            )
            != normalized
            or normalize_metric_origin(
                observation_version.metric_origin,
                observation_version.metric_kind,
            )
            != observation_version.metric_origin
        ):
            raise _corrupt_evidence()
        method_row = connection.execute(
            "SELECT method FROM reliability_test_executions WHERE execution_id=?",
            (observation_version.execution_id,),
        ).fetchone()
        if method_row is None:
            raise _corrupt_evidence()
        _validate_metric_for_method(
            str(method_row[0]),
            observation_version.metric_kind,
            observation_version.metric_unit,
        )
        expected_hash = _observation_content_sha256(
            observation_id=observation_version.observation_id,
            observation_version_id=observation_version.observation_version_id,
            execution_id=observation_version.execution_id,
            version_number=observation_version.version_number,
            previous_version_id=observation_version.previous_version_id,
            normalized=normalized,
            metric_origin=observation_version.metric_origin,
            document_snapshot=observation_version.document_snapshot,
            document_id=observation_version.document_snapshot.document_id,
            failure_ids=observation_version.failure_ids,
            created_at_utc=observation_version.created_at_utc,
        )
        audit_payload = observation_audits.get(observation_version.observation_version_id)
        if expected_hash != observation_version.content_sha256 or not _observation_audit_matches(audit_payload, observation_version):
            raise _corrupt_evidence()
        foreign_failure = connection.execute(
            """
            SELECT 1
            FROM reliability_observation_failure_refs r
            JOIN failure_observations f ON f.failure_id=r.failure_id
            WHERE r.observation_version_id=? AND f.execution_id<>?
            LIMIT 1
            """,
            (observation_version.observation_version_id, observation_version.execution_id),
        ).fetchone()
        if foreign_failure is not None:
            raise _corrupt_evidence()
        observation_chains[observation_version.observation_id] = (
            observation_version.version_number,
            observation_version.observation_version_id,
        )

    dataset_roots = connection.execute(
        """
        SELECT d.dataset_id, d.created_at_utc,
               (SELECT v.created_at_utc FROM reliability_dataset_versions v WHERE v.dataset_id=d.dataset_id AND v.version_number=1),
               (SELECT count(*) FROM reliability_dataset_versions v WHERE v.dataset_id=d.dataset_id)
        FROM reliability_datasets d
        """,
    ).fetchall()
    if any(int(row[3]) < 1 or str(row[1]) != str(row[2]) for row in dataset_roots):
        raise _corrupt_evidence()
    dataset_audits = _version_audits(connection, "reliability_dataset.version_created", "datasetVersionId")
    dataset_rows = connection.execute(
        "SELECT dataset_version_id FROM reliability_dataset_versions ORDER BY dataset_id, version_number",
    ).fetchall()
    if len(dataset_rows) != len(dataset_audits):
        raise _corrupt_evidence()
    dataset_chains: dict[str, tuple[int, str]] = {}
    for row in dataset_rows:
        dataset_version = repository.get_dataset_version(str(row[0]), deadline)
        previous = dataset_chains.get(dataset_version.dataset_id)
        expected_number = 1 if previous is None else previous[0] + 1
        expected_previous = None if previous is None else previous[1]
        if dataset_version.version_number != expected_number or dataset_version.previous_version_id != expected_previous:
            raise _corrupt_evidence()
        _validate_included_uniqueness(dataset_version.members)
        for member in dataset_version.members:
            observation = repository.get_observation_version(member.observation_version_id, deadline)
            execution_row = connection.execute(
                """
                SELECT e.execution_id, e.local_specimen_id, e.method, s.run_id, s.package_kind,
                       e.wheel_model_id
                FROM reliability_observations o
                JOIN reliability_test_executions e ON e.execution_id=o.execution_id
                JOIN r130sh_sources s ON s.local_import_id=e.local_import_id
                WHERE o.observation_id=?
                """,
                (observation.observation_id,),
            ).fetchone()
            if execution_row is None or (
                member.execution_id != str(execution_row[0])
                or member.local_specimen_id != str(execution_row[1])
                or member.source_run_id != str(execution_row[3])
                or dataset_version.wheel_model_id != str(execution_row[5])
            ):
                raise _corrupt_evidence()
            eligibility, policy_reason = _policy_eligibility(
                observation,
                execution_method=str(execution_row[2]),
                package_kind=str(execution_row[4]),
                dataset_method=dataset_version.method,
                dataset_metric_kind=dataset_version.metric_kind,
                dataset_metric_unit=dataset_version.metric_unit,
            )
            if member.policy_eligibility != eligibility or member.policy_reason != policy_reason:
                raise _corrupt_evidence()
        expected_hash = _dataset_version_content_sha256(
            dataset_id=dataset_version.dataset_id,
            dataset_version_id=dataset_version.dataset_version_id,
            wheel_model_id=dataset_version.wheel_model_id,
            version_number=dataset_version.version_number,
            previous_version_id=dataset_version.previous_version_id,
            title=dataset_version.title,
            method=dataset_version.method,
            metric_kind=dataset_version.metric_kind,
            metric_unit=dataset_version.metric_unit,
            population_basis=dataset_version.population_basis,
            methodology_basis=dataset_version.methodology_basis,
            comparability_basis=dataset_version.comparability_basis,
            members=dataset_version.members,
            actor=dataset_version.actor,
            reason=dataset_version.decision_reason,
            created_at_utc=dataset_version.created_at_utc,
        )
        if expected_hash != dataset_version.content_sha256 or not _dataset_audit_matches(dataset_audits.get(dataset_version.dataset_version_id), dataset_version):
            raise _corrupt_evidence()
        dataset_chains[dataset_version.dataset_id] = (
            dataset_version.version_number,
            dataset_version.dataset_version_id,
        )


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
    # finishedAtUtc is a run lifecycle fact, not a proved failure or detection time.
    observed_at_utc = None
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
                duration_s=None,
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
                duration_s=None,
                rpm=None,
                vibration_summary=vibration_summary,
                observed_at_utc=observed_at_utc,
                source_outer_package_sha256=source_outer_package_sha256,
            )
        )
    return tuple(observations)


def _json(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _snapshot_sha256(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _execution_snapshot_sha256(
    *,
    execution_id: str,
    local_import_id: str,
    local_specimen_id: str,
    wheel_model_id: str,
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
        "wheelModelId": wheel_model_id,
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


def _document_snapshot_payload(snapshot: AnalystDocumentSnapshot) -> dict[str, object]:
    return {
        "documentId": snapshot.document_id,
        "documentKind": snapshot.document_kind,
        "title": snapshot.title,
        "designation": snapshot.designation,
        "revisionLabel": snapshot.revision_label,
        "recordRevision": snapshot.record_revision,
        "managedFileSha256": snapshot.managed_file_sha256,
    }


def _parse_document_snapshot(value: dict[str, object]) -> AnalystDocumentSnapshot:
    if set(value) != {
        "documentId",
        "documentKind",
        "title",
        "designation",
        "revisionLabel",
        "recordRevision",
        "managedFileSha256",
    }:
        raise _corrupt_evidence()
    revision = value["recordRevision"]
    file_sha = value["managedFileSha256"]
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise _corrupt_evidence()
    return AnalystDocumentSnapshot(
        document_id=_uuid4(_required_string(value["documentId"])),
        document_kind=_bounded_text(_required_string(value["documentKind"]), 100, "Вид документа"),
        title=_bounded_text(_required_string(value["title"]), 300, "Название документа"),
        designation=_bounded_optional_text(_required_string(value["designation"]), 200, "Обозначение документа"),
        revision_label=_bounded_optional_text(_required_string(value["revisionLabel"]), 200, "Редакция документа"),
        record_revision=revision,
        managed_file_sha256=None if file_sha is None else _sha256(_required_string(file_sha)),
    )


def _observation_content_sha256(
    *,
    observation_id: str,
    observation_version_id: str,
    execution_id: str,
    version_number: int,
    previous_version_id: str | None,
    normalized: tuple[str, str, str | None, str | None, str | None, str | None, str, str, str, str, str],
    metric_origin: str | None,
    document_snapshot: AnalystDocumentSnapshot,
    document_id: str,
    failure_ids: tuple[str, ...],
    created_at_utc: str,
) -> str:
    payload = {
        "observationId": observation_id,
        "observationVersionId": observation_version_id,
        "executionId": execution_id,
        "versionNumber": version_number,
        "previousVersionId": previous_version_id,
        "classification": normalized[0],
        "endpointKind": normalized[1],
        "metricKind": normalized[2],
        "metricUnit": normalized[3],
        "metricOrigin": metric_origin,
        "lowerValue": normalized[4],
        "upperValue": normalized[5],
        "observationScope": "execution",
        "originBasis": normalized[6],
        "endpointBasis": normalized[7],
        "documentId": document_id,
        "documentLocator": normalized[8],
        "documentSnapshot": _document_snapshot_payload(document_snapshot),
        "failureIds": sorted(failure_ids),
        "actor": normalized[9],
        "decisionReason": normalized[10],
        "createdAtUtc": created_at_utc,
    }
    return _snapshot_sha256(payload)


def _dataset_version_content_sha256(
    *,
    dataset_id: str,
    dataset_version_id: str,
    wheel_model_id: str,
    version_number: int,
    previous_version_id: str | None,
    title: str,
    method: str,
    metric_kind: str,
    metric_unit: str,
    population_basis: str,
    methodology_basis: str,
    comparability_basis: str,
    members: tuple[ReliabilityDatasetMember, ...],
    actor: str,
    reason: str,
    created_at_utc: str,
) -> str:
    payload = {
        "datasetId": dataset_id,
        "datasetVersionId": dataset_version_id,
        "wheelModelId": wheel_model_id,
        "versionNumber": version_number,
        "previousVersionId": previous_version_id,
        "policyId": "life_metric_exact_v1",
        "title": title,
        "method": method,
        "metricKind": metric_kind,
        "metricUnit": metric_unit,
        "populationBasis": population_basis,
        "methodologyBasis": methodology_basis,
        "comparabilityBasis": comparability_basis,
        "members": [
            {
                "observationVersionId": item.observation_version_id,
                "executionId": item.execution_id,
                "localSpecimenId": item.local_specimen_id,
                "sourceRunId": item.source_run_id,
                "policyEligibility": item.policy_eligibility,
                "policyReason": item.policy_reason,
                "decision": item.decision,
                "inclusionReason": item.inclusion_reason,
            }
            for item in sorted(members, key=lambda item: item.observation_version_id)
        ],
        "actor": actor,
        "decisionReason": reason,
        "createdAtUtc": created_at_utc,
    }
    return _snapshot_sha256(payload)


def observations_sha256(observations: tuple[FailureObservation, ...]) -> str:
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
        for item in sorted(observations, key=lambda item: item.failure_id)
    ]
    serialized = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _execution_observations_sha256(connection: sqlite3.Connection, execution_id: str) -> str:
    rows = connection.execute(
        """
        SELECT failure_id, failure_type, subject_kind, source_event_reference,
               source_field_reference, cycles_at_failure, duration_s, rpm,
               vibration_summary_json, observed_at_utc, source_outer_package_sha256
        FROM failure_observations WHERE execution_id=? ORDER BY failure_id LIMIT 65
        """,
        (execution_id,),
    ).fetchall()
    if len(rows) > 64:
        raise _corrupt_evidence()
    observations = tuple(
        FailureObservation(
            failure_id=_uuid4(str(row[0])),
            failure_type=parse_failure_type(str(row[1])),
            subject_kind=parse_subject_kind(str(row[2])),
            source_event_reference=_bounded_reference(str(row[3])),
            source_field_reference=_bounded_reference(str(row[4])),
            cycles_at_failure=None if row[5] is None else _stored_integer(row[5]),
            duration_s=None if row[6] is None else str(row[6]),
            rpm=None if row[7] is None else str(row[7]),
            vibration_summary=_json_object(str(row[8])),
            observed_at_utc=None if row[9] is None else str(row[9]),
            source_outer_package_sha256=_sha256(str(row[10])),
        )
        for row in rows
    )
    return observations_sha256(observations)


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


def parse_classification(value: str) -> Literal["failure", "right_censored", "withdrawn", "invalid"]:
    if value in {"failure", "right_censored", "withdrawn", "invalid"}:
        return cast(Literal["failure", "right_censored", "withdrawn", "invalid"], value)
    raise ProjectOperationError("validation_error", "Классификация ReliabilityObservation не поддерживается.")


def parse_endpoint_kind(value: str) -> Literal["exact", "right_bound", "interval", "unavailable"]:
    if value in {"exact", "right_bound", "interval", "unavailable"}:
        return cast(Literal["exact", "right_bound", "interval", "unavailable"], value)
    raise ProjectOperationError("validation_error", "Форма границы наблюдения не поддерживается.")


def parse_metric_kind(value: str) -> Literal["rbd_steady_rotation_time", "rpt_start_stop_cycles"]:
    if value in {"rbd_steady_rotation_time", "rpt_start_stop_cycles"}:
        return cast(Literal["rbd_steady_rotation_time", "rpt_start_stop_cycles"], value)
    raise ProjectOperationError("validation_error", "Вид наработки не поддерживается.")


def parse_metric_unit(value: str) -> Literal["hours", "count"]:
    if value in {"hours", "count"}:
        return cast(Literal["hours", "count"], value)
    raise ProjectOperationError("validation_error", "Единица наработки не поддерживается.")


def parse_metric_origin(value: str) -> Literal["analyst_provided"]:
    if value == "analyst_provided":
        return "analyst_provided"
    raise ProjectOperationError("validation_error", "Происхождение наработки не поддерживается.")


def normalize_metric_origin(
    value: str | None,
    metric_kind: str | None,
) -> Literal["analyst_provided"] | None:
    if metric_kind is None:
        if value is not None:
            raise ProjectOperationError("validation_error", "Без числовой наработки происхождение не задаётся.")
        return None
    if value is None:
        raise ProjectOperationError("validation_error", "Для числовой наработки требуется происхождение.")
    return parse_metric_origin(value)


def parse_dataset_method(value: str) -> Literal["rbd", "rpt"]:
    if value in {"rbd", "rpt"}:
        return cast(Literal["rbd", "rpt"], value)
    raise ProjectOperationError("validation_error", "Метод life dataset не поддерживается.")


def parse_policy_eligibility(value: str) -> Literal["eligible", "ineligible"]:
    if value in {"eligible", "ineligible"}:
        return cast(Literal["eligible", "ineligible"], value)
    raise _corrupt_evidence()


def parse_inclusion_decision(value: str) -> Literal["included", "excluded"]:
    if value in {"included", "excluded"}:
        return cast(Literal["included", "excluded"], value)
    raise ProjectOperationError("validation_error", "Решение о включении не поддерживается.")


def normalize_observation_values(
    *,
    classification: str,
    endpoint_kind: str,
    metric_kind: str | None,
    metric_unit: str | None,
    lower_value: str | None,
    upper_value: str | None,
    origin_basis: str,
    endpoint_basis: str,
    document_locator: str,
    actor: str,
    reason: str,
) -> tuple[str, str, str | None, str | None, str | None, str | None, str, str, str, str, str]:
    classification_value = parse_classification(classification)
    endpoint_value = parse_endpoint_kind(endpoint_kind)
    kind_value = None if metric_kind is None else parse_metric_kind(metric_kind)
    unit_value = None if metric_unit is None else parse_metric_unit(metric_unit)
    if (kind_value is None) != (unit_value is None) or (kind_value is None) != (lower_value is None):
        raise ProjectOperationError("validation_error", "Вид, единица и значение наработки задаются вместе.")
    normalized_lower: str | None = None
    normalized_upper: str | None = None
    if kind_value is not None and unit_value is not None and lower_value is not None:
        normalized_lower = canonical_metric_value(lower_value, kind_value)
        normalized_upper = None if upper_value is None else canonical_metric_value(upper_value, kind_value)
    if endpoint_value == "unavailable":
        if kind_value is not None or upper_value is not None:
            raise ProjectOperationError("validation_error", "Неизвестная граница не содержит числовую наработку.")
    elif endpoint_value == "interval":
        if normalized_lower is None or normalized_upper is None:
            raise ProjectOperationError("validation_error", "Интервал требует две границы.")
        if Decimal(normalized_lower) >= Decimal(normalized_upper):
            raise ProjectOperationError("validation_error", "Верхняя граница интервала должна быть больше нижней.")
    elif normalized_lower is None or normalized_upper is not None:
        raise ProjectOperationError("validation_error", "Точная или правая граница требует одно значение.")
    allowed_endpoints = {
        "failure": {"exact", "interval", "unavailable"},
        "right_censored": {"right_bound"},
        "withdrawn": {"right_bound", "unavailable"},
        "invalid": {"unavailable"},
    }
    if endpoint_value not in allowed_endpoints[classification_value]:
        raise ProjectOperationError("validation_error", "Классификация и тип границы наблюдения несовместимы.")
    return (
        classification_value,
        endpoint_value,
        kind_value,
        unit_value,
        normalized_lower,
        normalized_upper,
        _bounded_text(origin_basis, 1000, "Начало отсчёта"),
        _bounded_text(endpoint_basis, 1000, "Основание границы"),
        _bounded_text(document_locator, 1000, "Локатор документа"),
        _bounded_text(actor, 200, "Автор решения"),
        _bounded_text(reason, 2000, "Основание решения"),
    )


def canonical_metric_value(value: str, metric_kind: str) -> str:
    if len(value) > 64:
        raise ProjectOperationError("validation_error", "Значение наработки имеет неверный формат.")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ProjectOperationError("validation_error", "Значение наработки должно быть числом.") from error
    if not parsed.is_finite() or parsed < 0:
        raise ProjectOperationError("validation_error", "Значение наработки должно быть конечным и неотрицательным.")
    maximum = Decimal("1000000000000") if metric_kind == "rpt_start_stop_cycles" else Decimal("1000000000")
    if parsed > maximum:
        raise ProjectOperationError("validation_error", "Значение наработки превышает допустимый предел.")
    if metric_kind == "rpt_start_stop_cycles" and parsed != parsed.to_integral_value():
        raise ProjectOperationError("validation_error", "Число циклов должно быть целым.")
    canonical = format(parsed, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    if canonical == "-0":
        canonical = "0"
    if canonical != value:
        raise ProjectOperationError("validation_error", "Значение наработки должно быть в canonical decimal format.")
    return canonical


def _validate_metric_for_method(method: str, metric_kind: str | None, metric_unit: str | None) -> None:
    if metric_kind is None:
        return
    expected = {
        "rbd": ("rbd_steady_rotation_time", "hours"),
        "rpt": ("rpt_start_stop_cycles", "count"),
    }.get(method)
    if expected is None or (metric_kind, metric_unit) != expected:
        raise ProjectOperationError("validation_error", "Наработка не соответствует методу исполнения.")


def _normalize_dataset_metric(method: str, metric_kind: str, metric_unit: str) -> tuple[str, str, str]:
    method_value = parse_dataset_method(method)
    kind_value = parse_metric_kind(metric_kind)
    unit_value = parse_metric_unit(metric_unit)
    _validate_metric_for_method(method_value, kind_value, unit_value)
    return method_value, kind_value, unit_value


def _normalize_dataset_decisions(
    decisions: tuple[dict[str, object], ...],
) -> tuple[tuple[str, Literal["included", "excluded"], str], ...]:
    if not 1 <= len(decisions) <= 100:
        raise ProjectOperationError("validation_error", "Версия выборки требует от 1 до 100 рассмотренных observations.")
    normalized: list[tuple[str, Literal["included", "excluded"], str]] = []
    for item in decisions:
        if set(item) != {"observationVersionId", "decision", "reason"}:
            raise ProjectOperationError("validation_error", "Решение состава выборки имеет неверную структуру.")
        normalized.append(
            (
                _uuid4(_required_string(item["observationVersionId"])),
                parse_inclusion_decision(_required_string(item["decision"])),
                _bounded_text(_required_string(item["reason"]), 2000, "Причина включения или исключения"),
            )
        )
    if len({item[0] for item in normalized}) != len(normalized):
        raise ProjectOperationError("validation_error", "Версия observation указана в выборке повторно.")
    return tuple(sorted(normalized))


def _policy_eligibility(
    observation: ReliabilityObservationVersion,
    *,
    execution_method: str,
    package_kind: str,
    dataset_method: str,
    dataset_metric_kind: str,
    dataset_metric_unit: str,
) -> tuple[Literal["eligible", "ineligible"], str]:
    if package_kind != "final":
        return "ineligible", "Diagnostic partial не включается политикой life_metric_exact_v1."
    if execution_method == "pmn":
        return "ineligible", "ПМН не является наблюдением наработки этой политики."
    if execution_method != dataset_method:
        return "ineligible", "Метод исполнения не совпадает с методом выборки."
    if observation.metric_kind != dataset_metric_kind or observation.metric_unit != dataset_metric_unit:
        return "ineligible", "Вид или единица наработки не совпадает с выборкой."
    if observation.metric_origin != "analyst_provided":
        return "ineligible", "Происхождение числовой наработки не подтверждено инженером."
    if observation.classification == "failure" and observation.endpoint_kind == "exact":
        return "eligible", "Подтверждённый отказ с точной наработкой."
    if observation.classification == "right_censored" and observation.endpoint_kind == "right_bound":
        return "eligible", "Наблюдение без отказа с доказанной правой границей."
    if observation.classification == "failure":
        return "ineligible", "Отказ не имеет точной наработки; interval/unavailable не включается этой политикой."
    return "ineligible", "Классификация не включается политикой life_metric_exact_v1."


def _validate_included_uniqueness(members: tuple[ReliabilityDatasetMember, ...]) -> None:
    included = tuple(item for item in members if item.decision == "included")
    if len({item.local_specimen_id for item in included}) != len(included):
        raise ProjectOperationError("validation_error", "В выборке допускается одно включённое наблюдение на Specimen.")
    if len({item.source_run_id for item in included}) != len(included):
        raise ProjectOperationError("validation_error", "В выборке допускается одна включённая export revision исходного запуска.")


def _bounded_text(value: str, maximum_bytes: int, label: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized.encode("utf-8")) > maximum_bytes or any(ord(char) < 32 for char in normalized):
        raise ProjectOperationError("validation_error", f"{label}: значение отсутствует или превышает лимит.")
    return normalized


def _bounded_optional_text(value: str, maximum_bytes: int, label: str) -> str:
    normalized = value.strip()
    if len(normalized.encode("utf-8")) > maximum_bytes or any(ord(char) < 32 for char in normalized):
        raise ProjectOperationError("validation_error", f"{label}: значение превышает лимит.")
    return normalized


def _assert_existing_observation_matches(
    existing: ReliabilityObservationVersion,
    *,
    observation_id: str,
    execution_id: str,
    expected_previous_version_id: str | None,
    normalized: tuple[str, str, str | None, str | None, str | None, str | None, str, str, str, str, str],
    metric_origin: str | None,
    document_id: str,
    failure_ids: tuple[str, ...],
) -> None:
    expected_hash = _observation_content_sha256(
        observation_id=observation_id,
        observation_version_id=existing.observation_version_id,
        execution_id=execution_id,
        version_number=existing.version_number,
        previous_version_id=expected_previous_version_id,
        normalized=normalized,
        metric_origin=metric_origin,
        document_snapshot=existing.document_snapshot,
        document_id=_uuid4(document_id),
        failure_ids=failure_ids,
        created_at_utc=existing.created_at_utc,
    )
    if existing.content_sha256 != expected_hash:
        raise ProjectOperationError("revision_conflict", "Повтор identity содержит другое решение.")


def _assert_existing_dataset_matches(
    existing: ReliabilityDatasetVersion,
    *,
    dataset_id: str,
    wheel_model_id: str,
    expected_previous_version_id: str | None,
    title: str,
    method: str,
    metric_kind: str,
    metric_unit: str,
    population_basis: str,
    methodology_basis: str,
    comparability_basis: str,
    normalized_decisions: tuple[tuple[str, Literal["included", "excluded"], str], ...],
    actor: str,
    reason: str,
) -> None:
    existing_decisions = tuple(
        sorted((item.observation_version_id, item.decision, item.inclusion_reason) for item in existing.members),
    )
    expected_hash = _dataset_version_content_sha256(
        dataset_id=dataset_id,
        dataset_version_id=existing.dataset_version_id,
        wheel_model_id=wheel_model_id,
        version_number=existing.version_number,
        previous_version_id=expected_previous_version_id,
        title=title,
        method=method,
        metric_kind=metric_kind,
        metric_unit=metric_unit,
        population_basis=population_basis,
        methodology_basis=methodology_basis,
        comparability_basis=comparability_basis,
        members=existing.members,
        actor=actor,
        reason=reason,
        created_at_utc=existing.created_at_utc,
    )
    if existing_decisions != normalized_decisions or existing.content_sha256 != expected_hash:
        raise ProjectOperationError("revision_conflict", "Повтор identity содержит другой состав выборки.")


def _revision_conflict(expected: str | None, actual: str | None) -> ProjectOperationError:
    return ProjectOperationError(
        "revision_conflict",
        "Сохранённая версия изменилась; черновик не применён.",
        details={"expectedVersionId": expected, "actualVersionId": actual},
    )


def _version_audits(
    connection: sqlite3.Connection,
    event_type: str,
    identity_key: str,
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for row in connection.execute(
        "SELECT payload_json, actor_kind, occurred_at_utc FROM project_audit_events WHERE event_type=?",
        (event_type,),
    ).fetchall():
        payload = _json_object(str(row[0]))
        identity = _uuid4(_required_string(payload.get(identity_key)))
        if identity in result:
            raise _corrupt_evidence()
        result[identity] = {
            **payload,
            "__auditActorKind": str(row[1]),
            "__auditOccurredAtUtc": str(row[2]),
        }
    return result


def _observation_audit_matches(
    payload: dict[str, object] | None,
    version: ReliabilityObservationVersion,
) -> bool:
    return payload == {
        "observationId": version.observation_id,
        "observationVersionId": version.observation_version_id,
        "executionId": version.execution_id,
        "versionNumber": version.version_number,
        "previousVersionId": version.previous_version_id,
        "contentSha256": version.content_sha256,
        "__auditActorKind": "user",
        "__auditOccurredAtUtc": version.created_at_utc,
    }


def _dataset_audit_matches(
    payload: dict[str, object] | None,
    version: ReliabilityDatasetVersion,
) -> bool:
    return payload == {
        "datasetId": version.dataset_id,
        "datasetVersionId": version.dataset_version_id,
        "versionNumber": version.version_number,
        "previousVersionId": version.previous_version_id,
        "contentSha256": version.content_sha256,
        "__auditActorKind": "user",
        "__auditOccurredAtUtc": version.created_at_utc,
    }


def _execution_summary_from_row(row: sqlite3.Row) -> ReliabilityExecutionSummary:
    return ReliabilityExecutionSummary(
        execution_id=_uuid4(str(row[0])),
        local_specimen_id=_uuid4(str(row[1])),
        source_specimen_id=_bounded_reference(str(row[2])),
        source_run_id=_bounded_reference(str(row[3])),
        export_revision=int(row[4]),
        package_kind=cast(Literal["final", "diagnostic_partial"], str(row[5])),
        method=parse_test_method(str(row[6])),
        lifecycle_status=parse_lifecycle_status(str(row[7])),
        technical_status=None if row[8] is None else str(row[8]),
        specimen_outcome=None if row[9] is None else str(row[9]),
        run_validity=None if row[10] is None else str(row[10]),
        data_completeness=None if row[11] is None else str(row[11]),
        materialized_at_utc=str(row[12]),
        failure_observation_count=int(row[13]),
        current_observation_version_id=None if row[14] is None else _uuid4(str(row[14])),
        current_observation_version_number=None if row[15] is None else int(row[15]),
        current_classification=None if row[16] is None else parse_classification(str(row[16])),
    )


def _encode_execution_cursor(
    wheel_model_id: str,
    snapshot_rowid: int,
    after_time: str,
    after_id: str,
) -> str:
    return _encode_page_cursor(wheel_model_id, snapshot_rowid, after_time, after_id, "execution")


def _encode_page_cursor(
    wheel_model_id: str,
    snapshot_rowid: int,
    after_time: str,
    after_id: str,
    kind: Literal["execution", "dataset"],
) -> str:
    payload = {
        "v": 1,
        "kind": kind,
        "wheelModelId": wheel_model_id,
        "snapshotRowId": snapshot_rowid,
        "afterTime": after_time,
        "afterId": after_id,
    }
    return urlsafe_b64encode(_json(payload).encode("utf-8")).decode("ascii").rstrip("=")


def _decode_execution_cursor(cursor: str, wheel_model_id: str) -> tuple[int, str, str]:
    return _decode_page_cursor(cursor, wheel_model_id, "execution")


def _decode_page_cursor(
    cursor: str,
    wheel_model_id: str,
    kind: Literal["execution", "dataset"],
) -> tuple[int, str, str]:
    if not cursor or len(cursor) > 512 or not cursor.isascii():
        raise ProjectOperationError("validation_error", "Cursor страницы имеет неверный формат.")
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded: object = json.loads(urlsafe_b64decode(cursor + padding).decode("utf-8"))
    except (Base64Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectOperationError("validation_error", "Cursor страницы имеет неверный формат.") from error
    if not isinstance(decoded, dict):
        raise ProjectOperationError("validation_error", "Cursor страницы имеет неверный формат.")
    payload = cast(dict[str, object], decoded)
    if set(payload) != {"v", "kind", "wheelModelId", "snapshotRowId", "afterTime", "afterId"}:
        raise ProjectOperationError("validation_error", "Cursor страницы имеет неверную структуру.")
    snapshot_rowid = payload["snapshotRowId"]
    if payload["v"] != 1 or payload["kind"] != kind or payload["wheelModelId"] != wheel_model_id or not isinstance(snapshot_rowid, int) or isinstance(snapshot_rowid, bool) or snapshot_rowid < 0:
        raise ProjectOperationError("validation_error", "Cursor не относится к выбранному списку.")
    after_time = _bounded_reference(_required_string(payload["afterTime"]))
    after_id = _uuid4(_required_string(payload["afterId"]))
    return snapshot_rowid, after_time, after_id


def _unique_ids(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if len(values) > 64:
        raise ProjectOperationError("validation_error", f"Список {label} ID превышает лимит 64.")
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


def _stored_integer(value: object, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise _corrupt_evidence()
    return value


def _bounded_reference(value: str) -> str:
    if not value or len(value.encode("utf-8")) > 512 or any(ord(char) < 32 for char in value):
        raise ProjectOperationError("corrupt_project", "Source reference повреждён.")
    return value


def _sha256(value: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ProjectOperationError("corrupt_project", "Source SHA-256 повреждён.")
    return value


def _check_deadline(deadline: RequestDeadline | None, stage: str) -> None:
    if deadline is not None:
        deadline.check(stage)
