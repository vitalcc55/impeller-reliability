from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import struct
from threading import Event
from time import monotonic
from zipfile import ZIP_BZIP2, ZipFile
import zlib

from pydantic import TypeAdapter
import pytest

from impeller_reliability.integration.r130run.models import JobPhase
from impeller_reliability.integration.r130run.validator import (
    ProgressCallback,
    RunPackageValidator,
    SourceChangedError,
    ValidationCancelledError,
    ValidationControl,
    ValidationTimeoutError,
)
from support.r130run_builder import JsonValue, build_synthetic_r130run, write_r130run


def test_validates_downstream_synthetic_package_without_extraction(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "synthetic.r130run")

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "partial"
    assert report.packageId == "019d3c80-3d21-7a65-8e5a-111111111111"
    assert report.runId == "019d3c80-3d21-7a65-8e5a-222222222222"
    assert report.outerPackageSha256 == hashlib.sha256(package.read_bytes()).hexdigest()
    assert report.findingCounts.error == 0
    assert {item.status for item in report.semanticCoverage} >= {"covered", "not_available", "contract_gap"}
    assert list(tmp_path.iterdir()) == [package]


def test_reports_manifest_hash_mismatch_as_completed_validation(tmp_path: Path) -> None:
    def change_hash(manifest: dict[str, JsonValue]) -> None:
        files = manifest["files"]
        assert isinstance(files, list)
        first = files[0]
        assert isinstance(first, dict)
        first["sha256"] = "0" * 64

    package = build_synthetic_r130run(tmp_path / "checksum-mismatch.r130run", manifest_mutator=change_hash)

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "failed"
    assert report.semanticVerdict == "not_available"
    assert [item.code for item in report.findings] == ["entry_hash_mismatch"]


def test_reports_manifest_size_mismatch(tmp_path: Path) -> None:
    def change_size(manifest: dict[str, JsonValue]) -> None:
        files = manifest["files"]
        assert isinstance(files, list)
        first = files[0]
        assert isinstance(first, dict)
        size = first["size"]
        assert isinstance(size, int)
        first["size"] = size + 1

    package = build_synthetic_r130run(tmp_path / "size-mismatch.r130run", manifest_mutator=change_size)

    report = RunPackageValidator().validate(package, _control())

    assert report.findings[0].code == "entry_size_mismatch"


