from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
from threading import Event
from time import monotonic
from typing import Final, Literal, cast
from uuid import RFC_4122, UUID

from impeller_reliability.integration.r130run.m9a import (
    M9aPackageFacts,
    canonical_json,
)
from impeller_reliability.integration.r130run.models import (
    UPSTREAM_COMMIT,
    VALIDATOR_VERSION,
    RunPackageValidationReport,
)
from impeller_reliability.integration.r130run.validator import (
    RunPackageValidator,
    SourceChangedError,
    ValidationControl,
    ValidationTimeoutError,
)
from impeller_reliability.persistence.audit import audit_now, insert_audit
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_paths import (
    ensure_managed_directory,
    inspect_reserved_directory,
)
from impeller_reliability.persistence.timestamps import require_canonical_utc_timestamp
from impeller_reliability.worker.deadline import RequestDeadline

SourceIntegrityStatus = Literal["verified", "missing", "modified", "verification_error"]
ImportDisposition = Literal["created", "existing"]

SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
FINAL_PATH_RE: Final = re.compile(
    r"^imports/r130sh/([0-9a-f-]{36})/rev-([1-9][0-9]*)/([0-9a-f]{64})\.r130run$",
)
STAGING_NAME_RE: Final = re.compile(r"^[0-9a-f-]{36}\.part$")
STREAM_CHUNK_BYTES: Final = 1024 * 1024
WINDOWS_REPARSE_POINT_ATTRIBUTE: Final = 0x0400


@dataclass(frozen=True, slots=True)
class ImportedRunSummary:
    local_import_id: str
    package_id: str
    export_revision: int
    outer_package_sha256: str
    run_id: str
    package_kind: str
    package_schema: str
    package_created_at_utc: str
    source_snapshot_sha256: str
    producer_name: str
    producer_version: str
    producer_build_id: str
    producer_git_commit: str
    outer_size_bytes: int
    imported_at_utc: str
    validator_version: str
    validation_contract_commit: str
    structural_verdict: str
    semantic_verdict: str
    source_integrity: SourceIntegrityStatus
    source_specimen_id: str
    local_specimen_id: str | None
    binding_revision: int
    mode: str
    technical_status: str | None
    termination_reason: str | None
    specimen_outcome: str | None
    run_validity: str | None
    data_completeness: str | None
    imported_existing: bool = False


@dataclass(frozen=True, slots=True)
class ImportedRunDetail:
    summary: ImportedRunSummary
    projection: dict[str, object]
    inventory: tuple[dict[str, object], ...]
    semantic_coverage: tuple[dict[str, object], ...]
    validation_findings: tuple[dict[str, object], ...]
    enrichment_resolutions: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class SpecimenBinding:
    source_specimen_id: str
    local_specimen_id: str | None
    record_revision: int
    updated_by_actor: str | None
    reason: str
    created_at_utc: str
    updated_at_utc: str


