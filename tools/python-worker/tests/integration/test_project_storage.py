from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import closing, contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from impeller_reliability.application.project_service import ProjectService
from impeller_reliability.persistence import project_database, project_schema, project_session
from impeller_reliability.persistence.project_database import (
    MIGRATIONS,
    Migration,
    ProjectMigrator,
    VerifiedBackup,
    configure_project_connection,
    remove_owned_backup,
    sha256_file,
)
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_manifest import ProjectManifest, read_manifest, write_manifest
from impeller_reliability.persistence.project_paths import inspect_reserved_file
from impeller_reliability.persistence.project_session import ProjectSession
from impeller_reliability.worker.deadline import RequestDeadline


def _create_project(path: Path) -> ProjectService:
    service = ProjectService()
    service.create(
        path=str(path),
        application_instance_id=str(uuid4()),
        application_version="0.1.0",
        name="Проект рабочего колеса",
        project_number="ИР-001",
        description="Проверка контейнера с кириллицей.",
        status="draft",
    )
    return service


def _create_container_shell(path: Path, project_id: str) -> Path:
    path.mkdir()
    (path / "backups").mkdir()
    write_manifest(
        path / "project-manifest.json",
        ProjectManifest(
            projectId=project_id,
            createdAtUtc="2026-08-26T00:00:00.000Z",
            createdWithApplicationVersion="0.1.0",
        ),
    )
    return path / "project.sqlite"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_journal_mode_without_writes(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        return str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    finally:
        connection.close()


@contextmanager
def _database(path: Path) -> Generator[sqlite3.Connection]:
    connection = sqlite3.connect(path / "project.sqlite")
    connection.row_factory = sqlite3.Row
    configure_project_connection(connection)
    try:
        yield connection
    finally:
        connection.close()


def test_create_update_close_and_reopen_with_cyrillic_path(tmp_path: Path) -> None:
    project_path = tmp_path / "Каталог с пробелами" / "Надёжность колеса.irproj"
    project_path.parent.mkdir()
    service = _create_project(project_path)

    created = service.get_overview()
    assert created.name == "Проект рабочего колеса"
    assert created.record_revision == 1
    assert (project_path / "assets" / "documents").is_dir()
    assert (project_path / "backups").is_dir()
    assert not (project_path / "imports").exists()

    updated = service.update_metadata(
        expected_revision=1,
        name="Проект после изменения",
        project_number="ИР-002",
        description="Данные сохранены.",
        status="active",
    )
    assert updated.record_revision == 2
    assert service.close() is True

    reopened = ProjectService()
    persisted = reopened.open(path=str(project_path), application_instance_id=str(uuid4()))
    assert persisted.name == "Проект после изменения"
    assert persisted.project_number == "ИР-002"
    assert persisted.description == "Данные сохранены."
    assert persisted.status == "active"
    assert persisted.record_revision == 2
    reopened.close()

    with _database(project_path) as connection:
        events = connection.execute("SELECT event_type, payload_json FROM project_audit_events ORDER BY sequence").fetchall()
    assert [str(row["event_type"]) for row in events] == ["project.created", "project.metadata_updated"]
    created_payload = json.loads(str(events[0]["payload_json"]))
    assert created_payload["after"] == {
        "description": "Проверка контейнера с кириллицей.",
        "name": "Проект рабочего колеса",
        "projectNumber": "ИР-001",
        "status": "draft",
    }
    update_payload = json.loads(str(events[1]["payload_json"]))
    assert update_payload["changedFields"] == ["name", "projectNumber", "description", "status"]
    assert update_payload["changes"]["status"] == {"before": "draft", "after": "active"}


def test_stale_revision_is_rejected_without_audit_event(tmp_path: Path) -> None:
    project_path = tmp_path / "revision.irproj"
    service = _create_project(project_path)
    service.update_metadata(
        expected_revision=1,
        name="Редакция 2",
        project_number="",
        description="",
        status="active",
    )

    with pytest.raises(ProjectOperationError, match="изменён") as raised:
        service.update_metadata(
            expected_revision=1,
            name="Устаревший draft",
            project_number="",
            description="",
            status="draft",
        )
    assert raised.value.code == "revision_conflict"
    assert service.get_overview().name == "Редакция 2"
    service.close()

    with _database(project_path) as connection:
        count = int(connection.execute("SELECT count(*) FROM project_audit_events").fetchone()[0])
    assert count == 2


def test_noop_update_does_not_create_revision_or_false_changed_fields(tmp_path: Path) -> None:
    project_path = tmp_path / "noop.irproj"
    service = _create_project(project_path)
    unchanged = service.update_metadata(
        expected_revision=1,
        name="  Проект рабочего колеса  ",
        project_number=" ИР-001 ",
        description=" Проверка контейнера с кириллицей. ",
        status="draft",
    )
    assert unchanged.record_revision == 1
    service.close()
    with _database(project_path) as connection:
        events = connection.execute("SELECT event_type, payload_json FROM project_audit_events ORDER BY sequence").fetchall()
    assert [str(row["event_type"]) for row in events] == ["project.created"]
    assert "changes" not in json.loads(str(events[0]["payload_json"]))


def test_metadata_update_keeps_audit_time_monotonic_when_wall_clock_moves_backward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "clock-correction.irproj"
    service = _create_project(project_path)
    before = service.get_overview()
    monkeypatch.setattr(project_session, "utc_now", lambda: "2000-01-01T00:00:00.000Z")

    updated = service.update_metadata(
        expected_revision=before.record_revision,
        name="После коррекции часов",
        project_number=before.project_number,
        description=before.description,
        status=before.status,
    )

    assert updated.updated_at_utc >= before.updated_at_utc
    service.close()
    reopened = ProjectService()
    persisted = reopened.open(path=str(project_path), application_instance_id=str(uuid4()))
    assert persisted.name == "После коррекции часов"
    assert persisted.updated_at_utc == updated.updated_at_utc
    reopened.close()


def test_audit_failure_rolls_back_metadata_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_path = tmp_path / "audit-rollback.irproj"
    service = _create_project(project_path)

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr("impeller_reliability.persistence.project_session.insert_audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit write failed"):
        service.update_metadata(
            expected_revision=1,
            name="Не должно сохраниться",
            project_number="ИР-999",
            description="rollback",
            status="active",
        )
    overview = service.get_overview()
    assert overview.name == "Проект рабочего колеса"
    assert overview.record_revision == 1
    service.close()


def test_manual_backup_and_active_session_guards(tmp_path: Path) -> None:
    project_path = tmp_path / "backup.irproj"
    service = _create_project(project_path)
    backup_path, digest, created_at = service.create_backup()
    assert backup_path.is_file()
    assert len(digest) == 64
    assert created_at.endswith("Z")
    with pytest.raises(ProjectOperationError) as active:
        service.open(path=str(project_path), application_instance_id=str(uuid4()))
    assert active.value.code == "project_locked"
    assert service.close() is True
    assert service.close() is False
    with pytest.raises(ProjectOperationError, match="не открыт"):
        service.get_overview()


def test_audit_is_append_only(tmp_path: Path) -> None:
    project_path = tmp_path / "audit.irproj"
    service = _create_project(project_path)
    service.close()
    with _database(project_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            connection.execute("UPDATE project_audit_events SET event_type = 'changed'")
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            connection.execute("DELETE FROM project_audit_events")


@pytest.mark.parametrize("corruption", ["manifest", "project_id", "database"])
def test_corrupt_project_is_rejected(tmp_path: Path, corruption: str) -> None:
    project_path = tmp_path / f"{corruption}.irproj"
    service = _create_project(project_path)
    service.close()
    if corruption == "manifest":
        (project_path / "project-manifest.json").write_text("{invalid", encoding="utf-8")
    elif corruption == "project_id":
        manifest_path = project_path / "project-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["projectId"] = str(uuid4())
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        (project_path / "project.sqlite").write_bytes(b"not-a-sqlite-database")

    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"


def test_foreign_sqlite_is_rejected_without_any_mutation(tmp_path: Path) -> None:
    project_path = tmp_path / "foreign.irproj"
    database_path = _create_container_shell(project_path, str(uuid4()))
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("CREATE TABLE unrelated_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO unrelated_data VALUES ('untouched')")
    before_hash = _sha256(database_path)
    before_mtime = database_path.stat().st_mtime_ns
    before_journal_mode = _read_journal_mode_without_writes(database_path)

    service = ProjectService()
    with pytest.raises(ProjectOperationError) as raised:
        service.open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"
    assert service.has_active_session is False
    assert _sha256(database_path) == before_hash
    assert database_path.stat().st_mtime_ns == before_mtime
    assert _read_journal_mode_without_writes(database_path) == before_journal_mode == "delete"
    assert not (project_path / ".project.lock").exists()
    assert not (project_path / "project.sqlite-wal").exists()
    assert not (project_path / "project.sqlite-shm").exists()
    assert not (project_path / "project.sqlite-journal").exists()


def test_missing_project_database_is_not_created_by_open(tmp_path: Path) -> None:
    project_path = tmp_path / "missing-database.irproj"
    database_path = _create_container_shell(project_path, str(uuid4()))

    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"
    assert not database_path.exists()
    assert not (project_path / ".project.lock").exists()
    assert list((project_path / "backups").iterdir()) == []


def test_mismatched_project_id_is_rejected_before_any_write(tmp_path: Path) -> None:
    project_path = tmp_path / "mismatched-project-id.irproj"
    service = _create_project(project_path)
    service.close()
    manifest_path = project_path / "project-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["projectId"] = str(uuid4())
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    database_path = project_path / "project.sqlite"
    lock_path = project_path / ".project.lock"
    before_database_hash = _sha256(database_path)
    before_database_mtime = database_path.stat().st_mtime_ns
    before_lock = lock_path.read_bytes()
    before_lock_mtime = lock_path.stat().st_mtime_ns

    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"
    assert _sha256(database_path) == before_database_hash
    assert database_path.stat().st_mtime_ns == before_database_mtime
    assert lock_path.read_bytes() == before_lock
    assert lock_path.stat().st_mtime_ns == before_lock_mtime
    assert not (project_path / "project.sqlite-wal").exists()
    assert not (project_path / "project.sqlite-shm").exists()


def test_invalid_application_id_is_rejected_before_lock_or_wal(tmp_path: Path) -> None:
    project_id = str(uuid4())
    project_path = tmp_path / "wrong-application-id.irproj"
    database_path = _create_container_shell(project_path, project_id)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA application_id = 123")
        connection.execute("PRAGMA user_version = 1")
        connection.execute("CREATE TABLE project_metadata (project_id TEXT NOT NULL)")
        connection.execute("INSERT INTO project_metadata VALUES (?)", (project_id,))
    before_hash = _sha256(database_path)

    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"
    assert _sha256(database_path) == before_hash
    assert not (project_path / ".project.lock").exists()
    assert not (project_path / "project.sqlite-wal").exists()
    assert not (project_path / "project.sqlite-shm").exists()


def test_unpublished_schema_zero_is_not_migrated_or_modified(tmp_path: Path) -> None:
    project_path = tmp_path / "schema-zero.irproj"
    database_path = _create_container_shell(project_path, str(uuid4()))
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(f"PRAGMA application_id = {project_database.PROJECT_APPLICATION_ID}")
        connection.execute("CREATE TABLE unrelated_data (value TEXT NOT NULL)")
    before_hash = _sha256(database_path)
    before_mtime = database_path.stat().st_mtime_ns

    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"
    assert _sha256(database_path) == before_hash
    assert database_path.stat().st_mtime_ns == before_mtime
    assert not (project_path / ".project.lock").exists()
    assert list((project_path / "backups").iterdir()) == []


@pytest.mark.parametrize("link_kind", ["hardlink", "symlink"])
def test_linked_project_database_is_rejected_without_touching_target(
    tmp_path: Path,
    link_kind: str,
) -> None:
    project_path = tmp_path / f"database-{link_kind}.irproj"
    database_path = _create_container_shell(project_path, str(uuid4()))
    external_database = tmp_path / f"external-{link_kind}.sqlite"
    with closing(sqlite3.connect(external_database)) as connection:
        connection.execute("CREATE TABLE external_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO external_data VALUES ('untouched')")
    if link_kind == "hardlink":
        os.link(external_database, database_path)
    else:
        os.symlink(external_database, database_path)
    before_hash = _sha256(external_database)
    before_mtime = external_database.stat().st_mtime_ns

    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"
    assert _sha256(external_database) == before_hash
    assert external_database.stat().st_mtime_ns == before_mtime
    assert not (project_path / ".project.lock").exists()
    assert not external_database.with_name(f"{external_database.name}-wal").exists()
    assert not external_database.with_name(f"{external_database.name}-shm").exists()


def test_reparse_backups_directory_is_rejected_without_external_write(tmp_path: Path) -> None:
    project_path = tmp_path / "junction-backups.irproj"
    service = _create_project(project_path)
    service.close()
    backups_path = project_path / "backups"
    backups_path.rmdir()
    external_backups = tmp_path / "external-backups"
    external_backups.mkdir()
    marker = external_backups / "marker.txt"
    marker.write_text("untouched", encoding="utf-8")
    subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(backups_path), str(external_backups)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert backups_path.is_junction()

    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"
    assert marker.read_text(encoding="utf-8") == "untouched"
    assert list(external_backups.iterdir()) == [marker]


@pytest.mark.parametrize("lock_kind", ["hardlink", "symlink"])
def test_linked_lock_and_reparse_sidecar_are_rejected_without_touching_targets(
    tmp_path: Path,
    lock_kind: str,
) -> None:
    project_path = tmp_path / f"linked-reserved-files-{lock_kind}.irproj"
    service = _create_project(project_path)
    service.close()
    lock_path = project_path / ".project.lock"
    lock_path.unlink()
    external_lock = tmp_path / "external-lock.bin"
    external_lock.write_bytes(b"external lock")
    if lock_kind == "hardlink":
        os.link(external_lock, lock_path)
    else:
        os.symlink(external_lock, lock_path)
    external_sidecar = tmp_path / f"external-sidecar-{lock_kind}.bin"
    external_sidecar.write_bytes(b"external sidecar")
    os.symlink(external_sidecar, project_path / "project.sqlite-wal")
    lock_hash = _sha256(external_lock)
    sidecar_hash = _sha256(external_sidecar)

    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"
    assert _sha256(external_lock) == lock_hash
    assert _sha256(external_sidecar) == sidecar_hash


def test_newer_schema_is_not_modified(tmp_path: Path) -> None:
    project_path = tmp_path / "newer.irproj"
    service = _create_project(project_path)
    service.close()
    with _database(project_path) as connection:
        connection.execute("PRAGMA user_version = 99")
    database_path = project_path / "project.sqlite"
    lock_path = project_path / ".project.lock"
    before_mtime = database_path.stat().st_mtime_ns
    before_hash = _sha256(database_path)
    before_lock = lock_path.read_bytes()
    before_lock_mtime = lock_path.stat().st_mtime_ns
    before_journal_mode = _read_journal_mode_without_writes(database_path)

    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "incompatible_schema"
    assert database_path.stat().st_mtime_ns == before_mtime
    assert _sha256(database_path) == before_hash
    assert _read_journal_mode_without_writes(database_path) == before_journal_mode
    assert lock_path.read_bytes() == before_lock
    assert lock_path.stat().st_mtime_ns == before_lock_mtime
    assert not (project_path / "project.sqlite-wal").exists()
    assert not (project_path / "project.sqlite-shm").exists()
    assert list((project_path / "backups").iterdir()) == []


@pytest.mark.parametrize(
    "schema_mutation",
    [
        "DROP TABLE project_audit_events",
        "DROP TRIGGER project_audit_events_no_update",
        "DROP TRIGGER project_audit_events_no_delete",
        "DROP TRIGGER project_audit_events_no_update; CREATE TRIGGER project_audit_events_no_update BEFORE UPDATE ON project_audit_events BEGIN SELECT 1; END;",
        "DELETE FROM schema_migrations",
        "ALTER TABLE project_metadata ADD COLUMN unexpected TEXT",
        "CREATE TABLE unrecognized_project_data (value TEXT)",
    ],
)
def test_schema_v1_contract_is_rejected_without_mutation(
    tmp_path: Path,
    schema_mutation: str,
) -> None:
    project_path = tmp_path / "invalid-schema-v1.irproj"
    service = _create_project(project_path)
    service.close()
    with _database(project_path) as connection:
        connection.executescript(schema_mutation)
    database_path = project_path / "project.sqlite"
    lock_path = project_path / ".project.lock"
    before_hash = _sha256(database_path)
    before_mtime = database_path.stat().st_mtime_ns
    before_lock = lock_path.read_bytes()
    before_lock_mtime = lock_path.stat().st_mtime_ns

    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))

    assert raised.value.code == "corrupt_project"
    assert _sha256(database_path) == before_hash
    assert database_path.stat().st_mtime_ns == before_mtime
    assert lock_path.read_bytes() == before_lock
    assert lock_path.stat().st_mtime_ns == before_lock_mtime
    assert not (project_path / "project.sqlite-wal").exists()
    assert not (project_path / "project.sqlite-shm").exists()


