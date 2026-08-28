from __future__ import annotations

from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import subprocess
from uuid import uuid4
import zipfile

import pytest

from impeller_reliability.application.project_service import ProjectService
from impeller_reliability.persistence.audit import insert_audit
from impeller_reliability.persistence.case_documents import COPY_CHUNK_BYTES, CaseDocumentRepository
from impeller_reliability.persistence.project_database import (
    configure_project_connection,
)
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.timestamps import utc_now
from impeller_reliability.worker.deadline import RequestDeadline


def _create_project(path: Path) -> ProjectService:
    service = ProjectService()
    service.create(
        path=str(path),
        application_instance_id=str(uuid4()),
        application_version="0.1.0",
        name="Дело",
        project_number="D-1",
        description="",
        status="draft",
    )
    return service


def _open_repository(project_path: Path) -> tuple[sqlite3.Connection, CaseDocumentRepository]:
    connection = sqlite3.connect(project_path / "project.sqlite")
    connection.row_factory = sqlite3.Row
    configure_project_connection(connection)
    return connection, CaseDocumentRepository(connection, project_path)


def test_new_project_initializes_complete_schema_v1_without_migration_backup(tmp_path: Path) -> None:
    project_path = tmp_path / "case-documents.irproj"
    service = _create_project(project_path)
    try:
        assert service.get_overview().schema_version == 1
    finally:
        service.close()

    assert list((project_path / "backups").iterdir()) == []

    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == 1
        objects = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'case_document%'")}
        assert {
            "case_documents",
            "case_document_files",
            "case_document_wheel_models",
            "case_document_specimens",
            "case_document_files_no_update",
            "case_document_files_no_delete",
        } <= objects


