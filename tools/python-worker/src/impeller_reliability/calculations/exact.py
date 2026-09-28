from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
import re

_BOUNDED_DECIMAL_INPUT = re.compile(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?")
_CANONICAL_INTEGER = re.compile(r"(?:0|[1-9][0-9]{0,12})")
_DECIMAL_PREVIEW_DIGITS = 12


class ExactInputError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class ExactRationalValue:
    numerator: str
    denominator: str
    decimal: str | None
    decimal_preview: str


def positive_integer(value: object, field: str, maximum: int) -> int:
    if not isinstance(value, str):
        raise ExactInputError("invalid_input_type", f"{field}: ожидалась строка canonical integer.")
    if _CANONICAL_INTEGER.fullmatch(value) is None:
        raise ExactInputError("invalid_numeric_format", f"{field}: неверный canonical integer.")
    parsed = int(value)
    if parsed <= 0 or parsed > maximum:
        raise ExactInputError("numeric_out_of_range", f"{field}: значение вне допустимого диапазона.")
    return parsed


def positive_decimal(value: object, field: str, maximum: Decimal) -> Decimal:
    parsed = canonical_decimal(value, field)
    if parsed <= 0 or parsed > maximum:
        raise ExactInputError("numeric_out_of_range", f"{field}: значение вне допустимого диапазона.")
    return parsed


def nonnegative_decimal(value: object, field: str, maximum: Decimal) -> Decimal:
    parsed = canonical_decimal(value, field)
    if parsed < 0 or parsed > maximum:
        raise ExactInputError("numeric_out_of_range", f"{field}: значение вне допустимого диапазона.")
    return parsed


def canonical_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise ExactInputError("invalid_input_type", f"{field}: ожидалась строка canonical decimal.")
    if len(value) > 31 or _BOUNDED_DECIMAL_INPUT.fullmatch(value) is None:
        raise ExactInputError("invalid_numeric_format", f"{field}: неверный canonical decimal.")
    return Decimal(value)


def exact_value(value: Fraction) -> ExactRationalValue:
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
