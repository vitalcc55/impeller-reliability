from __future__ import annotations

from collections.abc import Callable

from pydantic import ValidationError
import pytest

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.reliability_domain import (
    FailureObservation,
    canonical_metric_value,
    normalize_metric_origin,
    normalize_observation_values,
    observations_sha256,
    parse_classification,
    parse_dataset_method,
    parse_endpoint_kind,
    parse_failure_type,
    parse_lifecycle_status,
    parse_metric_kind,
    parse_metric_origin,
    parse_metric_unit,
    parse_subject_kind,
    parse_test_method,
)
from impeller_reliability.protocol.envelopes import ReliabilityExecutionPageResult


@pytest.mark.parametrize(
    ("parser", "value"),
    [
        (parse_test_method, "rbd"),
        (parse_test_method, "rpt"),
        (parse_test_method, "pmn"),
        (parse_lifecycle_status, "completed"),
        (parse_lifecycle_status, "interrupted"),
        (parse_lifecycle_status, "failed"),
        (parse_failure_type, "specimen_outcome"),
        (parse_failure_type, "technical_interruption"),
        (parse_subject_kind, "specimen"),
        (parse_subject_kind, "equipment"),
        (parse_subject_kind, "unknown"),
        (parse_classification, "failure"),
        (parse_classification, "right_censored"),
        (parse_classification, "withdrawn"),
        (parse_classification, "invalid"),
        (parse_endpoint_kind, "exact"),
        (parse_endpoint_kind, "right_bound"),
        (parse_endpoint_kind, "interval"),
        (parse_endpoint_kind, "unavailable"),
        (parse_metric_kind, "rbd_steady_rotation_time"),
        (parse_metric_kind, "rpt_start_stop_cycles"),
        (parse_metric_unit, "hours"),
        (parse_metric_unit, "count"),
        (parse_metric_origin, "analyst_provided"),
        (parse_dataset_method, "rbd"),
        (parse_dataset_method, "rpt"),
    ],
)
def test_reliability_domain_accepts_only_declared_vocabulary(
    parser: Callable[[str], str],
    value: str,
) -> None:
    assert parser(value) == value


@pytest.mark.parametrize(
    "parser",
    [
        parse_test_method,
        parse_lifecycle_status,
        parse_failure_type,
        parse_subject_kind,
        parse_classification,
        parse_endpoint_kind,
        parse_metric_kind,
        parse_metric_unit,
        parse_metric_origin,
        parse_dataset_method,
    ],
)
def test_reliability_domain_rejects_unknown_vocabulary(parser: Callable[[str], str]) -> None:
    with pytest.raises(ProjectOperationError) as raised:
        parser("unsupported")
    assert raised.value.code in {"validation_error", "corrupt_project"}


def test_observation_evidence_hash_is_independent_of_storage_order() -> None:
    first = FailureObservation(
        failure_id="00000000-0000-4000-8000-000000000002",
        failure_type="specimen_outcome",
        subject_kind="specimen",
        source_event_reference="run-summary.json#/laboratory_conclusion",
        source_field_reference="#/specimen_outcome",
        cycles_at_failure=None,
        duration_s="1",
        rpm=None,
        vibration_summary={},
        observed_at_utc=None,
        source_outer_package_sha256="a" * 64,
    )
    second = FailureObservation(
        failure_id="00000000-0000-4000-8000-000000000001",
        failure_type="technical_interruption",
        subject_kind="unknown",
        source_event_reference="run-summary.json#/termination_reason",
        source_field_reference="#/technical_status",
        cycles_at_failure=None,
        duration_s="1",
        rpm=None,
        vibration_summary={},
        observed_at_utc=None,
        source_outer_package_sha256="a" * 64,
    )
    assert observations_sha256((first, second)) == observations_sha256((second, first))


def test_execution_page_contract_rejects_more_than_fifty_summaries() -> None:
    execution: dict[str, object] = {
        "executionId": "00000000-0000-4000-8000-000000000001",
        "localImportId": "00000000-0000-4000-8000-000000000002",
        "localSpecimenId": "00000000-0000-4000-8000-000000000003",
        "sourceSpecimenId": "specimen-1",
        "method": "rbd",
        "lifecycleStatus": "completed",
        "plannedParametersSnapshot": {},
        "resultSummary": {},
        "sourceOuterPackageSha256": "a" * 64,
        "materializedAtUtc": "2026-09-01T00:00:00.000Z",
        "failureObservations": [],
    }
    summary = {
        "executionId": execution["executionId"],
        "localSpecimenId": execution["localSpecimenId"],
        "sourceSpecimenId": execution["sourceSpecimenId"],
        "sourceRunId": "run-1",
        "exportRevision": 1,
        "packageKind": "final",
        "method": "rbd",
        "lifecycleStatus": "completed",
        "technicalStatus": "completed",
        "specimenOutcome": "passed",
        "runValidity": "valid",
        "dataCompleteness": "complete",
        "materializedAtUtc": execution["materializedAtUtc"],
        "failureObservationCount": 0,
        "currentObservationVersionId": None,
        "currentObservationVersionNumber": None,
        "currentClassification": None,
    }
    with pytest.raises(ValidationError):
        ReliabilityExecutionPageResult.model_validate({"items": [summary] * 51, "nextCursor": "next"})


