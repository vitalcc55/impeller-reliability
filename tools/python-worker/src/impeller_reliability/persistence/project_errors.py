from __future__ import annotations

from typing import Literal

ProjectErrorCode = Literal[
    "timeout",
    "project_locked",
    "corrupt_project",
    "incompatible_schema",
    "revision_conflict",
    "entity_not_found",
    "entity_archived",
    "entity_in_use",
    "duplicate_entity",
    "validation_error",
    "storage_error",
]


class ProjectOperationError(Exception):
    def __init__(
        self,
        code: ProjectErrorCode,
        message: str,
        *,
        details: dict[str, object] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code: ProjectErrorCode = code
        self.message: str = message
        self.details: dict[str, object] = details or {}
        self.retryable: bool = retryable