def test_schema_change_between_probe_and_write_open_is_rejected_before_wal(tmp_path: Path) -> None:
    project_path = tmp_path / "schema-swap.irproj"
    service = _create_project(project_path)
    service.close()
    database_path = project_path / "project.sqlite"
    manifest = read_manifest(project_path / "project-manifest.json")
    database_identity = project_database.probe_project_database_identity(
        database_path,
        manifest,
        project_schema.PROJECT_SCHEMA_VERSION,
    )
    with _database(project_path) as connection:
        connection.execute("DROP TRIGGER project_audit_events_no_update")
        assert str(connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]).lower() == "delete"
    path_identity = inspect_reserved_file(database_path, database_path.name)
    assert path_identity is not None
    before_hash = _sha256(database_path)
    before_mtime = database_path.stat().st_mtime_ns

    with pytest.raises(ProjectOperationError) as raised:
        project_database.open_project_database(
            database_path,
            path_identity,
            database_identity,
            manifest,
        )

    assert raised.value.code == "corrupt_project"
    assert _sha256(database_path) == before_hash
    assert database_path.stat().st_mtime_ns == before_mtime
    assert _read_journal_mode_without_writes(database_path) == "delete"
    assert not (project_path / "project.sqlite-wal").exists()
    assert not (project_path / "project.sqlite-shm").exists()