def test_case_document_file_rows_are_immutable_in_schema_v1(tmp_path: Path) -> None:
    project_path = tmp_path / "immutable-file.irproj"
    service = _create_project(project_path)
    service.close()

    document_id = str(uuid4())
    now = utc_now()
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO case_documents (case_document_id, document_kind, title, record_revision, created_at_utc, updated_at_utc) VALUES (?, 'standard', 'ГОСТ', 1, ?, ?)",
            (document_id, now, now),
        )
        connection.execute(
            "INSERT INTO case_document_files (case_document_id, original_file_name, stored_relative_path, media_type, size_bytes, sha256, attached_at_utc) VALUES (?, 'gost.pdf', ?, 'application/pdf', 5, ?, ?)",
            (
                document_id,
                f"assets/documents/{document_id}/{'a' * 64}.pdf",
                "a" * 64,
                now,
            ),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="case_document_file_immutable"):
            connection.execute(
                "UPDATE case_document_files SET original_file_name = 'changed.pdf' WHERE case_document_id = ?",
                (document_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="case_document_file_immutable"):
            connection.execute(
                "DELETE FROM case_document_files WHERE case_document_id = ?",
                (document_id,),
            )


def test_metadata_document_create_retry_update_links_archive_and_audit(tmp_path: Path) -> None:
    project_path = tmp_path / "metadata-document.irproj"
    service = _create_project(project_path)
    wheel = service.create_wheel(
        {
            "wheelModelId": str(uuid4()),
            "fullName": "Колесо",
            "designation": "ВР-1",
            "nominalDiameterMm": None,
            "nominalSpeedRpm": None,
            "bladeCount": None,
            "geometryDescription": "",
            "compositionDescription": "",
            "materialDescription": "",
            "notes": "",
        },
        deadline=None,
    )
    specimen = service.create_specimen(
        {
            "specimenId": str(uuid4()),
            "wheelModelId": wheel.wheel_model_id,
            "identificationNumber": "SN-1",
            "batchNumber": "",
            "marking": "",
            "manufacturedOn": None,
            "receivedOn": None,
            "workingDiameterMm": None,
            "initialConditionNotes": "",
            "notes": "",
        },
        deadline=None,
    )
    service.close()

    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    values = {
        "documentKind": "standard",
        "title": "  ГОСТ 123  ",
        "designation": "",
        "revisionLabel": "",
        "documentDate": "2026-08-28",
        "issuer": " Росстандарт ",
        "notes": "",
    }
    try:
        created = repository.create(
            document_id=document_id,
            values=values,
            wheel_model_ids=(wheel.wheel_model_id,),
            specimen_ids=(specimen.specimen_id,),
            deadline=None,
        )
        assert created.record_revision == 1
        assert created.title == "ГОСТ 123"
        assert created.issuer == "Росстандарт"
        assert created.integrity_status == "not_attached"
        assert created.wheel_model_ids == (wheel.wheel_model_id,)
        assert created.specimen_ids == (specimen.specimen_id,)
        assert created.warnings == (
            "case_document_file_missing",
            "case_document_designation_missing",
            "case_document_revision_missing",
        )

        retried = repository.create(
            document_id=document_id,
            values=values,
            wheel_model_ids=(wheel.wheel_model_id,),
            specimen_ids=(specimen.specimen_id,),
            deadline=None,
        )
        assert retried == created

        unchanged = repository.update(
            document_id=document_id,
            expected_revision=1,
            values=values,
            wheel_model_ids=(wheel.wheel_model_id,),
            specimen_ids=(specimen.specimen_id,),
            deadline=None,
        )
        assert unchanged.record_revision == 1

        updated = repository.update(
            document_id=document_id,
            expected_revision=1,
            values={**values, "designation": "ГОСТ 123", "revisionLabel": "Ред. 1"},
            wheel_model_ids=(),
            specimen_ids=(specimen.specimen_id,),
            deadline=None,
        )
        assert updated.record_revision == 2
        assert updated.wheel_model_ids == ()
        assert updated.warnings == ("case_document_file_missing",)

        with pytest.raises(ProjectOperationError) as conflict:
            repository.update(
                document_id=document_id,
                expected_revision=1,
                values=values,
                wheel_model_ids=(),
                specimen_ids=(),
                deadline=None,
            )
        assert conflict.value.code == "revision_conflict"

        archived = repository.set_archived(
            document_id=document_id,
            expected_revision=2,
            archived=True,
            deadline=None,
        )
        assert archived.record_revision == 3
        assert archived.archived_at_utc is not None
        assert repository.list(include_archived=False, document_kind=None) == ()
        assert repository.list(include_archived=True, document_kind="standard") == (archived,)

        restored = repository.set_archived(
            document_id=document_id,
            expected_revision=3,
            archived=False,
            deadline=None,
        )
        assert restored.record_revision == 4
        events = connection.execute("SELECT event_type FROM project_audit_events WHERE event_type LIKE 'case_document.%' ORDER BY sequence").fetchall()
        assert [str(row[0]) for row in events] == [
            "case_document.created",
            "case_document.updated",
            "case_document.archived",
            "case_document.restored",
        ]
    finally:
        connection.close()


def test_document_applicability_requires_existing_targets(tmp_path: Path) -> None:
    project_path = tmp_path / "missing-target.irproj"
    service = _create_project(project_path)
    service.close()
    connection, repository = _open_repository(project_path)
    try:
        with pytest.raises(ProjectOperationError) as raised:
            repository.create(
                document_id=str(uuid4()),
                values={
                    "documentKind": "other",
                    "title": "Материал",
                    "designation": "",
                    "revisionLabel": "",
                    "documentDate": None,
                    "issuer": "",
                    "notes": "",
                },
                wheel_model_ids=(str(uuid4()),),
                specimen_ids=(),
                deadline=None,
            )
        assert raised.value.code == "entity_not_found"
    finally:
        connection.close()


def _document_values(title: str = "Документ") -> dict[str, object]:
    return {
        "documentKind": "other",
        "title": title,
        "designation": "",
        "revisionLabel": "",
        "documentDate": None,
        "issuer": "",
        "notes": "",
    }


def test_attach_file_once_retry_duplicate_content_and_audit_privacy(tmp_path: Path) -> None:
    project_path = tmp_path / "managed-file.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / "evidence.pdf"
    source.write_bytes(b"%PDF-1.7\nsynthetic evidence\n")

    connection, repository = _open_repository(project_path)
    first_id = str(uuid4())
    second_id = str(uuid4())
    try:
        repository.create(
            document_id=first_id,
            values=_document_values("Первый"),
            wheel_model_ids=(),
            specimen_ids=(),
            deadline=None,
        )
        attached = repository.attach_file(
            document_id=first_id,
            expected_revision=1,
            source_path=source,
            deadline=None,
        )
        assert attached.record_revision == 2
        assert attached.integrity_status == "verified"
        assert attached.file is not None
        assert attached.file.original_file_name == "evidence.pdf"
        assert attached.file.size_bytes == source.stat().st_size
        managed = project_path.joinpath(*attached.file.stored_relative_path.split("/"))
        assert managed.read_bytes() == source.read_bytes()

        retried = repository.attach_file(
            document_id=first_id,
            expected_revision=1,
            source_path=source,
            deadline=None,
        )
        assert retried == attached

        different = tmp_path / "different.pdf"
        different.write_bytes(b"%PDF-1.7\ndifferent\n")
        with pytest.raises(ProjectOperationError) as already_attached:
            repository.attach_file(
                document_id=first_id,
                expected_revision=2,
                source_path=different,
                deadline=None,
            )
        assert already_attached.value.code == "file_already_attached"

        repository.create(
            document_id=second_id,
            values=_document_values("Второй"),
            wheel_model_ids=(),
            specimen_ids=(),
            deadline=None,
        )
        with pytest.raises(ProjectOperationError) as duplicate:
            repository.attach_file(
                document_id=second_id,
                expected_revision=1,
                source_path=source,
                deadline=None,
            )
        assert duplicate.value.code == "duplicate_document_content"
        assert repository.get(second_id).file is None

        payload = str(connection.execute("SELECT payload_json FROM project_audit_events WHERE event_type='case_document.file_attached'").fetchone()[0])
        assert str(source) not in payload
        assert "evidence.pdf" in payload
        assert len(list((project_path / "assets" / "documents").rglob("*.part"))) == 0
    finally:
        connection.close()


def test_verify_and_resolve_report_missing_and_modified_without_changing_registry(tmp_path: Path) -> None:
    project_path = tmp_path / "integrity.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / "evidence.txt"
    source.write_text("Доказательство", encoding="utf-8")
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    try:
        repository.create(
            document_id=document_id,
            values=_document_values(),
            wheel_model_ids=(),
            specimen_ids=(),
            deadline=None,
        )
        attached = repository.attach_file(
            document_id=document_id,
            expected_revision=1,
            source_path=source,
            deadline=None,
        )
        assert repository.verify_file(document_id, deadline=None).integrity_status == "verified"
        resolved = repository.resolve_file(document_id, deadline=None)
        assert resolved.is_absolute()
        assert resolved.read_bytes() == source.read_bytes()

        assert attached.file is not None
        managed = project_path.joinpath(*attached.file.stored_relative_path.split("/"))
        managed.write_text("Изменено", encoding="utf-8")
        modified = repository.verify_file(document_id, deadline=None)
        assert modified.integrity_status == "modified"
        assert modified.file is not None and modified.file.sha256 == attached.file.sha256
        with pytest.raises(ProjectOperationError) as mismatch:
            repository.resolve_file(document_id, deadline=None)
        assert mismatch.value.code == "file_integrity_mismatch"

        managed.unlink()
        missing = repository.verify_file(document_id, deadline=None)
        assert missing.integrity_status == "missing"
        assert "case_document_file_missing" in missing.warnings
        listed = repository.list(include_archived=False, document_kind=None)
        assert listed[0].warnings[0] == "case_document_file_missing"
        with pytest.raises(ProjectOperationError) as absent:
            repository.resolve_file(document_id, deadline=None)
        assert absent.value.code == "file_missing"

        row = connection.execute(
            "SELECT sha256, stored_relative_path FROM case_document_files WHERE case_document_id=?",
            (document_id,),
        ).fetchone()
        assert row is not None and str(row[0]) == attached.file.sha256
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("name", "content", "code"),
    [
        ("script.ps1", b"Write-Host unsafe", "unsupported_file_type"),
        ("invalid.pdf", b"not a pdf", "unsupported_file_type"),
        ("invalid.png", b"not a png", "unsupported_file_type"),
        ("invalid.txt", b"\xff", "unsupported_file_type"),
    ],
)
def test_attach_rejects_unsupported_or_invalid_content(
    tmp_path: Path,
    name: str,
    content: bytes,
    code: str,
) -> None:
    project_path = tmp_path / f"invalid-{name}.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / name
    source.write_bytes(content)
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    try:
        repository.create(
            document_id=document_id,
            values=_document_values(),
            wheel_model_ids=(),
            specimen_ids=(),
            deadline=None,
        )
        with pytest.raises(ProjectOperationError) as raised:
            repository.attach_file(
                document_id=document_id,
                expected_revision=1,
                source_path=source,
                deadline=None,
            )
        assert raised.value.code == code
        assert repository.get(document_id).file is None
    finally:
        connection.close()


