from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
import sqlite3
from typing import Final, Literal
from uuid import RFC_4122, UUID

from pydantic import JsonValue, TypeAdapter, ValidationError

from impeller_reliability.persistence.audit import audit_now, insert_audit
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.sqlite_deadline import sqlite_query_rows_with_deadline
from impeller_reliability.persistence.timestamps import require_canonical_utc_timestamp
from impeller_reliability.worker.deadline import RequestDeadline

CompletenessWarning = Literal[
    "customer_address_missing",
    "wheel_nominal_diameter_missing",
    "wheel_nominal_speed_missing",
    "specimen_working_diameter_missing",
]

CUSTOMER_FIELDS: Final = ("fullName", "legalAddress", "actualAddress", "notes")
WHEEL_FIELDS: Final = (
    "fullName",
    "designation",
    "nominalDiameterMm",
    "nominalSpeedRpm",
    "bladeCount",
    "geometryDescription",
    "compositionDescription",
    "materialDescription",
    "notes",
)
SPECIMEN_FIELDS: Final = (
    "wheelModelId",
    "identificationNumber",
    "batchNumber",
    "marking",
    "manufacturedOn",
    "receivedOn",
    "workingDiameterMm",
    "initialConditionNotes",
    "notes",
)
MAX_DOSSIER_AUDIT_PAYLOAD_BYTES: Final = 250_000
JSON_OBJECT_ADAPTER: Final = TypeAdapter(dict[str, JsonValue])
STRING_LIST_ADAPTER: Final = TypeAdapter(list[str])


@dataclass(frozen=True, slots=True)
class CustomerProfile:
    project_id: str
    full_name: str
    legal_address: str
    actual_address: str
    notes: str
    record_revision: int
    created_at_utc: str
    updated_at_utc: str
    warnings: tuple[CompletenessWarning, ...]


@dataclass(frozen=True, slots=True)
class WheelModel:
    wheel_model_id: str
    full_name: str
    designation: str
    nominal_diameter_mm: str | None
    nominal_speed_rpm: int | None
    blade_count: int | None
    geometry_description: str
    composition_description: str
    material_description: str
    notes: str
    record_revision: int
    archived_at_utc: str | None
    created_at_utc: str
    updated_at_utc: str
    warnings: tuple[CompletenessWarning, ...]


@dataclass(frozen=True, slots=True)
class Specimen:
    specimen_id: str
    wheel_model_id: str
    wheel_model_name: str
    identification_number: str
    batch_number: str
    marking: str
    manufactured_on: str | None
    received_on: str | None
    working_diameter_mm: str | None
    initial_condition_notes: str
    notes: str
    record_revision: int
    archived_at_utc: str | None
    created_at_utc: str
    updated_at_utc: str
    warnings: tuple[CompletenessWarning, ...]


