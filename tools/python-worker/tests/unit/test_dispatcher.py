from pathlib import Path

from impeller_reliability.protocol.envelopes import RequestEnvelope
from impeller_reliability.worker.dispatcher import Dispatcher


def _request(operation: str) -> RequestEnvelope:
    return RequestEnvelope.model_validate(
        {
            "protocolVersion": 1,
            "requestId": "request-1",
            "kind": "request",
            "operation": operation,
            "revision": 0,
            "deadlineMs": 5_000,
            "payload": {},
        }
    )


def test_handshake_reports_only_m01_capabilities(tmp_path: Path) -> None:
    response = Dispatcher(tmp_path).dispatch(_request("system.handshake"))
    assert response.result["protocolVersions"] == [1]
    assert response.result["algorithmVersions"] == {}
    assert response.result["capabilities"] == [
        "system.handshake",
        "system.ping",
        "system.shutdown",
        "storage.health",
    ]


def test_ping_and_shutdown(tmp_path: Path) -> None:
    dispatcher = Dispatcher(tmp_path)
    assert dispatcher.dispatch(_request("system.ping")).result == {"pong": True}
    assert dispatcher.dispatch(_request("system.shutdown")).result == {"accepted": True}
    assert dispatcher.shutdown_requested is True
