from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Final

from pydantic import JsonValue, TypeAdapter, ValidationError

from impeller_reliability.persistence.project_errors import ProjectOperationError

PROJECT_SCHEMA_VERSION: Final = 1
PROJECT_METADATA_FIELDS: Final = ("name", "projectNumber", "description", "status")
JSON_OBJECT_ADAPTER: Final = TypeAdapter(dict[str, JsonValue])
STRING_LIST_ADAPTER: Final = TypeAdapter(list[str])
METADATA_VALUES_ADAPTER: Final = TypeAdapter(dict[str, str])

SCHEMA_MIGRATIONS_TABLE_SQL: Final = "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, applied_at_utc TEXT NOT NULL)"
PROJECT_METADATA_TABLE_SQL: Final = """
CREATE TABLE project_metadata (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
    project_number TEXT NOT NULL CHECK (length(project_number) <= 100),
    description TEXT NOT NULL CHECK (length(description) <= 4000),
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'completed', 'archived')),
    record_revision INTEGER NOT NULL CHECK (record_revision >= 1),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    created_with_application_version TEXT NOT NULL
)
"""
PROJECT_AUDIT_EVENTS_TABLE_SQL: Final = """
CREATE TABLE project_audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    occurred_at_utc TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('application', 'user')),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
)
"""
PROJECT_AUDIT_NO_UPDATE_TRIGGER_SQL: Final = "CREATE TRIGGER project_audit_events_no_update BEFORE UPDATE ON project_audit_events BEGIN SELECT RAISE(ABORT, 'project_audit_append_only'); END"
PROJECT_AUDIT_NO_DELETE_TRIGGER_SQL: Final = "CREATE TRIGGER project_audit_events_no_delete BEFORE DELETE ON project_audit_events BEGIN SELECT RAISE(ABORT, 'project_audit_append_only'); END"


@dataclass(frozen=True, slots=True)
class SchemaObject:
    object_type: str
    name: str
    sql: str


@dataclass(frozen=True, slots=True)
class PublishedSchemaContract:
    version: int
    objects: tuple[SchemaObject, ...]
    migrations: tuple[tuple[int, str], ...]


SCHEMA_V1_CONTRACT: Final = PublishedSchemaContract(
    version=PROJECT_SCHEMA_VERSION,
    objects=(
        SchemaObject("table", "schema_migrations", SCHEMA_MIGRATIONS_TABLE_SQL),
        SchemaObject("table", "project_metadata", PROJECT_METADATA_TABLE_SQL),
        SchemaObject("table", "project_audit_events", PROJECT_AUDIT_EVENTS_TABLE_SQL),
        SchemaObject("trigger", "project_audit_events_no_update", PROJECT_AUDIT_NO_UPDATE_TRIGGER_SQL),
        SchemaObject("trigger", "project_audit_events_no_delete", PROJECT_AUDIT_NO_DELETE_TRIGGER_SQL),
    ),
    migrations=((1, "create_project_container"),),
)


def create_schema_v1_objects(connection: sqlite3.Connection) -> None:
    for schema_object in SCHEMA_V1_CONTRACT.objects:
        connection.execute(schema_object.sql)


