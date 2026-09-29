from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import TYPE_CHECKING, Final, Literal

from impeller_reliability.calculations.exact import (
    ExactInputError,
    ExactRationalValue,
    exact_value,
    nonnegative_decimal,
    positive_decimal,
    positive_integer,
)

if TYPE_CHECKING:
    from impeller_reliability.calculations.pmn_result_snapshot import PmnReferenceResultModel

PmnFailureApplicability = Literal[
    "exact_supported",
    "unavailable",
    "interval_endpoint",
    "right_censored",
    "ambiguous_pauses",
    "variable_cycle",
    "unknown_start",
    "repeated_attempts",
]
PmnFailureReason = Literal[
    "failure_duration_unavailable",
    "failure_endpoint_interval",
    "failure_not_observed",
    "failure_structure_ambiguous",
    "failure_cycle_variable",
    "failure_start_unknown",
    "failure_attempts_ambiguous",
]

ALGORITHM_ID: Final = "pmn_reference"
ALGORITHM_VERSION: Final = "1.0.0"
NUMERIC_POLICY: Final = "exact_fraction_v1"
FORMULA_REFERENCES: Final = (
    "ПМИ Р130У, редакция 01, 2024, страница 15, формула 8",
    "ПМИ Р130У, редакция 01, 2024, страница 15, формула 9",
    "ПМИ Р130У, редакция 01, 2024, страница 16, формула 10",
    "ПМИ Р130У, редакция 01, 2024, страница 15, таблица 5",
)

_FAILURE_REASON_BY_APPLICABILITY: Final[dict[str, PmnFailureReason]] = {
    "unavailable": "failure_duration_unavailable",
    "interval_endpoint": "failure_endpoint_interval",
    "right_censored": "failure_not_observed",
    "ambiguous_pauses": "failure_structure_ambiguous",
    "variable_cycle": "failure_cycle_variable",
    "unknown_start": "failure_start_unknown",
    "repeated_attempts": "failure_attempts_ambiguous",
}


class PmnCalculationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class PmnFailureInput:
    applicability: PmnFailureApplicability
    duration_to_failure_s: str | None


@dataclass(frozen=True, slots=True)
class PmnReferenceInput:
    nominal_rpm: str
    speed_factor: str
    target_cycles: str
    acceleration_duration_s: str
    steady_duration_s: str
    deceleration_duration_s: str
    failure: PmnFailureInput | None = None


@dataclass(frozen=True, slots=True)
class PmnPhase:
    phase: Literal["acceleration", "steady_rotation", "deceleration"]
    start_s: ExactRationalValue
    end_s: ExactRationalValue
    start_rpm: ExactRationalValue
    end_rpm: ExactRationalValue


@dataclass(frozen=True, slots=True)
class PmnDiagramPoint:
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
class PmnFailureResult:
    status: Literal["calculated", "not_applicable"]
    cycles_to_failure: str | None
    reason_code: PmnFailureReason | None


@dataclass(frozen=True, slots=True)
class PmnReferenceResult:
    algorithm_id: Literal["pmn_reference"]
    algorithm_version: Literal["1.0.0"]
    numeric_policy: Literal["exact_fraction_v1"]
    maximum_rpm: ExactRationalValue
    cycle_duration_s_exact: ExactRationalValue
    total_duration_s_exact: ExactRationalValue
    total_duration_min_exact: ExactRationalValue
    total_duration_h_exact: ExactRationalValue
    failure_result: PmnFailureResult
    phases: tuple[PmnPhase, PmnPhase, PmnPhase]
    diagram_points: tuple[
        PmnDiagramPoint,
        PmnDiagramPoint,
        PmnDiagramPoint,
        PmnDiagramPoint,
        PmnDiagramPoint,
        PmnDiagramPoint,
        PmnDiagramPoint,
    ]
    formula_references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PmnQuantities:
    acceleration: Fraction
    steady_end: Fraction
    cycle_duration: Fraction
    maximum_rpm: Fraction
    total_duration: Fraction


