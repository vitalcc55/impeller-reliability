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


def test_run_package_validation_payload_is_internal_and_bounded() -> None:
    request = REQUEST_ENVELOPE_ADAPTER.validate_python(
        {
            "protocolVersion": 1,
            "requestId": "validation-1",
            "kind": "request",
            "operation": "runPackageValidation.start",
            "revision": 4,
            "deadlineMs": 5_000,
            "payload": {
                "jobId": "b8503ed4-66ba-4ab5-aead-f8cbe36cbc75",
                "sourcePath": "C:\\approved\\candidate.r130run",
                "validationBudgetMs": 1_800_000,
            },
        }
    )
    assert request.operation == "runPackageValidation.start"
    assert request.payload.validationBudgetMs == 1_800_000

    for invalid_budget in (0, 999, 1_800_001, 1.5):
        with pytest.raises(ValidationError):
            REQUEST_ENVELOPE_ADAPTER.validate_python(
                {
                    **request.model_dump(mode="python"),
                    "payload": {**request.payload.model_dump(mode="python"), "validationBudgetMs": invalid_budget},
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
