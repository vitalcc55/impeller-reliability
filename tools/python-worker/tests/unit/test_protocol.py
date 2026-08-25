import json

from pydantic import ValidationError
import pytest

from impeller_reliability.protocol.envelopes import REQUEST_ENVELOPE_ADAPTER, EmptyPayload, PingRequest


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