def test_attach_rejects_file_over_100_mib_before_copy(tmp_path: Path) -> None:
    project_path = tmp_path / "oversized.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / "oversized.pdf"
    with source.open("wb") as stream:
        stream.write(b"%PDF-")
        stream.truncate(100 * 1024 * 1024 + 1)
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    try:
        repository.create(
            document_id=document_id,
            values=_document_values(),
            wheel_model_ids=(),
            specimen_ids=(),
            deadline=None,
        )
        with pytest.raises(ProjectOperationError) as raised:
            repository.attach_file(
                document_id=document_id,
                expected_revision=1,
                source_path=source,
                deadline=None,
            )
        assert raised.value.code == "file_too_large"
    finally:
        connection.close()


def test_create_with_file_is_atomic_and_idempotent_at_revision_one(tmp_path: Path) -> None:
    project_path = tmp_path / "create-with-file.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / "request.json"
    source.write_text('{"request": "synthetic"}', encoding="utf-8")
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    try:
        created = repository.create_with_file(
            document_id=document_id,
            values={**_document_values(), "documentKind": "test_request"},
            wheel_model_ids=(),
            specimen_ids=(),
            source_path=source,
            deadline=None,
        )
        assert created.record_revision == 1
        assert created.integrity_status == "verified"
        retried = repository.create_with_file(
            document_id=document_id,
            values={**_document_values(), "documentKind": "test_request"},
            wheel_model_ids=(),
            specimen_ids=(),
            source_path=source,
            deadline=None,
        )
        assert retried == created
        events = connection.execute("SELECT event_type FROM project_audit_events WHERE event_type LIKE 'case_document.%'").fetchall()
        assert [str(row[0]) for row in events] == ["case_document.created"]
    finally:
        connection.close()