@pytest.mark.parametrize("audit_corruption", ["missing_history", "mismatched_entity"])
def test_audit_evidence_contract_is_rejected_without_mutation(
    tmp_path: Path,
    audit_corruption: str,
) -> None:
    project_path = tmp_path / "invalid-audit-evidence.irproj"
    service = _create_project(project_path)
    service.close()
    with _database(project_path) as connection:
        if audit_corruption == "missing_history":
            connection.execute("DROP TRIGGER project_audit_events_no_delete")
            connection.execute("DELETE FROM project_audit_events")
            connection.execute(project_schema.PROJECT_AUDIT_NO_DELETE_TRIGGER_SQL)
        else:
            connection.execute("DROP TRIGGER project_audit_events_no_update")
            connection.execute(
                "UPDATE project_audit_events SET payload_json = ? WHERE sequence = 1",
                ('{"entityId":"another-project","entityType":"project","toRevision":1}',),
            )
            connection.execute(project_schema.PROJECT_AUDIT_NO_UPDATE_TRIGGER_SQL)
        connection.commit()
    database_path = project_path / "project.sqlite"
    lock_path = project_path / ".project.lock"
    before_hash = _sha256(database_path)
    before_lock = lock_path.read_bytes()

    candidate = ProjectService()
    with pytest.raises(ProjectOperationError) as raised:
        candidate.open(path=str(project_path), application_instance_id=str(uuid4()))

    assert raised.value.code == "corrupt_project"
    assert _sha256(database_path) == before_hash
    assert lock_path.read_bytes() == before_lock
    assert not (project_path / "project.sqlite-wal").exists()
    assert not (project_path / "project.sqlite-shm").exists()


