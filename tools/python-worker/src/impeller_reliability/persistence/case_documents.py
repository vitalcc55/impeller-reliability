from __future__ import annotations

import codecs
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
from typing import Final, Literal, Never
from uuid import uuid4
import zipfile

from pydantic import JsonValue, TypeAdapter, ValidationError

from impeller_reliability.persistence.analyst_dossier import canonical_date, canonical_uuid4
from impeller_reliability.persistence.audit import audit_now, insert_audit
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.sqlite_deadline import sqlite_query_rows_with_deadline
from impeller_reliability.persistence.timestamps import require_canonical_utc_timestamp
from impeller_reliability.worker.deadline import RequestDeadline

DocumentKind = Literal[
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
]
IntegrityStatus = Literal["not_attached", "verified", "missing", "modified", "verification_error"]
CaseDocumentWarning = Literal[
    "case_document_file_missing",
    "case_document_designation_missing",
    "case_document_revision_missing",
]

DOCUMENT_KINDS: Final[tuple[DocumentKind, ...]] = (
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
NORMATIVE_KINDS: Final = {
    "technical_specification",
    "individual_test_method",
    "typical_test_method",
    "standard",
}
DOCUMENT_FIELDS: Final = (
    "documentKind",
    "title",
    "designation",
    "revisionLabel",
    "documentDate",
    "issuer",
    "notes",
)
MAX_FILE_BYTES: Final = 100 * 1024 * 1024
COPY_CHUNK_BYTES: Final = 1024 * 1024
FILE_MEDIA_TYPES: Final = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".json": "application/json",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
TEXT_EXTENSIONS: Final = {".csv", ".json", ".txt"}
JSON_OBJECT_ADAPTER: Final = TypeAdapter(dict[str, JsonValue])
STRING_LIST_ADAPTER: Final = TypeAdapter(list[str])


@dataclass(frozen=True, slots=True)
class CaseDocumentFile:
    original_file_name: str
    stored_relative_path: str
    media_type: str
    size_bytes: int
    sha256: str
    attached_at_utc: str


@dataclass(frozen=True, slots=True)
class CaseDocument:
    case_document_id: str
    document_kind: DocumentKind
    title: str
    designation: str
    revision_label: str
    document_date: str | None
    issuer: str
    notes: str
    record_revision: int
    archived_at_utc: str | None
    created_at_utc: str
    updated_at_utc: str
    file: CaseDocumentFile | None
    integrity_status: IntegrityStatus
    wheel_model_ids: tuple[str, ...]
    specimen_ids: tuple[str, ...]
    warnings: tuple[CaseDocumentWarning, ...]


@dataclass(frozen=True, slots=True)
class _StagedFile:
    path: Path
    original_file_name: str
    extension: str
    media_type: str
    size_bytes: int
    sha256: str


def validate_case_document_evidence(
    connection: sqlite3.Connection,
    deadline: RequestDeadline | None = None,
) -> None:
    try:
        _validate_case_document_evidence(connection, deadline)
    except ProjectOperationError:
        raise
    except (TypeError, ValueError, ValidationError) as error:
        raise _evidence_error() from error


def _validate_case_document_evidence(
    connection: sqlite3.Connection,
    deadline: RequestDeadline | None,
) -> None:
    expected: dict[str, tuple[dict[str, object], int, str, str, str | None]] = {}
    rows = tuple(
        sqlite_query_rows_with_deadline(
            connection,
            """
        SELECT case_document_id, document_kind, title, designation, revision_label,
               document_date, issuer, notes, record_revision, archived_at_utc,
               created_at_utc, updated_at_utc
        FROM case_documents ORDER BY case_document_id
        """,
            (),
            deadline,
            "case_document_evidence_rows_query",
        )
    )
    for row in rows:
        _check_deadline(deadline, "case_document_evidence_rows")
        document_id = canonical_uuid4(str(row[0]))
        values = _normalize_values(
            {
                "documentKind": row[1],
                "title": row[2],
                "designation": row[3],
                "revisionLabel": row[4],
                "documentDate": row[5],
                "issuer": row[6],
                "notes": row[7],
            }
        )
        if any(values[field] != row[index + 1] for index, field in enumerate(DOCUMENT_FIELDS)):
            raise _evidence_error()
        wheel_ids = _evidence_ids(
            list(
                sqlite_query_rows_with_deadline(
                    connection,
                    "SELECT wheel_model_id FROM case_document_wheel_models WHERE case_document_id=? ORDER BY wheel_model_id",
                    (document_id,),
                    deadline,
                    "case_document_evidence_wheel_links",
                )
            )
        )
        specimen_ids = _evidence_ids(
            list(
                sqlite_query_rows_with_deadline(
                    connection,
                    "SELECT specimen_id FROM case_document_specimens WHERE case_document_id=? ORDER BY specimen_id",
                    (document_id,),
                    deadline,
                    "case_document_evidence_specimen_links",
                )
            )
        )
        file_row = connection.execute(
            "SELECT original_file_name, stored_relative_path, media_type, size_bytes, sha256, attached_at_utc FROM case_document_files WHERE case_document_id=?",
            (document_id,),
        ).fetchone()
        state: dict[str, object] = {
            **values,
            "wheelModelIds": list(wheel_ids),
            "specimenIds": list(specimen_ids),
            "archivedAtUtc": _stored_timestamp(row[9]),
        }
        attached_at: str | None = None
        if file_row is not None:
            state["file"] = _normalize_file_payload(
                document_id,
                {
                    "originalFileName": file_row[0],
                    "storedRelativePath": file_row[1],
                    "mediaType": file_row[2],
                    "sizeBytes": file_row[3],
                    "sha256": file_row[4],
                },
            )
            attached_at = require_canonical_utc_timestamp(str(file_row[5]))
        revision = _positive_int(row[8])
        created_at = require_canonical_utc_timestamp(str(row[10]))
        updated_at = require_canonical_utc_timestamp(str(row[11]))
        if updated_at < created_at:
            raise _evidence_error()
        expected[document_id] = (state, revision, created_at, updated_at, attached_at)

    reconstructed: dict[str, dict[str, object]] = {}
    revisions: dict[str, int] = {}
    created_times: dict[str, str] = {}
    updated_times: dict[str, str] = {}
    attached_times: dict[str, str | None] = {}
    events = tuple(
        sqlite_query_rows_with_deadline(
            connection,
            """
        SELECT event_type, actor_kind, occurred_at_utc, payload_json
        FROM project_audit_events
        WHERE event_type LIKE 'case_document.%'
        ORDER BY sequence
        """,
            (),
            deadline,
            "case_document_evidence_audit_query",
        )
    )
    for row in events:
        _check_deadline(deadline, "case_document_evidence_audit")
        event_type = str(row[0])
        if str(row[1]) != "user":
            raise _evidence_error()
        occurred_at = require_canonical_utc_timestamp(str(row[2]))
        payload = _parse_payload(row[3])
        if payload.get("entityType") != "caseDocument":
            raise _evidence_error()
        entity_id_value = payload.get("entityId")
        if not isinstance(entity_id_value, str):
            raise _evidence_error()
        document_id = canonical_uuid4(entity_id_value)

        if event_type == "case_document.created":
            if document_id in reconstructed or _positive_int(payload.get("toRevision")) != 1:
                raise _evidence_error()
            after = _json_object(payload.get("after"))
            state = _normalize_initial_audit_state(document_id, after)
            changed_fields = payload.get("changedFields")
            if not isinstance(changed_fields, list) or len(changed_fields) != len(set(changed_fields)) or set(changed_fields) != set(after):
                raise _evidence_error()
            state["archivedAtUtc"] = None
            reconstructed[document_id] = state
            revisions[document_id] = 1
            created_times[document_id] = occurred_at
            updated_times[document_id] = occurred_at
            attached_times[document_id] = occurred_at if "file" in state else None
            continue

        if document_id not in reconstructed:
            raise _evidence_error()
        current_revision = revisions[document_id]
        from_revision = _positive_int(payload.get("fromRevision"))
        to_revision = _positive_int(payload.get("toRevision"))
        if from_revision != current_revision or to_revision != current_revision + 1:
            raise _evidence_error()
        state = reconstructed[document_id]

        if event_type == "case_document.updated":
            if state.get("archivedAtUtc") is not None:
                raise _evidence_error()
            changes = _parse_changes(payload)
            if not changes:
                raise _evidence_error()
            if not set(changes) <= set(DOCUMENT_FIELDS) | {"wheelModelIds", "specimenIds"}:
                raise _evidence_error()
            _apply_changes(state, changes)
            _validate_audit_state(document_id, state)
        elif event_type == "case_document.file_attached":
            if state.get("archivedAtUtc") is not None or "file" in state or payload.get("changedFields") != ["file"]:
                raise _evidence_error()
            file_payload = _json_object(payload.get("file"))
            state["file"] = _normalize_file_payload(document_id, file_payload)
            attached_times[document_id] = occurred_at
        elif event_type in {"case_document.archived", "case_document.restored"}:
            changes = _parse_changes(payload)
            if set(changes) != {"archivedAtUtc"}:
                raise _evidence_error()
            before = state.get("archivedAtUtc")
            change = changes["archivedAtUtc"]
            if change.get("before") != before:
                raise _evidence_error()
            archive_after = change.get("after")
            if event_type.endswith(".archived"):
                if before is not None or not isinstance(archive_after, str):
                    raise _evidence_error()
                archive_after = require_canonical_utc_timestamp(archive_after)
            elif before is None or archive_after is not None:
                raise _evidence_error()
            state["archivedAtUtc"] = archive_after
        else:
            raise _evidence_error()

        revisions[document_id] = to_revision
        updated_times[document_id] = occurred_at

    if set(reconstructed) != set(expected):
        raise _evidence_error()
    for document_id, (state, revision, created_at, updated_at, attached_at) in expected.items():
        if (
            reconstructed[document_id] != state
            or revisions[document_id] != revision
            or created_times[document_id] != created_at
            or updated_times[document_id] != updated_at
            or attached_times[document_id] != attached_at
        ):
            raise _evidence_error()


class CaseDocumentRepository:
    def __init__(self, connection: sqlite3.Connection, project_path: Path) -> None:
        self._connection = connection
        self._project_path = project_path
        self._integrity_cache: dict[str, tuple[str, int, IntegrityStatus]] = {}

    def create(
        self,
        *,
        document_id: str,
        values: Mapping[str, object],
        wheel_model_ids: tuple[str, ...],
        specimen_ids: tuple[str, ...],
        deadline: RequestDeadline | None,
    ) -> CaseDocument:
        document_id = canonical_uuid4(document_id)
        normalized = _normalize_values(values)
        wheel_ids = _normalize_ids(wheel_model_ids)
        specimen_ids = _normalize_ids(specimen_ids)
        self._require_targets(wheel_ids, specimen_ids)
        existing = self._find(document_id)
        if existing is not None:
            if (
                existing.record_revision == 1
                and existing.archived_at_utc is None
                and existing.file is None
                and _document_values(existing) == normalized
                and existing.wheel_model_ids == wheel_ids
                and existing.specimen_ids == specimen_ids
            ):
                return existing
            raise ProjectOperationError("duplicate_entity", "Идентификатор документа уже используется.")

        now = audit_now(self._connection)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                """
                INSERT INTO case_documents (
                    case_document_id, document_kind, title, designation,
                    revision_label, document_date, issuer, notes,
                    record_revision, archived_at_utc, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?)
                """,
                (
                    document_id,
                    *[normalized[field] for field in DOCUMENT_FIELDS],
                    now,
                    now,
                ),
            )
            self._replace_links(document_id, wheel_ids, specimen_ids)
            initial = {
                **normalized,
                "wheelModelIds": list(wheel_ids),
                "specimenIds": list(specimen_ids),
            }
            insert_audit(
                self._connection,
                event_type="case_document.created",
                actor_kind="user",
                occurred_at_utc=now,
                payload={
                    "entityType": "caseDocument",
                    "entityId": document_id,
                    "toRevision": 1,
                    "changedFields": list(initial),
                    "after": initial,
                },
            )
            _commit(self._connection, deadline, "case_document_create")
        except Exception as error:
            self._connection.rollback()
            _raise_mutation_error(error)
        return _build_document_result(
            document_id=document_id,
            normalized=normalized,
            record_revision=1,
            archived_at_utc=None,
            created_at_utc=now,
            updated_at_utc=now,
            file=None,
            integrity_status="not_attached",
            wheel_model_ids=wheel_ids,
            specimen_ids=specimen_ids,
        )

    def create_with_file(
        self,
        *,
        document_id: str,
        values: Mapping[str, object],
        wheel_model_ids: tuple[str, ...],
        specimen_ids: tuple[str, ...],
        source_path: Path,
        deadline: RequestDeadline | None,
    ) -> CaseDocument:
        document_id = canonical_uuid4(document_id)
        normalized = _normalize_values(values)
        wheel_ids = _normalize_ids(wheel_model_ids)
        specimen_ids = _normalize_ids(specimen_ids)
        self._require_targets(wheel_ids, specimen_ids)
        staged = self._stage_source(source_path, deadline)
        final_path: Path | None = None
        try:
            existing = self._find(document_id, deadline=deadline)
            if existing is not None:
                if (
                    existing.record_revision == 1
                    and existing.archived_at_utc is None
                    and existing.file is not None
                    and _document_values(existing) == normalized
                    and existing.wheel_model_ids == wheel_ids
                    and existing.specimen_ids == specimen_ids
                    and _same_file_snapshot(existing.file, staged)
                ):
                    return existing
                raise ProjectOperationError("duplicate_entity", "Идентификатор документа уже используется.")

            now = audit_now(self._connection)
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if (
                    self._connection.execute(
                        "SELECT 1 FROM case_documents WHERE case_document_id=?",
                        (document_id,),
                    ).fetchone()
                    is not None
                ):
                    raise ProjectOperationError("duplicate_entity", "Идентификатор документа уже используется.")
                if (
                    self._connection.execute(
                        "SELECT 1 FROM case_document_files WHERE sha256=?",
                        (staged.sha256,),
                    ).fetchone()
                    is not None
                ):
                    raise ProjectOperationError(
                        "duplicate_document_content",
                        "Файл с таким содержимым уже зарегистрирован в проекте.",
                    )
                final_path, relative_path = self._move_staged_to_final(document_id, staged)
                self._connection.execute(
                    """
                    INSERT INTO case_documents (
                        case_document_id, document_kind, title, designation,
                        revision_label, document_date, issuer, notes,
                        record_revision, archived_at_utc, created_at_utc, updated_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?)
                    """,
                    (
                        document_id,
                        *[normalized[field] for field in DOCUMENT_FIELDS],
                        now,
                        now,
                    ),
                )
                self._connection.execute(
                    """
                    INSERT INTO case_document_files (
                        case_document_id, original_file_name, stored_relative_path,
                        media_type, size_bytes, sha256, attached_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        staged.original_file_name,
                        relative_path,
                        staged.media_type,
                        staged.size_bytes,
                        staged.sha256,
                        now,
                    ),
                )
                self._replace_links(document_id, wheel_ids, specimen_ids)
                initial = {
                    **normalized,
                    "wheelModelIds": list(wheel_ids),
                    "specimenIds": list(specimen_ids),
                    "file": _file_payload(staged, relative_path),
                }
                insert_audit(
                    self._connection,
                    event_type="case_document.created",
                    actor_kind="user",
                    occurred_at_utc=now,
                    payload={
                        "entityType": "caseDocument",
                        "entityId": document_id,
                        "toRevision": 1,
                        "changedFields": list(initial),
                        "after": initial,
                    },
                )
                _commit(self._connection, deadline, "case_document_create_with_file")
            except Exception as error:
                self._connection.rollback()
                if final_path is not None and self._file_row(document_id) is None:
                    _remove_operation_file(final_path)
                _raise_mutation_error(error)
        finally:
            _remove_operation_file(staged.path)
        attached_file = CaseDocumentFile(
            original_file_name=staged.original_file_name,
            stored_relative_path=relative_path,
            media_type=staged.media_type,
            size_bytes=staged.size_bytes,
            sha256=staged.sha256,
            attached_at_utc=now,
        )
        self._remember_integrity(document_id, attached_file, "verified")
        return _build_document_result(
            document_id=document_id,
            normalized=normalized,
            record_revision=1,
            archived_at_utc=None,
            created_at_utc=now,
            updated_at_utc=now,
            file=attached_file,
            integrity_status="verified",
            wheel_model_ids=wheel_ids,
            specimen_ids=specimen_ids,
        )

    def list(
        self,
        *,
        include_archived: bool,
        document_kind: str | None,
        deadline: RequestDeadline | None = None,
    ) -> tuple[CaseDocument, ...]:
        _check_deadline(deadline, "case_document_list")
        parameters: list[object] = []
        clauses: list[str] = []
        if not include_archived:
            clauses.append("archived_at_utc IS NULL")
        if document_kind is not None:
            clauses.append("document_kind = ?")
            parameters.append(_document_kind(document_kind))
        where = "" if not clauses else f"WHERE {' AND '.join(clauses)}"
        rows = self._connection.execute(
            f"SELECT case_document_id FROM case_documents {where} ORDER BY archived_at_utc IS NOT NULL, lower(title), lower(designation), case_document_id",
            parameters,
        ).fetchall()
        result = tuple(
            document
            for row in rows
            if (
                document := self._find(
                    str(row[0]),
                    verify_file=False,
                    deadline=deadline,
                )
            )
            is not None
        )
        _check_deadline(deadline, "case_document_list")
        return result

    def get(
        self,
        document_id: str,
        deadline: RequestDeadline | None = None,
    ) -> CaseDocument:
        _check_deadline(deadline, "case_document_get")
        document = self._find(canonical_uuid4(document_id), deadline=deadline)
        if document is None:
            raise ProjectOperationError("entity_not_found", "Документ дела не найден.")
        _check_deadline(deadline, "case_document_get")
        return document

    def update(
        self,
        *,
        document_id: str,
        expected_revision: int,
        values: Mapping[str, object],
        wheel_model_ids: tuple[str, ...],
        specimen_ids: tuple[str, ...],
        deadline: RequestDeadline | None,
    ) -> CaseDocument:
        normalized = _normalize_values(values)
        wheel_ids = _normalize_ids(wheel_model_ids)
        specimen_ids = _normalize_ids(specimen_ids)
        self._require_targets(wheel_ids, specimen_ids)
        current = self._require(document_id, verify_file=False, deadline=deadline)
        if current.file is not None and current.integrity_status == "verification_error":
            current = _with_integrity(
                current,
                self._integrity_status(current.case_document_id, current.file, deadline),
            )
        if current.archived_at_utc is not None:
            raise ProjectOperationError("entity_archived", "Архивный документ нельзя изменять.")
        if expected_revision != current.record_revision:
            raise _revision_conflict(expected_revision, current.record_revision)
        before = {
            **_document_values(current),
            "wheelModelIds": list(current.wheel_model_ids),
            "specimenIds": list(current.specimen_ids),
        }
        after = {
            **normalized,
            "wheelModelIds": list(wheel_ids),
            "specimenIds": list(specimen_ids),
        }
        changes = _changes(before, after)
        if not changes:
            return self.get(current.case_document_id, deadline)

        now = audit_now(self._connection)
        next_revision = current.record_revision + 1
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._connection.execute(
                """
                UPDATE case_documents SET document_kind=?, title=?, designation=?,
                    revision_label=?, document_date=?, issuer=?, notes=?,
                    record_revision=?, updated_at_utc=?
                WHERE case_document_id=? AND record_revision=? AND archived_at_utc IS NULL
                """,
                (
                    *[normalized[field] for field in DOCUMENT_FIELDS],
                    next_revision,
                    now,
                    current.case_document_id,
                    current.record_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise _revision_conflict(expected_revision, None)
            self._replace_links(current.case_document_id, wheel_ids, specimen_ids)
            insert_audit(
                self._connection,
                event_type="case_document.updated",
                actor_kind="user",
                occurred_at_utc=now,
                payload=_update_payload(current.case_document_id, current.record_revision, next_revision, changes),
            )
            _commit(self._connection, deadline, "case_document_update")
        except Exception as error:
            self._connection.rollback()
            _raise_mutation_error(error)
        return _build_document_result(
            document_id=current.case_document_id,
            normalized=normalized,
            record_revision=next_revision,
            archived_at_utc=current.archived_at_utc,
            created_at_utc=current.created_at_utc,
            updated_at_utc=now,
            file=current.file,
            integrity_status=current.integrity_status,
            wheel_model_ids=wheel_ids,
            specimen_ids=specimen_ids,
        )

    def set_archived(
        self,
        *,
        document_id: str,
        expected_revision: int,
        archived: bool,
        deadline: RequestDeadline | None,
    ) -> CaseDocument:
        current = self._require(document_id, verify_file=False, deadline=deadline)
        if current.file is not None and current.integrity_status == "verification_error":
            current = _with_integrity(
                current,
                self._integrity_status(current.case_document_id, current.file, deadline),
            )
        if expected_revision != current.record_revision:
            raise _revision_conflict(expected_revision, current.record_revision)
        if archived == (current.archived_at_utc is not None):
            return self.get(current.case_document_id, deadline)
        now = audit_now(self._connection)
        archived_at = now if archived else None
        next_revision = current.record_revision + 1
        changes: dict[str, dict[str, object]] = {"archivedAtUtc": {"before": current.archived_at_utc, "after": archived_at}}
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._connection.execute(
                "UPDATE case_documents SET archived_at_utc=?, record_revision=?, updated_at_utc=? WHERE case_document_id=? AND record_revision=?",
                (archived_at, next_revision, now, current.case_document_id, current.record_revision),
            )
            if cursor.rowcount != 1:
                raise _revision_conflict(expected_revision, None)
            insert_audit(
                self._connection,
                event_type="case_document.archived" if archived else "case_document.restored",
                actor_kind="user",
                occurred_at_utc=now,
                payload=_update_payload(current.case_document_id, current.record_revision, next_revision, changes),
            )
            _commit(self._connection, deadline, "case_document_archive" if archived else "case_document_restore")
        except Exception as error:
            self._connection.rollback()
            _raise_mutation_error(error)
        return _build_document_result(
            document_id=current.case_document_id,
            normalized=_document_values(current),
            record_revision=next_revision,
            archived_at_utc=archived_at,
            created_at_utc=current.created_at_utc,
            updated_at_utc=now,
            file=current.file,
            integrity_status=current.integrity_status,
            wheel_model_ids=current.wheel_model_ids,
            specimen_ids=current.specimen_ids,
        )

    def attach_file(
        self,
        *,
        document_id: str,
        expected_revision: int,
        source_path: Path,
        deadline: RequestDeadline | None,
    ) -> CaseDocument:
        current = self._require(document_id, verify_file=False, deadline=deadline)
        if current.archived_at_utc is not None:
            raise ProjectOperationError("entity_archived", "К архивному документу нельзя прикрепить файл.")
        staged = self._stage_source(source_path, deadline)
        final_path: Path | None = None
        try:
            if current.file is not None:
                if _same_file_snapshot(current.file, staged):
                    return self.get(current.case_document_id, deadline)
                raise ProjectOperationError(
                    "file_already_attached",
                    "К документу уже прикреплён неизменяемый файл.",
                )
            if expected_revision != current.record_revision:
                raise _revision_conflict(expected_revision, current.record_revision)

            now = audit_now(self._connection)
            next_revision = current.record_revision + 1
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                state = self._connection.execute(
                    "SELECT record_revision, archived_at_utc FROM case_documents WHERE case_document_id=?",
                    (current.case_document_id,),
                ).fetchone()
                if state is None:
                    raise ProjectOperationError("entity_not_found", "Документ дела не найден.")
                existing_file = self._file_row(current.case_document_id)
                if existing_file is not None:
                    if _same_file_snapshot(existing_file, staged):
                        self._connection.rollback()
                        return self.get(current.case_document_id, deadline)
                    raise ProjectOperationError(
                        "file_already_attached",
                        "К документу уже прикреплён неизменяемый файл.",
                    )
                if state[1] is not None:
                    raise ProjectOperationError("entity_archived", "К архивному документу нельзя прикрепить файл.")
                actual_revision = int(state[0])
                if expected_revision != actual_revision:
                    raise _revision_conflict(expected_revision, actual_revision)
                duplicate = self._connection.execute(
                    "SELECT case_document_id FROM case_document_files WHERE sha256=?",
                    (staged.sha256,),
                ).fetchone()
                if duplicate is not None:
                    raise ProjectOperationError(
                        "duplicate_document_content",
                        "Файл с таким содержимым уже зарегистрирован в проекте.",
                    )

                final_path, relative_path = self._move_staged_to_final(
                    current.case_document_id,
                    staged,
                )
                self._connection.execute(
                    """
                    INSERT INTO case_document_files (
                        case_document_id, original_file_name, stored_relative_path,
                        media_type, size_bytes, sha256, attached_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        current.case_document_id,
                        staged.original_file_name,
                        relative_path,
                        staged.media_type,
                        staged.size_bytes,
                        staged.sha256,
                        now,
                    ),
                )
                cursor = self._connection.execute(
                    "UPDATE case_documents SET record_revision=?, updated_at_utc=? WHERE case_document_id=? AND record_revision=?",
                    (next_revision, now, current.case_document_id, actual_revision),
                )
                if cursor.rowcount != 1:
                    raise _revision_conflict(expected_revision, None)
                file_payload = _file_payload(staged, relative_path)
                insert_audit(
                    self._connection,
                    event_type="case_document.file_attached",
                    actor_kind="user",
                    occurred_at_utc=now,
                    payload={
                        "entityType": "caseDocument",
                        "entityId": current.case_document_id,
                        "fromRevision": actual_revision,
                        "toRevision": next_revision,
                        "changedFields": ["file"],
                        "file": file_payload,
                    },
                )
                _commit(self._connection, deadline, "case_document_attach")
            except Exception as error:
                self._connection.rollback()
                if final_path is not None and self._file_row(current.case_document_id) is None:
                    _remove_operation_file(final_path)
                _raise_mutation_error(error)
        finally:
            _remove_operation_file(staged.path)
        attached_file = CaseDocumentFile(
            original_file_name=staged.original_file_name,
            stored_relative_path=relative_path,
            media_type=staged.media_type,
            size_bytes=staged.size_bytes,
            sha256=staged.sha256,
            attached_at_utc=now,
        )
        self._remember_integrity(current.case_document_id, attached_file, "verified")
        return _build_document_result(
            document_id=current.case_document_id,
            normalized=_document_values(current),
            record_revision=next_revision,
            archived_at_utc=current.archived_at_utc,
            created_at_utc=current.created_at_utc,
            updated_at_utc=now,
            file=attached_file,
            integrity_status="verified",
            wheel_model_ids=current.wheel_model_ids,
            specimen_ids=current.specimen_ids,
        )

    def verify_file(
        self,
        document_id: str,
        deadline: RequestDeadline | None,
    ) -> CaseDocument:
        return self.get(document_id, deadline)

    def resolve_file(
        self,
        document_id: str,
        deadline: RequestDeadline | None,
    ) -> Path:
        document = self.get(document_id, deadline)
        if document.file is None:
            raise ProjectOperationError("file_missing", "К документу не прикреплён файл.")
        if document.integrity_status == "missing":
            raise ProjectOperationError("file_missing", "Управляемый файл документа отсутствует.")
        if document.integrity_status != "verified":
            raise ProjectOperationError(
                "file_integrity_mismatch",
                "Управляемый файл документа не прошёл проверку целостности.",
            )
        return self._registered_path(document.case_document_id, document.file)

    def recover_managed_files(self, deadline: RequestDeadline | None = None) -> None:
        _check_deadline(deadline, "case_document_recovery_start")
        root = self._managed_root()
        staging = root / ".staging"
        _ensure_directory(staging)
        part_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.part$",
            re.ASCII,
        )
        for candidate in staging.iterdir():
            _check_deadline(deadline, "case_document_recovery_staging")
            if part_pattern.fullmatch(candidate.name) is not None:
                _remove_operation_file(candidate)

        registered = {str(row[0]) for row in self._connection.execute("SELECT stored_relative_path FROM case_document_files")}
        managed_pattern = re.compile(
            r"^[0-9a-f]{64}\.(?:pdf|docx|xlsx|csv|json|txt|png|jpg|jpeg)$",
            re.ASCII,
        )
        for directory in root.iterdir():
            _check_deadline(deadline, "case_document_recovery_documents")
            if directory.name == ".staging":
                continue
            try:
                canonical_uuid4(directory.name)
                directory_stat = os.lstat(directory)
            except ValueError, OSError:
                continue
            if not stat.S_ISDIR(directory_stat.st_mode) or _is_reparse(directory_stat):
                continue
            for candidate in directory.iterdir():
                _check_deadline(deadline, "case_document_recovery_files")
                if managed_pattern.fullmatch(candidate.name) is None:
                    continue
                relative = PurePosixPath(
                    "assets",
                    "documents",
                    directory.name,
                    candidate.name,
                ).as_posix()
                if relative not in registered:
                    _remove_operation_file(candidate)
            with suppress(OSError):
                directory.rmdir()

    def _stage_source(
        self,
        source_path: Path,
        deadline: RequestDeadline | None,
    ) -> _StagedFile:
        _check_deadline(deadline, "case_document_file_validate")
        extension = source_path.suffix.lower()
        media_type = FILE_MEDIA_TYPES.get(extension)
        if media_type is None:
            raise ProjectOperationError("unsupported_file_type", "Тип файла не поддерживается.")
        if not source_path.is_absolute():
            raise ProjectOperationError("validation_error", "Путь к выбранному файлу некорректен.")
        try:
            source_stat = os.lstat(source_path)
        except OSError as error:
            raise ProjectOperationError("storage_error", "Выбранный файл недоступен.") from error
        if not stat.S_ISREG(source_stat.st_mode) or _is_reparse(source_stat):
            raise ProjectOperationError("unsupported_file_type", "Можно выбрать только обычный файл.")
        if source_stat.st_size <= 0:
            raise ProjectOperationError("unsupported_file_type", "Пустой файл не поддерживается.")
        if source_stat.st_size > MAX_FILE_BYTES:
            raise ProjectOperationError("file_too_large", "Размер файла превышает 100 МиБ.")
        original_name = source_path.name
        if original_name in {"", ".", ".."} or len(original_name) > 255:
            raise ProjectOperationError("validation_error", "Имя выбранного файла некорректно.")

        staging_root = self._managed_root() / ".staging"
        _ensure_directory(staging_root)
        staging_path = staging_root / f"{uuid4()}.part"
        digest = hashlib.sha256()
        size_bytes = 0
        prefix = bytearray()
        decoder = codecs.getincrementaldecoder("utf-8")("strict") if extension in TEXT_EXTENSIONS else None
        try:
            with source_path.open("rb") as source, staging_path.open("xb") as target:
                initial = os.fstat(source.fileno())
                while True:
                    _check_deadline(deadline, "case_document_file_copy")
                    chunk = source.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    if size_bytes > MAX_FILE_BYTES:
                        raise ProjectOperationError("file_too_large", "Размер файла превышает 100 МиБ.")
                    digest.update(chunk)
                    target.write(chunk)
                    if len(prefix) < 16:
                        prefix.extend(chunk[: 16 - len(prefix)])
                    if decoder is not None:
                        decoder.decode(chunk)
                if decoder is not None:
                    decoder.decode(b"", final=True)
                target.flush()
                os.fsync(target.fileno())
                final_source = os.fstat(source.fileno())
                if initial.st_size != final_source.st_size or initial.st_mtime_ns != final_source.st_mtime_ns:
                    raise ProjectOperationError("storage_error", "Выбранный файл изменился во время копирования.")
            if size_bytes != source_stat.st_size:
                raise ProjectOperationError("storage_error", "Размер выбранного файла изменился во время копирования.")
            _validate_staged_content(staging_path, extension, bytes(prefix), deadline)
        except UnicodeDecodeError as error:
            _remove_operation_file(staging_path)
            raise ProjectOperationError("unsupported_file_type", "Текстовый файл должен быть UTF-8.") from error
        except ProjectOperationError:
            _remove_operation_file(staging_path)
            raise
        except (OSError, zipfile.BadZipFile) as error:
            _remove_operation_file(staging_path)
            raise ProjectOperationError("storage_error", "Файл не удалось безопасно скопировать.") from error
        return _StagedFile(
            path=staging_path,
            original_file_name=original_name,
            extension=extension,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=digest.hexdigest(),
        )

    def _managed_root(self) -> Path:
        assets = self._project_path / "assets"
        _ensure_directory(assets)
        documents = assets / "documents"
        _ensure_directory(documents)
        return documents

    def _move_staged_to_final(
        self,
        document_id: str,
        staged: _StagedFile,
    ) -> tuple[Path, str]:
        document_root = self._managed_root() / document_id
        _ensure_directory(document_root)
        final_path = document_root / f"{staged.sha256}{staged.extension}"
        if final_path.exists() or final_path.is_symlink():
            raise ProjectOperationError("storage_error", "Целевой managed file уже существует.")
        try:
            staged.path.rename(final_path)
        except OSError as error:
            raise ProjectOperationError("storage_error", "Managed file не удалось зафиксировать.") from error
        relative_path = PurePosixPath("assets", "documents", document_id, final_path.name).as_posix()
        return final_path, relative_path

    def _file_row(self, document_id: str) -> CaseDocumentFile | None:
        row = self._connection.execute(
            "SELECT original_file_name, stored_relative_path, media_type, size_bytes, sha256, attached_at_utc FROM case_document_files WHERE case_document_id=?",
            (document_id,),
        ).fetchone()
        if row is None:
            return None
        return CaseDocumentFile(
            original_file_name=str(row[0]),
            stored_relative_path=str(row[1]),
            media_type=str(row[2]),
            size_bytes=int(row[3]),
            sha256=str(row[4]),
            attached_at_utc=require_canonical_utc_timestamp(str(row[5])),
        )

    def _registered_path(self, document_id: str, file: CaseDocumentFile) -> Path:
        relative = PurePosixPath(file.stored_relative_path)
        expected_name = f"{file.sha256}{relative.suffix.lower()}"
        if (
            relative.is_absolute()
            or relative.as_posix() != file.stored_relative_path
            or relative.parts != ("assets", "documents", document_id, expected_name)
            or relative.suffix.lower() not in FILE_MEDIA_TYPES
            or "\\" in file.stored_relative_path
        ):
            raise ProjectOperationError("file_integrity_mismatch", "Registry path документа некорректен.")
        root = Path(os.path.abspath(self._project_path / "assets" / "documents"))
        document_root = root / document_id
        candidate = Path(os.path.abspath(self._project_path.joinpath(*relative.parts)))
        try:
            contained = os.path.commonpath((str(root), str(candidate))) == str(root)
        except ValueError:
            contained = False
        if not contained:
            raise ProjectOperationError("file_integrity_mismatch", "Registry path документа вышел за managed root.")
        for directory in (root, document_root):
            directory_stat = os.lstat(directory)
            if not stat.S_ISDIR(directory_stat.st_mode) or _is_reparse(directory_stat):
                raise ProjectOperationError(
                    "file_integrity_mismatch",
                    "Registry path документа содержит небезопасный каталог.",
                )
        return candidate

    def _integrity_status(
        self,
        document_id: str,
        file: CaseDocumentFile,
        deadline: RequestDeadline | None = None,
    ) -> IntegrityStatus:
        try:
            path = self._registered_path(document_id, file)
            file_stat = os.lstat(path)
            if not stat.S_ISREG(file_stat.st_mode) or _is_reparse(file_stat):
                return self._remember_integrity(document_id, file, "verification_error")
            if file_stat.st_size != file.size_bytes:
                return self._remember_integrity(document_id, file, "modified")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while True:
                    _check_deadline(deadline, "case_document_file_verify")
                    chunk = stream.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    digest.update(chunk)
            status: IntegrityStatus = "verified" if digest.hexdigest() == file.sha256 else "modified"
            return self._remember_integrity(document_id, file, status)
        except FileNotFoundError:
            return self._remember_integrity(document_id, file, "missing")
        except ProjectOperationError as error:
            if error.code == "timeout":
                raise
            return self._remember_integrity(document_id, file, "verification_error")
        except OSError:
            return self._remember_integrity(document_id, file, "verification_error")

    def _presence_status(
        self,
        document_id: str,
        file: CaseDocumentFile,
    ) -> IntegrityStatus:
        cached = self._integrity_cache.get(document_id)
        if cached is not None and cached[:2] == (file.sha256, file.size_bytes):
            return cached[2]
        try:
            path = self._registered_path(document_id, file)
            file_stat = os.lstat(path)
            if not stat.S_ISREG(file_stat.st_mode) or _is_reparse(file_stat):
                return self._remember_integrity(document_id, file, "verification_error")
            if file_stat.st_size != file.size_bytes:
                return self._remember_integrity(document_id, file, "modified")
            return "verification_error"
        except FileNotFoundError:
            return self._remember_integrity(document_id, file, "missing")
        except OSError, ProjectOperationError:
            return self._remember_integrity(document_id, file, "verification_error")

    def _remember_integrity(
        self,
        document_id: str,
        file: CaseDocumentFile,
        status: IntegrityStatus,
    ) -> IntegrityStatus:
        self._integrity_cache[document_id] = (file.sha256, file.size_bytes, status)
        return status

    def _require(
        self,
        document_id: str,
        *,
        verify_file: bool,
        deadline: RequestDeadline | None,
    ) -> CaseDocument:
        _check_deadline(deadline, "case_document_get")
        document = self._find(
            canonical_uuid4(document_id),
            verify_file=verify_file,
            deadline=deadline,
        )
        if document is None:
            raise ProjectOperationError("entity_not_found", "Документ дела не найден.")
        _check_deadline(deadline, "case_document_get")
        return document

    def _find(
        self,
        document_id: str,
        *,
        verify_file: bool = True,
        deadline: RequestDeadline | None = None,
    ) -> CaseDocument | None:
        row = self._connection.execute(
            """
            SELECT d.case_document_id, d.document_kind, d.title, d.designation,
                   d.revision_label, d.document_date, d.issuer, d.notes,
                   d.record_revision, d.archived_at_utc, d.created_at_utc,
                   d.updated_at_utc, f.original_file_name, f.stored_relative_path,
                   f.media_type, f.size_bytes, f.sha256, f.attached_at_utc
            FROM case_documents d
            LEFT JOIN case_document_files f ON f.case_document_id = d.case_document_id
            WHERE d.case_document_id = ?
            """,
            (document_id,),
        ).fetchone()
        if row is None:
            return None
        wheel_ids = tuple(
            str(link[0])
            for link in self._connection.execute(
                "SELECT wheel_model_id FROM case_document_wheel_models WHERE case_document_id=? ORDER BY wheel_model_id",
                (document_id,),
            )
        )
        specimen_ids = tuple(
            str(link[0])
            for link in self._connection.execute(
                "SELECT specimen_id FROM case_document_specimens WHERE case_document_id=? ORDER BY specimen_id",
                (document_id,),
            )
        )
        file = None
        if row[12] is not None:
            file = CaseDocumentFile(
                original_file_name=str(row[12]),
                stored_relative_path=str(row[13]),
                media_type=str(row[14]),
                size_bytes=int(row[15]),
                sha256=str(row[16]),
                attached_at_utc=require_canonical_utc_timestamp(str(row[17])),
            )
        kind = _document_kind(str(row[1]))
        designation = str(row[3])
        revision_label = str(row[4])
        warnings: list[CaseDocumentWarning] = []
        if file is None:
            warnings.append("case_document_file_missing")
        if kind in NORMATIVE_KINDS and designation == "":
            warnings.append("case_document_designation_missing")
        if kind in NORMATIVE_KINDS and revision_label == "":
            warnings.append("case_document_revision_missing")
        integrity_status: IntegrityStatus = "not_attached"
        if file is not None:
            integrity_status = self._integrity_status(document_id, file, deadline) if verify_file else self._presence_status(document_id, file)
            if integrity_status == "missing":
                warnings.insert(0, "case_document_file_missing")
        return CaseDocument(
            case_document_id=canonical_uuid4(str(row[0])),
            document_kind=kind,
            title=str(row[2]),
            designation=designation,
            revision_label=revision_label,
            document_date=_stored_date(row[5]),
            issuer=str(row[6]),
            notes=str(row[7]),
            record_revision=int(row[8]),
            archived_at_utc=_stored_timestamp(row[9]),
            created_at_utc=require_canonical_utc_timestamp(str(row[10])),
            updated_at_utc=require_canonical_utc_timestamp(str(row[11])),
            file=file,
            integrity_status=integrity_status,
            wheel_model_ids=wheel_ids,
            specimen_ids=specimen_ids,
            warnings=tuple(warnings),
        )

    def _require_targets(
        self,
        wheel_model_ids: tuple[str, ...],
        specimen_ids: tuple[str, ...],
    ) -> None:
        for table, field, identifiers in (
            ("wheel_models", "wheel_model_id", wheel_model_ids),
            ("specimens", "specimen_id", specimen_ids),
        ):
            if not identifiers:
                continue
            placeholders = ",".join("?" for _ in identifiers)
            count = int(
                self._connection.execute(
                    f"SELECT count(*) FROM {table} WHERE {field} IN ({placeholders})",
                    identifiers,
                ).fetchone()[0]
            )
            if count != len(identifiers):
                raise ProjectOperationError("entity_not_found", "Объект применимости документа не найден.")

    def _replace_links(
        self,
        document_id: str,
        wheel_model_ids: tuple[str, ...],
        specimen_ids: tuple[str, ...],
    ) -> None:
        self._connection.execute(
            "DELETE FROM case_document_wheel_models WHERE case_document_id=?",
            (document_id,),
        )
        self._connection.executemany(
            "INSERT INTO case_document_wheel_models (case_document_id, wheel_model_id) VALUES (?, ?)",
            ((document_id, identifier) for identifier in wheel_model_ids),
        )
        self._connection.execute(
            "DELETE FROM case_document_specimens WHERE case_document_id=?",
            (document_id,),
        )
        self._connection.executemany(
            "INSERT INTO case_document_specimens (case_document_id, specimen_id) VALUES (?, ?)",
            ((document_id, identifier) for identifier in specimen_ids),
        )


