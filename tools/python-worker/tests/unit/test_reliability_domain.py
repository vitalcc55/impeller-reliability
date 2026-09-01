from __future__ import annotations

from collections.abc import Callable

import pytest

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.reliability_domain import (
    parse_censoring_policy,
    parse_failure_type,
    parse_life_metric_unit,
    parse_lifecycle_status,
    parse_subject_kind,
    parse_test_method,
)


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