def test_null_sqlite_schema_object_is_rejected_by_read_only_probe(tmp_path: Path) -> None:
    project_path = tmp_path / "null-schema-object.irproj"
    service = _create_project(project_path)
    service.close()
    database_path = project_path / "project.sqlite"
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("CREATE TABLE extra_object (value TEXT)")
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute("UPDATE sqlite_schema SET sql = NULL WHERE name = 'extra_object'")
        schema_version = int(connection.execute("PRAGMA schema_version").fetchone()[0])
        connection.execute(f"PRAGMA schema_version = {schema_version + 1}")
        connection.commit()
    lock_path = project_path / ".project.lock"
    before_hash = _sha256(database_path)
    before_mtime = database_path.stat().st_mtime_ns
    before_lock = lock_path.read_bytes()

    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))

    assert raised.value.code == "corrupt_project"
    assert _sha256(database_path) == before_hash
    assert database_path.stat().st_mtime_ns == before_mtime
    assert lock_path.read_bytes() == before_lock
    assert not (project_path / "project.sqlite-wal").exists()
    assert not (project_path / "project.sqlite-shm").exists()


def test_oversized_audit_payload_is_rejected_before_json_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "oversized-audit.irproj"
    service = _create_project(project_path)
    service.close()
    with _database(project_path) as connection:
        payload = json.loads(str(connection.execute("SELECT payload_json FROM project_audit_events WHERE sequence = 1").fetchone()[0]))
        payload["padding"] = "A" * 100_000
        connection.execute("DROP TRIGGER project_audit_events_no_update")
        connection.execute(
            "UPDATE project_audit_events SET payload_json = ? WHERE sequence = 1",
            (json.dumps(payload, ensure_ascii=False),),
        )
        connection.execute(project_schema.PROJECT_AUDIT_NO_UPDATE_TRIGGER_SQL)
        connection.commit()

    def fail_if_materialized(_value: str) -> dict[str, object]:
        raise AssertionError("oversized_audit_payload_was_materialized")

    monkeypatch.setattr(project_schema, "_parse_json_object", fail_if_materialized)
    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"


def test_oversized_audit_scalar_is_rejected_before_timestamp_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "oversized-audit-scalar.irproj"
    service = _create_project(project_path)
    service.close()
    with _database(project_path) as connection:
        connection.execute("DROP TRIGGER project_audit_events_no_update")
        connection.execute(
            "UPDATE project_audit_events SET occurred_at_utc = ? WHERE sequence = 1",
            ("2" * 100_000,),
        )
        connection.execute(project_schema.PROJECT_AUDIT_NO_UPDATE_TRIGGER_SQL)
        connection.commit()

    def reject_materialized_scalar(value: str) -> datetime:
        if len(value.encode("utf-8")) > 32:
            raise AssertionError("oversized_audit_scalar_was_materialized")
        if value == "None":
            raise ProjectOperationError("corrupt_project", "bounded audit scalar rejected")
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    monkeypatch.setattr(project_schema, "_require_timestamp", reject_materialized_scalar)
    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"


