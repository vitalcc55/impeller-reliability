from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError, field_validator

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_values import (
    require_application_version,
    require_canonical_project_id,
)
from impeller_reliability.persistence.timestamps import require_canonical_utc_timestamp

PROJECT_CONTAINER_SCHEMA: Final = "impeller.project-container.v1"
PROJECT_DATABASE_FILE: Final = "project.sqlite"
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


def read_manifest(path: Path) -> ProjectManifest:
    try:
        raw = path.read_text(encoding="utf-8")
        return ProjectManifest.model_validate_json(raw)
    except (OSError, UnicodeError, ValidationError, ValueError) as error:
        raise ProjectOperationError("corrupt_project", "Manifest проекта повреждён или несовместим.") from error


def write_manifest(path: Path, manifest: ProjectManifest) -> None:
    encoded = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    path.write_text(encoded, encoding="utf-8", newline="\n")
