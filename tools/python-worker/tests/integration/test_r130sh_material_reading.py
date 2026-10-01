from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import IO, Literal
from uuid import uuid4
from zipfile import ZipFile, ZipInfo

from pydantic import TypeAdapter
import pytest

from impeller_reliability.integration.r130run.validator import RunPackageValidator
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.worker.deadline import RequestDeadline
from support.r130run_builder import RUN_ID, JsonValue, build_synthetic_r130run
from support.r130run_materials import inspection_record, photo_record, protocol_payloads
from support.r130sh_import import create_import_project, frozen_package, import_run_package

OBJECT = TypeAdapter(dict[str, JsonValue])


def material_package(path: Path, *, revision: int = 1, comment: str = "Source comment", count: int = 2) -> Path:
    inspections: list[JsonValue] = []
    for index in range(count):
        record = inspection_record()
        record.update(inspection_id=f"inspection-{index}", comment=comment, stage="vibration_pause", trip_index=2**80)
        inspections.append(record)
    photo, content = photo_record()
    photo.update(inspection_id="inspection-0", width_px=2**80, height_px=2**72)
    release_payloads = protocol_payloads()
    release = OBJECT.validate_json(release_payloads["protocol/release.json"])
    release.update(release_id=2**80, revision_number=2**72, photo_ids=[photo["attachment_id"], "unresolved-photo"])
    release_payloads["protocol/release.json"] = json.dumps(release).encode()
    photo_path = photo["path"]
    assert isinstance(photo_path, str)
    return build_synthetic_r130run(
        path,
        manifest_mutator=lambda manifest: manifest.update(export_revision=revision),
        payload_overrides={
            "inspections.json": json.dumps({"schema_version": "r130sh.inspections.v1", "run_id": RUN_ID, "inspections": inspections}).encode(),
            "attachments/index.json": json.dumps({"schema_version": "r130sh.attachments.v1", "run_id": RUN_ID, "attachments": [photo]}).encode(),
            photo_path: content,
            **release_payloads,
        },
    )


def test_unbound_material_read_is_addressed_and_does_not_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, frozen_package("normal_final_rbd.r130run"))
    assert imported.local_specimen_id is None
    service.close()
    before = hashlib.sha256((project_path / "project.sqlite").read_bytes()).hexdigest()
    service.open(path=str(project_path), application_instance_id="materials-read")

    def forbid_full_validation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("material_read_must_not_scan_csv")

    monkeypatch.setattr(RunPackageValidator, "validate", forbid_full_validation)
    first = service.list_imported_run_inspection_page(imported.local_import_id, None, 1, None)
    assert first.origin.local_import_id == imported.local_import_id
    assert first.verification.validatorVersion == "m03b.3"
    assert len(first.items) == 1
    assert first.next_cursor is not None
    assert first.items[0].data is not None
    assert first.items[0].material_id is not None
    detail = service.get_imported_run_inspection(imported.local_import_id, first.items[0].material_id, None)
    assert detail.item.data == first.items[0].data
    second = service.list_imported_run_inspection_page(imported.local_import_id, first.next_cursor, 1, None)
    assert second.items[0].source_index == 1
    assert service.list_imported_run_photo_page(imported.local_import_id, None, 25, None).items == ()
    assert service.get_imported_run_protocol(imported.local_import_id, None).item.state == "not_included"
    service.close()
    assert hashlib.sha256((project_path / "project.sqlite").read_bytes()).hexdigest() == before


def test_material_read_rejects_unknown_import_cursor_and_deadline(tmp_path: Path) -> None:
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, frozen_package("normal_final_rbd.r130run"))
    with pytest.raises(ProjectOperationError) as unknown:
        service.list_imported_run_inspection_page(str(uuid4()), None, 25, None)
    assert unknown.value.code == "entity_not_found"
    with pytest.raises(ProjectOperationError) as cursor:
        service.list_imported_run_inspection_page(imported.local_import_id, "invalid", 25, None)
    assert cursor.value.code == "validation_error"
    with pytest.raises(ProjectOperationError) as timeout:
        service.get_imported_run_protocol(imported.local_import_id, RequestDeadline(expires_at=0))
    assert timeout.value.code == "timeout"
    service.close()


