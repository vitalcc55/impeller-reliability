from __future__ import annotations

from dataclasses import asdict
from fractions import Fraction
import json

from pydantic import ValidationError
import pytest

from impeller_reliability.calculations.exact import exact_value
from impeller_reliability.calculations.pmn import (
    PmnCalculationError,
    PmnFailureApplicability,
    PmnFailureInput,
    PmnReferenceInput,
    calculate_pmn_reference,
)
from impeller_reliability.calculations.pmn_result_snapshot import PmnReferenceResultModel


def _input(
    *,
    nominal_rpm: str = "1500",
    speed_factor: str = "1.2",
    target_cycles: str = "2",
    acceleration_duration_s: str = "10",
    steady_duration_s: str = "30",
    deceleration_duration_s: str = "10",
    failure: PmnFailureInput | None = None,
) -> PmnReferenceInput:
    return PmnReferenceInput(
        nominal_rpm=nominal_rpm,
        speed_factor=speed_factor,
        target_cycles=target_cycles,
        acceleration_duration_s=acceleration_duration_s,
        steady_duration_s=steady_duration_s,
        deceleration_duration_s=deceleration_duration_s,
        failure=failure,
    )


def test_pmi_reference_and_exact_unit_conversions() -> None:
    result = calculate_pmn_reference(_input())
    assert (result.algorithm_id, result.algorithm_version, result.numeric_policy) == ("pmn_reference", "1.0.0", "exact_fraction_v1")
    assert result.maximum_rpm.decimal == "1800"
    assert result.cycle_duration_s_exact.decimal == "50"
    assert result.total_duration_s_exact.decimal == "100"
    assert (result.total_duration_min_exact.numerator, result.total_duration_min_exact.denominator) == ("5", "3")
    assert result.total_duration_min_exact.decimal is None
    assert result.total_duration_min_exact.decimal_preview.endswith("…")
    assert (result.total_duration_h_exact.numerator, result.total_duration_h_exact.denominator) == ("1", "36")
    assert result.failure_result.status == "not_applicable"
    assert result.failure_result.reason_code == "failure_duration_unavailable"
    assert len(result.phases) == 3
    assert [(phase.phase, phase.start_s.decimal, phase.end_s.decimal) for phase in result.phases] == [
        ("acceleration", "0", "10"),
        ("steady_rotation", "10", "40"),
        ("deceleration", "40", "50"),
    ]
    assert result.phases[0].start_rpm.decimal == "0"
    assert result.phases[-1].end_rpm.decimal == "0"
    assert len(result.diagram_points) == 7
    assert result.diagram_points[0].x == 0 and result.diagram_points[-1].x == 2000
    snapshot = PmnReferenceResultModel.model_validate_json(json.dumps(asdict(result)))
    assert snapshot.maximum_rpm.numerator == "1800"


def test_speed_factor_changes_only_maximum_speed() -> None:
    first = calculate_pmn_reference(_input())
    second = calculate_pmn_reference(_input(speed_factor="1.25"))
    assert second.maximum_rpm.decimal == "1875"
    assert second.cycle_duration_s_exact == first.cycle_duration_s_exact
    assert second.total_duration_s_exact == first.total_duration_s_exact


def test_decimal_phases_and_zero_phase_are_exact() -> None:
    result = calculate_pmn_reference(_input(target_cycles="3", acceleration_duration_s="0.1", steady_duration_s="0.2", deceleration_duration_s="0.3"))
    assert result.cycle_duration_s_exact.decimal == "0.6"
    assert result.total_duration_s_exact.decimal == "1.8"
    zero = calculate_pmn_reference(_input(acceleration_duration_s="0", steady_duration_s="50", deceleration_duration_s="0"))
    assert zero.cycle_duration_s_exact.decimal == "50"
    assert zero.diagram_points[0].x == zero.diagram_points[1].x
    assert zero.diagram_points[2].x == zero.diagram_points[3].x


@pytest.mark.parametrize(("duration", "expected"), [("0", "0"), ("20", "1"), ("49.999", "1"), ("50", "1"), ("50.001", "2")])
def test_table_5_uses_exact_ceiling(duration: str, expected: str) -> None:
    result = calculate_pmn_reference(_input(failure=PmnFailureInput("exact_supported", duration)))
    assert result.failure_result.status == "calculated"
    assert result.failure_result.cycles_to_failure == expected
    assert result.failure_result.reason_code is None


