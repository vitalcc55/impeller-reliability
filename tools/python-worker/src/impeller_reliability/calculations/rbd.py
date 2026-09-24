from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
import re
from typing import Final, Literal

RbdFailureApplicability = Literal[
    "exact_supported",
    "unavailable",
    "interval_endpoint",
    "right_censored",
    "unsupported_phase",
    "ambiguous_pauses",
]

ALGORITHM_ID: Final = "rbd_reference"
ALGORITHM_VERSION: Final = "1.0.0"
NUMERIC_POLICY: Final = "exact_fraction_v1"
_BOUNDED_DECIMAL_INPUT = re.compile(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?")
_CANONICAL_INTEGER = re.compile(r"(?:0|[1-9][0-9]{0,12})")
_DECIMAL_PREVIEW_DIGITS = 12


class RbdCalculationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class RbdFailureInput:
    applicability: RbdFailureApplicability
    duration_to_failure_s: str | None


@dataclass(frozen=True, slots=True)
class RbdReferenceInput:
    nominal_rpm: str
    base_cycles: str
    reserve_factor: str
    acceleration_duration_s: str
    deceleration_duration_s: str
    failure: RbdFailureInput | None = None


@dataclass(frozen=True, slots=True)
class ExactRationalValue:
    numerator: str
    denominator: str
    decimal: str | None
    decimal_preview: str


@dataclass(frozen=True, slots=True)
class RbdPhase:
    phase: Literal["acceleration", "steady_rotation", "deceleration"]
    start_s: ExactRationalValue
    end_s: ExactRationalValue
    start_rpm: ExactRationalValue
    end_rpm: ExactRationalValue


@dataclass(frozen=True, slots=True)
class RbdDiagramPoint:
    boundary: Literal["start", "acceleration_end", "steady_end", "cycle_end"]
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class RbdFailureResult:
    status: Literal["calculated", "not_applicable"]
    cycles_to_failure: str | None
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class RbdReferenceResult:
    algorithm_id: Literal["rbd_reference"]
    algorithm_version: Literal["1.0.0"]
    numeric_policy: Literal["exact_fraction_v1"]
    maximum_rpm: ExactRationalValue
    required_cycles_exact: ExactRationalValue
    steady_duration_s_exact: ExactRationalValue
    cycle_duration_s_exact: ExactRationalValue
    total_duration_s_exact: ExactRationalValue
    failure_result: RbdFailureResult
    phases: tuple[RbdPhase, RbdPhase, RbdPhase]
    diagram_points: tuple[RbdDiagramPoint, RbdDiagramPoint, RbdDiagramPoint, RbdDiagramPoint]
    formula_references: tuple[str, ...]


def calculate_rbd_reference(values: object) -> RbdReferenceResult:
    if not isinstance(values, RbdReferenceInput):
        raise RbdCalculationError("invalid_input_type", "Ожидался типизированный набор входов РБД.")
    nominal_rpm = _positive_decimal(values.nominal_rpm, "nominal_rpm", Decimal("1000000"))
    base_cycles = _positive_integer(values.base_cycles, "base_cycles", 1_000_000_000_000)
    reserve_factor = _positive_decimal(values.reserve_factor, "reserve_factor", Decimal("1000000"))
    acceleration = _nonnegative_decimal(
        values.acceleration_duration_s,
        "acceleration_duration_s",
        Decimal("1000000000"),
    )
    deceleration = _nonnegative_decimal(
        values.deceleration_duration_s,
        "deceleration_duration_s",
        Decimal("1000000000"),
    )

    nominal_fraction = Fraction(nominal_rpm)
    acceleration_fraction = Fraction(acceleration)
    deceleration_fraction = Fraction(deceleration)
    required_cycles = Fraction(base_cycles) * Fraction(reserve_factor)
    steady_duration = Fraction(60) * required_cycles / nominal_fraction
    cycle_duration = acceleration_fraction + steady_duration + deceleration_fraction
    steady_end = acceleration_fraction + steady_duration

    return RbdReferenceResult(
        algorithm_id=ALGORITHM_ID,
        algorithm_version=ALGORITHM_VERSION,
        numeric_policy=NUMERIC_POLICY,
        maximum_rpm=_exact_value(nominal_fraction),
        required_cycles_exact=_exact_value(required_cycles),
        steady_duration_s_exact=_exact_value(steady_duration),
        cycle_duration_s_exact=_exact_value(cycle_duration),
        total_duration_s_exact=_exact_value(cycle_duration),
        failure_result=_calculate_failure(values.failure, acceleration_fraction, nominal_fraction),
        phases=(
            RbdPhase(
                phase="acceleration",
                start_s=_exact_value(Fraction(0)),
                end_s=_exact_value(acceleration_fraction),
                start_rpm=_exact_value(Fraction(0)),
                end_rpm=_exact_value(nominal_fraction),
            ),
            RbdPhase(
                phase="steady_rotation",
                start_s=_exact_value(acceleration_fraction),
                end_s=_exact_value(steady_end),
                start_rpm=_exact_value(nominal_fraction),
                end_rpm=_exact_value(nominal_fraction),
            ),
            RbdPhase(
                phase="deceleration",
                start_s=_exact_value(steady_end),
                end_s=_exact_value(cycle_duration),
                start_rpm=_exact_value(nominal_fraction),
                end_rpm=_exact_value(Fraction(0)),
            ),
        ),
        diagram_points=_diagram_points(acceleration_fraction, steady_end, cycle_duration),
        formula_references=(
            "ПМИ Р130У, редакция 01, 2024, страница 12, формула 1",
            "ПМИ Р130У, редакция 01, 2024, страница 12, формула 2",
            "ПМИ Р130У, редакция 01, 2024, страница 12, формула 3",
            "ПМИ Р130У, редакция 01, 2024, таблица 3",
        ),
    )


def _diagram_points(
    acceleration_end: Fraction,
    steady_end: Fraction,
    cycle_end: Fraction,
) -> tuple[RbdDiagramPoint, RbdDiagramPoint, RbdDiagramPoint, RbdDiagramPoint]:
    # A legible schematic, not a proportional time series. Exact durations remain in phases.
    acceleration_x = max(150, min(350, round(acceleration_end * 1000 / cycle_end)))
    steady_x = max(650, min(850, round(steady_end * 1000 / cycle_end)))
    return (
        RbdDiagramPoint("start", 0, 100),
        RbdDiagramPoint("acceleration_end", acceleration_x, 0),
        RbdDiagramPoint("steady_end", steady_x, 0),
        RbdDiagramPoint("cycle_end", 1000, 100),
    )


def _calculate_failure(
    failure: object | None,
    acceleration: Fraction,
    nominal_rpm: Fraction,
) -> RbdFailureResult:
    if failure is None:
        return RbdFailureResult("not_applicable", None, "failure_duration_unavailable")
    if not isinstance(failure, RbdFailureInput):
        raise RbdCalculationError("invalid_input_type", "Ожидался типизированный набор входов отказа.")
    reason_by_applicability = {
        "unavailable": "failure_duration_unavailable",
        "interval_endpoint": "failure_endpoint_interval",
        "right_censored": "failure_not_observed",
        "unsupported_phase": "failure_phase_unsupported",
        "ambiguous_pauses": "failure_structure_ambiguous",
    }
    applicability = _validated_failure_applicability(failure.applicability)
    if applicability != "exact_supported":
        return RbdFailureResult(
            "not_applicable",
            None,
            reason_by_applicability[applicability],
        )
    if failure.duration_to_failure_s is None:
        raise RbdCalculationError(
            "failure_duration_required",
            "Для расчёта по таблице 3 требуется документированная продолжительность до отказа.",
        )
    duration = _nonnegative_decimal(
        failure.duration_to_failure_s,
        "duration_to_failure_s",
        Decimal("1000000000000"),
    )
    elapsed_after_acceleration = Fraction(duration) - acceleration
    if elapsed_after_acceleration < 0:
        raise RbdCalculationError(
            "failure_before_acceleration_complete",
            "Продолжительность до отказа меньше выбранного времени разгона.",
        )
    cycles = (elapsed_after_acceleration * nominal_rpm / 60).__floor__()
    return RbdFailureResult("calculated", str(cycles), None)


def _validated_failure_applicability(value: object) -> RbdFailureApplicability:
    if value == "exact_supported":
        return "exact_supported"
    if value == "unavailable":
        return "unavailable"
    if value == "interval_endpoint":
        return "interval_endpoint"
    if value == "right_censored":
        return "right_censored"
    if value == "unsupported_phase":
        return "unsupported_phase"
    if value == "ambiguous_pauses":
        return "ambiguous_pauses"
    raise RbdCalculationError(
        "invalid_failure_applicability",
        "Указана неподдерживаемая применимость расчёта по таблице 3.",
    )


def _positive_integer(value: object, field: str, maximum: int) -> int:
    if not isinstance(value, str):
        raise RbdCalculationError("invalid_input_type", f"{field}: ожидалась строка canonical integer.")
    if _CANONICAL_INTEGER.fullmatch(value) is None:
        raise RbdCalculationError("invalid_numeric_format", f"{field}: неверный canonical integer.")
    parsed = int(value)
    if parsed <= 0 or parsed > maximum:
        raise RbdCalculationError("numeric_out_of_range", f"{field}: значение вне допустимого диапазона.")
    return parsed


def _positive_decimal(value: object, field: str, maximum: Decimal) -> Decimal:
    parsed = _canonical_decimal(value, field)
    if parsed <= 0 or parsed > maximum:
        raise RbdCalculationError("numeric_out_of_range", f"{field}: значение вне допустимого диапазона.")
    return parsed


def _nonnegative_decimal(value: object, field: str, maximum: Decimal) -> Decimal:
    parsed = _canonical_decimal(value, field)
    if parsed < 0 or parsed > maximum:
        raise RbdCalculationError("numeric_out_of_range", f"{field}: значение вне допустимого диапазона.")
    return parsed


def _canonical_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise RbdCalculationError("invalid_input_type", f"{field}: ожидалась строка canonical decimal.")
    if len(value) > 31 or _BOUNDED_DECIMAL_INPUT.fullmatch(value) is None:
        raise RbdCalculationError("invalid_numeric_format", f"{field}: неверный canonical decimal.")
    return Decimal(value)


def _exact_value(value: Fraction) -> ExactRationalValue:
    decimal = _terminating_decimal(value)
    return ExactRationalValue(
        numerator=str(value.numerator),
        denominator=str(value.denominator),
        decimal=decimal,
        decimal_preview=decimal if decimal is not None else _periodic_preview(value),
    )


def _terminating_decimal(value: Fraction) -> str | None:
    denominator = value.denominator
    twos = 0
    fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        return None
    scale = max(twos, fives)
    scaled = value.numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    sign = "-" if scaled < 0 else ""
    digits = str(abs(scaled))
    if scale == 0:
        return f"{sign}{digits}"
    digits = digits.zfill(scale + 1)
    result = f"{sign}{digits[:-scale]}.{digits[-scale:]}".rstrip("0").rstrip(".")
    return "0" if result in {"-0", ""} else result


def _periodic_preview(value: Fraction) -> str:
    sign = "-" if value < 0 else ""
    numerator = abs(value.numerator)
    integer, remainder = divmod(numerator, value.denominator)
    digits: list[str] = []
    for _ in range(_DECIMAL_PREVIEW_DIGITS):
        remainder *= 10
        digit, remainder = divmod(remainder, value.denominator)
        digits.append(str(digit))
    return f"{sign}{integer}.{''.join(digits)}…"
