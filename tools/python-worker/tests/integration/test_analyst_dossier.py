from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from impeller_reliability.application.project_service import ProjectService
from impeller_reliability.persistence.analyst_dossier import canonical_date, canonical_decimal, canonical_uuid4
from impeller_reliability.persistence.project_database import (
    MIGRATIONS,
    ProjectMetadataSeed,
    ProjectMigrator,
    configure_project_connection,
    create_project_database,
)
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_manifest import ProjectManifest, write_manifest
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


def _wheel_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "wheelModelId": str(uuid4()),
        "fullName": "Рабочее колесо ВР-1",
        "designation": "ВР-1",
        "nominalDiameterMm": "500.0",
        "nominalSpeedRpm": 1500,
        "bladeCount": 12,
        "geometryDescription": "Радиальное",
        "compositionDescription": "Сварная конструкция",
        "materialDescription": "Сталь",
        "notes": "",
    }
    values.update(overrides)
    return values


def _specimen_values(wheel_id: str, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "specimenId": str(uuid4()),
        "wheelModelId": wheel_id,
        "identificationNumber": "SN-001",
        "batchNumber": "P-1",
        "marking": "01",
        "manufacturedOn": "2026-01-10",
        "receivedOn": "2026-02-10",
        "workingDiameterMm": "499.50",
        "initialConditionNotes": "Без повреждений",
        "notes": "",
    }
    values.update(overrides)
    return values


def test_schema_v1_contains_analyst_dossier_without_migration_backup(tmp_path: Path) -> None:
    project_path = tmp_path / "legacy.irproj"
    (project_path / "assets" / "documents").mkdir(parents=True)
    (project_path / "backups").mkdir()
    manifest = ProjectManifest(
        projectId=str(uuid4()),
        createdAtUtc=utc_now(),
        createdWithApplicationVersion="0.1.0",
    )
    write_manifest(project_path / "project-manifest.json", manifest)
    connection = create_project_database(project_path / "project.sqlite")
    try:
        ProjectMigrator((MIGRATIONS[0],)).initialize(
            connection,
            manifest,
            ProjectMetadataSeed("Старое дело", "D-OLD", "", "draft"),
        )
    finally:
        connection.close()

    service = ProjectService()
    overview = service.open(path=str(project_path), application_instance_id=str(uuid4()))
    assert overview.schema_version == 1
    service.close()

    assert list((project_path / "backups").iterdir()) == []
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='wheel_models'").fetchone() is not None


def test_customer_create_update_noop_conflict_and_warnings(tmp_path: Path) -> None:
    service = _create_project(tmp_path / "customer.irproj")
    assert service.get_customer() is None
    created = service.upsert_customer(
        expected_revision=None,
        values={"fullName": "Заказчик", "legalAddress": "", "actualAddress": "", "notes": ""},
        deadline=None,
    )
    assert created.record_revision == 1
    assert created.warnings == ("customer_address_missing",)

    unchanged = service.upsert_customer(
        expected_revision=1,
        values={"fullName": "Заказчик", "legalAddress": "", "actualAddress": "", "notes": ""},
        deadline=None,
    )
    assert unchanged.record_revision == 1
    updated = service.upsert_customer(
        expected_revision=1,
        values={"fullName": "Заказчик", "legalAddress": "Юридический", "actualAddress": "Фактический", "notes": "Уточнено"},
        deadline=None,
    )
    assert updated.record_revision == 2
    assert updated.warnings == ()
    with pytest.raises(ProjectOperationError) as raised:
        service.upsert_customer(expected_revision=1, values={"fullName": "Старый", "legalAddress": "", "actualAddress": "", "notes": ""}, deadline=None)
    assert raised.value.code == "revision_conflict"
    service.close()

    connection = sqlite3.connect(tmp_path / "customer.irproj" / "project.sqlite")
    try:
        events = connection.execute("SELECT event_type, payload_json FROM project_audit_events WHERE event_type LIKE 'customer_%' ORDER BY sequence").fetchall()
    finally:
        connection.close()
    assert [row[0] for row in events] == ["customer_profile.created", "customer_profile.updated"]
    payload = json.loads(events[1][1])
    assert payload["changedFields"] == ["legalAddress", "actualAddress", "notes"]