def _normalize_values(values: Mapping[str, object]) -> dict[str, object]:
    kind = values.get("documentKind")
    if not isinstance(kind, str):
        raise ValueError("invalid_document_kind")
    document_date = values.get("documentDate")
    if document_date is not None and not isinstance(document_date, str):
        raise ValueError("invalid_document_date")
    return {
        "documentKind": _document_kind(kind),
        "title": _required_text(values.get("title"), 300),
        "designation": _optional_text(values.get("designation"), 200),
        "revisionLabel": _optional_text(values.get("revisionLabel"), 200),
        "documentDate": canonical_date(document_date),
        "issuer": _optional_text(values.get("issuer"), 300),
        "notes": _optional_text(values.get("notes"), 4_000),
    }


def _document_kind(value: str) -> DocumentKind:
    match value:
        case "technical_specification":
            return "technical_specification"
        case "individual_test_method":
            return "individual_test_method"
        case "typical_test_method":
            return "typical_test_method"
        case "customer_requirement":
            return "customer_requirement"
        case "test_request":
            return "test_request"
        case "operational_documentation":
            return "operational_documentation"
        case "standard":
            return "standard"
        case "drawing":
            return "drawing"
        case "measurement_or_attestation_record":
            return "measurement_or_attestation_record"
        case "other":
            return "other"
        case _:
            raise ValueError("invalid_document_kind")


