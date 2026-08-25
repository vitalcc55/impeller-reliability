from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
import sys

import pytest

from impeller_reliability.worker import main as worker_main


class _BinaryInput:
    def __init__(self, content: bytes) -> None:
        self.buffer = BytesIO(content)


def _run(content: bytes, state_directory: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(sys, "stdin", _BinaryInput(content))
    output = StringIO()
    with redirect_stdout(output):
        assert worker_main.run_worker(state_directory) == 0
    return output.getvalue().splitlines()


def test_worker_handles_success_contract_error_and_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    messages = (
        b"\n".join(
            [
                b'{"protocolVersion":1,"requestId":"ping","kind":"request","operation":"system.ping","revision":0,"deadlineMs":5000,"payload":{}}',
                b'{"protocolVersion":1,"requestId":"bad","kind":"request","operation":"system.execute","revision":1,"deadlineMs":5000,"payload":{}}',
                b'{"protocolVersion":1,"requestId":"shutdown","kind":"request","operation":"system.shutdown","revision":2,"deadlineMs":5000,"payload":{}}',
            ]
        )
        + b"\n"
    )
    responses = _run(messages, tmp_path, monkeypatch)
    assert '"requestId":"ping"' in responses[0]
    assert '"result":{"pong":true}' in responses[0]
    assert '"code":"contract_error"' in responses[1]
    assert '"result":{"accepted":true}' in responses[2]


def test_worker_rejects_oversized_non_finite_and_invalid_utf8(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oversized = b"x" * (worker_main.MAX_MESSAGE_BYTES + 1) + b"\n"
    non_finite = b'{"value":NaN}\n'
    responses = _run(oversized + non_finite + b"\xff\n", tmp_path, monkeypatch)
    assert len(responses) == 3
    assert all('"ok":false' in response for response in responses)


def test_worker_converts_unexpected_storage_failure_to_internal_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "state-file"
    state_file.write_text("not a directory", encoding="utf-8")
    request = b'{"protocolVersion":1,"requestId":"health","kind":"request","operation":"storage.health","revision":0,"deadlineMs":5000,"payload":{}}\n'
    responses = _run(request, state_file, monkeypatch)
    assert '"requestId":"health"' in responses[0]
    assert '"code":"internal_error"' in responses[0]
    assert '"message":"Внутренняя ошибка worker."' in responses[0]


def test_self_test_and_cli_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = StringIO()
    with redirect_stdout(output):
        assert worker_main.run_self_test() == 0
    assert '"passed":true' in output.getvalue()

    monkeypatch.setattr(sys, "argv", ["worker", "--self-test"])
    output = StringIO()
    with redirect_stdout(output):
        assert worker_main.main() == 0
    assert '"passed":true' in output.getvalue()
