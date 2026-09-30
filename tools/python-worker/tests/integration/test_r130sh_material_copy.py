from __future__ import annotations

import hashlib
import io
import json
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
from typing import IO, Literal
from uuid import uuid4
from zipfile import ZipFile, ZipInfo

from pydantic import TypeAdapter
import pytest

from impeller_reliability.integration.r130run.material_models import MaterialIdentity
from impeller_reliability.persistence import material_copies, r130sh_sources
from impeller_reliability.persistence.material_copies import discard_material_copy
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_paths import inspect_reserved_directory, opened_file_identity
from impeller_reliability.protocol.envelopes import REQUEST_ENVELOPE_ADAPTER, ImportedRunMaterialCopyResult
from impeller_reliability.worker.deadline import RequestDeadline
from impeller_reliability.worker.dispatcher import Dispatcher
from support.material_content import valid_material_content
from support.r130run_builder import JsonValue, build_synthetic_r130run
from support.r130run_materials import photo_payloads, photo_record, protocol_payloads
from support.r130sh_import import create_import_project, frozen_package, import_run_package


@pytest.mark.parametrize("media_type", ["image/png", "image/jpeg", "application/pdf"])
def test_selected_material_copy_matches_source_bytes_without_project_writes(tmp_path: Path, media_type: str) -> None:
    photo, content = photo_record(media_type=media_type if media_type != "application/pdf" else "image/png")
    payloads = protocol_payloads() if media_type == "application/pdf" else photo_payloads(photo, content)
    content = valid_material_content(media_type)
    if media_type == "application/pdf":
        release = TypeAdapter(dict[str, JsonValue]).validate_json(payloads["protocol/release.json"])
        release["content_sha256"] = hashlib.sha256(content).hexdigest()
        payloads["protocol/release.json"] = json.dumps(release).encode()
        payloads["protocol/protocol.pdf"] = content
    else:
        photo.update(size=len(content), sha256=hashlib.sha256(content).hexdigest(), width_px=2, height_px=2)
        payloads = photo_payloads(photo, content)
    package = build_synthetic_r130run(tmp_path / "source.r130run", payload_overrides=payloads)
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.list_imported_run_inspection_page(imported.local_import_id, None, 25, None).origin
    identity = MaterialIdentity(origin=origin, kind="protocol" if media_type == "application/pdf" else "photo", material_id="7" if media_type == "application/pdf" else str(photo["attachment_id"]))
    service.close()
    before = (project_path / "project.sqlite").read_bytes()
    service.open(path=str(project_path), application_instance_id="material-copy")
    output_directory = tmp_path / "viewer"
    output_directory.mkdir()
    try:
        copied = service.resolve_imported_run_material(identity, output_directory, None)
        assert copied.absolute_path.parent == output_directory.resolve()
        assert copied.media_type == media_type
        with ZipFile(package) as archive:
            expected = archive.read("protocol/protocol.pdf" if media_type == "application/pdf" else str(photo["path"]))
        assert copied.absolute_path.read_bytes() == expected
        assert copied.sha256 == hashlib.sha256(expected).hexdigest()
        assert copied.size_bytes == len(expected)
        assert copied.identity == identity
        assert len(list(output_directory.iterdir())) == 1
    finally:
        service.close()
    assert (project_path / "project.sqlite").read_bytes() == before


def test_copy_rejects_header_disagreement_and_removes_partial_file(tmp_path: Path) -> None:
    photo, _content = photo_record()
    content = b"MZ-not-a-photo"
    photo.update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
    package = build_synthetic_r130run(tmp_path / "wrong-header.r130run", payload_overrides=photo_payloads(photo, content))
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.list_imported_run_photo_page(imported.local_import_id, None, 25, None).origin
    directory = tmp_path / "viewer"
    directory.mkdir()
    try:
        with pytest.raises(ProjectOperationError) as error:
            service.resolve_imported_run_material(MaterialIdentity(origin=origin, kind="photo", material_id=str(photo["attachment_id"])), directory, None)
        assert error.value.code == "unsupported_file_type"
        assert not list(directory.iterdir())
    finally:
        service.close()


