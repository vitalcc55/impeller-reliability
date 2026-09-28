from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Final, Literal

from impeller_reliability.calculations.exact import (
    ExactInputError,
    ExactRationalValue,
    canonical_decimal,
    exact_value,
    nonnegative_decimal,
    positive_decimal,
    positive_integer,
)

RptFailureApplicability = Literal[
    "exact_supported",
    "unavailable",
    "interval_endpoint",
    "right_censored",
    "ambiguous_pauses",
    "variable_cycle",
    "unknown_start",
    "repeated_attempts",
]
RptLowerPointPolicy = Literal["one_percent", "full_stop", "explicit_rpm"]

ALGORITHM_ID: Final = "rpt_reference"
ALGORITHM_VERSION: Final = "1.0.0"
NUMERIC_POLICY: Final = "exact_fraction_v1"


class RptCalculationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class RptFailureInput:
    applicability: RptFailureApplicability
    duration_to_failure_s: str | None


@dataclass(frozen=True, slots=True)
class RptReferenceInput:
    nominal_rpm: str
    design_cycles: str
    reserve_factor: str
    acceleration_duration_s: str
    steady_duration_s: str
    deceleration_duration_s: str
    failure: RptFailureInput | None = None


@dataclass(frozen=True, slots=True)
class RptPhase:
    phase: Literal["acceleration", "steady_rotation", "deceleration"]
    start_s: ExactRationalValue
    end_s: ExactRationalValue
    start_rpm: ExactRationalValue
    end_rpm: ExactRationalValue


@dataclass(frozen=True, slots=True)
class RptDiagramPoint:
    boundary: Literal[
        "cycle_start",
        "acceleration_end",
        "steady_end",
        "cycle_end",
        "repeat_acceleration_end",
        "repeat_steady_end",
        "repeat_cycle_end",
    ]
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class RptFailureResult:
    status: Literal["calculated", "not_applicable"]
    cycles_to_failure: str | None
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class RptLowerPointComparison:
    source_policy: RptLowerPointPolicy
    target_policy: RptLowerPointPolicy
    source_explicit_lower_rpm: str | None
    target_lower_rpm: str
    status: Literal[
        "matches_typical_formula",
        "differs_from_typical_formula",
        "source_target_policy_conflict",
        "target_unavailable",
    ]


@dataclass(frozen=True, slots=True)
class RptReferenceResult:
    algorithm_id: Literal["rpt_reference"]
    algorithm_version: Literal["1.0.0"]
    numeric_policy: Literal["exact_fraction_v1"]
    maximum_rpm: ExactRationalValue
    minimum_rpm: ExactRationalValue
    required_cycles_exact: ExactRationalValue
    cycle_duration_s_exact: ExactRationalValue
    total_duration_s_exact: ExactRationalValue
    total_duration_h_exact: ExactRationalValue
    failure_result: RptFailureResult
    phases: tuple[RptPhase, RptPhase, RptPhase]
    diagram_points: tuple[
        RptDiagramPoint,
        RptDiagramPoint,
        RptDiagramPoint,
        RptDiagramPoint,
        RptDiagramPoint,
        RptDiagramPoint,
        RptDiagramPoint,
    ]
    formula_references: tuple[str, ...]


def calculate_rpt_reference(values: object) -> RptReferenceResult:
    if not isinstance(values, RptReferenceInput):
        raise RptCalculationError("invalid_input_type", "Ожидался типизированный набор входов РПТ.")
    try:
        nominal_rpm = positive_decimal(values.nominal_rpm, "nominal_rpm", Decimal("1000000"))
        design_cycles = positive_integer(values.design_cycles, "design_cycles", 1_000_000_000_000)
        reserve_factor = positive_decimal(values.reserve_factor, "reserve_factor", Decimal("1000000"))
        acceleration = nonnegative_decimal(values.acceleration_duration_s, "acceleration_duration_s", Decimal("1000000000"))
        steady = nonnegative_decimal(values.steady_duration_s, "steady_duration_s", Decimal("1000000000"))
        deceleration = nonnegative_decimal(values.deceleration_duration_s, "deceleration_duration_s", Decimal("1000000000"))
    except ExactInputError as error:
        raise RptCalculationError(error.reason_code, str(error)) from error

    acceleration_fraction = Fraction(acceleration)
    steady_fraction = Fraction(steady)
    deceleration_fraction = Fraction(deceleration)
    cycle_duration = acceleration_fraction + steady_fraction + deceleration_fraction
    if cycle_duration <= 0:
        raise RptCalculationError("cycle_duration_zero", "Продолжительность цикла РПТ должна быть положительной.")
    maximum_rpm = Fraction(nominal_rpm)
    minimum_rpm = maximum_rpm / 100
    required_cycles = Fraction(design_cycles) * Fraction(reserve_factor)
    total_duration = cycle_duration * required_cycles
    steady_end = acceleration_fraction + steady_fraction

    return RptReferenceResult(
        algorithm_id=ALGORITHM_ID,
        algorithm_version=ALGORITHM_VERSION,
        numeric_policy=NUMERIC_POLICY,
        maximum_rpm=exact_value(maximum_rpm),
        minimum_rpm=exact_value(minimum_rpm),
        required_cycles_exact=exact_value(required_cycles),
        cycle_duration_s_exact=exact_value(cycle_duration),
        total_duration_s_exact=exact_value(total_duration),
        total_duration_h_exact=exact_value(total_duration / 3600),
        failure_result=_calculate_failure(values.failure, cycle_duration),
        phases=(
            RptPhase(
                phase="acceleration",
                start_s=exact_value(Fraction(0)),
                end_s=exact_value(acceleration_fraction),
                start_rpm=exact_value(minimum_rpm),
                end_rpm=exact_value(maximum_rpm),
            ),
            RptPhase(
                phase="steady_rotation",
                start_s=exact_value(acceleration_fraction),
                end_s=exact_value(steady_end),
                start_rpm=exact_value(maximum_rpm),
                end_rpm=exact_value(maximum_rpm),
            ),
            RptPhase(
                phase="deceleration",
                start_s=exact_value(steady_end),
                end_s=exact_value(cycle_duration),
                start_rpm=exact_value(maximum_rpm),
                end_rpm=exact_value(minimum_rpm),
            ),
        ),
        diagram_points=_diagram_points(acceleration_fraction, steady_end, cycle_duration),
        formula_references=(
            "ПМИ Р130У, редакция 01, 2024, страница 14, формула 4",
            "ПМИ Р130У, редакция 01, 2024, страница 14, формула 5",
            "ПМИ Р130У, редакция 01, 2024, страница 14, формула 6",
            "ПМИ Р130У, редакция 01, 2024, страница 14, формула 7",
            "ПМИ Р130У, редакция 01, 2024, страница 13, таблица 4",
        ),
    )


