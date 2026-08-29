from __future__ import annotations

import codecs
from collections.abc import Callable, Iterator
import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
import hashlib
import io
from itertools import pairwise
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
import struct
from threading import Event
from time import monotonic
from typing import Literal
import unicodedata
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo
import zlib

from pydantic import TypeAdapter, ValidationError

from impeller_reliability.integration.r130run.models import (
    CONTRACT_SCHEMA,
    FindingSeverity,
    JobPhase,
    RunPackageFinding,
    RunPackageFindingCounts,
    RunPackageProducer,
    RunPackageSemanticCoverage,
    RunPackageValidationReport,
    SemanticVerdict,
    StructuralVerdict,
)

GIB = 1024 * 1024 * 1024
MIB = 1024 * 1024
STREAM_CHUNK_BYTES = MIB
MAX_OUTER_BYTES = 8 * GIB
MAX_CENTRAL_DIRECTORY_BYTES = 16 * MIB
MAX_ENTRY_COUNT = 4_096
MAX_PATH_BYTES = 512
MAX_ENTRY_METADATA_BYTES = 8 * 1024
MAX_MANIFEST_BYTES = 2 * MIB
MAX_JSON_BYTES = 16 * MIB
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 250_000
MAX_JSONL_LINE_BYTES = MIB
MAX_CSV_RECORD_CHARS = 8 * MIB
MAX_CSV_FIELD_CHARS = MIB
MAX_DECOMPRESSED_BYTES = 32 * GIB
MAX_FINDINGS = 200
MAX_REPORT_BYTES = 900 * 1024
MAX_SAFE_INTEGER = 9_007_199_254_740_991
csv.field_size_limit(MAX_CSV_FIELD_CHARS)
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
ALLOWED_ZIP_FLAGS = 0x0800 | 0x0008
SHA256_PATTERN_LENGTH = 64
CORE_PATHS = frozenset(
    {
        "plan/original.json",
        "plan/effective.json",
        "plan/amendments.jsonl",
        "run-summary.json",
        "environment.json",
        "provenance.json",
        "events.jsonl",
        "measurements.csv",
        "measurement-descriptors.json",
        "accepted-summary.json",
        "vibration-baseline.json",
        "inspections.json",
        "attachments/index.json",
    }
)
SEMANTIC_JSON_PATHS = frozenset(
    {
        "plan/original.json",
        "plan/effective.json",
        "provenance.json",
        "accepted-summary.json",
        "inspections.json",
    }
)
ProgressCallback = Callable[[JobPhase, int, int, int, int], None]
type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
type FrozenShape = type[object] | tuple[type[object], ...] | dict[str, FrozenShape] | list[FrozenShape]
JsonPairs = list[tuple[str, object]]
JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
JSON_OBJECT_ADAPTER: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])
PLAN_SHAPE: FrozenShape = {
    "schema_version": str,
    "run_id": str,
    "plan_id": str,
    "plan_revision": int,
    "specimen_id": str,
    "wheel_identifier": str,
    "original_plan_sha256": str,
    "effective_plan_sha256": str,
    "mode": str,
    "laboratory_case_reference": str,
    "customer_order_reference": str,
    "source_values": {
        "base_cycles": str,
        "reserve_factor": str,
        "nominal_rpm": str,
        "acceleration_duration_s": str,
        "deceleration_duration_s": str,
    },
    "methodical_requirements": {
        "required_cycles_exact": str,
        "required_steady_duration_s_exact": str,
    },
    "execution_targets": {
        "target_cycles": int,
        "target_steady_duration_s": str,
        "rounding_policy": str,
    },
}
EVENT_SHAPE: FrozenShape = {
    "schema_version": str,
    "event_id": str,
    "run_id": str,
    "run_sequence": int,
    "event_type": str,
    "occurred_at_utc": str,
    "clock_epoch_id": str,
    "epoch_monotonic_elapsed_s": str,
    "run_elapsed_s": str,
    "source": str,
    "actor_json": (str, dict, type(None)),
    "correlation_id": str,
    "idempotency_key": str,
    "payload_json": dict,
    "payload_sha256": str,
}
INSPECTION_SHAPE: FrozenShape = {
    "schema_version": str,
    "inspection_id": str,
    "run_id": str,
    "stage": str,
    "trip_index": int,
    "performed_at_utc": str,
    "run_elapsed_s": str,
    "actor": {"employee_id": str, "display_name": str},
    "findings": {
        "cracks": bool,
        "chips": bool,
        "deformation": bool,
        "partial_destruction": bool,
        "total_destruction": bool,
        "balancing_elements_state": str,
        "other_findings": str,
    },
    "inspection_outcome": str,
    "comment": str,
    "attachment_ids": [str],
}
PROVENANCE_SHAPE: FrozenShape = {
    "schema_version": str,
    "run_id": str,
    "producer": {"name": str, "version": str, "build_id": str, "git_commit": str},
    "database_schema_version": int,
    "stand": {
        "name": str,
        "serial_number": str,
        "stand_spec_sha256": str,
        "register_map_sha256": str,
        "direction_binding_sha256": str,
    },
    "time_source": {"wall_clock": str, "duration_clock": str},
    "measurement_systems": [
        {
            "instrument_id": str,
            "name": str,
            "model": str,
            "serial_number": str,
            "firmware_version": str,
            "measurement_role": str,
            "verification_certificate": str,
            "verification_valid_from": str,
            "verification_valid_until": str,
            "settings_snapshot": dict,
        }
    ],
}
ACCEPTED_SHAPE: FrozenShape = {
    "schema_version": str,
    "run_id": str,
    "mode": str,
    "crediting_policy": str,
    "points": [{"measurement_id": str, "accepted_elapsed_s": str}],
}


class ValidationCancelledError(Exception):
    pass


class ValidationTimeoutError(Exception):
    pass


class SourceChangedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ValidationControl:
    cancel_event: Event
    expires_at: float
    progress: ProgressCallback
    clock: Callable[[], float] = monotonic

    def check(self, phase: JobPhase) -> None:
        if self.cancel_event.is_set():
            raise ValidationCancelledError
        if self.clock() >= self.expires_at:
            raise ValidationTimeoutError


@dataclass(frozen=True, slots=True)
class _SourceSignature:
    size: int
    mtime_ns: int
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    resolved_path: str
    signature: _SourceSignature


@dataclass(frozen=True, slots=True)
class _ManifestFile:
    path: str
    media_type: str
    size: int
    sha256: str
    row_count: int | None


@dataclass(frozen=True, slots=True)
class _Manifest:
    package_id: str
    export_revision: int
    run_id: str
    package_kind: Literal["final", "diagnostic_partial"]
    producer: RunPackageProducer
    files: tuple[_ManifestFile, ...]


@dataclass(frozen=True, slots=True)
class _ValidatedEntry:
    info: ZipInfo
    data_start: int


class _PackageFindingError(Exception):
    def __init__(self, finding: RunPackageFinding) -> None:
        super().__init__(finding.code)
        self.finding = finding