@pytest.mark.parametrize("foreign", ["project", "revision", "hash", "material"])
def test_copy_does_not_substitute_foreign_identity(tmp_path: Path, foreign: str) -> None:
    package = build_synthetic_r130run(tmp_path / "source.r130run", payload_overrides=protocol_payloads())
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.get_imported_run_protocol(imported.local_import_id, None).origin
    if foreign == "project":
        origin = origin.model_copy(update={"project_id": str(uuid4())})
    elif foreign == "revision":
        origin = origin.model_copy(update={"export_revision": 2})
    elif foreign == "hash":
        origin = origin.model_copy(update={"outer_package_sha256": "0" * 64})
    identity = MaterialIdentity(origin=origin, kind="protocol", material_id="8" if foreign == "material" else "7")
    directory = tmp_path / "viewer"
    directory.mkdir()
    try:
        with pytest.raises(ProjectOperationError) as error:
            service.resolve_imported_run_material(identity, directory, None)
        assert error.value.code in ("validation_error", "entity_not_found")
        assert not list(directory.iterdir())
    finally:
        service.close()


@pytest.mark.parametrize("failure", ["source-change", "deadline"])
def test_copy_failure_after_metadata_read_removes_owned_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    package = build_synthetic_r130run(tmp_path / "source.r130run", payload_overrides=protocol_payloads())
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.get_imported_run_protocol(imported.local_import_id, None).origin
    identity = MaterialIdentity(origin=origin, kind="protocol", material_id="7")
    source = project_path / "imports" / "r130sh" / imported.package_id / "rev-1" / f"{imported.outer_package_sha256}.r130run"
    directory = tmp_path / "viewer"
    directory.mkdir()
    now = [0.0]
    deadline = RequestDeadline(expires_at=30, _clock=lambda: now[0])
    original = ZipFile.open

    def interfere(self: ZipFile, name: str | ZipInfo, mode: Literal["r", "w"] = "r", pwd: bytes | None = None, *, force_zip64: bool = False) -> IO[bytes]:
        member_name = name if isinstance(name, str) else name.filename
        if member_name == "protocol/protocol.pdf":
            if failure == "deadline":
                now[0] = 31
            else:
                member = self.getinfo("measurements.csv")
                offset = member.header_offset + 30 + len(member.filename.encode()) + len(member.extra)
                before = source.stat()
                with source.open("r+b") as stream:
                    stream.seek(offset)
                    value = stream.read(1)
                    stream.seek(offset)
                    stream.write(bytes([value[0] ^ 1]))
                os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        return original(self, name, mode, pwd, force_zip64=force_zip64)

    monkeypatch.setattr(ZipFile, "open", interfere)
    try:
        with pytest.raises(ProjectOperationError) as error:
            service.resolve_imported_run_material(identity, directory, deadline)
        assert error.value.code == ("timeout" if failure == "deadline" else "file_integrity_mismatch")
        assert not list(directory.iterdir())
    finally:
        service.close()


def test_copy_size_bound_rejects_before_publication(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "source.r130run", payload_overrides=protocol_payloads())
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.get_imported_run_protocol(imported.local_import_id, None).origin
    directory = tmp_path / "viewer"
    directory.mkdir()
    try:
        with pytest.raises(ProjectOperationError) as error:
            service.resolve_imported_run_material(MaterialIdentity(origin=origin, kind="protocol", material_id="7"), directory, None, copy_byte_limit=10)
        assert error.value.code == "file_too_large"
        assert not list(directory.iterdir())
    finally:
        service.close()


def test_copy_does_not_write_into_project_assets(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "source.r130run", payload_overrides=protocol_payloads())
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.get_imported_run_protocol(imported.local_import_id, None).origin
    directory = project_path / "assets" / "documents"
    before = list(directory.iterdir())
    try:
        with pytest.raises(ProjectOperationError) as error:
            service.resolve_imported_run_material(MaterialIdentity(origin=origin, kind="protocol", material_id="7"), directory, None)
        assert error.value.code == "validation_error"
        assert list(directory.iterdir()) == before
    finally:
        service.close()


