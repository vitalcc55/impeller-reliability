from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from time import monotonic
from zipfile import ZipFile

from pydantic import TypeAdapter
import pytest

from impeller_reliability.integration.r130run.m9a import read_m9a_package_facts
from impeller_reliability.integration.r130run.validator import RunPackageValidator, ValidationControl, parse_material_json, parse_material_manifest, validate_material_metadata
from support.r130run_builder import M9A_BASE_PACKAGE, RUN_ID, JsonValue, build_synthetic_r130run
from support.r130run_materials import inspection_payload, inspection_record, photo_payloads, photo_record, protocol_payloads


def test_optional_protocol_pair_is_not_required(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "without-protocol.r130run")
    report = RunPackageValidator().validate(package, _control())
    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "passed"


def test_matching_protocol_pair_passes_structural_validation(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "with-protocol.r130run", payload_overrides=protocol_payloads())
    report = RunPackageValidator().validate(package, _control())
    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "passed"
    inventory = read_m9a_package_facts(package, report).inventory
    release = next(item for item in inventory if item.path == "protocol/release.json")
    assert release.media_type == "application/json"
    assert release.semantic_coverage == "covered"
    assert next(item for item in inventory if item.path == "protocol/protocol.pdf").media_type == "application/pdf"


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [("run_id", "foreign-run"), ("content_sha256", "0" * 64)],
)
def test_resealed_protocol_identity_mismatch_is_rejected(tmp_path: Path, field: str, invalid_value: str) -> None:
    payloads = protocol_payloads()
    release = TypeAdapter(dict[str, JsonValue]).validate_json(payloads["protocol/release.json"])
    release[field] = invalid_value
    payloads["protocol/release.json"] = json.dumps(release).encode("utf-8")
    package = build_synthetic_r130run(tmp_path / "resealed-protocol.r130run", payload_overrides=payloads)
    report = RunPackageValidator().validate(package, _control())
    # Archive integrity passes even though the release relation is false.
    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "failed"
    assert any(item.location.startswith("protocol/release.json") for item in report.findings if item.severity == "error")


def _control() -> ValidationControl:
    return ValidationControl(Event(), monotonic() + 30, lambda _phase, _completed, _total, _entries, _entry_total: None)


@pytest.mark.parametrize("location", ["attachments/index.json", "protocol/release.json"])
def test_material_subset_preserves_existing_eligibility_claim_rejection(tmp_path: Path, location: str) -> None:
    payloads = protocol_payloads()
    if location == "attachments/index.json":
        payloads[location] = json.dumps({"schema_version": "r130sh.attachments.v1", "run_id": RUN_ID, "attachments": [], "calculation_eligible": True}).encode()
    else:
        release = TypeAdapter(dict[str, JsonValue]).validate_json(payloads[location])
        release["calculation_eligible"] = True
        payloads[location] = json.dumps(release).encode()
    package = build_synthetic_r130run(tmp_path / "eligibility-claim.r130run", payload_overrides=payloads)
    report = RunPackageValidator().validate(package, _control())
    assert any(finding.code == "authoritative_eligibility_forbidden" for finding in report.findings)
    with ZipFile(package) as archive:
        control = _control()
        manifest = parse_material_manifest(archive.read("manifest.json"), control)
        metadata = {path: parse_material_json(archive.read(path), path, control) for path in ("inspections.json", "attachments/index.json", "protocol/release.json")}
    current = validate_material_metadata(manifest, metadata, control)
    assert current.semanticVerdict == "failed"
    assert [finding.code for finding in current.findings] == ["authoritative_eligibility_forbidden"]


