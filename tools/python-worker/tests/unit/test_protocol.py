import json

from pydantic import ValidationError
import pytest

from impeller_reliability.protocol.envelopes import (
    REQUEST_ENVELOPE_ADAPTER,
    CaseDocumentFileResult,
    EmptyPayload,
    PingRequest,
    RbdCalculationCreateRequest,
    RbdPlanSourceResult,
    RbdReferenceResultModel,
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


def test_rbd_calculation_command_is_fixed_bounded_and_does_not_accept_outputs() -> None:
    payload = {
        "analysisInputSnapshotId": "113ec2c8-9439-4ce8-823d-3e2b0de8f001",
        "calculationSnapshotId": "223ec2c8-9439-4ce8-823d-3e2b0de8f002",
        "executionId": "333ec2c8-9439-4ce8-823d-3e2b0de8f003",
        "planSelection": "original",
        "selections": [
            {"field": field, "origin": "source"}
            for field in (
                "base_cycles",
                "reserve_factor",
                "nominal_rpm",
                "acceleration_duration_s",
                "deceleration_duration_s",
            )
        ],
        "failureEvidence": None,
        "actor": "Инженер",
        "reason": "Первая строка\nВторая строка",
    }
    request = REQUEST_ENVELOPE_ADAPTER.validate_python(
        {
            "protocolVersion": 1,
            "requestId": "rbd-1",
            "kind": "request",
            "operation": "rbdCalculation.create",
            "revision": 8,
            "deadlineMs": 30_000,
            "payload": payload,
        }
    )
    assert request.operation == "rbdCalculation.create"
    with pytest.raises(ValidationError):
        REQUEST_ENVELOPE_ADAPTER.validate_python(
            {
                **request.model_dump(mode="python"),
                "payload": {**payload, "calculatedOutputs": {"requiredCycles": "1500.3"}},
            }
        )
    with pytest.raises(ValidationError):
        REQUEST_ENVELOPE_ADAPTER.validate_python(
            {
                **request.model_dump(mode="python"),
                "payload": {key: value for key, value in payload.items() if key != "failureEvidence"},
            }
        )
    with pytest.raises(ValidationError):
        REQUEST_ENVELOPE_ADAPTER.validate_python(
            {
                **request.model_dump(mode="python"),
                "payload": {**payload, "reason": "Неверный Unicode\ud800"},
            }
        )
    valid_emoji = REQUEST_ENVELOPE_ADAPTER.validate_python(
        {
            **request.model_dump(mode="python"),
            "payload": {**payload, "reason": "Расчёт 🔧"},
        }
    )
    assert isinstance(valid_emoji, RbdCalculationCreateRequest)
    assert valid_emoji.payload.reason == "Расчёт 🔧"
    with pytest.raises(ValidationError):
        REQUEST_ENVELOPE_ADAPTER.validate_python(
            {
                **request.model_dump(mode="python"),
                "payload": {**payload, "reason": "🔧" * 501},
            }
        )
    with pytest.raises(ValidationError):
        RbdReferenceResultModel.model_validate({"algorithm_id": "rbd_reference"})
    with pytest.raises(ValidationError):
        REQUEST_ENVELOPE_ADAPTER.validate_python(
            {
                **request.model_dump(mode="python"),
                "payload": {
                    **payload,
                    "selections": [{"field": "base_cycles", "origin": "source"}] * 5,
                },
            }
        )


def test_rbd_source_response_accepts_unicode_code_points_without_surrogates() -> None:
    response = {
        "executionId": "333ec2c8-9439-4ce8-823d-3e2b0de8f003",
        "localImportId": "113ec2c8-9439-4ce8-823d-3e2b0de8f001",
        "packageId": "package-1",
        "runId": "run-1",
        "exportRevision": 1,
        "outerPackageSha256": "a" * 64,
        "sourceSnapshotSha256": "b" * 64,
        "producerName": "🔧" * 101,
        "producerVersion": "1",
        "producerBuildId": "build-1",
        "producerGitCommit": "commit-1",
        "planSelection": "original",
        "payloadPath": "plan/original.json",
        "payloadSha256": "c" * 64,
        "planId": "plan-1",
        "planRevision": 1,
        "sourceValues": {
            "baseCycles": "1000",
            "reserveFactor": "1.5",
            "nominalRpm": "1500",
            "accelerationDurationS": "5",
            "decelerationDurationS": "5",
        },
        "methodicalRequirements": {
            "requiredCyclesExact": "1500",
            "requiredSteadyDurationSExact": "60",
        },
        "executionTargets": {
            "targetCycles": "1500",
            "targetSteadyDurationS": "60",
            "totalDurationS": "70",
            "roundingPolicy": "🔧" * 101,
        },
    }
    assert RbdPlanSourceResult.model_validate(response).producerName == "🔧" * 101
    with pytest.raises(ValidationError):
        RbdPlanSourceResult.model_validate({**response, "producerName": "x" * 201})
    with pytest.raises(ValidationError):
        RbdPlanSourceResult.model_validate({**response, "producerName": "\ud800"})