def _normalize_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({canonical_uuid4(value) for value in values}))


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


def _document_values(document: CaseDocument) -> dict[str, object]:
    return {
        "documentKind": document.document_kind,
        "title": document.title,
        "designation": document.designation,
        "revisionLabel": document.revision_label,
        "documentDate": document.document_date,
        "issuer": document.issuer,
        "notes": document.notes,
    }


def _with_integrity(
    document: CaseDocument,
    integrity_status: IntegrityStatus,
) -> CaseDocument:
    warnings: list[CaseDocumentWarning] = []
    if document.file is None or integrity_status == "missing":
        warnings.append("case_document_file_missing")
    if document.document_kind in NORMATIVE_KINDS and document.designation == "":
        warnings.append("case_document_designation_missing")
    if document.document_kind in NORMATIVE_KINDS and document.revision_label == "":
        warnings.append("case_document_revision_missing")
    return replace(
        document,
        integrity_status=integrity_status,
        warnings=tuple(warnings),
    )


def _build_document_result(
    *,
    document_id: str,
    normalized: Mapping[str, object],
    record_revision: int,
    archived_at_utc: str | None,
    created_at_utc: str,
    updated_at_utc: str,
    file: CaseDocumentFile | None,
    integrity_status: IntegrityStatus,
    wheel_model_ids: tuple[str, ...],
    specimen_ids: tuple[str, ...],
) -> CaseDocument:
    document = CaseDocument(
        case_document_id=document_id,
        document_kind=_document_kind(str(normalized["documentKind"])),
        title=str(normalized["title"]),
        designation=str(normalized["designation"]),
        revision_label=str(normalized["revisionLabel"]),
        document_date=_stored_date(normalized["documentDate"]),
        issuer=str(normalized["issuer"]),
        notes=str(normalized["notes"]),
        record_revision=record_revision,
        archived_at_utc=archived_at_utc,
        created_at_utc=created_at_utc,
        updated_at_utc=updated_at_utc,
        file=file,
        integrity_status=integrity_status,
        wheel_model_ids=wheel_model_ids,
        specimen_ids=specimen_ids,
        warnings=(),
    )
    return _with_integrity(document, integrity_status)


