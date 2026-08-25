from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.protocol.envelopes import (
    REQUEST_ENVELOPE_ADAPTER,
    EmptyPayload,
    ErrorPayload,
    ErrorResponse,
    ProtocolResponse,
    StorageHealthRequest,
    StorageHealthResult,
)
from impeller_reliability.worker.deadline import RequestDeadline
from impeller_reliability.worker.dispatcher import Dispatcher

MAX_MESSAGE_BYTES = 1_048_576


@runtime_checkable
class _BinaryWriter(Protocol):
    def write(self, data: bytes) -> int: ...

    def flush(self) -> None: ...


class _RequestIdentity(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    requestId: str = "unknown"
    revision: int = Field(default=0, ge=0)


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"non_finite_number:{value}")


def _write_protocol(model: ProtocolResponse | BaseModel | dict[str, object]) -> None:
    payload = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
    line = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
    binary_stdout = getattr(sys.stdout, "buffer", None)
    if isinstance(binary_stdout, _BinaryWriter):
        binary_stdout.write(line.encode("utf-8"))
        binary_stdout.flush()
    else:
        sys.stdout.write(line)
        sys.stdout.flush()


def _contract_error(request_id: str, revision: int, message: str) -> ErrorResponse:
    return ErrorResponse(
        requestId=request_id,
        revision=revision,
        error=ErrorPayload(code="contract_error", message=message, details={}, retryable=False),
    )


def _request_identity(raw_request: object) -> tuple[str, int]:
    try:
        identity = _RequestIdentity.model_validate(raw_request)
    except ValidationError:
        return ("unknown", 0)
    return (identity.requestId or "unknown", identity.revision)


def run_worker(state_directory: Path) -> int:
    dispatcher = Dispatcher(state_directory)
    try:
        for raw_line in sys.stdin.buffer:
            request_id = "unknown"
            revision = 0
            if len(raw_line) > MAX_MESSAGE_BYTES:
                _write_protocol(_contract_error(request_id, revision, "Сообщение превышает допустимый размер."))
                continue
            try:
                decoded = raw_line.decode("utf-8", errors="strict")
                raw_request: object = json.loads(decoded, parse_constant=_reject_non_finite)
                request_id, revision = _request_identity(raw_request)
                request = REQUEST_ENVELOPE_ADAPTER.validate_python(raw_request)
                response: ProtocolResponse = dispatcher.dispatch(
                    request,
                    RequestDeadline.start(request.deadlineMs),
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
                response = _contract_error(request_id, revision, f"Некорректный запрос: {error}")
            except ProjectOperationError as error:
                response = ErrorResponse(
                    requestId=request_id,
                    revision=revision,
                    error=ErrorPayload(
                        code=error.code,
                        message=error.message,
                        details=error.details,
                        retryable=error.retryable,
                    ),
                )
            except Exception as error:
                print(f"worker_internal_error:{type(error).__name__}:{error}", file=sys.stderr, flush=True)
                response = ErrorResponse(
                    requestId=request_id,
                    revision=revision,
                    error=ErrorPayload(
                        code="internal_error",
                        message="Внутренняя ошибка worker.",
                        details={},
                        retryable=False,
                    ),
                )
            _write_protocol(response)
            if dispatcher.shutdown_requested:
                return 0
        return 0
    finally:
        dispatcher.close()


def run_self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="impeller-worker-self-test-") as directory:
        dispatcher = Dispatcher(Path(directory))
        request = StorageHealthRequest(
            protocolVersion=1,
            requestId="self-test",
            kind="request",
            operation="storage.health",
            revision=0,
            deadlineMs=5_000,
            payload=EmptyPayload(),
        )
        response = dispatcher.dispatch(request)
        if not isinstance(response.result, StorageHealthResult):
            return 1
        storage = response.result.model_dump(mode="json")
        passed = storage.get("status") == "ok"
        _write_protocol({"schemaVersion": 1, "passed": passed, "storage": storage})
        return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return run_self_test()
    state_directory = Path(os.environ.get("IMPELLER_STATE_DIR", tempfile.gettempdir())) / "impeller-reliability"
    return run_worker(state_directory)


if __name__ == "__main__":
    raise SystemExit(main())