def test_wheel_crud_archive_restore_and_incomplete_warnings(tmp_path: Path) -> None:
    service = _create_project(tmp_path / "wheels.irproj")
    incomplete = service.create_wheel(_wheel_values(nominalDiameterMm=None, nominalSpeedRpm=None), None)
    assert incomplete.warnings == ("wheel_nominal_diameter_missing", "wheel_nominal_speed_missing")
    complete = service.create_wheel(_wheel_values(fullName="Модель Б", designation="B"), None)
    assert complete.nominal_diameter_mm == "500"
    assert [item.full_name for item in service.list_wheels(False)] == ["Модель Б", "Рабочее колесо ВР-1"]

    unchanged = service.update_wheel(complete.wheel_model_id, 1, _wheel_values(fullName="Модель Б", designation="B"), None)
    assert unchanged.record_revision == 1
    updated = service.update_wheel(complete.wheel_model_id, 1, _wheel_values(fullName="Модель Б2", designation="B"), None)
    assert updated.record_revision == 2
    archived = service.set_wheel_archived(updated.wheel_model_id, 2, True, None)
    assert archived.record_revision == 3 and archived.archived_at_utc is not None
    assert archived.wheel_model_id not in {item.wheel_model_id for item in service.list_wheels(False)}
    restored = service.set_wheel_archived(archived.wheel_model_id, 3, False, None)
    assert restored.record_revision == 4 and restored.archived_at_utc is None
    assert service.set_wheel_archived(restored.wheel_model_id, 4, False, None).record_revision == 4
    with pytest.raises(ProjectOperationError) as raised:
        service.update_wheel("00000000-0000-4000-8000-000000000000", 1, _wheel_values(), None)
    assert raised.value.code == "entity_not_found"
    service.close()


def test_specimen_rules_duplicate_and_model_in_use(tmp_path: Path) -> None:
    project_path = tmp_path / "specimens.irproj"
    service = _create_project(project_path)
    wheel = service.create_wheel(_wheel_values(), None)
    specimen = service.create_specimen(_specimen_values(wheel.wheel_model_id), None)
    assert specimen.working_diameter_mm == "499.5"
    assert specimen.wheel_model_name == wheel.full_name
    assert specimen.warnings == ()
    with pytest.raises(ProjectOperationError) as duplicate:
        service.create_specimen(_specimen_values(wheel.wheel_model_id), None)
    assert duplicate.value.code == "duplicate_entity"
    with pytest.raises(ProjectOperationError) as in_use:
        service.set_wheel_archived(wheel.wheel_model_id, 1, True, None)
    assert in_use.value.code == "entity_in_use"

    updated = service.update_specimen(specimen.specimen_id, 1, _specimen_values(wheel.wheel_model_id, workingDiameterMm=None, notes="Изменён"), None)
    assert updated.record_revision == 2
    assert updated.warnings == ("specimen_working_diameter_missing",)
    archived_specimen = service.set_specimen_archived(updated.specimen_id, 2, True, None)
    archived_wheel = service.set_wheel_archived(wheel.wheel_model_id, 1, True, None)
    with pytest.raises(ProjectOperationError) as archived_model:
        service.set_specimen_archived(archived_specimen.specimen_id, 3, False, None)
    assert archived_model.value.code == "entity_archived"
    restored_wheel = service.set_wheel_archived(archived_wheel.wheel_model_id, 2, False, None)
    restored_specimen = service.set_specimen_archived(archived_specimen.specimen_id, 3, False, None)
    assert restored_wheel.archived_at_utc is None and restored_specimen.archived_at_utc is None
    service.close()

    reopened = ProjectService()
    reopened.open(path=str(project_path), application_instance_id=str(uuid4()))
    assert reopened.get_specimen(specimen.specimen_id).notes == "Изменён"
    reopened.close()


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "text", "1e3"])
def test_canonical_decimal_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        canonical_decimal(value)


def test_canonical_values_are_strict() -> None:
    assert canonical_decimal("001.5000") == "1.5"
    assert canonical_decimal("500") == "500"
    assert canonical_decimal(None) is None
    assert canonical_decimal("") is None
    assert canonical_date("") is None
    assert canonical_date("2026-02-28") == "2026-02-28"
    with pytest.raises(ValueError):
        canonical_date("2026-02-30")
    with pytest.raises(ValueError):
        canonical_uuid4("not-a-uuid")


def test_expired_dossier_mutation_rolls_back_state_and_audit(tmp_path: Path) -> None:
    service = _create_project(tmp_path / "deadline.irproj")
    deadline = RequestDeadline(expires_at=1.0, _clock=lambda: 1.0)
    with pytest.raises(ProjectOperationError) as raised:
        service.upsert_customer(
            expected_revision=None,
            values={"fullName": "Заказчик", "legalAddress": "", "actualAddress": "", "notes": ""},
            deadline=deadline,
        )
    assert raised.value.code == "timeout"
    assert service.get_customer() is None
    service.close()
    with closing(sqlite3.connect(tmp_path / "deadline.irproj" / "project.sqlite")) as connection:
        assert int(connection.execute("SELECT count(*) FROM project_audit_events").fetchone()[0]) == 1


def test_tampered_dossier_audit_is_rejected_on_reopen(tmp_path: Path) -> None:
    project_path = tmp_path / "tampered.irproj"
    service = _create_project(project_path)
    wheel = service.create_wheel(_wheel_values(), None)
    service.close()
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        connection.execute("DROP TRIGGER project_audit_events_no_update")
        connection.execute("UPDATE project_audit_events SET payload_json = json_set(payload_json, '$.after.fullName', 'Подмена') WHERE event_type='wheel_model.created'")
        connection.execute("CREATE TRIGGER project_audit_events_no_update BEFORE UPDATE ON project_audit_events BEGIN SELECT RAISE(ABORT, 'project_audit_append_only'); END")
        connection.commit()
    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"
    assert wheel.full_name == "Рабочее колесо ВР-1"


