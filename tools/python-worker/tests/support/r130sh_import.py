from __future__ import annotations

from pathlib import Path
import shutil
from threading import Event
from time import monotonic
from uuid import uuid4

from impeller_reliability.application.project_service import ProjectService
from impeller_reliability.integration.r130run.m9a import M9aPackageFacts, read_m9a_package_facts
from impeller_reliability.integration.r130run.models import RunPackageValidationReport
from impeller_reliability.integration.r130run.validator import RunPackageValidator, ValidationControl
from impeller_reliability.persistence.r130sh_sources import ImportedRunSummary

M9A_ROOT = Path(__file__).resolve().parents[4] / "fixtures" / "contracts" / "r130run" / "v1" / "m9a"


def create_import_project(tmp_path: Path) -> tuple[ProjectService, Path]:
    service = ProjectService()
    project_path = (tmp_path / "acceptance.irproj").resolve()
    service.create(
        path=str(project_path),
        application_instance_id="tests",
        application_version="0.0.0-test",
        name="M9b acceptance",
        project_number="",
        description="",
        status="draft",
    )
    return service, project_path


def frozen_package(name: str) -> Path:
    return M9A_ROOT / "packages" / name


def validated_package(path: Path) -> tuple[RunPackageValidationReport, M9aPackageFacts]:
    report = RunPackageValidator().validate(
        path,
        ValidationControl(Event(), monotonic() + 30, _ignore_validation_progress),
    )
    return report, read_m9a_package_facts(path, report)


def _ignore_validation_progress(
    _phase: str,
    _completed_bytes: int,
    _total_bytes: int,
    _completed_entries: int,
    _total_entries: int,
) -> None:
    return None


def stage_package(project_path: Path, source: Path) -> Path:
    value = project_path / "imports" / "r130sh" / ".staging" / f"{uuid4()}.part"
    value.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, value)
    return value


def import_run_package(
    service: ProjectService,
    project_path: Path,
    source: Path,
) -> ImportedRunSummary:
    report, facts = validated_package(source)
    return service.register_imported_run(
        local_import_id=str(uuid4()),
        staged_path=stage_package(project_path, source),
        facts=facts,
        report=report,
        deadline=None,
    )