def test_material_dispatcher_serializes_metadata_and_internal_copy(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "source.r130run", payload_overrides=protocol_payloads())
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.get_imported_run_protocol(imported.local_import_id, None).origin
    service.close()
    dispatcher = Dispatcher(tmp_path / "worker-state")

    def dispatch(operation: str, payload: dict[str, object]) -> str:
        request = REQUEST_ENVELOPE_ADAPTER.validate_python(
            {
                "protocolVersion": 1,
                "requestId": str(uuid4()),
                "kind": "request",
                "operation": operation,
                "revision": 1,
                "deadlineMs": 30000,
                "payload": payload,
            }
        )
        return dispatcher.dispatch(request).model_dump_json()

    try:
        dispatch("project.open", {"path": str(project_path), "applicationInstanceId": "material-dispatch"})
        page = dispatch("importedRun.listInspectionPage", {"origin": origin.model_dump(mode="json"), "limit": 1})
        assert '"sourceIndex":0' in page and '"findings"' in page and '"runElapsedS":"0"' in page
        assert '"scope":"source_material_metadata"' in page
        assert '"absolutePath"' not in page and '"path"' not in page
        detail = dispatch("importedRun.getProtocol", {"origin": origin.model_dump(mode="json")})
        assert '"releaseId":"7"' in detail and '"revisionNumber":"2"' in detail
        directory = tmp_path / "viewer"
        directory.mkdir()
        copy_id = str(uuid4())
        result = dispatch(
            "importedRun.resolveMaterial",
            {
                "identity": MaterialIdentity(origin=origin, kind="protocol", material_id="7").model_dump(mode="json"),
                "outputDirectory": str(directory),
                "copyId": copy_id,
            },
        )
        assert '"absolutePath"' in result and '"mediaType":"application/pdf"' in result
        assert (directory / f"{copy_id}.pdf").is_file()
        outer = TypeAdapter(dict[str, object]).validate_json(result)
        copy_result = ImportedRunMaterialCopyResult.model_validate(outer["result"])
        assert len(copy_result.fileIdentity.fileId) == 32
        dispatch("project.close", {})
        discarded = dispatch("materialCopy.discard", {"approvedDirectory": str(directory), "copyId": copy_id, "mediaType": "application/pdf", "fileIdentity": copy_result.fileIdentity.model_dump()})
        assert '"discarded":true' in discarded and not list(directory.iterdir())
    finally:
        dispatch("project.close", {})


@pytest.mark.parametrize("state", ["not_included", "unavailable", "ambiguous", "empty_pdf"])
def test_copy_refuses_non_openable_source_material_states(tmp_path: Path, state: str) -> None:
    photo, content = photo_record()
    payloads: dict[str, bytes] = {}
    base = frozen_package("normal_final_rbd.r130run")
    kind: Literal["photo", "protocol"] = "protocol"
    material_id = "7"
    if state == "unavailable":
        base = frozen_package("diagnostic_partial.r130run")
        with ZipFile(base) as archive:
            manifest = TypeAdapter(dict[str, JsonValue]).validate_json(archive.read("manifest.json"))
        photo.update(run_id=manifest["run_id"], path=None, availability="unavailable", unavailable_reason="Source file missing")
        payloads = photo_payloads(photo, content)
        kind, material_id = "photo", str(photo["attachment_id"])
    elif state == "ambiguous":
        payloads = photo_payloads(photo, content)
        payloads["attachments/index.json"] = json.dumps({"schema_version": "r130sh.attachments.v1", "run_id": photo["run_id"], "attachments": [photo, photo]}).encode()
        kind, material_id = "photo", str(photo["attachment_id"])
    elif state == "empty_pdf":
        payloads = protocol_payloads()
        release = TypeAdapter(dict[str, JsonValue]).validate_json(payloads["protocol/release.json"])
        release["content_sha256"] = hashlib.sha256(b"").hexdigest()
        payloads["protocol/release.json"] = json.dumps(release).encode()
        payloads["protocol/protocol.pdf"] = b""
    package = build_synthetic_r130run(
        tmp_path / "source.r130run",
        base_package=base,
        payload_overrides=payloads,
        manifest_mutator=lambda value: value.update(package_kind="diagnostic_partial" if state == "unavailable" else "final"),
    )
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.get_imported_run_protocol(imported.local_import_id, None).origin
    directory = tmp_path / "viewer"
    directory.mkdir()
    try:
        with pytest.raises(ProjectOperationError) as error:
            service.resolve_imported_run_material(MaterialIdentity(origin=origin, kind=kind, material_id=material_id), directory, None)
        expected = {"not_included": "file_missing", "unavailable": "file_missing", "ambiguous": "validation_error", "empty_pdf": "unsupported_file_type"}
        assert error.value.code == expected[state]
        assert not list(directory.iterdir())
    finally:
        service.close()


