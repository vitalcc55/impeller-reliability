from pathlib import Path
from uuid import uuid4

from impeller_reliability.protocol.envelopes import (
    EmptyPayload,
    HandshakeRequest,
    HandshakeResult,
    PingRequest,
    PingResult,
    ProjectCreateBackupRequest,
    ProjectCreatePayload,
    ProjectCreateRequest,
    ProjectDraft,
    ShutdownRequest,
    ShutdownResult,
)
from impeller_reliability.worker.dispatcher import Dispatcher


def test_handshake_reports_m02_1_capabilities_and_revision(tmp_path: Path) -> None:
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
        "project.create",
        "project.open",
        "project.close",
        "project.getOverview",
        "project.updateMetadata",
        "project.createBackup",
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


def test_backup_runs_only_for_explicit_backup_request(tmp_path: Path) -> None:
    dispatcher = Dispatcher(tmp_path)
    project_path = tmp_path / "dispatcher.irproj"
    dispatcher.dispatch(
        ProjectCreateRequest(
            protocolVersion=1,
            requestId="create",
            kind="request",
            operation="project.create",
            revision=1,
            deadlineMs=5_000,
            payload=ProjectCreatePayload(
                path=str(project_path),
                applicationInstanceId=str(uuid4()),
                applicationVersion="0.1.0",
                draft=ProjectDraft(
                    name="Dispatcher",
                    projectNumber="",
                    description="",
                    status="draft",
                ),
            ),
        )
    )
    dispatcher.dispatch(
        PingRequest(
            protocolVersion=1,
            requestId="ping-after-create",
            kind="request",
            operation="system.ping",
            revision=2,
            deadlineMs=5_000,
            payload=EmptyPayload(),
        )
    )
    assert list((project_path / "backups").glob("*.sqlite")) == []
    dispatcher.dispatch(
        ProjectCreateBackupRequest(
            protocolVersion=1,
            requestId="backup",
            kind="request",
            operation="project.createBackup",
            revision=3,
            deadlineMs=5_000,
            payload=EmptyPayload(),
        )
    )
    assert len(list((project_path / "backups").glob("*.sqlite"))) == 1
    dispatcher.close()
