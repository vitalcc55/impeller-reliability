from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
from zipfile import ZipFile

from pydantic import TypeAdapter
import pytest

from impeller_reliability.integration.r130run.material_models import MaterialIdentity
from impeller_reliability.persistence.material_copies import discard_material_copy
from impeller_reliability.worker.deadline import RequestDeadline
from support.r130run_builder import JsonValue
from support.r130sh_import import create_import_project, import_run_package

MATERIAL_FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "contracts" / "r130run" / "v1" / "source-materials"


@pytest.mark.parametrize(
    ("filename", "export_revision", "protocol_revision"),
    [
        ("protocol_not_included.r130run", 1, None),
        ("protocol_revision_1.r130run", 2, "1"),
        ("protocol_revision_2.r130run", 3, "2"),
        ("photo_unavailable.r130run", 1, None),
    ],
)
def test_current_producer_materials_read_copy_and_reopen_without_project_writes(tmp_path: Path, filename: str, export_revision: int, protocol_revision: str | None) -> None:
    package = MATERIAL_FIXTURES / filename
    service, project_path = create_import_project(tmp_path)
    try:
        imported = import_run_package(service, project_path, package)
        assert imported.local_specimen_id is None
        assert imported.export_revision == export_revision
        service.close()
        before = (project_path / "project.sqlite").read_bytes()
        service.open(path=str(project_path), application_instance_id="producer-materials")
        inspections = service.list_imported_run_inspection_page(imported.local_import_id, None, 25, None)
        photos = service.list_imported_run_photo_page(imported.local_import_id, None, 25, None)
        protocol = service.get_imported_run_protocol(imported.local_import_id, None)
        assert inspections.origin == photos.origin == protocol.origin
        assert inspections.origin.outer_package_sha256 == hashlib.sha256(package.read_bytes()).hexdigest()
        assert inspections.verification.semanticVerdict == "passed"
        assert len(inspections.items) == 2
        assert all(item.data is not None and item.data.run_id == imported.run_id for item in inspections.items)
        assert protocol.item.state == ("not_included" if protocol_revision is None else "verified")
        if protocol_revision is not None:
            assert protocol.item.data is not None
            assert protocol.item.data.revision_number == protocol_revision
            assert protocol.item.data.revision_number != str(export_revision)
        if filename == "photo_unavailable.r130run":
            assert len(photos.items) == 1
            assert photos.items[0].state == "unavailable"
            assert photos.items[0].data is not None
            assert photos.items[0].data.availability == "unavailable"
            assert photos.items[0].data.unavailable_reason
        else:
            assert len(photos.items) == 2
            assert {item.data.media_type for item in photos.items if item.data is not None} == {"image/jpeg", "image/png"}
            assert any(item.data is not None and item.data.inspection_id is None for item in photos.items)
            assert any(item.data is not None and item.data.inspection_id == f"{imported.run_id}:inspection:pre-test" for item in photos.items)
            for item in photos.items:
                assert item.material_id is not None and item.data is not None
                identity = MaterialIdentity(origin=photos.origin, kind="photo", material_id=item.material_id)
                directory = tmp_path / item.material_id
                directory.mkdir()
                copied = service.resolve_imported_run_material(identity, directory)
                suffix = ".jpg" if item.data.media_type == "image/jpeg" else ".png"
                with ZipFile(package) as archive:
                    expected = archive.read(f"attachments/photos/{item.material_id}{suffix}")
                assert copied.absolute_path.read_bytes() == expected
                assert copied.sha256 == item.data.sha256 == hashlib.sha256(expected).hexdigest()
                assert copied.size_bytes == item.data.size == len(expected)
                assert copied.identity == identity
                assert discard_material_copy(directory, copied.absolute_path.stem, copied.media_type, copied.file_identity, RequestDeadline.start(5000))
                assert not tuple(directory.iterdir())
        if protocol.item.material_id is not None:
            assert protocol.item.data is not None
            directory = tmp_path / "protocol"
            directory.mkdir()
            identity = MaterialIdentity(origin=protocol.origin, kind="protocol", material_id=protocol.item.material_id)
            copied = service.resolve_imported_run_material(identity, directory)
            with ZipFile(package) as archive:
                expected = archive.read("protocol/protocol.pdf")
            assert copied.absolute_path.read_bytes() == expected
            assert copied.sha256 == protocol.item.data.content_sha256 == hashlib.sha256(expected).hexdigest()
            assert copied.size_bytes == len(expected)
            assert discard_material_copy(directory, copied.absolute_path.stem, copied.media_type, copied.file_identity, RequestDeadline.start(5000))
        service.close()
        service.open(path=str(project_path), application_instance_id="producer-materials-reopen")
        assert service.list_imported_run_inspection_page(imported.local_import_id, None, 25, None) == inspections
        assert service.list_imported_run_photo_page(imported.local_import_id, None, 25, None) == photos
        assert service.get_imported_run_protocol(imported.local_import_id, None) == protocol
        service.close()
        assert (project_path / "project.sqlite").read_bytes() == before
    finally:
        service.close()


