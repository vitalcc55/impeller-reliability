from __future__ import annotations

import json
from pathlib import Path

import pytest

from impeller_reliability.calculations.rbd import (
    RbdCalculationError,
    RbdFailureApplicability,
    RbdFailureInput,
    RbdReferenceInput,
    calculate_rbd_reference,
)

GOLDEN_PATH = Path(__file__).parents[4] / "fixtures" / "golden" / "rbd-reference-v1.json"


def test_rbd_reference_examples_are_independent_golden_values() -> None:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    first, second, third = golden["cases"]

    first_result = calculate_rbd_reference(_reference_input(first["input"]))
    assert first_result.required_cycles_exact.decimal == first["expected"]["requiredCyclesExact"]
    assert first_result.steady_duration_s_exact.decimal == first["expected"]["steadyDurationSExact"]
    assert first_result.cycle_duration_s_exact.decimal == first["expected"]["cycleDurationSExact"]
    assert first_result.total_duration_s_exact.decimal == first["expected"]["totalDurationSExact"]

    second_result = calculate_rbd_reference(_reference_input(second["input"]))
    assert second_result.required_cycles_exact.decimal == second["expected"]["requiredCyclesExact"]
    assert second_result.steady_duration_s_exact.decimal == second["expected"]["steadyDurationSExact"]
    assert second_result.cycle_duration_s_exact.decimal == second["expected"]["cycleDurationSExact"]
    assert second_result.total_duration_s_exact.decimal == second["expected"]["totalDurationSExact"]
    assert second_result.required_cycles_exact.decimal != str(second["producerTargets"]["targetCycles"])
    assert second_result.steady_duration_s_exact.decimal != second["producerTargets"]["targetSteadyDurationS"]
    assert [(point.boundary, point.x, point.y) for point in second_result.diagram_points] == [
        ("start", 0, 100),
        ("acceleration_end", 150, 0),
        ("steady_end", 850, 0),
        ("cycle_end", 1000, 100),
    ]

    third_result = calculate_rbd_reference(
        _reference_input(
            third["input"],
            failure=RbdFailureInput(applicability="exact_supported", duration_to_failure_s="60000"),
        )
    )
    assert third_result.failure_result.status == "calculated"
    assert third_result.failure_result.cycles_to_failure == third["expected"]["failureCycles"]


@pytest.mark.parametrize(
    ("duration", "expected"),
    [("10.039", "0"), ("10.040", "1"), ("10.041", "1")],
)
def test_table_3_floor_is_exact_at_integer_boundary(duration: str, expected: str) -> None:
    result = calculate_rbd_reference(
        RbdReferenceInput(
            nominal_rpm="1500",
            base_cycles="1",
            reserve_factor="1",
            acceleration_duration_s="10",
            deceleration_duration_s="10",
            failure=RbdFailureInput(
                applicability="exact_supported",
                duration_to_failure_s=duration,
            ),
        )
    )
    assert result.failure_result.cycles_to_failure == expected


def test_periodic_division_keeps_exact_fraction_and_bounded_preview() -> None:
    result = calculate_rbd_reference(
        RbdReferenceInput(
            nominal_rpm="7",
            base_cycles="1",
            reserve_factor="1",
            acceleration_duration_s="0",
            deceleration_duration_s="0",
            failure=RbdFailureInput(applicability="unavailable", duration_to_failure_s=None),
        )
    )
    assert result.steady_duration_s_exact.numerator == "60"
    assert result.steady_duration_s_exact.denominator == "7"
    assert result.steady_duration_s_exact.decimal is None
    assert result.steady_duration_s_exact.decimal_preview == "8.571428571428…"
    assert result.failure_result.status == "not_applicable"
    assert result.failure_result.reason_code == "failure_duration_unavailable"


@pytest.mark.parametrize(
    "field_patch",
    [
        {"nominal_rpm": "0"},
        {"base_cycles": "0"},
        {"reserve_factor": "-1"},
        {"acceleration_duration_s": "-0.1"},
        {"deceleration_duration_s": "NaN"},
        {"nominal_rpm": "Infinity"},
        {"reserve_factor": "1e999999999"},
        {"reserve_factor": "01"},
        {"reserve_factor": "1_000"},
        {"reserve_factor": "١"},
        {"reserve_factor": "0.0000000000001"},
        {"base_cycles": "1000000000001"},
        {"nominal_rpm": "1000000.000000000001"},
    ],
)
def test_rbd_numeric_contract_rejects_invalid_or_excessive_values(
    field_patch: dict[str, str],
) -> None:
    values = {
        "nominal_rpm": "1500",
        "base_cycles": "2000000",
        "reserve_factor": "1.5",
        "acceleration_duration_s": "10",
        "deceleration_duration_s": "10",
    }
    values.update(field_patch)
    with pytest.raises(RbdCalculationError) as raised:
        calculate_rbd_reference(
            RbdReferenceInput(
                nominal_rpm=values["nominal_rpm"],
                base_cycles=values["base_cycles"],
                reserve_factor=values["reserve_factor"],
                acceleration_duration_s=values["acceleration_duration_s"],
                deceleration_duration_s=values["deceleration_duration_s"],
            )
        )
    assert raised.value.reason_code in {"invalid_numeric_format", "numeric_out_of_range"}