def test_copy_preserves_an_existing_destination(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "source.r130run", payload_overrides=protocol_payloads())
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.get_imported_run_protocol(imported.local_import_id, None).origin
    directory = tmp_path / "viewer"
    directory.mkdir()
    copy_id = str(uuid4())
    destination = directory / f"{copy_id}.pdf"
    destination.write_bytes(b"foreign contents")
    try:
        with pytest.raises(ProjectOperationError) as error:
            service.resolve_imported_run_material(MaterialIdentity(origin=origin, kind="protocol", material_id="7"), directory, None, copy_id=copy_id)
        assert error.value.code == "storage_error"
        assert destination.read_bytes() == b"foreign contents"
        assert list(directory.iterdir()) == [destination]
    finally:
        service.close()


def test_real_worker_jsonl_material_chain_preserves_typed_metadata(tmp_path: Path) -> None:
    payloads = protocol_payloads()
    content = valid_material_content("application/pdf")
    release = TypeAdapter(dict[str, JsonValue]).validate_json(payloads["protocol/release.json"])
    release["content_sha256"] = hashlib.sha256(content).hexdigest()
    payloads["protocol/release.json"], payloads["protocol/protocol.pdf"] = json.dumps(release).encode(), content
    package = build_synthetic_r130run(tmp_path / "source.r130run", payload_overrides=payloads)
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    inspections = service.list_imported_run_inspection_page(imported.local_import_id, None, 25, None)
    inspection_id = inspections.items[0].material_id
    origin = inspections.origin.model_dump(mode="json")
    identity = MaterialIdentity(origin=inspections.origin, kind="protocol", material_id="7").model_dump(mode="json")
    service.close()
    before = (project_path / "project.sqlite").read_bytes()
    directory = tmp_path / "viewer"
    directory.mkdir()
    copy_id = str(uuid4())
    commands: list[tuple[str, dict[str, object]]] = [
        ("project.open", {"path": str(project_path), "applicationInstanceId": "materials-jsonl"}),
        ("importedRun.listInspectionPage", {"origin": origin, "limit": 1}),
        ("importedRun.getInspection", {"origin": origin, "inspectionId": inspection_id}),
        ("importedRun.listPhotoPage", {"origin": origin}),
        ("importedRun.getProtocol", {"origin": origin}),
        ("importedRun.resolveMaterial", {"identity": identity, "outputDirectory": str(directory), "copyId": copy_id}),
        ("importedRun.getProtocol", {"origin": {**origin, "exportRevision": 2}}),
        ("project.close", {}),
        ("system.shutdown", {}),
    ]
    requests = [
        json.dumps({"protocolVersion": 1, "requestId": str(index), "kind": "request", "operation": operation, "revision": index, "deadlineMs": 30000, "payload": payload})
        for index, (operation, payload) in enumerate(commands)
    ]
    environment = {**os.environ, "IMPELLER_STATE_DIR": str(tmp_path / "worker-state")}
    process = subprocess.Popen(
        [sys.executable, "-m", "impeller_reliability.worker.main"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=environment
    )
    stdout, stderr = process.communicate("\n".join(requests) + "\n", timeout=35)
    assert process.returncode == 0 and stderr == ""
    responses = [TypeAdapter(dict[str, object]).validate_json(line) for line in stdout.splitlines()]
    assert [response["requestId"] for response in responses] == [str(index) for index in range(len(commands))]
    assert [response["ok"] for response in responses] == [True, True, True, True, True, True, False, True, True]
    assert '"runElapsedS":"0"' in stdout and '"releaseId":"7"' in stdout and '"revisionNumber":"2"' in stdout
    for response in responses[1:5]:
        serialized = json.dumps(response)
        assert '"absolutePath"' not in serialized and '"path"' not in serialized
    assert (directory / f"{copy_id}.pdf").read_bytes() == content
    assert (project_path / "project.sqlite").read_bytes() == before


def test_copy_pins_destination_directory_before_any_material_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = build_synthetic_r130run(tmp_path / "source.r130run", payload_overrides=protocol_payloads())
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.get_imported_run_protocol(imported.local_import_id, None).origin
    directory, external, displaced = tmp_path / "viewer", tmp_path / "foreign", tmp_path / "displaced"
    directory.mkdir()
    external.mkdir()
    original = inspect_reserved_directory
    attempted = False
    blocked = False

    def replace_after_inspection(path: Path, label: str) -> None:
        nonlocal attempted, blocked
        original(path, label)
        if path != directory or label != "material copy directory" or attempted:
            return
        attempted = True
        try:
            directory.rename(displaced)
        except PermissionError:
            blocked = True
            return
        subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(directory), str(external)], check=True, capture_output=True, text=True)

    monkeypatch.setattr(r130sh_sources, "inspect_reserved_directory", replace_after_inspection)
    try:
        try:
            copied = service.resolve_imported_run_material(MaterialIdentity(origin=origin, kind="protocol", material_id="7"), directory, None)
            assert copied.absolute_path.is_file()
        except ProjectOperationError as error:
            assert error.code == "file_integrity_mismatch"
        assert attempted and blocked
        assert not list(external.iterdir())
    finally:
        service.close()
        if directory.is_junction():
            directory.rmdir()