def test_recovery_removes_only_exact_unregistered_managed_orphans(tmp_path: Path) -> None:
    project_path = tmp_path / "recovery.irproj"
    service = _create_project(project_path)
    service.close()
    connection, repository = _open_repository(project_path)
    managed_root = project_path / "assets" / "documents"
    staging = managed_root / ".staging"
    staging.mkdir(exist_ok=True)
    stale_part = staging / f"{uuid4()}.part"
    stale_part.write_bytes(b"partial")
    unknown_part = staging / "keep-me.part"
    unknown_part.write_bytes(b"unknown")
    orphan_id = str(uuid4())
    orphan_dir = managed_root / orphan_id
    orphan_dir.mkdir()
    orphan_file = orphan_dir / f"{'b' * 64}.pdf"
    orphan_file.write_bytes(b"%PDF-orphan")
    unknown_file = orphan_dir / "keep-me.bin"
    unknown_file.write_bytes(b"unknown")
    try:
        repository.recover_managed_files()
        assert not stale_part.exists()
        assert unknown_part.exists()
        assert not orphan_file.exists()
        assert unknown_file.exists()
    finally:
        connection.close()


def test_recovery_obeys_project_open_deadline(tmp_path: Path) -> None:
    project_path = tmp_path / "recovery-deadline.irproj"
    service = _create_project(project_path)
    service.close()
    connection, repository = _open_repository(project_path)
    try:
        with pytest.raises(ProjectOperationError) as raised:
            repository.recover_managed_files(RequestDeadline.start(0))
        assert raised.value.code == "timeout"
        assert raised.value.details == {"stage": "case_document_recovery_start"}
    finally:
        connection.close()


def test_attached_file_noop_mutations_return_fresh_integrity_status(tmp_path: Path) -> None:
    project_path = tmp_path / "noop-integrity.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nnoop integrity\n")
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    try:
        created = repository.create_with_file(
            document_id=document_id,
            values=_document_values(),
            wheel_model_ids=(),
            specimen_ids=(),
            source_path=source,
            deadline=None,
        )
        unchanged = repository.update(
            document_id=document_id,
            expected_revision=1,
            values=_document_values(),
            wheel_model_ids=(),
            specimen_ids=(),
            deadline=None,
        )
        assert unchanged.record_revision == 1
        assert unchanged.integrity_status == "verified"
        updated = repository.update(
            document_id=document_id,
            expected_revision=1,
            values={**_document_values(), "notes": "Metadata changed"},
            wheel_model_ids=(),
            specimen_ids=(),
            deadline=None,
        )
        assert updated.record_revision == 2
        assert updated.integrity_status == "verified"
        archived = repository.set_archived(
            document_id=document_id,
            expected_revision=2,
            archived=True,
            deadline=None,
        )
        assert archived.record_revision == 3
        assert archived.integrity_status == "verified"
        repeated = repository.set_archived(
            document_id=document_id,
            expected_revision=3,
            archived=True,
            deadline=None,
        )
        assert repeated.record_revision == 3
        assert repeated.integrity_status == "verified"
        assert created.file == repeated.file
    finally:
        connection.close()


