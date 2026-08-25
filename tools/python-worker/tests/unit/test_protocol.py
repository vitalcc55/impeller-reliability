import json

from pydantic import ValidationError
import pytest

from impeller_reliability.protocol.envelopes import RequestEnvelope


def test_request_rejects_unknown_operation() -> None:
    with pytest.raises(ValidationError):
        RequestEnvelope.model_validate(
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


def test_request_is_json_serializable() -> None:
    request = RequestEnvelope(
        protocolVersion=1,
        requestId="request-1",
        kind="request",
        operation="system.ping",
        revision=0,
        deadlineMs=1_000,
        payload={},
    )
    assert json.loads(request.model_dump_json())["operation"] == "system.ping"