@pytest.mark.parametrize(
    ("applicability", "reason"),
    [
        ("unavailable", "failure_duration_unavailable"),
        ("interval_endpoint", "failure_endpoint_interval"),
        ("right_censored", "failure_not_observed"),
        ("ambiguous_pauses", "failure_structure_ambiguous"),
        ("variable_cycle", "failure_cycle_variable"),
        ("unknown_start", "failure_start_unknown"),
        ("repeated_attempts", "failure_attempts_ambiguous"),
    ],
)
def test_inapplicable_failure_keeps_main_calculation(applicability: PmnFailureApplicability, reason: str) -> None:
    result = calculate_pmn_reference(_input(failure=PmnFailureInput(applicability, None)))
    assert result.total_duration_s_exact.decimal == "100"
    assert result.failure_result.status == "not_applicable"
    assert result.failure_result.reason_code == reason


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("nominal_rpm", "0", "numeric_out_of_range"),
        ("speed_factor", "0", "numeric_out_of_range"),
        ("speed_factor", "NaN", "invalid_numeric_format"),
        ("speed_factor", "Infinity", "invalid_numeric_format"),
        ("target_cycles", "0", "numeric_out_of_range"),
        ("target_cycles", "2.5", "invalid_numeric_format"),
        ("target_cycles", "1000000000001", "numeric_out_of_range"),
        ("steady_duration_s", "-1", "invalid_numeric_format"),
        ("steady_duration_s", "", "invalid_numeric_format"),
        ("deceleration_duration_s", "1000000001", "numeric_out_of_range"),
    ],
)
def test_invalid_inputs_have_typed_errors(field: str, value: str, reason: str) -> None:
    inputs = _input()
    object.__setattr__(inputs, field, value)
    with pytest.raises(PmnCalculationError) as raised:
        calculate_pmn_reference(inputs)
    assert raised.value.reason_code == reason


def test_wrong_runtime_types_and_zero_full_cycle_are_rejected() -> None:
    with pytest.raises(PmnCalculationError, match="типизированный") as raised:
        calculate_pmn_reference(object())
    assert raised.value.reason_code == "invalid_input_type"
    with pytest.raises(PmnCalculationError) as raised:
        calculate_pmn_reference(_input(acceleration_duration_s="0", steady_duration_s="0", deceleration_duration_s="0"))
    assert raised.value.reason_code == "cycle_duration_zero"
    for invalid in (True, 1.2, object()):
        value = _input()
        object.__setattr__(value, "target_cycles", invalid)
        with pytest.raises(PmnCalculationError) as raised:
            calculate_pmn_reference(value)
        assert raised.value.reason_code == "invalid_input_type"


def test_failure_duration_must_match_applicability() -> None:
    with pytest.raises(PmnCalculationError) as raised:
        calculate_pmn_reference(_input(failure=PmnFailureInput("exact_supported", None)))
    assert raised.value.reason_code == "failure_duration_required"
    with pytest.raises(PmnCalculationError) as raised:
        calculate_pmn_reference(_input(failure=PmnFailureInput("unavailable", "20")))
    assert raised.value.reason_code == "failure_duration_not_applicable"
    with pytest.raises(PmnCalculationError) as raised:
        calculate_pmn_reference(_input(failure=PmnFailureInput("exact_supported", "1000000000001")))
    assert raised.value.reason_code == "numeric_out_of_range"
    value = _input()
    object.__setattr__(value, "failure", object())
    with pytest.raises(PmnCalculationError) as raised:
        calculate_pmn_reference(value)
    assert raised.value.reason_code == "invalid_input_type"


def test_chart_size_does_not_grow_with_cycles() -> None:
    base = calculate_pmn_reference(_input())
    many = calculate_pmn_reference(_input(target_cycles="1000000000000"))
    assert len(base.diagram_points) == len(many.diagram_points) == 7
    assert many.total_duration_s_exact.decimal == "50000000000000"


def test_documented_failure_can_be_later_than_planned_duration() -> None:
    result = calculate_pmn_reference(_input(failure=PmnFailureInput("exact_supported", "100.001")))
    assert result.total_duration_s_exact.decimal == "100"
    assert result.failure_result.cycles_to_failure == "3"


@pytest.mark.parametrize(
    ("section", "index", "field", "value"),
    [
        ("maximum_rpm", None, "decimal", "1801"),
        ("phases", 0, "end_rpm", {"numerator": "-1", "denominator": "1", "decimal": "-1", "decimal_preview": "-1"}),
        ("phases", 1, "start_rpm", {"numerator": "1799", "denominator": "1", "decimal": "1799", "decimal_preview": "1799"}),
    ],
)
def test_result_snapshot_rejects_inconsistent_exact_values_and_speed_profile(section: str, index: int | None, field: str, value: object) -> None:
    snapshot = PmnReferenceResultModel.model_validate_json(json.dumps(asdict(calculate_pmn_reference(_input()))))
    payload = snapshot.model_dump(mode="json")
    if index is None:
        payload[section][field] = value
    else:
        payload[section][index][field] = value
    with pytest.raises(ValidationError):
        PmnReferenceResultModel.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(("field", "replacement"), [("total_duration_min_exact", Fraction(2)), ("total_duration_h_exact", Fraction(1, 35))])
def test_result_snapshot_rejects_canonical_but_inconsistent_time_conversion(field: str, replacement: Fraction) -> None:
    payload = PmnReferenceResultModel.model_validate_json(json.dumps(asdict(calculate_pmn_reference(_input())))).model_dump(mode="json")
    payload[field] = asdict(exact_value(replacement))
    with pytest.raises(ValidationError, match="pmn_duration_conversion_invalid"):
        PmnReferenceResultModel.model_validate_json(json.dumps(payload))


def test_result_snapshot_rejects_unknown_failure_reason() -> None:
    payload = PmnReferenceResultModel.model_validate_json(json.dumps(asdict(calculate_pmn_reference(_input())))).model_dump(mode="json")
    payload["failure_result"]["reason_code"] = "unknown_reason"
    with pytest.raises(ValidationError):
        PmnReferenceResultModel.model_validate_json(json.dumps(payload))