def validate_published_schema(connection: sqlite3.Connection, schema_version: int) -> None:
    contract = _contract_for(schema_version)
    rows = connection.execute("SELECT type, name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
    actual = {(str(row[0]), str(row[1])): _normalize_sql(str(row[2])) for row in rows if row[2] is not None}
    expected = {(schema_object.object_type, schema_object.name): _normalize_sql(schema_object.sql) for schema_object in contract.objects}
    if actual != expected:
        raise ProjectOperationError(
            "corrupt_project",
            "Структура project.sqlite не соответствует опубликованной schema.",
        )
    migration_rows = tuple((int(row[0]), str(row[1])) for row in connection.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall())
    if migration_rows != contract.migrations:
        raise ProjectOperationError(
            "corrupt_project",
            "История migration project.sqlite не соответствует schema.",
        )


def validate_project_evidence(connection: sqlite3.Connection, project_id: str) -> None:
    metadata_rows = connection.execute("SELECT project_id, name, project_number, description, status, record_revision FROM project_metadata").fetchall()
    if len(metadata_rows) != 1 or str(metadata_rows[0][0]) != project_id:
        raise _evidence_error()
    metadata = metadata_rows[0]
    expected_current = {
        "name": str(metadata[1]),
        "projectNumber": str(metadata[2]),
        "description": str(metadata[3]),
        "status": str(metadata[4]),
    }
    record_revision = _require_int(metadata[5])
    event_rows = connection.execute("SELECT sequence, event_type, actor_kind, payload_json FROM project_audit_events ORDER BY sequence").fetchall()
    if not event_rows:
        raise _evidence_error()

    reconstructed: dict[str, str] | None = None
    expected_revision = 0
    for expected_sequence, row in enumerate(event_rows, start=1):
        if _require_int(row[0]) != expected_sequence:
            raise _evidence_error()
        payload = _parse_json_object(str(row[3]))
        if expected_sequence == 1:
            if str(row[1]) != "project.created" or str(row[2]) != "application":
                raise _evidence_error()
            if (
                payload.get("entityType") != "project"
                or payload.get("entityId") != project_id
                or _require_int(payload.get("toRevision")) != 1
                or _require_int(payload.get("schemaVersion")) != PROJECT_SCHEMA_VERSION
                or _require_string_list(payload.get("changedFields")) != list(PROJECT_METADATA_FIELDS)
            ):
                raise _evidence_error()
            reconstructed = _require_metadata_values(payload.get("after"))
            expected_revision = 1
            continue

        if reconstructed is None:
            raise _evidence_error()
        if str(row[1]) != "project.metadata_updated" or str(row[2]) != "user":
            raise _evidence_error()
        changed_fields = _require_string_list(payload.get("changedFields"))
        if not changed_fields or len(changed_fields) != len(set(changed_fields)):
            raise _evidence_error()
        if any(field not in PROJECT_METADATA_FIELDS for field in changed_fields):
            raise _evidence_error()
        changes = _require_json_object(payload.get("changes"))
        if (
            payload.get("entityType") != "project"
            or payload.get("entityId") != project_id
            or _require_int(payload.get("fromRevision")) != expected_revision
            or _require_int(payload.get("toRevision")) != expected_revision + 1
            or set(changes) != set(changed_fields)
        ):
            raise _evidence_error()
        for field in changed_fields:
            change = _require_json_object(changes[field])
            before = change.get("before")
            after = change.get("after")
            if set(change) != {"before", "after"} or before != reconstructed[field] or not isinstance(after, str):
                raise _evidence_error()
            reconstructed[field] = after
        expected_revision += 1

    if reconstructed != expected_current or expected_revision != record_revision:
        raise _evidence_error()


def _contract_for(schema_version: int) -> PublishedSchemaContract:
    if schema_version == SCHEMA_V1_CONTRACT.version:
        return SCHEMA_V1_CONTRACT
    raise ProjectOperationError(
        "corrupt_project",
        "Для версии project.sqlite отсутствует опубликованный schema contract.",
    )


def _normalize_sql(value: str) -> str:
    return " ".join(value.split()).removesuffix(";")


def _parse_json_object(value: str) -> dict[str, JsonValue]:
    try:
        return JSON_OBJECT_ADAPTER.validate_json(value, strict=True)
    except ValidationError as error:
        raise _evidence_error() from error


def _require_json_object(value: JsonValue | None) -> dict[str, JsonValue]:
    try:
        return JSON_OBJECT_ADAPTER.validate_python(value, strict=True)
    except ValidationError as error:
        raise _evidence_error() from error


def _require_string_list(value: JsonValue | None) -> list[str]:
    try:
        return STRING_LIST_ADAPTER.validate_python(value, strict=True)
    except ValidationError as error:
        raise _evidence_error() from error


def _require_metadata_values(value: JsonValue | None) -> dict[str, str]:
    try:
        values = METADATA_VALUES_ADAPTER.validate_python(value, strict=True)
    except ValidationError as error:
        raise _evidence_error() from error
    if set(values) != set(PROJECT_METADATA_FIELDS):
        raise _evidence_error()
    return values


def _require_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _evidence_error()
    return value


def _evidence_error() -> ProjectOperationError:
    return ProjectOperationError(
        "corrupt_project",
        "Audit evidence и metadata project.sqlite не согласованы.",
    )
