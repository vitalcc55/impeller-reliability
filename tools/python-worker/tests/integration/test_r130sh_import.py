from __future__ import annotations

from contextlib import closing
import csv
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
from threading import Event
from time import monotonic, sleep
from uuid import uuid4
from zipfile import ZipFile

from pydantic import TypeAdapter
import pytest

from impeller_reliability.application.project_service import ProjectService
from impeller_reliability.integration.r130run.import_jobs import RunPackageImportJobManager
from impeller_reliability.integration.r130run.import_models import imported_run_detail_model
from impeller_reliability.integration.r130run.m9a import M9aPackageFacts, read_m9a_package_facts
from impeller_reliability.integration.r130run.models import RunPackageValidationReport
from impeller_reliability.integration.r130run.validator import (
    RunPackageValidator,
    ValidationControl,
)
from impeller_reliability.persistence import r130sh_sources as r130sh_sources_module
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.r130sh_sources import ImportedRunDetail, ImportedRunSummary
from impeller_reliability.worker.deadline import RequestDeadline
from support.r130run_builder import build_synthetic_r130run

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
M9A_ROOT = REPOSITORY_ROOT / "fixtures" / "contracts" / "r130run" / "v1" / "m9a"
OBJECT_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
EXPECTED_TERMINAL: dict[str, tuple[str, str | None, str | None, str | None, str | None]] = {
    "normal_final_pmn": ("final", "completed", "normal_done", "passed", "valid"),
    "normal_final_rpt_one_percent": ("final", "completed", "normal_done", "passed", "valid"),
    "normal_final_rpt_full_stop": ("final", "completed", "normal_done", "passed", "valid"),
    "normal_final_rbd": ("final", "completed", "normal_done", "passed", "valid"),
    "first_vibration_trip_inspection_amendment_completion": ("final", "completed", "normal_done", None, None),
    "repeated_vibration_trip": ("final", "interrupted", "repeated_vibration_trip", None, None),
    "manual_stop": ("diagnostic_partial", "interrupted", "manual_stop", "not_assessed", "not_assessed"),
    "device_failure": ("diagnostic_partial", "error", "device_error", "not_assessed", "not_assessed"),
    "communication_loss": ("diagnostic_partial", "error", "communication_loss", "not_assessed", "not_assessed"),
    "storage_failure_data_gap": ("diagnostic_partial", "error", "storage_failure", None, None),
}
EXPECTED_SCENARIOS = {
    "communication_loss",
    "device_failure",
    "diagnostic_partial",
    "duplicate_import_key",
    "environment_deviation_confirmation",
    "exact_methodical_rounding",
    "first_vibration_trip_inspection_amendment_completion",
    "manual_stop",
    "measurement_retained_after_attempt_rejection",
    "non_synchronous_xyz_rpm_fallback",
    "normal_final_pmn",
    "normal_final_rbd",
    "normal_final_rpt_full_stop",
    "normal_final_rpt_one_percent",
    "repeated_vibration_trip",
    "same_marking_distinct_specimens",
    "shared_specimen_pmn_rpt_rbd",
    "storage_failure_data_gap",
}


