from __future__ import annotations

from collections.abc import Callable, Mapping
import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import TextIOWrapper
import json
from pathlib import Path
from typing import Final, cast
from zipfile import ZipFile

from impeller_reliability.integration.r130run.models import RunPackageValidationReport

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None

MEASUREMENT_COLUMNS: Final = (
    "measurement_id",
    "run_id",
    "measurement_sequence",
    "source_generation",
    "source_sequence",
    "attempt_id",
    "attempt_disposition",
    "segment_id",
    "segment_disposition",
    "accepted",
    "accepted_elapsed_s",
    "sampled_at_utc",
    "clock_epoch_id",
    "epoch_monotonic_elapsed_s",
    "run_elapsed_s",
    "attempt_elapsed_s",
    "segment_elapsed_s",
    "phase",
    "axis_synchrony",
    "rpm_plan",
    "rpm_fact",
    "rpm_quality",
    "rpm_source",
    "rpm_source_timestamp_utc",
    "rpm_fallback_active",
    "fixed_channels_json",
    "vibration_max_axis_mm_s",
    "vibration_x_mm_s",
    "vibration_x_quality",
    "vibration_x_source_timestamp_utc",
    "vibration_y_mm_s",
    "vibration_y_quality",
    "vibration_y_source_timestamp_utc",
    "vibration_z_mm_s",
    "vibration_z_quality",
    "vibration_z_source_timestamp_utc",
    "measurement_descriptor_id",
    "active_vibration_criterion_id",
    "active_vibration_criterion_role",
    "active_threshold_mm_s",
)


class M9aContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class M9aInventoryItem:
    path: str
    media_type: str
    size_bytes: int
    sha256: str
    row_count: int | None
    semantic_coverage: str


@dataclass(frozen=True, slots=True)
class M9aProjection:
    run_id: str
    source_specimen_id: str
    mode: str
    package_kind: str
    technical_status: str | None
    termination_reason: str | None
    specimen_outcome: str | None
    run_validity: str | None
    data_completeness: str | None
    partial_reasons: tuple[str, ...]
    resume_available: bool
    original_plan_id: str
    original_plan_revision: int
    original_plan_sha256: str
    effective_plan_id: str
    effective_plan_revision: int
    effective_plan_sha256: str
    original_plan_summary: dict[str, JsonValue]
    effective_plan_summary: dict[str, JsonValue]
    started_at_utc: str
    finished_at_utc: str | None
    customer_full_name: str | None
    customer_address: str | None
    customer_order_reference: str | None
    wheel_full_name: str | None
    wheel_identifier: str | None
    working_diameter_mm: str | None
    sample_label: str | None
    environment_status: str | None
    environment_summary: dict[str, JsonValue]
    provenance_summary: dict[str, JsonValue]
    measurement_count: int
    accepted_measurement_count: int
    event_count: int
    inspection_count: int
    attachment_count: int
    amendment_count: int
    crediting_policy: str | None
    accepted_elapsed_s: str | None


@dataclass(frozen=True, slots=True)
class M9aPackageFacts:
    package_id: str
    export_revision: int
    run_id: str
    package_kind: str
    package_schema: str
    package_created_at_utc: str
    source_snapshot_sha256: str
    producer_name: str
    producer_version: str
    producer_build_id: str
    producer_git_commit: str
    outer_package_sha256: str
    outer_size_bytes: int
    inventory: tuple[M9aInventoryItem, ...]
    projection: M9aProjection


