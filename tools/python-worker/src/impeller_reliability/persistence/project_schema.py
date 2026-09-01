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
MAX_AUDIT_PAYLOAD_BYTES: Final = 250_000
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
CUSTOMER_PROFILE_TABLE_SQL: Final = """
CREATE TABLE customer_profile (
    project_id TEXT PRIMARY KEY REFERENCES project_metadata(project_id),
    full_name TEXT NOT NULL CHECK (length(trim(full_name)) BETWEEN 1 AND 300),
    legal_address TEXT NOT NULL DEFAULT '' CHECK (length(legal_address) <= 1000),
    actual_address TEXT NOT NULL DEFAULT '' CHECK (length(actual_address) <= 1000),
    notes TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 4000),
    record_revision INTEGER NOT NULL CHECK (record_revision >= 1),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
)
"""
WHEEL_MODELS_TABLE_SQL: Final = """
CREATE TABLE wheel_models (
    wheel_model_id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL CHECK (length(trim(full_name)) BETWEEN 1 AND 300),
    designation TEXT NOT NULL DEFAULT '' CHECK (length(designation) <= 200),
    nominal_diameter_mm TEXT NULL CHECK (nominal_diameter_mm IS NULL OR length(nominal_diameter_mm) <= 64),
    nominal_speed_rpm INTEGER NULL CHECK (nominal_speed_rpm IS NULL OR nominal_speed_rpm > 0),
    blade_count INTEGER NULL CHECK (blade_count IS NULL OR blade_count > 0),
    geometry_description TEXT NOT NULL DEFAULT '' CHECK (length(geometry_description) <= 4000),
    composition_description TEXT NOT NULL DEFAULT '' CHECK (length(composition_description) <= 4000),
    material_description TEXT NOT NULL DEFAULT '' CHECK (length(material_description) <= 4000),
    notes TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 4000),
    record_revision INTEGER NOT NULL CHECK (record_revision >= 1),
    archived_at_utc TEXT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
)
"""
SPECIMENS_TABLE_SQL: Final = """
CREATE TABLE specimens (
    specimen_id TEXT PRIMARY KEY,
    wheel_model_id TEXT NOT NULL REFERENCES wheel_models(wheel_model_id),
    identification_number TEXT NOT NULL CHECK (length(trim(identification_number)) BETWEEN 1 AND 200),
    batch_number TEXT NOT NULL DEFAULT '' CHECK (length(batch_number) <= 200),
    marking TEXT NOT NULL DEFAULT '' CHECK (length(marking) <= 500),
    manufactured_on TEXT NULL,
    received_on TEXT NULL,
    working_diameter_mm TEXT NULL CHECK (working_diameter_mm IS NULL OR length(working_diameter_mm) <= 64),
    initial_condition_notes TEXT NOT NULL DEFAULT '' CHECK (length(initial_condition_notes) <= 4000),
    notes TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 4000),
    record_revision INTEGER NOT NULL CHECK (record_revision >= 1),
    archived_at_utc TEXT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(wheel_model_id, identification_number)
)
"""
WHEEL_MODELS_ARCHIVED_INDEX_SQL: Final = "CREATE INDEX wheel_models_archived_idx ON wheel_models(archived_at_utc)"
WHEEL_MODELS_NAME_INDEX_SQL: Final = "CREATE INDEX wheel_models_name_idx ON wheel_models(full_name, designation)"
SPECIMENS_ARCHIVED_INDEX_SQL: Final = "CREATE INDEX specimens_archived_idx ON specimens(archived_at_utc)"
SPECIMENS_MODEL_INDEX_SQL: Final = "CREATE INDEX specimens_model_idx ON specimens(wheel_model_id)"
SPECIMENS_IDENTIFICATION_INDEX_SQL: Final = "CREATE INDEX specimens_identification_idx ON specimens(identification_number)"
CASE_DOCUMENTS_TABLE_SQL: Final = """
CREATE TABLE case_documents (
    case_document_id TEXT PRIMARY KEY,
    document_kind TEXT NOT NULL CHECK (document_kind IN ('technical_specification', 'individual_test_method', 'typical_test_method', 'customer_requirement', 'test_request', 'operational_documentation', 'standard', 'drawing', 'measurement_or_attestation_record', 'other')),
    title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 300),
    designation TEXT NOT NULL DEFAULT '' CHECK (length(designation) <= 200),
    revision_label TEXT NOT NULL DEFAULT '' CHECK (length(revision_label) <= 200),
    document_date TEXT NULL,
    issuer TEXT NOT NULL DEFAULT '' CHECK (length(issuer) <= 300),
    notes TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 4000),
    record_revision INTEGER NOT NULL CHECK (record_revision >= 1),
    archived_at_utc TEXT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
)
"""
CASE_DOCUMENT_FILES_TABLE_SQL: Final = """
CREATE TABLE case_document_files (
    case_document_id TEXT PRIMARY KEY REFERENCES case_documents(case_document_id),
    original_file_name TEXT NOT NULL,
    stored_relative_path TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 104857600),
    sha256 TEXT NOT NULL UNIQUE,
    attached_at_utc TEXT NOT NULL
)
"""
CASE_DOCUMENT_WHEEL_MODELS_TABLE_SQL: Final = """
CREATE TABLE case_document_wheel_models (
    case_document_id TEXT NOT NULL REFERENCES case_documents(case_document_id),
    wheel_model_id TEXT NOT NULL REFERENCES wheel_models(wheel_model_id),
    PRIMARY KEY (case_document_id, wheel_model_id)
)
"""
CASE_DOCUMENT_SPECIMENS_TABLE_SQL: Final = """
CREATE TABLE case_document_specimens (
    case_document_id TEXT NOT NULL REFERENCES case_documents(case_document_id),
    specimen_id TEXT NOT NULL REFERENCES specimens(specimen_id),
    PRIMARY KEY (case_document_id, specimen_id)
)
"""
CASE_DOCUMENT_FILES_NO_UPDATE_TRIGGER_SQL: Final = "CREATE TRIGGER case_document_files_no_update BEFORE UPDATE ON case_document_files BEGIN SELECT RAISE(ABORT, 'case_document_file_immutable'); END"
CASE_DOCUMENT_FILES_NO_DELETE_TRIGGER_SQL: Final = "CREATE TRIGGER case_document_files_no_delete BEFORE DELETE ON case_document_files BEGIN SELECT RAISE(ABORT, 'case_document_file_immutable'); END"
CASE_DOCUMENTS_KIND_INDEX_SQL: Final = "CREATE INDEX case_documents_kind_idx ON case_documents(document_kind)"
CASE_DOCUMENTS_ARCHIVED_INDEX_SQL: Final = "CREATE INDEX case_documents_archived_idx ON case_documents(archived_at_utc)"
CASE_DOCUMENTS_TITLE_INDEX_SQL: Final = "CREATE INDEX case_documents_title_idx ON case_documents(title, designation)"
CASE_DOCUMENT_FILES_SHA256_INDEX_SQL: Final = "CREATE INDEX case_document_files_sha256_idx ON case_document_files(sha256)"
CASE_DOCUMENT_WHEEL_MODELS_TARGET_INDEX_SQL: Final = "CREATE INDEX case_document_wheel_models_target_idx ON case_document_wheel_models(wheel_model_id)"
CASE_DOCUMENT_SPECIMENS_TARGET_INDEX_SQL: Final = "CREATE INDEX case_document_specimens_target_idx ON case_document_specimens(specimen_id)"
R130SH_SOURCES_TABLE_SQL: Final = """
CREATE TABLE r130sh_sources (
    local_import_id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL,
    export_revision INTEGER NOT NULL CHECK (export_revision >= 1),
    outer_package_sha256 TEXT NOT NULL CHECK (length(outer_package_sha256) = 64),
    run_id TEXT NOT NULL,
    package_kind TEXT NOT NULL CHECK (package_kind IN ('final', 'diagnostic_partial')),
    package_schema TEXT NOT NULL,
    package_created_at_utc TEXT NOT NULL,
    source_snapshot_sha256 TEXT NOT NULL CHECK (length(source_snapshot_sha256) = 64),
    producer_name TEXT NOT NULL,
    producer_version TEXT NOT NULL,
    producer_build_id TEXT NOT NULL,
    producer_git_commit TEXT NOT NULL,
    managed_relative_path TEXT NOT NULL UNIQUE,
    outer_size_bytes INTEGER NOT NULL CHECK (outer_size_bytes > 0),
    imported_at_utc TEXT NOT NULL,
    validator_version TEXT NOT NULL,
    validation_contract_commit TEXT NOT NULL,
    structural_verdict TEXT NOT NULL CHECK (structural_verdict = 'passed'),
    semantic_verdict TEXT NOT NULL CHECK (semantic_verdict IN ('passed', 'passed_with_warnings')),
    semantic_coverage_json TEXT NOT NULL CHECK (json_valid(semantic_coverage_json)),
    validation_findings_json TEXT NOT NULL CHECK (json_valid(validation_findings_json)),
    UNIQUE (package_id, export_revision)
)
"""
R130SH_SOURCE_INVENTORY_TABLE_SQL: Final = """
CREATE TABLE r130sh_source_inventory (
    local_import_id TEXT NOT NULL REFERENCES r130sh_sources(local_import_id),
    path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    row_count INTEGER NULL CHECK (row_count IS NULL OR row_count >= 0),
    semantic_coverage TEXT NOT NULL CHECK (semantic_coverage IN ('covered', 'structural_only')),
    PRIMARY KEY (local_import_id, path)
)
"""
R130SH_SPECIMEN_BINDINGS_TABLE_SQL: Final = """
CREATE TABLE r130sh_specimen_bindings (
    source_specimen_id TEXT PRIMARY KEY,
    local_specimen_id TEXT NULL REFERENCES specimens(specimen_id),
    record_revision INTEGER NOT NULL CHECK (record_revision >= 1),
    updated_by_actor TEXT NULL,
    reason TEXT NOT NULL DEFAULT '' CHECK (length(reason) <= 2000),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
)
"""
R130SH_RUN_PROJECTIONS_TABLE_SQL: Final = """
CREATE TABLE r130sh_run_projections (
    local_import_id TEXT PRIMARY KEY REFERENCES r130sh_sources(local_import_id),
    run_id TEXT NOT NULL,
    source_specimen_id TEXT NOT NULL REFERENCES r130sh_specimen_bindings(source_specimen_id),
    mode TEXT NOT NULL CHECK (mode IN ('pmn', 'rpt', 'rbd')),
    package_kind TEXT NOT NULL CHECK (package_kind IN ('final', 'diagnostic_partial')),
    technical_status TEXT NULL,
    termination_reason TEXT NULL,
    specimen_outcome TEXT NULL,
    run_validity TEXT NULL,
    data_completeness TEXT NULL,
    partial_reasons_json TEXT NOT NULL CHECK (json_valid(partial_reasons_json)),
    resume_available INTEGER NOT NULL CHECK (resume_available IN (0, 1)),
    original_plan_id TEXT NOT NULL,
    original_plan_revision INTEGER NOT NULL CHECK (original_plan_revision >= 1),
    original_plan_sha256 TEXT NOT NULL CHECK (length(original_plan_sha256) = 64),
    effective_plan_id TEXT NOT NULL,
    effective_plan_revision INTEGER NOT NULL CHECK (effective_plan_revision >= 1),
    effective_plan_sha256 TEXT NOT NULL CHECK (length(effective_plan_sha256) = 64),
    original_plan_summary_json TEXT NOT NULL CHECK (json_valid(original_plan_summary_json)),
    effective_plan_summary_json TEXT NOT NULL CHECK (json_valid(effective_plan_summary_json)),
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT NULL,
    customer_full_name TEXT NULL,
    customer_address TEXT NULL,
    customer_order_reference TEXT NULL,
    wheel_full_name TEXT NULL,
    wheel_identifier TEXT NULL,
    working_diameter_mm TEXT NULL,
    sample_label TEXT NULL,
    environment_status TEXT NULL,
    environment_summary_json TEXT NOT NULL CHECK (json_valid(environment_summary_json)),
    provenance_summary_json TEXT NOT NULL CHECK (json_valid(provenance_summary_json)),
    measurement_count INTEGER NOT NULL CHECK (measurement_count >= 0),
    accepted_measurement_count INTEGER NOT NULL CHECK (accepted_measurement_count >= 0),
    event_count INTEGER NOT NULL CHECK (event_count >= 0),
    inspection_count INTEGER NOT NULL CHECK (inspection_count >= 0),
    attachment_count INTEGER NOT NULL CHECK (attachment_count >= 0),
    amendment_count INTEGER NOT NULL CHECK (amendment_count >= 0),
    crediting_policy TEXT NULL,
    accepted_elapsed_s TEXT NULL
)
"""
R130SH_ENRICHMENT_RESOLUTIONS_TABLE_SQL: Final = """
CREATE TABLE r130sh_enrichment_resolutions (
    resolution_id TEXT PRIMARY KEY,
    local_import_id TEXT NOT NULL REFERENCES r130sh_sources(local_import_id),
    source_payload_path TEXT NOT NULL,
    source_field TEXT NOT NULL,
    target_entity_type TEXT NOT NULL CHECK (target_entity_type IN ('customer_profile', 'wheel_model', 'specimen')),
    target_entity_id TEXT NOT NULL,
    target_field TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('use_source', 'use_analyst', 'copied_to_analyst')),
    actor TEXT NOT NULL,
    occurred_at_utc TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(reason) <= 2000),
    UNIQUE (local_import_id, source_payload_path, source_field, target_entity_type, target_entity_id, target_field)
)
"""
R130SH_SOURCES_NO_UPDATE_TRIGGER_SQL: Final = "CREATE TRIGGER r130sh_sources_no_update BEFORE UPDATE ON r130sh_sources BEGIN SELECT RAISE(ABORT, 'r130sh_source_immutable'); END"
R130SH_SOURCES_NO_DELETE_TRIGGER_SQL: Final = "CREATE TRIGGER r130sh_sources_no_delete BEFORE DELETE ON r130sh_sources BEGIN SELECT RAISE(ABORT, 'r130sh_source_immutable'); END"
R130SH_INVENTORY_NO_UPDATE_TRIGGER_SQL: Final = (
    "CREATE TRIGGER r130sh_source_inventory_no_update BEFORE UPDATE ON r130sh_source_inventory BEGIN SELECT RAISE(ABORT, 'r130sh_source_inventory_immutable'); END"
)
R130SH_INVENTORY_NO_DELETE_TRIGGER_SQL: Final = (
    "CREATE TRIGGER r130sh_source_inventory_no_delete BEFORE DELETE ON r130sh_source_inventory BEGIN SELECT RAISE(ABORT, 'r130sh_source_inventory_immutable'); END"
)
R130SH_PROJECTIONS_NO_UPDATE_TRIGGER_SQL: Final = (
    "CREATE TRIGGER r130sh_run_projections_no_update BEFORE UPDATE ON r130sh_run_projections BEGIN SELECT RAISE(ABORT, 'r130sh_run_projection_immutable'); END"
)
R130SH_PROJECTIONS_NO_DELETE_TRIGGER_SQL: Final = (
    "CREATE TRIGGER r130sh_run_projections_no_delete BEFORE DELETE ON r130sh_run_projections BEGIN SELECT RAISE(ABORT, 'r130sh_run_projection_immutable'); END"
)
R130SH_RESOLUTIONS_NO_UPDATE_TRIGGER_SQL: Final = (
    "CREATE TRIGGER r130sh_enrichment_resolutions_no_update BEFORE UPDATE ON r130sh_enrichment_resolutions BEGIN SELECT RAISE(ABORT, 'r130sh_enrichment_resolution_immutable'); END"
)
R130SH_RESOLUTIONS_NO_DELETE_TRIGGER_SQL: Final = (
    "CREATE TRIGGER r130sh_enrichment_resolutions_no_delete BEFORE DELETE ON r130sh_enrichment_resolutions BEGIN SELECT RAISE(ABORT, 'r130sh_enrichment_resolution_immutable'); END"
)
R130SH_SOURCES_RUN_INDEX_SQL: Final = "CREATE INDEX r130sh_sources_run_idx ON r130sh_sources(run_id, export_revision)"
R130SH_PROJECTIONS_SPECIMEN_INDEX_SQL: Final = "CREATE INDEX r130sh_run_projections_specimen_idx ON r130sh_run_projections(source_specimen_id)"
R130SH_BINDINGS_LOCAL_INDEX_SQL: Final = "CREATE INDEX r130sh_specimen_bindings_local_idx ON r130sh_specimen_bindings(local_specimen_id)"
R130SH_RESOLUTIONS_IMPORT_INDEX_SQL: Final = "CREATE INDEX r130sh_enrichment_resolutions_import_idx ON r130sh_enrichment_resolutions(local_import_id)"


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


