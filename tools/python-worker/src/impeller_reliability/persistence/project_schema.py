from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
import sqlite3
from typing import Final
from uuid import RFC_4122, UUID

from pydantic import JsonValue, TypeAdapter, ValidationError

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_values import (
    ProjectMetadataField,
    require_application_version,
    require_canonical_project_id,
    require_project_metadata_value,
)
from impeller_reliability.persistence.sqlite_deadline import (
    sqlite_deadline_guard,
    sqlite_query_rows_with_deadline,
)
from impeller_reliability.persistence.timestamps import parse_canonical_utc_timestamp
from impeller_reliability.worker.deadline import RequestDeadline

PROJECT_SCHEMA_VERSION: Final = 1
PROJECT_METADATA_FIELDS: Final = ("name", "projectNumber", "description", "status")
MAX_SCHEMA_SCALAR_BYTES: Final = 4_096
MAX_PROJECT_ID_BYTES: Final = 36
MAX_TIMESTAMP_BYTES: Final = 24
MAX_APPLICATION_VERSION_BYTES: Final = 64
# Update evidence holds before+after; escaped control characters may occupy six UTF-8 bytes per input character.
MAX_AUDIT_PAYLOAD_BYTES: Final = 2 * 6 * (200 + 100 + 4_000 + 9) + 4_096
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


def validate_published_schema(
    connection: sqlite3.Connection,
    schema_version: int,
    deadline: RequestDeadline | None = None,
) -> None:
    contract = _contract_for(schema_version)
    expected = {(schema_object.object_type, schema_object.name): _normalize_sql(schema_object.sql) for schema_object in contract.objects}
    actual: dict[tuple[str, str], str] = {}
    with sqlite_deadline_guard(connection, deadline, "project_schema_objects"):
        rows = connection.execute(
            """
            SELECT CASE WHEN typeof(type) = 'text' AND length(CAST(type AS BLOB)) <= 16 THEN type END,
                   CASE WHEN typeof(name) = 'text' AND length(CAST(name AS BLOB)) <= ? THEN name END,
                   CASE WHEN typeof(sql) = 'text' AND length(CAST(sql AS BLOB)) <= ? THEN sql END
            FROM sqlite_schema
            WHERE name NOT LIKE 'sqlite_%'
            """,
            (MAX_SCHEMA_SCALAR_BYTES, MAX_SCHEMA_SCALAR_BYTES),
        )
        for row in rows:
            _check_deadline(deadline, "project_schema_objects")
            key = (str(row[0]), str(row[1]))
            if row[2] is None or key not in expected:
                raise ProjectOperationError(
                    "corrupt_project",
                    "Структура project.sqlite не соответствует опубликованной schema.",
                )
            actual[key] = _normalize_sql(str(row[2]))
    if actual != expected:
        raise ProjectOperationError(
            "corrupt_project",
            "Структура project.sqlite не соответствует опубликованной schema.",
        )
    with sqlite_deadline_guard(connection, deadline, "project_schema_migrations"):
        migration_records = connection.execute(
            """
            SELECT version,
                   CASE WHEN typeof(name) = 'text' AND length(CAST(name AS BLOB)) <= ? THEN name END,
                   CASE WHEN typeof(applied_at_utc) = 'text' AND length(CAST(applied_at_utc AS BLOB)) <= ? THEN applied_at_utc END
            FROM schema_migrations
            ORDER BY version
            """,
            (MAX_SCHEMA_SCALAR_BYTES, MAX_TIMESTAMP_BYTES),
        ).fetchmany(len(contract.migrations) + 1)
    migration_rows = tuple((int(row[0]), str(row[1])) for row in migration_records)
    for row in migration_records:
        _check_deadline(deadline, "project_schema_migrations")
        _require_timestamp(str(row[2]))
    if migration_rows != contract.migrations:
        raise ProjectOperationError(
            "corrupt_project",
            "История migration project.sqlite не соответствует schema.",
        )