def _changes(
    before: dict[str, object],
    after: dict[str, object],
) -> dict[str, dict[str, object]]:
    return {field: {"before": before[field], "after": after[field]} for field in after if before[field] != after[field]}


def _update_payload(
    document_id: str,
    from_revision: int,
    to_revision: int,
    changes: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    return {
        "entityType": "caseDocument",
        "entityId": document_id,
        "fromRevision": from_revision,
        "toRevision": to_revision,
        "changedFields": list(changes),
        "changes": changes,
    }


def _stored_date(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or canonical_date(value) != value:
        raise ValueError("invalid_stored_date")
    return value


def _stored_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid_stored_timestamp")
    return require_canonical_utc_timestamp(value)


def _commit(
    connection: sqlite3.Connection,
    deadline: RequestDeadline | None,
    stage: str,
) -> None:
    _check_deadline(deadline, f"{stage}_commit")
    connection.commit()


def _check_deadline(deadline: RequestDeadline | None, stage: str) -> None:
    if deadline is not None:
        deadline.check(stage)


def _revision_conflict(expected: int | None, actual: int | None) -> ProjectOperationError:
    return ProjectOperationError(
        "revision_conflict",
        "Сведения были изменены. Перечитайте актуальную редакцию.",
        details={"expectedRevision": expected, "actualRevision": actual},
    )


def _same_file_snapshot(existing: CaseDocumentFile, staged: _StagedFile) -> bool:
    return (
        existing.original_file_name == staged.original_file_name
        and existing.media_type == staged.media_type
        and existing.size_bytes == staged.size_bytes
        and existing.sha256 == staged.sha256
        and PurePosixPath(existing.stored_relative_path).suffix.lower() == staged.extension
    )


def _file_payload(staged: _StagedFile, relative_path: str) -> dict[str, object]:
    return {
        "originalFileName": staged.original_file_name,
        "mediaType": staged.media_type,
        "sizeBytes": staged.size_bytes,
        "sha256": staged.sha256,
        "storedRelativePath": relative_path,
    }


def _ensure_directory(path: Path) -> None:
    try:
        path.mkdir(exist_ok=True)
        path_stat = os.lstat(path)
    except OSError as error:
        raise ProjectOperationError("storage_error", "Managed directory недоступна.") from error
    if not stat.S_ISDIR(path_stat.st_mode) or _is_reparse(path_stat):
        raise ProjectOperationError("storage_error", "Managed directory имеет небезопасный тип.")


def _is_reparse(value: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, "st_file_attributes", 0) & reparse_flag)


def _validate_staged_content(
    path: Path,
    extension: str,
    prefix: bytes,
    deadline: RequestDeadline | None,
) -> None:
    _check_deadline(deadline, "case_document_file_signature")
    valid = True
    if extension == ".pdf":
        valid = prefix.startswith(b"%PDF-")
    elif extension == ".png":
        valid = prefix.startswith(b"\x89PNG\r\n\x1a\n")
    elif extension in {".jpg", ".jpeg"}:
        valid = prefix.startswith(b"\xff\xd8\xff")
    elif extension in {".docx", ".xlsx"}:
        required = "word/document.xml" if extension == ".docx" else "xl/workbook.xml"
        try:
            with zipfile.ZipFile(path) as archive:
                names: set[str] = set()
                for member in archive.infolist():
                    _check_deadline(deadline, "case_document_office_structure")
                    names.add(member.filename)
                valid = len(names) <= 10_000 and "[Content_Types].xml" in names and required in names
        except zipfile.BadZipFile:
            valid = False
    if not valid:
        raise ProjectOperationError("unsupported_file_type", "Содержимое файла не соответствует расширению.")


def _remove_operation_file(path: Path) -> None:
    try:
        path_stat = os.lstat(path)
        if stat.S_ISREG(path_stat.st_mode) and not _is_reparse(path_stat):
            path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def _evidence_ids(rows: Sequence[Sequence[object]]) -> tuple[str, ...]:
    identifiers = tuple(canonical_uuid4(str(row[0])) for row in rows)
    if identifiers != tuple(sorted(set(identifiers))):
        raise _evidence_error()
    return identifiers


def _positive_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise _evidence_error()
    return value


def _parse_payload(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, str):
        raise _evidence_error()
    try:
        return JSON_OBJECT_ADAPTER.validate_json(value, strict=True)
    except ValidationError as error:
        raise _evidence_error() from error


def _json_object(value: object) -> dict[str, JsonValue]:
    try:
        return JSON_OBJECT_ADAPTER.validate_python(value, strict=True)
    except ValidationError as error:
        raise _evidence_error() from error


def _normalize_initial_audit_state(
    document_id: str,
    value: Mapping[str, object],
) -> dict[str, object]:
    string_value = dict(value)
    allowed = set(DOCUMENT_FIELDS) | {"wheelModelIds", "specimenIds", "file"}
    required = set(DOCUMENT_FIELDS) | {"wheelModelIds", "specimenIds"}
    if not required <= set(string_value) or not set(string_value) <= allowed:
        raise _evidence_error()
    state: dict[str, object] = {
        **_normalize_values(string_value),
        "wheelModelIds": list(_audit_ids(string_value["wheelModelIds"])),
        "specimenIds": list(_audit_ids(string_value["specimenIds"])),
    }
    if "file" in string_value:
        state["file"] = _normalize_file_payload(document_id, _json_object(string_value["file"]))
    return state


def _audit_ids(value: object) -> tuple[str, ...]:
    try:
        raw_identifiers = STRING_LIST_ADAPTER.validate_python(value, strict=True)
    except ValidationError as error:
        raise _evidence_error() from error
    identifiers = tuple(canonical_uuid4(item) for item in raw_identifiers)
    if identifiers != tuple(sorted(set(identifiers))):
        raise _evidence_error()
    return identifiers


def _normalize_file_payload(
    document_id: str,
    value: Mapping[str, object],
) -> dict[str, object]:
    payload = dict(value)
    if set(payload) != {
        "originalFileName",
        "storedRelativePath",
        "mediaType",
        "sizeBytes",
        "sha256",
    }:
        raise _evidence_error()
    original_name = payload["originalFileName"]
    relative_path = payload["storedRelativePath"]
    media_type = payload["mediaType"]
    size_bytes = payload["sizeBytes"]
    sha256 = payload["sha256"]
    if (
        not isinstance(original_name, str)
        or original_name in {"", ".", ".."}
        or len(original_name) > 255
        or "/" in original_name
        or "\\" in original_name
        or not isinstance(relative_path, str)
        or not isinstance(media_type, str)
        or media_type not in FILE_MEDIA_TYPES.values()
        or not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or not 0 < size_bytes <= MAX_FILE_BYTES
        or not isinstance(sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", sha256, re.ASCII) is None
    ):
        raise _evidence_error()
    relative = PurePosixPath(relative_path)
    extension = relative.suffix.lower()
    if (
        relative.is_absolute()
        or relative.as_posix() != relative_path
        or relative.parts != ("assets", "documents", document_id, f"{sha256}{extension}")
        or extension not in FILE_MEDIA_TYPES
        or FILE_MEDIA_TYPES[extension] != media_type
        or "\\" in relative_path
    ):
        raise _evidence_error()
    return {
        "originalFileName": original_name,
        "storedRelativePath": relative_path,
        "mediaType": media_type,
        "sizeBytes": size_bytes,
        "sha256": sha256,
    }


def _parse_changes(payload: Mapping[str, object]) -> dict[str, dict[str, object]]:
    try:
        changed_fields = STRING_LIST_ADAPTER.validate_python(payload.get("changedFields"), strict=True)
        changes = JSON_OBJECT_ADAPTER.validate_python(payload.get("changes"), strict=True)
    except ValidationError as error:
        raise _evidence_error() from error
    if len(changed_fields) != len(set(changed_fields)) or set(changed_fields) != set(changes):
        raise _evidence_error()
    result: dict[str, dict[str, object]] = {}
    for field, change in changes.items():
        try:
            parsed_change = JSON_OBJECT_ADAPTER.validate_python(change, strict=True)
        except ValidationError as error:
            raise _evidence_error() from error
        if set(parsed_change) != {"before", "after"}:
            raise _evidence_error()
        if parsed_change["before"] == parsed_change["after"]:
            raise _evidence_error()
        result[field] = {"before": parsed_change["before"], "after": parsed_change["after"]}
    return result


def _apply_changes(
    state: dict[str, object],
    changes: dict[str, dict[str, object]],
) -> None:
    for field, change in changes.items():
        if state.get(field) != change["before"]:
            raise _evidence_error()
        state[field] = change["after"]


def _validate_audit_state(document_id: str, state: dict[str, object]) -> None:
    normalized = _normalize_values(state)
    if any(state.get(field) != normalized[field] for field in DOCUMENT_FIELDS):
        raise _evidence_error()
    wheel_ids = _audit_ids(state.get("wheelModelIds"))
    specimen_ids = _audit_ids(state.get("specimenIds"))
    if state.get("wheelModelIds") != list(wheel_ids) or state.get("specimenIds") != list(specimen_ids):
        raise _evidence_error()
    file_value = state.get("file")
    if file_value is not None:
        parsed_file = _json_object(file_value)
        if _normalize_file_payload(document_id, parsed_file) != file_value:
            raise _evidence_error()


def _evidence_error() -> ProjectOperationError:
    return ProjectOperationError(
        "corrupt_project",
        "Audit evidence и документы дела не согласованы.",
    )


def _raise_mutation_error(error: Exception) -> Never:
    if isinstance(error, ProjectOperationError):
        raise error
    if isinstance(error, (OSError, sqlite3.DatabaseError)):
        raise ProjectOperationError(
            "storage_error",
            "Операция с документом не была зафиксирована.",
        ) from error
    raise error