def test_material_read_never_opens_csv_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, frozen_package("normal_final_rbd.r130run"))
    opened: list[str] = []
    original_open = ZipFile.open

    def observe_open(self: ZipFile, name: str | ZipInfo, mode: Literal["r", "w"] = "r", pwd: bytes | None = None, *, force_zip64: bool = False) -> IO[bytes]:
        member = name if isinstance(name, str) else name.filename
        assert member != "measurements.csv"
        opened.append(member)
        return original_open(self, name, mode, pwd, force_zip64=force_zip64)

    monkeypatch.setattr(ZipFile, "open", observe_open)
    service.list_imported_run_inspection_page(imported.local_import_id, None, 25, None)
    assert set(opened) <= {"manifest.json", "inspections.json", "attachments/index.json", "protocol/release.json"}
    service.close()


def test_exact_revisions_and_lossless_serialized_materials(tmp_path: Path) -> None:
    service, project_path = create_import_project(tmp_path)
    old = import_run_package(service, project_path, material_package(tmp_path / "old.r130run", comment="Old source"))
    new = import_run_package(service, project_path, material_package(tmp_path / "new.r130run", revision=2, comment="New source"))
    for imported, comment in ((old, "Old source"), (new, "New source")):
        page = service.list_imported_run_inspection_page(imported.local_import_id, None, 1, None)
        assert page.origin.export_revision == imported.export_revision
        assert page.items[0].data is not None
        assert page.items[0].data.comment == comment
        serialized = json.loads(page.model_dump_json())
        assert serialized["items"][0]["data"]["tripIndex"] == str(2**80)
        assert serialized["items"][0]["data"]["findings"]["cracks"] is False
        assert serialized["items"][0]["data"]["runElapsedS"] == "0"
        photos = service.list_imported_run_photo_page(imported.local_import_id, None, 25, None)
        assert photos.items[0].data is not None
        assert photos.items[0].data.width_px == str(2**80)
        assert photos.items[0].references[0].status == "resolved"
        assert '"path"' not in photos.model_dump_json()
        protocol = service.get_imported_run_protocol(imported.local_import_id, None)
        assert protocol.item.data is not None
        assert protocol.item.data.release_id == str(2**80)
        assert protocol.item.data.revision_number == str(2**72)
        assert [reference.status for reference in protocol.item.references] == ["resolved", "unresolved"]
        assert page.next_cursor is not None
        other = new if imported == old else old
        with pytest.raises(ProjectOperationError, match="Cursor"):
            service.list_imported_run_inspection_page(other.local_import_id, page.next_cursor, 1, None)
        with pytest.raises(ProjectOperationError, match="Cursor"):
            service.list_imported_run_photo_page(imported.local_import_id, page.next_cursor, 1, None)
        with pytest.raises(ProjectOperationError) as missing:
            service.get_imported_run_inspection(imported.local_import_id, "foreign-material", None)
        assert missing.value.code == "entity_not_found"
    service.close()
    service.open(path=str(project_path), application_instance_id="materials-reopen")
    assert service.get_imported_run_inspection(old.local_import_id, "inspection-0", None).item.data is not None
    service.close()


@pytest.mark.parametrize("limit", [0, 51, True])
def test_material_page_rejects_invalid_limits(tmp_path: Path, limit: int) -> None:
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, frozen_package("normal_final_rbd.r130run"))
    with pytest.raises(ProjectOperationError) as error:
        service.list_imported_run_inspection_page(imported.local_import_id, None, limit, None)
    assert error.value.code == "validation_error"
    service.close()


@pytest.mark.parametrize("comment", ["я" * 8193, "x" * (64 * 1024)], ids=["text-byte-limit", "record-byte-limit"])
def test_oversized_record_is_explicit_without_truncation(tmp_path: Path, comment: str) -> None:
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, material_package(tmp_path / "large.r130run", comment=comment))
    page = service.list_imported_run_inspection_page(imported.local_import_id, None, 25, None)
    assert [item.state for item in page.items] == ["too_large", "too_large"]
    assert all(item.data is None and item.detail for item in page.items)
    assert len(page.model_dump_json().encode()) < 256 * 1024
    assert service.get_imported_run_inspection(imported.local_import_id, "inspection-0", None).item.state == "too_large"
    service.close()


def test_page_byte_bound_continues_without_loss_or_repetition(tmp_path: Path) -> None:
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, material_package(tmp_path / "page-bound.r130run", count=30, comment="x" * 14000))
    first = service.list_imported_run_inspection_page(imported.local_import_id, None, 50, None)
    assert first.page_bound == "byte_limit" and first.next_cursor is not None
    second = service.list_imported_run_inspection_page(imported.local_import_id, first.next_cursor, 50, None)
    assert [item.source_index for item in (*first.items, *second.items)] == list(range(30))
    assert second.next_cursor is None
    assert all(len(page.model_dump_json().encode()) <= 256 * 1024 for page in (first, second))
    service.close()