def read_m9a_package_facts(
    package_path: Path,
    report: RunPackageValidationReport,
    checkpoint: Callable[[], None] | None = None,
) -> M9aPackageFacts:
    if report.structuralVerdict != "passed" or report.semanticVerdict != "passed":
        raise M9aContractError("package_not_accepted")
    with ZipFile(package_path, mode="r") as archive:
        _checkpoint(checkpoint)
        manifest = _object(_load_json(archive, "manifest.json"), "manifest.json")
        _checkpoint(checkpoint)
        original = _object(_load_json(archive, "plan/original.json"), "plan/original.json")
        effective_envelope = _object(
            _load_json(archive, "plan/effective.json"),
            "plan/effective.json",
        )
        summary = _object(_load_json(archive, "run-summary.json"), "run-summary.json")
        environment = _object(_load_json(archive, "environment.json"), "environment.json")
        provenance = _object(_load_json(archive, "provenance.json"), "provenance.json")
        accepted = _object(_load_json(archive, "accepted-summary.json"), "accepted-summary.json")
        inspections = _object(_load_json(archive, "inspections.json"), "inspections.json")
        attachments = _object(
            _load_json(archive, "attachments/index.json"),
            "attachments/index.json",
        )
        inventory = _inventory(manifest)
        inventory_by_path = {item.path: item for item in inventory}
        measurement_count, accepted_count, accepted_elapsed = _measurement_summary(
            archive,
            _text(manifest.get("run_id"), "manifest.run_id"),
            checkpoint,
        )

    effective_container = _object(
        effective_envelope.get("effective_plan"),
        "plan/effective.json.effective_plan",
    )
    effective = _object(
        effective_container.get("effective_plan"),
        "plan/effective.json.effective_plan.effective_plan",
    )
    run_card = _object(summary.get("run_card"), "run-summary.json.run_card")
    decision = _object(environment.get("decision"), "environment.json.decision")
    provenance_value = _object(provenance.get("provenance"), "provenance.json.provenance")
    inspection_values = _list(inspections.get("inspections"), "inspections.json.inspections")
    attachment_values = _list(attachments.get("attachments"), "attachments/index.json.attachments")

    accepted_measurement_count = _non_negative_int(
        accepted.get("accepted_measurement_count"),
        "accepted-summary.json.accepted_measurement_count",
    )
    accepted_elapsed_s = _decimal_text(
        accepted.get("accepted_elapsed_s"),
        "accepted-summary.json.accepted_elapsed_s",
    )
    if accepted_measurement_count != accepted_count or accepted_elapsed_s != accepted_elapsed:
        raise M9aContractError("accepted_summary_mismatch")

    package_id = _text(manifest.get("package_id"), "manifest.package_id")
    export_revision = _positive_int(manifest.get("export_revision"), "manifest.export_revision")
    run_id = _text(manifest.get("run_id"), "manifest.run_id")
    package_kind = _text(manifest.get("package_kind"), "manifest.package_kind")
    producer = _object(manifest.get("producer"), "manifest.producer")
    partial_reasons = tuple(_text(value, "run-summary.json.partial_reasons[]") for value in _list(summary.get("partial_reasons"), "run-summary.json.partial_reasons"))
    original_file = _inventory_item(inventory_by_path, "plan/original.json")
    effective_file = _inventory_item(inventory_by_path, "plan/effective.json")
    event_file = _inventory_item(inventory_by_path, "events.jsonl")
    amendment_file = _inventory_item(inventory_by_path, "plan/amendments.jsonl")
    original_summary = _plan_summary(original)
    effective_summary = _plan_summary(effective)
    projection = M9aProjection(
        run_id=run_id,
        source_specimen_id=_text(summary.get("specimen_id"), "run-summary.json.specimen_id"),
        mode=_text(summary.get("mode"), "run-summary.json.mode"),
        package_kind=package_kind,
        technical_status=_optional_text(summary.get("technical_status"), "run-summary.json.technical_status"),
        termination_reason=_optional_text(summary.get("termination_reason"), "run-summary.json.termination_reason"),
        specimen_outcome=_optional_text(summary.get("specimen_outcome"), "run-summary.json.specimen_outcome"),
        run_validity=_optional_text(summary.get("run_validity"), "run-summary.json.run_validity"),
        data_completeness=_optional_text(summary.get("data_completeness"), "run-summary.json.data_completeness"),
        partial_reasons=partial_reasons,
        resume_available=_boolean(summary.get("resume_available"), "run-summary.json.resume_available"),
        original_plan_id=_text(original.get("plan_id"), "plan/original.json.plan_id"),
        original_plan_revision=_positive_int(
            original.get("plan_revision"),
            "plan/original.json.plan_revision",
        ),
        original_plan_sha256=original_file.sha256,
        effective_plan_id=_text(effective.get("plan_id"), "plan/effective.json.plan_id"),
        effective_plan_revision=_positive_int(
            effective.get("plan_revision"),
            "plan/effective.json.plan_revision",
        ),
        effective_plan_sha256=effective_file.sha256,
        original_plan_summary=original_summary,
        effective_plan_summary=effective_summary,
        started_at_utc=_text(summary.get("started_at_utc"), "run-summary.json.started_at_utc"),
        finished_at_utc=_optional_text(summary.get("finished_at_utc"), "run-summary.json.finished_at_utc"),
        customer_full_name=_optional_text(run_card.get("customer_name"), "run_card.customer_name"),
        customer_address=_optional_text(run_card.get("customer_address"), "run_card.customer_address"),
        customer_order_reference=_optional_text(
            run_card.get("customer_order_reference"),
            "run_card.customer_order_reference",
        ),
        wheel_full_name=_optional_text(run_card.get("wheel_full_name"), "run_card.wheel_full_name"),
        wheel_identifier=_optional_text(run_card.get("wheel_identifier"), "run_card.wheel_identifier"),
        working_diameter_mm=_optional_scalar_text(
            run_card.get("working_diameter_mm"),
            "run_card.working_diameter_mm",
        ),
        sample_label=_optional_text(summary.get("sample_label"), "run-summary.json.sample_label"),
        environment_status=_optional_text(decision.get("status"), "environment.decision.status"),
        environment_summary=_environment_summary(decision),
        provenance_summary=_provenance_summary(provenance_value),
        measurement_count=measurement_count,
        accepted_measurement_count=accepted_measurement_count,
        event_count=_row_count(event_file),
        inspection_count=len(inspection_values),
        attachment_count=len(attachment_values),
        amendment_count=_row_count(amendment_file),
        crediting_policy=_optional_text(accepted.get("crediting_policy"), "accepted-summary.json.crediting_policy"),
        accepted_elapsed_s=accepted_elapsed_s,
    )
    if projection.run_id != report.runId or package_id != report.packageId:
        raise M9aContractError("validation_report_identity_mismatch")
    return M9aPackageFacts(
        package_id=package_id,
        export_revision=export_revision,
        run_id=run_id,
        package_kind=package_kind,
        package_schema=_text(manifest.get("schema_version"), "manifest.schema_version"),
        package_created_at_utc=_text(manifest.get("created_at_utc"), "manifest.created_at_utc"),
        source_snapshot_sha256=_text(
            manifest.get("source_snapshot_sha256"),
            "manifest.source_snapshot_sha256",
        ),
        producer_name=_text(producer.get("name"), "manifest.producer.name"),
        producer_version=_text(producer.get("version"), "manifest.producer.version"),
        producer_build_id=_text(producer.get("build_id"), "manifest.producer.build_id"),
        producer_git_commit=_text(producer.get("git_commit"), "manifest.producer.git_commit"),
        outer_package_sha256=report.outerPackageSha256,
        outer_size_bytes=report.outerSizeBytes,
        inventory=inventory,
        projection=projection,
    )


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _inventory(manifest: dict[str, JsonValue]) -> tuple[M9aInventoryItem, ...]:
    result: list[M9aInventoryItem] = []
    files = _list(manifest.get("files"), "manifest.files")
    if len(files) > 128:
        raise M9aContractError("inventory_limit_exceeded")
    for value in files:
        item = _object(value, "manifest.files[]")
        path = _text(item.get("path"), "manifest.files[].path")
        result.append(
            M9aInventoryItem(
                path=path,
                media_type=_text(item.get("media_type"), "manifest.files[].media_type"),
                size_bytes=_non_negative_int(item.get("size"), "manifest.files[].size"),
                sha256=_text(item.get("sha256"), "manifest.files[].sha256"),
                row_count=(None if item.get("row_count") is None else _non_negative_int(item.get("row_count"), "manifest.files[].row_count")),
                semantic_coverage=(
                    "covered"
                    if path
                    in {
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
                        "inspections.json",
                        "attachments/index.json",
                    }
                    else "structural_only"
                ),
            ),
        )
    return tuple(result)