def test_copy_denies_file_write_and_replacement_through_final_source_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = build_synthetic_r130run(tmp_path / "source.r130run", payload_overrides=protocol_payloads())
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.get_imported_run_protocol(imported.local_import_id, None).origin
    directory = tmp_path / "viewer"
    directory.mkdir()
    copy_id = str(uuid4())
    copied_path = directory / f"{copy_id}.pdf"
    original = RequestDeadline.check
    blocked: list[str] = []

    def interfere(deadline: RequestDeadline, operation: str) -> None:
        if operation == "material_source_hash" and copied_path.exists() and not blocked:
            try:
                copied_path.write_bytes(b"foreign replacement")
            except PermissionError:
                blocked.append("write")
            try:
                copied_path.rename(directory / "foreign.pdf")
            except PermissionError:
                blocked.append("rename")
        original(deadline, operation)

    monkeypatch.setattr(RequestDeadline, "check", interfere)
    try:
        copied = service.resolve_imported_run_material(MaterialIdentity(origin=origin, kind="protocol", material_id="7"), directory, None, copy_id=copy_id)
        assert blocked == ["write", "rename"]
        assert copied.absolute_path.read_bytes() == protocol_payloads()["protocol/protocol.pdf"]
        assert len(list(directory.iterdir())) == 1
    finally:
        service.close()


def test_copy_failure_deletes_owned_handle_without_a_path_unlink_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = build_synthetic_r130run(tmp_path / "source.r130run", payload_overrides=protocol_payloads())
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.get_imported_run_protocol(imported.local_import_id, None).origin
    directory = tmp_path / "viewer"
    directory.mkdir()
    foreign = directory / "foreign.txt"
    foreign.write_bytes(b"preserve")
    copy_id = str(uuid4())
    copied_path = directory / f"{copy_id}.pdf"
    displaced = directory / "displaced.pdf"
    now = [0.0]
    deadline = RequestDeadline(expires_at=30, _clock=lambda: now[0])
    original_check, original_unlink = RequestDeadline.check, Path.unlink
    path_cleanup_attempted = False

    def expire_during_final_hash(value: RequestDeadline, operation: str) -> None:
        if operation == "material_source_hash" and copied_path.exists():
            now[0] = 31
        original_check(value, operation)

    def replace_at_unlink(path: Path, missing_ok: bool = False) -> None:
        nonlocal path_cleanup_attempted
        if path == copied_path:
            path_cleanup_attempted = True
            path.rename(displaced)
            path.write_bytes(b"foreign replacement")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(RequestDeadline, "check", expire_during_final_hash)
    monkeypatch.setattr(Path, "unlink", replace_at_unlink)
    try:
        with pytest.raises(ProjectOperationError) as error:
            service.resolve_imported_run_material(MaterialIdentity(origin=origin, kind="protocol", material_id="7"), directory, deadline, copy_id=copy_id)
        assert error.value.code == "timeout"
        assert not path_cleanup_attempted
        assert not copied_path.exists() and not displaced.exists()
        assert list(directory.iterdir()) == [foreign] and foreign.read_bytes() == b"preserve"
    finally:
        service.close()


