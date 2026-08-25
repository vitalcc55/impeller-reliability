from pathlib import Path

from impeller_reliability.protocol.envelopes import (
    EmptyPayload,
    HandshakeRequest,
    HandshakeResult,
    PingRequest,
    PingResult,
    ShutdownRequest,
    ShutdownResult,
)
from impeller_reliability.worker.dispatcher import Dispatcher


def test_handshake_reports_only_m01_capabilities_and_revision(tmp_path: Path) -> None:
    request = HandshakeRequest(
        protocolVersion=1,
        requestId="request-1",
        kind="request",
        operation="system.handshake",
        revision=12,
        deadlineMs=5_000,
        payload=EmptyPayload(),
    )
    response = Dispatcher(tmp_path).dispatch(request)
    assert response.revision == 12
    assert isinstance(response.result, HandshakeResult)
    assert response.result.protocolVersions == [1]
    assert response.result.algorithmVersions == {}
    assert response.result.capabilities == [
        "system.handshake",
        "system.ping",
        "system.shutdown",
        "storage.health",
    ]


def test_ping_and_shutdown_are_explicit_operations(tmp_path: Path) -> None:
    dispatcher = Dispatcher(tmp_path)
    ping = dispatcher.dispatch(
        PingRequest(
            protocolVersion=1,
            requestId="ping",
            kind="request",
            operation="system.ping",
            revision=1,
            deadlineMs=5_000,
            payload=EmptyPayload(),
        )
    )
    assert isinstance(ping.result, PingResult)
    assert ping.result.pong is True
    shutdown = dispatcher.dispatch(
        ShutdownRequest(
            protocolVersion=1,
            requestId="shutdown",
            kind="request",
            operation="system.shutdown",
            revision=2,
            deadlineMs=5_000,
            payload=EmptyPayload(),
        )
    )
    assert isinstance(shutdown.result, ShutdownResult)
    assert shutdown.result.accepted is True
    assert dispatcher.shutdown_requested is True
