from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from typing import override
from uuid import uuid4

import pytest

from impeller_reliability.integration.r130run.jobs import RunPackageValidationJobManager
from impeller_reliability.integration.r130run.models import (
    RunPackageValidationJobSnapshot,
    RunPackageValidationReport,
)
from impeller_reliability.integration.r130run.validator import (
    RunPackageValidator,
    SourceFingerprint,
    ValidationControl,
    ValidationTimeoutError,
)
from impeller_reliability.persistence.project_errors import ProjectOperationError
from support.r130run_builder import build_synthetic_r130run


def test_start_returns_immediately_and_job_completes_in_background(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "valid.r130run")
    manager = RunPackageValidationJobManager()
    job_id = str(uuid4())

    started = monotonic()
    initial = manager.start(job_id, package, 30_000)

    assert monotonic() - started < 1
    assert initial.state in {"queued", "running", "completed"}
    completed = _wait_for_terminal(manager, job_id)
    assert completed.state == "completed"
    assert completed.report is not None
    assert completed.report.structuralVerdict == "passed"


def test_same_job_retry_is_idempotent_and_conflicting_input_is_rejected(tmp_path: Path) -> None:
    first = build_synthetic_r130run(tmp_path / "first.r130run")
    second = build_synthetic_r130run(tmp_path / "second.r130run")
    gate = _GateValidator()
    manager = RunPackageValidationJobManager(lambda: gate)
    job_id = str(uuid4())

    manager.start(job_id, first, 30_000)
    assert gate.started.wait(1)
    repeated = manager.start(job_id, first, 30_000)
    assert repeated.jobId == job_id

    with pytest.raises(ProjectOperationError, match="Идентификатор проверки") as path_conflict:
        manager.start(job_id, second, 30_000)
    assert path_conflict.value.code == "job_id_conflict"
    with pytest.raises(ProjectOperationError) as budget_conflict:
        manager.start(job_id, first, 31_000)
    assert budget_conflict.value.code == "job_id_conflict"
    with pytest.raises(ProjectOperationError) as second_job:
        manager.start(str(uuid4()), second, 30_000)
    assert second_job.value.code == "operation_in_progress"

    gate.release.set()
    _wait_for_terminal(manager, job_id)


@pytest.mark.parametrize("budget_ms", [0, 999, 1_800_001])
def test_validation_budget_is_bounded(tmp_path: Path, budget_ms: int) -> None:
    package = build_synthetic_r130run(tmp_path / "valid.r130run")
    manager = RunPackageValidationJobManager()

    with pytest.raises(ProjectOperationError) as failure:
        manager.start(str(uuid4()), package, budget_ms)

    assert failure.value.code == "validation_error"


def test_cancel_is_cooperative_terminal_and_discard_releases_slot(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "valid.r130run")
    gate = _GateValidator()
    manager = RunPackageValidationJobManager(lambda: gate)
    job_id = str(uuid4())
    manager.start(job_id, package, 30_000)
    assert gate.started.wait(1)

    cancelling = manager.cancel(job_id)
    assert cancelling.state == "cancelling"
    cancelled = _wait_for_terminal(manager, job_id)
    assert cancelled.state == "cancelled"
    assert cancelled.typedError is not None
    assert cancelled.typedError.code == "cancelled"
    assert manager.cancel(job_id).state == "cancelled"
    assert manager.discard(job_id).discarded is True

    with pytest.raises(ProjectOperationError) as missing:
        manager.get(job_id)
    assert missing.value.code == "entity_not_found"


def test_discard_rejects_active_job(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "valid.r130run")
    gate = _GateValidator()
    manager = RunPackageValidationJobManager(lambda: gate)
    job_id = str(uuid4())
    manager.start(job_id, package, 30_000)
    assert gate.started.wait(1)

    with pytest.raises(ProjectOperationError) as failure:
        manager.discard(job_id)

    assert failure.value.code == "operation_in_progress"
    gate.release.set()
    _wait_for_terminal(manager, job_id)


def test_terminal_job_is_replaced_atomically_but_active_job_is_preserved(tmp_path: Path) -> None:
    first = build_synthetic_r130run(tmp_path / "first.r130run")
    second = build_synthetic_r130run(tmp_path / "second.r130run")
    manager = RunPackageValidationJobManager()
    first_id = str(uuid4())
    second_id = str(uuid4())
    manager.start(first_id, first, 30_000)
    _wait_for_terminal(manager, first_id)

    replacement = manager.start(second_id, second, 30_000, replace_job_id=first_id)
    assert replacement.jobId == second_id
    _wait_for_terminal(manager, second_id)

    gate = _GateValidator()
    active_manager = RunPackageValidationJobManager(lambda: gate)
    active_id = str(uuid4())
    active_manager.start(active_id, first, 30_000)
    assert gate.started.wait(1)
    with pytest.raises(ProjectOperationError) as active:
        active_manager.start(str(uuid4()), second, 30_000, replace_job_id=active_id)
    assert active.value.code == "operation_in_progress"
    assert active_manager.get(active_id).jobId == active_id
    gate.release.set()
    _wait_for_terminal(active_manager, active_id)