@pytest.mark.parametrize("failure", ["descriptor", "buffer"])
def test_copy_wrapper_failure_disposes_created_native_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    package = build_synthetic_r130run(tmp_path / "source.r130run", payload_overrides=protocol_payloads())
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.get_imported_run_protocol(imported.local_import_id, None).origin
    directory = tmp_path / "viewer"
    directory.mkdir()
    original = msvcrt.open_osfhandle

    def fail_descriptor(handle: int, flags: int) -> int:
        if flags & os.O_RDWR:
            raise OSError("descriptor allocation failed")
        return original(handle, flags)

    def fail_buffer(_raw: io.FileIO) -> io.BufferedRandom:
        raise OSError("buffer allocation failed")

    if failure == "descriptor":
        monkeypatch.setattr(msvcrt, "open_osfhandle", fail_descriptor)
    else:
        monkeypatch.setattr(io, "BufferedRandom", fail_buffer)
    try:
        with pytest.raises(ProjectOperationError) as error:
            service.resolve_imported_run_material(MaterialIdentity(origin=origin, kind="protocol", material_id="7"), directory, None)
        assert error.value.code == "storage_error"
        assert not list(directory.iterdir())
    finally:
        service.close()


def test_missing_copy_destination_does_not_report_source_zip_damage(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "source.r130run", payload_overrides=protocol_payloads())
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.get_imported_run_protocol(imported.local_import_id, None).origin
    try:
        with pytest.raises(ProjectOperationError) as error:
            service.resolve_imported_run_material(MaterialIdentity(origin=origin, kind="protocol", material_id="7"), tmp_path / "missing", None)
        assert error.value.code == "storage_error"
        assert service.get_imported_run_protocol(imported.local_import_id, None).item.state == "verified"
    finally:
        service.close()


def test_native_discard_uses_exact_identity_without_project_session(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "source.r130run", payload_overrides=protocol_payloads())
    service, project_path = create_import_project(tmp_path)
    imported = import_run_package(service, project_path, package)
    origin = service.get_imported_run_protocol(imported.local_import_id, None).origin
    directory = tmp_path / "viewer"
    directory.mkdir()
    copy_id = str(uuid4())
    copied = service.resolve_imported_run_material(MaterialIdentity(origin=origin, kind="protocol", material_id="7"), directory, None, copy_id=copy_id)
    service.close()
    assert len(copied.file_identity.file_id) == 32 and len(copied.file_identity.volume_id) == 16
    assert int(copied.file_identity.file_id, 16) == copied.absolute_path.stat().st_ino
    assert int(copied.file_identity.volume_id, 16) == copied.absolute_path.stat().st_dev
    assert discard_material_copy(directory, copy_id, "application/pdf", copied.file_identity, RequestDeadline.start(5000))
    assert not copied.absolute_path.exists()
    assert not discard_material_copy(directory, copy_id, "application/pdf", copied.file_identity, RequestDeadline.start(5000))


def test_native_discard_preserves_a_foreign_file_identity(tmp_path: Path) -> None:
    from impeller_reliability.persistence.project_paths import ManagedFileIdentity

    directory = tmp_path / "viewer"
    directory.mkdir()
    copy_id = str(uuid4())
    foreign = directory / f"{copy_id}.pdf"
    foreign.write_bytes(b"foreign bytes")
    with pytest.raises(ProjectOperationError) as error:
        discard_material_copy(directory, copy_id, "application/pdf", ManagedFileIdentity("0" * 16, "0" * 31 + "1"), RequestDeadline.start(5000))
    assert error.value.code == "file_integrity_mismatch"
    assert foreign.read_bytes() == b"foreign bytes"


