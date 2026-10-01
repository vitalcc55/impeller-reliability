from __future__ import annotations

import json
from uuid import uuid4

from pydantic import ValidationError
import pytest

from impeller_reliability.protocol.envelopes import REQUEST_ENVELOPE_ADAPTER, MaterialCopyDiscardRequest


def origin() -> dict[str, object]:
    return {"projectId": str(uuid4()), "localImportId": str(uuid4()), "packageId": "package", "runId": "run", "exportRevision": 1, "outerPackageSha256": "a" * 64}


def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
    return {"protocolVersion": 1, "requestId": str(uuid4()), "kind": "request", "revision": 1, "deadlineMs": 30000, "operation": operation, "payload": payload}


@pytest.mark.parametrize("operation", ["listInspectionPage", "getInspection", "listPhotoPage", "getProtocol", "resolveMaterial"])
def test_material_requests_parse_canonical_json(operation: str) -> None:
    payload: dict[str, object] = {"origin": origin()}
    if operation == "getInspection":
        payload["inspectionId"] = "inspection"
    elif operation == "resolveMaterial":
        payload = {"identity": {"origin": origin(), "kind": "protocol", "materialId": str(2**80)}, "outputDirectory": "C:/private-copy", "copyId": str(uuid4())}
    parsed = REQUEST_ENVELOPE_ADAPTER.validate_json(json.dumps(request(f"importedRun.{operation}", payload)))
    assert parsed.operation == f"importedRun.{operation}"


@pytest.mark.parametrize("field", ["path", "absolutePath", "memberPath", "destination", "url"])
def test_metadata_request_has_no_path_capability(field: str) -> None:
    with pytest.raises(ValidationError):
        REQUEST_ENVELOPE_ADAPTER.validate_python(request("importedRun.getProtocol", {"origin": origin(), field: "C:/outside"}))


@pytest.mark.parametrize("limit", [0, 51, True, 25.0])
def test_material_request_page_limit_is_strict(limit: object) -> None:
    with pytest.raises(ValidationError):
        REQUEST_ENVELOPE_ADAPTER.validate_python(request("importedRun.listPhotoPage", {"origin": origin(), "limit": limit}))


def test_material_origin_does_not_accept_python_field_alias_in_wire() -> None:
    values = origin()
    values["project_id"] = values.pop("projectId")
    with pytest.raises(ValidationError):
        REQUEST_ENVELOPE_ADAPTER.validate_python(request("importedRun.getProtocol", {"origin": values}))


def test_private_discard_preserves_high_native_identity_bits() -> None:
    payload: dict[str, object] = {"approvedDirectory": "C:/private-copy", "copyId": str(uuid4()), "mediaType": "application/pdf", "fileIdentity": {"fileId": "f" * 32, "volumeId": "f" * 16}}
    parsed = REQUEST_ENVELOPE_ADAPTER.validate_json(json.dumps(request("materialCopy.discard", payload)))
    assert isinstance(parsed, MaterialCopyDiscardRequest)
    assert parsed.payload.fileIdentity.fileId == "f" * 32
    assert parsed.payload.fileIdentity.volumeId == "f" * 16
    assert REQUEST_ENVELOPE_ADAPTER.validate_json(parsed.model_dump_json(by_alias=True)) == parsed


@pytest.mark.parametrize("file_id", [2**80, "F" * 32, "f" * 31, "f" * 33])
def test_private_discard_rejects_rounded_or_malformed_identity(file_id: object) -> None:
    payload: dict[str, object] = {"approvedDirectory": "C:/private-copy", "copyId": str(uuid4()), "mediaType": "application/pdf", "fileIdentity": {"fileId": file_id, "volumeId": "f" * 16}}
    with pytest.raises(ValidationError):
        REQUEST_ENVELOPE_ADAPTER.validate_python(request("materialCopy.discard", payload))