@pytest.mark.parametrize(
    ("value", "metric_kind"),
    [
        ("-1", "rbd_steady_rotation_time"),
        ("NaN", "rbd_steady_rotation_time"),
        ("Infinity", "rbd_steady_rotation_time"),
        ("1.5", "rpt_start_stop_cycles"),
        ("1.0", "rbd_steady_rotation_time"),
        ("1e3", "rbd_steady_rotation_time"),
        ("1000000000001", "rpt_start_stop_cycles"),
    ],
)
def test_life_metric_numeric_contract_rejects_invalid_or_noncanonical_values(
    value: str,
    metric_kind: str,
) -> None:
    with pytest.raises(ProjectOperationError) as raised:
        canonical_metric_value(value, metric_kind)
    assert raised.value.code == "validation_error"


def test_life_metric_numeric_contract_preserves_zero_and_exact_decimal() -> None:
    assert canonical_metric_value("0", "rbd_steady_rotation_time") == "0"
    assert canonical_metric_value("12.5", "rbd_steady_rotation_time") == "12.5"
    assert canonical_metric_value("12", "rpt_start_stop_cycles") == "12"


def test_life_metric_origin_is_explicit_and_matches_value_presence() -> None:
    assert normalize_metric_origin("analyst_provided", "rbd_steady_rotation_time") == "analyst_provided"
    assert normalize_metric_origin(None, None) is None
    with pytest.raises(ProjectOperationError):
        normalize_metric_origin(None, "rbd_steady_rotation_time")
    with pytest.raises(ProjectOperationError):
        normalize_metric_origin("source_measured", "rbd_steady_rotation_time")


def test_failure_may_be_saved_without_metric_but_right_censoring_requires_bound() -> None:
    normalized = normalize_observation_values(
        classification="failure",
        endpoint_kind="unavailable",
        metric_kind=None,
        metric_unit=None,
        lower_value=None,
        upper_value=None,
        origin_basis="Начало не установлено",
        endpoint_basis="Момент отказа не установлен",
        document_locator="Заключение 1",
        actor="engineer",
        reason="Отказ подтверждён без точной наработки",
    )
    assert normalized[:6] == ("failure", "unavailable", None, None, None, None)
    with pytest.raises(ProjectOperationError, match="несовместимы"):
        normalize_observation_values(
            classification="right_censored",
            endpoint_kind="unavailable",
            metric_kind=None,
            metric_unit=None,
            lower_value=None,
            upper_value=None,
            origin_basis="Начало не установлено",
            endpoint_basis="Граница не установлена",
            document_locator="Заключение 2",
            actor="engineer",
            reason="Недостаточно сведений",
        )


@pytest.mark.parametrize(
    ("classification", "endpoint_kind"),
    [
        ("failure", "right_bound"),
        ("right_censored", "exact"),
        ("right_censored", "unavailable"),
        ("withdrawn", "exact"),
        ("withdrawn", "interval"),
        ("invalid", "exact"),
        ("invalid", "right_bound"),
        ("invalid", "interval"),
    ],
)
def test_classification_rejects_incompatible_endpoint(
    classification: str,
    endpoint_kind: str,
) -> None:
    with pytest.raises(ProjectOperationError, match="несовместимы"):
        normalize_observation_values(
            classification=classification,
            endpoint_kind=endpoint_kind,
            metric_kind=None if endpoint_kind == "unavailable" else "rbd_steady_rotation_time",
            metric_unit=None if endpoint_kind == "unavailable" else "hours",
            lower_value=None if endpoint_kind == "unavailable" else "1",
            upper_value="2" if endpoint_kind == "interval" else None,
            origin_basis="Документированное начало",
            endpoint_basis="Документированная граница",
            document_locator="Журнал, строка 1",
            actor="engineer",
            reason="Проверка матрицы",
        )


def test_interval_failure_remains_two_distinct_bounds() -> None:
    normalized = normalize_observation_values(
        classification="failure",
        endpoint_kind="interval",
        metric_kind="rbd_steady_rotation_time",
        metric_unit="hours",
        lower_value="10",
        upper_value="12.5",
        origin_basis="Начало установившегося вращения",
        endpoint_basis="Между последним осмотром и обнаружением",
        document_locator="Журнал, строки 10-11",
        actor="engineer",
        reason="Точное время отказа неизвестно",
    )
    assert normalized[4:6] == ("10", "12.5")
