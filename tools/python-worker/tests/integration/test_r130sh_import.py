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
from impeller_reliability.persistence import r130sh_sources as r130sh_sources_module, reliability_domain as reliability_domain_module
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.r130sh_sources import ImportedRunDetail, ImportedRunSummary
from impeller_reliability.persistence.reliability_domain import ReliabilityDomainRepository
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
        assert imported.validator_version == "m03b.2"
        assert imported.validation_contract_commit == "09097561a6a58b1663a6912357a3c8d1daf7f28c"
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


def test_diagnostic_partial_resume_available_true_survives_production_import_and_reopen(
    tmp_path: Path,
) -> None:
    service, project_path = _project(tmp_path)
    base = build_synthetic_r130run(tmp_path / "partial-base.r130run")
    with ZipFile(base) as archive:
        summary = OBJECT_ADAPTER.validate_json(archive.read("run-summary.json"))
    summary["package_kind"] = "diagnostic_partial"
    summary["partial_reasons"] = ["run_can_continue_on_stand"]
    summary["resume_available"] = True
    summary["finished_at_utc"] = None
    package = build_synthetic_r130run(
        tmp_path / "resume-available-partial.r130run",
        payload_overrides={
            "run-summary.json": (json.dumps(summary, ensure_ascii=False) + "\n").encode("utf-8"),
        },
        manifest_mutator=lambda manifest: manifest.update(
            package_kind="diagnostic_partial",
        ),
    )

    imported = _import_via_job(
        service,
        project_path,
        package,
        allow_diagnostic_partial=True,
    )
    before = service.get_imported_run(imported.local_import_id)
    assert before.summary.package_kind == "diagnostic_partial"
    assert before.projection["resume_available"] is True
    assert before.projection["partial_reasons"] == ["run_can_continue_on_stand"]
    assert before.summary.technical_status == summary["technical_status"]
    assert before.summary.run_validity == summary["run_validity"]
    assert before.summary.data_completeness == summary["data_completeness"]
    service.close()

    service.open(path=str(project_path), application_instance_id="resume-available-reopen")
    after = service.get_imported_run(imported.local_import_id)
    assert after == before
    assert after.projection["resume_available"] is True
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


