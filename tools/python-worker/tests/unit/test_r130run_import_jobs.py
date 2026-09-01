from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
from time import monotonic, sleep
from uuid import uuid4

import pytest

from impeller_reliability.application.project_service import ProjectService
from impeller_reliability.integration.r130run.import_jobs import (
    FinalizeCallback,
    RunPackageImportJobManager,
)
from impeller_reliability.integration.r130run.import_models import RunPackageImportJobSnapshot
from impeller_reliability.integration.r130run.m9a import M9aPackageFacts
from impeller_reliability.integration.r130run.models import RunPackageValidationReport
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.r130sh_sources import ImportedRunSummary
from impeller_reliability.worker.deadline import RequestDeadline

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
M9A_PACKAGES = REPOSITORY_ROOT / "fixtures" / "contracts" / "r130run" / "v1" / "m9a" / "packages"


def _audit_count(project_path: Path) -> int:
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        return int(connection.execute("SELECT count(*) FROM project_audit_events").fetchone()[0])


def test_import_job_completes_and_lost_response_retry_is_idempotent(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    manager = RunPackageImportJobManager()
    job_id = str(uuid4())
    source = M9A_PACKAGES / "normal_final_pmn.r130run"

    started = manager.start(
        job_id=job_id,
        project_path=project_path,
        source_path=source,
        allow_diagnostic_partial=False,
    )
    repeated_start = manager.start(
        job_id=job_id,
        project_path=project_path,
        source_path=source,
        allow_diagnostic_partial=False,
    )
    assert repeated_start.jobId == started.jobId

    completed = _wait(manager, job_id, service)
    repeated_get = manager.get(job_id, finalize=_finalizer(service), deadline=None)

    assert completed.state == "completed"
    assert completed.result is not None
    assert completed.result.disposition == "created"
    assert repeated_get == completed
    assert len(service.list_imported_runs()) == 1
    assert manager.cancel(job_id) == completed
    with pytest.raises(ProjectOperationError) as terminal_not_discarded:
        manager.start(
            job_id=str(uuid4()),
            project_path=project_path,
            source_path=source,
            allow_diagnostic_partial=False,
        )
    assert terminal_not_discarded.value.code == "operation_in_progress"
    manager.discard(job_id)
    manager.shutdown()
    service.close()


def test_exact_second_import_returns_existing_without_second_source(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    source = M9A_PACKAGES / "duplicate_import_key.r130run"
    first_manager = RunPackageImportJobManager()
    first = _run(first_manager, str(uuid4()), project_path, source, service)
    first_manager.discard(first.jobId)
    second_manager = RunPackageImportJobManager()
    second = _run(second_manager, str(uuid4()), project_path, source, service)

    assert first.result is not None
    assert second.result is not None
    assert first.result.importedRun.localImportId == second.result.importedRun.localImportId
    assert second.result.disposition == "existing"
    assert len(service.list_imported_runs()) == 1
    service.close()


def test_diagnostic_partial_requires_explicit_confirmation(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    source = M9A_PACKAGES / "diagnostic_partial.r130run"
    audit_before = _audit_count(project_path)
    rejected = _run(
        RunPackageImportJobManager(),
        str(uuid4()),
        project_path,
        source,
        service,
        allow_diagnostic_partial=False,
    )
    assert rejected.state == "failed"
    assert rejected.typedError is not None
    assert rejected.typedError.code == "diagnostic_confirmation_required"
    assert service.list_imported_runs() == ()
    assert _audit_count(project_path) == audit_before
    staging_root = project_path / "imports" / "r130sh" / ".staging"
    assert not staging_root.exists() or tuple(staging_root.iterdir()) == ()

    accepted = _run(
        RunPackageImportJobManager(),
        str(uuid4()),
        project_path,
        source,
        service,
        allow_diagnostic_partial=True,
    )

    assert accepted.state == "completed"
    assert accepted.result is not None
    assert accepted.result.disposition == "created"
    assert accepted.result.importedRun.packageKind == "diagnostic_partial"
    assert len(service.list_imported_runs()) == 1
    service.close()


def test_cancel_before_registration_leaves_no_source_or_staging(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    manager = RunPackageImportJobManager()
    job_id = str(uuid4())
    manager.start(
        job_id=job_id,
        project_path=project_path,
        source_path=M9A_PACKAGES / "normal_final_rbd.r130run",
        allow_diagnostic_partial=False,
    )

    cancelled = manager.cancel(job_id)
    expires = monotonic() + 3
    while cancelled.state not in {"cancelled", "failed", "completed"} and monotonic() < expires:
        sleep(0.01)
        cancelled = manager.cancel(job_id)

    assert cancelled.state == "cancelled"
    assert service.list_imported_runs() == ()
    staging = project_path / "imports" / "r130sh" / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []
    service.close()


def test_import_rejects_reparse_managed_root_without_external_write(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    imports_root = project_path / "imports"
    imports_root.mkdir()
    managed_root = imports_root / "r130sh"
    external = tmp_path / "external-r130sh"
    external.mkdir()
    marker = external / "marker.txt"
    marker.write_text("untouched", encoding="utf-8")
    subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(managed_root), str(external)],
        check=True,
        capture_output=True,
        text=True,
    )
    manager = RunPackageImportJobManager()
    try:
        result = _run(
            manager,
            str(uuid4()),
            project_path,
            M9A_PACKAGES / "normal_final_pmn.r130run",
            service,
        )
        assert result.state == "failed"
        assert result.typedError is not None
        assert result.typedError.code == "storage_error"
        assert marker.read_text(encoding="utf-8") == "untouched"
        assert list(external.iterdir()) == [marker]
    finally:
        if managed_root.is_junction():
            managed_root.rmdir()
        service.close()


def test_job_manager_rejects_parallel_conflicts_and_atomically_replaces_terminal_job(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    manager = RunPackageImportJobManager()
    job_id = str(uuid4())
    source = M9A_PACKAGES / "normal_final_rbd.r130run"
    manager.start(
        job_id=job_id,
        project_path=project_path,
        source_path=source,
        allow_diagnostic_partial=False,
    )

    assert manager.has_active_job is True
    with pytest.raises(ProjectOperationError) as active_discard:
        manager.discard(job_id)
    assert active_discard.value.code == "operation_in_progress"
    with pytest.raises(ProjectOperationError) as parallel:
        manager.start(
            job_id=str(uuid4()),
            project_path=project_path,
            source_path=source,
            allow_diagnostic_partial=False,
        )
    assert parallel.value.code == "operation_in_progress"
    with pytest.raises(ProjectOperationError) as changed_request:
        manager.start(
            job_id=job_id,
            project_path=project_path,
            source_path=source,
            allow_diagnostic_partial=True,
        )
    assert changed_request.value.code == "job_id_conflict"

    manager.shutdown()
    snapshot = manager.get(job_id, finalize=_finalizer(service), deadline=None)
    assert snapshot.state == "cancelled"
    replacement_job_id = str(uuid4())
    with pytest.raises(ProjectOperationError) as unguarded_replacement:
        manager.start(
            job_id=replacement_job_id,
            project_path=project_path,
            source_path=source,
            allow_diagnostic_partial=False,
        )
    assert unguarded_replacement.value.code == "operation_in_progress"
    replacement = manager.start(
        job_id=replacement_job_id,
        replace_job_id=job_id,
        project_path=project_path,
        source_path=source,
        allow_diagnostic_partial=False,
    )
    assert replacement.jobId == replacement_job_id
    manager.shutdown()
    replacement = manager.get(replacement_job_id, finalize=_finalizer(service), deadline=None)
    assert replacement.state == "cancelled"
    assert manager.discard(replacement_job_id).jobId == replacement_job_id
    service.close()


def test_job_reports_validation_and_registration_failures_without_committing(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    invalid = tmp_path / "invalid.r130run"
    invalid.write_bytes(b"not a zip")
    invalid_manager = RunPackageImportJobManager()
    invalid_result = _run(invalid_manager, str(uuid4()), project_path, invalid, service)
    assert invalid_result.state == "failed"
    assert invalid_result.typedError is not None
    assert invalid_result.typedError.code == "validation_error"
    invalid_manager.discard(invalid_result.jobId)

    manager = RunPackageImportJobManager()
    job_id = str(uuid4())
    manager.start(
        job_id=job_id,
        project_path=project_path,
        source_path=M9A_PACKAGES / "normal_final_pmn.r130run",
        allow_diagnostic_partial=False,
    )

    def fail_registration(
        _local_import_id: str,
        _staged_path: Path,
        _facts: M9aPackageFacts,
        _report: RunPackageValidationReport,
        _deadline: RequestDeadline | None,
    ) -> ImportedRunSummary:
        raise ProjectOperationError(
            "import_integrity_conflict",
            "Tuple уже связан с другим SHA.",
        )

    expires = monotonic() + 5
    failed = manager.get(job_id, finalize=fail_registration, deadline=None)
    while failed.state not in {"completed", "failed", "cancelled"} and monotonic() < expires:
        sleep(0.01)
        failed = manager.get(job_id, finalize=fail_registration, deadline=None)
    assert failed.state == "failed"
    assert failed.typedError is not None
    assert failed.typedError.code == "import_integrity_conflict"
    assert service.list_imported_runs() == ()
    manager.discard(job_id)

    timeout_manager = RunPackageImportJobManager()
    timeout_job_id = str(uuid4())
    timeout_manager.start(
        job_id=timeout_job_id,
        project_path=project_path,
        source_path=M9A_PACKAGES / "normal_final_rpt_full_stop.r130run",
        allow_diagnostic_partial=False,
    )

    def fail_with_timeout(
        _local_import_id: str,
        _staged_path: Path,
        _facts: M9aPackageFacts,
        _report: RunPackageValidationReport,
        _deadline: RequestDeadline | None,
    ) -> ImportedRunSummary:
        raise ProjectOperationError("timeout", "Injected bounded deadline.", retryable=True)

    expires = monotonic() + 5
    timed_out = timeout_manager.get(timeout_job_id, finalize=fail_with_timeout, deadline=None)
    while timed_out.state not in {"completed", "failed", "cancelled"} and monotonic() < expires:
        sleep(0.01)
        timed_out = timeout_manager.get(
            timeout_job_id,
            finalize=fail_with_timeout,
            deadline=None,
        )
    assert timed_out.state == "failed"
    assert timed_out.typedError is not None
    assert timed_out.typedError.code == "timeout"
    assert timed_out.typedError.retryable is True
    timeout_manager.discard(timeout_job_id)

    generic_manager = RunPackageImportJobManager()
    generic_job_id = str(uuid4())
    generic_manager.start(
        job_id=generic_job_id,
        project_path=project_path,
        source_path=M9A_PACKAGES / "normal_final_rpt_one_percent.r130run",
        allow_diagnostic_partial=False,
    )

    def fail_unexpectedly(
        _local_import_id: str,
        _staged_path: Path,
        _facts: M9aPackageFacts,
        _report: RunPackageValidationReport,
        _deadline: RequestDeadline | None,
    ) -> ImportedRunSummary:
        raise RuntimeError("injected registration failure")

    expires = monotonic() + 5
    generic_failed = generic_manager.get(generic_job_id, finalize=fail_unexpectedly, deadline=None)
    while generic_failed.state not in {"completed", "failed", "cancelled"} and monotonic() < expires:
        sleep(0.01)
        generic_failed = generic_manager.get(generic_job_id, finalize=fail_unexpectedly, deadline=None)
    assert generic_failed.state == "failed"
    assert generic_failed.typedError is not None
    assert generic_failed.typedError.code == "storage_error"
    assert generic_failed.typedError.retryable is True
    generic_manager.discard(generic_job_id)
    service.close()


def _project(tmp_path: Path) -> tuple[ProjectService, Path]:
    service = ProjectService()
    project_path = (tmp_path / "job.irproj").resolve()
    service.create(
        path=str(project_path),
        application_instance_id="job-tests",
        application_version="0.0.0-test",
        name="Import job",
        project_number="",
        description="",
        status="draft",
    )
    return service, project_path


def _finalizer(service: ProjectService) -> FinalizeCallback:
    def finalize(
        local_import_id: str,
        staged_path: Path,
        facts: M9aPackageFacts,
        report: RunPackageValidationReport,
        deadline: RequestDeadline | None,
    ) -> ImportedRunSummary:
        return service.register_imported_run(
            local_import_id=local_import_id,
            staged_path=staged_path,
            facts=facts,
            report=report,
            deadline=deadline,
        )

    return finalize


def _wait(
    manager: RunPackageImportJobManager,
    job_id: str,
    service: ProjectService,
) -> RunPackageImportJobSnapshot:
    expires = monotonic() + 5
    snapshot = manager.get(job_id, finalize=_finalizer(service), deadline=None)
    while snapshot.state not in {"completed", "failed", "cancelled"} and monotonic() < expires:
        sleep(0.01)
        snapshot = manager.get(job_id, finalize=_finalizer(service), deadline=None)
    return snapshot


def _run(
    manager: RunPackageImportJobManager,
    job_id: str,
    project_path: Path,
    source: Path,
    service: ProjectService,
    *,
    allow_diagnostic_partial: bool = False,
) -> RunPackageImportJobSnapshot:
    manager.start(
        job_id=job_id,
        project_path=project_path,
        source_path=source,
        allow_diagnostic_partial=allow_diagnostic_partial,
    )
    return _wait(manager, job_id, service)