@dataclass(slots=True)
class _FindingAccumulator:
    findings: list[RunPackageFinding]
    error: int = 0
    warning: int = 0
    info: int = 0
    total: int = 0

    @classmethod
    def empty(cls) -> _FindingAccumulator:
        return cls([])

    def add(self, finding: RunPackageFinding) -> None:
        self.total += 1
        if finding.severity == "error":
            self.error += 1
        elif finding.severity == "warning":
            self.warning += 1
        else:
            self.info += 1
        if len(self.findings) < MAX_FINDINGS:
            self.findings.append(finding)

    def counts(self) -> RunPackageFindingCounts:
        return RunPackageFindingCounts(
            error=self.error,
            warning=self.warning,
            info=self.info,
            total=self.total,
            truncated=self.total > len(self.findings),
        )


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def inspect_source(path: Path) -> SourceFingerprint:
    try:
        source_stat = path.lstat()
    except OSError as error:
        raise OSError("run_package_source_unavailable") from error
    if not stat.S_ISREG(source_stat.st_mode) or _is_reparse(source_stat):
        raise OSError("run_package_source_not_regular")
    if source_stat.st_size <= 0 or source_stat.st_size > MAX_OUTER_BYTES:
        raise OSError("run_package_source_size")
    return SourceFingerprint(str(path.resolve(strict=True)), _signature(source_stat))