@pytest.mark.parametrize("missing_member", ["protocol/release.json", "protocol/protocol.pdf"])
def test_protocol_members_must_be_a_pair(tmp_path: Path, missing_member: str) -> None:
    payloads = protocol_payloads()
    del payloads[missing_member]
    package = build_synthetic_r130run(tmp_path / "orphan.r130run", payload_overrides=payloads)
    report = RunPackageValidator().validate(package, _control())
    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "failed"
    assert "protocol_pair_incomplete" in {item.code for item in report.findings}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "foreign-schema"),
        ("release_id", True),
        ("release_id", 0),
        ("release_id", "7"),
        ("revision_number", -1),
        ("revision_number", False),
        ("protocol_number", " "),
        ("template_version", None),
        ("released_at_utc", "2026-09-30T09:00:00.000000Z"),
        ("released_at_utc", "2026-09-30T09:00:00+00:00"),
        ("released_at_utc", "2026-02-30T09:00:00.000Z"),
        ("released_by_actor", []),
        ("photo_ids", "photo"),
        ("photo_ids", [False]),
        ("photo_ids", [""]),
    ],
)
def test_protocol_field_violations_are_semantic_findings(tmp_path: Path, field: str, value: JsonValue) -> None:
    payloads = protocol_payloads()
    release = TypeAdapter(dict[str, JsonValue]).validate_json(payloads["protocol/release.json"])
    release[field] = value
    payloads["protocol/release.json"] = json.dumps(release).encode()
    package = build_synthetic_r130run(tmp_path / "invalid-release.r130run", payload_overrides=payloads)
    report = RunPackageValidator().validate(package, _control())
    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "failed"


def test_protocol_preserves_large_id_and_source_actor_object_contract(tmp_path: Path) -> None:
    payloads = protocol_payloads()
    release = TypeAdapter(dict[str, JsonValue]).validate_json(payloads["protocol/release.json"])
    release.update(release_id=2**80, revision_number=2**72, released_by_actor={}, released_at_utc="2026-09-30T09:00:00Z", photo_ids=["missing-photo"])
    payloads["protocol/release.json"] = json.dumps(release).encode()
    package = build_synthetic_r130run(tmp_path / "large-id.r130run", payload_overrides=payloads)
    report = RunPackageValidator().validate(package, _control())
    assert report.semanticVerdict == "passed"
    assert report.findingCounts.error == 0
    assert [item.code for item in report.findings] == ["material_reference_unresolved"]
    assert next(item for item in report.semanticCoverage if item.area == "protocol_release").status == "covered"
    assert next(item for item in read_m9a_package_facts(package, report).inventory if item.path == "protocol/protocol.pdf").semantic_coverage == "structural_only"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stage", "unknown"),
        ("trip_index", 1),
        ("trip_index", True),
        ("run_elapsed_s", "1"),
        ("run_elapsed_s", "NaN"),
        ("run_elapsed_s", "not-a-decimal"),
        ("run_elapsed_s", ""),
        ("run_elapsed_s", "0.0"),
        ("run_elapsed_s", "-1"),
        ("inspection_outcome", "blocking_damage"),
        ("attachment_ids", ["duplicate", "duplicate"]),
        ("performed_at_utc", "2026-09-30T09:00:00+03:00"),
        ("performed_at_utc", "2026-09-30T09:00:00Z"),
        ("performed_at_utc", "2026-09-30T09:00:00.123+00:00"),
        ("actor", {"employee_id": "", "full_name": "Test", "position": "Tester"}),
        ("actor", {"employee_id": "employee", "full_name": "Test", "position": "Tester", "legacy": False}),
        ("comment", " noncanonical "),
    ],
)
def test_inspection_invariants_are_validated(tmp_path: Path, field: str, value: JsonValue) -> None:
    record = inspection_record()
    record[field] = value
    package = build_synthetic_r130run(tmp_path / "invalid-inspection.r130run", payload_overrides={"inspections.json": inspection_payload(record)})
    report = RunPackageValidator().validate(package, _control())
    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "failed"


@pytest.mark.parametrize("stage", ["pre_test", "post_trial_run", "vibration_pause", "post_rbd", "post_rpt", "post_pmn"])
@pytest.mark.parametrize(
    ("state", "damage", "outcome"), [("intact", False, "clear"), ("not_assessed", False, "inconclusive"), ("damaged", False, "blocking_damage"), ("intact", True, "blocking_damage")]
)
def test_inspection_stage_and_source_outcome_are_supported(tmp_path: Path, stage: str, state: str, damage: bool, outcome: str) -> None:
    record = inspection_record()
    record.update(stage=stage, trip_index=1 if stage == "vibration_pause" else None, performed_at_utc="2026-09-30T09:00:00.123456+00:00", inspection_outcome=outcome)
    detail = record["findings"]
    assert isinstance(detail, dict)
    detail.update(balancing_elements_state=state, cracks=damage)
    package = build_synthetic_r130run(tmp_path / "source-outcome.r130run", payload_overrides={"inspections.json": inspection_payload(record)})
    report = RunPackageValidator().validate(package, _control())
    assert report.semanticVerdict == "passed"