SCHEMA_V1_OBJECTS: Final = (
    SchemaObject("table", "schema_migrations", SCHEMA_MIGRATIONS_TABLE_SQL),
    SchemaObject("table", "project_metadata", PROJECT_METADATA_TABLE_SQL),
    SchemaObject("table", "project_audit_events", PROJECT_AUDIT_EVENTS_TABLE_SQL),
    SchemaObject("trigger", "project_audit_events_no_update", PROJECT_AUDIT_NO_UPDATE_TRIGGER_SQL),
    SchemaObject("trigger", "project_audit_events_no_delete", PROJECT_AUDIT_NO_DELETE_TRIGGER_SQL),
    SchemaObject("table", "customer_profile", CUSTOMER_PROFILE_TABLE_SQL),
    SchemaObject("table", "wheel_models", WHEEL_MODELS_TABLE_SQL),
    SchemaObject("table", "specimens", SPECIMENS_TABLE_SQL),
    SchemaObject("index", "wheel_models_archived_idx", WHEEL_MODELS_ARCHIVED_INDEX_SQL),
    SchemaObject("index", "wheel_models_name_idx", WHEEL_MODELS_NAME_INDEX_SQL),
    SchemaObject("index", "specimens_archived_idx", SPECIMENS_ARCHIVED_INDEX_SQL),
    SchemaObject("index", "specimens_model_idx", SPECIMENS_MODEL_INDEX_SQL),
    SchemaObject("index", "specimens_identification_idx", SPECIMENS_IDENTIFICATION_INDEX_SQL),
    SchemaObject("table", "case_documents", CASE_DOCUMENTS_TABLE_SQL),
    SchemaObject("table", "case_document_files", CASE_DOCUMENT_FILES_TABLE_SQL),
    SchemaObject("table", "case_document_wheel_models", CASE_DOCUMENT_WHEEL_MODELS_TABLE_SQL),
    SchemaObject("table", "case_document_specimens", CASE_DOCUMENT_SPECIMENS_TABLE_SQL),
    SchemaObject("trigger", "case_document_files_no_update", CASE_DOCUMENT_FILES_NO_UPDATE_TRIGGER_SQL),
    SchemaObject("trigger", "case_document_files_no_delete", CASE_DOCUMENT_FILES_NO_DELETE_TRIGGER_SQL),
    SchemaObject("index", "case_documents_kind_idx", CASE_DOCUMENTS_KIND_INDEX_SQL),
    SchemaObject("index", "case_documents_archived_idx", CASE_DOCUMENTS_ARCHIVED_INDEX_SQL),
    SchemaObject("index", "case_documents_title_idx", CASE_DOCUMENTS_TITLE_INDEX_SQL),
    SchemaObject("index", "case_document_files_sha256_idx", CASE_DOCUMENT_FILES_SHA256_INDEX_SQL),
    SchemaObject(
        "index",
        "case_document_wheel_models_target_idx",
        CASE_DOCUMENT_WHEEL_MODELS_TARGET_INDEX_SQL,
    ),
    SchemaObject(
        "index",
        "case_document_specimens_target_idx",
        CASE_DOCUMENT_SPECIMENS_TARGET_INDEX_SQL,
    ),
    SchemaObject("table", "r130sh_sources", R130SH_SOURCES_TABLE_SQL),
    SchemaObject("table", "r130sh_source_inventory", R130SH_SOURCE_INVENTORY_TABLE_SQL),
    SchemaObject("table", "r130sh_specimen_bindings", R130SH_SPECIMEN_BINDINGS_TABLE_SQL),
    SchemaObject("table", "r130sh_run_projections", R130SH_RUN_PROJECTIONS_TABLE_SQL),
    SchemaObject("table", "r130sh_enrichment_resolutions", R130SH_ENRICHMENT_RESOLUTIONS_TABLE_SQL),
    SchemaObject("trigger", "r130sh_sources_no_update", R130SH_SOURCES_NO_UPDATE_TRIGGER_SQL),
    SchemaObject("trigger", "r130sh_sources_no_delete", R130SH_SOURCES_NO_DELETE_TRIGGER_SQL),
    SchemaObject("trigger", "r130sh_source_inventory_no_update", R130SH_INVENTORY_NO_UPDATE_TRIGGER_SQL),
    SchemaObject("trigger", "r130sh_source_inventory_no_delete", R130SH_INVENTORY_NO_DELETE_TRIGGER_SQL),
    SchemaObject("trigger", "r130sh_run_projections_no_update", R130SH_PROJECTIONS_NO_UPDATE_TRIGGER_SQL),
    SchemaObject("trigger", "r130sh_run_projections_no_delete", R130SH_PROJECTIONS_NO_DELETE_TRIGGER_SQL),
    SchemaObject("trigger", "r130sh_enrichment_resolutions_no_update", R130SH_RESOLUTIONS_NO_UPDATE_TRIGGER_SQL),
    SchemaObject("trigger", "r130sh_enrichment_resolutions_no_delete", R130SH_RESOLUTIONS_NO_DELETE_TRIGGER_SQL),
    SchemaObject("index", "r130sh_sources_run_idx", R130SH_SOURCES_RUN_INDEX_SQL),
    SchemaObject("index", "r130sh_run_projections_specimen_idx", R130SH_PROJECTIONS_SPECIMEN_INDEX_SQL),
    SchemaObject("index", "r130sh_specimen_bindings_local_idx", R130SH_BINDINGS_LOCAL_INDEX_SQL),
    SchemaObject("index", "r130sh_enrichment_resolutions_import_idx", R130SH_RESOLUTIONS_IMPORT_INDEX_SQL),
)
SCHEMA_V1_CONTRACT: Final = PublishedSchemaContract(
    version=1,
    objects=SCHEMA_V1_OBJECTS,
    migrations=((1, "create_project_database"),),
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
    _validate_audit_stream(connection, deadline)
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
            WHERE event_type IN ('project.created', 'project.metadata_updated')
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


def _validate_audit_stream(connection: sqlite3.Connection, deadline: RequestDeadline | None) -> None:
    allowed_events = {
        "project.created",
        "project.metadata_updated",
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
        "case_document.created",
        "case_document.updated",
        "case_document.file_attached",
        "case_document.archived",
        "case_document.restored",
        "r130sh_import.completed",
        "r130sh_source.specimen_bound",
        "r130sh_source.enrichment_resolution_recorded",
    }
    rows = sqlite_query_rows_with_deadline(
        connection,
        """
        SELECT sequence,
               CASE WHEN typeof(event_id)='text' AND length(CAST(event_id AS BLOB)) <= 36 THEN event_id END,
               CASE WHEN typeof(event_type)='text' AND length(CAST(event_type AS BLOB)) <= 64 THEN event_type END,
               CASE WHEN typeof(occurred_at_utc)='text' AND length(CAST(occurred_at_utc AS BLOB)) <= ? THEN occurred_at_utc END,
               CASE WHEN typeof(actor_kind)='text' AND length(CAST(actor_kind AS BLOB)) <= 16 THEN actor_kind END,
               CASE WHEN typeof(payload_json)='text' AND length(CAST(payload_json AS BLOB)) <= ? THEN payload_json END
        FROM project_audit_events ORDER BY sequence
        """,
        (MAX_TIMESTAMP_BYTES, MAX_AUDIT_PAYLOAD_BYTES),
        deadline,
        "project_audit_stream",
    )
    previous_at: datetime | None = None
    with closing(rows):
        for expected_sequence, row in enumerate(rows, start=1):
            if _require_int(row[0]) != expected_sequence:
                raise _evidence_error()
            _require_event_id(row[1])
            if str(row[2]) not in allowed_events or str(row[4]) not in {"application", "user"}:
                raise _evidence_error()
            event_at = _require_timestamp(str(row[3]))
            if previous_at is not None and event_at < previous_at:
                raise _evidence_error()
            previous_at = event_at
            if not isinstance(row[5], str) or len(row[5].encode("utf-8")) > MAX_AUDIT_PAYLOAD_BYTES:
                raise _evidence_error()
            _parse_json_object(row[5])


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
    previous_sequence = 0
    for event_count, row in enumerate(event_rows, start=1):
        _check_deadline(deadline, "project_evidence_audit")
        sequence = _require_int(row[0])
        if sequence <= previous_sequence:
            raise _evidence_error()
        previous_sequence = sequence
        _require_event_id(row[1])
        event_at = _require_timestamp(str(row[4]))
        if event_at < previous_event_at:
            raise _evidence_error()
        if not isinstance(row[5], str):
            raise _evidence_error()
        payload = _parse_json_object(row[5])
        if event_count == 1:
            if str(row[2]) != "project.created" or str(row[3]) != "application":
                raise _evidence_error()
            if (
                payload.get("entityType") != "project"
                or payload.get("entityId") != project_id
                or _require_int(payload.get("toRevision")) != 1
                or _require_int(payload.get("schemaVersion")) != 1
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
