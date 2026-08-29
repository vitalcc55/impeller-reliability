from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from typing import Literal

from impeller_reliability.integration.r130run.models import (
    JobErrorCode,
    JobPhase,
    JobState,
    RunPackageValidationDiscardResult,
    RunPackageValidationJobError,
    RunPackageValidationJobSnapshot,
    RunPackageValidationProgress,
    RunPackageValidationReport,
)
from impeller_reliability.integration.r130run.validator import (
    RunPackageValidator,
    SourceChangedError,
    SourceFingerprint,
    ValidationCancelledError,
    ValidationControl,
    ValidationTimeoutError,
    inspect_source,
    utc_now,
)
from impeller_reliability.persistence.project_errors import ProjectOperationError

MAX_VALIDATION_BUDGET_MS = 1_800_000
MIN_VALIDATION_BUDGET_MS = 1_000
SHUTDOWN_JOIN_SECONDS = 1.5
TerminalState = Literal["completed", "failed", "cancelled"]
ValidatorFactory = Callable[[], RunPackageValidator]


@dataclass(slots=True)
class _JobRecord:
    job_id: str
    source_path: Path
    fingerprint: SourceFingerprint
    budget_ms: int
    state: JobState
    phase: JobPhase
    progress: RunPackageValidationProgress
    cancel_event: Event
    started_at_utc: str | None = None
    finished_at_utc: str | None = None
    report: RunPackageValidationReport | None = None
    typed_error: RunPackageValidationJobError | None = None
    thread: Thread | None = None