def test_project_reopens_when_registered_document_file_is_missing(tmp_path: Path) -> None:
    project_path = tmp_path / "reopen-missing.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nreopen\n")
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    try:
        document = repository.create_with_file(
            document_id=document_id,
            values=_document_values(),
            wheel_model_ids=(),
            specimen_ids=(),
            source_path=source,
            deadline=None,
        )
        assert document.file is not None
        project_path.joinpath(*document.file.stored_relative_path.split("/")).unlink()
    finally:
        connection.close()

    reopened = ProjectService()
    try:
        assert (
            reopened.open(
                path=str(project_path),
                application_instance_id=str(uuid4()),
            ).schema_version
            == 1
        )
    finally:
        reopened.close()


def test_verify_file_reports_timeout_from_streaming_hash_stage(tmp_path: Path) -> None:
    project_path = tmp_path / "verify-deadline.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / "large.pdf"
    source.write_bytes(b"%PDF-1.7\n" + b"x" * (COPY_CHUNK_BYTES * 2))
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    try:
        repository.create_with_file(
            document_id=document_id,
            values=_document_values(),
            wheel_model_ids=(),
            specimen_ids=(),
            source_path=source,
            deadline=None,
        )
        tick = 0.0

        def clock() -> float:
            nonlocal tick
            tick += 0.0002
            return tick

        deadline = RequestDeadline.start(1, clock=clock)
        with pytest.raises(ProjectOperationError) as raised:
            repository.verify_file(document_id, deadline=deadline)
        assert raised.value.code == "timeout"
        assert raised.value.details == {"stage": "case_document_file_verify"}
    finally:
        connection.close()