@pytest.mark.parametrize("change", ["missing", "modified", "same-mtime"])
def test_material_read_rechecks_source_each_time(tmp_path: Path, change: str) -> None:
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, material_package(tmp_path / "material.r130run"))
    service.get_imported_run_protocol(imported.local_import_id, None)
    source = project_path / "imports" / "r130sh" / imported.package_id / f"rev-{imported.export_revision}" / f"{imported.outer_package_sha256}.r130run"
    if change == "missing":
        source.unlink()
    else:
        stat = source.stat()
        content = bytearray(source.read_bytes())
        content[20] ^= 1
        source.write_bytes(content)
        if change == "same-mtime":
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ProjectOperationError) as error:
        service.get_imported_run_protocol(imported.local_import_id, None)
    assert error.value.code == ("file_missing" if change == "missing" else "file_integrity_mismatch")
    service.close()


def test_source_change_between_hash_and_metadata_read_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, material_package(tmp_path / "material.r130run"))
    source = project_path / "imports" / "r130sh" / imported.package_id / f"rev-{imported.export_revision}" / f"{imported.outer_package_sha256}.r130run"
    original_open = ZipFile.open
    changed = False

    def change_source(self: ZipFile, name: str | ZipInfo, mode: Literal["r", "w"] = "r", pwd: bytes | None = None, *, force_zip64: bool = False) -> IO[bytes]:
        nonlocal changed
        if not changed:
            changed = True
            with source.open("r+b") as stream:
                stream.seek(20)
                value = stream.read(1)
                stream.seek(20)
                stream.write(bytes([value[0] ^ 1]))
        return original_open(self, name, mode, pwd, force_zip64=force_zip64)

    monkeypatch.setattr(ZipFile, "open", change_source)
    with pytest.raises(ProjectOperationError) as error:
        service.get_imported_run_protocol(imported.local_import_id, None)
    assert changed
    assert error.value.code == "file_integrity_mismatch"
    service.close()


def test_duplicate_ids_are_not_resolved_by_first_match(tmp_path: Path) -> None:
    base = material_package(tmp_path / "base.r130run")
    with ZipFile(base) as archive:
        inspections = OBJECT.validate_json(archive.read("inspections.json"))
        photos = OBJECT.validate_json(archive.read("attachments/index.json"))
    records = inspections["inspections"]
    assert isinstance(records, list) and isinstance(records[0], dict)
    inspections["inspections"] = [records[0], records[0]]
    attachments = photos["attachments"]
    assert isinstance(attachments, list)
    photos["attachments"] = [attachments[0], attachments[0]]
    package = build_synthetic_r130run(
        tmp_path / "duplicates.r130run",
        base_package=base,
        payload_overrides={
            "inspections.json": json.dumps(inspections).encode(),
            "attachments/index.json": json.dumps(photos).encode(),
        },
    )
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    page = service.list_imported_run_inspection_page(imported.local_import_id, None, 1, None)
    assert page.items[0].state == "ambiguous" and page.next_cursor is not None
    next_page = service.list_imported_run_inspection_page(imported.local_import_id, page.next_cursor, 1, None)
    assert next_page.items[0].source_index == 1
    with pytest.raises(ProjectOperationError) as error:
        service.get_imported_run_inspection(imported.local_import_id, "inspection-0", None)
    assert error.value.details == {"reason": "material_id_ambiguous"}
    photos_page = service.list_imported_run_photo_page(imported.local_import_id, None, 25, None)
    assert all(item.state == "ambiguous" and item.references[0].status == "ambiguous" for item in photos_page.items)
    assert service.get_imported_run_protocol(imported.local_import_id, None).item.references[0].status == "ambiguous"
    service.close()


