from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from pydantic import ValidationError

from impeller_reliability.protocol.envelopes import (
    ErrorPayload,
    ErrorResponse,
    RequestEnvelope,
    SuccessResponse,
)
from impeller_reliability.worker.dispatcher import Dispatcher

MAX_MESSAGE_BYTES = 1_048_576


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"non_finite_number:{value}")


def _write_protocol(model: SuccessResponse | ErrorResponse | dict[str, object]) -> None:
    if isinstance(model, SuccessResponse):
        payload: dict[str, object] = {
            "protocolVersion": model.protocolVersion,
            "requestId": model.requestId,
            "kind": model.kind,
            "ok": model.ok,
            "result": model.result,
            "evidence": model.evidence,
            "warnings": model.warnings,
        }
    elif isinstance(model, ErrorResponse):
        payload = {
            "protocolVersion": model.protocolVersion,
            "requestId": model.requestId,
            "kind": model.kind,
            "ok": model.ok,
            "error": {
                "code": model.error.code,
                "message": model.error.message,
                "details": model.error.details,
                "retryable": model.error.retryable,
            },
        }
    else:
        payload = model
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _contract_error(request_id: str, message: str) -> ErrorResponse:
    return ErrorResponse(
        requestId=request_id,
        error=ErrorPayload(code="contract_error", message=message, details={}, retryable=False),
    )


def run_worker(state_directory: Path) -> int:
    dispatcher = Dispatcher(state_directory)
    for raw_line in sys.stdin.buffer:
        request_id = "unknown"
        if len(raw_line) > MAX_MESSAGE_BYTES:
            _write_protocol(_contract_error("unknown", "Сообщение превышает допустимый размер."))
            continue
        try:
            decoded = raw_line.decode("utf-8", errors="strict")
            raw_request: object = json.loads(decoded, parse_constant=_reject_non_finite)
            request = RequestEnvelope.model_validate(raw_request)
            request_id = request.requestId
            response: SuccessResponse | ErrorResponse = dispatcher.dispatch(request)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
            response = _contract_error(request_id, f"Некорректный запрос: {error}")
        except Exception as error:
            print(f"worker_internal_error:{type(error).__name__}:{error}", file=sys.stderr, flush=True)
            response = ErrorResponse(
                requestId=request_id,
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


def run_self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="impeller-worker-self-test-") as directory:
        dispatcher = Dispatcher(Path(directory))
        request = RequestEnvelope(
            protocolVersion=1,
            requestId="self-test",
            kind="request",
            operation="storage.health",
            revision=0,
            deadlineMs=5_000,
            payload={},
        )
        response = dispatcher.dispatch(request)
        _write_protocol(
            {
                "schemaVersion": 1,
                "passed": response.result.get("status") == "ok",
                "storage": response.result,
            }
        )
        return 0 if response.result.get("status") == "ok" else 1


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