def _measurement_summary(
    archive: ZipFile,
    expected_run_id: str,
    checkpoint: Callable[[], None] | None,
) -> tuple[int, int, str]:
    count = 0
    accepted_count = 0
    last_accepted = Decimal(0)
    with archive.open("measurements.csv", mode="r") as raw, TextIOWrapper(raw, encoding="utf-8", errors="strict", newline="") as text:
        reader = csv.DictReader(text)
        if tuple(reader.fieldnames or ()) != MEASUREMENT_COLUMNS:
            raise M9aContractError("measurement_header_mismatch")
        previous_sequence = -1
        for row in reader:
            _checkpoint(checkpoint)
            count += 1
            previous_sequence = validate_measurement_row(row, expected_run_id, previous_sequence)
            if row["accepted"] == "true":
                accepted_count += 1
                try:
                    last_accepted = Decimal(row["accepted_elapsed_s"])
                except InvalidOperation as error:
                    raise M9aContractError("accepted_elapsed_invalid") from error
    return count, accepted_count, _canonical_decimal(last_accepted)


def validate_measurement_row(
    row: Mapping[str, str],
    expected_run_id: str,
    previous_sequence: int,
) -> int:
    if row["run_id"] != expected_run_id or not row["measurement_id"]:
        raise M9aContractError("measurement_identity_invalid")
    try:
        sequence = int(row["measurement_sequence"])
    except ValueError as error:
        raise M9aContractError("measurement_sequence_invalid") from error
    if sequence <= previous_sequence:
        raise M9aContractError("measurement_sequence_invalid")
    if row["axis_synchrony"] not in {"synchronous", "non_synchronous"}:
        raise M9aContractError("measurement_axis_synchrony_invalid")
    if row["rpm_fallback_active"] not in {"true", "false"}:
        raise M9aContractError("measurement_rpm_fallback_invalid")
    if row["accepted"] == "true":
        if row["attempt_disposition"] not in {"active", "accepted"} or row["segment_disposition"] != "included":
            raise M9aContractError("measurement_acceptance_invalid")
        try:
            accepted_elapsed = Decimal(row["accepted_elapsed_s"])
        except InvalidOperation as error:
            raise M9aContractError("accepted_elapsed_invalid") from error
        if not accepted_elapsed.is_finite() or accepted_elapsed < 0:
            raise M9aContractError("accepted_elapsed_invalid")
    elif row["accepted"] != "false" or row["accepted_elapsed_s"] != "" or row["attempt_disposition"] != "rejected" or row["segment_disposition"] != "excluded":
        raise M9aContractError("measurement_acceptance_invalid")
    return sequence