def test_imports_all_m9a_packages_and_reopens_persisted_sources(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    index = json.loads((M9A_ROOT / "package-index.json").read_text(encoding="utf-8"))
    assert {str(entry["case_name"]) for entry in index["packages"]} == EXPECTED_SCENARIOS
    imported_ids: list[str] = []
    details_by_scenario: dict[str, list[ImportedRunDetail]] = {}

    for entry in index["packages"]:
        source = M9A_ROOT / entry["path"]
        imported = _import_via_job(
            service,
            project_path,
            source,
            allow_diagnostic_partial=entry["package_kind"] == "diagnostic_partial",
        )
        assert imported.outer_package_sha256 == entry["sha256"]
        assert imported.outer_size_bytes == entry["size"]
        assert imported.source_integrity == "verified"
        case_name = str(entry["case_name"])
        detail = service.get_imported_run(imported.local_import_id)
        _assert_m9b_case(case_name, detail)
        details_by_scenario.setdefault(case_name, []).append(detail)
        if case_name == "non_synchronous_xyz_rpm_fallback":
            with ZipFile(_managed_path(project_path, imported)) as archive:
                rows = tuple(csv.DictReader(io.TextIOWrapper(archive.open("measurements.csv"), encoding="utf-8")))
            assert rows
            assert all(row["axis_synchrony"] == "non_synchronous" for row in rows)
            assert any(row["rpm_fallback_active"] == "true" for row in rows)
        imported_ids.append(imported.local_import_id)

    shared = details_by_scenario["shared_specimen_pmn_rpt_rbd"]
    assert {item.summary.mode for item in shared} == {"pmn", "rpt", "rbd"}
    assert len({item.summary.source_specimen_id for item in shared}) == 1
    assert len({item.summary.binding_revision for item in shared}) == 1
    distinct = details_by_scenario["same_marking_distinct_specimens"]
    assert len({item.summary.source_specimen_id for item in distinct}) == 2
    assert len({item.projection["sample_label"] for item in distinct}) == 1
    assert all(item.summary.local_specimen_id is None for item in (*shared, *distinct))

    items = service.list_imported_runs()
    assert len(items) == 21
    assert sum(item.package_kind == "final" for item in items) == 16
    assert sum(item.package_kind == "diagnostic_partial" for item in items) == 5
    details_before = {item_id: imported_run_detail_model(service.get_imported_run(item_id)).model_dump(mode="json") for item_id in imported_ids}
    service.close()

    service.open(path=str(project_path), application_instance_id="reopen")
    details_after = {item_id: imported_run_detail_model(service.get_imported_run(item_id)).model_dump(mode="json") for item_id in imported_ids}
    assert details_after == details_before
    assert len(service.list_imported_runs()) == 21
    service.close()


def test_exact_repeat_is_noop_without_duplicate_audit(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    source = _package("duplicate_import_key.r130run")
    first = _import(service, project_path, source)
    audit_before = _audit_count(project_path)

    repeated = _import(service, project_path, source)

    assert repeated.local_import_id == first.local_import_id
    assert repeated.imported_existing is True
    assert len(service.list_imported_runs()) == 1
    assert _audit_count(project_path) == audit_before
    service.close()


def test_same_package_revision_with_different_outer_hash_is_conflict(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    source = _package("duplicate_import_key.r130run")
    first = _import(service, project_path, source)
    report, facts = _validated(source)
    changed_facts = replace(facts, outer_package_sha256="0" * 64)
    staged = _stage(project_path, source)

    with pytest.raises(ProjectOperationError) as raised:
        service.register_imported_run(
            local_import_id=str(uuid4()),
            staged_path=staged,
            facts=changed_facts,
            report=report,
            deadline=None,
        )

    assert raised.value.code == "import_integrity_conflict"
    assert service.list_imported_runs() == (first,)
    assert not staged.exists()
    service.close()


def test_new_export_revision_coexists_with_previous_revision(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    source = _package("normal_final_rbd.r130run")
    first = _import(service, project_path, source)
    second_package = build_synthetic_r130run(
        tmp_path / "revision-2.r130run",
        manifest_mutator=lambda manifest: manifest.update(
            package_id=first.package_id,
            export_revision=2,
        ),
    )

    second = _import(service, project_path, second_package)

    assert second.package_id == first.package_id
    assert second.export_revision == 2
    assert second.local_import_id != first.local_import_id
    assert len(service.list_imported_runs()) == 2
    service.close()


def test_shared_and_distinct_source_specimen_identities_do_not_use_marking(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    shared = [_import(service, project_path, _package(f"shared_specimen_pmn_rpt_rbd-{mode}.r130run")) for mode in ("pmn", "rpt", "rbd")]
    distinct = [_import(service, project_path, _package(f"same_marking_distinct_specimens-{index}.r130run")) for index in (1, 2)]

    assert len({item.source_specimen_id for item in shared}) == 1
    assert len({item.binding_revision for item in shared}) == 1
    assert len({item.source_specimen_id for item in distinct}) == 2
    assert all(item.local_specimen_id is None for item in (*shared, *distinct))
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        assert connection.execute("SELECT count(*) FROM r130sh_specimen_bindings").fetchone()[0] == 3
    service.close()


def test_binding_and_enrichment_resolution_are_optimistic_and_audited(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    imported = _import(service, project_path, _package("normal_final_rbd.r130run"))
    project = service.get_overview()
    wheel = service.create_wheel(
        {
            "wheelModelId": str(uuid4()),
            "fullName": "Локальная модель",
            "designation": "",
            "nominalDiameterMm": None,
            "nominalSpeedRpm": None,
            "bladeCount": None,
            "geometryDescription": "",
            "compositionDescription": "",
            "materialDescription": "",
            "notes": "",
        },
        None,
    )
    specimen = service.create_specimen(
        {
            "specimenId": str(uuid4()),
            "wheelModelId": wheel.wheel_model_id,
            "identificationNumber": "LOCAL-001",
            "batchNumber": "",
            "marking": "",
            "manufacturedOn": None,
            "receivedOn": None,
            "workingDiameterMm": None,
            "initialConditionNotes": "",
            "notes": "",
        },
        None,
    )

    binding = service.bind_imported_run_specimen(
        source_specimen_id=imported.source_specimen_id,
        local_specimen_id=specimen.specimen_id,
        expected_revision=1,
        actor="local_user",
        reason="Подтверждено инженером",
        deadline=None,
    )
    assert binding.local_specimen_id == specimen.specimen_id
    assert binding.record_revision == 2
    with pytest.raises(ProjectOperationError, match="Binding") as stale:
        service.bind_imported_run_specimen(
            source_specimen_id=imported.source_specimen_id,
            local_specimen_id=None,
            expected_revision=1,
            actor="local_user",
            reason="stale",
            deadline=None,
        )
    assert stale.value.code == "revision_conflict"

    resolved = service.record_imported_run_resolution(
        resolution_id=str(uuid4()),
        local_import_id=imported.local_import_id,
        source_payload_path="run-summary.json",
        source_field="run_card.customer_name",
        target_entity_type="customer_profile",
        target_entity_id=project.project_id,
        target_field="fullName",
        decision="copied_to_analyst",
        actor="local_user",
        reason="",
        expected_target_revision=None,
        deadline=None,
    )
    customer = service.get_customer()
    assert customer is not None
    assert customer.full_name == "Лабораторный заказчик"
    assert len(resolved.enrichment_resolutions) == 1
    service.close()

    service.open(path=str(project_path), application_instance_id="reopen")
    assert service.get_imported_run_binding(imported.source_specimen_id).local_specimen_id == specimen.specimen_id
    assert len(service.get_imported_run(imported.local_import_id).enrichment_resolutions) == 1
    service.close()


def test_enrichment_copy_is_whitelisted_empty_only_and_idempotent(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    imported = _import(service, project_path, _package("normal_final_rbd.r130run"))
    wheel = service.create_wheel(
        {
            "wheelModelId": str(uuid4()),
            "fullName": "Локальная модель",
            "designation": "",
            "nominalDiameterMm": None,
            "nominalSpeedRpm": None,
            "bladeCount": None,
            "geometryDescription": "",
            "compositionDescription": "",
            "materialDescription": "",
            "notes": "",
        },
        None,
    )
    specimen = service.create_specimen(
        {
            "specimenId": str(uuid4()),
            "wheelModelId": wheel.wheel_model_id,
            "identificationNumber": "LOCAL-COPY",
            "batchNumber": "",
            "marking": "",
            "manufacturedOn": None,
            "receivedOn": None,
            "workingDiameterMm": None,
            "initialConditionNotes": "",
            "notes": "",
        },
        None,
    )

    wheel_resolution_id = str(uuid4())
    first = service.record_imported_run_resolution(
        resolution_id=str(uuid4()),
        local_import_id=imported.local_import_id,
        source_payload_path="run-summary.json",
        source_field="run_card.wheel_identifier",
        target_entity_type="wheel_model",
        target_entity_id=wheel.wheel_model_id,
        target_field="designation",
        decision="copied_to_analyst",
        actor="local_user",
        reason="Подтверждено по карточке испытания",
        expected_target_revision=wheel.record_revision,
        deadline=None,
    )
    copied_wheel = service.get_wheel(wheel.wheel_model_id)
    assert copied_wheel.designation == first.projection["wheel_identifier"]
    audit_after_first = _audit_count(project_path)

    repeated = service.record_imported_run_resolution(
        resolution_id=wheel_resolution_id,
        local_import_id=imported.local_import_id,
        source_payload_path="run-summary.json",
        source_field="run_card.wheel_identifier",
        target_entity_type="wheel_model",
        target_entity_id=wheel.wheel_model_id,
        target_field="designation",
        decision="copied_to_analyst",
        actor="local_user",
        reason="Подтверждено по карточке испытания",
        expected_target_revision=wheel.record_revision,
        deadline=None,
    )
    assert repeated == first
    assert _audit_count(project_path) == audit_after_first

    service.record_imported_run_resolution(
        resolution_id=str(uuid4()),
        local_import_id=imported.local_import_id,
        source_payload_path="run-summary.json",
        source_field="sample_label",
        target_entity_type="specimen",
        target_entity_id=specimen.specimen_id,
        target_field="marking",
        decision="copied_to_analyst",
        actor="local_user",
        reason="Явное заполнение новой analyst entity",
        expected_target_revision=specimen.record_revision,
        deadline=None,
    )
    copied_specimen = service.get_specimen(specimen.specimen_id)
    assert copied_specimen.marking != ""

    service.record_imported_run_resolution(
        resolution_id=str(uuid4()),
        local_import_id=imported.local_import_id,
        source_payload_path="plan/original.json",
        source_field="source_values.nominal_rpm",
        target_entity_type="wheel_model",
        target_entity_id=wheel.wheel_model_id,
        target_field="nominalSpeedRpm",
        decision="copied_to_analyst",
        actor="local_user",
        reason="Копирование выбранного поля в пустую карточку",
        expected_target_revision=copied_wheel.record_revision,
        deadline=None,
    )
    copied_wheel = service.get_wheel(wheel.wheel_model_id)
    assert copied_wheel.nominal_speed_rpm is not None

    project = service.get_overview()
    service.record_imported_run_resolution(
        resolution_id=str(uuid4()),
        local_import_id=imported.local_import_id,
        source_payload_path="run-summary.json",
        source_field="run_card.customer_name",
        target_entity_type="customer_profile",
        target_entity_id=project.project_id,
        target_field="fullName",
        decision="copied_to_analyst",
        actor="local_user",
        reason="Создать новую карточку заказчика",
        expected_target_revision=None,
        deadline=None,
    )
    customer = service.get_customer()
    assert customer is not None
    service.record_imported_run_resolution(
        resolution_id=str(uuid4()),
        local_import_id=imported.local_import_id,
        source_payload_path="run-summary.json",
        source_field="run_card.customer_address",
        target_entity_type="customer_profile",
        target_entity_id=project.project_id,
        target_field="legalAddress",
        decision="copied_to_analyst",
        actor="local_user",
        reason="Заполнить выбранный пустой адрес",
        expected_target_revision=customer.record_revision,
        deadline=None,
    )
    updated_customer = service.get_customer()
    assert updated_customer is not None
    assert updated_customer.legal_address != ""

    second_import = _import(service, project_path, _package("normal_final_pmn.r130run"))
    with pytest.raises(ProjectOperationError, match="Непустое analyst value") as overwrite:
        service.record_imported_run_resolution(
            resolution_id=str(uuid4()),
            local_import_id=second_import.local_import_id,
            source_payload_path="run-summary.json",
            source_field="run_card.wheel_identifier",
            target_entity_type="wheel_model",
            target_entity_id=wheel.wheel_model_id,
            target_field="designation",
            decision="copied_to_analyst",
            actor="local_user",
            reason="Не должно перезаписать",
            expected_target_revision=copied_wheel.record_revision,
            deadline=None,
        )
    assert overwrite.value.code == "validation_error"

    with pytest.raises(ProjectOperationError, match="Source/enrichment relationship"):
        service.record_imported_run_resolution(
            resolution_id=str(uuid4()),
            local_import_id=imported.local_import_id,
            source_payload_path="measurements.csv",
            source_field="rpm",
            target_entity_type="specimen",
            target_entity_id=specimen.specimen_id,
            target_field="marking",
            decision="use_source",
            actor="local_user",
            reason="Недопустимая связь",
            expected_target_revision=None,
            deadline=None,
        )
    service.close()


def test_reopen_removes_only_exact_import_orphans(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    imported = _import(service, project_path, _package("normal_final_pmn.r130run"))
    service.close()
    staging = project_path / "imports" / "r130sh" / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    orphan_stage = staging / f"{uuid4()}.part"
    unrelated_stage = staging / "keep.tmp"
    orphan_stage.write_bytes(b"operation-owned")
    unrelated_stage.write_bytes(b"unrelated")
    orphan_final = project_path / "imports" / "r130sh" / str(uuid4()) / "rev-7" / f"{'a' * 64}.r130run"
    orphan_final.parent.mkdir(parents=True)
    orphan_final.write_bytes(b"orphan")

    service.open(path=str(project_path), application_instance_id="orphan-cleanup")

    assert not orphan_stage.exists()
    assert unrelated_stage.exists()
    assert not orphan_final.exists()
    assert _managed_path(project_path, imported).exists()
    service.close()


def test_enrichment_copy_rejects_missing_archived_and_incomplete_targets(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    imported = _import(service, project_path, _package("normal_final_rbd.r130run"))
    with pytest.raises(ProjectOperationError) as missing:
        service.record_imported_run_resolution(
            resolution_id=str(uuid4()),
            local_import_id=imported.local_import_id,
            source_payload_path="run-summary.json",
            source_field="run_card.wheel_identifier",
            target_entity_type="wheel_model",
            target_entity_id=str(uuid4()),
            target_field="designation",
            decision="copied_to_analyst",
            actor="local_user",
            reason="Несуществующая цель",
            expected_target_revision=1,
            deadline=None,
        )
    assert missing.value.code == "entity_not_found"

    wheel = service.create_wheel(
        {
            "wheelModelId": str(uuid4()),
            "fullName": "Архивная модель",
            "designation": "",
            "nominalDiameterMm": None,
            "nominalSpeedRpm": None,
            "bladeCount": None,
            "geometryDescription": "",
            "compositionDescription": "",
            "materialDescription": "",
            "notes": "",
        },
        None,
    )
    archived = service.set_wheel_archived(wheel.wheel_model_id, wheel.record_revision, True, None)
    with pytest.raises(ProjectOperationError) as archived_target:
        service.record_imported_run_resolution(
            resolution_id=str(uuid4()),
            local_import_id=imported.local_import_id,
            source_payload_path="run-summary.json",
            source_field="run_card.wheel_identifier",
            target_entity_type="wheel_model",
            target_entity_id=wheel.wheel_model_id,
            target_field="designation",
            decision="copied_to_analyst",
            actor="local_user",
            reason="Архивная цель",
            expected_target_revision=archived.record_revision,
            deadline=None,
        )
    assert archived_target.value.code == "entity_archived"

    project = service.get_overview()
    with pytest.raises(ProjectOperationError, match="Сначала явно создайте CustomerProfile"):
        service.record_imported_run_resolution(
            resolution_id=str(uuid4()),
            local_import_id=imported.local_import_id,
            source_payload_path="run-summary.json",
            source_field="run_card.customer_address",
            target_entity_type="customer_profile",
            target_entity_id=project.project_id,
            target_field="legalAddress",
            decision="copied_to_analyst",
            actor="local_user",
            reason="Адрес без обязательного имени",
            expected_target_revision=None,
            deadline=None,
        )
    service.close()


def test_missing_or_modified_archive_does_not_block_project_open(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    first = _import(service, project_path, _package("normal_final_pmn.r130run"))
    second = _import(service, project_path, _package("normal_final_rbd.r130run"))
    service.close()
    first_path = _managed_path(project_path, first)
    second_path = _managed_path(project_path, second)
    first_path.unlink()
    second_path.write_bytes(b"modified")

    service.open(path=str(project_path), application_instance_id="broken-source")

    assert service.verify_imported_run_source(first.local_import_id) == "missing"
    assert service.verify_imported_run_source(second.local_import_id) == "modified"
    assert len(service.list_imported_runs()) == 2
    service.close()


def test_same_size_source_change_invalidates_cached_integrity_until_explicit_verify(
    tmp_path: Path,
) -> None:
    service, project_path = _project(tmp_path)
    imported = _import(service, project_path, _package("normal_final_pmn.r130run"))
    managed_path = _managed_path(project_path, imported)
    original = managed_path.read_bytes()
    assert service.verify_imported_run_source(imported.local_import_id) == "verified"

    changed = bytes([original[0] ^ 1]) + original[1:]
    previous_mtime = managed_path.stat().st_mtime_ns
    managed_path.write_bytes(changed)
    os.utime(managed_path, ns=(previous_mtime + 1_000_000, previous_mtime + 1_000_000))

    assert service.list_imported_runs()[0].source_integrity == "modified"
    assert service.verify_imported_run_source(imported.local_import_id) == "modified"
    managed_path.write_bytes(original)
    os.utime(managed_path, ns=(previous_mtime + 2_000_000, previous_mtime + 2_000_000))
    assert service.list_imported_runs()[0].source_integrity == "modified"
    assert service.verify_imported_run_source(imported.local_import_id) == "verified"
    service.close()


def test_imported_run_reads_honor_request_deadline(tmp_path: Path) -> None:
    service, _project_path = _project(tmp_path)
    with pytest.raises(ProjectOperationError) as expired:
        service.list_imported_runs(RequestDeadline.start(0))
    assert expired.value.code == "timeout"
    service.close()


def test_import_publication_hash_receives_operation_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project_path = _project(tmp_path)
    source = _package("normal_final_pmn.r130run")
    report, facts = _validated(source)
    deadline = RequestDeadline.start(30_000)
    observed_deadlines: list[RequestDeadline | None] = []

    def observed_hash(path: Path, received: RequestDeadline | None = None) -> str:
        observed_deadlines.append(received)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    monkeypatch.setattr(r130sh_sources_module, "_sha256_file", observed_hash)
    service.register_imported_run(
        local_import_id=str(uuid4()),
        staged_path=_stage(project_path, source),
        facts=facts,
        report=report,
        deadline=deadline,
    )

    assert observed_deadlines == [deadline]
    service.close()


def test_committed_import_success_does_not_depend_on_post_commit_detail_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project_path = _project(tmp_path)
    source = _package("normal_final_pmn.r130run")
    report, facts = _validated(source)

    def forbidden_post_commit_get(*_args: object, **_kwargs: object) -> ImportedRunDetail:
        raise AssertionError("post_commit_detail_read")

    monkeypatch.setattr(r130sh_sources_module.R130shSourceRepository, "get", forbidden_post_commit_get)
    imported = service.register_imported_run(
        local_import_id=str(uuid4()),
        staged_path=_stage(project_path, source),
        facts=facts,
        report=report,
        deadline=None,
    )

    assert imported.outer_package_sha256 == facts.outer_package_sha256
    service.close()


def test_source_tables_and_resolution_rows_are_immutable(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    imported = _import(service, project_path, _package("normal_final_pmn.r130run"))
    service.record_imported_run_resolution(
        resolution_id=str(uuid4()),
        local_import_id=imported.local_import_id,
        source_payload_path="run-summary.json",
        source_field="run_card.customer_name",
        target_entity_type="customer_profile",
        target_entity_id=str(uuid4()),
        target_field="fullName",
        decision="use_source",
        actor="local_user",
        reason="Проверка неизменяемого provenance",
        expected_target_revision=None,
        deadline=None,
    )
    service.close()

    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        for statement in (
            "UPDATE r130sh_sources SET producer_name='changed'",
            "DELETE FROM r130sh_sources",
            "UPDATE r130sh_source_inventory SET media_type='changed'",
            "DELETE FROM r130sh_source_inventory",
            "UPDATE r130sh_run_projections SET mode='rbd'",
            "DELETE FROM r130sh_run_projections",
            "UPDATE r130sh_enrichment_resolutions SET reason='changed'",
            "DELETE FROM r130sh_enrichment_resolutions",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement)
        assert (
            connection.execute(
                "SELECT outer_package_sha256 FROM r130sh_sources WHERE local_import_id=?",
                (imported.local_import_id,),
            ).fetchone()[0]
            == imported.outer_package_sha256
        )


def test_enrichment_resolution_limit_rejects_before_commit(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    imported = _import(service, project_path, _package("normal_final_pmn.r130run"))
    for index in range(32):
        service.record_imported_run_resolution(
            resolution_id=str(uuid4()),
            local_import_id=imported.local_import_id,
            source_payload_path="run-summary.json",
            source_field="run_card.customer_name",
            target_entity_type="customer_profile",
            target_entity_id=str(uuid4()),
            target_field="fullName",
            decision="use_source",
            actor="local_user",
            reason=f"Решение {index}",
            expected_target_revision=None,
            deadline=None,
        )
    audit_before = _audit_count(project_path)

    with pytest.raises(ProjectOperationError) as limit:
        service.record_imported_run_resolution(
            resolution_id=str(uuid4()),
            local_import_id=imported.local_import_id,
            source_payload_path="run-summary.json",
            source_field="run_card.customer_name",
            target_entity_type="customer_profile",
            target_entity_id=str(uuid4()),
            target_field="fullName",
            decision="use_source",
            actor="local_user",
            reason="Лишнее решение",
            expected_target_revision=None,
            deadline=None,
        )
    assert limit.value.code == "validation_error"
    assert _audit_count(project_path) == audit_before
    assert len(service.get_imported_run(imported.local_import_id).enrichment_resolutions) == 32
    service.close()


def test_reopen_rejects_absolute_inventory_path_before_renderer_read(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    imported = _import(service, project_path, _package("normal_final_pmn.r130run"))
    service.close()
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        trigger_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='r130sh_source_inventory_no_update'",
            ).fetchone()[0],
        )
        connection.execute("DROP TRIGGER r130sh_source_inventory_no_update")
        connection.execute(
            "UPDATE r130sh_source_inventory SET path='C:/private/source.bin' WHERE local_import_id=? AND path=(SELECT min(path) FROM r130sh_source_inventory WHERE local_import_id=?)",
            (imported.local_import_id, imported.local_import_id),
        )
        connection.execute(trigger_sql)
        connection.commit()

    with pytest.raises(ProjectOperationError) as corrupt:
        service.open(path=str(project_path), application_instance_id="tampered-inventory")
    assert corrupt.value.code == "corrupt_project"


def test_renderer_models_contain_no_absolute_or_managed_path(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    imported = _import(service, project_path, _package("normal_final_pmn.r130run"))
    payload = imported_run_detail_model(service.get_imported_run(imported.local_import_id)).model_dump_json()

    assert str(project_path) not in payload
    assert "managedRelativePath" not in payload
    assert "sourcePath" not in payload
    assert "calculationEligible" not in payload
    assert "readyForCalculation" not in payload
    service.close()


def test_import_boundary_rejects_invalid_ids_and_unknown_sources(tmp_path: Path) -> None:
    service, _project_path = _project(tmp_path)
    for operation in (
        lambda: service.get_imported_run("not-a-uuid"),
        lambda: service.verify_imported_run_source("not-a-uuid"),
        lambda: service.get_imported_run("019d3c80-3d21-7a65-8e5a-111111111111"),
    ):
        with pytest.raises(ProjectOperationError) as invalid_id:
            operation()
        assert invalid_id.value.code == "validation_error"

    with pytest.raises(ProjectOperationError) as invalid_source_identity:
        service.bind_imported_run_specimen(
            source_specimen_id="invalid\nidentity",
            local_specimen_id=None,
            expected_revision=1,
            actor="local_user",
            reason="invalid",
            deadline=None,
        )
    assert invalid_source_identity.value.code == "corrupt_project"

    unknown_import_id = str(uuid4())
    with pytest.raises(ProjectOperationError) as unknown_import:
        service.get_imported_run(unknown_import_id)
    assert unknown_import.value.code == "entity_not_found"
    with pytest.raises(ProjectOperationError) as unknown_verify:
        service.verify_imported_run_source(unknown_import_id)
    assert unknown_verify.value.code == "entity_not_found"
    service.close()


def _project(tmp_path: Path) -> tuple[ProjectService, Path]:
    service = ProjectService()
    project_path = (tmp_path / "acceptance.irproj").resolve()
    service.create(
        path=str(project_path),
        application_instance_id="tests",
        application_version="0.0.0-test",
        name="M9b acceptance",
        project_number="",
        description="",
        status="draft",
    )
    return service, project_path


def _assert_m9b_case(case_name: str, detail: ImportedRunDetail) -> None:
    summary = detail.summary
    projection = detail.projection
    expected_terminal = EXPECTED_TERMINAL.get(case_name)
    if expected_terminal is not None:
        package_kind, technical_status, termination_reason, specimen_outcome, run_validity = expected_terminal
        assert summary.package_kind == package_kind
        assert summary.technical_status == technical_status
        assert summary.termination_reason == termination_reason
        if specimen_outcome is not None:
            assert summary.specimen_outcome == specimen_outcome
        if run_validity is not None:
            assert summary.run_validity == run_validity
    if case_name == "first_vibration_trip_inspection_amendment_completion":
        assert _integer(projection["event_count"]) >= 1
        assert _integer(projection["inspection_count"]) >= 1
        assert _integer(projection["amendment_count"]) >= 1
    elif case_name == "repeated_vibration_trip":
        assert projection["resume_available"] is False
        assert _integer(projection["event_count"]) >= 2
        assert _integer(projection["inspection_count"]) >= 2
    elif case_name == "storage_failure_data_gap":
        assert summary.data_completeness == "gaps_detected"
    elif case_name == "environment_deviation_confirmation":
        environment = OBJECT_ADAPTER.validate_python(projection["environment_summary"])
        confirmation = OBJECT_ADAPTER.validate_python(environment["confirmation"])
        actor = OBJECT_ADAPTER.validate_python(confirmation["actor"])
        assert actor["full_name"]
        assert confirmation["reason"]
        assert summary.run_validity == "valid"
    elif case_name == "diagnostic_partial":
        assert summary.package_kind == "diagnostic_partial"
        assert projection["partial_reasons"]
    elif case_name == "exact_methodical_rounding":
        original_plan = OBJECT_ADAPTER.validate_python(projection["original_plan_summary"])
        requirements = OBJECT_ADAPTER.validate_python(original_plan["methodical_requirements"])
        targets = OBJECT_ADAPTER.validate_python(original_plan["execution_targets"])
        assert requirements["required_cycles_exact"] == "1500.3"
        assert requirements["required_steady_duration_s_exact"] == "60.012"
        assert targets["target_cycles"] == 1501
        assert targets["target_steady_duration_s"] == "60.04"
        assert targets["total_duration_s"] == "70.04"
    elif case_name == "measurement_retained_after_attempt_rejection":
        assert _integer(projection["measurement_count"]) > _integer(projection["accepted_measurement_count"])
        assert any(item["path"] == "measurements.csv" for item in detail.inventory)
    elif case_name == "duplicate_import_key":
        assert summary.package_id
        assert summary.export_revision == 1
    elif case_name == "non_synchronous_xyz_rpm_fallback":
        assert _integer(projection["measurement_count"]) > 0
        assert any(item["path"] == "measurements.csv" for item in detail.inventory)
    elif case_name in {"same_marking_distinct_specimens", "shared_specimen_pmn_rpt_rbd"}:
        assert summary.source_specimen_id
        assert summary.local_specimen_id is None


def _integer(value: object) -> int:
    assert isinstance(value, int) and not isinstance(value, bool)
    return value


def _package(name: str) -> Path:
    return M9A_ROOT / "packages" / name


def _validated(path: Path) -> tuple[RunPackageValidationReport, M9aPackageFacts]:
    report = RunPackageValidator().validate(
        path,
        ValidationControl(Event(), monotonic() + 30, _ignore_validation_progress),
    )
    return report, read_m9a_package_facts(path, report)


def _ignore_validation_progress(
    _phase: str,
    _completed_bytes: int,
    _total_bytes: int,
    _completed_entries: int,
    _total_entries: int,
) -> None:
    return None


def _stage(project_path: Path, source: Path) -> Path:
    value = project_path / "imports" / "r130sh" / ".staging" / f"{uuid4()}.part"
    value.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, value)
    return value


def _import(
    service: ProjectService,
    project_path: Path,
    source: Path,
) -> ImportedRunSummary:
    report, facts = _validated(source)
    return service.register_imported_run(
        local_import_id=str(uuid4()),
        staged_path=_stage(project_path, source),
        facts=facts,
        report=report,
        deadline=None,
    )


def _import_via_job(
    service: ProjectService,
    project_path: Path,
    source: Path,
    *,
    allow_diagnostic_partial: bool,
) -> ImportedRunSummary:
    manager = RunPackageImportJobManager()
    job_id = str(uuid4())
    manager.start(
        job_id=job_id,
        project_path=project_path,
        source_path=source,
        allow_diagnostic_partial=allow_diagnostic_partial,
    )

    def finalize(
        local_import_id: str,
        staged_path: Path,
        facts: M9aPackageFacts,
        report: RunPackageValidationReport,
        deadline: RequestDeadline | None,
    ) -> ImportedRunSummary:
        return service.register_imported_run(
            local_import_id=local_import_id,
            staged_path=staged_path,
            facts=facts,
            report=report,
            deadline=deadline,
        )

    expires_at = monotonic() + 10
    snapshot = manager.get(job_id, finalize=finalize, deadline=None)
    while snapshot.state not in {"completed", "failed", "cancelled"} and monotonic() < expires_at:
        sleep(0.01)
        snapshot = manager.get(job_id, finalize=finalize, deadline=None)
    assert snapshot.state == "completed"
    assert snapshot.result is not None
    local_import_id = snapshot.result.importedRun.localImportId
    manager.discard(job_id)
    return service.get_imported_run(local_import_id).summary


def _audit_count(project_path: Path) -> int:
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        return int(connection.execute("SELECT count(*) FROM project_audit_events").fetchone()[0])


def _managed_path(project_path: Path, imported: ImportedRunSummary) -> Path:
    return project_path / "imports" / "r130sh" / imported.package_id / f"rev-{imported.export_revision}" / f"{imported.outer_package_sha256}.r130run"
