from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import Event
from time import monotonic

from pydantic import TypeAdapter
import pytest

from impeller_reliability.integration.r130run.m9a import read_m9a_package_facts
from impeller_reliability.integration.r130run.validator import RunPackageValidator, ValidationControl
from support.r130run_builder import RUN_ID, JsonValue, build_synthetic_r130run


def _protocol_payloads() -> dict[str, bytes]:
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


def test_optional_protocol_pair_is_not_required(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "without-protocol.r130run")
    report = RunPackageValidator().validate(package, _control())
    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "passed"


def test_matching_protocol_pair_passes_structural_validation(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "with-protocol.r130run", payload_overrides=_protocol_payloads())
    report = RunPackageValidator().validate(package, _control())
    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "passed"
    inventory = read_m9a_package_facts(package, report).inventory
    release = next(item for item in inventory if item.path == "protocol/release.json")
    assert release.media_type == "application/json"
    assert release.semantic_coverage == "structural_only"
    assert next(item for item in inventory if item.path == "protocol/protocol.pdf").media_type == "application/pdf"


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [("run_id", "foreign-run"), ("content_sha256", "0" * 64)],
)
def test_resealed_protocol_identity_gap_is_observable(tmp_path: Path, field: str, invalid_value: str) -> None:
    payloads = _protocol_payloads()
    release = TypeAdapter(dict[str, JsonValue]).validate_json(payloads["protocol/release.json"])
    release[field] = invalid_value
    payloads["protocol/release.json"] = json.dumps(release).encode("utf-8")
    package = build_synthetic_r130run(tmp_path / "resealed-protocol.r130run", payload_overrides=payloads)
    report = RunPackageValidator().validate(package, _control())
    # Archive integrity passes even though the release relation is false.
    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "passed"
    assert report.findingCounts.error == 0
    assert "protocol_release" not in {item.area for item in report.semanticCoverage}


def _control() -> ValidationControl:
    return ValidationControl(Event(), monotonic() + 30, lambda _phase, _completed, _total, _entries, _entry_total: None)