def _plan_summary(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    summary = _select_fields(
        value,
        (
            "schema_version",
            "run_id",
            "plan_id",
            "plan_revision",
            "specimen_id",
            "wheel_identifier",
            "mode",
            "laboratory_case_reference",
            "customer_order_reference",
        ),
    )
    summary["source_values"] = _select_fields(
        _optional_object(value.get("source_values")),
        ("nominal_rpm",),
    )
    summary["methodical_requirements"] = _select_fields(
        _optional_object(value.get("methodical_requirements")),
        (
            "required_cycles_exact",
            "required_steady_duration_s_exact",
            "required_total_duration_s_exact",
            "cycle_duration_s_exact",
            "target_max_rpm_exact",
        ),
    )
    summary["execution_targets"] = _select_fields(
        _optional_object(value.get("execution_targets")),
        (
            "target_cycles",
            "target_max_rpm",
            "lower_rpm",
            "upper_rpm",
            "target_steady_duration_s",
            "total_duration_s",
            "lower_point_policy",
            "rounding_policy",
        ),
    )
    return summary


def _environment_summary(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    result = _select_fields(value, ("status",))
    result["values"] = _select_fields(
        _optional_object(value.get("values")),
        ("temperature_c", "humidity_pct", "pressure_kpa", "source"),
    )
    deviations = _list(value.get("deviations"), "environment.decision.deviations")
    result["deviations"] = [
        _select_fields(
            _object(item, "environment.decision.deviations[]"),
            ("dimension", "minimum", "maximum", "observed", "relation"),
        )
        for item in deviations
    ]
    confirmation = _optional_object(value.get("confirmation"))
    actor = _optional_object(confirmation.get("actor"))
    result["confirmation"] = {
        **_select_fields(confirmation, ("confirmed_at_utc", "reason")),
        "actor": _select_fields(actor, ("employee_id", "full_name", "position")),
    }
    return result


def _provenance_summary(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    result = _select_fields(
        value,
        ("producer_name", "app_version", "database_schema_version", "time_source"),
    )
    for field in ("build_id", "git_commit", "stand_name", "stand_serial_number"):
        result[field] = _select_fields(
            _optional_object(value.get(field)),
            ("availability", "value", "reason"),
        )
    return result


def _select_fields(
    value: Mapping[str, JsonValue],
    fields: tuple[str, ...],
) -> dict[str, JsonValue]:
    return {field: value[field] for field in fields if field in value}


def _optional_object(value: object) -> dict[str, JsonValue]:
    return {} if value is None else _object(value, "projection")


def _load_json(archive: ZipFile, path: str) -> JsonValue:
    try:
        return cast(JsonValue, json.loads(archive.read(path).decode("utf-8")))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise M9aContractError(f"invalid_json:{path}") from error


def _object(value: object, location: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise M9aContractError(f"object_required:{location}")
    return cast(dict[str, JsonValue], value)


def _checkpoint(callback: Callable[[], None] | None) -> None:
    if callback is not None:
        callback()


def _list(value: object, location: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise M9aContractError(f"list_required:{location}")
    return cast(list[JsonValue], value)


def _text(value: object, location: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 4096:
        raise M9aContractError(f"text_required:{location}")
    return value


def _optional_text(value: object, location: str) -> str | None:
    if value is None:
        return None
    return _text(value, location)


def _optional_scalar_text(value: object, location: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise M9aContractError(f"scalar_required:{location}")
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _boolean(value: object, location: str) -> bool:
    if not isinstance(value, bool):
        raise M9aContractError(f"boolean_required:{location}")
    return value


def _positive_int(value: object, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise M9aContractError(f"positive_integer_required:{location}")
    return value


def _non_negative_int(value: object, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise M9aContractError(f"non_negative_integer_required:{location}")
    return value


def _decimal_text(value: object, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise M9aContractError(f"decimal_required:{location}")
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as error:
        raise M9aContractError(f"decimal_required:{location}") from error
    if not decimal_value.is_finite() or decimal_value < 0:
        raise M9aContractError(f"decimal_required:{location}")
    return _canonical_decimal(decimal_value)


def _canonical_decimal(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _row_count(item: M9aInventoryItem) -> int:
    if item.row_count is None:
        raise M9aContractError(f"row_count_missing:{item.path}")
    return item.row_count


def _inventory_item(
    inventory: Mapping[str, M9aInventoryItem],
    path: str,
) -> M9aInventoryItem:
    try:
        return inventory[path]
    except KeyError as error:
        raise M9aContractError(f"inventory_missing:{path}") from error


__all__ = [
    "M9aContractError",
    "M9aInventoryItem",
    "M9aPackageFacts",
    "M9aProjection",
    "canonical_json",
    "read_m9a_package_facts",
    "validate_measurement_row",
]