def test_entity_ids_make_create_retry_idempotent(tmp_path: Path) -> None:
    service = _create_project(tmp_path / "idempotent-create.irproj")
    wheel_id = str(uuid4())
    wheel_values = {**_wheel_values(), "wheelModelId": wheel_id}
    first_wheel = service.create_wheel(wheel_values, None)
    retried_wheel = service.create_wheel(wheel_values, None)
    assert first_wheel == retried_wheel
    assert first_wheel.wheel_model_id == wheel_id
    assert len(service.list_wheels(True)) == 1

    specimen_id = str(uuid4())
    specimen_values = {**_specimen_values(wheel_id), "specimenId": specimen_id}
    first_specimen = service.create_specimen(specimen_values, None)
    retried_specimen = service.create_specimen(specimen_values, None)
    assert first_specimen == retried_specimen
    assert first_specimen.specimen_id == specimen_id
    assert len(service.list_specimens(True)) == 1
    service.close()


def test_noop_audit_revision_is_rejected_on_reopen(tmp_path: Path) -> None:
    project_path = tmp_path / "noop-audit.irproj"
    service = _create_project(project_path)
    wheel = service.create_wheel({**_wheel_values(), "wheelModelId": str(uuid4())}, None)
    service.close()
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        occurred_at = utc_now()
        connection.execute(
            "UPDATE wheel_models SET record_revision=2, updated_at_utc=? WHERE wheel_model_id=?",
            (occurred_at, wheel.wheel_model_id),
        )
        connection.execute(
            "INSERT INTO project_audit_events(event_id,event_type,occurred_at_utc,actor_kind,payload_json) VALUES(?,?,?,?,?)",
            (
                str(uuid4()),
                "wheel_model.updated",
                occurred_at,
                "user",
                json.dumps(
                    {
                        "entityType": "wheelModel",
                        "entityId": wheel.wheel_model_id,
                        "fromRevision": 1,
                        "toRevision": 2,
                        "changedFields": [],
                        "changes": {},
                    }
                ),
            ),
        )
        connection.commit()
    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"


def test_archived_model_with_active_specimen_is_rejected_on_reopen(tmp_path: Path) -> None:
    project_path = tmp_path / "invalid-archive-state.irproj"
    service = _create_project(project_path)
    wheel = service.create_wheel({**_wheel_values(), "wheelModelId": str(uuid4())}, None)
    service.create_specimen(
        {**_specimen_values(wheel.wheel_model_id), "specimenId": str(uuid4())},
        None,
    )
    service.close()
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        occurred_at = utc_now()
        connection.execute(
            "UPDATE wheel_models SET archived_at_utc=?, record_revision=2, updated_at_utc=? WHERE wheel_model_id=?",
            (occurred_at, occurred_at, wheel.wheel_model_id),
        )
        connection.execute(
            "INSERT INTO project_audit_events(event_id,event_type,occurred_at_utc,actor_kind,payload_json) VALUES(?,?,?,?,?)",
            (
                str(uuid4()),
                "wheel_model.archived",
                occurred_at,
                "user",
                json.dumps(
                    {
                        "entityType": "wheelModel",
                        "entityId": wheel.wheel_model_id,
                        "fromRevision": 1,
                        "toRevision": 2,
                        "changedFields": ["archivedAtUtc"],
                        "changes": {"archivedAtUtc": {"before": None, "after": occurred_at}},
                    }
                ),
            ),
        )
        connection.commit()
    with pytest.raises(ProjectOperationError) as raised:
        ProjectService().open(path=str(project_path), application_instance_id=str(uuid4()))
    assert raised.value.code == "corrupt_project"


def test_read_only_probe_observes_committed_wal_schema(tmp_path: Path) -> None:
    project_path = tmp_path / "wal-migration.irproj"
    (project_path / "assets" / "documents").mkdir(parents=True)
    (project_path / "backups").mkdir()
    manifest = ProjectManifest(
        projectId=str(uuid4()),
        createdAtUtc=utc_now(),
        createdWithApplicationVersion="0.1.0",
    )
    write_manifest(project_path / "project-manifest.json", manifest)
    writer = create_project_database(project_path / "project.sqlite")
    try:
        ProjectMigrator((MIGRATIONS[0],)).initialize(
            writer,
            manifest,
            ProjectMetadataSeed("Дело", "D-WAL", "", "draft"),
        )
        configure_project_connection(writer)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        ProjectMigrator(MIGRATIONS).migrate_existing(
            writer,
            project_path / "project.sqlite",
            project_path / "backups",
            manifest,
        )
        service = ProjectService()
        overview = service.open(path=str(project_path), application_instance_id=str(uuid4()))
        assert overview.schema_version == 1
        service.close()
    finally:
        writer.close()