def validate_pmn_reference_input(values: object) -> tuple[Decimal, Decimal, int, Decimal, Decimal, Decimal]:
    if not isinstance(values, PmnReferenceInput):
        raise PmnCalculationError("invalid_input_type", "Ожидался типизированный набор входов ПМН.")
    try:
        nominal_rpm = positive_decimal(values.nominal_rpm, "nominal_rpm", Decimal("1000000"))
        speed_factor = positive_decimal(values.speed_factor, "speed_factor", Decimal("1000000"))
        target_cycles = positive_integer(values.target_cycles, "target_cycles", 1_000_000_000_000)
        acceleration = nonnegative_decimal(values.acceleration_duration_s, "acceleration_duration_s", Decimal("1000000000"))
        steady = nonnegative_decimal(values.steady_duration_s, "steady_duration_s", Decimal("1000000000"))
        deceleration = nonnegative_decimal(values.deceleration_duration_s, "deceleration_duration_s", Decimal("1000000000"))
    except ExactInputError as error:
        raise PmnCalculationError(error.reason_code, str(error)) from error

    if acceleration + steady + deceleration <= 0:
        raise PmnCalculationError("cycle_duration_zero", "Продолжительность цикла ПМН должна быть положительной.")
    failure = _validated_failure_input(values.failure)
    if failure is not None:
        applicability = _validated_failure_applicability(failure.applicability)
        if applicability == "exact_supported":
            _validated_failure_duration(failure)
        elif failure.duration_to_failure_s is not None:
            raise PmnCalculationError("failure_duration_not_applicable", "Время до отказа допустимо только для точного документированного случая.")
    return nominal_rpm, speed_factor, target_cycles, acceleration, steady, deceleration


def calculate_pmn_reference(values: object) -> PmnReferenceResult:
    if not isinstance(values, PmnReferenceInput):
        raise PmnCalculationError("invalid_input_type", "Ожидался типизированный набор входов ПМН.")
    quantities = _reference_quantities(validate_pmn_reference_input(values))
    acceleration_fraction = quantities.acceleration
    steady_end = quantities.steady_end
    cycle_duration = quantities.cycle_duration
    maximum_rpm = quantities.maximum_rpm
    total_duration = quantities.total_duration

    return PmnReferenceResult(
        algorithm_id=ALGORITHM_ID,
        algorithm_version=ALGORITHM_VERSION,
        numeric_policy=NUMERIC_POLICY,
        maximum_rpm=exact_value(maximum_rpm),
        cycle_duration_s_exact=exact_value(cycle_duration),
        total_duration_s_exact=exact_value(total_duration),
        total_duration_min_exact=exact_value(total_duration / 60),
        total_duration_h_exact=exact_value(total_duration / 3600),
        failure_result=_calculate_failure(values.failure, cycle_duration),
        phases=(
            PmnPhase("acceleration", exact_value(Fraction(0)), exact_value(acceleration_fraction), exact_value(Fraction(0)), exact_value(maximum_rpm)),
            PmnPhase("steady_rotation", exact_value(acceleration_fraction), exact_value(steady_end), exact_value(maximum_rpm), exact_value(maximum_rpm)),
            PmnPhase("deceleration", exact_value(steady_end), exact_value(cycle_duration), exact_value(maximum_rpm), exact_value(Fraction(0))),
        ),
        diagram_points=_diagram_points(acceleration_fraction, steady_end, cycle_duration),
        formula_references=FORMULA_REFERENCES,
    )


def validate_pmn_saved_result(values: PmnReferenceInput, result: PmnReferenceResultModel) -> None:
    quantities = _reference_quantities(validate_pmn_reference_input(values))
    if (
        _snapshot_fraction(result.maximum_rpm.numerator, result.maximum_rpm.denominator) != quantities.maximum_rpm
        or _snapshot_fraction(result.cycle_duration_s_exact.numerator, result.cycle_duration_s_exact.denominator) != quantities.cycle_duration
        or _snapshot_fraction(result.total_duration_s_exact.numerator, result.total_duration_s_exact.denominator) != quantities.total_duration
        or _snapshot_fraction(result.phases[0].end_s.numerator, result.phases[0].end_s.denominator) != quantities.acceleration
        or _snapshot_fraction(result.phases[1].end_s.numerator, result.phases[1].end_s.denominator) != quantities.steady_end
        or tuple(result.formula_references) != FORMULA_REFERENCES
    ):
        raise PmnCalculationError("saved_result_mismatch", "Сохранённый результат ПМН не соответствует выбранным входам.")
    expected_failure = _calculate_failure(values.failure, quantities.cycle_duration)
    if (
        result.failure_result.status != expected_failure.status
        or result.failure_result.cycles_to_failure != expected_failure.cycles_to_failure
        or result.failure_result.reason_code != expected_failure.reason_code
    ):
        raise PmnCalculationError("saved_result_mismatch", "Показатель таблицы 5 не соответствует сохранённой применимости.")
    expected_points = _diagram_points(quantities.acceleration, quantities.steady_end, quantities.cycle_duration)
    if tuple((point.boundary, point.x, point.y) for point in result.diagram_points) != tuple((point.boundary, point.x, point.y) for point in expected_points):
        raise PmnCalculationError("saved_result_mismatch", "Схема фаз не соответствует сохранённым длительностям.")