def test_unavailable_source_photo_and_empty_inspections_are_distinct(tmp_path: Path) -> None:
    photo, _content = photo_record()
    photo.update(path=None, availability="unavailable", unavailable_reason="Source file was removed", inspection_id=None)
    base = frozen_package("diagnostic_partial.r130run")
    with ZipFile(base) as archive:
        manifest = OBJECT.validate_json(archive.read("manifest.json"))
    photo["run_id"] = manifest["run_id"]
    package = build_synthetic_r130run(
        tmp_path / "unavailable.r130run",
        base_package=base,
        manifest_mutator=lambda value: value.update(package_kind="diagnostic_partial"),
        payload_overrides={
            "attachments/index.json": json.dumps({"schema_version": "r130sh.attachments.v1", "run_id": manifest["run_id"], "attachments": [photo]}).encode(),
            "inspections.json": json.dumps({"schema_version": "r130sh.inspections.v1", "run_id": manifest["run_id"], "inspections": []}).encode(),
        },
    )
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    page = service.list_imported_run_photo_page(imported.local_import_id, None, 25, None)
    assert page.items[0].state == "unavailable"
    assert page.items[0].data is not None
    assert page.items[0].data.unavailable_reason == "Source file was removed"
    assert page.items[0].data.inspection_id is None and not page.items[0].references
    empty = service.list_imported_run_inspection_page(imported.local_import_id, None, 25, None)
    assert empty.items == () and empty.next_cursor is None and empty.page_bound is None
    service.close()


def test_material_deadline_expires_during_member_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, material_package(tmp_path / "material.r130run"))
    now = [0.0]
    deadline = RequestDeadline(expires_at=30, _clock=lambda: now[0])
    original_open = ZipFile.open

    def expire(self: ZipFile, name: str | ZipInfo, mode: Literal["r", "w"] = "r", pwd: bytes | None = None, *, force_zip64: bool = False) -> IO[bytes]:
        member = name if isinstance(name, str) else name.filename
        if member == "inspections.json":
            now[0] = 31
        return original_open(self, name, mode, pwd, force_zip64=force_zip64)

    monkeypatch.setattr(ZipFile, "open", expire)
    with pytest.raises(ProjectOperationError) as error:
        service.get_imported_run_protocol(imported.local_import_id, deadline)
    assert error.value.code == "timeout"
    service.close()


def test_available_photo_does_not_interpret_irrelevant_unavailable_reason(tmp_path: Path) -> None:
    photo, content = photo_record()
    photo["unavailable_reason"] = 123
    photo_path = photo["path"]
    assert isinstance(photo_path, str)
    package = build_synthetic_r130run(
        tmp_path / "extra-reason.r130run",
        payload_overrides={
            "attachments/index.json": json.dumps({"schema_version": "r130sh.attachments.v1", "run_id": RUN_ID, "attachments": [photo]}).encode(),
            photo_path: content,
        },
    )
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    try:
        page = service.list_imported_run_photo_page(imported.local_import_id, None, 25, None)
        assert page.items[0].state == "verified"
        assert page.items[0].data is not None and page.items[0].data.unavailable_reason is None
    finally:
        service.close()


@pytest.mark.parametrize("actor", [{"x" * 17000: "value"}, {"a": "x" * 9000, "b": "x" * 9000}], ids=["large-key", "combined-values"])
def test_generated_protocol_actor_text_obeys_response_bound(tmp_path: Path, actor: dict[str, str]) -> None:
    payloads = protocol_payloads()
    release = OBJECT.validate_json(payloads["protocol/release.json"])
    release["released_by_actor"] = dict(actor)
    payloads["protocol/release.json"] = json.dumps(release).encode()
    package = build_synthetic_r130run(tmp_path / "actor-text.r130run", payload_overrides=payloads)
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    try:
        result = service.get_imported_run_protocol(imported.local_import_id, None)
        assert result.item.state == "too_large" and result.item.data is None
    finally:
        service.close()


def test_restored_mtime_change_during_material_read_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = material_package(tmp_path / "race.r130run")
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    source = project_path / "imports" / "r130sh" / imported.package_id / f"rev-{imported.export_revision}" / f"{imported.outer_package_sha256}.r130run"
    original_open = ZipFile.open
    changed = False

    def change_csv(self: ZipFile, name: str | ZipInfo, mode: Literal["r", "w"] = "r", pwd: bytes | None = None, *, force_zip64: bool = False) -> IO[bytes]:
        nonlocal changed
        if not changed:
            changed = True
            member = self.getinfo("measurements.csv")
            offset = member.header_offset + 30 + len(member.filename.encode("utf-8")) + len(member.extra)
            before = source.stat()
            with source.open("r+b") as stream:
                stream.seek(offset)
                value = stream.read(1)
                stream.seek(offset)
                stream.write(bytes([value[0] ^ 1]))
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        return original_open(self, name, mode, pwd, force_zip64=force_zip64)

    monkeypatch.setattr(ZipFile, "open", change_csv)
    try:
        with pytest.raises(ProjectOperationError) as error:
            service.get_imported_run_protocol(imported.local_import_id, None)
        assert changed and error.value.code == "file_integrity_mismatch"
    finally:
        service.close()