def test_non_integer_metadata_revision_is_rejected_before_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "oversized-metadata-revision.irproj"
    service = _create_project(project_path)
    service.close()
    with _database(project_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE project_metadata SET record_revision = ?",
            (sqlite3.Binary(b"1" * 100_000),),
        )
        connection.commit()

    def reject_materialized_revision(value: object) -> int:
        if isinstance(value, bytes) and len(value) > 32:
            raise AssertionError("oversized_metadata_revision_was_materialized")
        if isinstance(value, int):
            return value
        raise ProjectOperationError("corrupt_project", "bounded metadata revision rejected")

    monkeypatch.setattr(project_schema, "_require_int", reject_materialized_revision)
    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"


def test_audit_evidence_validation_honors_deadline_while_streaming(tmp_path: Path) -> None:
    project_path = tmp_path / "audit-deadline.irproj"
    service = _create_project(project_path)
    service.close()
    manifest = read_manifest(project_path / "project-manifest.json")
    clock = _DelayedClock(expire_on_call=3)
    with closing(sqlite3.connect(f"file:{(project_path / 'project.sqlite').as_posix()}?mode=ro&immutable=1", uri=True)) as connection, pytest.raises(ProjectOperationError) as raised:
        project_schema.validate_project_evidence(
            connection,
            manifest.projectId,
            manifest.createdAtUtc,
            manifest.createdWithApplicationVersion,
            deadline=RequestDeadline.start(1_000, clock=clock),
        )
    assert raised.value.code == "timeout"


@pytest.mark.parametrize(
    "timestamp_target",
    ["manifest", "metadata_created", "metadata_updated", "audit"],
)
def test_invalid_project_timestamp_is_rejected_without_mutation(
    tmp_path: Path,
    timestamp_target: str,
) -> None:
    project_path = tmp_path / "invalid-timestamp.irproj"
    service = _create_project(project_path)
    service.close()
    invalid_timestamp = "2026-99-99T25:61:61.000Z"
    if timestamp_target == "manifest":
        manifest_path = project_path / "project-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["createdAtUtc"] = invalid_timestamp
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        with _database(project_path) as connection:
            if timestamp_target == "audit":
                connection.execute("DROP TRIGGER project_audit_events_no_update")
                connection.execute(
                    "UPDATE project_audit_events SET occurred_at_utc = ? WHERE sequence = 1",
                    (invalid_timestamp,),
                )
                connection.execute(project_schema.PROJECT_AUDIT_NO_UPDATE_TRIGGER_SQL)
            else:
                column = "created_at_utc" if timestamp_target == "metadata_created" else "updated_at_utc"
                connection.execute(f"UPDATE project_metadata SET {column} = ?", (invalid_timestamp,))
            connection.commit()
    database_path = project_path / "project.sqlite"
    lock_path = project_path / ".project.lock"
    before_hash = _sha256(database_path)
    before_lock = lock_path.read_bytes()
    candidate = ProjectService()

    with pytest.raises(ProjectOperationError) as raised:
        candidate.open(path=str(project_path), application_instance_id=str(uuid4()))

    assert raised.value.code == "corrupt_project"
    assert _sha256(database_path) == before_hash
    assert lock_path.read_bytes() == before_lock
    assert not (project_path / "project.sqlite-wal").exists()
    assert not (project_path / "project.sqlite-shm").exists()


@pytest.mark.parametrize(
    ("version_target", "invalid_value"),
    [
        ("manifest", ""),
        ("manifest", " 0.1.0"),
        ("both", "\ufeff0.1.0"),
        ("both", "🚀" * 33),
        ("metadata", ""),
        ("metadata", "0.2.0"),
        ("blob", sqlite3.Binary(b"0.1.0")),
    ],
)
def test_invalid_or_mismatched_application_version_is_rejected_without_mutation(
    tmp_path: Path,
    version_target: str,
    invalid_value: object,
) -> None:
    project_path = tmp_path / "invalid-application-version.irproj"
    service = _create_project(project_path)
    service.close()
    if version_target in {"manifest", "both", "blob"}:
        manifest_value = "b'0.1.0'" if version_target == "blob" else invalid_value
        assert isinstance(manifest_value, str)
        manifest_path = project_path / "project-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["createdWithApplicationVersion"] = manifest_value
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    if version_target in {"metadata", "both", "blob"}:
        with _database(project_path) as connection:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                "UPDATE project_metadata SET created_with_application_version = ?",
                (invalid_value,),
            )
            connection.commit()
    database_path = project_path / "project.sqlite"
    lock_path = project_path / ".project.lock"
    before_hash = _sha256(database_path)
    before_lock = lock_path.read_bytes()
    candidate = ProjectService()

    with pytest.raises(ProjectOperationError) as raised:
        candidate.open(path=str(project_path), application_instance_id=str(uuid4()))

    assert raised.value.code == "corrupt_project"
    assert candidate.has_active_session is False
    assert _sha256(database_path) == before_hash
    assert lock_path.read_bytes() == before_lock
    assert not (project_path / "project.sqlite-wal").exists()
    assert not (project_path / "project.sqlite-shm").exists()


