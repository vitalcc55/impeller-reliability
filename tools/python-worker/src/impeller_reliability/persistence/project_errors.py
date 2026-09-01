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
    "operation_in_progress",
    "job_id_conflict",
    "duplicate_entity",
    "duplicate_document_content",
    "file_already_attached",
    "unsupported_file_type",
    "file_too_large",
    "file_missing",
    "file_integrity_mismatch",
    "import_integrity_conflict",
    "resolution_conflict",
    "validation_error",
    "storage_error",
    "worker_unavailable",
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