def test_saved_producer_export_revisions_coexist_and_exact_repeat_is_read_only(tmp_path: Path) -> None:
    service, project_path = create_import_project(tmp_path)
    try:
        sources = [MATERIAL_FIXTURES / name for name in ("protocol_not_included.r130run", "protocol_revision_1.r130run", "protocol_revision_2.r130run")]
        imports = [import_run_package(service, project_path, source) for source in sources]
        assert len({item.package_id for item in imports}) == 1
        assert len({item.local_import_id for item in imports}) == 3
        assert [item.export_revision for item in imports] == [1, 2, 3]
        service.close()
        before = (project_path / "project.sqlite").read_bytes()
        service.open(path=str(project_path), application_instance_id="producer-revisions")
        repeated = import_run_package(service, project_path, sources[2])
        assert repeated.imported_existing
        assert replace(repeated, imported_existing=False) == imports[2]
        assert len(service.list_imported_runs()) == 3
        assert [service.get_imported_run_protocol(item.local_import_id).item.material_id for item in imports] == [None, "1", "2"]
        service.close()
        assert (project_path / "project.sqlite").read_bytes() == before
    finally:
        service.close()


def test_producer_material_fixture_provenance_matches_all_archived_members() -> None:
    metadata = TypeAdapter(dict[str, JsonValue]).validate_json((MATERIAL_FIXTURES / "UPSTREAM_SOURCE.json").read_bytes())
    producer = metadata["producer"]
    assert isinstance(producer, dict)
    assert producer["commit"] == "9d92abb8d3d4a8c9dea5acc165f00aa28e09fb6c"
    assert producer["version"] == "0.9.48"
    packages = metadata["packages"]
    assert isinstance(packages, list) and len(packages) == 4
    for record in packages:
        assert isinstance(record, dict)
        filename = record["file"]
        assert isinstance(filename, str)
        path = MATERIAL_FIXTURES / filename
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]
        assert path.stat().st_size == record["sizeBytes"]
        members = record["members"]
        assert isinstance(members, list)
        with ZipFile(path) as archive:
            manifest = TypeAdapter(dict[str, JsonValue]).validate_json(archive.read("manifest.json"))
            assert manifest["package_id"] == record["packageId"]
            assert manifest["run_id"] == record["runId"]
            assert manifest["export_revision"] == record["exportRevision"]
            assert manifest["files"] == members
            if "protocol/release.json" in archive.namelist():
                release = TypeAdapter(dict[str, JsonValue]).validate_json(archive.read("protocol/release.json"))
                assert str(release["release_id"]) == record["protocolReleaseId"]
                assert str(release["revision_number"]) == record["protocolRevision"]
            else:
                assert record["protocolReleaseId"] is None
                assert record["protocolRevision"] is None
            payload_names: set[str] = set()
            for member in members:
                assert isinstance(member, dict)
                member_path = member["path"]
                assert isinstance(member_path, str)
                payload_names.add(member_path)
                content = archive.read(member_path)
                assert hashlib.sha256(content).hexdigest() == member["sha256"]
                assert len(content) == member["size"]
            assert payload_names == set(archive.namelist()) - {"manifest.json", "checksums.sha256"}