def validate_project_evidence(
    connection: sqlite3.Connection,
    project_id: str,
    project_created_at_utc: str,
    project_created_with_application_version: str,
    deadline: RequestDeadline | None = None,
) -> None:
    _check_deadline(deadline, "project_evidence_start")
    with sqlite_deadline_guard(connection, deadline, "project_evidence_metadata"):
        metadata_rows = connection.execute(
            """
            SELECT CASE WHEN typeof(project_id) = 'text' AND length(CAST(project_id AS BLOB)) <= ? THEN project_id END,
                   CASE WHEN typeof(name) = 'text' AND length(CAST(name AS BLOB)) <= 800 THEN name END,
                   CASE WHEN typeof(project_number) = 'text' AND length(CAST(project_number AS BLOB)) <= 400 THEN project_number END,
                   CASE WHEN typeof(description) = 'text' AND length(CAST(description AS BLOB)) <= 16000 THEN description END,
                   CASE WHEN typeof(status) = 'text' AND length(CAST(status AS BLOB)) <= 36 THEN status END,
                   CASE
                       WHEN typeof(record_revision) = 'integer' AND record_revision >= 1
                       THEN record_revision
                   END,
                   CASE WHEN typeof(created_at_utc) = 'text' AND length(CAST(created_at_utc AS BLOB)) <= ? THEN created_at_utc END,
                   CASE WHEN typeof(updated_at_utc) = 'text' AND length(CAST(updated_at_utc AS BLOB)) <= ? THEN updated_at_utc END,
                   CASE
                       WHEN typeof(created_with_application_version) = 'text'
                        AND length(CAST(created_with_application_version AS BLOB)) <= ?
                       THEN created_with_application_version
                   END
            FROM project_metadata
            """,
            (MAX_PROJECT_ID_BYTES, MAX_TIMESTAMP_BYTES, MAX_TIMESTAMP_BYTES, MAX_APPLICATION_VERSION_BYTES),
        ).fetchmany(2)
    if len(metadata_rows) != 1:
        raise _evidence_error()
    metadata = metadata_rows[0]
    if _require_project_id(metadata[0]) != project_id:
        raise _evidence_error()
    expected_current = {
        "name": _require_metadata_value("name", metadata[1]),
        "projectNumber": _require_metadata_value("projectNumber", metadata[2]),
        "description": _require_metadata_value("description", metadata[3]),
        "status": _require_metadata_value("status", metadata[4]),
    }
    record_revision = _require_int(metadata[5])
    manifest_created_at = _require_timestamp(project_created_at_utc)
    metadata_created_at = _require_timestamp(str(metadata[6]))
    metadata_updated_at = _require_timestamp(str(metadata[7]))
    manifest_application_version = _require_application_version(project_created_with_application_version)
    metadata_application_version = _require_application_version(metadata[8])
    if metadata_created_at != manifest_created_at or metadata_updated_at < metadata_created_at:
        raise _evidence_error()
    if metadata_application_version != manifest_application_version:
        raise _evidence_error()
    _check_deadline(deadline, "project_evidence_metadata")
    event_rows = sqlite_query_rows_with_deadline(
        connection,
        """
            SELECT sequence,
                   CASE
                       WHEN typeof(event_id) = 'text'
                        AND length(CAST(event_id AS BLOB)) <= 36
                       THEN event_id
                   END,
                   CASE
                       WHEN typeof(event_type) = 'text'
                        AND length(CAST(event_type AS BLOB)) <= 32
                       THEN event_type
                   END,
                   CASE
                       WHEN typeof(actor_kind) = 'text'
                        AND length(CAST(actor_kind AS BLOB)) <= 16
                       THEN actor_kind
                   END,
                   CASE
                       WHEN typeof(occurred_at_utc) = 'text'
                        AND length(CAST(occurred_at_utc AS BLOB)) <= ?
                       THEN occurred_at_utc
                   END,
                   CASE
                       WHEN typeof(payload_json) = 'text'
                        AND length(CAST(payload_json AS BLOB)) <= ?
                       THEN payload_json
                   END
            FROM project_audit_events
            ORDER BY sequence
            """,
        (MAX_TIMESTAMP_BYTES, MAX_AUDIT_PAYLOAD_BYTES),
        deadline,
        "project_evidence_audit",
    )

    with closing(event_rows):
        reconstructed, expected_revision, previous_event_at, event_count = _validate_audit_rows(
            event_rows,
            project_id,
            metadata_created_at,
            deadline,
        )

    if event_count == 0:
        raise _evidence_error()
    if reconstructed != expected_current or expected_revision != record_revision or previous_event_at != metadata_updated_at:
        raise _evidence_error()