def _diagram_points(
    acceleration_end: Fraction,
    steady_end: Fraction,
    cycle_end: Fraction,
) -> tuple[
    RptDiagramPoint,
    RptDiagramPoint,
    RptDiagramPoint,
    RptDiagramPoint,
    RptDiagramPoint,
    RptDiagramPoint,
    RptDiagramPoint,
]:
    # Two illustrative cycles remain bounded regardless of the required cycle count.
    acceleration_x = round(acceleration_end * 1000 / cycle_end)
    steady_x = round(steady_end * 1000 / cycle_end)
    return (
        RptDiagramPoint("cycle_start", 0, 100),
        RptDiagramPoint("acceleration_end", acceleration_x, 0),
        RptDiagramPoint("steady_end", steady_x, 0),
        RptDiagramPoint("cycle_end", 1000, 100),
        RptDiagramPoint("repeat_acceleration_end", 1000 + acceleration_x, 0),
        RptDiagramPoint("repeat_steady_end", 1000 + steady_x, 0),
        RptDiagramPoint("repeat_cycle_end", 2000, 100),
    )


def _calculate_failure(failure: object | None, cycle_duration: Fraction) -> RptFailureResult:
    if failure is None:
        return RptFailureResult("not_applicable", None, "failure_duration_unavailable")
    if not isinstance(failure, RptFailureInput):
        raise RptCalculationError("invalid_input_type", "Ожидался типизированный набор входов отказа РПТ.")
    reason_by_applicability = {
        "unavailable": "failure_duration_unavailable",
        "interval_endpoint": "failure_endpoint_interval",
        "right_censored": "failure_not_observed",
        "ambiguous_pauses": "failure_structure_ambiguous",
        "variable_cycle": "failure_cycle_variable",
        "unknown_start": "failure_start_unknown",
        "repeated_attempts": "failure_attempts_ambiguous",
    }
    applicability = _validated_failure_applicability(failure.applicability)
    if applicability != "exact_supported":
        return RptFailureResult("not_applicable", None, reason_by_applicability[applicability])
    if failure.duration_to_failure_s is None:
        raise RptCalculationError("failure_duration_required", "Для расчёта по таблице 4 требуется документированное время до отказа.")
    try:
        duration = nonnegative_decimal(failure.duration_to_failure_s, "duration_to_failure_s", Decimal("1000000000000"))
    except ExactInputError as error:
        raise RptCalculationError(error.reason_code, str(error)) from error
    return RptFailureResult("calculated", str((Fraction(duration) / cycle_duration).__floor__()), None)


def _validated_failure_applicability(value: object) -> RptFailureApplicability:
    if value == "exact_supported":
        return "exact_supported"
    if value == "unavailable":
        return "unavailable"
    if value == "interval_endpoint":
        return "interval_endpoint"
    if value == "right_censored":
        return "right_censored"
    if value == "ambiguous_pauses":
        return "ambiguous_pauses"
    if value == "variable_cycle":
        return "variable_cycle"
    if value == "unknown_start":
        return "unknown_start"
    if value == "repeated_attempts":
        return "repeated_attempts"
    raise RptCalculationError("invalid_failure_applicability", "Неподдерживаемая применимость таблицы 4 РПТ.")


def compare_rpt_lower_point(
    minimum_rpm: ExactRationalValue,
    source_policy: RptLowerPointPolicy,
    target_policy: RptLowerPointPolicy,
    source_explicit_lower_rpm: str | None,
    target_lower_rpm: str,
) -> RptLowerPointComparison:
    status: Literal[
        "matches_typical_formula",
        "differs_from_typical_formula",
        "source_target_policy_conflict",
        "target_unavailable",
    ]
    if source_policy != target_policy:
        status = "source_target_policy_conflict"
    else:
        try:
            target = Fraction(canonical_decimal(target_lower_rpm, "target_lower_rpm"))
        except ExactInputError:
            status = "target_unavailable"
        else:
            typical = Fraction(int(minimum_rpm.numerator), int(minimum_rpm.denominator))
            status = "matches_typical_formula" if target == typical else "differs_from_typical_formula"
    return RptLowerPointComparison(
        source_policy=source_policy,
        target_policy=target_policy,
        source_explicit_lower_rpm=source_explicit_lower_rpm,
        target_lower_rpm=target_lower_rpm,
        status=status,
    )
