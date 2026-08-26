from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError, field_validator

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_paths import inspect_reserved_file
from impeller_reliability.persistence.project_values import (
    require_application_version,
    require_canonical_project_id,
)
from impeller_reliability.persistence.timestamps import require_canonical_utc_timestamp
from impeller_reliability.worker.deadline import RequestDeadline

PROJECT_CONTAINER_SCHEMA: Final = "impeller.project-container.v1"
PROJECT_DATABASE_FILE: Final = "project.sqlite"
MAX_PROJECT_MANIFEST_BYTES: Final = 4_096
ApplicationVersion = Annotated[str, AfterValidator(require_application_version)]
ProjectId = Annotated[str, AfterValidator(require_canonical_project_id)]


class ProjectManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schemaVersion: Literal["impeller.project-container.v1"] = PROJECT_CONTAINER_SCHEMA
    projectId: ProjectId
    createdAtUtc: str
    createdWithApplicationVersion: ApplicationVersion
    databaseFile: Literal["project.sqlite"] = PROJECT_DATABASE_FILE

    @field_validator("createdAtUtc")
    @classmethod
    def validate_created_at_utc(cls, value: str) -> str:
        return require_canonical_utc_timestamp(value)


def read_manifest(path: Path, deadline: RequestDeadline | None = None) -> ProjectManifest:
    try:
        _check_deadline(deadline, "project_manifest_preflight")
        inspect_reserved_file(path, path.name)
        if path.stat().st_size > MAX_PROJECT_MANIFEST_BYTES:
            raise ProjectOperationError("corrupt_project", "Manifest проекта повреждён или несовместим.")
        descriptor = os.open(path, os.O_RDONLY | os.O_BINARY)
        try:
            chunks: list[bytes] = []
            remaining = MAX_PROJECT_MANIFEST_BYTES + 1
            while remaining > 0:
                _check_deadline(deadline, "project_manifest_read")
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            encoded = b"".join(chunks)
            if len(encoded) > MAX_PROJECT_MANIFEST_BYTES:
                raise ProjectOperationError("corrupt_project", "Manifest проекта повреждён или несовместим.")
        finally:
            os.close(descriptor)
        _check_deadline(deadline, "project_manifest_decode")
        raw = encoded.decode("utf-8", errors="strict")
        manifest = ProjectManifest.model_validate_json(raw)
        _check_deadline(deadline, "project_manifest_validate")
        return manifest
    except (OSError, UnicodeError, ValidationError, ValueError) as error:
        raise ProjectOperationError("corrupt_project", "Manifest проекта повреждён или несовместим.") from error


def write_manifest(path: Path, manifest: ProjectManifest) -> None:
    encoded = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    path.write_text(encoded, encoding="utf-8", newline="\n")


def _check_deadline(deadline: RequestDeadline | None, stage: str) -> None:
    if deadline is not None:
        deadline.check(stage)