class RunPackageValidationJobManager:
    def __init__(self, validator_factory: ValidatorFactory = RunPackageValidator) -> None:
        self._validator_factory = validator_factory
        self._lock = Lock()
        self._job: _JobRecord | None = None
        self._stopping = False

    def start(self, job_id: str, source_path: Path, validation_budget_ms: int) -> RunPackageValidationJobSnapshot:
        if validation_budget_ms < MIN_VALIDATION_BUDGET_MS or validation_budget_ms > MAX_VALIDATION_BUDGET_MS:
            raise ProjectOperationError("validation_error", "Срок проверки пакета находится вне допустимого диапазона.")
        try:
            fingerprint = inspect_source(source_path)
        except OSError as error:
            raise ProjectOperationError("storage_error", "Выбранный пакет недоступен для чтения.", retryable=True) from error
        with self._lock:
            if self._stopping:
                raise ProjectOperationError("worker_unavailable", "Worker завершает работу.", retryable=True)
            if self._job is not None:
                if self._job.job_id != job_id:
                    raise ProjectOperationError("operation_in_progress", "Другая проверка пакета ещё не освобождена.", retryable=True)
                if self._job.fingerprint != fingerprint or self._job.budget_ms != validation_budget_ms:
                    raise ProjectOperationError("job_id_conflict", "Идентификатор проверки уже связан с другим источником.")
                return self._snapshot(self._job)
            record = _JobRecord(
                job_id=job_id,
                source_path=source_path,
                fingerprint=fingerprint,
                budget_ms=validation_budget_ms,
                state="queued",
                phase="source_check",
                progress=RunPackageValidationProgress(
                    kind="unknown",
                    completedBytes=0,
                    totalBytes=fingerprint.signature.size,
                    completedEntries=0,
                    totalEntries=0,
                ),
                cancel_event=Event(),
            )
            thread = Thread(target=self._run, args=(record,), name=f"r130run-validation-{job_id}", daemon=True)
            record.thread = thread
            self._job = record
            thread.start()
            return self._snapshot(record)

    def get(self, job_id: str) -> RunPackageValidationJobSnapshot:
        with self._lock:
            return self._snapshot(self._require_job(job_id))

    def cancel(self, job_id: str) -> RunPackageValidationJobSnapshot:
        with self._lock:
            record = self._require_job(job_id)
            if record.state not in {"completed", "failed", "cancelled"}:
                record.state = "cancelling"
                record.cancel_event.set()
            return self._snapshot(record)

    def discard(self, job_id: str) -> RunPackageValidationDiscardResult:
        with self._lock:
            record = self._require_job(job_id)
            if record.state not in {"completed", "failed", "cancelled"} or (record.thread is not None and record.thread.is_alive()):
                raise ProjectOperationError("operation_in_progress", "Активную проверку нужно сначала отменить.", retryable=True)
            self._job = None
            return RunPackageValidationDiscardResult(jobId=job_id)

    def shutdown(self, join_seconds: float = SHUTDOWN_JOIN_SECONDS) -> bool:
        with self._lock:
            self._stopping = True
            record = self._job
            thread = None if record is None else record.thread
            if record is not None and record.state not in {"completed", "failed", "cancelled"}:
                record.state = "cancelling"
                record.cancel_event.set()
        if thread is not None:
            thread.join(max(0.0, join_seconds))
            return not thread.is_alive()
        return True

    def _run(self, record: _JobRecord) -> None:
        with self._lock:
            if record.cancel_event.is_set():
                self._publish_error(record, "cancelled", "Проверка отменена.", False, "cancelled")
                return
            record.state = "running"
            record.started_at_utc = utc_now()
        control = ValidationControl(
            cancel_event=record.cancel_event,
            expires_at=monotonic() + record.budget_ms / 1000,
            progress=lambda phase, completed_bytes, total_bytes, completed_entries, total_entries: self._progress(
                record,
                phase,
                completed_bytes,
                total_bytes,
                completed_entries,
                total_entries,
            ),
        )
        try:
            report = self._validator_factory().validate(
                record.source_path,
                control,
                expected_fingerprint=record.fingerprint,
            )
        except ValidationCancelledError:
            self._terminal_error(record, "cancelled", "Проверка отменена.", False, "cancelled")
        except ValidationTimeoutError:
            self._terminal_error(record, "timeout", "Проверка не завершена в установленный срок.", True, "failed")
        except SourceChangedError:
            self._terminal_error(record, "source_changed", "Исходный пакет изменился во время проверки.", True, "failed")
        except OSError:
            self._terminal_error(record, "storage_error", "Исходный пакет не удалось прочитать.", True, "failed")
        except Exception:
            self._terminal_error(record, "validation_error", "Проверка пакета завершилась внутренней ошибкой.", False, "failed")
        else:
            with self._lock:
                if record.cancel_event.is_set():
                    self._publish_error(record, "cancelled", "Проверка отменена.", False, "cancelled")
                else:
                    record.state = "completed"
                    record.phase = "finalizing"
                    record.report = report
                    record.typed_error = None
                    record.finished_at_utc = utc_now()

    def _progress(
        self,
        record: _JobRecord,
        phase: JobPhase,
        completed_bytes: int,
        total_bytes: int,
        completed_entries: int,
        total_entries: int,
    ) -> None:
        with self._lock:
            if record.state not in {"running", "cancelling"}:
                return
            record.phase = phase
            record.progress = RunPackageValidationProgress(
                kind="known" if total_bytes > 0 or total_entries > 0 else "unknown",
                completedBytes=completed_bytes,
                totalBytes=total_bytes,
                completedEntries=completed_entries,
                totalEntries=total_entries,
            )

    def _terminal_error(
        self,
        record: _JobRecord,
        code: JobErrorCode,
        message: str,
        retryable: bool,
        state: TerminalState,
    ) -> None:
        with self._lock:
            if record.cancel_event.is_set() and record.state == "cancelling":
                self._publish_error(record, "cancelled", "Проверка отменена.", False, "cancelled")
            else:
                self._publish_error(record, code, message, retryable, state)

    @staticmethod
    def _publish_error(
        record: _JobRecord,
        code: JobErrorCode,
        message: str,
        retryable: bool,
        state: TerminalState,
    ) -> None:
        record.state = state
        record.report = None
        record.typed_error = RunPackageValidationJobError(code=code, message=message, retryable=retryable)
        record.finished_at_utc = utc_now()

    def _require_job(self, job_id: str) -> _JobRecord:
        if self._job is None or self._job.job_id != job_id:
            raise ProjectOperationError("entity_not_found", "Проверка пакета не найдена.")
        return self._job

    @staticmethod
    def _snapshot(record: _JobRecord) -> RunPackageValidationJobSnapshot:
        return RunPackageValidationJobSnapshot(
            jobId=record.job_id,
            state=record.state,
            phase=record.phase,
            progress=record.progress.model_copy(deep=True),
            startedAtUtc=record.started_at_utc,
            finishedAtUtc=record.finished_at_utc,
            report=None if record.report is None else record.report.model_copy(deep=True),
            typedError=None if record.typed_error is None else record.typed_error.model_copy(deep=True),
        )