def _validate_audit_rows(
    event_rows: Iterable[Sequence[object]],
    project_id: str,
    metadata_created_at: datetime,
    deadline: RequestDeadline | None,
) -> tuple[dict[str, str] | None, int, datetime, int]:
    reconstructed: dict[str, str] | None = None
    expected_revision = 0
    previous_event_at = metadata_created_at
    event_count = 0
    for expected_sequence, row in enumerate(event_rows, start=1):
        event_count = expected_sequence
        _check_deadline(deadline, "project_evidence_audit")
        if _require_int(row[0]) != expected_sequence:
            raise _evidence_error()
        _require_event_id(row[1])
        event_at = _require_timestamp(str(row[4]))
        if event_at < previous_event_at:
            raise _evidence_error()
        if not isinstance(row[5], str):
            raise _evidence_error()
        payload = _parse_json_object(row[5])
        if expected_sequence == 1:
            if str(row[2]) != "project.created" or str(row[3]) != "application":
                raise _evidence_error()
            if (
                payload.get("entityType") != "project"
                or payload.get("entityId") != project_id
                or _require_int(payload.get("toRevision")) != 1
                or _require_int(payload.get("schemaVersion")) != PROJECT_SCHEMA_VERSION
                or _require_string_list(payload.get("changedFields")) != list(PROJECT_METADATA_FIELDS)
            ):
                raise _evidence_error()
            if event_at != metadata_created_at:
                raise _evidence_error()
            reconstructed = _require_metadata_values(payload.get("after"))
            expected_revision = 1
            previous_event_at = event_at
            continue

        if reconstructed is None:
            raise _evidence_error()
        if str(row[2]) != "project.metadata_updated" or str(row[3]) != "user":
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
        previous_event_at = event_at
    return reconstructed, expected_revision, previous_event_at, event_count


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


def _require_event_id(value: object) -> str:
    if not isinstance(value, str):
        raise _evidence_error()
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise _evidence_error() from error
    if str(parsed) != value or parsed.variant != RFC_4122 or parsed.version != 4:
        raise _evidence_error()
    return value


def _require_timestamp(value: str) -> datetime:
    try:
        return parse_canonical_utc_timestamp(value)
    except ValueError as error:
        raise _evidence_error() from error


def _require_application_version(value: object) -> str:
    if not isinstance(value, str):
        raise _evidence_error()
    try:
        return require_application_version(value)
    except ValueError as error:
        raise _evidence_error() from error


def _require_project_id(value: object) -> str:
    if not isinstance(value, str):
        raise _evidence_error()
    try:
        return require_canonical_project_id(value)
    except ValueError as error:
        raise _evidence_error() from error


def _require_metadata_value(field: ProjectMetadataField, value: object) -> str:
    try:
        return require_project_metadata_value(field, value)
    except ValueError as error:
        raise _evidence_error() from error


def _evidence_error() -> ProjectOperationError:
    return ProjectOperationError(
        "corrupt_project",
        "Audit evidence и metadata project.sqlite не согласованы.",
    )


def _check_deadline(deadline: RequestDeadline | None, stage: str) -> None:
    if deadline is not None:
        deadline.check(stage)