def _reference_quantities(values: tuple[Decimal, Decimal, int, Decimal, Decimal, Decimal]) -> _PmnQuantities:
    nominal_rpm, speed_factor, target_cycles, acceleration, steady, deceleration = values
    acceleration_fraction = Fraction(acceleration)
    steady_end = acceleration_fraction + Fraction(steady)
    cycle_duration = steady_end + Fraction(deceleration)
    return _PmnQuantities(
        acceleration=acceleration_fraction,
        steady_end=steady_end,
        cycle_duration=cycle_duration,
        maximum_rpm=Fraction(nominal_rpm) * Fraction(speed_factor),
        total_duration=cycle_duration * target_cycles,
    )


def _snapshot_fraction(numerator: str, denominator: str) -> Fraction:
    return Fraction(int(numerator), int(denominator))


def _diagram_points(
    acceleration_end: Fraction, steady_end: Fraction, cycle_end: Fraction
) -> tuple[PmnDiagramPoint, PmnDiagramPoint, PmnDiagramPoint, PmnDiagramPoint, PmnDiagramPoint, PmnDiagramPoint, PmnDiagramPoint]:
    # Two illustrative cycles keep the profile bounded independently of target_cycles.
    acceleration_x = round(acceleration_end * 1000 / cycle_end)
    steady_x = round(steady_end * 1000 / cycle_end)
    return (
        PmnDiagramPoint("cycle_start", 0, 100),
        PmnDiagramPoint("acceleration_end", acceleration_x, 0),
        PmnDiagramPoint("steady_end", steady_x, 0),
        PmnDiagramPoint("cycle_end", 1000, 100),
        PmnDiagramPoint("repeat_acceleration_end", 1000 + acceleration_x, 0),
        PmnDiagramPoint("repeat_steady_end", 1000 + steady_x, 0),
        PmnDiagramPoint("repeat_cycle_end", 2000, 100),
    )


def _calculate_failure(failure: object | None, cycle_duration: Fraction) -> PmnFailureResult:
    failure = _validated_failure_input(failure)
    if failure is None:
        return PmnFailureResult("not_applicable", None, "failure_duration_unavailable")
    applicability = _validated_failure_applicability(failure.applicability)
    if applicability != "exact_supported":
        return PmnFailureResult("not_applicable", None, failure_reason_for_applicability(applicability))
    duration = _validated_failure_duration(failure)
    return PmnFailureResult("calculated", str((Fraction(duration) / cycle_duration).__ceil__()), None)


def failure_reason_for_applicability(applicability: PmnFailureApplicability) -> PmnFailureReason | None:
    return None if applicability == "exact_supported" else _FAILURE_REASON_BY_APPLICABILITY[applicability]


def _validated_failure_input(failure: object) -> PmnFailureInput | None:
    if failure is None:
        return None
    if not isinstance(failure, PmnFailureInput):
        raise PmnCalculationError("invalid_input_type", "Ожидался типизированный набор входов отказа ПМН.")
    return failure


def _validated_failure_duration(failure: PmnFailureInput) -> Decimal:
    if failure.duration_to_failure_s is None:
        raise PmnCalculationError("failure_duration_required", "Для расчёта по таблице 5 требуется документированное время до отказа.")
    try:
        return nonnegative_decimal(failure.duration_to_failure_s, "duration_to_failure_s", Decimal("1000000000000"))
    except ExactInputError as error:
        raise PmnCalculationError(error.reason_code, str(error)) from error


def _validated_failure_applicability(value: object) -> PmnFailureApplicability:
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
    raise PmnCalculationError("invalid_failure_applicability", "Неподдерживаемая применимость таблицы 5 ПМН.")