def canonical_decimal(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().replace(",", ".")
    if normalized == "":
        return None
    if len(normalized) > 64 or re.fullmatch(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", normalized) is None:
        raise ValueError("invalid_decimal")
    try:
        decimal_value = Decimal(normalized)
    except InvalidOperation as error:
        raise ValueError("invalid_decimal") from error
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ValueError("invalid_decimal")
    rendered = format(decimal_value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered if rendered != "" else "0"


def canonical_date(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized == "":
        return None
    try:
        parsed = date.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError("invalid_date") from error
    if parsed.isoformat() != normalized:
        raise ValueError("invalid_date")
    return normalized


def canonical_uuid4(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError("invalid_uuid") from error
    if str(parsed) != value or parsed.variant != RFC_4122 or parsed.version != 4:
        raise ValueError("invalid_uuid")
    return value


class AnalystDossierRepository:
    def __init__(self, connection: sqlite3.Connection, project_id: str) -> None:
        self._connection = connection
        self._project_id = project_id

    def get_customer(self, deadline: RequestDeadline | None = None) -> CustomerProfile | None:
        _check_read_deadline(deadline, "customer_get")
        row = self._connection.execute(
            "SELECT project_id, full_name, legal_address, actual_address, notes, record_revision, created_at_utc, updated_at_utc FROM customer_profile WHERE project_id = ?",
            (self._project_id,),
        ).fetchone()
        result = None if row is None else _customer_from_row(row)
        _check_read_deadline(deadline, "customer_get")
        return result

    def upsert_customer(
        self,
        *,
        expected_revision: int | None,
        full_name: str,
        legal_address: str,
        actual_address: str,
        notes: str,
        deadline: RequestDeadline | None,
    ) -> CustomerProfile:
        full_name = _required_text(full_name, 300)
        legal_address = _optional_text(legal_address, 1_000)
        actual_address = _optional_text(actual_address, 1_000)
        notes = _optional_text(notes, 4_000)
        current = self.get_customer()
        values: dict[str, object] = {
            "fullName": full_name,
            "legalAddress": legal_address,
            "actualAddress": actual_address,
            "notes": notes,
        }
        if current is None:
            if expected_revision is not None:
                raise _revision_conflict(expected_revision, None)
            now = audit_now(self._connection)
            self._begin()
            try:
                self._connection.execute(
                    "INSERT INTO customer_profile (project_id, full_name, legal_address, actual_address, notes, record_revision, created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                    (self._project_id, full_name, legal_address, actual_address, notes, now, now),
                )
                insert_audit(
                    self._connection,
                    event_type="customer_profile.created",
                    actor_kind="user",
                    occurred_at_utc=now,
                    payload=_create_payload("customerProfile", self._project_id, values),
                )
                _commit(self._connection, deadline, "customer_create")
            except Exception:
                self._connection.rollback()
                raise
            created = self.get_customer()
            if created is None:
                raise AssertionError("customer_create_missing")
            return created
        if expected_revision != current.record_revision:
            raise _revision_conflict(expected_revision, current.record_revision)
        before = _customer_values(current)
        changes = _changes(before, values)
        if not changes:
            return current
        return self._update_customer(current, values, changes, deadline)

    def _update_customer(
        self,
        current: CustomerProfile,
        values: dict[str, object],
        changes: dict[str, dict[str, object]],
        deadline: RequestDeadline | None,
    ) -> CustomerProfile:
        now = audit_now(self._connection)
        next_revision = current.record_revision + 1
        self._begin()
        try:
            cursor = self._connection.execute(
                "UPDATE customer_profile SET full_name = ?, legal_address = ?, actual_address = ?, notes = ?, record_revision = ?, updated_at_utc = ? WHERE project_id = ? AND record_revision = ?",
                (
                    values["fullName"],
                    values["legalAddress"],
                    values["actualAddress"],
                    values["notes"],
                    next_revision,
                    now,
                    self._project_id,
                    current.record_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise _revision_conflict(current.record_revision, None)
            insert_audit(
                self._connection,
                event_type="customer_profile.updated",
                actor_kind="user",
                occurred_at_utc=now,
                payload=_update_payload("customerProfile", self._project_id, current.record_revision, next_revision, changes),
            )
            _commit(self._connection, deadline, "customer_update")
        except Exception:
            self._connection.rollback()
            raise
        updated = self.get_customer()
        if updated is None:
            raise AssertionError("customer_update_missing")
        return updated

    def create_wheel(self, values: dict[str, object], deadline: RequestDeadline | None) -> WheelModel:
        wheel_id = canonical_uuid4(str(values["wheelModelId"]))
        values = _normalize_wheel_values(values)
        existing_row = self._connection.execute(
            "SELECT * FROM wheel_models WHERE wheel_model_id = ?",
            (wheel_id,),
        ).fetchone()
        if existing_row is not None:
            existing = _wheel_from_row(existing_row)
            if existing.record_revision == 1 and existing.archived_at_utc is None and _wheel_values(existing) == values:
                return existing
            raise ProjectOperationError("duplicate_entity", "Идентификатор модели уже используется.")
        now = audit_now(self._connection)
        self._begin()
        try:
            self._connection.execute(
                """
                INSERT INTO wheel_models (
                    wheel_model_id, full_name, designation, nominal_diameter_mm,
                    nominal_speed_rpm, blade_count, geometry_description,
                    composition_description, material_description, notes,
                    record_revision, archived_at_utc, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?)
                """,
                _wheel_insert_values(wheel_id, values, now),
            )
            insert_audit(
                self._connection,
                event_type="wheel_model.created",
                actor_kind="user",
                occurred_at_utc=now,
                payload=_create_payload("wheelModel", wheel_id, values),
            )
            _commit(self._connection, deadline, "wheel_create")
        except Exception:
            self._connection.rollback()
            raise
        return self.get_wheel(wheel_id)

    def list_wheels(
        self,
        include_archived: bool,
        deadline: RequestDeadline | None = None,
    ) -> tuple[WheelModel, ...]:
        _check_read_deadline(deadline, "wheel_list")
        where = "" if include_archived else "WHERE archived_at_utc IS NULL"
        rows = self._connection.execute(f"SELECT * FROM wheel_models {where} ORDER BY lower(full_name), lower(designation), wheel_model_id").fetchall()
        result = tuple(_wheel_from_row(row) for row in rows)
        _check_read_deadline(deadline, "wheel_list")
        return result

    def get_wheel(self, wheel_id: str, deadline: RequestDeadline | None = None) -> WheelModel:
        _check_read_deadline(deadline, "wheel_get")
        row = self._connection.execute("SELECT * FROM wheel_models WHERE wheel_model_id = ?", (wheel_id,)).fetchone()
        if row is None:
            raise _not_found("Модель рабочего колеса не найдена.")
        result = _wheel_from_row(row)
        _check_read_deadline(deadline, "wheel_get")
        return result

    def update_wheel(
        self,
        wheel_id: str,
        expected_revision: int,
        values: dict[str, object],
        deadline: RequestDeadline | None,
    ) -> WheelModel:
        values = _normalize_wheel_values(values)
        current = self.get_wheel(wheel_id)
        if current.archived_at_utc is not None:
            raise _archived("Архивную модель нельзя изменять.")
        if expected_revision != current.record_revision:
            raise _revision_conflict(expected_revision, current.record_revision)
        changes = _changes(_wheel_values(current), values)
        if not changes:
            return current
        now = audit_now(self._connection)
        next_revision = current.record_revision + 1
        self._begin()
        try:
            cursor = self._connection.execute(
                """
                UPDATE wheel_models SET full_name=?, designation=?, nominal_diameter_mm=?,
                    nominal_speed_rpm=?, blade_count=?, geometry_description=?,
                    composition_description=?, material_description=?, notes=?,
                    record_revision=?, updated_at_utc=?
                WHERE wheel_model_id=? AND record_revision=? AND archived_at_utc IS NULL
                """,
                _wheel_update_values(values, next_revision, now, wheel_id, current.record_revision),
            )
            if cursor.rowcount != 1:
                raise _revision_conflict(expected_revision, None)
            insert_audit(
                self._connection,
                event_type="wheel_model.updated",
                actor_kind="user",
                occurred_at_utc=now,
                payload=_update_payload("wheelModel", wheel_id, current.record_revision, next_revision, changes),
            )
            _commit(self._connection, deadline, "wheel_update")
        except Exception:
            self._connection.rollback()
            raise
        return self.get_wheel(wheel_id)

    def set_wheel_archived(
        self,
        wheel_id: str,
        expected_revision: int,
        archived: bool,
        deadline: RequestDeadline | None,
    ) -> WheelModel:
        current = self.get_wheel(wheel_id)
        if expected_revision != current.record_revision:
            raise _revision_conflict(expected_revision, current.record_revision)
        if archived == (current.archived_at_utc is not None):
            return current
        if archived:
            active_specimens = int(
                self._connection.execute(
                    "SELECT count(*) FROM specimens WHERE wheel_model_id = ? AND archived_at_utc IS NULL",
                    (wheel_id,),
                ).fetchone()[0]
            )
            if active_specimens != 0:
                raise ProjectOperationError("entity_in_use", "Сначала архивируйте образцы этой модели.")
        now = audit_now(self._connection)
        archived_at = now if archived else None
        next_revision = current.record_revision + 1
        changes: dict[str, dict[str, object]] = {"archivedAtUtc": {"before": current.archived_at_utc, "after": archived_at}}
        self._begin()
        try:
            self._connection.execute(
                "UPDATE wheel_models SET archived_at_utc=?, record_revision=?, updated_at_utc=? WHERE wheel_model_id=? AND record_revision=?",
                (archived_at, next_revision, now, wheel_id, current.record_revision),
            )
            insert_audit(
                self._connection,
                event_type="wheel_model.archived" if archived else "wheel_model.restored",
                actor_kind="user",
                occurred_at_utc=now,
                payload=_update_payload("wheelModel", wheel_id, current.record_revision, next_revision, changes),
            )
            _commit(self._connection, deadline, "wheel_archive" if archived else "wheel_restore")
        except Exception:
            self._connection.rollback()
            raise
        return self.get_wheel(wheel_id)

    def create_specimen(self, values: dict[str, object], deadline: RequestDeadline | None) -> Specimen:
        specimen_id = canonical_uuid4(str(values["specimenId"]))
        values = _normalize_specimen_values(values)
        self._require_active_wheel(str(values["wheelModelId"]))
        existing_row = self._connection.execute(
            """
            SELECT s.*, w.full_name AS wheel_model_name
            FROM specimens s JOIN wheel_models w ON w.wheel_model_id = s.wheel_model_id
            WHERE s.specimen_id = ?
            """,
            (specimen_id,),
        ).fetchone()
        if existing_row is not None:
            existing = _specimen_from_row(existing_row)
            if existing.record_revision == 1 and existing.archived_at_utc is None and _specimen_values(existing) == values:
                return existing
            raise ProjectOperationError("duplicate_entity", "Идентификатор образца уже используется.")
        now = audit_now(self._connection)
        self._begin()
        try:
            self._connection.execute(
                """
                INSERT INTO specimens (
                    specimen_id, wheel_model_id, identification_number, batch_number,
                    marking, manufactured_on, received_on, working_diameter_mm,
                    initial_condition_notes, notes, record_revision, archived_at_utc,
                    created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?)
                """,
                _specimen_insert_values(specimen_id, values, now),
            )
            insert_audit(
                self._connection,
                event_type="specimen.created",
                actor_kind="user",
                occurred_at_utc=now,
                payload=_create_payload("specimen", specimen_id, values),
            )
            _commit(self._connection, deadline, "specimen_create")
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            if "UNIQUE constraint failed: specimens.wheel_model_id, specimens.identification_number" in str(error):
                raise ProjectOperationError("duplicate_entity", "У этой модели уже есть образец с таким номером.") from error
            raise
        except Exception:
            self._connection.rollback()
            raise
        return self.get_specimen(specimen_id)

    def list_specimens(
        self,
        include_archived: bool,
        deadline: RequestDeadline | None = None,
    ) -> tuple[Specimen, ...]:
        _check_read_deadline(deadline, "specimen_list")
        where = "" if include_archived else "WHERE s.archived_at_utc IS NULL"
        rows = self._connection.execute(
            f"""
            SELECT s.*, w.full_name AS wheel_model_name
            FROM specimens s JOIN wheel_models w ON w.wheel_model_id = s.wheel_model_id
            {where}
            ORDER BY lower(w.full_name), lower(s.identification_number), s.specimen_id
            """
        ).fetchall()
        result = tuple(_specimen_from_row(row) for row in rows)
        _check_read_deadline(deadline, "specimen_list")
        return result

    def get_specimen(self, specimen_id: str, deadline: RequestDeadline | None = None) -> Specimen:
        _check_read_deadline(deadline, "specimen_get")
        row = self._connection.execute(
            """
            SELECT s.*, w.full_name AS wheel_model_name
            FROM specimens s JOIN wheel_models w ON w.wheel_model_id = s.wheel_model_id
            WHERE s.specimen_id = ?
            """,
            (specimen_id,),
        ).fetchone()
        if row is None:
            raise _not_found("Образец не найден.")
        result = _specimen_from_row(row)
        _check_read_deadline(deadline, "specimen_get")
        return result

    def update_specimen(
        self,
        specimen_id: str,
        expected_revision: int,
        values: dict[str, object],
        deadline: RequestDeadline | None,
    ) -> Specimen:
        values = _normalize_specimen_values(values)
        current = self.get_specimen(specimen_id)
        if current.archived_at_utc is not None:
            raise _archived("Архивный образец нельзя изменять.")
        if expected_revision != current.record_revision:
            raise _revision_conflict(expected_revision, current.record_revision)
        self._require_active_wheel(str(values["wheelModelId"]))
        changes = _changes(_specimen_values(current), values)
        if not changes:
            return current
        now = audit_now(self._connection)
        next_revision = current.record_revision + 1
        self._begin()
        try:
            cursor = self._connection.execute(
                """
                UPDATE specimens SET wheel_model_id=?, identification_number=?, batch_number=?,
                    marking=?, manufactured_on=?, received_on=?, working_diameter_mm=?,
                    initial_condition_notes=?, notes=?, record_revision=?, updated_at_utc=?
                WHERE specimen_id=? AND record_revision=? AND archived_at_utc IS NULL
                """,
                _specimen_update_values(values, next_revision, now, specimen_id, current.record_revision),
            )
            if cursor.rowcount != 1:
                raise _revision_conflict(expected_revision, None)
            insert_audit(
                self._connection,
                event_type="specimen.updated",
                actor_kind="user",
                occurred_at_utc=now,
                payload=_update_payload("specimen", specimen_id, current.record_revision, next_revision, changes),
            )
            _commit(self._connection, deadline, "specimen_update")
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            if "UNIQUE constraint failed: specimens.wheel_model_id, specimens.identification_number" in str(error):
                raise ProjectOperationError("duplicate_entity", "У этой модели уже есть образец с таким номером.") from error
            raise
        except Exception:
            self._connection.rollback()
            raise
        return self.get_specimen(specimen_id)

    def set_specimen_archived(
        self,
        specimen_id: str,
        expected_revision: int,
        archived: bool,
        deadline: RequestDeadline | None,
    ) -> Specimen:
        current = self.get_specimen(specimen_id)
        if expected_revision != current.record_revision:
            raise _revision_conflict(expected_revision, current.record_revision)
        if archived == (current.archived_at_utc is not None):
            return current
        if not archived:
            self._require_active_wheel(current.wheel_model_id)
        now = audit_now(self._connection)
        archived_at = now if archived else None
        next_revision = current.record_revision + 1
        changes: dict[str, dict[str, object]] = {"archivedAtUtc": {"before": current.archived_at_utc, "after": archived_at}}
        self._begin()
        try:
            self._connection.execute(
                "UPDATE specimens SET archived_at_utc=?, record_revision=?, updated_at_utc=? WHERE specimen_id=? AND record_revision=?",
                (archived_at, next_revision, now, specimen_id, current.record_revision),
            )
            insert_audit(
                self._connection,
                event_type="specimen.archived" if archived else "specimen.restored",
                actor_kind="user",
                occurred_at_utc=now,
                payload=_update_payload("specimen", specimen_id, current.record_revision, next_revision, changes),
            )
            _commit(self._connection, deadline, "specimen_archive" if archived else "specimen_restore")
        except Exception:
            self._connection.rollback()
            raise
        return self.get_specimen(specimen_id)

    def _require_active_wheel(self, wheel_id: str) -> None:
        wheel = self.get_wheel(wheel_id)
        if wheel.archived_at_utc is not None:
            raise _archived("Архивную модель нельзя назначить образцу.")

    def _begin(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")


def validate_dossier_evidence(
    connection: sqlite3.Connection,
    project_id: str,
    deadline: RequestDeadline | None = None,
) -> None:
    states: dict[tuple[str, str], tuple[int, dict[str, JsonValue]]] = {}
    rows = sqlite_query_rows_with_deadline(
        connection,
        """
        SELECT event_type, actor_kind,
               CASE WHEN typeof(payload_json)='text' AND length(CAST(payload_json AS BLOB)) <= ? THEN payload_json END
        FROM project_audit_events
        WHERE event_type NOT IN ('project.created', 'project.metadata_updated')
          AND event_type NOT LIKE 'case_document.%'
          AND event_type NOT GLOB 'r130sh_*'
        ORDER BY sequence
        """,
        (MAX_DOSSIER_AUDIT_PAYLOAD_BYTES,),
        deadline,
        "dossier_evidence_audit",
    )
    allowed_events = {
        "customer_profile.created",
        "customer_profile.updated",
        "wheel_model.created",
        "wheel_model.updated",
        "wheel_model.archived",
        "wheel_model.restored",
        "specimen.created",
        "specimen.updated",
        "specimen.archived",
        "specimen.restored",
    }
    create_fields = {
        "customerProfile": {"fullName", "legalAddress", "actualAddress", "notes"},
        "wheelModel": {
            "fullName",
            "designation",
            "nominalDiameterMm",
            "nominalSpeedRpm",
            "bladeCount",
            "geometryDescription",
            "compositionDescription",
            "materialDescription",
            "notes",
        },
        "specimen": {
            "wheelModelId",
            "identificationNumber",
            "batchNumber",
            "marking",
            "manufacturedOn",
            "receivedOn",
            "workingDiameterMm",
            "initialConditionNotes",
            "notes",
        },
    }
    with closing(rows):
        for row in rows:
            if deadline is not None:
                deadline.check("dossier_evidence_audit")
            event_type = str(row[0])
            if event_type not in allowed_events or str(row[1]) != "user" or not isinstance(row[2], str):
                raise _dossier_evidence_error()
            payload = _parse_json_object(row[2])
            entity_type = payload.get("entityType")
            entity_id = payload.get("entityId")
            if not isinstance(entity_type, str) or not isinstance(entity_id, str):
                raise _dossier_evidence_error()
            expected_type = _entity_type_for_event(event_type)
            if entity_type != expected_type:
                raise _dossier_evidence_error()
            if entity_type == "customerProfile":
                if entity_id != project_id:
                    raise _dossier_evidence_error()
            else:
                try:
                    canonical_uuid4(entity_id)
                except ValueError as error:
                    raise _dossier_evidence_error() from error
            key = (entity_type, entity_id)
            if event_type.endswith(".created"):
                if key in states or payload.get("toRevision") != 1:
                    raise _dossier_evidence_error()
                after = _require_json_object(payload.get("after"))
                fields = _require_string_list(payload.get("changedFields"))
                if len(fields) != len(set(fields)) or set(fields) != set(after) or set(fields) != create_fields[entity_type]:
                    raise _dossier_evidence_error()
                state = dict(after)
                if entity_type != "customerProfile":
                    state["archivedAtUtc"] = None
                states[key] = (1, state)
                continue
            current = states.get(key)
            if current is None:
                raise _dossier_evidence_error()
            revision, state = current
            if payload.get("fromRevision") != revision or payload.get("toRevision") != revision + 1:
                raise _dossier_evidence_error()
            changes = _require_json_object(payload.get("changes"))
            changed_fields = _require_string_list(payload.get("changedFields"))
            if not changed_fields or len(changed_fields) != len(set(changed_fields)) or set(changed_fields) != set(changes):
                raise _dossier_evidence_error()
            changed_field_set = set(changed_fields)
            if event_type.endswith(".updated"):
                if not changed_field_set <= create_fields[entity_type]:
                    raise _dossier_evidence_error()
            elif changed_field_set != {"archivedAtUtc"}:
                raise _dossier_evidence_error()
            next_state = dict(state)
            for field, raw_change in changes.items():
                change = _require_json_object(raw_change)
                if set(change) != {"before", "after"}:
                    raise _dossier_evidence_error()
                if field not in next_state or change["before"] != next_state[field]:
                    raise _dossier_evidence_error()
                next_state[field] = change["after"]
            if event_type.endswith(".archived"):
                archived_change = _require_json_object(changes["archivedAtUtc"])
                if archived_change["before"] is not None or not isinstance(archived_change["after"], str):
                    raise _dossier_evidence_error()
            elif event_type.endswith(".restored"):
                restored_change = _require_json_object(changes["archivedAtUtc"])
                if not isinstance(restored_change["before"], str) or restored_change["after"] is not None:
                    raise _dossier_evidence_error()
            states[key] = (revision + 1, next_state)

    repository = AnalystDossierRepository(connection, project_id)
    expected: dict[tuple[str, str], tuple[int, dict[str, JsonValue]]] = {}
    try:
        customer = repository.get_customer()
        if customer is not None:
            expected[("customerProfile", customer.project_id)] = (
                customer.record_revision,
                JSON_OBJECT_ADAPTER.validate_python(_customer_values(customer), strict=True),
            )
        for wheel in repository.list_wheels(True):
            values = _wheel_values(wheel)
            values["archivedAtUtc"] = wheel.archived_at_utc
            expected[("wheelModel", wheel.wheel_model_id)] = (
                wheel.record_revision,
                JSON_OBJECT_ADAPTER.validate_python(values, strict=True),
            )
        for specimen in repository.list_specimens(True):
            values = _specimen_values(specimen)
            values["archivedAtUtc"] = specimen.archived_at_utc
            expected[("specimen", specimen.specimen_id)] = (
                specimen.record_revision,
                JSON_OBJECT_ADAPTER.validate_python(values, strict=True),
            )
    except (TypeError, ValueError, ValidationError) as error:
        raise _dossier_evidence_error() from error
    if states != expected:
        raise _dossier_evidence_error()
    invalid_archive_state = int(
        connection.execute(
            """
            SELECT count(*)
            FROM specimens s JOIN wheel_models w ON w.wheel_model_id = s.wheel_model_id
            WHERE s.archived_at_utc IS NULL AND w.archived_at_utc IS NOT NULL
            """
        ).fetchone()[0]
    )
    if invalid_archive_state != 0:
        raise _dossier_evidence_error()


def _entity_type_for_event(event_type: str) -> str:
    if event_type.startswith("customer_profile."):
        return "customerProfile"
    if event_type.startswith("wheel_model."):
        return "wheelModel"
    if event_type.startswith("specimen."):
        return "specimen"
    raise _dossier_evidence_error()


def _dossier_evidence_error() -> ProjectOperationError:
    return ProjectOperationError("corrupt_project", "Audit evidence и сведения дела не согласованы.")


def _parse_json_object(value: str) -> dict[str, JsonValue]:
    try:
        return JSON_OBJECT_ADAPTER.validate_json(value, strict=True)
    except ValidationError as error:
        raise _dossier_evidence_error() from error


def _require_json_object(value: JsonValue | None) -> dict[str, JsonValue]:
    try:
        return JSON_OBJECT_ADAPTER.validate_python(value, strict=True)
    except ValidationError as error:
        raise _dossier_evidence_error() from error


def _require_string_list(value: JsonValue | None) -> list[str]:
    try:
        return STRING_LIST_ADAPTER.validate_python(value, strict=True)
    except ValidationError as error:
        raise _dossier_evidence_error() from error


def _customer_from_row(row: sqlite3.Row) -> CustomerProfile:
    warnings: tuple[CompletenessWarning, ...] = ("customer_address_missing",) if str(row[2]).strip() == "" or str(row[3]).strip() == "" else ()
    return CustomerProfile(
        project_id=str(row[0]),
        full_name=str(row[1]),
        legal_address=str(row[2]),
        actual_address=str(row[3]),
        notes=str(row[4]),
        record_revision=int(row[5]),
        created_at_utc=str(row[6]),
        updated_at_utc=str(row[7]),
        warnings=warnings,
    )


def _wheel_from_row(row: sqlite3.Row) -> WheelModel:
    warnings: list[CompletenessWarning] = []
    if row[3] is None:
        warnings.append("wheel_nominal_diameter_missing")
    if row[4] is None:
        warnings.append("wheel_nominal_speed_missing")
    nominal_diameter = _stored_canonical_decimal(row[3])
    return WheelModel(
        wheel_model_id=canonical_uuid4(str(row[0])),
        full_name=str(row[1]),
        designation=str(row[2]),
        nominal_diameter_mm=nominal_diameter,
        nominal_speed_rpm=_stored_positive_int(row[4]),
        blade_count=_stored_positive_int(row[5]),
        geometry_description=str(row[6]),
        composition_description=str(row[7]),
        material_description=str(row[8]),
        notes=str(row[9]),
        record_revision=int(row[10]),
        archived_at_utc=_stored_timestamp(row[11]),
        created_at_utc=require_canonical_utc_timestamp(str(row[12])),
        updated_at_utc=require_canonical_utc_timestamp(str(row[13])),
        warnings=tuple(warnings),
    )


def _specimen_from_row(row: sqlite3.Row) -> Specimen:
    warnings: tuple[CompletenessWarning, ...] = ("specimen_working_diameter_missing",) if row[7] is None else ()
    return Specimen(
        specimen_id=canonical_uuid4(str(row[0])),
        wheel_model_id=canonical_uuid4(str(row[1])),
        wheel_model_name=str(row[14]),
        identification_number=str(row[2]),
        batch_number=str(row[3]),
        marking=str(row[4]),
        manufactured_on=_stored_date(row[5]),
        received_on=_stored_date(row[6]),
        working_diameter_mm=_stored_canonical_decimal(row[7]),
        initial_condition_notes=str(row[8]),
        notes=str(row[9]),
        record_revision=int(row[10]),
        archived_at_utc=_stored_timestamp(row[11]),
        created_at_utc=require_canonical_utc_timestamp(str(row[12])),
        updated_at_utc=require_canonical_utc_timestamp(str(row[13])),
        warnings=warnings,
    )


def _customer_values(customer: CustomerProfile) -> dict[str, object]:
    return {"fullName": customer.full_name, "legalAddress": customer.legal_address, "actualAddress": customer.actual_address, "notes": customer.notes}


def _wheel_values(wheel: WheelModel) -> dict[str, object]:
    return {
        "fullName": wheel.full_name,
        "designation": wheel.designation,
        "nominalDiameterMm": wheel.nominal_diameter_mm,
        "nominalSpeedRpm": wheel.nominal_speed_rpm,
        "bladeCount": wheel.blade_count,
        "geometryDescription": wheel.geometry_description,
        "compositionDescription": wheel.composition_description,
        "materialDescription": wheel.material_description,
        "notes": wheel.notes,
    }


def _normalize_wheel_values(values: dict[str, object]) -> dict[str, object]:
    return {
        "fullName": _required_text(values.get("fullName"), 300),
        "designation": _optional_text(values.get("designation"), 200),
        "nominalDiameterMm": canonical_decimal(_optional_nullable_text(values.get("nominalDiameterMm"))),
        "nominalSpeedRpm": _optional_positive_int(values.get("nominalSpeedRpm")),
        "bladeCount": _optional_positive_int(values.get("bladeCount")),
        "geometryDescription": _optional_text(values.get("geometryDescription"), 4_000),
        "compositionDescription": _optional_text(values.get("compositionDescription"), 4_000),
        "materialDescription": _optional_text(values.get("materialDescription"), 4_000),
        "notes": _optional_text(values.get("notes"), 4_000),
    }


def _normalize_specimen_values(values: dict[str, object]) -> dict[str, object]:
    wheel_id = values.get("wheelModelId")
    if not isinstance(wheel_id, str):
        raise ValueError("invalid_wheel_model_id")
    return {
        "wheelModelId": canonical_uuid4(wheel_id),
        "identificationNumber": _required_text(values.get("identificationNumber"), 200),
        "batchNumber": _optional_text(values.get("batchNumber"), 200),
        "marking": _optional_text(values.get("marking"), 500),
        "manufacturedOn": canonical_date(_optional_nullable_text(values.get("manufacturedOn"))),
        "receivedOn": canonical_date(_optional_nullable_text(values.get("receivedOn"))),
        "workingDiameterMm": canonical_decimal(_optional_nullable_text(values.get("workingDiameterMm"))),
        "initialConditionNotes": _optional_text(values.get("initialConditionNotes"), 4_000),
        "notes": _optional_text(values.get("notes"), 4_000),
    }


def _required_text(value: object, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid_text")
    normalized = value.strip()
    if normalized == "" or len(normalized) > maximum:
        raise ValueError("invalid_text")
    return normalized


def _optional_text(value: object, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid_text")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError("invalid_text")
    return normalized


def _optional_nullable_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid_text")
    return value


def _optional_positive_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("invalid_positive_integer")
    return value


def _stored_positive_int(value: object) -> int | None:
    return _optional_positive_int(value)


def _stored_canonical_decimal(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid_stored_decimal")
    canonical = canonical_decimal(value)
    if canonical != value:
        raise ValueError("noncanonical_stored_decimal")
    return canonical


def _stored_date(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid_stored_date")
    canonical = canonical_date(value)
    if canonical != value:
        raise ValueError("noncanonical_stored_date")
    return canonical


def _stored_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid_stored_timestamp")
    return require_canonical_utc_timestamp(value)


def _specimen_values(specimen: Specimen) -> dict[str, object]:
    return {
        "wheelModelId": specimen.wheel_model_id,
        "identificationNumber": specimen.identification_number,
        "batchNumber": specimen.batch_number,
        "marking": specimen.marking,
        "manufacturedOn": specimen.manufactured_on,
        "receivedOn": specimen.received_on,
        "workingDiameterMm": specimen.working_diameter_mm,
        "initialConditionNotes": specimen.initial_condition_notes,
        "notes": specimen.notes,
    }


def _wheel_insert_values(wheel_id: str, values: dict[str, object], now: str) -> tuple[object, ...]:
    return (wheel_id, *[values[field] for field in WHEEL_FIELDS], now, now)


def _wheel_update_values(values: dict[str, object], revision: int, now: str, wheel_id: str, expected_revision: int) -> tuple[object, ...]:
    return (*[values[field] for field in WHEEL_FIELDS], revision, now, wheel_id, expected_revision)


def _specimen_insert_values(specimen_id: str, values: dict[str, object], now: str) -> tuple[object, ...]:
    return (specimen_id, *[values[field] for field in SPECIMEN_FIELDS], now, now)


def _specimen_update_values(values: dict[str, object], revision: int, now: str, specimen_id: str, expected_revision: int) -> tuple[object, ...]:
    return (*[values[field] for field in SPECIMEN_FIELDS], revision, now, specimen_id, expected_revision)


def _changes(before: dict[str, object], after: dict[str, object]) -> dict[str, dict[str, object]]:
    return {field: {"before": before[field], "after": after[field]} for field in after if before[field] != after[field]}


def _create_payload(entity_type: str, entity_id: str, values: dict[str, object]) -> dict[str, object]:
    return {"entityType": entity_type, "entityId": entity_id, "toRevision": 1, "changedFields": list(values), "after": values}


def _update_payload(entity_type: str, entity_id: str, from_revision: int, to_revision: int, changes: dict[str, dict[str, object]]) -> dict[str, object]:
    return {"entityType": entity_type, "entityId": entity_id, "fromRevision": from_revision, "toRevision": to_revision, "changedFields": list(changes), "changes": changes}


def _commit(connection: sqlite3.Connection, deadline: RequestDeadline | None, stage: str) -> None:
    if deadline is not None:
        deadline.check(f"{stage}_commit")
    connection.commit()


def _check_read_deadline(deadline: RequestDeadline | None, stage: str) -> None:
    if deadline is not None:
        deadline.check(stage)


def _revision_conflict(expected: int | None, actual: int | None) -> ProjectOperationError:
    return ProjectOperationError("revision_conflict", "Сведения были изменены. Перечитайте актуальную редакцию.", details={"expectedRevision": expected, "actualRevision": actual})


def _not_found(message: str) -> ProjectOperationError:
    return ProjectOperationError("entity_not_found", message)


def _archived(message: str) -> ProjectOperationError:
    return ProjectOperationError("entity_archived", message)