def _office_bytes(required_member: str) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr(required_member, "<document />")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("name", "content", "media_type"),
    [
        ("method.docx", _office_bytes("word/document.xml"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("measurements.xlsx", _office_bytes("xl/workbook.xml"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("scan.png", b"\x89PNG\r\n\x1a\nsynthetic", "image/png"),
        ("photo.jpg", b"\xff\xd8\xffsynthetic", "image/jpeg"),
        ("table.csv", "параметр,значение\nскорость,1500\n".encode(), "text/csv"),
    ],
)
def test_attach_accepts_allowlisted_office_image_and_text_signatures(
    tmp_path: Path,
    name: str,
    content: bytes,
    media_type: str,
) -> None:
    project_path = tmp_path / f"valid-{Path(name).suffix[1:]}.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / name
    source.write_bytes(content)
    connection, repository = _open_repository(project_path)
    try:
        document = repository.create_with_file(
            document_id=str(uuid4()),
            values=_document_values(name),
            wheel_model_ids=(),
            specimen_ids=(),
            source_path=source,
            deadline=None,
        )
        assert document.integrity_status == "verified"
        assert document.file is not None and document.file.media_type == media_type
    finally:
        connection.close()


def test_attach_timeout_cleans_staging_and_does_not_register_file(tmp_path: Path) -> None:
    project_path = tmp_path / "attach-deadline.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / "large.pdf"
    source.write_bytes(b"%PDF-1.7\n" + b"x" * (COPY_CHUNK_BYTES * 2))
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    repository.create(
        document_id=document_id,
        values=_document_values(),
        wheel_model_ids=(),
        specimen_ids=(),
        deadline=None,
    )
    tick = 0.0

    def clock() -> float:
        nonlocal tick
        tick += 0.0002
        return tick

    try:
        with pytest.raises(ProjectOperationError) as raised:
            repository.attach_file(
                document_id=document_id,
                expected_revision=1,
                source_path=source,
                deadline=RequestDeadline.start(1, clock=clock),
            )
        assert raised.value.code == "timeout"
        assert raised.value.details == {"stage": "case_document_file_copy"}
        assert repository.get(document_id).file is None
        assert list((project_path / "assets" / "documents" / ".staging").glob("*.part")) == []
    finally:
        connection.close()


def test_database_failure_after_rename_rolls_back_create_and_attach_files(tmp_path: Path) -> None:
    project_path = tmp_path / "file-db-failure.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\ndatabase failure\n")
    connection, repository = _open_repository(project_path)
    attach_document_id = str(uuid4())
    create_document_id = str(uuid4())
    try:
        repository.create(
            document_id=attach_document_id,
            values=_document_values("Attach target"),
            wheel_model_ids=(),
            specimen_ids=(),
            deadline=None,
        )
        connection.execute(
            """
            CREATE TRIGGER injected_case_document_file_failure
            BEFORE INSERT ON case_document_files
            BEGIN
              SELECT RAISE(ABORT, 'injected_file_insert_failure');
            END
            """
        )
        for operation in ("create", "attach"):
            with pytest.raises(ProjectOperationError) as raised:
                if operation == "create":
                    repository.create_with_file(
                        document_id=create_document_id,
                        values=_document_values("Create target"),
                        wheel_model_ids=(),
                        specimen_ids=(),
                        source_path=source,
                        deadline=None,
                    )
                else:
                    repository.attach_file(
                        document_id=attach_document_id,
                        expected_revision=1,
                        source_path=source,
                        deadline=None,
                    )
            assert raised.value.code == "storage_error"

        with pytest.raises(ProjectOperationError) as missing:
            repository.get(create_document_id)
        assert missing.value.code == "entity_not_found"
        attach_target = repository.get(attach_document_id)
        assert attach_target.record_revision == 1 and attach_target.file is None
        managed_root = project_path / "assets" / "documents"
        assert list(managed_root.rglob("*.pdf")) == []
        assert list((managed_root / ".staging").glob("*.part")) == []
    finally:
        connection.close()


def test_resolve_rejects_registry_path_outside_document_root(tmp_path: Path) -> None:
    project_path = tmp_path / "containment.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\ncontainment\n")
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    try:
        document = repository.create_with_file(
            document_id=document_id,
            values=_document_values(),
            wheel_model_ids=(),
            specimen_ids=(),
            source_path=source,
            deadline=None,
        )
        assert document.file is not None
        connection.execute("DROP TRIGGER case_document_files_no_update")
        connection.execute(
            "UPDATE case_document_files SET stored_relative_path=? WHERE case_document_id=?",
            (f"assets/documents/{document_id}/../{'a' * 64}.pdf", document_id),
        )
        connection.commit()
        with pytest.raises(ProjectOperationError) as raised:
            repository.resolve_file(document_id, deadline=None)
        assert raised.value.code == "file_integrity_mismatch"
    finally:
        connection.close()


def test_project_reopen_validates_complete_document_audit_chain(tmp_path: Path) -> None:
    project_path = tmp_path / "audit-chain.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / "audit.pdf"
    source.write_bytes(b"%PDF-1.7\naudit chain\n")
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    try:
        repository.create(
            document_id=document_id,
            values=_document_values("Исходное название"),
            wheel_model_ids=(),
            specimen_ids=(),
            deadline=None,
        )
        repository.update(
            document_id=document_id,
            expected_revision=1,
            values={**_document_values("Уточнённое название"), "designation": "ТУ-42"},
            wheel_model_ids=(),
            specimen_ids=(),
            deadline=None,
        )
        repository.attach_file(
            document_id=document_id,
            expected_revision=2,
            source_path=source,
            deadline=None,
        )
        repository.set_archived(
            document_id=document_id,
            expected_revision=3,
            archived=True,
            deadline=None,
        )
        repository.set_archived(
            document_id=document_id,
            expected_revision=4,
            archived=False,
            deadline=None,
        )
    finally:
        connection.close()

    reopened = ProjectService()
    try:
        reopened.open(path=str(project_path), application_instance_id=str(uuid4()))
        document = reopened.get_case_document(document_id, deadline=None)
        assert document.record_revision == 5
        assert document.title == "Уточнённое название"
        assert document.file is not None
    finally:
        reopened.close()


def test_project_reopen_rejects_tampered_document_audit_payload(tmp_path: Path) -> None:
    project_path = tmp_path / "tampered-audit.irproj"
    service = _create_project(project_path)
    service.close()
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    try:
        repository.create(
            document_id=document_id,
            values=_document_values(),
            wheel_model_ids=(),
            specimen_ids=(),
            deadline=None,
        )
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='project_audit_events_no_update'",
        ).fetchone()
        assert trigger_sql is not None and isinstance(trigger_sql[0], str)
        connection.execute("DROP TRIGGER project_audit_events_no_update")
        connection.execute(
            "UPDATE project_audit_events SET payload_json='{}' WHERE event_type='case_document.created'",
        )
        connection.execute(str(trigger_sql[0]))
        connection.commit()
    finally:
        connection.close()

    reopened = ProjectService()
    try:
        with pytest.raises(ProjectOperationError) as raised:
            reopened.open(path=str(project_path), application_instance_id=str(uuid4()))
        assert raised.value.code == "corrupt_project"
    finally:
        reopened.close()