class RunPackageValidator:
    def validate(
        self,
        source_path: Path,
        control: ValidationControl,
        expected_fingerprint: SourceFingerprint | None = None,
    ) -> RunPackageValidationReport:
        started_at = utc_now()
        fingerprint = inspect_source(source_path)
        if expected_fingerprint is not None and fingerprint != expected_fingerprint:
            raise SourceChangedError
        source_file_name = _bounded_file_name(source_path.name)
        control.progress("source_check", 0, fingerprint.signature.size, 0, 0)
        control.check("source_check")
        with source_path.open("rb", buffering=0) as source:
            initial = _signature(os.fstat(source.fileno()))
            if initial != fingerprint.signature:
                raise SourceChangedError
            outer_sha256 = self._outer_hash(source, initial.size, control)
            source.seek(0)
            try:
                report = self._validate_zip(
                    source,
                    source_file_name=source_file_name,
                    outer_sha256=outer_sha256,
                    outer_size=initial.size,
                    started_at=started_at,
                    control=control,
                )
            except _PackageFindingError as error:
                report = _failed_report(
                    source_file_name,
                    outer_sha256,
                    initial.size,
                    started_at,
                    error.finding,
                )
            except BadZipFile, EOFError, RecursionError, RuntimeError, struct.error, zlib.error:
                report = _failed_report(
                    source_file_name,
                    outer_sha256,
                    initial.size,
                    started_at,
                    _finding("archive_malformed", "error", "archive", "ZIP package имеет повреждённую структуру.", "zip-envelope"),
                )
            final = _signature(os.fstat(source.fileno()))
        try:
            final_path = _signature(source_path.lstat())
        except OSError as error:
            raise SourceChangedError from error
        if final != initial or final_path != initial:
            raise SourceChangedError
        return _bounded_report(report)

    @staticmethod
    def _outer_hash(source: io.RawIOBase, total_bytes: int, control: ValidationControl) -> str:
        control.progress("outer_hash", 0, total_bytes, 0, 0)
        digest = hashlib.sha256()
        completed = 0
        while True:
            control.check("outer_hash")
            chunk = source.read(STREAM_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            completed += len(chunk)
            if completed > total_bytes:
                raise SourceChangedError
            control.progress("outer_hash", completed, total_bytes, 0, 0)
        return digest.hexdigest()

    def _validate_zip(
        self,
        source: io.RawIOBase,
        *,
        source_file_name: str,
        outer_sha256: str,
        outer_size: int,
        started_at: str,
        control: ValidationControl,
    ) -> RunPackageValidationReport:
        control.check("zip_index")
        central_offset, entry_count = _preflight_central_directory(source, outer_size)
        source.seek(0)
        try:
            archive = ZipFile(source, mode="r")
        except (BadZipFile, OSError, UnicodeDecodeError) as error:
            raise _invalid("archive_not_zip", "archive", "Пакет не является корректным ZIP.", "zip-envelope") from error
        with archive:
            infos = archive.infolist()
            if len(infos) != entry_count or not infos or len(infos) > MAX_ENTRY_COUNT:
                raise _invalid("entry_count_invalid", "archive", "Количество записей ZIP некорректно.", "zip-envelope")
            entries_by_name = _validate_zip_entries(source, infos, central_offset)
            control.progress("zip_index", outer_size, outer_size, 0, len(infos))
            manifest_entry = entries_by_name.get("manifest.json")
            checksum_entry = entries_by_name.get("checksums.sha256")
            if manifest_entry is None:
                raise _invalid("manifest_missing", "manifest.json", "Manifest пакета отсутствует.", "manifest-example")
            if checksum_entry is None:
                raise _invalid("checksum_index_missing", "checksums.sha256", "Индекс контрольных сумм отсутствует.", "r130run-v1-target")
            if manifest_entry.info.file_size > MAX_MANIFEST_BYTES:
                raise _invalid("manifest_too_large", "manifest.json", "Manifest превышает технический предел.", "m03a-safety-profile")
            control.check("manifest")
            control.progress("manifest", 0, manifest_entry.info.file_size, 0, len(infos))
            manifest_bytes = _read_bounded_entry(source, manifest_entry, MAX_MANIFEST_BYTES, control, "manifest")
            manifest = _parse_manifest(manifest_bytes)
            declared = {item.path: item for item in manifest.files}
            actual_names = set(entries_by_name)
            expected_names = set(declared) | {"manifest.json", "checksums.sha256"}
            if actual_names != expected_names:
                code = "undeclared_entry" if actual_names - expected_names else "declared_entry_missing"
                raise _invalid(code, "manifest.files", "Состав ZIP не совпадает с inventory manifest.", "manifest-example")
            if checksum_entry.info.file_size > MAX_MANIFEST_BYTES:
                raise _invalid("checksum_index_too_large", "checksums.sha256", "Индекс контрольных сумм превышает технический предел.", "m03a-safety-profile")
            _read_bounded_entry(source, checksum_entry, MAX_MANIFEST_BYTES, control, "manifest")

            semantic_findings = _FindingAccumulator.empty()
            validated_bytes = 0
            parsed_json: dict[str, JsonValue] = {}
            for index, item in enumerate(manifest.files, start=1):
                control.check("payload_integrity")
                entry = entries_by_name[item.path]
                payload, row_count = _read_payload(
                    source,
                    entry,
                    item,
                    control,
                    manifest.run_id,
                    semantic_findings,
                )
                validated_bytes += payload.size
                if payload.size != item.size:
                    raise _invalid("entry_size_mismatch", item.path, "Размер payload не совпадает с manifest.", "manifest-inventory")
                if payload.sha256 != item.sha256:
                    raise _invalid("entry_hash_mismatch", item.path, "SHA-256 payload не совпадает с manifest.", "manifest-inventory")
                if item.row_count is not None and item.path.endswith(".jsonl") and row_count != item.row_count:
                    raise _invalid("row_count_mismatch", item.path, "Количество JSONL записей не совпадает с manifest.", "manifest-inventory")
                if payload.json_value is not None and item.path in SEMANTIC_JSON_PATHS:
                    parsed_json[item.path] = payload.json_value
                control.progress("payload_integrity", validated_bytes, sum(value.size for value in manifest.files), index, len(manifest.files))

            control.check("semantic_validation")
            control.progress("semantic_validation", validated_bytes, validated_bytes, len(manifest.files), len(manifest.files))
            _semantic_validate(manifest, parsed_json, semantic_findings, control)
            coverage = _semantic_coverage()
            semantic_verdict: SemanticVerdict = "failed" if semantic_findings.error > 0 else "partial"
            if manifest.package_kind == "final" and not CORE_PATHS.issubset(declared):
                semantic_findings.add(_finding("final_core_missing", "error", "manifest.files", "Final package не содержит полный frozen core.", "r130run-v1-target"))
                semantic_verdict = "failed"
            control.check("finalizing")
            control.progress("finalizing", validated_bytes, validated_bytes, len(manifest.files), len(manifest.files))
            return _report(
                source_file_name=source_file_name,
                outer_sha256=outer_sha256,
                outer_size=outer_size,
                manifest=manifest,
                entry_count=len(infos),
                validated_bytes=validated_bytes,
                structural_verdict="passed",
                semantic_verdict=semantic_verdict,
                coverage=coverage,
                findings=semantic_findings,
                started_at=started_at,
            )


@dataclass(frozen=True, slots=True)
class _PayloadRead:
    sha256: str
    size: int
    json_value: JsonValue | None


def _read_payload(
    source: io.RawIOBase,
    entry: _ValidatedEntry,
    item: _ManifestFile,
    control: ValidationControl,
    run_id: str,
    semantic_findings: _FindingAccumulator,
) -> tuple[_PayloadRead, int]:
    digest = hashlib.sha256()
    completed = 0
    collected = bytearray()
    is_jsonl = item.path.endswith(".jsonl")
    pending = bytearray()
    row_count = 0
    csv_validator = _CsvStreamValidator(item.path) if item.path.endswith(".csv") else None
    try:
        for chunk in _iter_entry_chunks(source, entry, control, "payload_integrity"):
            digest.update(chunk)
            completed += len(chunk)
            if completed > MAX_DECOMPRESSED_BYTES:
                raise _invalid("payload_limit_exceeded", item.path, "Payload превышает технический предел.", "m03a-safety-profile")
            if item.path.endswith(".json"):
                if completed > MAX_JSON_BYTES:
                    raise _invalid("json_too_large", item.path, "JSON превышает технический предел.", "m03a-safety-profile")
                collected.extend(chunk)
            elif is_jsonl:
                pending.extend(chunk)
                consumed = 0
                while True:
                    control.check("payload_integrity")
                    newline = pending.find(b"\n", consumed)
                    if newline < 0:
                        break
                    line = bytes(pending[consumed:newline])
                    consumed = newline + 1
                    if len(line) > MAX_JSONL_LINE_BYTES:
                        raise _invalid("jsonl_line_too_large", item.path, "JSONL запись превышает технический предел.", "m03a-safety-profile")
                    if line == b"":
                        raise _invalid("jsonl_blank_line", item.path, "JSONL содержит пустую запись.", "m03a-safety-profile")
                    _semantic_validate_jsonl_item(item.path, _parse_json(line, item.path), run_id, semantic_findings)
                    row_count += 1
                if consumed:
                    del pending[:consumed]
                if len(pending) > MAX_JSONL_LINE_BYTES:
                    raise _invalid("jsonl_line_too_large", item.path, "JSONL запись превышает технический предел.", "m03a-safety-profile")
            elif csv_validator is not None:
                csv_validator.feed(chunk, control)
    except (BadZipFile, OSError, RuntimeError) as error:
        if isinstance(error, _PackageFindingError):
            raise
        raise _invalid("payload_read_error", item.path, "Payload ZIP не удалось прочитать полностью.", "zip-envelope") from error
    if completed != entry.info.file_size:
        raise _invalid("entry_size_mismatch", item.path, "Фактический размер payload не совпадает с ZIP index.", "zip-envelope")
    if is_jsonl and pending:
        if len(pending) > MAX_JSONL_LINE_BYTES:
            raise _invalid("jsonl_line_too_large", item.path, "JSONL запись превышает технический предел.", "m03a-safety-profile")
        _semantic_validate_jsonl_item(item.path, _parse_json(bytes(pending), item.path), run_id, semantic_findings)
        row_count += 1
    json_value = _parse_json(bytes(collected), item.path) if item.path.endswith(".json") else None
    if csv_validator is not None:
        row_count = csv_validator.finish(control)
    return _PayloadRead(digest.hexdigest(), completed, json_value), row_count


class _CsvStreamValidator:
    def __init__(self, location: str) -> None:
        self._location = location
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        self._pending = ""
        self._rows = 0

    def feed(self, content: bytes, control: ValidationControl) -> None:
        try:
            self._pending += self._decoder.decode(content)
        except UnicodeDecodeError as error:
            raise _invalid("csv_invalid", self._location, "CSV не прошёл bounded UTF-8 syntax profile.", "m03a-safety-profile") from error
        consumed = 0
        while True:
            control.check("payload_integrity")
            newline = self._pending.find("\n", consumed)
            if newline < 0:
                break
            record = self._pending[consumed:newline]
            consumed = newline + 1
            self._consume_record(record.removesuffix("\r"))
        if consumed:
            self._pending = self._pending[consumed:]
        if len(self._pending) > MAX_CSV_RECORD_CHARS:
            raise _invalid("csv_record_too_large", self._location, "CSV запись превышает технический предел.", "m03a-safety-profile")

    def finish(self, control: ValidationControl) -> int:
        control.check("payload_integrity")
        try:
            self._pending += self._decoder.decode(b"", final=True)
        except UnicodeDecodeError as error:
            raise _invalid("csv_invalid", self._location, "CSV не прошёл bounded UTF-8 syntax profile.", "m03a-safety-profile") from error
        if self._pending:
            self._consume_record(self._pending.removesuffix("\r"))
        return max(0, self._rows - 1)

    def _consume_record(self, record: str) -> None:
        if len(record) > MAX_CSV_RECORD_CHARS:
            raise _invalid("csv_record_too_large", self._location, "CSV запись превышает технический предел.", "m03a-safety-profile")
        try:
            rows = list(csv.reader([record], dialect="excel", strict=True))
        except csv.Error as error:
            raise _invalid("csv_invalid", self._location, "CSV не прошёл bounded UTF-8 syntax profile.", "m03a-safety-profile") from error
        if len(rows) != 1 or any(len(field) > MAX_CSV_FIELD_CHARS for field in rows[0]):
            raise _invalid("csv_record_too_large", self._location, "CSV запись превышает технический предел.", "m03a-safety-profile")
        self._rows += 1


def _preflight_central_directory(source: io.RawIOBase, size: int) -> tuple[int, int]:
    tail_size = min(size, 65_557)
    source.seek(size - tail_size)
    tail = source.read(tail_size)
    position = tail.rfind(b"PK\x05\x06")
    if position < 0 or position + 22 > len(tail):
        raise _invalid("archive_not_zip", "archive", "ZIP EOCD отсутствует.", "zip-envelope")
    eocd_offset = size - tail_size + position
    signature, disk, central_disk, disk_entries, total_entries, central_size, central_offset, comment_size = struct.unpack("<4s4H2LH", tail[position : position + 22])
    if signature != b"PK\x05\x06" or eocd_offset + 22 + comment_size != size or disk != 0 or central_disk != 0:
        raise _invalid("archive_malformed", "archive", "ZIP EOCD имеет недопустимую структуру.", "zip-envelope")
    if disk_entries == 0xFFFF or total_entries == 0xFFFF or central_size == 0xFFFFFFFF or central_offset == 0xFFFFFFFF:
        locator_offset = eocd_offset - 20
        if locator_offset < 0:
            raise _invalid("archive_zip64_invalid", "archive", "ZIP64 locator отсутствует.", "zip-envelope")
        source.seek(locator_offset)
        locator = source.read(20)
        if len(locator) != 20:
            raise _invalid("archive_zip64_invalid", "archive", "ZIP64 locator неполон.", "zip-envelope")
        locator_signature, locator_disk, zip64_offset, disk_count = struct.unpack("<4sLQL", locator)
        if locator_signature != b"PK\x06\x07" or locator_disk != 0 or disk_count != 1:
            raise _invalid("archive_multidisk", "archive", "Multidisk ZIP не поддерживается.", "zip-envelope")
        source.seek(zip64_offset)
        header = source.read(56)
        if len(header) != 56:
            raise _invalid("archive_zip64_invalid", "archive", "ZIP64 EOCD неполон.", "zip-envelope")
        values = struct.unpack("<4sQ2H2L4Q", header)
        if values[0] != b"PK\x06\x06" or values[4] != 0 or values[5] != 0 or values[6] != values[7]:
            raise _invalid("archive_zip64_invalid", "archive", "ZIP64 EOCD имеет недопустимую структуру.", "zip-envelope")
        total_entries = values[7]
        central_size = values[8]
        central_offset = values[9]
        zip64_record_end = zip64_offset + 12 + values[1]
        if zip64_record_end != locator_offset or locator_offset + 20 != eocd_offset:
            raise _invalid("archive_zip64_invalid", "archive", "ZIP64 records имеют недопустимые границы.", "zip-envelope")
        expected_central_end = zip64_offset
    elif disk_entries != total_entries:
        raise _invalid("archive_multidisk", "archive", "Multidisk ZIP не поддерживается.", "zip-envelope")
    else:
        expected_central_end = eocd_offset
    if total_entries <= 0 or total_entries > MAX_ENTRY_COUNT or central_size <= 0 or central_size > MAX_CENTRAL_DIRECTORY_BYTES:
        raise _invalid("central_directory_limit", "archive", "ZIP index превышает технический предел.", "m03a-safety-profile")
    if central_offset <= 0 or central_offset + central_size != expected_central_end:
        raise _invalid("archive_malformed", "archive", "ZIP index выходит за границы package.", "zip-envelope")
    return int(central_offset), int(total_entries)


def _validate_zip_entries(source: io.RawIOBase, infos: list[ZipInfo], central_offset: int) -> dict[str, _ValidatedEntry]:
    names: dict[str, _ValidatedEntry] = {}
    collision_keys: set[str] = set()
    ranges: list[tuple[int, int]] = []
    cumulative = 0
    for info in infos:
        name = _validate_package_path(info.filename, info.flag_bits)
        if name in names:
            raise _invalid("path_duplicate", name, "ZIP содержит повторяющееся имя.", "zip-envelope")
        collision_key = unicodedata.normalize("NFC", name).casefold()
        if collision_key in collision_keys:
            raise _invalid("path_case_collision", name, "ZIP содержит Unicode/case collision.", "zip-envelope")
        collision_keys.add(collision_key)
        if info.is_dir() or bool(info.external_attr & 0x10):
            raise _invalid("directory_entry", name, "Explicit directory entry не входит в package profile.", "m03a-safety-profile")
        if info.flag_bits & ~ALLOWED_ZIP_FLAGS:
            code = "entry_encrypted" if info.flag_bits & 0x1 else "entry_flags"
            raise _invalid(code, name, "ZIP entry содержит неподдерживаемые flags.", "m03a-safety-profile")
        if info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
            raise _invalid("unsupported_compression", name, "Метод сжатия ZIP не поддерживается.", "m03a-safety-profile")
        if len(info.extra) > MAX_ENTRY_METADATA_BYTES or len(info.comment) > MAX_ENTRY_METADATA_BYTES:
            raise _invalid("entry_metadata_limit", name, "Metadata ZIP entry превышает технический предел.", "m03a-safety-profile")
        file_type = stat.S_IFMT(info.external_attr >> 16)
        if file_type not in {0, stat.S_IFREG}:
            code = "entry_symlink" if file_type == stat.S_IFLNK else "entry_special_file"
            raise _invalid(code, name, "ZIP entry не является обычным файлом.", "m03a-safety-profile")
        if info.file_size < 0 or info.compress_size < 0:
            raise _invalid("entry_size_invalid", name, "ZIP entry имеет недопустимый размер.", "zip-envelope")
        cumulative += info.file_size
        if cumulative > MAX_DECOMPRESSED_BYTES:
            raise _invalid("expanded_size_limit", "archive", "Распакованный объём превышает технический предел.", "m03a-safety-profile")
        start, end, data_start = _local_entry_range(source, info)
        if start < 0 or end > central_offset:
            raise _invalid("entry_range_invalid", name, "Диапазон ZIP entry некорректен.", "zip-envelope")
        ranges.append((start, end))
        names[name] = _ValidatedEntry(info, data_start)
    ranges.sort()
    if ranges[0][0] != 0:
        raise _invalid("archive_leading_data", "archive", "Перед первым ZIP entry обнаружены лишние bytes.", "m03a-safety-profile")
    for previous, current in pairwise(ranges):
        if previous[1] != current[0]:
            code = "entry_overlap" if previous[1] > current[0] else "archive_unaccounted_data"
            raise _invalid(code, "archive", "ZIP содержит пересечение или неучтённые bytes между entries.", "zip-envelope")
    if ranges[-1][1] != central_offset:
        raise _invalid("archive_unaccounted_data", "archive", "Перед ZIP index обнаружены неучтённые bytes.", "zip-envelope")
    return names


def _local_entry_range(source: io.RawIOBase, info: ZipInfo) -> tuple[int, int, int]:
    source.seek(info.header_offset)
    header = source.read(30)
    if len(header) != 30:
        raise _invalid("local_header_invalid", info.filename, "Local ZIP header неполон.", "zip-envelope")
    signature, _version, flags, method, _time, _date, local_crc, local_compressed, local_size, name_length, extra_length = struct.unpack("<4s5H3L2H", header)
    if signature != b"PK\x03\x04" or flags != info.flag_bits or method != info.compress_type:
        raise _invalid("local_header_mismatch", info.filename, "Local и central ZIP headers не совпадают.", "zip-envelope")
    raw_name = source.read(name_length)
    local_extra = source.read(extra_length)
    if len(raw_name) != name_length or len(local_extra) != extra_length:
        raise _invalid("local_header_invalid", info.filename, "Local ZIP metadata неполна.", "zip-envelope")
    if extra_length > MAX_ENTRY_METADATA_BYTES:
        raise _invalid("entry_metadata_limit", info.filename, "Local metadata ZIP entry превышает технический предел.", "m03a-safety-profile")
    if b"\\" in raw_name:
        raise _invalid("path_noncanonical", info.filename, "ZIP path содержит backslash.", "m03a-safety-profile")
    try:
        decoded_name = raw_name.decode("utf-8" if flags & 0x0800 else "ascii")
    except UnicodeDecodeError as error:
        raise _invalid("path_encoding", info.filename, "Имя ZIP entry имеет неподдерживаемую encoding.", "m03a-safety-profile") from error
    if decoded_name != info.filename:
        raise _invalid("local_name_mismatch", info.filename, "Local и central ZIP names не совпадают.", "zip-envelope")
    data_start = info.header_offset + 30 + name_length + extra_length
    data_end = data_start + info.compress_size
    if flags & 0x0008:
        descriptor_end = _data_descriptor_end(source, info, data_end)
        return info.header_offset, descriptor_end, data_start
    resolved_size, resolved_compressed = _local_zip64_sizes(local_extra, local_size, local_compressed)
    if local_crc != info.CRC or resolved_size != info.file_size or resolved_compressed != info.compress_size:
        raise _invalid("local_header_mismatch", info.filename, "Local и central ZIP sizes/CRC не совпадают.", "zip-envelope")
    return info.header_offset, data_end, data_start


def _data_descriptor_end(source: io.RawIOBase, info: ZipInfo, offset: int) -> int:
    uses_zip64 = info.file_size > 0xFFFFFFFF or info.compress_size > 0xFFFFFFFF
    maximum = 24 if uses_zip64 else 16
    source.seek(offset)
    raw = source.read(maximum)
    cursor = 4 if raw.startswith(b"PK\x07\x08") else 0
    required = cursor + (20 if uses_zip64 else 12)
    if len(raw) < required:
        raise _invalid("data_descriptor_invalid", info.filename, "ZIP data descriptor неполон.", "zip-envelope")
    if uses_zip64:
        crc, compressed, size = struct.unpack_from("<LQQ", raw, cursor)
    else:
        crc, compressed, size = struct.unpack_from("<LLL", raw, cursor)
    if crc != info.CRC or compressed != info.compress_size or size != info.file_size:
        raise _invalid("data_descriptor_invalid", info.filename, "ZIP data descriptor не совпадает с central index.", "zip-envelope")
    return offset + required


def _local_zip64_sizes(extra: bytes, size: int, compressed: int) -> tuple[int, int]:
    if size != 0xFFFFFFFF and compressed != 0xFFFFFFFF:
        return size, compressed
    cursor = 0
    while cursor + 4 <= len(extra):
        field_id, field_size = struct.unpack_from("<HH", extra, cursor)
        cursor += 4
        end = cursor + field_size
        if end > len(extra):
            break
        if field_id == 0x0001:
            values = extra[cursor:end]
            value_cursor = 0
            if size == 0xFFFFFFFF:
                if value_cursor + 8 > len(values):
                    break
                size = struct.unpack_from("<Q", values, value_cursor)[0]
                value_cursor += 8
            if compressed == 0xFFFFFFFF:
                if value_cursor + 8 > len(values):
                    break
                compressed = struct.unpack_from("<Q", values, value_cursor)[0]
            return size, compressed
        cursor = end
    raise _invalid("local_header_mismatch", "archive", "ZIP64 local sizes отсутствуют.", "zip-envelope")


def _iter_entry_chunks(
    source: io.RawIOBase,
    entry: _ValidatedEntry,
    control: ValidationControl,
    phase: JobPhase,
) -> Iterator[bytes]:
    info = entry.info
    source.seek(entry.data_start)
    remaining = info.compress_size
    output_size = 0
    crc = 0
    decompressor = zlib.decompressobj(-15) if info.compress_type == ZIP_DEFLATED else None
    while remaining > 0:
        control.check(phase)
        compressed = source.read(min(STREAM_CHUNK_BYTES, remaining))
        if not compressed:
            raise _invalid("entry_read_error", info.filename, "Compressed payload завершился преждевременно.", "zip-envelope")
        remaining -= len(compressed)
        if decompressor is None:
            output_size += len(compressed)
            if output_size > MAX_DECOMPRESSED_BYTES:
                raise _invalid("payload_limit_exceeded", info.filename, "Payload превышает технический предел.", "m03a-safety-profile")
            crc = zlib.crc32(compressed, crc)
            yield compressed
        else:
            pending = compressed
            while pending:
                control.check(phase)
                output = decompressor.decompress(pending, STREAM_CHUNK_BYTES)
                pending = decompressor.unconsumed_tail
                if decompressor.unused_data:
                    raise _invalid("entry_trailing_stream", info.filename, "DEFLATE stream содержит неучтённый хвост.", "zip-envelope")
                if output:
                    output_size += len(output)
                    if output_size > MAX_DECOMPRESSED_BYTES:
                        raise _invalid("payload_limit_exceeded", info.filename, "Payload превышает технический предел.", "m03a-safety-profile")
                    crc = zlib.crc32(output, crc)
                    yield output
                elif not pending:
                    break
    if decompressor is not None:
        tail = decompressor.flush()
        if tail:
            output_size += len(tail)
            crc = zlib.crc32(tail, crc)
            yield tail
        if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
            raise _invalid("entry_deflate_incomplete", info.filename, "DEFLATE stream не завершён однозначно.", "zip-envelope")
    if output_size != info.file_size or crc & 0xFFFFFFFF != info.CRC:
        raise _invalid("entry_size_or_crc_mismatch", info.filename, "Фактические размер или CRC payload не совпадают с ZIP index.", "zip-envelope")


def _validate_package_path(value: str, flags: int) -> str:
    if value == "" or len(value.encode("utf-8")) > MAX_PATH_BYTES:
        raise _invalid("path_length", "archive", "Имя ZIP entry имеет недопустимую длину.", "m03a-safety-profile")
    if any(ord(character) < 32 or ord(character) == 127 for character in value) or "\x00" in value:
        raise _invalid("path_control", "archive", "Имя ZIP entry содержит control character.", "m03a-safety-profile")
    if any(ord(character) > 127 for character in value) and not flags & 0x0800:
        raise _invalid("path_encoding", value, "Non-ASCII ZIP name не помечено UTF-8.", "m03a-safety-profile")
    if "\ufeff" in value or value != unicodedata.normalize("NFC", value) or "\\" in value or value.startswith("/"):
        raise _invalid("path_noncanonical", value, "ZIP path не является canonical POSIX-relative.", "m03a-safety-profile")
    raw_parts = value.split("/")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in raw_parts) or str(path) != value:
        raise _invalid("path_traversal", value, "ZIP path выходит за package root.", "m03a-safety-profile")
    for part in path.parts:
        if _windows_unsafe_segment(part):
            raise _invalid("path_windows_unsafe", value, "ZIP path несовместим с безопасным Windows profile.", "m03a-safety-profile")
    return value