def test_shutdown_requests_cancel_and_joins_within_bound(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "valid.r130run")
    gate = _IgnoringCancelValidator()
    manager = RunPackageValidationJobManager(lambda: gate)
    job_id = str(uuid4())
    manager.start(job_id, package, 30_000)
    assert gate.started.wait(1)

    started = monotonic()
    joined = manager.shutdown(join_seconds=0.01)

    assert joined is False
    assert monotonic() - started < 0.5
    gate.release.set()
    _wait_for_terminal(manager, job_id)


def test_source_replaced_after_start_is_reported_as_changed(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "source.r130run")
    replacement = build_synthetic_r130run(tmp_path / "replacement.r130run")
    validator = _ReplaceBeforeValidationValidator(package, replacement)
    manager = RunPackageValidationJobManager(lambda: validator)
    job_id = str(uuid4())

    manager.start(job_id, package, 30_000)
    result = _wait_for_terminal(manager, job_id)

    assert result.state == "failed"
    assert result.typedError is not None
    assert result.typedError.code == "source_changed"


def test_start_rejects_unavailable_source_and_stopping_manager(tmp_path: Path) -> None:
    manager = RunPackageValidationJobManager()
    with pytest.raises(ProjectOperationError) as missing:
        manager.start(str(uuid4()), tmp_path / "missing.r130run", 30_000)
    assert missing.value.code == "storage_error"

    assert manager.shutdown(join_seconds=0) is True
    package = build_synthetic_r130run(tmp_path / "valid.r130run")
    with pytest.raises(ProjectOperationError) as stopping:
        manager.start(str(uuid4()), package, 30_000)
    assert stopping.value.code == "worker_unavailable"


@pytest.mark.parametrize(
    ("failure_kind", "expected_code"),
    [
        ("timeout", "timeout"),
        ("storage", "storage_error"),
        ("unexpected", "validation_error"),
    ],
)
def test_background_failures_are_sanitized_and_typed(
    tmp_path: Path,
    failure_kind: str,
    expected_code: str,
) -> None:
    error: Exception
    if failure_kind == "timeout":
        error = ValidationTimeoutError()
    elif failure_kind == "storage":
        error = OSError("read failed")
    else:
        error = ValueError("unexpected")
    validator = _FailingValidator(error)
    package = build_synthetic_r130run(tmp_path / "valid.r130run")
    manager = RunPackageValidationJobManager(lambda: validator)
    job_id = str(uuid4())

    manager.start(job_id, package, 30_000)
    result = _wait_for_terminal(manager, job_id)

    assert result.state == "failed"
    assert result.typedError is not None
    assert result.typedError.code == expected_code


class _GateValidator(RunPackageValidator):
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    @override
    def validate(
        self,
        source_path: Path,
        control: ValidationControl,
        expected_fingerprint: SourceFingerprint | None = None,
    ) -> RunPackageValidationReport:
        self.started.set()
        while not self.release.wait(0.01):
            control.check("payload_integrity")
        return super().validate(source_path, control, expected_fingerprint)


class _IgnoringCancelValidator(RunPackageValidator):
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    @override
    def validate(
        self,
        source_path: Path,
        control: ValidationControl,
        expected_fingerprint: SourceFingerprint | None = None,
    ) -> RunPackageValidationReport:
        self.started.set()
        self.release.wait(2)
        return super().validate(source_path, control, expected_fingerprint)


class _ReplaceBeforeValidationValidator(RunPackageValidator):
    def __init__(self, target: Path, replacement: Path) -> None:
        self._target = target
        self._replacement = replacement

    @override
    def validate(
        self,
        source_path: Path,
        control: ValidationControl,
        expected_fingerprint: SourceFingerprint | None = None,
    ) -> RunPackageValidationReport:
        os.replace(self._replacement, self._target)
        return super().validate(source_path, control, expected_fingerprint)


class _FailingValidator(RunPackageValidator):
    def __init__(self, error: Exception) -> None:
        self._error = error

    @override
    def validate(
        self,
        source_path: Path,
        control: ValidationControl,
        expected_fingerprint: SourceFingerprint | None = None,
    ) -> RunPackageValidationReport:
        raise self._error


def _wait_for_terminal(
    manager: RunPackageValidationJobManager,
    job_id: str,
    timeout_seconds: float = 3,
) -> RunPackageValidationJobSnapshot:
    expires = monotonic() + timeout_seconds
    while monotonic() < expires:
        snapshot = manager.get(job_id)
        if snapshot.state in {"completed", "failed", "cancelled"}:
            return snapshot
        sleep(0.01)
    raise AssertionError("validation_job_did_not_finish")