@pytest.mark.parametrize("copy_id", ["../outside", "C:\\outside", "\\\\host\\share", "https://host/file", str(uuid4()).upper()])
def test_native_discard_rejects_noncanonical_copy_ids(tmp_path: Path, copy_id: str) -> None:
    from impeller_reliability.persistence.project_paths import ManagedFileIdentity

    foreign = tmp_path / "outside"
    foreign.write_bytes(b"foreign bytes")
    with pytest.raises(ProjectOperationError) as error:
        discard_material_copy(tmp_path, copy_id, "application/pdf", ManagedFileIdentity("0" * 16, "0" * 31 + "1"), RequestDeadline.start(5000))
    assert error.value.code == "validation_error"
    assert foreign.read_bytes() == b"foreign bytes"


def test_native_discard_refuses_hardlink_even_with_matching_identity(tmp_path: Path) -> None:
    copy_id = str(uuid4())
    path = tmp_path / f"{copy_id}.pdf"
    path.write_bytes(b"foreign bytes")
    outside = tmp_path / "outside.pdf"
    os.link(path, outside)
    with path.open("rb") as stream:
        identity = opened_file_identity(stream.fileno())
    with pytest.raises(ProjectOperationError) as error:
        discard_material_copy(tmp_path, copy_id, "application/pdf", identity, RequestDeadline.start(5000))
    assert error.value.code == "file_integrity_mismatch"
    assert path.read_bytes() == outside.read_bytes() == b"foreign bytes"


def test_native_discard_refuses_junction_destination(tmp_path: Path) -> None:
    copy_id = str(uuid4())
    outside = tmp_path / "outside"
    outside.mkdir()
    path = outside / f"{copy_id}.pdf"
    path.write_bytes(b"foreign bytes")
    with path.open("rb") as stream:
        identity = opened_file_identity(stream.fileno())
    junction = tmp_path / "viewer"
    subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)], check=True, capture_output=True, text=True)
    try:
        with pytest.raises(ProjectOperationError) as error:
            discard_material_copy(junction, copy_id, "application/pdf", identity, RequestDeadline.start(5000))
        assert error.value.code == "validation_error"
        assert path.read_bytes() == b"foreign bytes"
    finally:
        junction.rmdir()


def test_native_discard_obeys_deadline_before_disposition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copy_id = str(uuid4())
    path = tmp_path / f"{copy_id}.pdf"
    path.write_bytes(b"copy bytes")
    with path.open("rb") as stream:
        identity = opened_file_identity(stream.fileno())
    original = RequestDeadline.check

    def expire_at_commit(deadline: RequestDeadline, operation: str) -> None:
        if operation == "material_copy_discard_commit":
            raise ProjectOperationError("timeout", "Expired before disposal")
        original(deadline, operation)

    monkeypatch.setattr(RequestDeadline, "check", expire_at_commit)
    with pytest.raises(ProjectOperationError) as error:
        discard_material_copy(tmp_path, copy_id, "application/pdf", identity, RequestDeadline.start(5000))
    assert error.value.code == "timeout"
    assert path.read_bytes() == b"copy bytes"


def test_native_discard_failure_has_no_path_delete_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copy_id = str(uuid4())
    path = tmp_path / f"{copy_id}.pdf"
    path.write_bytes(b"copy bytes")
    with path.open("rb") as stream:
        identity = opened_file_identity(stream.fileno())

    def fail_disposition(_descriptor: int) -> None:
        raise PermissionError("Disposition denied")

    def forbid_unlink(_path: Path, *, missing_ok: bool = False) -> None:
        pytest.fail("Native disposal must never fall back to deleting a path")

    monkeypatch.setattr(material_copies, "discard_opened_managed_file", fail_disposition)
    with monkeypatch.context() as guarded:
        guarded.setattr(Path, "unlink", forbid_unlink)
        with pytest.raises(ProjectOperationError) as error:
            discard_material_copy(tmp_path, copy_id, "application/pdf", identity, RequestDeadline.start(5000))
    assert error.value.code == "storage_error"
    assert path.read_bytes() == b"copy bytes"