@pytest.mark.parametrize(
    ("column", "audit_field", "raw_value"),
    [
        ("name", "name", b"A" * 200),
        ("project_number", "projectNumber", b"A" * 100),
        ("description", "description", b"A" * 4_000),
        ("status", "status", b"draft"),
    ],
)
def test_non_text_project_metadata_is_rejected_without_mutation(
    tmp_path: Path,
    column: str,
    audit_field: str,
    raw_value: bytes,
) -> None:
    project_path = tmp_path / "non-text-metadata.irproj"
    service = _create_project(project_path)
    service.close()
    with _database(project_path) as connection:
        payload = json.loads(str(connection.execute("SELECT payload_json FROM project_audit_events WHERE sequence = 1").fetchone()[0]))
        payload["after"][audit_field] = str(raw_value)
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            f"UPDATE project_metadata SET {column} = ?",
            (sqlite3.Binary(raw_value),),
        )
        connection.execute("DROP TRIGGER project_audit_events_no_update")
        connection.execute(
            "UPDATE project_audit_events SET payload_json = ? WHERE sequence = 1",
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":")),),
        )
        connection.execute(project_schema.PROJECT_AUDIT_NO_UPDATE_TRIGGER_SQL)
        connection.execute("PRAGMA ignore_check_constraints = OFF")
        connection.commit()
    database_path = project_path / "project.sqlite"
    lock_path = project_path / ".project.lock"
    before_hash = _sha256(database_path)
    before_lock = lock_path.read_bytes()
    candidate = ProjectService()

    try:
        with pytest.raises(ProjectOperationError) as raised:
            candidate.open(path=str(project_path), application_instance_id=str(uuid4()))
        assert raised.value.code == "corrupt_project"
    finally:
        candidate.close()

    assert _sha256(database_path) == before_hash
    assert lock_path.read_bytes() == before_lock
    assert not (project_path / "project.sqlite-wal").exists()
    assert not (project_path / "project.sqlite-shm").exists()


@pytest.mark.parametrize("representation", ["hex", "braced", "urn", "invalid_version"])
def test_noncanonical_project_id_is_rejected_without_mutation(
    tmp_path: Path,
    representation: str,
) -> None:
    project_path = tmp_path / "noncanonical-project-id.irproj"
    service = _create_project(project_path)
    service.close()
    manifest_path = project_path / "project-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    canonical_id = str(manifest["projectId"])
    if representation == "hex":
        project_id = canonical_id.replace("-", "")
    elif representation == "braced":
        project_id = f"{{{canonical_id}}}"
    elif representation == "urn":
        project_id = f"urn:uuid:{canonical_id}"
    else:
        project_id = f"{canonical_id[:14]}0{canonical_id[15:]}"
    manifest["projectId"] = project_id
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with _database(project_path) as connection:
        payload = json.loads(str(connection.execute("SELECT payload_json FROM project_audit_events WHERE sequence = 1").fetchone()[0]))
        payload["entityId"] = project_id
        connection.execute("UPDATE project_metadata SET project_id = ?", (project_id,))
        connection.execute("DROP TRIGGER project_audit_events_no_update")
        connection.execute(
            "UPDATE project_audit_events SET payload_json = ? WHERE sequence = 1",
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":")),),
        )
        connection.execute(project_schema.PROJECT_AUDIT_NO_UPDATE_TRIGGER_SQL)
        connection.commit()
    database_path = project_path / "project.sqlite"
    lock_path = project_path / ".project.lock"
    before_hash = _sha256(database_path)
    before_lock = lock_path.read_bytes()
    candidate = ProjectService()

    try:
        with pytest.raises(ProjectOperationError) as raised:
            candidate.open(path=str(project_path), application_instance_id=str(uuid4()))
        assert raised.value.code == "corrupt_project"
    finally:
        candidate.close()

    assert _sha256(database_path) == before_hash
    assert lock_path.read_bytes() == before_lock
    assert not (project_path / "project.sqlite-wal").exists()
    assert not (project_path / "project.sqlite-shm").exists()


def test_backup_precedes_forward_migration(tmp_path: Path) -> None:
    project_path = tmp_path / "migration.irproj"
    service = _create_project(project_path)
    service.close()
    manifest = json.loads((project_path / "project-manifest.json").read_text(encoding="utf-8"))

    def migration_0002(
        connection: sqlite3.Connection,
        _manifest: object,
        _initial_metadata: object,
    ) -> None:
        connection.execute("CREATE TABLE migration_marker (value TEXT NOT NULL)")

    from impeller_reliability.persistence.project_manifest import ProjectManifest

    parsed_manifest = ProjectManifest.model_validate(manifest)
    migrator = ProjectMigrator((*MIGRATIONS, Migration(2, "marker", migration_0002)))
    with _database(project_path) as connection:
        backup_path = migrator.migrate_existing(connection, project_path / "project.sqlite", project_path / "backups", parsed_manifest)
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == 2
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'migration_marker'").fetchone() is not None
    assert backup_path is not None and backup_path.is_file()
    with closing(sqlite3.connect(backup_path)) as backup:
        assert int(backup.execute("PRAGMA user_version").fetchone()[0]) == 1
        assert str(backup.execute("PRAGMA quick_check").fetchone()[0]) == "ok"


def test_failed_migration_rolls_back_and_keeps_verified_backup(tmp_path: Path) -> None:
    project_path = tmp_path / "rollback.irproj"
    service = _create_project(project_path)
    service.close()
    from impeller_reliability.persistence.project_manifest import read_manifest

    def broken_migration(
        connection: sqlite3.Connection,
        _manifest: object,
        _initial_metadata: object,
    ) -> None:
        connection.execute("CREATE TABLE should_rollback (value TEXT NOT NULL)")
        raise RuntimeError("migration failed")

    migrator = ProjectMigrator((*MIGRATIONS, Migration(2, "broken", broken_migration)))
    with _database(project_path) as connection, pytest.raises(RuntimeError, match="migration failed"):
        migrator.migrate_existing(
            connection,
            project_path / "project.sqlite",
            project_path / "backups",
            read_manifest(project_path / "project-manifest.json"),
        )
    with _database(project_path) as connection:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == 1
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'should_rollback'").fetchone() is None
    assert len(list((project_path / "backups").glob("*.sqlite"))) == 1


def test_failed_create_leaves_no_final_or_staging_directory(tmp_path: Path) -> None:
    project_path = tmp_path / "invalid.irproj"
    service = ProjectService()
    with pytest.raises(ProjectOperationError) as raised:
        service.create(
            path=str(project_path),
            application_instance_id=str(uuid4()),
            application_version="0.1.0",
            name="Проект",
            project_number="",
            description="",
            status="invalid",
        )
    assert raised.value.code == "storage_error"
    assert not project_path.exists()
    assert list(tmp_path.glob("*.creating-*")) == []