def _parse_manifest(content: bytes) -> _Manifest:
    value = _parse_json(content, "manifest.json")
    manifest = _require_object(value, "manifest.json")
    if manifest.get("schema_version") != CONTRACT_SCHEMA:
        raise _invalid("manifest_schema", "manifest.schema_version", "Schema manifest не поддерживается validation profile.", "manifest-example")
    package_id = _source_uuid(manifest.get("package_id"), "manifest.package_id")
    run_id = _source_uuid(manifest.get("run_id"), "manifest.run_id")
    export_revision = manifest.get("export_revision")
    raw_package_kind = manifest.get("package_kind")
    if not isinstance(export_revision, int) or isinstance(export_revision, bool) or export_revision < 1 or export_revision > MAX_SAFE_INTEGER:
        raise _invalid("manifest_export_revision", "manifest.export_revision", "Export revision должна быть положительным целым.", "manifest-example")
    if raw_package_kind == "final":
        package_kind: Literal["final", "diagnostic_partial"] = "final"
    elif raw_package_kind == "diagnostic_partial":
        package_kind = "diagnostic_partial"
    else:
        raise _invalid("manifest_package_kind", "manifest.package_kind", "Package kind не поддерживается.", "manifest-example")
    _require_utc(manifest.get("created_at_utc"), "manifest.created_at_utc")
    _require_sha256(manifest.get("source_snapshot_sha256"), "manifest.source_snapshot_sha256")
    producer_raw = _require_object(manifest.get("producer"), "manifest.producer")
    try:
        producer = RunPackageProducer(
            name=_require_ascii_string(producer_raw.get("name"), "manifest.producer.name", 128),
            version=_require_ascii_string(producer_raw.get("version"), "manifest.producer.version", 64),
            buildId=_require_ascii_string(producer_raw.get("build_id"), "manifest.producer.build_id", 128),
            gitCommit=_require_ascii_string(producer_raw.get("git_commit"), "manifest.producer.git_commit", 40),
        )
    except ValueError as error:
        raise _invalid("manifest_producer", "manifest.producer", "Producer manifest имеет недопустимую форму.", "manifest-example") from error
    files_raw = manifest.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        raise _invalid("manifest_files", "manifest.files", "Inventory manifest отсутствует или пуст.", "manifest-example")
    files: list[_ManifestFile] = []
    paths: set[str] = set()
    for index, raw in enumerate(files_raw):
        entry = _require_object(raw, f"manifest.files[{index}]")
        path = _validate_package_path(_require_string(entry.get("path"), f"manifest.files[{index}].path", MAX_PATH_BYTES), 0x0800)
        if path in {"manifest.json", "checksums.sha256"} or path in paths:
            raise _invalid("manifest_path_duplicate", f"manifest.files[{index}].path", "Inventory path дублируется или самореферентен.", "manifest-example")
        paths.add(path)
        media_type = _require_string(entry.get("media_type"), f"manifest.files[{index}].media_type", 128)
        size = entry.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise _invalid("manifest_size", f"manifest.files[{index}].size", "Declared size некорректен.", "manifest-example")
        sha256 = _require_sha256(entry.get("sha256"), f"manifest.files[{index}].sha256")
        row_count = entry.get("row_count")
        if row_count is not None and (not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0):
            raise _invalid("manifest_row_count", f"manifest.files[{index}].row_count", "Declared row count некорректен.", "manifest-example")
        files.append(_ManifestFile(path, media_type, size, sha256, row_count))
    if sum(item.size for item in files) > MAX_DECOMPRESSED_BYTES:
        raise _invalid("expanded_size_limit", "manifest.files", "Declared payload объём превышает технический предел.", "m03a-safety-profile")
    return _Manifest(package_id, export_revision, run_id, package_kind, producer, tuple(files))


