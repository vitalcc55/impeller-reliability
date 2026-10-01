from __future__ import annotations

import hashlib
import json
from zipfile import ZipFile

from pydantic import TypeAdapter

from support.r130run_builder import M9A_BASE_PACKAGE, RUN_ID, JsonValue


def protocol_payloads() -> dict[str, bytes]:
    # Transport characterization only; these bytes do not prove PDF rendering.
    pdf = b"%PDF-1.4\nsynthetic transport characterization\n%%EOF\n"
    release: dict[str, JsonValue] = {
        "schema_version": "r130sh.protocol-release.v1",
        "run_id": RUN_ID,
        "release_id": 7,
        "revision_number": 2,
        "protocol_number": "SYNTHETIC-7",
        "template_version": "synthetic-template",
        "content_sha256": hashlib.sha256(pdf).hexdigest(),
        "released_at_utc": "2026-09-30T09:00:00.000Z",
        "released_by_actor": {
            "employee_id": "synthetic-employee",
            "full_name": "Synthetic employee",
            "position": "Tester",
            "legacy": False,
        },
        "photo_ids": [],
    }
    return {"protocol/protocol.pdf": pdf, "protocol/release.json": json.dumps(release).encode("utf-8")}


def inspection_record() -> dict[str, JsonValue]:
    with ZipFile(M9A_BASE_PACKAGE) as archive:
        envelope = TypeAdapter(dict[str, JsonValue]).validate_json(archive.read("inspections.json"))
    records = envelope["inspections"]
    assert isinstance(records, list)
    return next(record for record in records if isinstance(record, dict) and record["stage"] == "pre_test")


def inspection_payload(record: dict[str, JsonValue]) -> bytes:
    return json.dumps({"schema_version": "r130sh.inspections.v1", "run_id": RUN_ID, "inspections": [record]}).encode()


def photo_record(*, media_type: str = "image/png") -> tuple[dict[str, JsonValue], bytes]:
    # Transport tests use signature bytes; displaying real images is a separate gate.
    content = b"\x89PNG\r\n\x1a\ntransport" if media_type == "image/png" else b"\xff\xd8\xfftransport\xff\xd9"
    identity = "0de43d70-06a5-4a35-89c4-b21834f1dbd4"
    suffix = ".png" if media_type == "image/png" else ".jpg"
    return {
        "attachment_id": identity,
        "run_id": RUN_ID,
        "inspection_id": None,
        "path": f"attachments/photos/{identity}{suffix}",
        "media_type": media_type,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "width_px": 1,
        "height_px": 1,
        "actor": {"employee_id": "employee", "full_name": "Test", "position": "Tester", "legacy": False},
        "attached_at_utc": "2026-09-30T09:00:00.123456Z",
        "availability": "available",
    }, content


def photo_payloads(record: dict[str, JsonValue], content: bytes) -> dict[str, bytes]:
    payloads = {"attachments/index.json": json.dumps({"schema_version": "r130sh.attachments.v1", "run_id": record["run_id"], "attachments": [record]}).encode()}
    path = record["path"]
    if isinstance(path, str):
        payloads[path] = content
    return payloads