class _DelayedClock:
    def __init__(self, expire_on_call: int) -> None:
        self._expire_on_call = expire_on_call
        self._calls = 0

    def __call__(self) -> float:
        self._calls += 1
        return 2.0 if self._calls >= self._expire_on_call else 0.0


class _ControlledClock:
    def __init__(self) -> None:
        self.expired = False

    def __call__(self) -> float:
        return 2.0 if self.expired else 0.0


def test_delayed_create_times_out_without_final_or_staging_directory(tmp_path: Path) -> None:
    project_path = tmp_path / "delayed-create.irproj"
    clock = _DelayedClock(expire_on_call=3)
    deadline = RequestDeadline.start(1_000, clock=clock)
    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().create(
            path=str(project_path),
            application_instance_id=str(uuid4()),
            application_version="0.1.0",
            name="Проект",
            project_number="",
            description="",
            status="draft",
            deadline=deadline,
        )
    assert raised.value.code == "timeout"
    assert not project_path.exists()
    assert list(tmp_path.glob("*.creating-*")) == []


def test_delayed_open_times_out_and_releases_lock(tmp_path: Path) -> None:
    project_path = tmp_path / "delayed-open.irproj"
    service = _create_project(project_path)
    service.close()
    clock = _DelayedClock(expire_on_call=3)
    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(
            path=str(project_path),
            application_instance_id=str(uuid4()),
            deadline=RequestDeadline.start(1_000, clock=clock),
        )
    assert raised.value.code == "timeout"
    reopened = ProjectService()
    reopened.open(path=str(project_path), application_instance_id=str(uuid4()))
    reopened.close()


def test_overview_failure_does_not_leave_closed_session_referenced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "overview-failure.irproj"
    service = _create_project(project_path)
    service.close()

    def fail_overview(_session: ProjectSession) -> object:
        raise RuntimeError("overview failed")

    monkeypatch.setattr(ProjectSession, "overview", fail_overview)
    opening_service = ProjectService()
    with pytest.raises(ProjectOperationError, match="открыть"):
        opening_service.open(path=str(project_path), application_instance_id=str(uuid4()))
    assert opening_service.has_active_session is False
    monkeypatch.undo()

    reopened = ProjectService()
    assert reopened.open(path=str(project_path), application_instance_id=str(uuid4())).name == "Проект рабочего колеса"
    reopened.close()


def test_delayed_backup_removes_partial_file_and_keeps_session(tmp_path: Path) -> None:
    project_path = tmp_path / "delayed-backup.irproj"
    service = _create_project(project_path)
    clock = _DelayedClock(expire_on_call=5)
    with pytest.raises(ProjectOperationError) as raised:
        service.create_backup(deadline=RequestDeadline.start(1_000, clock=clock))
    assert raised.value.code == "timeout"
    assert list((project_path / "backups").glob("*.sqlite")) == []
    assert service.get_overview().record_revision == 1
    service.close()


def test_backup_quick_check_is_interrupted_at_its_deadline() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE values_for_check (value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO values_for_check VALUES (?)",
            ((str(index),) for index in range(1_000)),
        )
        clock = _DelayedClock(expire_on_call=2)
        with pytest.raises(ProjectOperationError) as raised:
            project_database.quick_check_with_deadline(
                connection,
                RequestDeadline.start(1_000, clock=clock),
                progress_steps=1,
            )
        assert raised.value.code == "timeout"
    finally:
        connection.close()


def test_owned_backup_cleanup_does_not_scan_the_backup_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_path = tmp_path / "owned.sqlite"
    backup_path.write_bytes(b"owned")
    identity = inspect_reserved_file(backup_path, backup_path.name)
    assert identity is not None

    def fail_on_directory_scan(_path: Path, _pattern: str) -> Iterator[Path]:
        raise AssertionError("backup_directory_was_scanned")

    monkeypatch.setattr(Path, "glob", fail_on_directory_scan)
    assert remove_owned_backup(VerifiedBackup(path=backup_path, identity=identity))
    assert not backup_path.exists()


def test_backup_quick_check_timeout_removes_only_new_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "quick-check-timeout.irproj"
    service = _create_project(project_path)
    previous_backup = project_path / "backups" / "previous.sqlite"
    previous_backup.write_bytes(b"previous backup")
    clock = _ControlledClock()
    deadline = RequestDeadline.start(1_000, clock=clock)

    def expire_during_quick_check(
        _connection: sqlite3.Connection,
        active_deadline: RequestDeadline | None,
        **_kwargs: object,
    ) -> str:
        clock.expired = True
        assert active_deadline is not None
        active_deadline.check("backup_verify")
        raise AssertionError("deadline_check_did_not_raise")

    monkeypatch.setattr(project_database, "quick_check_with_deadline", expire_during_quick_check)
    with pytest.raises(ProjectOperationError) as raised:
        service.create_backup(deadline=deadline)
    assert raised.value.code == "timeout"
    assert list((project_path / "backups").iterdir()) == [previous_backup]
    assert service.get_overview().record_revision == 1
    service.close()


def test_hash_deadline_removes_only_new_backup_and_keeps_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "hash-timeout.irproj"
    service = _create_project(project_path)
    previous_backup = project_path / "backups" / "previous.sqlite"
    previous_backup.write_bytes(b"previous backup")
    controlled_clock = _ControlledClock()
    deadline = RequestDeadline.start(1_000, clock=controlled_clock)
    original_hash = sha256_file

    def expire_during_hash(path: Path, active_deadline: RequestDeadline | None = None) -> str:
        controlled_clock.expired = True
        return original_hash(path, active_deadline)

    monkeypatch.setattr(project_session, "sha256_file", expire_during_hash)
    with pytest.raises(ProjectOperationError) as raised:
        service.create_backup(deadline=deadline)
    assert raised.value.code == "timeout"
    assert list((project_path / "backups").iterdir()) == [previous_backup]
    assert previous_backup.read_bytes() == b"previous backup"
    assert service.get_overview().record_revision == 1
    service.close()