def _parse_json(content: bytes, location: str) -> JsonValue:
    if content.startswith(b"\xef\xbb\xbf"):
        raise _invalid("json_bom", location, "JSON BOM не допускается validation profile.", "m03a-safety-profile")
    try:
        decoded = content.decode("utf-8", errors="strict")
        json.loads(decoded, object_pairs_hook=_pairs_without_duplicates, parse_constant=_reject_non_finite)
        value = JSON_VALUE_ADAPTER.validate_json(content)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValidationError, ValueError) as error:
        raise _invalid("json_invalid", location, "JSON не прошёл bounded UTF-8 syntax profile.", "m03a-safety-profile") from error
    _validate_json_shape(value, location)
    return value


def _pairs_without_duplicates(pairs: JsonPairs) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"non_finite:{value}")


def _validate_json_shape(root: JsonValue, location: str) -> None:
    stack: list[tuple[JsonValue, int]] = [(root, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            raise _invalid("json_limit", location, "JSON превышает depth/node technical limit.", "m03a-safety-profile")
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise _invalid("json_non_finite", location, "JSON содержит non-finite number.", "m03a-safety-profile")


def _semantic_validate(
    manifest: _Manifest,
    json_payloads: dict[str, JsonValue],
    findings: _FindingAccumulator,
    control: ValidationControl,
) -> None:
    plan = json_payloads.get("plan/original.json")
    if plan is not None:
        _check_schema_and_run(plan, "r130sh.run-plan.v1", manifest.run_id, "plan/original.json", PLAN_SHAPE, findings, control)
    effective = json_payloads.get("plan/effective.json")
    if effective is not None:
        _check_schema_and_run(effective, "r130sh.run-plan.v1", manifest.run_id, "plan/effective.json", PLAN_SHAPE, findings, control)
    provenance = json_payloads.get("provenance.json")
    if provenance is not None:
        _check_schema_and_run(provenance, "r130sh.run-provenance.v1", manifest.run_id, "provenance.json", PROVENANCE_SHAPE, findings, control)
    accepted = json_payloads.get("accepted-summary.json")
    if accepted is not None:
        _check_schema_and_run(accepted, "r130sh.accepted-projection.v1", manifest.run_id, "accepted-summary.json", ACCEPTED_SHAPE, findings, control)
    inspections = json_payloads.get("inspections.json")
    if isinstance(inspections, list):
        for inspection in inspections:
            control.check("semantic_validation")
            _check_schema_and_run(inspection, "r130sh.inspection.v1", manifest.run_id, "inspections.json", INSPECTION_SHAPE, findings, control)
    elif inspections is not None:
        findings.add(_finding("semantic_shape_mismatch", "error", "inspections.json", "Payload не совпадает с frozen example shape.", "inspection-example"))


def _semantic_validate_jsonl_item(
    location: str,
    value: JsonValue,
    run_id: str,
    findings: _FindingAccumulator,
) -> None:
    if location == "events.jsonl":
        _check_schema_and_run(value, "r130sh.run-event.v1", run_id, location, EVENT_SHAPE, findings)


def _check_schema_and_run(
    value: JsonValue,
    schema: str,
    run_id: str,
    location: str,
    shape: FrozenShape,
    findings: _FindingAccumulator,
    control: ValidationControl | None = None,
) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != schema:
        findings.add(_finding("semantic_schema_mismatch", "error", location, "Payload schema не совпадает с frozen example.", schema))
        return
    if value.get("run_id") != run_id:
        findings.add(_finding("cross_file_run_id_mismatch", "error", location, "Run identity не совпадает с manifest.", schema))
    if not _matches_frozen_shape(value, shape):
        findings.add(_finding("semantic_shape_mismatch", "error", location, "Payload не совпадает с frozen example shape.", schema))
        return
    _check_frozen_values(value, schema, location, findings, control)


def _matches_frozen_shape(value: JsonValue, shape: FrozenShape) -> bool:
    if isinstance(shape, dict):
        return isinstance(value, dict) and all(key in value and _matches_frozen_shape(value[key], nested) for key, nested in shape.items())
    if isinstance(shape, list):
        if len(shape) != 1 or not isinstance(value, list):
            return False
        return all(_matches_frozen_shape(item, shape[0]) for item in value)
    if isinstance(shape, tuple):
        return type(value) in shape
    return type(value) is shape


def _check_frozen_values(
    value: dict[str, JsonValue],
    schema: str,
    location: str,
    findings: _FindingAccumulator,
    control: ValidationControl | None,
) -> None:
    valid = True
    if schema == "r130sh.run-plan.v1":
        execution_targets = value["execution_targets"]
        if not isinstance(execution_targets, dict):
            findings.add(_finding("semantic_value_mismatch", "error", location, "Payload values нарушают frozen semantic profile.", schema))
            return
        valid = _valid_source_id(value["plan_id"]) and _valid_source_id(value["specimen_id"])
        valid = valid and _valid_sha(value["original_plan_sha256"]) and _valid_sha(value["effective_plan_sha256"])
        valid = valid and value["mode"] == "rbd" and execution_targets["rounding_policy"] == "ceiling"
        try:
            source_values = value["source_values"]
            requirements = value["methodical_requirements"]
            targets = value["execution_targets"]
            assert isinstance(source_values, dict) and isinstance(requirements, dict) and isinstance(targets, dict)
            base_cycles = Decimal(str(source_values["base_cycles"]))
            reserve_factor = Decimal(str(source_values["reserve_factor"]))
            nominal_rpm = Decimal(str(source_values["nominal_rpm"]))
            required_cycles = Decimal(str(requirements["required_cycles_exact"]))
            required_duration = Decimal(str(requirements["required_steady_duration_s_exact"]))
            target_cycles = targets["target_cycles"]
            target_duration = Decimal(str(targets["target_steady_duration_s"]))
            if not isinstance(target_cycles, int) or isinstance(target_cycles, bool):
                raise ValueError("target_cycles_not_integer")
            valid = valid and base_cycles > 0 and reserve_factor > 0 and nominal_rpm > 0
            valid = valid and required_cycles == base_cycles * reserve_factor
            valid = valid and required_duration == required_cycles * Decimal(60) / nominal_rpm
            valid = valid and target_cycles == int(required_cycles.to_integral_value(rounding=ROUND_CEILING))
            valid = valid and target_duration == Decimal(target_cycles) * Decimal(60) / nominal_rpm
        except AssertionError, InvalidOperation, KeyError, TypeError, ValueError, ZeroDivisionError:
            valid = False
    elif schema == "r130sh.run-event.v1":
        payload = value["payload_json"]
        assert isinstance(payload, dict)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        valid = (
            _valid_source_id(value["event_id"])
            and _valid_source_id(value["clock_epoch_id"])
            and _valid_sha(value["payload_sha256"])
            and value["payload_sha256"] == hashlib.sha256(encoded).hexdigest()
            and isinstance(value["run_sequence"], int)
            and value["run_sequence"] >= 0
        )
    elif schema == "r130sh.inspection.v1":
        valid = _valid_source_id(value["inspection_id"]) and isinstance(value["trip_index"], int) and value["trip_index"] >= 0
    elif schema == "r130sh.run-provenance.v1":
        producer = value["producer"]
        stand = value["stand"]
        assert isinstance(producer, dict) and isinstance(stand, dict)
        valid = _valid_sha(stand["stand_spec_sha256"]) and _valid_sha(stand["register_map_sha256"])
        valid = valid and _valid_sha(stand["direction_binding_sha256"]) and _valid_git_commit(producer["git_commit"])
    elif schema == "r130sh.accepted-projection.v1":
        points = value["points"]
        assert isinstance(points, list)
        for point in points:
            if control is not None:
                control.check("semantic_validation")
            valid = valid and isinstance(point, dict) and _valid_source_id(point["measurement_id"])
    if not valid:
        findings.add(_finding("semantic_value_mismatch", "error", location, "Payload values нарушают frozen semantic profile.", schema))


def _valid_source_id(value: JsonValue) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return str(parsed) == value and parsed.version in {4, 7} and parsed.variant == "specified in RFC 4122"


def _valid_sha(value: JsonValue) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _valid_git_commit(value: JsonValue) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(character in "0123456789abcdef" for character in value)


def _semantic_coverage() -> list[RunPackageSemanticCoverage]:
    return [
        RunPackageSemanticCoverage(area="manifest", status="covered", contractSource="manifest-example"),
        RunPackageSemanticCoverage(area="rbd_plan", status="covered", contractSource="plan-rbd-example"),
        RunPackageSemanticCoverage(area="event_envelope", status="covered", contractSource="event-example"),
        RunPackageSemanticCoverage(area="inspection", status="covered", contractSource="inspection-example"),
        RunPackageSemanticCoverage(area="provenance", status="covered", contractSource="provenance-example"),
        RunPackageSemanticCoverage(area="accepted_projection", status="covered", contractSource="accepted-projection-example"),
        RunPackageSemanticCoverage(area="measurements_csv", status="not_available", contractSource="upstream-contract-gap"),
        RunPackageSemanticCoverage(area="checksums_sha256", status="contract_gap", contractSource="upstream-contract-gap"),
        RunPackageSemanticCoverage(area="remaining_payloads", status="contract_gap", contractSource="upstream-contract-gap"),
    ]


def _read_bounded_entry(source: io.RawIOBase, entry: _ValidatedEntry, limit: int, control: ValidationControl, phase: JobPhase) -> bytes:
    if entry.info.file_size > limit:
        raise _invalid("entry_limit", entry.info.filename, "ZIP entry превышает технический предел.", "m03a-safety-profile")
    try:
        result = bytearray()
        for chunk in _iter_entry_chunks(source, entry, control, phase):
            result.extend(chunk)
            if len(result) > limit:
                raise _invalid("entry_limit", entry.info.filename, "ZIP entry превышает технический предел.", "m03a-safety-profile")
        return bytes(result)
    except (BadZipFile, OSError, RuntimeError) as error:
        if isinstance(error, _PackageFindingError):
            raise
        raise _invalid("entry_read_error", entry.info.filename, "ZIP entry не удалось прочитать полностью.", "zip-envelope") from error


def _failed_report(source_name: str, outer_sha: str, outer_size: int, started_at: str, finding: RunPackageFinding) -> RunPackageValidationReport:
    findings = _FindingAccumulator.empty()
    findings.add(finding)
    return _report(
        source_file_name=source_name,
        outer_sha256=outer_sha,
        outer_size=outer_size,
        manifest=None,
        entry_count=0,
        validated_bytes=0,
        structural_verdict="failed",
        semantic_verdict="not_available",
        coverage=_semantic_coverage(),
        findings=findings,
        started_at=started_at,
    )


def _report(
    *,
    source_file_name: str,
    outer_sha256: str,
    outer_size: int,
    manifest: _Manifest | None,
    entry_count: int,
    validated_bytes: int,
    structural_verdict: StructuralVerdict,
    semantic_verdict: SemanticVerdict,
    coverage: list[RunPackageSemanticCoverage],
    findings: _FindingAccumulator,
    started_at: str,
) -> RunPackageValidationReport:
    return RunPackageValidationReport(
        sourceFileName=source_file_name,
        outerPackageSha256=outer_sha256,
        outerSizeBytes=outer_size,
        packageId=None if manifest is None else manifest.package_id,
        exportRevision=None if manifest is None else manifest.export_revision,
        runId=None if manifest is None else manifest.run_id,
        packageKind=None if manifest is None else manifest.package_kind,
        producer=None if manifest is None else manifest.producer,
        entryCount=entry_count,
        declaredPayloadBytes=0 if manifest is None else sum(item.size for item in manifest.files),
        validatedPayloadBytes=validated_bytes,
        structuralVerdict=structural_verdict,
        semanticVerdict=semantic_verdict,
        semanticCoverage=coverage,
        findingCounts=findings.counts(),
        findings=list(findings.findings),
        startedAtUtc=started_at,
        finishedAtUtc=utc_now(),
    )


def _bounded_report(report: RunPackageValidationReport) -> RunPackageValidationReport:
    while len(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_REPORT_BYTES:
        if not report.findings:
            raise ValueError("run_package_report_exceeds_transport_bound")
        report.findings.pop()
        report.findingCounts.truncated = True
    return report


def _invalid(code: str, location: str, message: str, contract_source: str) -> _PackageFindingError:
    return _PackageFindingError(_finding(code, "error", location, message, contract_source))


def _finding(code: str, severity: FindingSeverity, location: str, message: str, contract_source: str) -> RunPackageFinding:
    safe_location = _safe_finding_location(location)
    return RunPackageFinding(code=code[:96], severity=severity, location=safe_location[:512], message=message[:512], contractSource=contract_source[:160])


def _safe_finding_location(value: str) -> str:
    parts = value.split("/")
    if (
        value.startswith(("/", "\\"))
        or "\\" in value
        or "\ufeff" in value
        or "\x00" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or (len(value) >= 2 and value[1] == ":")
        or value != unicodedata.normalize("NFC", value)
        or any(part in {"", ".", ".."} for part in parts)
        or any(_windows_unsafe_segment(part) for part in parts)
    ):
        return "archive.entry_name"
    return value


def _windows_unsafe_segment(value: str) -> bool:
    stem = value.split(".", 1)[0].casefold()
    return ":" in value or value.endswith((".", " ")) or stem in {"con", "prn", "aux", "nul", *(f"com{index}" for index in range(1, 10)), *(f"lpt{index}" for index in range(1, 10))}


def _source_uuid(value: object, location: str) -> str:
    if not isinstance(value, str):
        raise _invalid("source_id_invalid", location, "Source ID не является UUID v4/v7.", "m03a-source-id-profile")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise _invalid("source_id_invalid", location, "Source ID не является UUID v4/v7.", "m03a-source-id-profile") from error
    if str(parsed) != value or parsed.version not in {4, 7} or parsed.variant != "specified in RFC 4122":
        raise _invalid("source_id_invalid", location, "Source ID не является canonical UUID v4/v7.", "m03a-source-id-profile")
    return value


def _require_sha256(value: object, location: str) -> str:
    if not isinstance(value, str) or len(value) != SHA256_PATTERN_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise _invalid("sha256_invalid", location, "SHA-256 должен быть lowercase hex.", "manifest-example")
    return value


def _require_utc(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise _invalid("timestamp_invalid", location, "Timestamp должен быть UTC ISO 8601.", "manifest-example")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _invalid("timestamp_invalid", location, "Timestamp должен быть UTC ISO 8601.", "manifest-example") from error
    if parsed.tzinfo != UTC:
        raise _invalid("timestamp_invalid", location, "Timestamp должен быть UTC ISO 8601.", "manifest-example")
    return value


def _require_object(value: object, location: str) -> dict[str, JsonValue]:
    try:
        return JSON_OBJECT_ADAPTER.validate_python(value)
    except ValidationError as error:
        raise _invalid("json_object_required", location, "Ожидался JSON object.", "m03a-safety-profile") from error


def _require_string(value: object, location: str, maximum: int) -> str:
    if not isinstance(value, str) or value == "" or len(value) > maximum:
        raise _invalid("string_invalid", location, "Строковое поле имеет недопустимую длину.", "m03a-safety-profile")
    return value


def _require_ascii_string(value: object, location: str, maximum: int) -> str:
    result = _require_string(value, location, maximum)
    if any(ord(character) < 32 or ord(character) > 126 for character in result):
        raise _invalid("string_invalid", location, "Строковое поле выходит за ASCII contract profile.", "m03a-safety-profile")
    return result


def _bounded_file_name(value: str) -> str:
    encoded = value.encode("utf-8")
    if 0 < len(encoded) <= 255:
        return value
    while len(encoded) > 255:
        value = value[:-1]
        encoded = value.encode("utf-8")
    return value or "package.r130run"


def _signature(value: os.stat_result) -> _SourceSignature:
    return _SourceSignature(value.st_size, value.st_mtime_ns, value.st_dev, value.st_ino)


def _is_reparse(value: os.stat_result) -> bool:
    attributes = getattr(value, "st_file_attributes", 0)
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)
