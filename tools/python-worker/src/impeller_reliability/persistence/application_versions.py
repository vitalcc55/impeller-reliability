from __future__ import annotations

import re
from typing import Final

APPLICATION_VERSION_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}", re.ASCII)


def require_application_version(value: str) -> str:
    if APPLICATION_VERSION_PATTERN.fullmatch(value) is None:
        raise ValueError("application_version_invalid")
    return value
