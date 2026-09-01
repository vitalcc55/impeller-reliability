from __future__ import annotations

from collections.abc import Callable

import pytest

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.reliability_domain import (
    FailureObservation,
    observations_sha256,
    parse_censoring_policy,
    parse_failure_type,
    parse_life_metric_unit,
    parse_lifecycle_status,
    parse_subject_kind,
    parse_test_method,
)
from impeller_reliability.protocol.envelopes import ReliabilityExecutionListResult


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
        (parse_life_metric_unit, "cycles"),
        (parse_life_metric_unit, "hours"),
        (parse_life_metric_unit, "unknown"),
        (parse_censoring_policy, "not_classified"),
        (parse_censoring_policy, "explicit"),
    ],
)
def test_reliability_domain_accepts_only_declared_vocabulary(
    parser: Callable[[str], str],
    value: str,
) -> None:
    assert parser(value) == value


@pytest.mark.parametrize(
    "parser",
    [parse_test_method, parse_lifecycle_status, parse_failure_type, parse_subject_kind, parse_life_metric_unit, parse_censoring_policy],
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


def test_execution_list_contract_accepts_more_than_one_page() -> None:
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
    items: list[object] = [execution] * 513
    assert len(ReliabilityExecutionListResult.model_validate({"items": items}).items) == 513