def test_materialized_reliability_execution_preserves_source_and_reopens(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    imported = _import(service, project_path, _package("normal_final_rbd.r130run"))
    wheel = service.create_wheel(
        {
            "wheelModelId": str(uuid4()),
            "fullName": "Колесо для анализа",
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
            "identificationNumber": "M04A-001",
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
    with pytest.raises(ProjectOperationError) as unbound:
        service.materialize_reliability_execution(imported.local_import_id, None)
    assert unbound.value.code == "validation_error"
    service.bind_imported_run_specimen(
        source_specimen_id=imported.source_specimen_id,
        local_specimen_id=specimen.specimen_id,
        expected_revision=1,
        actor="local_user",
        reason="Подтверждён инженерный объект анализа",
        deadline=None,
    )

    execution = service.materialize_reliability_execution(imported.local_import_id, None)
    with pytest.raises(ProjectOperationError) as rebound_after_materialization:
        service.bind_imported_run_specimen(
            source_specimen_id=imported.source_specimen_id,
            local_specimen_id=None,
            expected_revision=2,
            actor="local_user",
            reason="Попытка изменить историческую привязку",
            deadline=None,
        )
    assert rebound_after_materialization.value.code == "entity_in_use"
    repeated = service.materialize_reliability_execution(imported.local_import_id, None)
    assert repeated == execution
    assert execution.local_specimen_id == specimen.specimen_id
    assert execution.method == "rbd"
    assert execution.lifecycle_status == "completed"
    assert execution.failure_observations == ()
    page = service.list_reliability_execution_page(wheel.wheel_model_id, None, 25, None)
    assert [item.execution_id for item in page.items] == [execution.execution_id]
    assert page.next_cursor is None
    assert service.get_reliability_execution(execution.execution_id, None) == execution
    archived_source = _import(service, project_path, _package("normal_final_pmn.r130run"))
    service.bind_imported_run_specimen(
        source_specimen_id=archived_source.source_specimen_id,
        local_specimen_id=specimen.specimen_id,
        expected_revision=2,
        actor="local_user",
        reason="Проверка запрета archived Specimen",
        deadline=None,
    )
    service.set_specimen_archived(specimen.specimen_id, specimen.record_revision, True, None)
    assert service.materialize_reliability_execution(imported.local_import_id, None) == execution
    with pytest.raises(ProjectOperationError) as archived:
        service.materialize_reliability_execution(archived_source.local_import_id, None)
    assert archived.value.code == "entity_archived"

    source_before = _source_snapshot(project_path, imported.local_import_id)
    service.close()
    service.open(path=str(project_path), application_instance_id="reopen")
    assert service.get_reliability_execution(execution.execution_id, None) == execution
    assert _source_snapshot(project_path, imported.local_import_id) == source_before
    service.close()
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        trigger_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='reliability_test_executions_no_update'",
            ).fetchone()[0]
        )
        connection.execute("DROP TRIGGER reliability_test_executions_no_update")
        connection.execute(
            "UPDATE reliability_test_executions SET result_summary_json='{}' WHERE execution_id=?",
            (execution.execution_id,),
        )
        connection.execute(trigger_sql)
        connection.commit()
    with pytest.raises(ProjectOperationError) as corrupted:
        service.open(path=str(project_path), application_instance_id="tampered-derived-snapshot")
    assert corrupted.value.code == "corrupt_project"


def test_reliability_failure_observation_does_not_turn_technical_stop_into_specimen_failure(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    imported = _import(
        service,
        project_path,
        _package("device_failure.r130run"),
    )
    wheel = service.create_wheel(
        {
            "wheelModelId": str(uuid4()),
            "fullName": "Колесо для interruption",
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
            "identificationNumber": "M04A-002",
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
    service.bind_imported_run_specimen(
        source_specimen_id=imported.source_specimen_id,
        local_specimen_id=specimen.specimen_id,
        expected_revision=1,
        actor="local_user",
        reason="Подтверждён инженерный объект анализа",
        deadline=None,
    )
    execution = service.materialize_reliability_execution(imported.local_import_id, None)
    assert execution.lifecycle_status == "interrupted"
    assert len(execution.failure_observations) == 1
    observation = execution.failure_observations[0]
    assert observation.failure_type == "technical_interruption"
    assert observation.subject_kind == "unknown"
    assert observation.cycles_at_failure is None
    assert execution.result_summary["acceptedElapsedS"] == "0"
    assert observation.duration_s is None
    assert observation.vibration_summary["available"] is False
    assert service.get_reliability_execution(execution.execution_id, None) == execution

    partial = _import(service, project_path, _package("diagnostic_partial.r130run"))
    service.bind_imported_run_specimen(
        source_specimen_id=partial.source_specimen_id,
        local_specimen_id=specimen.specimen_id,
        expected_revision=2,
        actor="local_user",
        reason="Проверка отсутствующего технического факта",
        deadline=None,
    )
    partial_execution = service.materialize_reliability_execution(partial.local_import_id, None)
    assert partial_execution.lifecycle_status == "interrupted"
    assert partial_execution.failure_observations == ()
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        for statement in (
            "UPDATE reliability_test_executions SET method='pmn'",
            "DELETE FROM reliability_test_executions",
            "UPDATE failure_observations SET rpm='1'",
            "DELETE FROM failure_observations",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement)
    service.close()


def test_m04b_observation_and_dataset_versions_are_explicit_immutable_and_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project_path = _project(tmp_path)
    imported = _import(service, project_path, _package("normal_final_rbd.r130run"))
    wheel = service.create_wheel(
        {
            "wheelModelId": str(uuid4()),
            "fullName": "Колесо M04B",
            "designation": "РБД-01",
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
            "identificationNumber": "M04B-001",
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
    service.bind_imported_run_specimen(
        source_specimen_id=imported.source_specimen_id,
        local_specimen_id=specimen.specimen_id,
        expected_revision=1,
        actor="local_user",
        reason="Подтверждён образец",
        deadline=None,
    )
    execution = service.materialize_reliability_execution(imported.local_import_id, None)
    document_id = str(uuid4())
    document = service.create_case_document(
        document_id,
        {
            "documentKind": "typical_test_method",
            "title": "ПМИ Р130У",
            "designation": "ПМИ Р130У",
            "revisionLabel": "01",
            "documentDate": "2024-07-02",
            "issuer": "ЛИЦ ВВУ",
            "notes": "",
        },
        (wheel.wheel_model_id,),
        (specimen.specimen_id,),
        None,
    )
    observation_id = str(uuid4())
    version_id = str(uuid4())
    first = service.create_reliability_observation_version(
        observation_id=observation_id,
        observation_version_id=version_id,
        execution_id=execution.execution_id,
        expected_previous_version_id=None,
        classification="right_censored",
        endpoint_kind="right_bound",
        metric_kind="rbd_steady_rotation_time",
        metric_unit="hours",
        metric_origin="analyst_provided",
        lower_value="12.5",
        upper_value=None,
        origin_basis="Начало зачтённого установившегося вращения по разделу 10",
        endpoint_basis="Граница наблюдения по записи инженера",
        document_id=document.case_document_id,
        document_locator="Раздел 10; журнал испытания, строка 42",
        failure_ids=(),
        actor="local_user",
        reason="Отказ не установлен до документированной границы",
        deadline=None,
    )
    assert first.disposition == "created"
    assert first.version.classification == "right_censored"
    assert first.version.lower_value == "12.5"
    assert first.version.document_snapshot.record_revision == 1
    audit_before_retry = _audit_count(project_path)
    repeated = service.create_reliability_observation_version(
        observation_id=observation_id,
        observation_version_id=version_id,
        execution_id=execution.execution_id,
        expected_previous_version_id=None,
        classification="right_censored",
        endpoint_kind="right_bound",
        metric_kind="rbd_steady_rotation_time",
        metric_unit="hours",
        metric_origin="analyst_provided",
        lower_value="12.5",
        upper_value=None,
        origin_basis="Начало зачтённого установившегося вращения по разделу 10",
        endpoint_basis="Граница наблюдения по записи инженера",
        document_id=document.case_document_id,
        document_locator="Раздел 10; журнал испытания, строка 42",
        failure_ids=(),
        actor="local_user",
        reason="Отказ не установлен до документированной границы",
        deadline=None,
    )
    assert repeated.disposition == "existing"
    assert repeated.version == first.version
    assert _audit_count(project_path) == audit_before_retry
    with pytest.raises(ProjectOperationError) as conflicting_retry:
        service.create_reliability_observation_version(
            observation_id=observation_id,
            observation_version_id=version_id,
            execution_id=execution.execution_id,
            expected_previous_version_id=None,
            classification="right_censored",
            endpoint_kind="right_bound",
            metric_kind="rbd_steady_rotation_time",
            metric_unit="hours",
            metric_origin="analyst_provided",
            lower_value="13",
            upper_value=None,
            origin_basis="Начало зачтённого установившегося вращения по разделу 10",
            endpoint_basis="Граница наблюдения по записи инженера",
            document_id=document.case_document_id,
            document_locator="Раздел 10; журнал испытания, строка 42",
            failure_ids=(),
            actor="local_user",
            reason="Отказ не установлен до документированной границы",
            deadline=None,
        )
    assert conflicting_retry.value.code == "revision_conflict"

    dataset_id = str(uuid4())
    dataset_version_id = str(uuid4())
    dataset = service.create_reliability_dataset_version(
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        wheel_model_id=wheel.wheel_model_id,
        expected_previous_version_id=None,
        title="РБД — подтверждённая выборка",
        method="rbd",
        metric_kind="rbd_steady_rotation_time",
        metric_unit="hours",
        population_basis="Рабочие колёса модели РБД-01",
        methodology_basis="ПМИ Р130У, редакция 01",
        comparability_basis="Одинаковый метод РБД; условия отобраны инженером",
        decisions=(
            {
                "observationVersionId": version_id,
                "decision": "included",
                "reason": "Документированная правая граница наблюдения",
            },
        ),
        actor="local_user",
        reason="Первая зафиксированная выборка",
        deadline=None,
    )
    assert dataset.disposition == "created"
    assert dataset.version.members[0].policy_eligibility == "eligible"
    assert dataset.version.members[0].decision == "included"

    second = service.create_reliability_observation_version(
        observation_id=observation_id,
        observation_version_id=str(uuid4()),
        execution_id=execution.execution_id,
        expected_previous_version_id=version_id,
        classification="invalid",
        endpoint_kind="unavailable",
        metric_kind=None,
        metric_unit=None,
        metric_origin=None,
        lower_value=None,
        upper_value=None,
        origin_basis="Не установлено",
        endpoint_basis="Не установлено",
        document_id=document.case_document_id,
        document_locator="Заключение инженера",
        failure_ids=(),
        actor="local_user",
        reason="Документированная граница признана неприменимой",
        deadline=None,
    )
    assert second.version.version_number == 2
    with pytest.raises(ProjectOperationError) as stale_observation:
        service.create_reliability_observation_version(
            observation_id=observation_id,
            observation_version_id=str(uuid4()),
            execution_id=execution.execution_id,
            expected_previous_version_id=version_id,
            classification="invalid",
            endpoint_kind="unavailable",
            metric_kind=None,
            metric_unit=None,
            metric_origin=None,
            lower_value=None,
            upper_value=None,
            origin_basis="Не установлено",
            endpoint_basis="Не установлено",
            document_id=document.case_document_id,
            document_locator="Заключение инженера",
            failure_ids=(),
            actor="local_user",
            reason="Устаревший черновик",
            deadline=None,
        )
    assert stale_observation.value.code == "revision_conflict"
    with pytest.raises(ProjectOperationError) as ineligible_inclusion:
        service.create_reliability_dataset_version(
            dataset_id=dataset_id,
            dataset_version_id=str(uuid4()),
            wheel_model_id=wheel.wheel_model_id,
            expected_previous_version_id=dataset_version_id,
            title="РБД — уточнённая выборка",
            method="rbd",
            metric_kind="rbd_steady_rotation_time",
            metric_unit="hours",
            population_basis="Рабочие колёса модели РБД-01",
            methodology_basis="ПМИ Р130У, редакция 01",
            comparability_basis="Одинаковый метод РБД",
            decisions=(
                {
                    "observationVersionId": second.version.observation_version_id,
                    "decision": "included",
                    "reason": "Попытка включить invalid",
                },
            ),
            actor="local_user",
            reason="Проверка политики",
            deadline=None,
        )
    assert ineligible_inclusion.value.code == "validation_error"
    dataset_second = service.create_reliability_dataset_version(
        dataset_id=dataset_id,
        dataset_version_id=str(uuid4()),
        wheel_model_id=wheel.wheel_model_id,
        expected_previous_version_id=dataset_version_id,
        title="РБД — уточнённая выборка",
        method="rbd",
        metric_kind="rbd_steady_rotation_time",
        metric_unit="hours",
        population_basis="Рабочие колёса модели РБД-01",
        methodology_basis="ПМИ Р130У, редакция 01",
        comparability_basis="Одинаковый метод РБД",
        decisions=(
            {
                "observationVersionId": second.version.observation_version_id,
                "decision": "excluded",
                "reason": "Наблюдение признано invalid",
            },
        ),
        actor="local_user",
        reason="Исправлен состав",
        deadline=None,
    )
    assert dataset_second.version.version_number == 2
    assert dataset_second.version.members[0].policy_eligibility == "ineligible"
    assert dataset_second.version.members[0].decision == "excluded"
    history_head = second
    for expected_version in range(3, 52):
        history_head = service.create_reliability_observation_version(
            observation_id=observation_id,
            observation_version_id=str(uuid4()),
            execution_id=execution.execution_id,
            expected_previous_version_id=history_head.version.observation_version_id,
            classification="invalid",
            endpoint_kind="unavailable",
            metric_kind=None,
            metric_unit=None,
            metric_origin=None,
            lower_value=None,
            upper_value=None,
            origin_basis="Не установлено",
            endpoint_basis="Не установлено",
            document_id=document.case_document_id,
            document_locator="Заключение инженера",
            failure_ids=(),
            actor="local_user",
            reason=f"Коррекция истории {expected_version}",
            deadline=None,
        )
        assert history_head.version.version_number == expected_version
    first_history_page = service.list_reliability_observation_versions(
        execution.execution_id,
        None,
    )
    assert len(first_history_page) == 50
    assert first_history_page[0] == history_head.version
    assert first_history_page[-1] == second.version
    history_cursor = first_history_page[-1].previous_version_id
    assert history_cursor is not None
    assert history_cursor == first.version.observation_version_id
    assert (
        service.get_reliability_observation_version(
            history_cursor,
            None,
        )
        == first.version
    )
    updated_document = service.update_case_document(
        document.case_document_id,
        document.record_revision,
        {
            "documentKind": "typical_test_method",
            "title": "ПМИ Р130У — уточнённая карточка",
            "designation": "ПМИ Р130У",
            "revisionLabel": "01",
            "documentDate": "2024-07-02",
            "issuer": "ЛИЦ ВВУ",
            "notes": "Метаданные изменены после интерпретации",
        },
        (wheel.wheel_model_id,),
        (specimen.specimen_id,),
        None,
    )
    assert updated_document.record_revision == 2
    alternate_document = service.create_case_document(
        str(uuid4()),
        {
            "documentKind": "other",
            "title": "Альтернативный протокол",
            "designation": "ПРОТ-02",
            "revisionLabel": "01",
            "documentDate": "2026-09-09",
            "issuer": "ЛИЦ ВВУ",
            "notes": "Не использовался для первой интерпретации",
        },
        (wheel.wheel_model_id,),
        (specimen.specimen_id,),
        None,
    )
    other_wheel = service.create_wheel(
        {
            "wheelModelId": str(uuid4()),
            "fullName": "Другая модель",
            "designation": "OTHER",
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
    service.update_specimen(
        specimen.specimen_id,
        specimen.record_revision,
        {
            "wheelModelId": other_wheel.wheel_model_id,
            "identificationNumber": specimen.identification_number,
            "batchNumber": specimen.batch_number,
            "marking": specimen.marking,
            "manufacturedOn": specimen.manufactured_on,
            "receivedOn": specimen.received_on,
            "workingDiameterMm": specimen.working_diameter_mm,
            "initialConditionNotes": specimen.initial_condition_notes,
            "notes": specimen.notes,
        },
        None,
    )
    assert service.list_reliability_execution_page(wheel.wheel_model_id, None, 25, None).items[0].execution_id == execution.execution_id
    assert service.list_reliability_execution_page(other_wheel.wheel_model_id, None, 25, None).items == ()
    assert service.get_reliability_observation_version(version_id, None).document_snapshot.title == "ПМИ Р130У"
    assert service.get_reliability_dataset_version(dataset_version_id, None) == dataset.version
    service.close()

    service.open(path=str(project_path), application_instance_id="m04b-reopen")
    assert service.get_reliability_observation_version(version_id, None) == first.version
    assert service.get_reliability_dataset_version(dataset_version_id, None) == dataset.version
    assert service.get_reliability_dataset_version(dataset_second.version.dataset_version_id, None) == dataset_second.version
    assert service.list_reliability_dataset_page(wheel.wheel_model_id, None, 25, None).items[0].latest_version_id == dataset_second.version.dataset_version_id
    assert service.list_reliability_observation_versions(execution.execution_id, None)[0] == history_head.version
    service.close()

    maximum_json_bytes = 250_000
    parsed_json_sizes: list[int] = []

    def bounded_json_object(value: str) -> dict[str, object]:
        encoded_size = len(value.encode("utf-8"))
        parsed_json_sizes.append(encoded_size)
        assert encoded_size <= maximum_json_bytes
        parsed = json.loads(value)
        assert isinstance(parsed, dict)
        return OBJECT_ADAPTER.validate_python(parsed)

    monkeypatch.setattr(reliability_domain_module, "_json_object", bounded_json_object)
    oversized_json = json.dumps({"payload": "x" * maximum_json_bytes})
    oversized_mutations = (
        (
            "execution-snapshot",
            "reliability_test_executions_no_update",
            "UPDATE reliability_test_executions SET planned_parameters_snapshot_json=? WHERE execution_id=?",
            (oversized_json, execution.execution_id),
        ),
        (
            "document-snapshot",
            "reliability_observation_versions_no_update",
            "UPDATE reliability_observation_versions SET document_snapshot_json=? WHERE observation_version_id=?",
            (oversized_json, first.version.observation_version_id),
        ),
    )
    for mutation_name, trigger_name, statement, parameters in oversized_mutations:
        oversized_path = tmp_path / f"m04b-oversized-{mutation_name}.irproj"
        shutil.copytree(project_path, oversized_path)
        with closing(sqlite3.connect(oversized_path / "project.sqlite")) as connection:
            trigger_sql = str(
                connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                    (trigger_name,),
                ).fetchone()[0]
            )
            connection.execute(f"DROP TRIGGER {trigger_name}")
            connection.execute(statement, parameters)
            connection.execute(trigger_sql)
            connection.commit()
        with pytest.raises(ProjectOperationError) as oversized_project:
            ProjectService().open(
                path=str(oversized_path),
                application_instance_id=f"m04b-oversized-{mutation_name}",
            )
        assert oversized_project.value.code == "corrupt_project"

    oversized_vibration_path = tmp_path / "m04b-oversized-vibration.irproj"
    shutil.copytree(project_path, oversized_vibration_path)
    with closing(sqlite3.connect(oversized_vibration_path / "project.sqlite")) as connection:
        connection.execute(
            """
            INSERT INTO failure_observations (
                failure_id, execution_id, failure_type, subject_kind,
                source_event_reference, source_field_reference, cycles_at_failure,
                duration_s, rpm, vibration_summary_json, observed_at_utc,
                source_outer_package_sha256
            ) VALUES (?, ?, 'technical_interruption', 'equipment', ?, ?, NULL, NULL, NULL, ?, NULL, ?)
            """,
            (
                str(uuid4()),
                execution.execution_id,
                "events/oversized",
                "events/oversized.json",
                oversized_json,
                execution.source_outer_package_sha256,
            ),
        )
        connection.commit()
    with pytest.raises(ProjectOperationError) as oversized_vibration:
        ProjectService().open(
            path=str(oversized_vibration_path),
            application_instance_id="m04b-oversized-vibration",
        )
    assert oversized_vibration.value.code == "corrupt_project"

    oversized_audit_path = tmp_path / "m04b-oversized-audit.irproj"
    shutil.copytree(project_path, oversized_audit_path)
    with closing(sqlite3.connect(oversized_audit_path / "project.sqlite")) as connection:
        audit_trigger_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='project_audit_events_no_update'",
            ).fetchone()[0]
        )
        connection.execute("DROP TRIGGER project_audit_events_no_update")
        connection.execute(
            """
            UPDATE project_audit_events SET payload_json=?
            WHERE event_type='reliability_execution.materialized'
            """,
            (oversized_json,),
        )
        connection.execute(audit_trigger_sql)
        connection.commit()
        with pytest.raises(ProjectOperationError) as oversized_audit:
            reliability_domain_module.validate_reliability_evidence(connection)
        assert oversized_audit.value.code == "corrupt_project"
    assert max(parsed_json_sizes, default=0) <= maximum_json_bytes

    incompatible_dataset_path = tmp_path / "m04b-incompatible-dataset.irproj"
    shutil.copytree(project_path, incompatible_dataset_path)
    tampered_version = dataset_second.version
    tampered_payload = {
        "datasetId": tampered_version.dataset_id,
        "datasetVersionId": tampered_version.dataset_version_id,
        "wheelModelId": tampered_version.wheel_model_id,
        "versionNumber": tampered_version.version_number,
        "previousVersionId": tampered_version.previous_version_id,
        "policyId": tampered_version.policy_id,
        "title": tampered_version.title,
        "method": tampered_version.method,
        "metricKind": "rpt_start_stop_cycles",
        "metricUnit": "count",
        "populationBasis": tampered_version.population_basis,
        "methodologyBasis": tampered_version.methodology_basis,
        "comparabilityBasis": tampered_version.comparability_basis,
        "members": [
            {
                "observationVersionId": member.observation_version_id,
                "executionId": member.execution_id,
                "localSpecimenId": member.local_specimen_id,
                "sourceRunId": member.source_run_id,
                "policyEligibility": member.policy_eligibility,
                "policyReason": member.policy_reason,
                "decision": member.decision,
                "inclusionReason": member.inclusion_reason,
            }
            for member in sorted(
                tampered_version.members,
                key=lambda item: item.observation_version_id,
            )
        ],
        "actor": tampered_version.actor,
        "decisionReason": tampered_version.decision_reason,
        "createdAtUtc": tampered_version.created_at_utc,
    }
    tampered_hash = hashlib.sha256(
        json.dumps(
            tampered_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with closing(sqlite3.connect(incompatible_dataset_path / "project.sqlite")) as connection:
        dataset_trigger_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='reliability_dataset_versions_no_update'",
            ).fetchone()[0]
        )
        audit_trigger_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='project_audit_events_no_update'",
            ).fetchone()[0]
        )
        connection.execute("DROP TRIGGER reliability_dataset_versions_no_update")
        connection.execute("DROP TRIGGER project_audit_events_no_update")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            """
            UPDATE reliability_dataset_versions
            SET metric_kind='rpt_start_stop_cycles', metric_unit='count', content_sha256=?
            WHERE dataset_version_id=?
            """,
            (tampered_hash, tampered_version.dataset_version_id),
        )
        audit_row = connection.execute(
            """
            SELECT sequence, payload_json FROM project_audit_events
            WHERE event_type='reliability_dataset.version_created'
              AND json_extract(payload_json, '$.datasetVersionId')=?
            """,
            (tampered_version.dataset_version_id,),
        ).fetchone()
        assert audit_row is not None
        audit_payload = OBJECT_ADAPTER.validate_json(str(audit_row[1]))
        audit_payload["contentSha256"] = tampered_hash
        connection.execute(
            "UPDATE project_audit_events SET payload_json=? WHERE sequence=?",
            (
                json.dumps(audit_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                int(audit_row[0]),
            ),
        )
        connection.execute("PRAGMA ignore_check_constraints=OFF")
        connection.execute(dataset_trigger_sql)
        connection.execute(audit_trigger_sql)
        connection.commit()
        with pytest.raises(ProjectOperationError) as direct_validation:
            reliability_domain_module.validate_reliability_evidence(connection)
        assert direct_validation.value.code == "corrupt_project"
    with pytest.raises(ProjectOperationError) as incompatible_dataset:
        ProjectService().open(
            path=str(incompatible_dataset_path),
            application_instance_id="m04b-incompatible-dataset",
        )
    assert incompatible_dataset.value.code == "corrupt_project"

    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        trigger_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='reliability_observation_versions_no_update'",
            ).fetchone()[0]
        )
        connection.execute("DROP TRIGGER reliability_observation_versions_no_update")
        connection.execute(
            "UPDATE reliability_observation_versions SET endpoint_basis='Подменено' WHERE observation_version_id=?",
            (version_id,),
        )
        connection.execute(trigger_sql)
        connection.commit()
    with pytest.raises(ProjectOperationError) as tampered_observation:
        service.open(path=str(project_path), application_instance_id="m04b-tampered")
    assert tampered_observation.value.code == "corrupt_project"
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        connection.execute("DROP TRIGGER reliability_observation_versions_no_update")
        connection.execute(
            """
            UPDATE reliability_observation_versions
            SET endpoint_basis=?, document_id=?
            WHERE observation_version_id=?
            """,
            (first.version.endpoint_basis, alternate_document.case_document_id, version_id),
        )
        connection.execute(trigger_sql)
        connection.commit()
    with pytest.raises(ProjectOperationError) as mismatched_document:
        service.open(path=str(project_path), application_instance_id="m04b-document-tampered")
    assert mismatched_document.value.code == "corrupt_project"


def test_m04b_execution_keyset_pages_are_bounded_stable_and_do_not_repeat(
    tmp_path: Path,
) -> None:
    service, project_path = _project(tmp_path)
    imported = _import(service, project_path, _package("normal_final_rbd.r130run"))
    wheel = service.create_wheel(
        {
            "wheelModelId": str(uuid4()),
            "fullName": "Колесо pagination",
            "designation": "PAGE",
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
            "identificationNumber": "PAGE-001",
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
    service.bind_imported_run_specimen(
        source_specimen_id=imported.source_specimen_id,
        local_specimen_id=specimen.specimen_id,
        expected_revision=1,
        actor="local_user",
        reason="Pagination fixture",
        deadline=None,
    )
    base_execution = service.materialize_reliability_execution(imported.local_import_id, None)
    service.close()

    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        connection.row_factory = sqlite3.Row
        source = connection.execute("SELECT * FROM r130sh_sources WHERE local_import_id=?", (imported.local_import_id,)).fetchone()
        projection = connection.execute("SELECT * FROM r130sh_run_projections WHERE local_import_id=?", (imported.local_import_id,)).fetchone()
        execution = connection.execute("SELECT * FROM reliability_test_executions WHERE execution_id=?", (base_execution.execution_id,)).fetchone()
        assert source is not None and projection is not None and execution is not None

        def insert_clone(index: int) -> str:
            local_import_id = str(uuid4())
            source_values = dict(source)
            source_values.update(
                local_import_id=local_import_id,
                package_id=str(uuid4()),
                run_id=f"pagination-run-{index}",
                managed_relative_path=f"imports/r130sh/page-{index}.r130run",
            )
            _insert_mapping(connection, "r130sh_sources", source_values)
            projection_values = dict(projection)
            projection_values.update(local_import_id=local_import_id, run_id=f"pagination-run-{index}")
            _insert_mapping(connection, "r130sh_run_projections", projection_values)
            execution_id = str(uuid4())
            execution_values = dict(execution)
            execution_values.update(
                execution_id=execution_id,
                local_import_id=local_import_id,
                materialized_at_utc="2026-09-09T10:00:00.000Z",
            )
            _insert_mapping(connection, "reliability_test_executions", execution_values)
            return execution_id

        original_ids = {base_execution.execution_id, *(insert_clone(index) for index in range(54))}
        connection.commit()
        repository = ReliabilityDomainRepository(connection)
        first = repository.list_execution_page(wheel.wheel_model_id, None, 25, None)
        assert len(first.items) == 25
        assert first.next_cursor is not None
        late_id = insert_clone(999)
        connection.commit()
        collected = [item.execution_id for item in first.items]
        cursor: str | None = first.next_cursor
        while cursor is not None:
            page = repository.list_execution_page(wheel.wheel_model_id, cursor, 25, None)
            collected.extend(item.execution_id for item in page.items)
            cursor = page.next_cursor
        assert len(collected) == 55
        assert len(set(collected)) == 55
        assert set(collected) == original_ids
        assert late_id not in collected
        with pytest.raises(ProjectOperationError) as invalid_cursor:
            repository.list_execution_page(str(uuid4()), first.next_cursor, 25, None)
        assert invalid_cursor.value.code == "validation_error"
        with pytest.raises(ProjectOperationError):
            repository.list_execution_page(wheel.wheel_model_id, None, 51, None)


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


def _insert_mapping(connection: sqlite3.Connection, table: str, values: dict[str, object]) -> None:
    columns = tuple(values)
    placeholders = ",".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        tuple(values[column] for column in columns),
    )


def _source_snapshot(project_path: Path, local_import_id: str) -> tuple[object, ...]:
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        source = connection.execute(
            "SELECT package_id, export_revision, outer_package_sha256, source_snapshot_sha256 FROM r130sh_sources WHERE local_import_id=?",
            (local_import_id,),
        ).fetchone()
        projection = connection.execute(
            "SELECT run_id, source_specimen_id, mode, technical_status, specimen_outcome FROM r130sh_run_projections WHERE local_import_id=?",
            (local_import_id,),
        ).fetchone()
    assert source is not None and projection is not None
    return (*tuple(source), *tuple(projection))


def _managed_path(project_path: Path, imported: ImportedRunSummary) -> Path:
    return project_path / "imports" / "r130sh" / imported.package_id / f"rev-{imported.export_revision}" / f"{imported.outer_package_sha256}.r130run"