def test_hash_read_error_removes_only_new_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_path = tmp_path / "hash-read-error.irproj"
    service = _create_project(project_path)
    previous_backup = project_path / "backups" / "previous.sqlite"
    previous_backup.write_bytes(b"previous backup")

    def fail_hash(_path: Path, _deadline: RequestDeadline | None = None) -> str:
        raise OSError("hash read failed")

    monkeypatch.setattr(project_session, "sha256_file", fail_hash)
    with pytest.raises(OSError, match="hash read failed"):
        service.create_backup()
    assert list((project_path / "backups").iterdir()) == [previous_backup]
    assert service.get_overview().record_revision == 1
    service.close()


def test_hash_cleanup_does_not_delete_substituted_previous_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "hash-substitution.irproj"
    service = _create_project(project_path)
    previous_backup = project_path / "backups" / "previous.sqlite"
    previous_backup.write_bytes(b"previous backup")
    displaced_new_backups: list[Path] = []
    substituted_paths: list[Path] = []

    def substitute_before_failure(path: Path, _deadline: RequestDeadline | None = None) -> str:
        displaced_new_backup = path.with_name("displaced-new.sqlite")
        displaced_new_backups.append(displaced_new_backup)
        substituted_paths.append(path)
        path.replace(displaced_new_backup)
        previous_backup.replace(path)
        path.with_name(f"{path.name}-wal").write_bytes(b"previous sidecar")
        raise OSError("hash substitution")

    monkeypatch.setattr(project_session, "sha256_file", substitute_before_failure)
    with pytest.raises(OSError, match="hash substitution"):
        service.create_backup()
    assert len(displaced_new_backups) == 1 and not displaced_new_backups[0].exists()
    assert len(substituted_paths) == 1
    substituted_path = substituted_paths[0]
    assert substituted_path.read_bytes() == b"previous backup"
    assert substituted_path.with_name(f"{substituted_path.name}-wal").read_bytes() == b"previous sidecar"
    assert service.get_overview().record_revision == 1
    service.close()


def test_open_database_closes_connection_when_wal_configuration_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[sqlite3.Connection] = []

    project_path = tmp_path / "configuration-failure.irproj"
    service = _create_project(project_path)
    service.close()
    database_path = project_path / "project.sqlite"
    manifest = read_manifest(project_path / "project-manifest.json")
    expected_identity = inspect_reserved_file(database_path, database_path.name)
    assert expected_identity is not None
    expected_database_identity = project_database.probe_project_database_identity(
        database_path,
        manifest,
        project_schema.PROJECT_SCHEMA_VERSION,
    )

    def capture_connection(database_uri: str) -> sqlite3.Connection:
        connection = sqlite3.connect(database_uri, uri=True)
        captured.append(connection)
        return connection

    def fail_configuration(_connection: sqlite3.Connection) -> None:
        raise ProjectOperationError("storage_error", "WAL unavailable")

    monkeypatch.setattr(project_database, "configure_project_connection", fail_configuration)
    with pytest.raises(ProjectOperationError, match="WAL unavailable"):
        project_database.open_project_database(
            database_path,
            expected_identity,
            expected_database_identity,
            manifest,
            connection_factory=capture_connection,
        )
    assert len(captured) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        captured[0].execute("SELECT 1")


def test_write_connection_factory_cannot_redirect_to_external_database(tmp_path: Path) -> None:
    project_path = tmp_path / "connection-binding.irproj"
    service = _create_project(project_path)
    service.close()
    database_path = project_path / "project.sqlite"
    manifest = read_manifest(project_path / "project-manifest.json")
    expected_path_identity = inspect_reserved_file(database_path, database_path.name)
    assert expected_path_identity is not None
    expected_database_identity = project_database.probe_project_database_identity(
        database_path,
        manifest,
        project_schema.PROJECT_SCHEMA_VERSION,
    )
    external_database = tmp_path / "external-connection.sqlite"
    shutil.copy2(database_path, external_database)
    with closing(sqlite3.connect(external_database)) as connection:
        assert str(connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]).lower() == "delete"
    before_hash = _sha256(external_database)
    before_mtime = external_database.stat().st_mtime_ns

    def connect_external(_database_uri: str) -> sqlite3.Connection:
        return sqlite3.connect(external_database)

    with pytest.raises(ProjectOperationError, match=r"другой project\.sqlite"):
        project_database.open_project_database(
            database_path,
            expected_path_identity,
            expected_database_identity,
            manifest,
            connection_factory=connect_external,
        )
    assert _sha256(external_database) == before_hash
    assert external_database.stat().st_mtime_ns == before_mtime
    assert _read_journal_mode_without_writes(external_database) == "delete"
    assert not external_database.with_name(f"{external_database.name}-wal").exists()
    assert not external_database.with_name(f"{external_database.name}-shm").exists()


def test_os_lock_blocks_second_process_and_releases_after_crash(tmp_path: Path) -> None:
    project_path = tmp_path / "lock.irproj"
    service = _create_project(project_path)
    service.close()
    source_root = Path(__file__).resolve().parents[2] / "src"
    code = (
        "from impeller_reliability.application.project_service import ProjectService\n"
        "import sys,time,uuid\n"
        "service=ProjectService()\n"
        "service.open(path=sys.argv[1], application_instance_id=str(uuid.uuid4()))\n"
        "print('READY', flush=True)\n"
        "time.sleep(60)\n"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source_root)
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(project_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "READY"
        with pytest.raises(ProjectOperationError) as raised:
            ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
        assert raised.value.code == "project_locked"
    finally:
        process.kill()
        process.wait(timeout=10)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    deadline = time.monotonic() + 5
    while True:
        reopened = ProjectService()
        try:
            reopened.open(path=str(project_path), application_instance_id=str(uuid4()))
            reopened.close()
            break
        except ProjectOperationError as error:
            if error.code != "project_locked" or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
