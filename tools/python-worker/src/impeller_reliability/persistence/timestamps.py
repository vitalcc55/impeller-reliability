from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Final

CANONICAL_UTC_PATTERN: Final = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$")
CANONICAL_UTC_FORMAT: Final = "%Y-%m-%dT%H:%M:%S.%fZ"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def require_canonical_utc_timestamp(value: str) -> str:
    parse_canonical_utc_timestamp(value)
    return value


def parse_canonical_utc_timestamp(value: str) -> datetime:
    if CANONICAL_UTC_PATTERN.fullmatch(value) is None:
        raise ValueError("timestamp_is_not_canonical_utc")
    try:
        parsed = datetime.strptime(value, CANONICAL_UTC_FORMAT).replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError("timestamp_is_not_canonical_utc") from error
    canonical = parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if canonical != value:
        raise ValueError("timestamp_is_not_canonical_utc")
    return parsed