@pytest.mark.parametrize(
    ("unsafe_name", "expected_code"),
    [
        ("../escape.json", "path_traversal"),
        ("/absolute.json", "path_noncanonical"),
        ("folder\\entry.json", "path_noncanonical"),
    ],
)
def test_rejects_unsafe_package_paths(tmp_path: Path, unsafe_name: str, expected_code: str) -> None:
    package = build_synthetic_r130run(
        tmp_path / "unsafe.r130run",
        extra_entries=[(unsafe_name, b"{}")],
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "failed"
    assert report.findings[0].code == expected_code
    assert ".." not in report.findings[0].location
    assert "\\" not in report.findings[0].location


@pytest.mark.parametrize("unsafe_name", ["./entry.json", "folder//entry.json", ".", "\ufeffentry.json", "e\u0301.json"])
def test_rejects_lexical_path_aliases(tmp_path: Path, unsafe_name: str) -> None:
    package = build_synthetic_r130run(
        tmp_path / "lexical-alias.r130run",
        extra_entries=[(unsafe_name, b"{}")],
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "failed"
    assert report.findings[0].code in {"path_noncanonical", "path_traversal"}


@pytest.mark.parametrize("unsafe_name", ["con", "folder/a:b.json", "folder/name. ", "folder/nul.txt"])
def test_rejects_windows_unsafe_path_segments(tmp_path: Path, unsafe_name: str) -> None:
    package = build_synthetic_r130run(
        tmp_path / "windows-path.r130run",
        extra_entries=[(unsafe_name, b"{}")],
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.findings[0].code == "path_windows_unsafe"
    assert report.findings[0].location == "archive.entry_name"


def test_rejects_case_insensitive_name_collisions(tmp_path: Path) -> None:
    package = build_synthetic_r130run(
        tmp_path / "collision.r130run",
        extra_entries=[("Extra.json", b"{}"), ("extra.json", b"{}")],
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "failed"
    assert report.findings[0].code == "path_case_collision"


def test_rejects_unsupported_compression(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "bzip2.r130run", compression=ZIP_BZIP2)

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "failed"
    assert report.findings[0].code == "unsupported_compression"


def test_accepts_source_uuid_v4_without_weakening_local_entity_ids(tmp_path: Path) -> None:
    def use_uuid_v4(manifest: dict[str, JsonValue]) -> None:
        manifest["package_id"] = "8ab377f2-cfd8-4983-86ea-25f5d0171bd7"

    package = build_synthetic_r130run(tmp_path / "uuid4.r130run", manifest_mutator=use_uuid_v4)

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "passed"
    assert report.packageId == "8ab377f2-cfd8-4983-86ea-25f5d0171bd7"


def test_cancellation_interrupts_streaming_validation(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "cancel.r130run")
    cancel = Event()
    cancel.set()

    with pytest.raises(ValidationCancelledError):
        RunPackageValidator().validate(package, _control(cancel))


def test_source_gate_rejects_empty_file_and_directory(tmp_path: Path) -> None:
    empty = tmp_path / "empty.r130run"
    empty.write_bytes(b"")
    with pytest.raises(OSError, match="run_package_source_size"):
        RunPackageValidator().validate(empty, _control())
    with pytest.raises(OSError, match="run_package_source_not_regular"):
        RunPackageValidator().validate(tmp_path, _control())


def test_detects_source_change_during_outer_hash(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "source-change.r130run")
    changed = False

    def progress(phase: str, completed: int, _total: int, _entries: int, _entry_total: int) -> None:
        nonlocal changed
        if phase == "outer_hash" and completed > 0 and not changed:
            with package.open("ab") as stream:
                stream.write(b"changed")
            changed = True

    with pytest.raises(SourceChangedError):
        RunPackageValidator().validate(package, _control(progress=progress))


def test_rejects_duplicate_json_keys_in_manifest(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "base.r130run")
    with ZipFile(package, mode="r") as archive:
        entries = [(info.filename, archive.read(info)) for info in archive.infolist()]
    duplicate_manifest = b'{"schema_version":"r130sh.run-package.v1","schema_version":"duplicate"}'
    changed_entries = [(path, duplicate_manifest if path == "manifest.json" else content) for path, content in entries]
    changed = tmp_path / "duplicate-key.r130run"
    write_r130run(changed, changed_entries)

    report = RunPackageValidator().validate(changed, _control())

    assert report.structuralVerdict == "failed"
    assert report.findings[0].code == "json_invalid"


def test_reports_missing_and_duplicate_manifest(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "base.r130run")
    entries = _entries(package)
    missing = tmp_path / "missing-manifest.r130run"
    write_r130run(missing, [(path, content) for path, content in entries if path != "manifest.json"])
    missing_report = RunPackageValidator().validate(missing, _control())
    assert missing_report.findings[0].code == "manifest_missing"

    duplicate = tmp_path / "duplicate-manifest.r130run"
    manifest = next(content for path, content in entries if path == "manifest.json")
    with pytest.warns(UserWarning, match="Duplicate name"):
        write_r130run(duplicate, [*entries, ("manifest.json", manifest)])
    duplicate_report = RunPackageValidator().validate(duplicate, _control())
    assert duplicate_report.findings[0].code == "path_duplicate"


def test_rejects_encrypted_and_symlink_entry_metadata(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "base.r130run")
    encrypted = tmp_path / "encrypted.r130run"
    encrypted_bytes = bytearray(package.read_bytes())
    local = encrypted_bytes.find(b"PK\x03\x04")
    central = encrypted_bytes.find(b"PK\x01\x02")
    assert local >= 0 and central >= 0
    struct.pack_into("<H", encrypted_bytes, local + 6, struct.unpack_from("<H", encrypted_bytes, local + 6)[0] | 1)
    struct.pack_into("<H", encrypted_bytes, central + 8, struct.unpack_from("<H", encrypted_bytes, central + 8)[0] | 1)
    encrypted.write_bytes(encrypted_bytes)
    encrypted_report = RunPackageValidator().validate(encrypted, _control())
    assert encrypted_report.findings[0].code == "entry_encrypted"

    symlink = tmp_path / "symlink.r130run"
    symlink_bytes = bytearray(package.read_bytes())
    central = symlink_bytes.find(b"PK\x01\x02")
    assert central >= 0
    struct.pack_into("<L", symlink_bytes, central + 38, (stat.S_IFLNK | 0o777) << 16)
    symlink.write_bytes(symlink_bytes)
    symlink_report = RunPackageValidator().validate(symlink, _control())
    assert symlink_report.findings[0].code == "entry_symlink"


def test_rejects_unaccounted_zip_bytes_and_invalid_data_descriptor(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "base.r130run")
    original = bytearray(package.read_bytes())
    eocd = original.rfind(b"PK\x05\x06")
    assert eocd >= 0
    central_offset = struct.unpack_from("<L", original, eocd + 16)[0]
    with_gap = original[:central_offset] + b"gap" + original[central_offset:]
    struct.pack_into("<L", with_gap, eocd + 3 + 16, central_offset + 3)
    gap_package = tmp_path / "gap.r130run"
    gap_package.write_bytes(with_gap)

    gap_report = RunPackageValidator().validate(gap_package, _control())

    assert gap_report.findings[0].code == "archive_unaccounted_data"

    descriptor_bytes = bytearray(package.read_bytes())
    local = descriptor_bytes.find(b"PK\x03\x04")
    central = descriptor_bytes.find(b"PK\x01\x02")
    assert local >= 0 and central >= 0
    struct.pack_into("<H", descriptor_bytes, local + 6, struct.unpack_from("<H", descriptor_bytes, local + 6)[0] | 8)
    struct.pack_into("<H", descriptor_bytes, central + 8, struct.unpack_from("<H", descriptor_bytes, central + 8)[0] | 8)
    descriptor_package = tmp_path / "descriptor.r130run"
    descriptor_package.write_bytes(descriptor_bytes)

    descriptor_report = RunPackageValidator().validate(descriptor_package, _control())

    assert descriptor_report.findings[0].code == "data_descriptor_invalid"


def test_measures_actual_entry_output_independently_from_zip_index(tmp_path: Path) -> None:
    payload = b"measurement_id,run_id\nfirst,run\nsecond,run\n"
    prefix = b"measurement_id,run_id\nfirst,run\n"

    def declare_prefix(manifest: dict[str, JsonValue]) -> None:
        files = manifest["files"]
        assert isinstance(files, list)
        measurement = next(item for item in files if isinstance(item, dict) and item.get("path") == "measurements.csv")
        assert isinstance(measurement, dict)
        measurement["size"] = len(prefix)
        measurement["sha256"] = hashlib.sha256(prefix).hexdigest()

    package = build_synthetic_r130run(
        tmp_path / "underdeclared.r130run",
        payload_overrides={"measurements.csv": payload},
        manifest_mutator=declare_prefix,
        compression=0,
    )
    with ZipFile(package) as archive:
        info = archive.getinfo("measurements.csv")
    content = bytearray(package.read_bytes())
    name = b"measurements.csv"
    central_name = content.rfind(name)
    central = central_name - 46
    assert central >= 0
    prefix_crc = zlib.crc32(prefix) & 0xFFFFFFFF
    struct.pack_into("<L", content, info.header_offset + 14, prefix_crc)
    struct.pack_into("<L", content, info.header_offset + 22, len(prefix))
    struct.pack_into("<L", content, central + 16, prefix_crc)
    struct.pack_into("<L", content, central + 24, len(prefix))
    package.write_bytes(content)

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "failed"
    assert report.findings[0].code == "entry_size_or_crc_mismatch"


def test_invalid_utf8_central_name_returns_bounded_structural_report(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "invalid-name.r130run")
    content = bytearray(package.read_bytes())
    central = content.find(b"PK\x01\x02")
    assert central >= 0
    flags = struct.unpack_from("<H", content, central + 8)[0] | 0x0800
    struct.pack_into("<H", content, central + 8, flags)
    name_length = struct.unpack_from("<H", content, central + 28)[0]
    assert name_length > 0
    content[central + 46] = 0xFF
    package.write_bytes(content)

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "failed"
    assert report.findings[0].code == "archive_not_zip"


def test_rejects_dos_directory_attribute_and_malformed_zip64_locator(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "base.r130run")
    directory_bytes = bytearray(package.read_bytes())
    central = directory_bytes.find(b"PK\x01\x02")
    assert central >= 0
    attributes = struct.unpack_from("<L", directory_bytes, central + 38)[0]
    struct.pack_into("<L", directory_bytes, central + 38, attributes | 0x10)
    directory_package = tmp_path / "directory-attribute.r130run"
    directory_package.write_bytes(directory_bytes)
    directory_report = RunPackageValidator().validate(directory_package, _control())
    assert directory_report.findings[0].code == "directory_entry"

    zip64_bytes = bytearray(package.read_bytes())
    eocd = zip64_bytes.rfind(b"PK\x05\x06")
    assert eocd >= 0
    struct.pack_into("<H", zip64_bytes, eocd + 10, 0xFFFF)
    zip64_package = tmp_path / "invalid-zip64.r130run"
    zip64_package.write_bytes(zip64_bytes)
    zip64_report = RunPackageValidator().validate(zip64_package, _control())
    assert zip64_report.structuralVerdict == "failed"
    assert zip64_report.findings[0].code in {"archive_multidisk", "archive_zip64_invalid"}


@pytest.mark.parametrize(
    ("path", "content", "expected_code"),
    [
        ("events.jsonl", b"{broken}\n", "json_invalid"),
        ("measurements.csv", b"measurement_id,run_id\n\xff,broken\n", "csv_invalid"),
        ("run-summary.json", b'{"value":NaN}\n', "json_invalid"),
    ],
)
def test_rejects_malformed_payload_syntax(
    tmp_path: Path,
    path: str,
    content: bytes,
    expected_code: str,
) -> None:
    package = build_synthetic_r130run(
        tmp_path / "malformed.r130run",
        payload_overrides={path: content},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "failed"
    assert report.findings[0].code == expected_code


def test_rejects_overlong_jsonl_line_before_retaining_records(tmp_path: Path) -> None:
    package = build_synthetic_r130run(
        tmp_path / "long-jsonl.r130run",
        payload_overrides={"events.jsonl": b"{" + b"a" * (1024 * 1024) + b"}"},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "failed"
    assert report.findings[0].code == "jsonl_line_too_large"


def test_jsonl_blank_line_and_row_count_mismatch_are_rejected(tmp_path: Path) -> None:
    blank = build_synthetic_r130run(
        tmp_path / "blank-jsonl.r130run",
        payload_overrides={"events.jsonl": b"\n"},
    )
    blank_report = RunPackageValidator().validate(blank, _control())
    assert blank_report.findings[0].code == "jsonl_blank_line"

    def wrong_count(manifest: dict[str, JsonValue]) -> None:
        files = manifest["files"]
        assert isinstance(files, list)
        event = next(item for item in files if isinstance(item, dict) and item.get("path") == "events.jsonl")
        assert isinstance(event, dict)
        event["row_count"] = 2

    mismatch = build_synthetic_r130run(
        tmp_path / "row-count.r130run",
        manifest_mutator=wrong_count,
    )
    mismatch_report = RunPackageValidator().validate(mismatch, _control())
    assert mismatch_report.findings[0].code == "row_count_mismatch"


def test_jsonl_final_record_without_newline_is_validated(tmp_path: Path) -> None:
    event = _fixture_object("event.example.json")
    package = build_synthetic_r130run(
        tmp_path / "final-record.r130run",
        payload_overrides={"events.jsonl": json.dumps(event, ensure_ascii=False).encode("utf-8")},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "passed"
    assert report.findingCounts.error == 0


def test_rejects_csv_field_above_published_bound(tmp_path: Path) -> None:
    package = build_synthetic_r130run(
        tmp_path / "oversized-field.r130run",
        payload_overrides={"measurements.csv": b"measurement_id,run_id\n" + b"a" * (1024 * 1024 + 1) + b",run\n"},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.findings[0].code == "csv_invalid"


def test_rejects_json_above_depth_profile(tmp_path: Path) -> None:
    nested = b"[" * 70 + b"0" + b"]" * 70
    package = build_synthetic_r130run(
        tmp_path / "deep-json.r130run",
        payload_overrides={"run-summary.json": nested},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.findings[0].code == "json_limit"


def test_rejects_invalid_source_identity_and_expired_budget(tmp_path: Path) -> None:
    def invalid_package_id(manifest: dict[str, JsonValue]) -> None:
        manifest["package_id"] = "5ab377f2-cfd8-1983-86ea-25f5d0171bd7"

    package = build_synthetic_r130run(
        tmp_path / "invalid-id.r130run",
        manifest_mutator=invalid_package_id,
    )
    report = RunPackageValidator().validate(package, _control())
    assert report.findings[0].code == "source_id_invalid"

    with pytest.raises(ValidationTimeoutError):
        RunPackageValidator().validate(
            package,
            ValidationControl(Event(), monotonic() - 1, _ignore_progress),
        )


def test_large_measurement_csv_is_streamed_without_semantic_claim(tmp_path: Path) -> None:
    row = b"019d3c80-3d21-7a65-8e5a-555555555555,019d3c80-3d21-7a65-8e5a-222222222222\n"
    csv_payload = b"measurement_id,run_id\n" + row * 20_000
    package = build_synthetic_r130run(
        tmp_path / "large-csv.r130run",
        payload_overrides={"measurements.csv": csv_payload},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "partial"
    assert next(item for item in report.semanticCoverage if item.area == "measurements_csv").status == "not_available"


def test_csv_field_within_published_one_mib_bound_is_accepted(tmp_path: Path) -> None:
    csv_payload = b"measurement_id,run_id\n" + b"a" * (200 * 1024) + b",run\n"
    package = build_synthetic_r130run(
        tmp_path / "wide-field.r130run",
        payload_overrides={"measurements.csv": csv_payload},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "passed"


def test_frozen_semantic_shape_requires_example_fields(tmp_path: Path) -> None:
    incomplete_plan = b'{"schema_version":"r130sh.run-plan.v1","run_id":"019d3c80-3d21-7a65-8e5a-222222222222"}\n'
    package = build_synthetic_r130run(
        tmp_path / "incomplete-plan.r130run",
        payload_overrides={"plan/original.json": incomplete_plan},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "failed"
    assert any(item.code == "semantic_shape_mismatch" for item in report.findings)


def test_frozen_plan_exact_and_rounding_values_are_checked(tmp_path: Path) -> None:
    plan = _fixture_object("plan.rbd-rounding.example.json")
    targets = plan["execution_targets"]
    assert isinstance(targets, dict)
    targets["target_cycles"] = 1500
    package = build_synthetic_r130run(
        tmp_path / "invalid-rounding.r130run",
        payload_overrides={"plan/original.json": json.dumps(plan).encode("utf-8")},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.semanticVerdict == "failed"
    assert any(item.code == "semantic_value_mismatch" for item in report.findings)


def test_frozen_plan_rejects_non_finite_decimal_strings_as_semantic_finding(tmp_path: Path) -> None:
    plan = _fixture_object("plan.rbd-rounding.example.json")
    source_values = plan["source_values"]
    requirements = plan["methodical_requirements"]
    assert isinstance(source_values, dict)
    assert isinstance(requirements, dict)
    source_values["base_cycles"] = "Infinity"
    source_values["reserve_factor"] = "1"
    requirements["required_cycles_exact"] = "Infinity"
    requirements["required_steady_duration_s_exact"] = "Infinity"
    package = build_synthetic_r130run(
        tmp_path / "non-finite-plan.r130run",
        payload_overrides={"plan/original.json": json.dumps(plan).encode("utf-8")},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "failed"
    assert any(item.code == "semantic_value_mismatch" for item in report.findings)


@pytest.mark.parametrize(
    "field",
    [
        "source_values.base_cycles",
        "source_values.reserve_factor",
        "source_values.nominal_rpm",
        "source_values.acceleration_duration_s",
        "source_values.deceleration_duration_s",
        "methodical_requirements.required_cycles_exact",
        "methodical_requirements.required_steady_duration_s_exact",
        "execution_targets.target_steady_duration_s",
    ],
)
def test_frozen_plan_bounds_every_decimal_string_before_arithmetic(tmp_path: Path, field: str) -> None:
    plan = _fixture_object("plan.rbd-rounding.example.json")
    _set_nested_value(plan, field, "1e999999999")
    package = build_synthetic_r130run(
        tmp_path / "unbounded-decimal-plan.r130run",
        payload_overrides={"plan/original.json": json.dumps(plan).encode("utf-8")},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "failed"
    assert any(item.code == "semantic_value_mismatch" for item in report.findings)


@pytest.mark.parametrize(
    ("path", "fixture_name", "mutated_field"),
    [
        ("provenance.json", "provenance.example.json", "stand.stand_spec_sha256"),
        ("accepted-summary.json", "accepted-projection.example.json", "points.0.measurement_id"),
        ("inspections.json", "inspection.example.json", "inspection_id"),
    ],
)
def test_frozen_semantic_identity_and_hash_values_are_checked(
    tmp_path: Path,
    path: str,
    fixture_name: str,
    mutated_field: str,
) -> None:
    value = _fixture_object(fixture_name)
    _set_nested_value(value, mutated_field, "invalid")
    payload: JsonValue = [value] if path == "inspections.json" else value
    package = build_synthetic_r130run(
        tmp_path / "invalid-semantic-value.r130run",
        payload_overrides={path: json.dumps(payload, ensure_ascii=False).encode("utf-8")},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.semanticVerdict == "failed"
    assert any(item.code == "semantic_value_mismatch" for item in report.findings)


def test_event_payload_hash_is_checked(tmp_path: Path) -> None:
    event = _fixture_object("event.example.json")
    event["payload_sha256"] = "0" * 64
    package = build_synthetic_r130run(
        tmp_path / "invalid-event-hash.r130run",
        payload_overrides={"events.jsonl": json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"},
    )

    report = RunPackageValidator().validate(package, _control())

    assert any(item.code == "semantic_value_mismatch" for item in report.findings)


@pytest.mark.parametrize(
    ("fixture_name", "path", "field", "value", "container"),
    [
        ("plan.rbd-rounding.example.json", "plan/original.json", "plan_id", "not-a-uuid", "object"),
        ("plan.rbd-rounding.example.json", "plan/original.json", "source_values.nominal_rpm", "zero", "object"),
        ("event.example.json", "events.jsonl", "actor_json", 42, "jsonl"),
        ("provenance.example.json", "provenance.json", "producer.git_commit", "short", "object"),
        ("inspection.example.json", "inspections.json", "trip_index", -1, "array"),
    ],
)
def test_frozen_semantic_profiles_reject_invalid_identity_shape_and_values(
    tmp_path: Path,
    fixture_name: str,
    path: str,
    field: str,
    value: JsonValue,
    container: str,
) -> None:
    payload = _fixture_object(fixture_name)
    _set_nested_value(payload, field, value)
    document: JsonValue = [payload] if container == "array" else payload
    encoded = json.dumps(document, ensure_ascii=False).encode("utf-8")
    if container == "jsonl":
        encoded += b"\n"
    package = build_synthetic_r130run(
        tmp_path / "semantic-profile.r130run",
        payload_overrides={path: encoded},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.semanticVerdict == "failed"


def test_csv_rejects_unfinished_utf8_and_multiline_quoted_record(tmp_path: Path) -> None:
    unfinished = build_synthetic_r130run(
        tmp_path / "unfinished-utf8.r130run",
        payload_overrides={"measurements.csv": b"measurement_id,run_id\nvalue,run\xc3"},
    )
    unfinished_report = RunPackageValidator().validate(unfinished, _control())
    assert unfinished_report.findings[0].code == "csv_invalid"

    multiline = build_synthetic_r130run(
        tmp_path / "multiline-csv.r130run",
        payload_overrides={"measurements.csv": b'measurement_id,run_id\n"value\ncontinued",run\n'},
    )
    multiline_report = RunPackageValidator().validate(multiline, _control())
    assert multiline_report.findings[0].code == "csv_invalid"


def test_finding_counts_include_details_omitted_by_bound(tmp_path: Path) -> None:
    event = {
        "schema_version": "wrong",
        "run_id": "019d3c80-3d21-7a65-8e5a-222222222222",
    }
    events = b"".join((json.dumps(event) + "\n").encode("utf-8") for _ in range(250))
    package = build_synthetic_r130run(
        tmp_path / "many-findings.r130run",
        payload_overrides={"events.jsonl": events},
    )

    report = RunPackageValidator().validate(package, _control())

    assert len(report.findings) == 200
    assert report.findingCounts.total == 250
    assert report.findingCounts.error == 250
    assert report.findingCounts.truncated is True


def test_manifest_rejects_report_values_outside_cross_language_profile(tmp_path: Path) -> None:
    def invalid_report_values(manifest: dict[str, JsonValue]) -> None:
        producer = manifest["producer"]
        assert isinstance(producer, dict)
        producer["name"] = "😀" * 128
        manifest["export_revision"] = 9_007_199_254_740_992

    package = build_synthetic_r130run(
        tmp_path / "unsafe-report-values.r130run",
        manifest_mutator=invalid_report_values,
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "failed"
    assert report.findings[0].code in {"manifest_export_revision", "manifest_producer"}


def test_manifest_rejects_non_ascii_producer_projection(tmp_path: Path) -> None:
    def invalid_producer(manifest: dict[str, JsonValue]) -> None:
        producer = manifest["producer"]
        assert isinstance(producer, dict)
        producer["name"] = "R130Ш"

    package = build_synthetic_r130run(
        tmp_path / "unicode-producer.r130run",
        manifest_mutator=invalid_producer,
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.findings[0].code == "string_invalid"


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("row_count", -1, "manifest_row_count"),
        ("size", -1, "manifest_size"),
        ("media_type", "", "string_invalid"),
    ],
)
def test_manifest_inventory_fields_are_strict(
    tmp_path: Path,
    field: str,
    value: JsonValue,
    expected_code: str,
) -> None:
    def invalid_inventory(manifest: dict[str, JsonValue]) -> None:
        files = manifest["files"]
        assert isinstance(files, list)
        first = files[0]
        assert isinstance(first, dict)
        first[field] = value

    package = build_synthetic_r130run(
        tmp_path / "invalid-inventory.r130run",
        manifest_mutator=invalid_inventory,
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.findings[0].code == expected_code


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("package_kind", "manifest_package_kind"),
        ("timestamp", "timestamp_invalid"),
        ("snapshot_hash", "sha256_invalid"),
        ("empty_files", "manifest_files"),
        ("duplicate_path", "manifest_path_duplicate"),
        ("expanded_size", "expanded_size_limit"),
        ("invalid_row_count", "manifest_row_count"),
    ],
)
def test_manifest_contract_rejects_invalid_identity_and_inventory_cases(
    tmp_path: Path,
    case: str,
    expected_code: str,
) -> None:
    def mutate(manifest: dict[str, JsonValue]) -> None:
        files = manifest["files"]
        assert isinstance(files, list)
        if case == "package_kind":
            manifest["package_kind"] = "unknown"
        elif case == "timestamp":
            manifest["created_at_utc"] = "not-utc"
        elif case == "snapshot_hash":
            manifest["source_snapshot_sha256"] = "A" * 64
        elif case == "empty_files":
            manifest["files"] = []
        elif case == "duplicate_path":
            files.append(files[0])
        elif case == "expanded_size":
            for item in files:
                assert isinstance(item, dict)
                item["size"] = 3 * 1024 * 1024 * 1024
        else:
            first = files[0]
            assert isinstance(first, dict)
            first["row_count"] = -1

    package = build_synthetic_r130run(
        tmp_path / "invalid-manifest-contract.r130run",
        manifest_mutator=mutate,
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "failed"
    assert report.findings[0].code == expected_code


def test_diagnostic_partial_package_kind_is_reported_without_import_claim(tmp_path: Path) -> None:
    def diagnostic_partial(manifest: dict[str, JsonValue]) -> None:
        manifest["package_kind"] = "diagnostic_partial"

    package = build_synthetic_r130run(
        tmp_path / "diagnostic-partial.r130run",
        manifest_mutator=diagnostic_partial,
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "partial"
    assert report.packageKind == "diagnostic_partial"


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("inspections.json", b"{}"),
        (
            "provenance.json",
            b'{"schema_version":"r130sh.run-provenance.v1","run_id":"019d3c80-3d21-7a65-8e5a-222222222222"}',
        ),
        (
            "accepted-summary.json",
            b'{"schema_version":"r130sh.accepted-projection.v1","run_id":"019d3c80-3d21-7a65-8e5a-222222222222"}',
        ),
    ],
)
def test_covered_semantic_payloads_require_frozen_shape(tmp_path: Path, path: str, content: bytes) -> None:
    package = build_synthetic_r130run(
        tmp_path / "semantic-shape.r130run",
        payload_overrides={path: content},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.semanticVerdict == "failed"
    assert any(item.code == "semantic_shape_mismatch" for item in report.findings)


def test_cross_file_run_identity_mismatch_is_semantic_failure(tmp_path: Path) -> None:
    wrong_plan = b'{"schema_version":"r130sh.run-plan.v1","run_id":"8ab377f2-cfd8-4983-86ea-25f5d0171bd7"}\n'
    package = build_synthetic_r130run(
        tmp_path / "wrong-run.r130run",
        payload_overrides={"plan/original.json": wrong_plan},
    )

    report = RunPackageValidator().validate(package, _control())

    assert report.structuralVerdict == "passed"
    assert report.semanticVerdict == "failed"
    assert any(item.code == "cross_file_run_id_mismatch" for item in report.findings)


def _entries(package: Path) -> list[tuple[str, bytes]]:
    with ZipFile(package, mode="r") as archive:
        return [(info.filename, archive.read(info)) for info in archive.infolist()]


def _fixture_object(name: str) -> dict[str, JsonValue]:
    repository_root = Path(__file__).resolve().parents[4]
    return TypeAdapter(dict[str, JsonValue]).validate_json((repository_root / "fixtures/contracts/r130run/v1" / name).read_bytes())


def _set_nested_value(root: dict[str, JsonValue], dotted_path: str, value: JsonValue) -> None:
    current: JsonValue = root
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise AssertionError("invalid_fixture_path")
    if not isinstance(current, dict):
        raise AssertionError("invalid_fixture_target")
    current[parts[-1]] = value


def _control(
    cancel_event: Event | None = None,
    *,
    progress: ProgressCallback | None = None,
) -> ValidationControl:
    callback = progress or _ignore_progress
    return ValidationControl(
        cancel_event=cancel_event or Event(),
        expires_at=monotonic() + 30,
        progress=callback,
    )


def _ignore_progress(
    _phase: JobPhase,
    _completed: int,
    _total: int,
    _entries: int,
    _entry_total: int,
) -> None:
    return
