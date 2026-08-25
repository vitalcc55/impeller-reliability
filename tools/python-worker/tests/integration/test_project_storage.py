from __future__ import annotations

from collections.abc import Generator
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from impeller_reliability.application.project_service import ProjectService
from impeller_reliability.persistence import project_database
from impeller_reliability.persistence.project_database import MIGRATIONS, Migration, ProjectMigrator, configure_project_connection
from impeller_reliability.persistence.project_errors import ProjectOperationError
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


def test_newer_schema_is_not_modified(tmp_path: Path) -> None:
    project_path = tmp_path / "newer.irproj"
    service = _create_project(project_path)
    service.close()
    with _database(project_path) as connection:
        connection.execute("PRAGMA user_version = 99")
    before = (project_path / "project.sqlite").stat().st_mtime_ns

    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "incompatible_schema"
    assert (project_path / "project.sqlite").stat().st_mtime_ns == before
    assert list((project_path / "backups").iterdir()) == []


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


def test_open_database_closes_connection_when_wal_configuration_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[sqlite3.Connection] = []

    def capture_connection(database: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(database)
        captured.append(connection)
        return connection

    def fail_configuration(_connection: sqlite3.Connection) -> None:
        raise ProjectOperationError("storage_error", "WAL unavailable")

    monkeypatch.setattr(project_database, "configure_project_connection", fail_configuration)
    with pytest.raises(ProjectOperationError, match="WAL unavailable"):
        project_database.open_project_database(
            tmp_path / "failed.sqlite",
            connection_factory=capture_connection,
        )
    assert len(captured) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        captured[0].execute("SELECT 1")


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
