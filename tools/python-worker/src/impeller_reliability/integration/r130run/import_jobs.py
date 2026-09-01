from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from typing import Final
from uuid import UUID

from impeller_reliability.integration.r130run.import_models import (
    ImportJobErrorCode,
    ImportJobPhase,
    ImportJobState,
    RunPackageImportDiscardResult,
    RunPackageImportJobError,
    RunPackageImportJobSnapshot,
    RunPackageImportResult,
    imported_run_summary_model,
)
from impeller_reliability.integration.r130run.m9a import (
    M9aContractError,
    M9aPackageFacts,
    read_m9a_package_facts,
)
from impeller_reliability.integration.r130run.models import RunPackageValidationReport
from impeller_reliability.integration.r130run.validator import (
    RunPackageValidator,
    SourceChangedError,
    ValidationCancelledError,
    ValidationControl,
    ValidationTimeoutError,
    inspect_source,
)
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_paths import ensure_managed_directory
from impeller_reliability.persistence.r130sh_sources import ImportedRunSummary
from impeller_reliability.worker.deadline import RequestDeadline

STREAM_CHUNK_BYTES: Final = 1024 * 1024
IMPORT_TIMEOUT_SECONDS: Final = 300.0

FinalizeCallback = Callable[
    [str, Path, M9aPackageFacts, RunPackageValidationReport, RequestDeadline | None],
    ImportedRunSummary,
]


@dataclass(slots=True)
class _PreparedImport:
    staged_path: Path
    facts: M9aPackageFacts
    report: RunPackageValidationReport


@dataclass(slots=True)
class _ImportRecord:
    job_id: str
    project_path: Path
    source_path: Path
    source_signature: tuple[int, int, int, int]
    source_size: int
    allow_diagnostic_partial: bool
    local_import_id: str
    cancel_event: Event
    state: ImportJobState = "queued"
    phase: ImportJobPhase = "queued"
    completed_bytes: int = 0
    total_bytes: int = 0
    completed_entries: int = 0
    total_entries: int = 0
    started_at_utc: str | None = None
    finished_at_utc: str | None = None
    result: RunPackageImportResult | None = None
    typed_error: RunPackageImportJobError | None = None
    prepared: _PreparedImport | None = None
    commit_started: bool = False


class RunPackageImportJobManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._record: _ImportRecord | None = None
        self._thread: Thread | None = None

    @property
    def has_active_job(self) -> bool:
        with self._lock:
            return self._record is not None and self._record.state not in {
                "completed",
                "failed",
                "cancelled",
            }

    def start(
        self,
        *,
        job_id: str,
        project_path: Path,
        source_path: Path,
        allow_diagnostic_partial: bool,
        replace_job_id: str | None = None,
    ) -> RunPackageImportJobSnapshot:
        job_id = _uuid4(job_id)
        replace_job_id = None if replace_job_id is None else _uuid4(replace_job_id)
        fingerprint = inspect_source(source_path)
        signature = fingerprint.signature
        signature_tuple = (
            signature.size,
            signature.mtime_ns,
            signature.device,
            signature.inode,
        )
        with self._lock:
            current = self._record
            if current is not None:
                if current.job_id == job_id:
                    if (
                        current.project_path != project_path
                        or current.source_path != source_path
                        or current.source_signature != signature_tuple
                        or current.allow_diagnostic_partial != allow_diagnostic_partial
                    ):
                        raise ProjectOperationError(
                            "job_id_conflict",
                            "Job ID уже связан с другим import request.",
                        )
                    return _snapshot(current)
                if current.state not in {"completed", "failed", "cancelled"}:
                    raise ProjectOperationError(
                        "operation_in_progress",
                        "В проекте уже выполняется импорт R130SH.",
                    )
                if replace_job_id != current.job_id:
                    raise ProjectOperationError(
                        "operation_in_progress",
                        "Завершённый import job можно заменить только явным compare-and-replace.",
                    )
                self._record = None
                self._thread = None
            record = _ImportRecord(
                job_id=job_id,
                project_path=project_path,
                source_path=Path(fingerprint.resolved_path),
                source_signature=signature_tuple,
                source_size=signature.size,
                allow_diagnostic_partial=allow_diagnostic_partial,
                # The operation identity is also the durable import identity, allowing
                # reconciliation when a response is lost after the database commit.
                local_import_id=job_id,
                cancel_event=Event(),
                total_bytes=signature.size,
            )
            self._record = record
            thread = Thread(
                target=self._run,
                args=(record,),
                name="r130sh-import",
                daemon=False,
            )
            self._thread = thread
            thread.start()
            return _snapshot(record)

    def get(
        self,
        job_id: str,
        *,
        finalize: FinalizeCallback,
        deadline: RequestDeadline | None,
    ) -> RunPackageImportJobSnapshot:
        record = self._require(job_id)
        with self._lock:
            if record.state != "registering" or record.prepared is None or record.commit_started:
                return _snapshot(record)
            record.commit_started = True
            prepared = record.prepared
        try:
            imported = finalize(
                record.local_import_id,
                prepared.staged_path,
                prepared.facts,
                prepared.report,
                deadline,
            )
        except ProjectOperationError as error:
            code: ImportJobErrorCode
            if error.code == "import_integrity_conflict":
                code = "import_integrity_conflict"
            elif error.code == "timeout":
                code = "timeout"
            else:
                code = "storage_error"
            self._fail(
                record,
                code=code,
                message=error.message,
                retryable=error.retryable,
            )
        except Exception:
            self._fail(
                record,
                code="storage_error",
                message="Не удалось зарегистрировать импортированный пакет.",
                retryable=True,
            )
        else:
            with self._lock:
                record.state = "completed"
                record.phase = "terminal"
                record.completed_bytes = record.source_size
                record.total_bytes = record.source_size
                record.completed_entries = record.total_entries
                record.result = RunPackageImportResult(
                    disposition="existing" if imported.imported_existing else "created",
                    importedRun=imported_run_summary_model(imported),
                )
                record.finished_at_utc = _utc_now()
                record.prepared = None
        return _snapshot(record)

    def cancel(self, job_id: str) -> RunPackageImportJobSnapshot:
        record = self._require(job_id)
        with self._lock:
            if record.state in {"completed", "failed", "cancelled"}:
                return _snapshot(record)
            if record.commit_started:
                return _snapshot(record)
            record.cancel_event.set()
            if record.state == "registering" and record.prepared is not None:
                _remove_staged(record.prepared.staged_path)
                record.prepared = None
                _set_cancelled(record)
            else:
                record.state = "cancelling"
            return _snapshot(record)

    def discard(self, job_id: str) -> RunPackageImportDiscardResult:
        record = self._require(job_id)
        with self._lock:
            if record.state not in {"completed", "failed", "cancelled"}:
                raise ProjectOperationError(
                    "operation_in_progress",
                    "Active import job нельзя очистить.",
                )
            self._record = None
            self._thread = None
        return RunPackageImportDiscardResult(jobId=job_id)

    def shutdown(self, timeout_seconds: float = 10.0) -> None:
        with self._lock:
            record = self._record
            thread = self._thread
            if record is not None and record.state not in {"completed", "failed", "cancelled"} and not record.commit_started:
                record.cancel_event.set()
                if record.state == "registering" and record.prepared is not None:
                    _remove_staged(record.prepared.staged_path)
                    record.prepared = None
                    _set_cancelled(record)
                else:
                    record.state = "cancelling"
        if thread is not None and thread.is_alive():
            thread.join(timeout_seconds)
            if thread.is_alive():
                raise ProjectOperationError(
                    "operation_in_progress",
                    "Import job не завершился в bounded shutdown.",
                )

    def _run(self, record: _ImportRecord) -> None:
        record.started_at_utc = _utc_now()
        expires_at = monotonic() + IMPORT_TIMEOUT_SECONDS
        staged_path: Path | None = None
        staged_transferred = False
        try:
            self._set_state(record, "validating", "source_validation")
            source_report = RunPackageValidator().validate(
                record.source_path,
                ValidationControl(
                    record.cancel_event,
                    expires_at,
                    lambda phase, completed, total, entries, entry_total: self._validation_progress(
                        record,
                        phase,
                        completed,
                        total,
                        entries,
                        entry_total,
                    ),
                ),
            )
            if source_report.structuralVerdict != "passed" or source_report.semanticVerdict != "passed":
                raise _ImportFailure(
                    "validation_error",
                    "Пакет не прошёл production validation.",
                    False,
                )
            if source_report.packageKind == "diagnostic_partial" and not record.allow_diagnostic_partial:
                raise _ImportFailure(
                    "diagnostic_confirmation_required",
                    "Для диагностического неполного результата требуется явное подтверждение.",
                    False,
                )
            self._set_state(record, "copying", "streaming_copy")
            staged_path, staged_sha = self._copy_to_staging(record, expires_at)
            if staged_sha != source_report.outerPackageSha256:
                _remove_staged(staged_path)
                raise SourceChangedError
            self._set_state(record, "revalidating", "staged_validation")
            staged_report = RunPackageValidator().validate(
                staged_path,
                ValidationControl(
                    record.cancel_event,
                    expires_at,
                    lambda phase, completed, total, entries, entry_total: self._validation_progress(
                        record,
                        phase,
                        completed,
                        total,
                        entries,
                        entry_total,
                    ),
                ),
            )
            if staged_report.structuralVerdict != "passed" or staged_report.semanticVerdict != "passed" or staged_report.outerPackageSha256 != source_report.outerPackageSha256:
                _remove_staged(staged_path)
                raise _ImportFailure(
                    "validation_error",
                    "Staged copy не прошла повторную validation.",
                    False,
                )
            facts = read_m9a_package_facts(
                staged_path,
                staged_report,
                checkpoint=lambda: _import_checkpoint(record, expires_at),
            )
            with self._lock:
                if record.cancel_event.is_set():
                    raise ValidationCancelledError
                record.state = "registering"
                record.phase = "database_registration"
                record.completed_bytes = record.source_size
                record.total_bytes = record.source_size
                record.completed_entries = record.total_entries
                record.prepared = _PreparedImport(staged_path, facts, staged_report)
                staged_transferred = True
        except ValidationCancelledError:
            with self._lock:
                _set_cancelled(record)
        except ValidationTimeoutError:
            self._fail(record, "timeout", "Импорт превысил bounded deadline.", True)
        except SourceChangedError:
            self._fail(record, "source_changed", "Исходный пакет изменился во время импорта.", True)
        except M9aContractError:
            self._fail(record, "validation_error", "Пакет не прошёл M9a projection validation.", False)
        except _ImportFailure as error:
            self._fail(record, error.code, error.message, error.retryable)
        except ProjectOperationError as error:
            self._fail(
                record,
                "storage_error" if error.code in {"storage_error", "corrupt_project"} else "validation_error",
                error.message,
                error.retryable,
            )
        except OSError:
            self._fail(record, "storage_error", "Не удалось прочитать или скопировать пакет.", True)
        except Exception:
            self._fail(record, "validation_error", "Production import завершился ошибкой validation.", False)
        finally:
            if staged_path is not None and not staged_transferred:
                _remove_staged(staged_path)

    def _copy_to_staging(
        self,
        record: _ImportRecord,
        expires_at: float,
    ) -> tuple[Path, str]:
        imports_root = record.project_path / "imports"
        r130sh_root = imports_root / "r130sh"
        staging_root = r130sh_root / ".staging"
        ensure_managed_directory(imports_root, "imports/")
        ensure_managed_directory(r130sh_root, "imports/r130sh/")
        ensure_managed_directory(staging_root, "imports/r130sh/.staging/")
        staged_path = staging_root / f"{record.local_import_id}.part"
        if staged_path.exists():
            raise OSError("staging_path_exists")
        digest = hashlib.sha256()
        completed = 0
        try:
            before = os.stat(record.source_path, follow_symlinks=False)
            if _stat_signature(before) != record.source_signature:
                raise SourceChangedError
            with (
                record.source_path.open("rb", buffering=0) as source,
                staged_path.open(
                    "xb",
                    buffering=0,
                ) as target,
            ):
                while True:
                    if record.cancel_event.is_set():
                        raise ValidationCancelledError
                    if monotonic() >= expires_at:
                        raise ValidationTimeoutError
                    chunk = source.read(STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    target.write(chunk)
                    digest.update(chunk)
                    completed += len(chunk)
                    self._copy_progress(record, completed)
                target.flush()
                os.fsync(target.fileno())
            after = os.stat(record.source_path, follow_symlinks=False)
            if _stat_signature(after) != record.source_signature or completed != record.source_size:
                raise SourceChangedError
            return staged_path, digest.hexdigest()
        except Exception:
            _remove_staged(staged_path)
            raise

    def _validation_progress(
        self,
        record: _ImportRecord,
        _phase: str,
        completed: int,
        total: int,
        entries: int,
        entry_total: int,
    ) -> None:
        with self._lock:
            record.completed_bytes = min(completed, total) if total > 0 else completed
            record.total_bytes = total
            record.completed_entries = entries
            record.total_entries = max(record.total_entries, entry_total)

    def _copy_progress(self, record: _ImportRecord, completed: int) -> None:
        with self._lock:
            record.completed_bytes = completed
            record.total_bytes = record.source_size
            record.completed_entries = 0
            record.total_entries = 0

    def _set_state(
        self,
        record: _ImportRecord,
        state: ImportJobState,
        phase: ImportJobPhase,
    ) -> None:
        with self._lock:
            record.state = state
            record.phase = phase
            record.completed_bytes = 0
            record.total_bytes = record.source_size if state == "copying" else 0
            record.completed_entries = 0
            record.total_entries = 0

    def _fail(
        self,
        record: _ImportRecord,
        code: ImportJobErrorCode,
        message: str,
        retryable: bool,
    ) -> None:
        with self._lock:
            if record.prepared is not None:
                _remove_staged(record.prepared.staged_path)
                record.prepared = None
            record.state = "failed"
            record.phase = "terminal"
            record.typed_error = RunPackageImportJobError(
                code=code,
                message=message,
                retryable=retryable,
            )
            record.finished_at_utc = _utc_now()

    def _require(self, job_id: str) -> _ImportRecord:
        job_id = _uuid4(job_id)
        with self._lock:
            if self._record is None or self._record.job_id != job_id:
                raise ProjectOperationError("entity_not_found", "Import job не найден.")
            return self._record


class _ImportFailure(Exception):
    def __init__(
        self,
        code: ImportJobErrorCode,
        message: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code: ImportJobErrorCode = code
        self.message = message
        self.retryable = retryable


def _snapshot(record: _ImportRecord) -> RunPackageImportJobSnapshot:
    return RunPackageImportJobSnapshot(
        jobId=record.job_id,
        state=record.state,
        phase=record.phase,
        completedBytes=record.completed_bytes,
        totalBytes=record.total_bytes,
        completedEntries=record.completed_entries,
        totalEntries=record.total_entries,
        startedAtUtc=record.started_at_utc,
        finishedAtUtc=record.finished_at_utc,
        result=record.result,
        typedError=record.typed_error,
    )


def _set_cancelled(record: _ImportRecord) -> None:
    record.state = "cancelled"
    record.phase = "terminal"
    record.typed_error = RunPackageImportJobError(
        code="cancelled",
        message="Импорт отменён до фиксации в проекте.",
        retryable=True,
    )
    record.finished_at_utc = _utc_now()


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int]:
    return value.st_size, value.st_mtime_ns, value.st_dev, value.st_ino


def _remove_staged(path: Path) -> None:
    try:
        if path.is_file() and not path.is_symlink():
            path.unlink()
    except OSError:
        pass


def _import_checkpoint(record: _ImportRecord, expires_at: float) -> None:
    if record.cancel_event.is_set():
        raise ValidationCancelledError
    if monotonic() >= expires_at:
        raise ValidationTimeoutError


def _uuid4(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ProjectOperationError("validation_error", "Job ID должен быть UUID v4.") from error
    if str(parsed) != value or parsed.version != 4:
        raise ProjectOperationError("validation_error", "Job ID должен быть canonical UUID v4.")
    return value


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = ["FinalizeCallback", "RunPackageImportJobManager"]
