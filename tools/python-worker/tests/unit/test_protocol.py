import json

from pydantic import ValidationError
import pytest

from impeller_reliability.protocol.envelopes import (
    REQUEST_ENVELOPE_ADAPTER,
    CaseDocumentFileResult,
    EmptyPayload,
    PingRequest,
)


def test_request_rejects_unknown_operation() -> None:
    with pytest.raises(ValidationError):
        REQUEST_ENVELOPE_ADAPTER.validate_python(
            {
                "protocolVersion": 1,
                "requestId": "request-1",
                "kind": "request",
                "operation": "system.execute",
                "revision": 0,
                "deadlineMs": 1_000,
                "payload": {},
            }
        )


def test_operation_payload_is_strict_and_json_serializable() -> None:
    with pytest.raises(ValidationError):
        REQUEST_ENVELOPE_ADAPTER.validate_python(
            {
                "protocolVersion": 1,
                "requestId": "request-1",
                "kind": "request",
                "operation": "system.ping",
                "revision": 0,
                "deadlineMs": 1_000,
                "payload": {"unexpected": True},
            }
        )
    request = PingRequest(
        protocolVersion=1,
        requestId="request-1",
        kind="request",
        operation="system.ping",
        revision=7,
        deadlineMs=1_000,
        payload=EmptyPayload(),
    )
    payload = json.loads(request.model_dump_json())
    assert payload["operation"] == "system.ping"
    assert payload["revision"] == 7


def test_case_document_payloads_are_operation_specific() -> None:
    request = REQUEST_ENVELOPE_ADAPTER.validate_python(
        {
            "protocolVersion": 1,
            "requestId": "document-1",
            "kind": "request",
            "operation": "caseDocument.create",
            "revision": 3,
            "deadlineMs": 30_000,
            "payload": {
                "caseDocumentId": "113ec2c8-9439-4ce8-823d-3e2b0de8f001",
                "document": {
                    "documentKind": "standard",
                    "title": " ГОСТ ",
                    "designation": "",
                    "revisionLabel": "",
                    "documentDate": None,
                    "issuer": "",
                    "notes": "",
                },
                "wheelModelIds": [],
                "specimenIds": [],
            },
        }
    )
    assert request.operation == "caseDocument.create"
    assert request.payload.document.title == "ГОСТ"
    with pytest.raises(ValidationError):
        REQUEST_ENVELOPE_ADAPTER.validate_python(
            {
                **request.model_dump(mode="python"),
                "payload": {**request.payload.model_dump(mode="python"), "sourcePath": "C:\\secret.pdf"},
            }
        )

    with pytest.raises(ValidationError):
        CaseDocumentFileResult(
            originalFileName=r"C:\secret.pdf",
            mediaType="application/pdf",
            sizeBytes=10,
            sha256="a" * 64,
            attachedAtUtc="2026-08-28T00:00:00.000Z",
        )
