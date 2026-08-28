from pathlib import Path
from uuid import uuid4

from impeller_reliability.protocol.envelopes import (
    REQUEST_ENVELOPE_ADAPTER,
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


def _dispatch(dispatcher: Dispatcher, operation: str, payload: dict[str, object], revision: int) -> dict[str, object]:
    request = REQUEST_ENVELOPE_ADAPTER.validate_python(
        {
            "protocolVersion": 1,
            "requestId": f"request-{revision}",
            "kind": "request",
            "operation": operation,
            "revision": revision,
            "deadlineMs": 5_000,
            "payload": payload,
        }
    )
    return dispatcher.dispatch(request).result.model_dump(mode="python")


def test_handshake_reports_current_capabilities_and_revision(tmp_path: Path) -> None:
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
        "caseCustomer.get",
        "caseCustomer.upsert",
        "wheelModel.create",
        "wheelModel.list",
        "wheelModel.get",
        "wheelModel.update",
        "wheelModel.archive",
        "wheelModel.restore",
        "specimen.create",
        "specimen.list",
        "specimen.get",
        "specimen.update",
        "specimen.archive",
        "specimen.restore",
        "caseDocument.create",
        "caseDocument.createWithFile",
        "caseDocument.list",
        "caseDocument.get",
        "caseDocument.update",
        "caseDocument.attachFile",
        "caseDocument.verifyFile",
        "caseDocument.archive",
        "caseDocument.restore",
        "caseDocument.resolveFile",
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


def test_dispatcher_covers_operation_specific_analyst_dossier_contracts(tmp_path: Path) -> None:
    dispatcher = Dispatcher(tmp_path)
    project_path = tmp_path / "dossier-dispatcher.irproj"
    _dispatch(
        dispatcher,
        "project.create",
        {
            "path": str(project_path),
            "applicationInstanceId": str(uuid4()),
            "applicationVersion": "0.1.0",
            "draft": {"name": "Дело", "projectNumber": "", "description": "", "status": "draft"},
        },
        1,
    )
    assert _dispatch(dispatcher, "caseCustomer.get", {}, 2)["customer"] is None
    customer = _dispatch(
        dispatcher,
        "caseCustomer.upsert",
        {
            "expectedRevision": None,
            "customer": {"fullName": "Заказчик", "legalAddress": "", "actualAddress": "", "notes": ""},
        },
        3,
    )
    assert customer["recordRevision"] == 1
    wheel_draft: dict[str, object] = {
        "fullName": "Колесо",
        "designation": "К-1",
        "nominalDiameterMm": "500",
        "nominalSpeedRpm": 1500,
        "bladeCount": 12,
        "geometryDescription": "",
        "compositionDescription": "",
        "materialDescription": "",
        "notes": "",
    }
    wheel = _dispatch(
        dispatcher,
        "wheelModel.create",
        {"wheelModelId": str(uuid4()), **wheel_draft},
        4,
    )
    wheel_id = str(wheel["wheelModelId"])
    assert _dispatch(dispatcher, "wheelModel.list", {"includeArchived": False}, 5)["items"]
    assert _dispatch(dispatcher, "wheelModel.get", {"wheelModelId": wheel_id}, 6)["designation"] == "К-1"
    wheel = _dispatch(
        dispatcher,
        "wheelModel.update",
        {"wheelModelId": wheel_id, "expectedRevision": 1, "wheelModel": {**wheel_draft, "designation": "К-2"}},
        7,
    )
    specimen_draft: dict[str, object] = {
        "wheelModelId": wheel_id,
        "identificationNumber": "SN-1",
        "batchNumber": "",
        "marking": "",
        "manufacturedOn": None,
        "receivedOn": None,
        "workingDiameterMm": None,
        "initialConditionNotes": "",
        "notes": "",
    }
    specimen = _dispatch(
        dispatcher,
        "specimen.create",
        {"specimenId": str(uuid4()), **specimen_draft},
        8,
    )
    specimen_id = str(specimen["specimenId"])
    assert _dispatch(dispatcher, "specimen.list", {"includeArchived": False}, 9)["items"]
    assert _dispatch(dispatcher, "specimen.get", {"specimenId": specimen_id}, 10)["wheelModelId"] == wheel_id
    specimen = _dispatch(
        dispatcher,
        "specimen.update",
        {"specimenId": specimen_id, "expectedRevision": 1, "specimen": {**specimen_draft, "notes": "Уточнено"}},
        11,
    )
    specimen = _dispatch(dispatcher, "specimen.archive", {"specimenId": specimen_id, "expectedRevision": specimen["recordRevision"]}, 12)
    specimen = _dispatch(dispatcher, "specimen.restore", {"specimenId": specimen_id, "expectedRevision": specimen["recordRevision"]}, 13)
    specimen = _dispatch(dispatcher, "specimen.archive", {"specimenId": specimen_id, "expectedRevision": specimen["recordRevision"]}, 14)
    wheel = _dispatch(dispatcher, "wheelModel.archive", {"wheelModelId": wheel_id, "expectedRevision": wheel["recordRevision"]}, 15)
    wheel = _dispatch(dispatcher, "wheelModel.restore", {"wheelModelId": wheel_id, "expectedRevision": wheel["recordRevision"]}, 16)
    _dispatch(dispatcher, "specimen.restore", {"specimenId": specimen_id, "expectedRevision": specimen["recordRevision"]}, 17)
    assert wheel["archivedAtUtc"] is None
    dispatcher.close()


def test_dispatcher_covers_case_document_contracts(tmp_path: Path) -> None:
    dispatcher = Dispatcher(tmp_path)
    project_path = tmp_path / "case-document-dispatcher.irproj"
    _dispatch(
        dispatcher,
        "project.create",
        {
            "path": str(project_path),
            "applicationInstanceId": str(uuid4()),
            "applicationVersion": "0.1.0",
            "draft": {"name": "Дело", "projectNumber": "", "description": "", "status": "draft"},
        },
        1,
    )
    document_id = str(uuid4())
    document = {
        "documentKind": "standard",
        "title": "ГОСТ",
        "designation": "ГОСТ 1",
        "revisionLabel": "Ред. 1",
        "documentDate": "2026-08-28",
        "issuer": "Росстандарт",
        "notes": "",
    }
    created = _dispatch(
        dispatcher,
        "caseDocument.create",
        {
            "caseDocumentId": document_id,
            "document": document,
            "wheelModelIds": [],
            "specimenIds": [],
        },
        2,
    )
    assert created["recordRevision"] == 1
    assert created["integrityStatus"] == "not_attached"
    source = tmp_path / "standard.pdf"
    source.write_bytes(b"%PDF-1.7\ndispatcher\n")
    attached = _dispatch(
        dispatcher,
        "caseDocument.attachFile",
        {
            "caseDocumentId": document_id,
            "expectedRevision": 1,
            "sourcePath": str(source),
        },
        3,
    )
    assert attached["recordRevision"] == 2
    assert attached["integrityStatus"] == "verified"
    assert _dispatch(
        dispatcher,
        "caseDocument.list",
        {"includeArchived": False, "documentKind": "standard"},
        4,
    )["items"]
    assert _dispatch(
        dispatcher,
        "caseDocument.get",
        {"caseDocumentId": document_id},
        5,
    )["file"]
    assert (
        _dispatch(
            dispatcher,
            "caseDocument.verifyFile",
            {"caseDocumentId": document_id},
            6,
        )["integrityStatus"]
        == "verified"
    )
    resolved = _dispatch(
        dispatcher,
        "caseDocument.resolveFile",
        {"caseDocumentId": document_id},
        7,
    )
    assert Path(str(resolved["absolutePath"])).is_file()
    updated = _dispatch(
        dispatcher,
        "caseDocument.update",
        {
            "caseDocumentId": document_id,
            "expectedRevision": 2,
            "document": {**document, "notes": "Уточнено"},
            "wheelModelIds": [],
            "specimenIds": [],
        },
        8,
    )
    archived = _dispatch(
        dispatcher,
        "caseDocument.archive",
        {"caseDocumentId": document_id, "expectedRevision": updated["recordRevision"]},
        9,
    )
    restored = _dispatch(
        dispatcher,
        "caseDocument.restore",
        {"caseDocumentId": document_id, "expectedRevision": archived["recordRevision"]},
        10,
    )
    assert restored["archivedAtUtc"] is None
    dispatcher.close()
