from __future__ import annotations

import re
from typing import Final, Literal
from uuid import RFC_4122, UUID

APPLICATION_VERSION_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}", re.ASCII)
ProjectMetadataField = Literal["name", "projectNumber", "description", "status"]


def require_application_version(value: str) -> str:
    if APPLICATION_VERSION_PATTERN.fullmatch(value) is None:
        raise ValueError("application_version_invalid")
    return value


def require_canonical_project_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError("project_id_invalid") from error
    if str(parsed) != value or parsed.variant != RFC_4122 or parsed.version not in range(1, 9):
        raise ValueError("project_id_invalid")
    return value


def require_project_metadata_value(field: ProjectMetadataField, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("project_metadata_not_text")
    maximum = {"name": 200, "projectNumber": 100, "description": 4_000, "status": 9}[field]
    if len(value) > maximum or (field == "name" and value.strip() == ""):
        raise ValueError("project_metadata_invalid")
    if field == "status" and value not in {"draft", "active", "completed", "archived"}:
        raise ValueError("project_metadata_invalid")
    return value