@pytest.mark.parametrize("media_type", ["image/png", "image/jpeg"])
@pytest.mark.parametrize("linked", [False, True])
def test_run_wide_and_inspection_linked_photos_do_not_require_reverse_lists(tmp_path: Path, media_type: str, linked: bool) -> None:
    record, content = photo_record(media_type=media_type)
    inspection = inspection_record()
    record["inspection_id"] = inspection["inspection_id"] if linked else None
    package = build_synthetic_r130run(tmp_path / "photo.r130run", payload_overrides=photo_payloads(record, content))
    report = RunPackageValidator().validate(package, _control())
    assert report.semanticVerdict == "passed"
    assert report.findingCounts.warning == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attachment_id", "not-a-uuid"),
        ("run_id", "foreign"),
        ("inspection_id", ""),
        ("media_type", "text/html"),
        ("size", True),
        ("size", 0),
        ("size", 25 * 1024 * 1024 + 1),
        ("sha256", "0" * 64),
        ("width_px", False),
        ("height_px", 0),
        ("attached_at_utc", "2026-09-30T09:00:00+03:00"),
        ("availability", "unknown"),
        ("actor", {"employee_id": None, "full_name": "Test", "position": "Tester", "legacy": True}),
    ],
)
def test_photo_fields_and_inventory_are_validated(tmp_path: Path, field: str, value: JsonValue) -> None:
    record, content = photo_record()
    record[field] = value
    package = build_synthetic_r130run(tmp_path / "invalid-photo.r130run", payload_overrides=photo_payloads(record, content))
    report = RunPackageValidator().validate(package, _control())
    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "failed"


def test_unavailable_photo_is_allowed_only_in_diagnostic_partial(tmp_path: Path) -> None:
    record, content = photo_record()
    record.update(path=None, availability="unavailable", unavailable_reason="Synthetic missing file")
    package = build_synthetic_r130run(tmp_path / "final.r130run", payload_overrides=photo_payloads(record, content))
    report = RunPackageValidator().validate(package, _control())
    assert "photo_unavailable_in_final" in {finding.code for finding in report.findings}
    base = M9A_BASE_PACKAGE.parent / "diagnostic_partial.r130run"
    with ZipFile(base) as archive:
        manifest = TypeAdapter(dict[str, JsonValue]).validate_json(archive.read("manifest.json"))
    record["run_id"] = manifest["run_id"]
    payloads = photo_payloads(record, content)
    package = build_synthetic_r130run(tmp_path / "partial.r130run", base_package=base, payload_overrides=payloads, manifest_mutator=lambda value: value.update(package_kind="diagnostic_partial"))
    report = RunPackageValidator().validate(package, _control())
    assert report.semanticVerdict == "passed"


def test_duplicate_photo_ids_and_missing_inspection_are_explicit_warnings(tmp_path: Path) -> None:
    record, content = photo_record()
    record["inspection_id"] = "missing-inspection"
    payloads = photo_payloads(record, content)
    payloads["attachments/index.json"] = json.dumps({"schema_version": "r130sh.attachments.v1", "run_id": RUN_ID, "attachments": [record, record]}).encode()
    package = build_synthetic_r130run(tmp_path / "ambiguous-photo.r130run", payload_overrides=payloads)
    report = RunPackageValidator().validate(package, _control())
    assert report.semanticVerdict == "passed"
    assert {item.code for item in report.findings} == {"material_id_ambiguous", "material_reference_unresolved"}


@pytest.mark.parametrize("trip_index", [None, 0, -1, False])
def test_vibration_pause_requires_positive_trip_index(tmp_path: Path, trip_index: JsonValue) -> None:
    record = inspection_record()
    record.update(stage="vibration_pause", trip_index=trip_index)
    package = build_synthetic_r130run(tmp_path / "invalid-trip.r130run", payload_overrides={"inspections.json": inspection_payload(record)})
    report = RunPackageValidator().validate(package, _control())
    assert report.semanticVerdict == "failed"