def test_smallest_supported_values_and_upper_limits_remain_exact() -> None:
    result = calculate_rbd_reference(
        RbdReferenceInput(
            nominal_rpm="1000000",
            base_cycles="1000000000000",
            reserve_factor="0.000000000001",
            acceleration_duration_s="0",
            deceleration_duration_s="1000000000",
        )
    )
    assert result.required_cycles_exact.decimal == "1"
    assert result.steady_duration_s_exact.decimal == "0.00006"
    assert result.total_duration_s_exact.decimal == "1000000000.00006"


def test_failure_cycles_are_returned_as_an_exact_unbounded_integer_string() -> None:
    result = calculate_rbd_reference(
        RbdReferenceInput(
            nominal_rpm="1000000",
            base_cycles="1",
            reserve_factor="1",
            acceleration_duration_s="0",
            deceleration_duration_s="0",
            failure=RbdFailureInput(
                applicability="exact_supported",
                duration_to_failure_s="1000000000000",
            ),
        )
    )
    assert result.failure_result.cycles_to_failure == "16666666666666666"


@pytest.mark.parametrize("invalid_field", ["numeric_type", "applicability", "applicability_type"])
def test_runtime_type_and_applicability_errors_are_typed(invalid_field: str) -> None:
    failure = RbdFailureInput(applicability="unavailable", duration_to_failure_s=None)
    values = RbdReferenceInput(
        nominal_rpm="1500",
        base_cycles="1",
        reserve_factor="1",
        acceleration_duration_s="0",
        deceleration_duration_s="0",
        failure=failure,
    )
    if invalid_field == "numeric_type":
        object.__setattr__(values, "nominal_rpm", 1500)
    elif invalid_field == "applicability":
        object.__setattr__(failure, "applicability", "unknown")
    else:
        object.__setattr__(failure, "applicability", [])
    with pytest.raises(RbdCalculationError) as raised:
        calculate_rbd_reference(values)
    assert raised.value.reason_code in {"invalid_input_type", "invalid_failure_applicability"}


def test_phase_profile_follows_figure_1_without_claiming_measurements() -> None:
    result = calculate_rbd_reference(
        RbdReferenceInput(
            nominal_rpm="1500",
            base_cycles="1000",
            reserve_factor="1.5003",
            acceleration_duration_s="5",
            deceleration_duration_s="5",
        )
    )
    assert [(phase.phase, phase.start_rpm.decimal, phase.end_rpm.decimal) for phase in result.phases] == [
        ("acceleration", "0", "1500"),
        ("steady_rotation", "1500", "1500"),
        ("deceleration", "1500", "0"),
    ]


@pytest.mark.parametrize(
    ("applicability", "duration", "reason"),
    [
        ("unavailable", None, "failure_duration_unavailable"),
        ("interval_endpoint", None, "failure_endpoint_interval"),
        ("right_censored", None, "failure_not_observed"),
        ("unsupported_phase", "20", "failure_phase_unsupported"),
        ("ambiguous_pauses", "20", "failure_structure_ambiguous"),
    ],
)
def test_failure_not_applicable_reasons_do_not_block_reference_calculation(
    applicability: RbdFailureApplicability,
    duration: str | None,
    reason: str,
) -> None:
    result = calculate_rbd_reference(
        _reference_input(
            {
                "nominalRpm": "1500",
                "baseCycles": "1000",
                "reserveFactor": "1.5",
                "accelerationDurationS": "5",
                "decelerationDurationS": "5",
            },
            failure=RbdFailureInput(applicability=applicability, duration_to_failure_s=duration),
        )
    )
    assert result.required_cycles_exact.decimal == "1500"
    assert result.failure_result.status == "not_applicable"
    assert result.failure_result.reason_code == reason


def test_table_3_rejects_negative_subexpression_instead_of_clamping_to_zero() -> None:
    with pytest.raises(RbdCalculationError) as raised:
        calculate_rbd_reference(
            RbdReferenceInput(
                nominal_rpm="1500",
                base_cycles="1",
                reserve_factor="1",
                acceleration_duration_s="10",
                deceleration_duration_s="10",
                failure=RbdFailureInput(
                    applicability="exact_supported",
                    duration_to_failure_s="9.999",
                ),
            )
        )
    assert raised.value.reason_code == "failure_before_acceleration_complete"


def _reference_input(
    values: dict[str, object],
    *,
    failure: RbdFailureInput | None = None,
) -> RbdReferenceInput:
    return RbdReferenceInput(
        nominal_rpm=str(values["nominalRpm"]),
        base_cycles=str(values["baseCycles"]),
        reserve_factor=str(values["reserveFactor"]),
        acceleration_duration_s=str(values["accelerationDurationS"]),
        deceleration_duration_s=str(values["decelerationDurationS"]),
        failure=failure,
    )