class R130shSourceRepository:
    def __init__(self, connection: sqlite3.Connection, project_path: Path) -> None:
        self._connection = connection
        self._project_path = project_path
        self._integrity_cache: dict[
            str,
            tuple[SourceIntegrityStatus, tuple[int, int, int, int] | None],
        ] = {}

    def recover_managed_files(self, deadline: RequestDeadline | None = None) -> None:
        _check_deadline(deadline, "r130sh_recovery")
        imports_root = self._project_path / "imports"
        r130sh_root = imports_root / "r130sh"
        if not r130sh_root.exists():
            return
        inspect_reserved_directory(imports_root, "imports/")
        inspect_reserved_directory(r130sh_root, "imports/r130sh/")
        staging_root = r130sh_root / ".staging"
        if staging_root.exists():
            inspect_reserved_directory(staging_root, "imports/r130sh/.staging/")
            for path in staging_root.iterdir():
                _check_deadline(deadline, "r130sh_recovery_staging")
                if STAGING_NAME_RE.fullmatch(path.name) and _ordinary_file(path):
                    path.unlink()
        registered: set[str] = set()
        for row in self._connection.execute("SELECT managed_relative_path FROM r130sh_sources"):
            _check_deadline(deadline, "r130sh_recovery_registry")
            registered.add(str(row[0]))
        for package_root in r130sh_root.iterdir():
            _check_deadline(deadline, "r130sh_recovery_packages")
            if package_root == staging_root or not package_root.is_dir():
                continue
            try:
                package_id = str(UUID(package_root.name))
            except ValueError:
                continue
            if package_id != package_root.name:
                continue
            inspect_reserved_directory(package_root, f"imports/r130sh/{package_id}/")
            for revision_root in package_root.iterdir():
                _check_deadline(deadline, "r130sh_recovery_revisions")
                if not revision_root.is_dir() or re.fullmatch(r"rev-[1-9][0-9]*", revision_root.name) is None:
                    continue
                inspect_reserved_directory(
                    revision_root,
                    f"imports/r130sh/{package_id}/{revision_root.name}/",
                )
                for path in revision_root.iterdir():
                    _check_deadline(deadline, "r130sh_recovery_archives")
                    if not _ordinary_file(path):
                        continue
                    relative = path.relative_to(self._project_path).as_posix()
                    if FINAL_PATH_RE.fullmatch(relative) and relative not in registered:
                        path.unlink()

    def register(
        self,
        *,
        local_import_id: str,
        staged_path: Path,
        facts: M9aPackageFacts,
        report: RunPackageValidationReport,
        deadline: RequestDeadline | None,
    ) -> ImportedRunSummary:
        local_import_id = _uuid4(local_import_id)
        _check_deadline(deadline, "r130sh_import_preflight")
        existing = self._find_by_package_revision(facts.package_id, facts.export_revision)
        if existing is not None:
            if existing.outer_package_sha256 != facts.outer_package_sha256:
                _remove_operation_file(staged_path)
                raise ProjectOperationError(
                    "import_integrity_conflict",
                    "Эта редакция package уже зарегистрирована с другим SHA-256.",
                    details={
                        "packageId": facts.package_id,
                        "exportRevision": facts.export_revision,
                    },
                )
            _remove_operation_file(staged_path)
            return _with_existing(existing)
        if (
            self._connection.execute(
                "SELECT 1 FROM r130sh_sources WHERE local_import_id=?",
                (local_import_id,),
            ).fetchone()
            is not None
        ):
            raise ProjectOperationError(
                "duplicate_entity",
                "Локальный идентификатор импорта уже используется.",
            )

        relative_path = _managed_relative_path(facts)
        final_path = self._project_path.joinpath(*PurePosixPath(relative_path).parts)
        imports_root = self._project_path / "imports"
        r130sh_root = imports_root / "r130sh"
        package_root = r130sh_root / facts.package_id
        revision_root = package_root / f"rev-{facts.export_revision}"
        ensure_managed_directory(imports_root, "imports/")
        ensure_managed_directory(r130sh_root, "imports/r130sh/")
        ensure_managed_directory(package_root, f"imports/r130sh/{facts.package_id}/")
        ensure_managed_directory(revision_root, f"imports/r130sh/{facts.package_id}/rev-{facts.export_revision}/")
        if final_path.exists():
            raise ProjectOperationError(
                "storage_error",
                "Managed archive path уже занят незарегистрированным файлом.",
            )
        _check_deadline(deadline, "r130sh_import_publish")
        os.replace(staged_path, final_path)
        published = True
        try:
            file_stat = final_path.lstat()
            if not _ordinary_file(final_path) or file_stat.st_size != facts.outer_size_bytes:
                raise ProjectOperationError(
                    "storage_error",
                    "Managed archive не прошёл post-publication size check.",
                )
            if _sha256_file(final_path, deadline) != facts.outer_package_sha256:
                raise ProjectOperationError(
                    "storage_error",
                    "Managed archive не прошёл post-publication SHA-256 check.",
                )
            published_signature = _file_signature(final_path)
            now = audit_now(self._connection)
            binding_local_specimen_id: str | None = None
            binding_revision = 1
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                concurrent = self._find_by_package_revision(
                    facts.package_id,
                    facts.export_revision,
                )
                if concurrent is not None:
                    if concurrent.outer_package_sha256 != facts.outer_package_sha256:
                        raise ProjectOperationError(
                            "import_integrity_conflict",
                            "Эта редакция package уже зарегистрирована с другим SHA-256.",
                        )
                    self._connection.rollback()
                    _remove_operation_file(final_path)
                    return _with_existing(concurrent)
                self._insert_source(
                    local_import_id,
                    relative_path,
                    facts,
                    report,
                    now,
                )
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO r130sh_specimen_bindings (
                        source_specimen_id, local_specimen_id, record_revision,
                        updated_by_actor, reason, created_at_utc, updated_at_utc
                    ) VALUES (?, NULL, 1, NULL, '', ?, ?)
                    """,
                    (facts.projection.source_specimen_id, now, now),
                )
                binding_row = self._connection.execute(
                    "SELECT local_specimen_id, record_revision FROM r130sh_specimen_bindings WHERE source_specimen_id=?",
                    (facts.projection.source_specimen_id,),
                ).fetchone()
                if binding_row is None:
                    raise ProjectOperationError(
                        "storage_error",
                        "Binding state импортированного source specimen отсутствует.",
                    )
                binding_local_specimen_id = None if binding_row[0] is None else str(binding_row[0])
                binding_revision = int(binding_row[1])
                self._insert_projection(local_import_id, facts)
                for item in facts.inventory:
                    self._connection.execute(
                        """
                        INSERT INTO r130sh_source_inventory (
                            local_import_id, path, media_type, size_bytes,
                            sha256, row_count, semantic_coverage
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            local_import_id,
                            item.path,
                            item.media_type,
                            item.size_bytes,
                            item.sha256,
                            item.row_count,
                            item.semantic_coverage,
                        ),
                    )
                insert_audit(
                    self._connection,
                    event_type="r130sh_import.completed",
                    actor_kind="user",
                    occurred_at_utc=now,
                    payload={
                        "localImportId": local_import_id,
                        "packageId": facts.package_id,
                        "exportRevision": facts.export_revision,
                        "outerPackageSha256": facts.outer_package_sha256,
                        "runId": facts.run_id,
                        "packageKind": facts.package_kind,
                        "managedRelativePath": relative_path,
                        "producer": {
                            "name": facts.producer_name,
                            "version": facts.producer_version,
                            "buildId": facts.producer_build_id,
                            "gitCommit": facts.producer_git_commit,
                        },
                        "validationContractCommit": UPSTREAM_COMMIT,
                    },
                )
                _check_deadline(deadline, "r130sh_import_commit")
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
            published = False
            self._integrity_cache[local_import_id] = (
                "verified",
                published_signature,
            )
            return _created_summary(
                local_import_id,
                facts,
                now,
                binding_local_specimen_id,
                binding_revision,
                report.semanticVerdict,
            )
        except Exception:
            if (
                published
                and self._connection.execute(
                    "SELECT 1 FROM r130sh_sources WHERE local_import_id=?",
                    (local_import_id,),
                ).fetchone()
                is None
            ):
                _remove_operation_file(final_path)
            raise

    def list(self, deadline: RequestDeadline | None = None) -> tuple[ImportedRunSummary, ...]:
        _check_deadline(deadline, "r130sh_list")
        rows = self._connection.execute(
            _SUMMARY_QUERY + " ORDER BY sources.imported_at_utc DESC, sources.local_import_id",
        ).fetchall()
        result: list[ImportedRunSummary] = []
        for row in rows:
            _check_deadline(deadline, "r130sh_list_item")
            result.append(self._summary_from_row(row))
        _check_deadline(deadline, "r130sh_list")
        return tuple(result)

    def get(
        self,
        local_import_id: str,
        *,
        verify_source: bool = True,
        deadline: RequestDeadline | None = None,
    ) -> ImportedRunDetail:
        _check_deadline(deadline, "r130sh_get")
        local_import_id = _uuid4(local_import_id)
        row = self._connection.execute(
            _SUMMARY_QUERY + " WHERE sources.local_import_id=?",
            (local_import_id,),
        ).fetchone()
        if row is None:
            raise ProjectOperationError("entity_not_found", "Импортированный запуск не найден.")
        if verify_source:
            self.verify_source(local_import_id, deadline=deadline)
        summary = self._summary_from_row(row)
        projection_row = self._connection.execute(
            "SELECT * FROM r130sh_run_projections WHERE local_import_id=?",
            (local_import_id,),
        ).fetchone()
        if projection_row is None:
            raise ProjectOperationError("corrupt_project", "Projection импортированного запуска отсутствует.")
        inventory_rows = self._connection.execute(
            """
            SELECT path, media_type, size_bytes, sha256, row_count, semantic_coverage
            FROM r130sh_source_inventory
            WHERE local_import_id=? ORDER BY path
            """,
            (local_import_id,),
        ).fetchall()
        resolution_rows = self._connection.execute(
            """
            SELECT resolution_id, source_payload_path, source_field,
                   target_entity_type, target_entity_id, target_field,
                   decision, actor, occurred_at_utc, reason
            FROM r130sh_enrichment_resolutions
            WHERE local_import_id=? ORDER BY occurred_at_utc, resolution_id LIMIT 33
            """,
            (local_import_id,),
        ).fetchall()
        source_row = self._connection.execute(
            """
            SELECT semantic_coverage_json, validation_findings_json
            FROM r130sh_sources WHERE local_import_id=?
            """,
            (local_import_id,),
        ).fetchone()
        if source_row is None:
            raise ProjectOperationError("corrupt_project", "Registry импортированного запуска отсутствует.")
        if len(resolution_rows) > 32:
            raise ProjectOperationError(
                "corrupt_project",
                "Количество enrichment resolutions превышает M03B contract.",
            )
        projection = _projection_payload(projection_row)
        result = ImportedRunDetail(
            summary=summary,
            projection=projection,
            inventory=tuple(
                {
                    "path": _inventory_path(str(item[0])),
                    "mediaType": str(item[1]),
                    "sizeBytes": int(item[2]),
                    "sha256": str(item[3]),
                    "rowCount": None if item[4] is None else int(item[4]),
                    "semanticCoverage": str(item[5]),
                }
                for item in inventory_rows
            ),
            semantic_coverage=tuple(_json_array(str(source_row[0]))),
            validation_findings=tuple(_json_array(str(source_row[1]))),
            enrichment_resolutions=tuple(
                {
                    "resolutionId": str(item[0]),
                    "sourcePayloadPath": str(item[1]),
                    "sourceField": str(item[2]),
                    "targetEntityType": str(item[3]),
                    "targetEntityId": str(item[4]),
                    "targetField": str(item[5]),
                    "decision": str(item[6]),
                    "actor": str(item[7]),
                    "occurredAtUtc": str(item[8]),
                    "reason": str(item[9]),
                }
                for item in resolution_rows
            ),
        )
        _check_deadline(deadline, "r130sh_get")
        return result

    def verify_source(
        self,
        local_import_id: str,
        *,
        deadline: RequestDeadline | None = None,
    ) -> SourceIntegrityStatus:
        local_import_id = _uuid4(local_import_id)
        _check_deadline(deadline, "r130sh_verify")
        row = self._connection.execute(
            """
            SELECT managed_relative_path, outer_size_bytes, outer_package_sha256
            FROM r130sh_sources WHERE local_import_id=?
            """,
            (local_import_id,),
        ).fetchone()
        if row is None:
            raise ProjectOperationError("entity_not_found", "Импортированный запуск не найден.")
        path: Path | None = None
        try:
            path = _managed_path(self._project_path, str(row[0]))
            if not path.exists():
                status: SourceIntegrityStatus = "missing"
            elif not _ordinary_file(path) or path.stat().st_size != int(row[1]):
                status = "modified"
            else:
                actual_sha = _sha256_file(path, deadline)
                if actual_sha != str(row[2]):
                    status = "modified"
                else:
                    report = RunPackageValidator().validate(
                        path,
                        ValidationControl(
                            Event(),
                            monotonic() + (30 if deadline is None else deadline.remaining_seconds(30)),
                            _ignore_validation_progress,
                        ),
                    )
                    status = "verified" if report.structuralVerdict == "passed" else "verification_error"
        except SourceChangedError:
            status = "modified"
        except ValidationTimeoutError as error:
            raise ProjectOperationError(
                "timeout",
                "Проверка imported archive не завершилась в установленный срок.",
                retryable=True,
            ) from error
        except OSError, ValueError:
            status = "verification_error"
        _check_deadline(deadline, "r130sh_verify")
        signature = _file_signature(path) if path is not None and status in {"verified", "modified"} else None
        self._integrity_cache[local_import_id] = (status, signature)
        return status

    def get_binding(
        self,
        source_specimen_id: str,
        deadline: RequestDeadline | None = None,
    ) -> SpecimenBinding:
        _check_deadline(deadline, "r130sh_binding_get")
        row = self._connection.execute(
            """
            SELECT source_specimen_id, local_specimen_id, record_revision,
                   updated_by_actor, reason, created_at_utc, updated_at_utc
            FROM r130sh_specimen_bindings WHERE source_specimen_id=?
            """,
            (_source_identity(source_specimen_id),),
        ).fetchone()
        if row is None:
            raise ProjectOperationError("entity_not_found", "Source specimen identity не найдена.")
        result = SpecimenBinding(
            source_specimen_id=str(row[0]),
            local_specimen_id=None if row[1] is None else str(row[1]),
            record_revision=int(row[2]),
            updated_by_actor=None if row[3] is None else str(row[3]),
            reason=str(row[4]),
            created_at_utc=require_canonical_utc_timestamp(str(row[5])),
            updated_at_utc=require_canonical_utc_timestamp(str(row[6])),
        )
        _check_deadline(deadline, "r130sh_binding_get")
        return result

    def bind_specimen(
        self,
        *,
        source_specimen_id: str,
        local_specimen_id: str | None,
        expected_revision: int,
        actor: str,
        reason: str,
        deadline: RequestDeadline | None,
    ) -> SpecimenBinding:
        current = self.get_binding(source_specimen_id, deadline)
        if current.record_revision != expected_revision:
            raise ProjectOperationError(
                "revision_conflict",
                "Binding была изменена после открытия формы.",
                details={
                    "expectedRevision": expected_revision,
                    "actualRevision": current.record_revision,
                },
            )
        normalized_local_id = None if local_specimen_id is None else _uuid4(local_specimen_id)
        actor = _required_text(actor, 200, "actor")
        reason = _required_text(reason, 2000, "reason")
        if current.local_specimen_id == normalized_local_id:
            return current
        if normalized_local_id is not None:
            row = self._connection.execute(
                "SELECT archived_at_utc FROM specimens WHERE specimen_id=?",
                (normalized_local_id,),
            ).fetchone()
            if row is None:
                raise ProjectOperationError("entity_not_found", "Local Specimen не найден.")
            if row[0] is not None:
                raise ProjectOperationError("entity_archived", "Архивный Specimen нельзя выбрать для binding.")
        now = audit_now(self._connection)
        new_revision = current.record_revision + 1
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._connection.execute(
                """
                UPDATE r130sh_specimen_bindings
                SET local_specimen_id=?, record_revision=?, updated_by_actor=?,
                    reason=?, updated_at_utc=?
                WHERE source_specimen_id=? AND record_revision=?
                """,
                (
                    normalized_local_id,
                    new_revision,
                    actor,
                    reason,
                    now,
                    current.source_specimen_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ProjectOperationError("revision_conflict", "Binding изменилась во время сохранения.")
            insert_audit(
                self._connection,
                event_type="r130sh_source.specimen_bound",
                actor_kind="user",
                occurred_at_utc=now,
                payload={
                    "sourceSpecimenId": current.source_specimen_id,
                    "localSpecimenId": normalized_local_id,
                    "previousLocalSpecimenId": current.local_specimen_id,
                    "fromRevision": expected_revision,
                    "toRevision": new_revision,
                    "actor": actor,
                    "reason": reason,
                },
            )
            _check_deadline(deadline, "r130sh_binding_commit")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return SpecimenBinding(
            source_specimen_id=current.source_specimen_id,
            local_specimen_id=normalized_local_id,
            record_revision=new_revision,
            updated_by_actor=actor,
            reason=reason,
            created_at_utc=current.created_at_utc,
            updated_at_utc=now,
        )

    def record_enrichment_resolution(
        self,
        *,
        resolution_id: str,
        local_import_id: str,
        source_payload_path: str,
        source_field: str,
        target_entity_type: str,
        target_entity_id: str,
        target_field: str,
        decision: str,
        actor: str,
        reason: str,
        expected_target_revision: int | None,
        deadline: RequestDeadline | None,
    ) -> ImportedRunDetail:
        resolution_id = _uuid4(resolution_id)
        local_import_id = _uuid4(local_import_id)
        normalized = _validate_resolution(
            source_payload_path,
            source_field,
            target_entity_type,
            target_entity_id,
            target_field,
            decision,
            actor,
            reason,
        )
        existing = self._connection.execute(
            """
            SELECT decision, actor, reason FROM r130sh_enrichment_resolutions
            WHERE local_import_id=? AND source_payload_path=? AND source_field=?
              AND target_entity_type=? AND target_entity_id=? AND target_field=?
            """,
            (local_import_id, *normalized[:5]),
        ).fetchone()
        if existing is not None:
            if (str(existing[0]), str(existing[1]), str(existing[2])) == normalized[5:]:
                return self.get(local_import_id, verify_source=False, deadline=deadline)
            raise ProjectOperationError(
                "resolution_conflict",
                "Для этой пары source/analyst уже записано другое решение.",
            )
        detail_before = self.get(local_import_id, verify_source=False, deadline=deadline)
        now = audit_now(self._connection)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            resolution_count = int(
                self._connection.execute(
                    "SELECT count(*) FROM r130sh_enrichment_resolutions WHERE local_import_id=?",
                    (local_import_id,),
                ).fetchone()[0],
            )
            if resolution_count >= 32:
                raise ProjectOperationError(
                    "validation_error",
                    "Для одного импорта допускается не более 32 enrichment resolutions.",
                )
            if normalized[5] == "copied_to_analyst":
                self._copy_source_value_to_analyst(
                    local_import_id=local_import_id,
                    source_payload_path=normalized[0],
                    source_field=normalized[1],
                    target_entity_type=normalized[2],
                    target_entity_id=normalized[3],
                    target_field=normalized[4],
                    expected_target_revision=expected_target_revision,
                    occurred_at_utc=now,
                )
            self._connection.execute(
                """
                INSERT INTO r130sh_enrichment_resolutions (
                    resolution_id, local_import_id, source_payload_path,
                    source_field, target_entity_type, target_entity_id,
                    target_field, decision, actor, occurred_at_utc, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (resolution_id, local_import_id, *normalized[:7], now, normalized[7]),
            )
            insert_audit(
                self._connection,
                event_type="r130sh_source.enrichment_resolution_recorded",
                actor_kind="user",
                occurred_at_utc=now,
                payload={
                    "resolutionId": resolution_id,
                    "localImportId": local_import_id,
                    "sourcePayloadPath": normalized[0],
                    "sourceField": normalized[1],
                    "targetEntityType": normalized[2],
                    "targetEntityId": normalized[3],
                    "targetField": normalized[4],
                    "decision": normalized[5],
                    "actor": normalized[6],
                    "reason": normalized[7],
                },
            )
            _check_deadline(deadline, "r130sh_resolution_commit")
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise ProjectOperationError(
                "resolution_conflict",
                "Для этой пары source/analyst уже записано решение.",
            ) from error
        except Exception:
            self._connection.rollback()
            raise
        return replace(
            detail_before,
            enrichment_resolutions=tuple(
                sorted(
                    (
                        *detail_before.enrichment_resolutions,
                        {
                            "resolutionId": resolution_id,
                            "sourcePayloadPath": normalized[0],
                            "sourceField": normalized[1],
                            "targetEntityType": normalized[2],
                            "targetEntityId": normalized[3],
                            "targetField": normalized[4],
                            "decision": normalized[5],
                            "actor": normalized[6],
                            "occurredAtUtc": now,
                            "reason": normalized[7],
                        },
                    ),
                    key=lambda item: (str(item["occurredAtUtc"]), str(item["resolutionId"])),
                ),
            ),
        )

    def _copy_source_value_to_analyst(
        self,
        *,
        local_import_id: str,
        source_payload_path: str,
        source_field: str,
        target_entity_type: str,
        target_entity_id: str,
        target_field: str,
        expected_target_revision: int | None,
        occurred_at_utc: str,
    ) -> None:
        source_value = self._source_value(
            local_import_id,
            source_payload_path,
            source_field,
        )
        if target_entity_type == "customer_profile":
            self._copy_customer_value(
                target_entity_id,
                target_field,
                source_value,
                expected_target_revision,
                occurred_at_utc,
            )
            return
        table, id_column, column, entity_type = {
            ("wheel_model", "designation"): (
                "wheel_models",
                "wheel_model_id",
                "designation",
                "wheelModel",
            ),
            ("wheel_model", "nominalSpeedRpm"): (
                "wheel_models",
                "wheel_model_id",
                "nominal_speed_rpm",
                "wheelModel",
            ),
            ("specimen", "marking"): (
                "specimens",
                "specimen_id",
                "marking",
                "specimen",
            ),
            ("specimen", "workingDiameterMm"): (
                "specimens",
                "specimen_id",
                "working_diameter_mm",
                "specimen",
            ),
        }.get((target_entity_type, target_field), (None, None, None, None))
        if table is None or id_column is None or column is None or entity_type is None:
            raise ProjectOperationError(
                "validation_error",
                "Это analyst field нельзя заполнять после создания entity.",
            )
        row = self._connection.execute(
            f"SELECT {column}, record_revision, archived_at_utc FROM {table} WHERE {id_column}=?",
            (target_entity_id,),
        ).fetchone()
        if row is None:
            raise ProjectOperationError("entity_not_found", "Analyst entity не найдена.")
        if row[2] is not None:
            raise ProjectOperationError("entity_archived", "Архивную analyst entity нельзя изменять.")
        revision = int(row[1])
        if expected_target_revision != revision:
            raise ProjectOperationError(
                "revision_conflict",
                "Analyst entity была изменена после открытия формы.",
                details={
                    "expectedRevision": expected_target_revision,
                    "actualRevision": revision,
                },
            )
        before = row[0]
        if before not in {None, ""}:
            raise ProjectOperationError(
                "validation_error",
                "Непустое analyst value нельзя перезаписать автоматически.",
            )
        stored_value: object = source_value
        if target_field == "nominalSpeedRpm":
            try:
                stored_value = int(str(source_value))
            except ValueError as error:
                raise ProjectOperationError(
                    "validation_error",
                    "Source nominal RPM не является целым значением.",
                ) from error
            if stored_value <= 0:
                raise ProjectOperationError(
                    "validation_error",
                    "Source nominal RPM должен быть положительным.",
                )
        next_revision = revision + 1
        cursor = self._connection.execute(
            f"UPDATE {table} SET {column}=?, record_revision=?, updated_at_utc=? WHERE {id_column}=? AND record_revision=?",
            (stored_value, next_revision, occurred_at_utc, target_entity_id, revision),
        )
        if cursor.rowcount != 1:
            raise ProjectOperationError("revision_conflict", "Analyst entity изменилась во время сохранения.")
        insert_audit(
            self._connection,
            event_type=f"{target_entity_type}.updated",
            actor_kind="user",
            occurred_at_utc=occurred_at_utc,
            payload={
                "entityType": entity_type,
                "entityId": target_entity_id,
                "fromRevision": revision,
                "toRevision": next_revision,
                "changedFields": [target_field],
                "changes": {
                    target_field: {
                        "before": before,
                        "after": stored_value,
                    },
                },
            },
        )

    def _copy_customer_value(
        self,
        target_entity_id: str,
        target_field: str,
        source_value: object,
        expected_target_revision: int | None,
        occurred_at_utc: str,
    ) -> None:
        project_id_row = self._connection.execute(
            "SELECT project_id FROM project_metadata",
        ).fetchone()
        if project_id_row is None or target_entity_id != str(project_id_row[0]):
            raise ProjectOperationError("entity_not_found", "CustomerProfile target не найден.")
        column = {
            "fullName": "full_name",
            "legalAddress": "legal_address",
            "actualAddress": "actual_address",
        }.get(target_field)
        if column is None:
            raise ProjectOperationError("validation_error", "CustomerProfile field не поддерживается.")
        row = self._connection.execute(
            f"SELECT {column}, record_revision FROM customer_profile WHERE project_id=?",
            (target_entity_id,),
        ).fetchone()
        if row is None:
            if target_field != "fullName" or expected_target_revision is not None:
                raise ProjectOperationError(
                    "validation_error",
                    "Сначала явно создайте CustomerProfile с обязательным именем.",
                )
            values = {
                "fullName": str(source_value),
                "legalAddress": "",
                "actualAddress": "",
                "notes": "",
            }
            self._connection.execute(
                """
                INSERT INTO customer_profile (
                    project_id, full_name, legal_address, actual_address, notes,
                    record_revision, created_at_utc, updated_at_utc
                ) VALUES (?, ?, '', '', '', 1, ?, ?)
                """,
                (target_entity_id, str(source_value), occurred_at_utc, occurred_at_utc),
            )
            insert_audit(
                self._connection,
                event_type="customer_profile.created",
                actor_kind="user",
                occurred_at_utc=occurred_at_utc,
                payload={
                    "entityType": "customerProfile",
                    "entityId": target_entity_id,
                    "toRevision": 1,
                    "changedFields": list(values),
                    "after": values,
                },
            )
            return
        revision = int(row[1])
        if expected_target_revision != revision:
            raise ProjectOperationError(
                "revision_conflict",
                "CustomerProfile была изменена после открытия формы.",
                details={
                    "expectedRevision": expected_target_revision,
                    "actualRevision": revision,
                },
            )
        before = row[0]
        if before not in {None, ""}:
            raise ProjectOperationError(
                "validation_error",
                "Непустое analyst value нельзя перезаписать автоматически.",
            )
        next_revision = revision + 1
        cursor = self._connection.execute(
            f"UPDATE customer_profile SET {column}=?, record_revision=?, updated_at_utc=? WHERE project_id=? AND record_revision=?",
            (str(source_value), next_revision, occurred_at_utc, target_entity_id, revision),
        )
        if cursor.rowcount != 1:
            raise ProjectOperationError("revision_conflict", "CustomerProfile изменилась во время сохранения.")
        insert_audit(
            self._connection,
            event_type="customer_profile.updated",
            actor_kind="user",
            occurred_at_utc=occurred_at_utc,
            payload={
                "entityType": "customerProfile",
                "entityId": target_entity_id,
                "fromRevision": revision,
                "toRevision": next_revision,
                "changedFields": [target_field],
                "changes": {
                    target_field: {
                        "before": before,
                        "after": str(source_value),
                    },
                },
            },
        )

    def _source_value(
        self,
        local_import_id: str,
        source_payload_path: str,
        source_field: str,
    ) -> object:
        direct_columns = {
            ("run-summary.json", "run_card.customer_name"): "customer_full_name",
            ("run-summary.json", "run_card.customer_address"): "customer_address",
            ("run-summary.json", "run_card.wheel_full_name"): "wheel_full_name",
            ("run-summary.json", "run_card.wheel_identifier"): "wheel_identifier",
            ("run-summary.json", "run_card.working_diameter_mm"): "working_diameter_mm",
            ("run-summary.json", "specimen_id"): "source_specimen_id",
            ("run-summary.json", "sample_label"): "sample_label",
        }
        column = direct_columns.get((source_payload_path, source_field))
        if column is not None:
            row = self._connection.execute(
                f"SELECT {column} FROM r130sh_run_projections WHERE local_import_id=?",
                (local_import_id,),
            ).fetchone()
            if row is None or row[0] is None or str(row[0]) == "":
                raise ProjectOperationError("validation_error", "Source value отсутствует.")
            return row[0]
        if source_payload_path == "plan/original.json" and source_field == "source_values.nominal_rpm":
            row = self._connection.execute(
                "SELECT original_plan_summary_json FROM r130sh_run_projections WHERE local_import_id=?",
                (local_import_id,),
            ).fetchone()
            if row is None:
                raise ProjectOperationError("entity_not_found", "Импортированный запуск не найден.")
            plan = _json_object(str(row[0]))
            source_values = plan.get("source_values")
            if not isinstance(source_values, dict):
                raise ProjectOperationError("validation_error", "Source nominal RPM отсутствует.")
            typed_source_values = cast(dict[str, object], source_values)
            nominal_rpm = typed_source_values.get("nominal_rpm")
            if nominal_rpm is None:
                raise ProjectOperationError("validation_error", "Source nominal RPM отсутствует.")
            return nominal_rpm
        raise ProjectOperationError("validation_error", "Source field не поддерживается.")

    def _insert_source(
        self,
        local_import_id: str,
        relative_path: str,
        facts: M9aPackageFacts,
        report: RunPackageValidationReport,
        now: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO r130sh_sources (
                local_import_id, package_id, export_revision, outer_package_sha256,
                run_id, package_kind, package_schema, package_created_at_utc,
                source_snapshot_sha256, producer_name, producer_version,
                producer_build_id, producer_git_commit, managed_relative_path,
                outer_size_bytes, imported_at_utc, validator_version,
                validation_contract_commit, structural_verdict, semantic_verdict,
                semantic_coverage_json, validation_findings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                local_import_id,
                facts.package_id,
                facts.export_revision,
                facts.outer_package_sha256,
                facts.run_id,
                facts.package_kind,
                facts.package_schema,
                facts.package_created_at_utc,
                facts.source_snapshot_sha256,
                facts.producer_name,
                facts.producer_version,
                facts.producer_build_id,
                facts.producer_git_commit,
                relative_path,
                facts.outer_size_bytes,
                now,
                VALIDATOR_VERSION,
                UPSTREAM_COMMIT,
                report.structuralVerdict,
                report.semanticVerdict,
                canonical_json(
                    [item.model_dump(mode="json") for item in report.semanticCoverage],
                ),
                canonical_json([item.model_dump(mode="json") for item in report.findings]),
            ),
        )

    def _insert_projection(self, local_import_id: str, facts: M9aPackageFacts) -> None:
        value = facts.projection
        self._connection.execute(
            """
            INSERT INTO r130sh_run_projections (
                local_import_id, run_id, source_specimen_id, mode, package_kind,
                technical_status, termination_reason, specimen_outcome,
                run_validity, data_completeness, partial_reasons_json,
                resume_available, original_plan_id, original_plan_revision,
                original_plan_sha256, effective_plan_id, effective_plan_revision,
                effective_plan_sha256, original_plan_summary_json,
                effective_plan_summary_json, started_at_utc, finished_at_utc,
                customer_full_name, customer_address, customer_order_reference,
                wheel_full_name, wheel_identifier, working_diameter_mm,
                sample_label, environment_status, environment_summary_json,
                provenance_summary_json, measurement_count,
                accepted_measurement_count, event_count, inspection_count,
                attachment_count, amendment_count, crediting_policy,
                accepted_elapsed_s
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                local_import_id,
                value.run_id,
                value.source_specimen_id,
                value.mode,
                value.package_kind,
                value.technical_status,
                value.termination_reason,
                value.specimen_outcome,
                value.run_validity,
                value.data_completeness,
                canonical_json(list(value.partial_reasons)),
                int(value.resume_available),
                value.original_plan_id,
                value.original_plan_revision,
                value.original_plan_sha256,
                value.effective_plan_id,
                value.effective_plan_revision,
                value.effective_plan_sha256,
                canonical_json(value.original_plan_summary),
                canonical_json(value.effective_plan_summary),
                value.started_at_utc,
                value.finished_at_utc,
                value.customer_full_name,
                value.customer_address,
                value.customer_order_reference,
                value.wheel_full_name,
                value.wheel_identifier,
                value.working_diameter_mm,
                value.sample_label,
                value.environment_status,
                canonical_json(value.environment_summary),
                canonical_json(value.provenance_summary),
                value.measurement_count,
                value.accepted_measurement_count,
                value.event_count,
                value.inspection_count,
                value.attachment_count,
                value.amendment_count,
                value.crediting_policy,
                value.accepted_elapsed_s,
            ),
        )

    def _find_by_package_revision(
        self,
        package_id: str,
        export_revision: int,
    ) -> ImportedRunSummary | None:
        row = self._connection.execute(
            _SUMMARY_QUERY + " WHERE sources.package_id=? AND sources.export_revision=?",
            (package_id, export_revision),
        ).fetchone()
        return None if row is None else self._summary_from_row(row)

    def _summary_from_row(self, row: sqlite3.Row) -> ImportedRunSummary:
        local_import_id = str(row[0])
        current_integrity, current_signature = _cheap_integrity_evidence(
            self._project_path,
            str(row[12]),
            int(row[13]),
        )
        cached = self._integrity_cache.get(local_import_id)
        if cached is None or current_integrity != "verified":
            integrity = current_integrity
        elif cached[1] != current_signature:
            integrity = "modified"
        else:
            integrity = cached[0]
        return ImportedRunSummary(
            local_import_id=local_import_id,
            package_id=str(row[1]),
            export_revision=int(row[2]),
            outer_package_sha256=str(row[3]),
            run_id=str(row[4]),
            package_kind=str(row[5]),
            package_schema=str(row[6]),
            package_created_at_utc=str(row[7]),
            source_snapshot_sha256=str(row[8]),
            producer_name=str(row[9]),
            producer_version=str(row[10]),
            producer_build_id=str(row[11]),
            producer_git_commit=str(row[28]),
            outer_size_bytes=int(row[13]),
            imported_at_utc=str(row[14]),
            validator_version=str(row[15]),
            validation_contract_commit=str(row[16]),
            structural_verdict=str(row[17]),
            semantic_verdict=str(row[18]),
            source_integrity=integrity,
            source_specimen_id=str(row[19]),
            local_specimen_id=None if row[20] is None else str(row[20]),
            binding_revision=int(row[21]),
            mode=str(row[22]),
            technical_status=None if row[23] is None else str(row[23]),
            termination_reason=None if row[24] is None else str(row[24]),
            specimen_outcome=None if row[25] is None else str(row[25]),
            run_validity=None if row[26] is None else str(row[26]),
            data_completeness=None if row[27] is None else str(row[27]),
        )


_SUMMARY_QUERY: Final = """
SELECT sources.local_import_id, sources.package_id, sources.export_revision,
       sources.outer_package_sha256, sources.run_id, sources.package_kind,
       sources.package_schema, sources.package_created_at_utc,
       sources.source_snapshot_sha256, sources.producer_name,
       sources.producer_version, sources.producer_build_id,
       sources.managed_relative_path, sources.outer_size_bytes,
       sources.imported_at_utc, sources.validator_version,
       sources.validation_contract_commit, sources.structural_verdict,
       sources.semantic_verdict, projections.source_specimen_id,
       bindings.local_specimen_id, bindings.record_revision,
       projections.mode, projections.technical_status,
       projections.termination_reason, projections.specimen_outcome,
       projections.run_validity, projections.data_completeness,
       sources.producer_git_commit
FROM r130sh_sources AS sources
JOIN r130sh_run_projections AS projections
  ON projections.local_import_id = sources.local_import_id
JOIN r130sh_specimen_bindings AS bindings
  ON bindings.source_specimen_id = projections.source_specimen_id
"""


def validate_r130sh_source_evidence(
    connection: sqlite3.Connection,
    deadline: RequestDeadline | None = None,
) -> None:
    _check_deadline(deadline, "r130sh_evidence")
    source_rows = connection.execute(
        """
        SELECT local_import_id, package_id, export_revision, outer_package_sha256,
               run_id, package_kind, managed_relative_path, outer_size_bytes,
               imported_at_utc, semantic_coverage_json, validation_findings_json
        FROM r130sh_sources ORDER BY local_import_id
        """,
    )
    for row in source_rows:
        _check_deadline(deadline, "r130sh_evidence_source")
        _uuid4(str(row[0]))
        _source_uuid(str(row[1]))
        if int(row[2]) < 1 or not SHA256_RE.fullmatch(str(row[3])):
            raise _corrupt_source()
        _source_identity(str(row[4]))
        if str(row[5]) not in {"final", "diagnostic_partial"}:
            raise _corrupt_source()
        if FINAL_PATH_RE.fullmatch(str(row[6])) is None or int(row[7]) <= 0:
            raise _corrupt_source()
        require_canonical_utc_timestamp(str(row[8]))
        _json_array(str(row[9]))
        _json_array(str(row[10]))
        inventory_rows = connection.execute(
            """
            SELECT path, media_type, size_bytes, sha256, row_count, semantic_coverage
            FROM r130sh_source_inventory WHERE local_import_id=?
            """,
            (str(row[0]),),
        ).fetchall()
        for inventory_row in inventory_rows:
            _check_deadline(deadline, "r130sh_evidence_inventory")
            _inventory_path(str(inventory_row[0]))
            if (
                not str(inventory_row[1])
                or int(inventory_row[2]) < 0
                or SHA256_RE.fullmatch(str(inventory_row[3])) is None
                or (inventory_row[4] is not None and int(inventory_row[4]) < 0)
                or str(inventory_row[5]) not in {"covered", "structural_only"}
            ):
                raise _corrupt_source()
        projection_count = int(
            connection.execute(
                "SELECT count(*) FROM r130sh_run_projections WHERE local_import_id=?",
                (str(row[0]),),
            ).fetchone()[0],
        )
        resolution_count = int(
            connection.execute(
                "SELECT count(*) FROM r130sh_enrichment_resolutions WHERE local_import_id=?",
                (str(row[0]),),
            ).fetchone()[0],
        )
        if not inventory_rows or projection_count != 1 or resolution_count > 32:
            raise _corrupt_source()
    _check_deadline(deadline, "r130sh_evidence")


def _projection_payload(row: sqlite3.Row) -> dict[str, object]:
    scalar_names = (
        "local_import_id",
        "run_id",
        "source_specimen_id",
        "mode",
        "package_kind",
        "technical_status",
        "termination_reason",
        "specimen_outcome",
        "run_validity",
        "data_completeness",
        "resume_available",
        "original_plan_id",
        "original_plan_revision",
        "original_plan_sha256",
        "effective_plan_id",
        "effective_plan_revision",
        "effective_plan_sha256",
        "started_at_utc",
        "finished_at_utc",
        "customer_full_name",
        "customer_address",
        "customer_order_reference",
        "wheel_full_name",
        "wheel_identifier",
        "working_diameter_mm",
        "sample_label",
        "environment_status",
        "measurement_count",
        "accepted_measurement_count",
        "event_count",
        "inspection_count",
        "attachment_count",
        "amendment_count",
        "crediting_policy",
        "accepted_elapsed_s",
    )
    result = {name: row[name] for name in scalar_names}
    result["resume_available"] = bool(result["resume_available"])
    result["partial_reasons"] = _json_list(str(row["partial_reasons_json"]))
    result["original_plan_summary"] = _json_object(str(row["original_plan_summary_json"]))
    result["effective_plan_summary"] = _json_object(str(row["effective_plan_summary_json"]))
    result["environment_summary"] = _json_object(str(row["environment_summary_json"]))
    result["provenance_summary"] = _json_object(str(row["provenance_summary_json"]))
    return result


def _validate_resolution(
    source_payload_path: str,
    source_field: str,
    target_entity_type: str,
    target_entity_id: str,
    target_field: str,
    decision: str,
    actor: str,
    reason: str,
) -> tuple[str, str, str, str, str, str, str, str]:
    relationships = {
        ("run-summary.json", "run_card.customer_name", "customer_profile", "fullName"),
        ("run-summary.json", "run_card.customer_address", "customer_profile", "legalAddress"),
        ("run-summary.json", "run_card.customer_address", "customer_profile", "actualAddress"),
        ("run-summary.json", "run_card.wheel_full_name", "wheel_model", "fullName"),
        ("run-summary.json", "run_card.wheel_identifier", "wheel_model", "designation"),
        ("plan/original.json", "source_values.nominal_rpm", "wheel_model", "nominalSpeedRpm"),
        ("run-summary.json", "run_card.working_diameter_mm", "specimen", "workingDiameterMm"),
        ("run-summary.json", "specimen_id", "specimen", "identificationNumber"),
        ("run-summary.json", "sample_label", "specimen", "marking"),
    }
    relationship = (
        _required_text(source_payload_path, 512, "sourcePayloadPath"),
        _required_text(source_field, 200, "sourceField"),
        _required_text(target_entity_type, 64, "targetEntityType"),
        _required_text(target_field, 100, "targetField"),
    )
    if relationship not in relationships:
        raise ProjectOperationError(
            "validation_error",
            "Source/enrichment relationship не входит в M03B whitelist.",
        )
    target_id = _required_text(target_entity_id, 200, "targetEntityId")
    normalized_decision = _required_text(decision, 64, "decision")
    if normalized_decision not in {"use_source", "use_analyst", "copied_to_analyst"}:
        raise ProjectOperationError("validation_error", "Решение source/enrichment не поддерживается.")
    normalized_actor = _required_text(actor, 200, "actor")
    normalized_reason = reason.strip()
    if normalized_decision in {"use_source", "use_analyst"} and not normalized_reason:
        raise ProjectOperationError("validation_error", "Для конфликтного выбора требуется причина.")
    if len(normalized_reason) > 2000:
        raise ProjectOperationError("validation_error", "Причина слишком длинная.")
    return (
        relationship[0],
        relationship[1],
        relationship[2],
        target_id,
        relationship[3],
        normalized_decision,
        normalized_actor,
        normalized_reason,
    )


def _managed_relative_path(facts: M9aPackageFacts) -> str:
    value = f"imports/r130sh/{facts.package_id}/rev-{facts.export_revision}/{facts.outer_package_sha256}.r130run"
    if FINAL_PATH_RE.fullmatch(value) is None:
        raise ProjectOperationError("validation_error", "Managed archive path неканоничен.")
    return value


def _inventory_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or len(value.encode("utf-8")) > 512
        or "\\" in value
        or ":" in value
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise _corrupt_source()
    return value


def _managed_path(project_path: Path, relative: str) -> Path:
    if FINAL_PATH_RE.fullmatch(relative) is None:
        raise ValueError("managed_path_invalid")
    candidate = project_path.joinpath(*PurePosixPath(relative).parts)
    if project_path.resolve() not in candidate.resolve(strict=False).parents:
        raise ValueError("managed_path_escape")
    return candidate


def _cheap_integrity_evidence(
    project_path: Path,
    relative: str,
    expected_size: int,
) -> tuple[SourceIntegrityStatus, tuple[int, int, int, int] | None]:
    try:
        path = _managed_path(project_path, relative)
        if not path.exists():
            return "missing", None
        if not _ordinary_file(path):
            return "modified", None
        signature = _file_signature(path)
        if signature[0] != expected_size:
            return "modified", signature
        return "verified", signature
    except OSError, ValueError:
        return "verification_error", None


def _file_signature(path: Path) -> tuple[int, int, int, int]:
    value = path.stat()
    return value.st_size, value.st_mtime_ns, value.st_dev, value.st_ino


def _ordinary_file(path: Path) -> bool:
    try:
        value = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(value.st_mode) and not path.is_symlink() and not path.is_junction() and value.st_file_attributes & WINDOWS_REPARSE_POINT_ATTRIBUTE == 0


def _sha256_file(path: Path, deadline: RequestDeadline | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(STREAM_CHUNK_BYTES), b""):
            _check_deadline(deadline, "r130sh_verify_hash")
            digest.update(chunk)
    return digest.hexdigest()


def _remove_operation_file(path: Path) -> None:
    try:
        if _ordinary_file(path):
            path.unlink()
    except OSError:
        pass


def _uuid4(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ProjectOperationError("validation_error", "Local ID должен быть UUID v4.") from error
    if str(parsed) != value or parsed.variant != RFC_4122 or parsed.version != 4:
        raise ProjectOperationError("validation_error", "Local ID должен быть canonical UUID v4.")
    return value


def _source_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise _corrupt_source() from error
    if str(parsed) != value or parsed.variant != RFC_4122 or parsed.version not in {4, 7}:
        raise _corrupt_source()
    return value


def _source_identity(value: str) -> str:
    if not value or len(value.encode("utf-8")) > 200 or any(ord(char) < 32 for char in value):
        raise _corrupt_source()
    return value


def _required_text(value: str, limit: int, field: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise ProjectOperationError("validation_error", f"Поле {field} заполнено некорректно.")
    return normalized


def _json_array(value: str) -> list[dict[str, object]]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise _corrupt_source() from error
    if not isinstance(parsed, list):
        raise _corrupt_source()
    result: list[dict[str, object]] = []
    for item in cast(list[object], parsed):
        if not isinstance(item, dict):
            raise _corrupt_source()
        result.append(cast(dict[str, object], item))
    return result


def _json_list(value: str) -> list[object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise _corrupt_source() from error
    if not isinstance(parsed, list):
        raise _corrupt_source()
    return cast(list[object], parsed)


def _json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise _corrupt_source() from error
    if not isinstance(parsed, dict):
        raise _corrupt_source()
    return cast(dict[str, object], parsed)


def _corrupt_source() -> ProjectOperationError:
    return ProjectOperationError("corrupt_project", "R130SH source evidence повреждён.")


def _check_deadline(deadline: RequestDeadline | None, stage: str) -> None:
    if deadline is not None:
        deadline.check(stage)


def _with_existing(value: ImportedRunSummary) -> ImportedRunSummary:
    return replace(value, imported_existing=True)


def _created_summary(
    local_import_id: str,
    facts: M9aPackageFacts,
    imported_at_utc: str,
    local_specimen_id: str | None,
    binding_revision: int,
    semantic_verdict: str,
) -> ImportedRunSummary:
    projection = facts.projection
    return ImportedRunSummary(
        local_import_id=local_import_id,
        package_id=facts.package_id,
        export_revision=facts.export_revision,
        outer_package_sha256=facts.outer_package_sha256,
        run_id=facts.run_id,
        package_kind=facts.package_kind,
        package_schema=facts.package_schema,
        package_created_at_utc=facts.package_created_at_utc,
        source_snapshot_sha256=facts.source_snapshot_sha256,
        producer_name=facts.producer_name,
        producer_version=facts.producer_version,
        producer_build_id=facts.producer_build_id,
        producer_git_commit=facts.producer_git_commit,
        outer_size_bytes=facts.outer_size_bytes,
        imported_at_utc=imported_at_utc,
        validator_version=VALIDATOR_VERSION,
        validation_contract_commit=UPSTREAM_COMMIT,
        structural_verdict="passed",
        semantic_verdict=semantic_verdict,
        source_integrity="verified",
        source_specimen_id=projection.source_specimen_id,
        local_specimen_id=local_specimen_id,
        binding_revision=binding_revision,
        mode=projection.mode,
        technical_status=projection.technical_status,
        termination_reason=projection.termination_reason,
        specimen_outcome=projection.specimen_outcome,
        run_validity=projection.run_validity,
        data_completeness=projection.data_completeness,
    )


def _ignore_validation_progress(
    _phase: str,
    _completed_bytes: int,
    _total_bytes: int,
    _completed_entries: int,
    _total_entries: int,
) -> None:
    return None


__all__ = [
    "ImportedRunDetail",
    "ImportedRunSummary",
    "R130shSourceRepository",
    "SourceIntegrityStatus",
    "SpecimenBinding",
    "validate_r130sh_source_evidence",
]
