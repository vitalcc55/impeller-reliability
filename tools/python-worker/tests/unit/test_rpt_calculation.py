from __future__ import annotations

import pytest

from impeller_reliability.calculations.rpt import (
    RptCalculationError,
    RptFailureApplicability,
    RptFailureInput,
    RptReferenceInput,
    calculate_rpt_reference,
)


def _input(
    *,
    nominal_rpm: str = "1500",
    design_cycles: str = "1000",
    reserve_factor: str = "1.5",
    acceleration_duration_s: str = "10",
    steady_duration_s: str = "0",
    deceleration_duration_s: str = "10",
    failure: RptFailureInput | None = None,
) -> RptReferenceInput:
    return RptReferenceInput(
        nominal_rpm=nominal_rpm,
        design_cycles=design_cycles,
        reserve_factor=reserve_factor,
        acceleration_duration_s=acceleration_duration_s,
        steady_duration_s=steady_duration_s,
        deceleration_duration_s=deceleration_duration_s,
        failure=failure,
    )


def test_pmi_reference_and_table_4_independent_example() -> None:
    result = calculate_rpt_reference(_input(failure=RptFailureInput(applicability="exact_supported", duration_to_failure_s="20000")))
    assert result.maximum_rpm.decimal == "1500"
    assert result.minimum_rpm.decimal == "15"
    assert result.cycle_duration_s_exact.decimal == "20"
    assert result.required_cycles_exact.decimal == "1500"
    assert result.total_duration_s_exact.decimal == "30000"
    assert result.total_duration_h_exact.numerator == "25"
    assert result.total_duration_h_exact.denominator == "3"
    assert result.total_duration_h_exact.decimal is None
    assert result.failure_result.status == "calculated"
    assert result.failure_result.cycles_to_failure == "1000"
    assert result.phases[1].start_s.decimal == result.phases[1].end_s.decimal == "10"
    assert result.diagram_points[1].x == result.diagram_points[2].x


def test_fractional_requirement_is_not_rounded_to_producer_target() -> None:
    result = calculate_rpt_reference(_input(design_cycles="3", acceleration_duration_s="2", steady_duration_s="1", deceleration_duration_s="3"))
    assert result.required_cycles_exact.decimal == "4.5"
    assert result.cycle_duration_s_exact.decimal == "6"
    assert result.total_duration_s_exact.decimal == "27"
    assert result.minimum_rpm.decimal == "15"
    assert result.failure_result.status == "not_applicable"
    assert result.failure_result.reason_code == "failure_duration_unavailable"
    assert result.phases[1].end_s.decimal == "3"
    assert result.diagram_points[1].x < result.diagram_points[2].x


@pytest.mark.parametrize(
    ("duration", "expected"),
    [("19.999", "0"), ("20", "1"), ("20.001", "1"), ("39.999", "1"), ("40", "2")],
)
def test_table_4_uses_exact_floor_at_cycle_boundary(duration: str, expected: str) -> None:
    result = calculate_rpt_reference(_input(failure=RptFailureInput(applicability="exact_supported", duration_to_failure_s=duration)))
    assert result.failure_result.cycles_to_failure == expected


def test_decimal_boundary_does_not_use_binary_float() -> None:
    result = calculate_rpt_reference(
        _input(
            design_cycles="1",
            reserve_factor="1",
            acceleration_duration_s="0.1",
            steady_duration_s="0.1",
            deceleration_duration_s="0.1",
            failure=RptFailureInput(applicability="exact_supported", duration_to_failure_s="0.9"),
        )
    )
    assert result.cycle_duration_s_exact.decimal == "0.3"
    assert result.failure_result.cycles_to_failure == "3"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("nominal_rpm", "0", "numeric_out_of_range"),
        ("design_cycles", "0", "numeric_out_of_range"),
        ("design_cycles", "3.5", "invalid_numeric_format"),
        ("design_cycles", "1000000000001", "numeric_out_of_range"),
        ("reserve_factor", "-1", "invalid_numeric_format"),
        ("reserve_factor", "1e9999999", "invalid_numeric_format"),
        ("reserve_factor", "NaN", "invalid_numeric_format"),
        ("nominal_rpm", "Infinity", "invalid_numeric_format"),
        ("nominal_rpm", "١", "invalid_numeric_format"),
        ("acceleration_duration_s", "-0.1", "invalid_numeric_format"),
        ("steady_duration_s", "", "invalid_numeric_format"),
        ("steady_duration_s", "0.0000000000001", "invalid_numeric_format"),
        ("deceleration_duration_s", "1000000001", "numeric_out_of_range"),
    ],
)
def test_invalid_inputs_are_typed(field: str, value: str, reason: str) -> None:
    values = {
        "nominal_rpm": "1500",
        "design_cycles": "1000",
        "reserve_factor": "1.5",
        "acceleration_duration_s": "10",
        "steady_duration_s": "0",
        "deceleration_duration_s": "10",
    }
    values[field] = value
    with pytest.raises(RptCalculationError) as raised:
        calculate_rpt_reference(
            RptReferenceInput(
                nominal_rpm=values["nominal_rpm"],
                design_cycles=values["design_cycles"],
                reserve_factor=values["reserve_factor"],
                acceleration_duration_s=values["acceleration_duration_s"],
                steady_duration_s=values["steady_duration_s"],
                deceleration_duration_s=values["deceleration_duration_s"],
            )
        )
    assert raised.value.reason_code == reason


def test_zero_cycle_duration_is_rejected_but_zero_steady_duration_is_valid() -> None:
    with pytest.raises(RptCalculationError) as raised:
        calculate_rpt_reference(_input(acceleration_duration_s="0", steady_duration_s="0", deceleration_duration_s="0"))
    assert raised.value.reason_code == "cycle_duration_zero"


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
def test_inapplicable_failure_does_not_block_reference(applicability: RptFailureApplicability, reason: str) -> None:
    result = calculate_rpt_reference(_input(failure=RptFailureInput(applicability=applicability, duration_to_failure_s=None)))
    assert result.failure_result.status == "not_applicable"
    assert result.failure_result.reason_code == reason
    assert result.total_duration_s_exact.decimal == "30000"


def test_exact_failure_requires_documented_duration_and_accepts_zero() -> None:
    with pytest.raises(RptCalculationError) as missing:
        calculate_rpt_reference(_input(failure=RptFailureInput(applicability="exact_supported", duration_to_failure_s=None)))
    assert missing.value.reason_code == "failure_duration_required"
    zero = calculate_rpt_reference(_input(failure=RptFailureInput(applicability="exact_supported", duration_to_failure_s="0")))
    assert zero.failure_result.cycles_to_failure == "0"
