from __future__ import annotations

import json
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.timestamps import require_canonical_utc_timestamp

PROJECT_CONTAINER_SCHEMA: Final = "impeller.project-container.v1"
PROJECT_DATABASE_FILE: Final = "project.sqlite"


class ProjectManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schemaVersion: Literal["impeller.project-container.v1"] = PROJECT_CONTAINER_SCHEMA
    projectId: str
    createdAtUtc: str
    createdWithApplicationVersion: str
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