def test_all_published_document_kinds_round_trip(tmp_path: Path) -> None:
    document_kinds = (
        "technical_specification",
        "individual_test_method",
        "typical_test_method",
        "customer_requirement",
        "test_request",
        "operational_documentation",
        "standard",
        "drawing",
        "measurement_or_attestation_record",
        "other",
    )
    project_path = tmp_path / "document-kinds.irproj"
    service = _create_project(project_path)
    service.close()
    connection, repository = _open_repository(project_path)
    try:
        for document_kind in document_kinds:
            document = repository.create(
                document_id=str(uuid4()),
                values={**_document_values(document_kind), "documentKind": document_kind},
                wheel_model_ids=(),
                specimen_ids=(),
                deadline=None,
            )
            assert document.document_kind == document_kind
    finally:
        connection.close()


def test_audit_writer_rejects_payload_larger_than_published_schema_limit(tmp_path: Path) -> None:
    project_path = tmp_path / "large-audit.irproj"
    service = _create_project(project_path)
    service.close()
    connection, _repository = _open_repository(project_path)
    try:
        with pytest.raises(ProjectOperationError) as raised:
            insert_audit(
                connection,
                event_type="case_document.created",
                actor_kind="user",
                occurred_at_utc=utc_now(),
                payload={"value": "x" * 250_000},
            )
        assert raised.value.code == "validation_error"
    finally:
        connection.close()


def test_project_reopen_rejects_noop_document_audit_revision(tmp_path: Path) -> None:
    project_path = tmp_path / "noop-audit.irproj"
    service = _create_project(project_path)
    service.close()
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    try:
        document = repository.create(
            document_id=document_id,
            values=_document_values(),
            wheel_model_ids=(),
            specimen_ids=(),
            deadline=None,
        )
        occurred_at = utc_now()
        connection.execute(
            "UPDATE case_documents SET record_revision=2, updated_at_utc=? WHERE case_document_id=?",
            (occurred_at, document.case_document_id),
        )
        insert_audit(
            connection,
            event_type="case_document.updated",
            actor_kind="user",
            occurred_at_utc=occurred_at,
            payload={
                "entityType": "caseDocument",
                "entityId": document.case_document_id,
                "fromRevision": 1,
                "toRevision": 2,
                "changedFields": [],
                "changes": {},
            },
        )
        connection.commit()
    finally:
        connection.close()

    reopened = ProjectService()
    try:
        with pytest.raises(ProjectOperationError) as raised:
            reopened.open(path=str(project_path), application_instance_id=str(uuid4()))
        assert raised.value.code == "corrupt_project"
    finally:
        reopened.close()