MALFORMED_MATERIAL_CASES: list[tuple[str, JsonValue]] = [
    ("protocol/release.json", []),
    ("attachments/index.json", {"schema_version": "r130sh.attachments.v1", "run_id": RUN_ID, "attachments": {}}),
    ("inspections.json", {"schema_version": "r130sh.inspections.v1", "run_id": RUN_ID, "inspections": [None, [], True, {}]}),
]


@pytest.mark.parametrize(("path", "payload"), MALFORMED_MATERIAL_CASES)
def test_malformed_material_values_produce_reports(tmp_path: Path, path: str, payload: JsonValue) -> None:
    payloads = protocol_payloads()
    payloads[path] = json.dumps(payload).encode()
    package = build_synthetic_r130run(tmp_path / "malformed.r130run", payload_overrides=payloads)
    report = RunPackageValidator().validate(package, _control())
    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "failed"


def test_unindexed_photo_is_not_silently_accepted(tmp_path: Path) -> None:
    record, content = photo_record()
    path = record["path"]
    assert isinstance(path, str)
    package = build_synthetic_r130run(tmp_path / "unindexed.r130run", payload_overrides={path: content})
    report = RunPackageValidator().validate(package, _control())
    assert report.semanticVerdict == "failed"
    assert "photo_inventory_unindexed" in {finding.code for finding in report.findings}


def test_photo_availability_cannot_be_inferred_from_matching_bytes(tmp_path: Path) -> None:
    record, content = photo_record()
    del record["availability"]
    package = build_synthetic_r130run(tmp_path / "missing-availability.r130run", payload_overrides=photo_payloads(record, content))
    report = RunPackageValidator().validate(package, _control())
    assert report.semanticVerdict == "failed"
    assert "photo_fields_invalid" in {finding.code for finding in report.findings}


def test_inspection_dangling_attachment_reference_is_a_warning(tmp_path: Path) -> None:
    record = inspection_record()
    record["attachment_ids"] = ["missing-photo"]
    package = build_synthetic_r130run(tmp_path / "dangling-inspection-photo.r130run", payload_overrides={"inspections.json": inspection_payload(record)})
    report = RunPackageValidator().validate(package, _control())
    assert report.semanticVerdict == "passed"
    assert [finding.code for finding in report.findings] == ["material_reference_unresolved"]


@pytest.mark.parametrize("field", ["cracks", "chips", "deformation", "partial_destruction", "total_destruction"])
def test_each_source_damage_flag_requires_blocking_outcome(tmp_path: Path, field: str) -> None:
    record = inspection_record()
    damage = record["findings"]
    assert isinstance(damage, dict)
    damage[field] = True
    record["inspection_outcome"] = "blocking_damage"
    package = build_synthetic_r130run(tmp_path / "source-damage.r130run", payload_overrides={"inspections.json": inspection_payload(record)})
    assert RunPackageValidator().validate(package, _control()).semanticVerdict == "passed"


@pytest.mark.parametrize("member", ["protocol/release.json", "protocol/protocol.pdf"])
def test_protocol_media_type_must_match_the_selected_member(tmp_path: Path, member: str) -> None:
    def change_media(manifest: dict[str, JsonValue]) -> None:
        files = manifest["files"]
        assert isinstance(files, list)
        for item in files:
            if isinstance(item, dict) and item["path"] == member:
                item["media_type"] = "text/html"

    package = build_synthetic_r130run(tmp_path / "wrong-protocol-type.r130run", payload_overrides=protocol_payloads(), manifest_mutator=change_media)
    report = RunPackageValidator().validate(package, _control())
    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "failed"


@pytest.mark.parametrize(("path", "schema", "field"), [("inspections.json", "r130sh.inspections.v1", "inspections"), ("attachments/index.json", "r130sh.attachments.v1", "attachments")])
@pytest.mark.parametrize("value", [None, "not-a-list", False])
def test_malformed_material_envelope_has_one_finding(tmp_path: Path, path: str, schema: str, field: str, value: JsonValue) -> None:
    payload: dict[str, JsonValue] = {"schema_version": schema, "run_id": RUN_ID, field: value}
    package = build_synthetic_r130run(tmp_path / "malformed-envelope.r130run", payload_overrides={path: json.dumps(payload).encode()})
    report = RunPackageValidator().validate(package, _control())
    assert report.semanticVerdict == "failed"
    assert report.findingCounts.error == 1
    assert [(finding.code, finding.location) for finding in report.findings] == [("semantic_shape_mismatch", path)]
