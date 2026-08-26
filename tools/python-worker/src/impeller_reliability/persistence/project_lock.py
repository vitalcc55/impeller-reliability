from __future__ import annotations

from dataclasses import dataclass
import json
import msvcrt
import os
from pathlib import Path
import socket
from typing import BinaryIO, Self

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_paths import (
    inspect_opened_regular_file,
    inspect_reserved_file,
)


@dataclass(frozen=True, slots=True)
class LockOwner:
    project_id: str
    application_instance_id: str
    pid: int
    started_at_utc: str
    host: str

    def to_json(self) -> bytes:
        payload = {
            "projectId": self.project_id,
            "applicationInstanceId": self.application_instance_id,
            "pid": self.pid,
            "startedAtUtc": self.started_at_utc,
            "host": self.host,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class ProjectLock:
    def __init__(self, path: Path, stream: BinaryIO) -> None:
        self._path = path
        self._stream = stream
        self._released = False

    @classmethod
    def acquire(cls, path: Path, owner: LockOwner) -> ProjectLock:
        expected_identity = inspect_reserved_file(path, ".project.lock", allow_missing=True)
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
        except FileExistsError:
            if expected_identity is None:
                expected_identity = inspect_reserved_file(path, ".project.lock")
            descriptor = os.open(path, os.O_RDWR | os.O_BINARY)
        try:
            opened_identity = inspect_opened_regular_file(descriptor, ".project.lock")
            if expected_identity is not None and opened_identity != expected_identity:
                raise ProjectOperationError("corrupt_project", ".project.lock был подменён перед захватом блокировки.")
        except Exception:
            os.close(descriptor)
            raise
        stream = os.fdopen(descriptor, "r+b", buffering=0)
        if os.fstat(descriptor).st_size == 0:
            stream.write(b"\0")
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            stream.seek(1)
            raw_owner = stream.read(4096).decode("utf-8", errors="replace")
            stream.close()
            raise ProjectOperationError(
                "project_locked",
                "Проект уже открыт в другом процессе.",
                details={"owner": raw_owner},
                retryable=True,
            ) from error

        payload = owner.to_json()
        stream.seek(1)
        stream.write(payload)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
        stream.seek(0)
        return cls(path, stream)

    @property
    def path(self) -> Path:
        return self._path

    def release(self) -> None:
        if self._released:
            return
        self._stream.seek(0)
        msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
        self._stream.close()
        self._released = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _exc_type: object, _exc_value: object, _traceback: object) -> None:
        self.release()


def current_lock_owner(project_id: str, application_instance_id: str, started_at_utc: str) -> LockOwner:
    return LockOwner(
        project_id=project_id,
        application_instance_id=application_instance_id,
        pid=os.getpid(),
        started_at_utc=started_at_utc,
        host=socket.gethostname(),
    )