def test_project_reopen_rejects_document_update_after_archive(tmp_path: Path) -> None:
    project_path = tmp_path / "archived-update-audit.irproj"
    service = _create_project(project_path)
    service.close()
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    try:
        document = repository.create(
            document_id=document_id,
            values=_document_values("До архива"),
            wheel_model_ids=(),
            specimen_ids=(),
            deadline=None,
        )
        archived = repository.set_archived(
            document_id=document.case_document_id,
            expected_revision=1,
            archived=True,
            deadline=None,
        )
        occurred_at = utc_now()
        connection.execute(
            "UPDATE case_documents SET title='После архива', record_revision=3, updated_at_utc=? WHERE case_document_id=?",
            (occurred_at, archived.case_document_id),
        )
        insert_audit(
            connection,
            event_type="case_document.updated",
            actor_kind="user",
            occurred_at_utc=occurred_at,
            payload={
                "entityType": "caseDocument",
                "entityId": archived.case_document_id,
                "fromRevision": 2,
                "toRevision": 3,
                "changedFields": ["title"],
                "changes": {"title": {"before": "До архива", "after": "После архива"}},
            },
        )
        connection.commit()
    finally:
        connection.close()

    reopened = ProjectService()
    try:
        with pytest.raises(ProjectOperationError) as raised:
            reopened.open(path=str(project_path), application_instance_id=str(uuid4()))
        assert raised.value.code == "corrupt_project"
    finally:
        reopened.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [("document_date", "2026-02-30"), ("case_document_id", "not-a-uuid")],
)
def test_project_reopen_maps_malformed_document_rows_to_corrupt_project(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    project_path = tmp_path / f"malformed-{field}.irproj"
    service = _create_project(project_path)
    service.close()
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    try:
        repository.create(
            document_id=document_id,
            values=_document_values(),
            wheel_model_ids=(),
            specimen_ids=(),
            deadline=None,
        )
        connection.execute(f"UPDATE case_documents SET {field}=? WHERE case_document_id=?", (value, document_id))
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("originalFileName", r"C:\\secret.pdf"),
        ("storedRelativePath", "noncanonical"),
    ],
)
def test_project_reopen_rejects_noncanonical_file_registry_metadata(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    project_path = tmp_path / f"registry-{field}.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nregistry\n")
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    try:
        document = repository.create_with_file(
            document_id=document_id,
            values=_document_values(),
            wheel_model_ids=(),
            specimen_ids=(),
            source_path=source,
            deadline=None,
        )
        assert document.file is not None
        replacement = value
        if field == "storedRelativePath":
            replacement = document.file.stored_relative_path.replace(
                f"/{document.case_document_id}/",
                f"/{document.case_document_id}/./",
            )
        file_trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='case_document_files_no_update'",
        ).fetchone()
        audit_trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='project_audit_events_no_update'",
        ).fetchone()
        assert file_trigger_sql is not None and isinstance(file_trigger_sql[0], str)
        assert audit_trigger_sql is not None and isinstance(audit_trigger_sql[0], str)
        connection.execute("DROP TRIGGER case_document_files_no_update")
        column = "original_file_name" if field == "originalFileName" else "stored_relative_path"
        connection.execute(
            f"UPDATE case_document_files SET {column}=? WHERE case_document_id=?",
            (replacement, document.case_document_id),
        )
        connection.execute("DROP TRIGGER project_audit_events_no_update")
        audit_row = connection.execute(
            "SELECT sequence, payload_json FROM project_audit_events WHERE event_type='case_document.created'",
        ).fetchone()
        assert audit_row is not None
        payload = json.loads(str(audit_row[1]))
        payload["after"]["file"][field] = replacement
        connection.execute(
            "UPDATE project_audit_events SET payload_json=? WHERE sequence=?",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), int(audit_row[0])),
        )
        connection.execute(str(file_trigger_sql[0]))
        connection.execute(str(audit_trigger_sql[0]))
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"


def test_resolve_rejects_reparse_document_directory(tmp_path: Path) -> None:
    project_path = tmp_path / "reparse-document.irproj"
    service = _create_project(project_path)
    service.close()
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nreparse\n")
    connection, repository = _open_repository(project_path)
    document_id = str(uuid4())
    external = tmp_path / "external"
    external.mkdir()
    managed_directory: Path | None = None
    try:
        document = repository.create_with_file(
            document_id=document_id,
            values=_document_values(),
            wheel_model_ids=(),
            specimen_ids=(),
            source_path=source,
            deadline=None,
        )
        assert document.file is not None
        managed_directory = project_path / "assets" / "documents" / document.case_document_id
        managed_file = managed_directory / Path(document.file.stored_relative_path).name
        external_file = external / managed_file.name
        external_file.write_bytes(managed_file.read_bytes())
        managed_file.unlink()
        managed_directory.rmdir()
        subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(managed_directory), str(external)],
            check=True,
            capture_output=True,
        )
        with pytest.raises(ProjectOperationError) as raised:
            repository.resolve_file(document.case_document_id, deadline=None)
        assert raised.value.code == "file_integrity_mismatch"
    finally:
        if managed_directory is not None and managed_directory.is_junction():
            managed_directory.rmdir()
        connection.close()
