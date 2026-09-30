from __future__ import annotations

from contextlib import closing
import csv
from dataclasses import asdict, replace
from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
from threading import Event
from time import monotonic, sleep
from typing import Literal
from uuid import uuid4
from zipfile import ZipFile
import zlib

from pydantic import TypeAdapter
import pytest

from impeller_reliability.application.project_service import ProjectService
from impeller_reliability.calculations.exact import exact_value
from impeller_reliability.calculations.pmn_input_snapshot import PmnInputField
from impeller_reliability.calculations.pmn_result_snapshot import PmnReferenceResultModel
from impeller_reliability.calculations.rbd_input_snapshot import RbdSavedFieldSelectionModel
from impeller_reliability.calculations.rpt_input_snapshot import RptInputField
from impeller_reliability.integration.r130run.import_jobs import RunPackageImportJobManager
from impeller_reliability.integration.r130run.import_models import ImportedRunPlanModel, imported_run_detail_model
from impeller_reliability.integration.r130run.m9a import M9aPackageFacts, read_m9a_package_facts
from impeller_reliability.integration.r130run.models import RunPackageValidationReport
from impeller_reliability.integration.r130run.validator import (
    MAX_JSON_BYTES,
    RunPackageValidator,
    ValidationControl,
)
from impeller_reliability.persistence import (
    pmn_calculations as pmn_calculations_module,
    r130sh_sources as r130sh_sources_module,
    rbd_calculations as rbd_calculations_module,
    reliability_domain as reliability_domain_module,
    rpt_calculations as rpt_calculations_module,
)
from impeller_reliability.persistence.pmn_calculations import PmnCalculationWriteResult, PmnEvidenceReference, PmnFailureEvidence, PmnFieldSelection
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.r130sh_sources import ImportedRunDetail, ImportedRunSummary, RbdPlanSourceSnapshot
from impeller_reliability.persistence.rbd_calculations import (
    RbdCalculationRepository,
    RbdCalculationWriteResult,
    RbdEvidenceReference,
    RbdFailureEvidence,
    RbdFieldSelection,
    RbdInputField,
)
from impeller_reliability.persistence.reliability_domain import (
    ReliabilityDomainRepository,
    TestExecution as ReliabilityTestExecution,
)
from impeller_reliability.persistence.rpt_calculations import RptCalculationWriteResult, RptEvidenceReference, RptFailureEvidence, RptFieldSelection
from impeller_reliability.protocol.envelopes import (
    REQUEST_ENVELOPE_ADAPTER,
    PmnCalculationDetailResult,
    PmnCalculationPageResult,
    PmnCalculationWriteResultModel,
    PmnPlanSourceResult,
    RbdPlanSourceValuesResult,
    RptCalculationDetailResult,
    RptCalculationPageResult,
    RptCalculationWriteResultModel,
    RptPlanSourceResult,
)
from impeller_reliability.worker.deadline import RequestDeadline
from impeller_reliability.worker.dispatcher import Dispatcher
from support.r130run_builder import build_synthetic_r130run

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
M9A_ROOT = REPOSITORY_ROOT / "fixtures" / "contracts" / "r130run" / "v1" / "m9a"
PMN_REFERENCE_ROOT = REPOSITORY_ROOT / "fixtures" / "contracts" / "r130run" / "v1" / "pmn-reference"
OBJECT_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
FIELD_SELECTIONS_ADAPTER: TypeAdapter[list[dict[str, object]]] = TypeAdapter(list[dict[str, object]])


class _RbdFieldResolutionProbe(RbdCalculationRepository):
    def field_snapshots(
        self,
        source: RbdPlanSourceSnapshot,
        selections: tuple[RbdFieldSelection, ...],
        execution: tuple[str, str, str],
    ) -> list[dict[str, object]]:
        return self._resolve_fields(source, selections, execution, None)[1]


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
        assert imported.validator_version == "m03b.3"
        assert imported.validation_contract_commit == "b7792758b407ffc52d2fff051243056f63dbf18f"
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


def test_nullable_plan_references_survive_import_detail_retry_and_reopen(tmp_path: Path) -> None:
    service, project_path = _project(tmp_path)
    source = _package_with_nullable_plan_references(tmp_path)

    first = _import_via_job(
        service,
        project_path,
        source,
        allow_diagnostic_partial=False,
    )
    detail = imported_run_detail_model(service.get_imported_run(first.local_import_id))
    assert detail.projection.originalPlan.laboratoryCaseReference is None
    assert detail.projection.originalPlan.customerOrderReference is None
    assert detail.projection.effectivePlan.laboratoryCaseReference is None
    assert detail.projection.effectivePlan.customerOrderReference is None
    for field in ("laboratoryCaseReference", "customerOrderReference"):
        for invalid in ("", "   ", "\u0085"):
            payload = detail.projection.originalPlan.model_dump()
            payload[field] = invalid
            with pytest.raises(ValueError, match="plan_reference_required"):
                ImportedRunPlanModel.model_validate(payload)
    format_mark_plan = detail.projection.originalPlan.model_dump()
    format_mark_plan["laboratoryCaseReference"] = "\ufeff"
    assert ImportedRunPlanModel.model_validate(format_mark_plan).laboratoryCaseReference == "\ufeff"

    audit_before_retry = _audit_count(project_path)
    repeated = _import_via_job(
        service,
        project_path,
        source,
        allow_diagnostic_partial=False,
    )
    assert repeated.local_import_id == first.local_import_id
    assert _audit_count(project_path) == audit_before_retry
    service.close()

    service.open(path=str(project_path), application_instance_id="nullable-reopen")
    reopened = imported_run_detail_model(service.get_imported_run(first.local_import_id))
    assert reopened == detail
    assert len(service.list_imported_runs()) == 1
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


def test_materialized_reliability_execution_preserves_source_and_reopens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    rounding_import = _import(service, project_path, _package("exact_methodical_rounding.r130run"))
    assert rounding_import.source_specimen_id == imported.source_specimen_id
    rounding_execution = service.materialize_reliability_execution(rounding_import.local_import_id, None)
    original_plan = service.read_rbd_plan_source(
        rounding_execution.execution_id,
        rounding_import.local_import_id,
        "original",
    )
    effective_plan = service.read_rbd_plan_source(
        rounding_execution.execution_id,
        rounding_import.local_import_id,
        "effective",
    )
    rounding_detail = service.get_imported_run(rounding_import.local_import_id)
    assert original_plan.execution_id == rounding_execution.execution_id
    assert original_plan.local_import_id == rounding_import.local_import_id
    assert original_plan.outer_package_sha256 == rounding_import.outer_package_sha256
    assert original_plan.source_snapshot_sha256 == rounding_import.source_snapshot_sha256
    assert original_plan.producer_name == rounding_import.producer_name
    assert original_plan.producer_version == rounding_import.producer_version
    assert original_plan.producer_build_id == rounding_import.producer_build_id
    assert original_plan.producer_git_commit == rounding_import.producer_git_commit
    assert original_plan.source_values.base_cycles == "1000"
    assert original_plan.source_values.reserve_factor == "1.5003"
    assert original_plan.source_values.nominal_rpm == "1500"
    assert original_plan.source_values.acceleration_duration_s == "5"
    assert original_plan.source_values.deceleration_duration_s == "5"
    assert original_plan.methodical_requirements.required_cycles_exact == "1500.3"
    assert original_plan.methodical_requirements.required_steady_duration_s_exact == "60.012"
    assert original_plan.execution_targets.target_cycles == "1501"
    assert original_plan.execution_targets.target_steady_duration_s == "60.04"
    assert original_plan.execution_targets.total_duration_s == "70.04"
    assert original_plan.payload_path == "plan/original.json"
    assert original_plan.payload_sha256 == rounding_detail.projection["original_plan_sha256"]
    assert effective_plan.selection == "effective"
    assert effective_plan.payload_path == "plan/effective.json"
    assert effective_plan.payload_sha256 == rounding_detail.projection["effective_plan_sha256"]
    assert effective_plan.payload_sha256 != original_plan.payload_sha256
    assert effective_plan.source_values == original_plan.source_values
    assert effective_plan.execution_targets == original_plan.execution_targets
    calculation_input_id = str(uuid4())
    calculation_id = str(uuid4())
    source_selections = tuple(
        RbdFieldSelection(field=field, origin="source")
        for field in (
            "base_cycles",
            "reserve_factor",
            "nominal_rpm",
            "acceleration_duration_s",
            "deceleration_duration_s",
        )
    )
    calculation = service.create_rbd_calculation(
        analysis_input_snapshot_id=calculation_input_id,
        calculation_snapshot_id=calculation_id,
        execution_id=rounding_execution.execution_id,
        selection="original",
        selections=source_selections,
        failure=None,
        actor="local_user",
        reason="Расчёт требований по исходной редакции плана",
        deadline=None,
    )
    assert calculation.disposition == "created"
    assert calculation.detail.input_snapshot.input_snapshot["fieldSelections"]
    assert calculation.detail.calculation_snapshot.result_snapshot["required_cycles_exact"] == {
        "decimal": "1500.3",
        "decimal_preview": "1500.3",
        "denominator": "10",
        "numerator": "15003",
    }
    with closing(sqlite3.connect(project_path / "project.sqlite")) as competing_writer, closing(sqlite3.connect(project_path / "project.sqlite")) as waiting_connection:
        waiting_repository = RbdCalculationRepository(waiting_connection)
        waiting_input_id = str(uuid4())
        waiting_calculation_id = str(uuid4())
        competing_writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(ProjectOperationError) as write_lock_timeout:
            waiting_repository.create(
                analysis_input_snapshot_id=waiting_input_id,
                calculation_snapshot_id=waiting_calculation_id,
                source=original_plan,
                selections=source_selections,
                failure=None,
                actor="local_user",
                reason="Проверка ограниченного ожидания записи",
                operation_sha256=waiting_repository.operation_sha256(
                    analysis_input_snapshot_id=waiting_input_id,
                    calculation_snapshot_id=waiting_calculation_id,
                    execution_id=rounding_execution.execution_id,
                    plan_selection="original",
                    selections=source_selections,
                    failure=None,
                    actor="local_user",
                    reason="Проверка ограниченного ожидания записи",
                ),
                deadline=RequestDeadline.start(100),
            )
        assert write_lock_timeout.value.code == "timeout"
        competing_writer.rollback()
    repeated_calculation = service.create_rbd_calculation(
        analysis_input_snapshot_id=calculation_input_id,
        calculation_snapshot_id=calculation_id,
        execution_id=rounding_execution.execution_id,
        selection="original",
        selections=source_selections,
        failure=None,
        actor="local_user",
        reason="Расчёт требований по исходной редакции плана",
        deadline=None,
    )
    assert repeated_calculation.disposition == "existing"
    assert repeated_calculation.detail == calculation.detail
    failed_input_id = str(uuid4())
    failed_calculation_id = str(uuid4())
    with monkeypatch.context() as patch_context:

        def fail_audit(*args: object, **kwargs: object) -> None:
            raise RuntimeError("injected audit failure")

        patch_context.setattr(
            rbd_calculations_module,
            "insert_audit",
            fail_audit,
        )
        with pytest.raises(RuntimeError, match="injected audit failure"):
            service.create_rbd_calculation(
                analysis_input_snapshot_id=failed_input_id,
                calculation_snapshot_id=failed_calculation_id,
                execution_id=rounding_execution.execution_id,
                selection="original",
                selections=source_selections,
                failure=None,
                actor="local_user",
                reason="Проверка атомарного rollback",
                deadline=None,
            )
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        input_count = connection.execute(
            "SELECT count(*) FROM rbd_analysis_input_snapshots WHERE analysis_input_snapshot_id=?",
            (failed_input_id,),
        ).fetchone()
        calculation_count = connection.execute(
            "SELECT count(*) FROM rbd_calculation_snapshots WHERE calculation_snapshot_id=?",
            (failed_calculation_id,),
        ).fetchone()
        assert input_count is not None and int(input_count[0]) == 0
        assert calculation_count is not None and int(calculation_count[0]) == 0
    with pytest.raises(ProjectOperationError) as conflicting_retry:
        service.create_rbd_calculation(
            analysis_input_snapshot_id=calculation_input_id,
            calculation_snapshot_id=calculation_id,
            execution_id=rounding_execution.execution_id,
            selection="original",
            selections=source_selections,
            failure=None,
            actor="local_user",
            reason="Другое основание",
            deadline=None,
        )
    assert conflicting_retry.value.code == "revision_conflict"
    manual_input_id = str(uuid4())
    manual_calculation_id = str(uuid4())
    manual_selections = tuple(
        RbdFieldSelection(
            field=item.field,
            origin="manual" if item.field == "reserve_factor" else "source",
            manual_value="2" if item.field == "reserve_factor" else None,
            basis="Коэффициент принят инженером для отдельного сценария" if item.field == "reserve_factor" else "",
        )
        for item in source_selections
    )
    failure_document_id = str(uuid4())
    failure_document = service.create_case_document(
        failure_document_id,
        {
            "documentKind": "measurement_or_attestation_record",
            "title": "Протокол регистрации момента отказа",
            "designation": "РБД-ОТК-01",
            "revisionLabel": "01",
            "documentDate": "2024-07-02",
            "issuer": "ЛИЦ ВВУ",
            "notes": "",
        },
        (),
        (),
        None,
    )
    with pytest.raises(ProjectOperationError) as undocumented_failure:
        service.create_rbd_calculation(
            analysis_input_snapshot_id=str(uuid4()),
            calculation_snapshot_id=str(uuid4()),
            execution_id=rounding_execution.execution_id,
            selection="original",
            selections=source_selections,
            failure=RbdFailureEvidence(
                applicability="exact_supported",
                duration_to_failure_s="60000",
                basis="Указано число без документа",
            ),
            actor="local_user",
            reason="Проверка документированного T_OTK",
            deadline=None,
        )
    assert undocumented_failure.value.code == "validation_error"
    unsupported_observation_selections = tuple(
        RbdFieldSelection(
            field=item.field,
            origin="manual" if item.field == "base_cycles" else "source",
            manual_value="1000" if item.field == "base_cycles" else None,
            basis="Неверная ссылка" if item.field == "base_cycles" else "",
            evidence=RbdEvidenceReference(observation_version_id=str(uuid4())) if item.field == "base_cycles" else None,
        )
        for item in source_selections
    )
    with pytest.raises(ProjectOperationError) as unsupported_observation:
        service.create_rbd_calculation(
            analysis_input_snapshot_id=str(uuid4()),
            calculation_snapshot_id=str(uuid4()),
            execution_id=rounding_execution.execution_id,
            selection="original",
            selections=unsupported_observation_selections,
            failure=None,
            actor="local_user",
            reason="Проверка физического смысла свидетельства",
            deadline=None,
        )
    assert unsupported_observation.value.code == "validation_error"
    manual_calculation = service.create_rbd_calculation(
        analysis_input_snapshot_id=manual_input_id,
        calculation_snapshot_id=manual_calculation_id,
        execution_id=rounding_execution.execution_id,
        selection="original",
        selections=manual_selections,
        failure=RbdFailureEvidence(
            applicability="exact_supported",
            duration_to_failure_s="60000",
            basis="Точное документированное время до отказа для простого запуска",
            evidence=RbdEvidenceReference(
                document_id=failure_document.case_document_id,
                document_record_revision=failure_document.record_revision,
                document_locator="раздел 3, время испытания до отказа и начало отсчёта",
            ),
        ),
        actor="local_user",
        reason="Новый снимок с документированным аналитическим дополнением",
        deadline=None,
    )
    assert manual_calculation.detail.calculation_snapshot.result_snapshot["required_cycles_exact"] == {
        "decimal": "2000",
        "decimal_preview": "2000",
        "denominator": "1",
        "numerator": "2000",
    }
    assert manual_calculation.detail.calculation_snapshot.result_snapshot["failure_result"] == {
        "cycles_to_failure": "1499875",
        "reason_code": None,
        "status": "calculated",
    }
    updated_failure_document = service.update_case_document(
        failure_document.case_document_id,
        failure_document.record_revision,
        {
            "documentKind": "measurement_or_attestation_record",
            "title": "Уточнённый протокол регистрации момента отказа",
            "designation": "РБД-ОТК-01",
            "revisionLabel": "02",
            "documentDate": "2024-07-02",
            "issuer": "ЛИЦ ВВУ",
            "notes": "",
        },
        (wheel.wheel_model_id,),
        (specimen.specimen_id,),
        None,
    )
    assert updated_failure_document.record_revision == failure_document.record_revision + 1
    assert service.get_rbd_calculation_detail(manual_calculation_id, None) == manual_calculation.detail
    archived_failure_document = service.set_case_document_archived(
        failure_document.case_document_id,
        updated_failure_document.record_revision,
        True,
        None,
    )
    audit_before_archived_attempt = _audit_count(project_path)
    with pytest.raises(ProjectOperationError) as archived_failure_evidence:
        service.create_rbd_calculation(
            analysis_input_snapshot_id=str(uuid4()),
            calculation_snapshot_id=str(uuid4()),
            execution_id=rounding_execution.execution_id,
            selection="original",
            selections=source_selections,
            failure=RbdFailureEvidence(
                applicability="exact_supported",
                duration_to_failure_s="60000",
                basis="Новый расчёт по архивному документу",
                evidence=RbdEvidenceReference(
                    document_id=archived_failure_document.case_document_id,
                    document_record_revision=archived_failure_document.record_revision,
                    document_locator="раздел 3",
                ),
            ),
            actor="local_user",
            reason="Проверка недоступного документа",
            deadline=None,
        )
    assert archived_failure_evidence.value.code == "entity_archived"
    assert _audit_count(project_path) == audit_before_archived_attempt
    assert service.get_rbd_calculation_detail(calculation_id, None) == calculation.detail
    calculation_page = service.list_rbd_calculation_page(wheel.wheel_model_id, None, 25, None)
    assert {item.calculation_snapshot_id for item in calculation_page.items} == {
        calculation_id,
        manual_calculation_id,
    }
    assert calculation_page.next_cursor is None
    with pytest.raises(ProjectOperationError) as invalid_page:
        service.list_rbd_calculation_page(wheel.wheel_model_id, None, 0, None)
    assert invalid_page.value.code == "validation_error"
    with pytest.raises(ProjectOperationError) as missing_calculation:
        service.get_rbd_calculation_detail(str(uuid4()), None)
    assert missing_calculation.value.code == "entity_not_found"
    history_ids = {calculation_id, manual_calculation_id}
    for _ in range(49):
        history_result_id = str(uuid4())
        service.create_rbd_calculation(
            analysis_input_snapshot_id=str(uuid4()),
            calculation_snapshot_id=history_result_id,
            execution_id=rounding_execution.execution_id,
            selection="effective",
            selections=source_selections,
            failure=None,
            actor="local_user",
            reason="Проверка ограниченной страницы истории",
            deadline=None,
        )
        history_ids.add(history_result_id)
    first_history_page = service.list_rbd_calculation_page(wheel.wheel_model_id, None, 50, None)
    assert len(first_history_page.items) == 50
    assert first_history_page.next_cursor is not None
    second_history_page = service.list_rbd_calculation_page(
        wheel.wheel_model_id,
        first_history_page.next_cursor,
        50,
        None,
    )
    assert len(second_history_page.items) == 1
    assert second_history_page.next_cursor is None
    assert {item.calculation_snapshot_id for item in (*first_history_page.items, *second_history_page.items)} == history_ids
    with pytest.raises(ProjectOperationError) as mismatched_execution_source:
        service.read_rbd_plan_source(
            execution.execution_id,
            rounding_import.local_import_id,
            "original",
        )
    assert mismatched_execution_source.value.code == "entity_not_found"
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
    assert {item.execution_id for item in page.items} == {
        execution.execution_id,
        rounding_execution.execution_id,
    }
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
    assert service.get_rbd_calculation_detail(calculation_id, None) == calculation.detail
    assert service.get_rbd_calculation_detail(manual_calculation_id, None) == manual_calculation.detail
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


def _project_with_rbd_execution(
    tmp_path: Path,
    package_path: Path | None = None,
) -> tuple[ProjectService, Path, ImportedRunSummary, ReliabilityTestExecution]:
    service, project_path = _project(tmp_path)
    imported = _import(service, project_path, package_path or _package("normal_final_rbd.r130run"))
    wheel = service.create_wheel(
        {
            "wheelModelId": str(uuid4()),
            "fullName": "Колесо для source integrity",
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
            "identificationNumber": "SOURCE-SPECIMEN-001",
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
        reason="Проверка источника расчёта",
        deadline=None,
    )
    execution = service.materialize_reliability_execution(imported.local_import_id, None)
    return service, project_path, imported, execution


def test_current_r130sh_exporter_pmn_package_imports_calculates_and_reopens(tmp_path: Path) -> None:
    provenance = OBJECT_ADAPTER.validate_json((PMN_REFERENCE_ROOT / "UPSTREAM_SOURCE.json").read_bytes())
    producer_metadata = OBJECT_ADAPTER.validate_python(provenance["producer"])
    package_metadata = OBJECT_ADAPTER.validate_python(provenance["package"])
    seed_metadata = OBJECT_ADAPTER.validate_python(provenance["seed"])
    producer_commit = str(producer_metadata["commit"])
    package_hash = str(package_metadata["sha256"])
    package = PMN_REFERENCE_ROOT / str(package_metadata["file"])
    assert hashlib.sha256(package.read_bytes()).hexdigest() == package_hash
    assert package.stat().st_size == package_metadata["sizeBytes"]
    with ZipFile(package) as archive:
        manifest = OBJECT_ADAPTER.validate_json(archive.read("manifest.json"))
        producer = OBJECT_ADAPTER.validate_python(manifest["producer"])
        assert producer == {
            "name": "R130SH",
            "version": producer_metadata["version"],
            "build_id": seed_metadata["buildId"],
            "git_commit": producer_commit,
        }
        assert manifest["run_id"] == package_metadata["runId"]
        assert manifest["package_id"] == package_metadata["packageId"]
        assert manifest["export_revision"] == package_metadata["exportRevision"]
        assert manifest["source_snapshot_sha256"] == package_metadata["sourceSnapshotSha256"]
        assert manifest["package_kind"] == "final"
    service, project_path, imported, execution = _project_with_rbd_execution(tmp_path, package)
    assert imported.outer_package_sha256 == package_hash
    assert imported.export_revision == package_metadata["exportRevision"]
    for selection in ("original", "effective"):
        source = service.get_pmn_source_inputs(execution.execution_id, selection, None)
        assert source.producer_git_commit == producer_commit
        assert source.source_values.nominal_rpm == "1500"
        assert source.source_values.speed_factor == "1.1"
        assert source.source_values.target_cycles == "2"
        assert source.methodical_requirements.target_max_rpm_exact == "1650"
        assert source.execution_targets.cycle_duration_s == "5"
        assert source.payload_path == f"plan/{selection}.json"
    saved = service.create_pmn_calculation(
        analysis_input_snapshot_id=str(uuid4()),
        calculation_snapshot_id=str(uuid4()),
        execution_id=execution.execution_id,
        selection="effective",
        selections=_pmn_source_selections(),
        failure=None,
        actor="local_user",
        reason="Сверка текущего exporter R130SH",
        deadline=None,
    )
    assert saved.disposition == "created"
    result = saved.detail.calculation_snapshot.result_snapshot
    assert OBJECT_ADAPTER.validate_python(result["maximum_rpm"])["decimal"] == "1650"
    assert OBJECT_ADAPTER.validate_python(result["cycle_duration_s_exact"])["decimal"] == "5"
    assert OBJECT_ADAPTER.validate_python(result["total_duration_s_exact"])["decimal"] == "10"
    calculation_id = saved.detail.calculation_snapshot.calculation_snapshot_id
    service.close()
    service.open(path=str(project_path), application_instance_id="current-pmn-export-reopen")
    assert service.get_pmn_calculation_detail(calculation_id, None) == saved.detail
    service.close()


@pytest.mark.parametrize("selection", ["original", "effective"])
def test_pmn_source_inputs_keep_provenance_fields_requirements_targets_and_coordinates(tmp_path: Path, selection: Literal["original", "effective"]) -> None:
    service, project_path, imported, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_pmn.r130run"))
    source = service.get_pmn_source_inputs(execution.execution_id, selection, None)
    assert source.execution_id == execution.execution_id
    assert source.local_import_id == imported.local_import_id
    assert source.outer_package_sha256 == imported.outer_package_sha256
    assert source.export_revision == imported.export_revision
    assert source.selection == selection
    assert source.source_values.nominal_rpm == "1500"
    assert source.source_values.speed_factor == "1.1"
    assert source.source_values.target_cycles == "2"
    assert source.source_values.acceleration_duration_s == "2"
    assert source.source_values.steady_duration_s == "1"
    assert source.source_values.deceleration_duration_s == "2"
    assert source.methodical_requirements.target_max_rpm_exact == "1650"
    assert source.methodical_requirements.cycle_duration_s_exact == "5"
    assert source.methodical_requirements.total_duration_s_exact == "10"
    assert source.execution_targets.target_max_rpm == "1650"
    assert source.execution_targets.target_cycles == "2"
    assert source.execution_targets.cycle_duration_s == "5"
    assert source.execution_targets.total_duration_s == "10"

    expected_member = "plan/original.json" if selection == "original" else "plan/effective.json"
    expected_prefix = "/source_values/" if selection == "original" else "/effective_plan/effective_plan/source_values/"
    assert source.payload_path == expected_member
    with ZipFile(_managed_path(project_path, imported)) as archive:
        payload = archive.read(expected_member)
        assert hashlib.sha256(payload).hexdigest() == source.payload_sha256
        for field in (
            "nominal_rpm",
            "speed_factor",
            "target_cycles",
            "acceleration_duration_s",
            "steady_duration_s",
            "deceleration_duration_s",
        ):
            reference = r130sh_sources_module.plan_source_field_reference(selection, field)
            member, pointer = reference.split("#", 1)
            assert member == expected_member
            assert pointer == f"{expected_prefix}{field}"
            current: object = OBJECT_ADAPTER.validate_json(archive.read(member))
            for segment in pointer[1:].split("/"):
                current = OBJECT_ADAPTER.validate_python(current)[segment]
            assert current == getattr(source.source_values, field)
    service.close()


def test_pmn_source_reader_rejects_wrong_import_pair_and_method(tmp_path: Path) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_pmn.r130run"))
    other = _import(service, project_path, _package("shared_specimen_pmn_rpt_rbd-pmn.r130run"))
    with pytest.raises(ProjectOperationError) as wrong_import:
        service.read_pmn_plan_source(execution.execution_id, other.local_import_id, "original", None)
    assert wrong_import.value.code == "entity_not_found"
    service.close()

    rbd_root = tmp_path / "rbd"
    rbd_root.mkdir()
    rbd_service, _, _, rbd_execution = _project_with_rbd_execution(rbd_root)
    with pytest.raises(ProjectOperationError) as wrong_method:
        rbd_service.get_pmn_source_inputs(rbd_execution.execution_id, "original", None)
    assert wrong_method.value.code == "entity_not_found"
    rbd_service.close()


@pytest.mark.parametrize("damage", ["missing", "modified"])
def test_pmn_source_reader_rejects_unavailable_managed_archive(tmp_path: Path, damage: str) -> None:
    service, project_path, imported, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_pmn.r130run"))
    assert service.get_pmn_source_inputs(execution.execution_id, "original", None).execution_id == execution.execution_id
    managed_path = _managed_path(project_path, imported)
    if damage == "missing":
        managed_path.unlink()
    else:
        managed_path.write_bytes(b"modified")
    with pytest.raises(ProjectOperationError) as rejected:
        service.get_pmn_source_inputs(execution.execution_id, "original", None)
    assert rejected.value.code == "file_integrity_mismatch"
    service.close()


def test_pmn_source_reader_preserves_numeric_json_scalars_and_zero(tmp_path: Path) -> None:
    synthetic = _package_with_plan_values(
        tmp_path,
        base_name="normal_final_pmn.r130run",
        output_name="pmn-numeric-scalars.r130run",
        source_updates={"speed_factor": 1.25, "target_cycles": 2, "steady_duration_s": 0},
        requirement_updates={"target_max_rpm_exact": "1875", "cycle_duration_s_exact": "4", "total_duration_s_exact": "8"},
        target_updates={"target_max_rpm": "1875", "target_cycles": 2, "cycle_duration_s": "4", "total_duration_s": "8"},
    )
    service, _, _, execution = _project_with_rbd_execution(tmp_path, synthetic)
    for selection in ("original", "effective"):
        source = service.get_pmn_source_inputs(execution.execution_id, selection, None)
        assert source.source_values.speed_factor == "1.25"
        assert source.source_values.target_cycles == "2"
        assert source.source_values.steady_duration_s == "0"
        assert source.methodical_requirements.target_max_rpm_exact == "1875"
        assert source.execution_targets.target_cycles == "2"
    service.close()


def _pmn_source_selections() -> tuple[PmnFieldSelection, ...]:
    return tuple(
        PmnFieldSelection(field=field, origin="source")
        for field in (
            "nominal_rpm",
            "speed_factor",
            "target_cycles",
            "acceleration_duration_s",
            "steady_duration_s",
            "deceleration_duration_s",
        )
    )


def test_pmn_calculation_immutable_pair_history_retry_and_reopen_without_zip(tmp_path: Path) -> None:
    service, project_path, imported, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_pmn.r130run"))
    input_id, calculation_id = str(uuid4()), str(uuid4())
    selections = _pmn_source_selections()
    saved = service.create_pmn_calculation(
        analysis_input_snapshot_id=input_id,
        calculation_snapshot_id=calculation_id,
        execution_id=execution.execution_id,
        selection="effective",
        selections=selections,
        failure=None,
        actor=" local_user ",
        reason="  ПМН по исходному плану  ",
        deadline=None,
    )
    assert saved.disposition == "created"
    assert saved.detail.input_snapshot.actor == "local_user"
    assert saved.detail.input_snapshot.decision_reason == "ПМН по исходному плану"
    result = saved.detail.calculation_snapshot.result_snapshot
    assert OBJECT_ADAPTER.validate_python(result["maximum_rpm"])["decimal"] == "1650"
    assert OBJECT_ADAPTER.validate_python(result["cycle_duration_s_exact"])["decimal"] == "5"
    assert OBJECT_ADAPTER.validate_python(result["total_duration_s_exact"])["decimal"] == "10"
    assert OBJECT_ADAPTER.validate_python(result["failure_result"])["status"] == "not_applicable"
    source = OBJECT_ADAPTER.validate_python(saved.detail.input_snapshot.input_snapshot["source"])
    assert source["localImportId"] == imported.local_import_id
    assert source["planSelection"] == "effective"
    assert service.get_pmn_calculation_detail(calculation_id, None) == saved.detail
    page = service.list_pmn_calculation_page(execution.wheel_model_id, None, 25, None)
    assert [item.calculation_snapshot_id for item in page.items] == [calculation_id]
    assert page.next_cursor is None
    assert (
        service.create_pmn_calculation(
            analysis_input_snapshot_id=input_id,
            calculation_snapshot_id=calculation_id,
            execution_id=execution.execution_id,
            selection="effective",
            selections=selections,
            failure=None,
            actor=" local_user ",
            reason="  ПМН по исходному плану  ",
            deadline=None,
        ).disposition
        == "existing"
    )
    service.close()
    _managed_path(project_path, imported).unlink()
    service.open(path=str(project_path), application_instance_id="pmn-reopen")
    assert service.get_pmn_calculation_detail(calculation_id, None) == saved.detail
    assert (
        service.create_pmn_calculation(
            analysis_input_snapshot_id=input_id,
            calculation_snapshot_id=calculation_id,
            execution_id=execution.execution_id,
            selection="effective",
            selections=selections,
            failure=None,
            actor=" local_user ",
            reason="  ПМН по исходному плану  ",
            deadline=None,
        ).disposition
        == "existing"
    )
    service.close()


@pytest.mark.parametrize(("failure_duration", "expected_cycles"), [("2", "1"), ("0", "0")])
def test_pmn_manual_input_and_table_5_survive_document_change(tmp_path: Path, failure_duration: str, expected_cycles: str) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_pmn.r130run"))
    document = service.create_case_document(
        str(uuid4()),
        {
            "documentKind": "measurement_or_attestation_record",
            "title": "Протокол точного времени до отказа",
            "designation": "ПМН-ОТК-01",
            "revisionLabel": "01",
            "documentDate": "2024-07-02",
            "issuer": "ЛИЦ ВВУ",
            "notes": "",
        },
        (),
        (),
        None,
    )
    evidence = PmnEvidenceReference(
        document_id=document.case_document_id,
        document_record_revision=document.record_revision,
        document_locator="раздел 3, событие отказа и начало отсчёта",
    )
    selections = tuple(
        PmnFieldSelection(
            field=field,
            origin="manual" if field == "speed_factor" else "source",
            manual_value="1.2" if field == "speed_factor" else None,
            basis="Коэффициент выбран инженером" if field == "speed_factor" else "",
            evidence=evidence if field == "speed_factor" else None,
        )
        for field in (
            "nominal_rpm",
            "speed_factor",
            "target_cycles",
            "acceleration_duration_s",
            "steady_duration_s",
            "deceleration_duration_s",
        )
    )
    failure = PmnFailureEvidence(
        applicability="exact_supported",
        duration_to_failure_s=failure_duration,
        basis="Время до установленного отказа соответствует одному запуску и постоянному циклу",
        evidence=evidence,
    )
    input_id, calculation_id = str(uuid4()), str(uuid4())
    saved = service.create_pmn_calculation(
        analysis_input_snapshot_id=input_id,
        calculation_snapshot_id=calculation_id,
        execution_id=execution.execution_id,
        selection="original",
        selections=selections,
        failure=failure,
        actor="local_user",
        reason="Ручное дополнение и таблица 5",
        deadline=None,
    )
    saved_fields = FIELD_SELECTIONS_ADAPTER.validate_python(saved.detail.input_snapshot.input_snapshot["fieldSelections"])
    speed = next(item for item in saved_fields if item["field"] == "speed_factor")
    assert speed["rawSourceValue"] == "1.1"
    assert speed["value"] == "1.2"
    assert OBJECT_ADAPTER.validate_python(speed["document"])["recordRevision"] == document.record_revision
    assert OBJECT_ADAPTER.validate_python(saved.detail.calculation_snapshot.result_snapshot["maximum_rpm"])["decimal"] == "1800"
    assert OBJECT_ADAPTER.validate_python(saved.detail.calculation_snapshot.result_snapshot["failure_result"])["cycles_to_failure"] == expected_cycles
    updated = service.update_case_document(
        document.case_document_id,
        document.record_revision,
        {
            "documentKind": "measurement_or_attestation_record",
            "title": "Протокол уточнён",
            "designation": "ПМН-ОТК-01",
            "revisionLabel": "02",
            "documentDate": "2024-07-02",
            "issuer": "ЛИЦ ВВУ",
            "notes": "",
        },
        (),
        (),
        None,
    )
    service.set_case_document_archived(document.case_document_id, updated.record_revision, True, None)
    assert service.get_pmn_calculation_detail(calculation_id, None) == saved.detail
    assert (
        service.create_pmn_calculation(
            analysis_input_snapshot_id=input_id,
            calculation_snapshot_id=calculation_id,
            execution_id=execution.execution_id,
            selection="original",
            selections=selections,
            failure=failure,
            actor="local_user",
            reason="Ручное дополнение и таблица 5",
            deadline=None,
        ).disposition
        == "existing"
    )
    with pytest.raises(ProjectOperationError) as archived:
        service.create_pmn_calculation(
            analysis_input_snapshot_id=str(uuid4()),
            calculation_snapshot_id=str(uuid4()),
            execution_id=execution.execution_id,
            selection="original",
            selections=selections,
            failure=failure,
            actor="local_user",
            reason="Новая запись по архивному документу",
            deadline=None,
        )
    assert archived.value.code in {"entity_archived", "validation_error"}
    service.close()
    service.open(path=str(project_path), application_instance_id="pmn-document-reopen")
    assert service.get_pmn_calculation_detail(calculation_id, None) == saved.detail
    service.close()


def test_pmn_calculation_rollback_conflicting_retry_and_bounded_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_pmn.r130run"))
    selections = _pmn_source_selections()
    input_id, calculation_id = str(uuid4()), str(uuid4())
    with monkeypatch.context() as patch_context:

        def fail_audit(*args: object, **kwargs: object) -> None:
            raise RuntimeError("injected pmn audit failure")

        patch_context.setattr(pmn_calculations_module, "insert_audit", fail_audit)
        with pytest.raises(RuntimeError, match="injected pmn audit failure"):
            service.create_pmn_calculation(
                analysis_input_snapshot_id=input_id,
                calculation_snapshot_id=calculation_id,
                execution_id=execution.execution_id,
                selection="original",
                selections=selections,
                failure=None,
                actor="local_user",
                reason="Проверка атомарности",
                deadline=None,
            )
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        assert connection.execute("SELECT count(*) FROM pmn_analysis_input_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM pmn_calculation_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM project_audit_events WHERE event_type='pmn_calculation.created'").fetchone()[0] == 0
    saved = service.create_pmn_calculation(
        analysis_input_snapshot_id=input_id,
        calculation_snapshot_id=calculation_id,
        execution_id=execution.execution_id,
        selection="original",
        selections=selections,
        failure=None,
        actor="local_user",
        reason="Проверка атомарности",
        deadline=None,
    )
    assert saved.disposition == "created"
    for changed_input_id, changed_calculation_id, changed_reason in (
        (input_id, calculation_id, "Иное основание"),
        (input_id, str(uuid4()), "Проверка атомарности"),
        (str(uuid4()), calculation_id, "Проверка атомарности"),
    ):
        with pytest.raises(ProjectOperationError) as conflicting:
            service.create_pmn_calculation(
                analysis_input_snapshot_id=changed_input_id,
                calculation_snapshot_id=changed_calculation_id,
                execution_id=execution.execution_id,
                selection="original",
                selections=selections,
                failure=None,
                actor="local_user",
                reason=changed_reason,
                deadline=None,
            )
        assert conflicting.value.code == "revision_conflict"
    with pytest.raises(ProjectOperationError) as wrong_method:
        service.get_rpt_calculation_detail(calculation_id, None)
    assert wrong_method.value.code == "entity_not_found"
    second_id = str(uuid4())
    service.create_pmn_calculation(
        analysis_input_snapshot_id=str(uuid4()),
        calculation_snapshot_id=second_id,
        execution_id=execution.execution_id,
        selection="effective",
        selections=selections,
        failure=None,
        actor="local_user",
        reason="Вторая запись",
        deadline=None,
    )
    first_page = service.list_pmn_calculation_page(execution.wheel_model_id, None, 1, None)
    assert [item.calculation_snapshot_id for item in first_page.items] == [second_id]
    assert first_page.next_cursor is not None
    second_page = service.list_pmn_calculation_page(execution.wheel_model_id, first_page.next_cursor, 1, None)
    assert [item.calculation_snapshot_id for item in second_page.items] == [calculation_id]
    assert second_page.next_cursor is None
    with pytest.raises(ProjectOperationError) as too_large:
        service.list_pmn_calculation_page(execution.wheel_model_id, None, 51, None)
    assert too_large.value.code == "validation_error"
    service.close()


def test_pmn_calculation_four_typed_dispatch_operations_and_bounded_envelope(tmp_path: Path) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_pmn.r130run"))
    service.close()
    dispatcher = Dispatcher(tmp_path)

    def request(operation: str, payload: dict[str, object], revision: int) -> object:
        envelope = REQUEST_ENVELOPE_ADAPTER.validate_python(
            {
                "protocolVersion": 1,
                "requestId": f"pmn-{revision}",
                "kind": "request",
                "operation": operation,
                "revision": revision,
                "deadlineMs": 30_000 if operation in {"pmnCalculation.getSourceInputs", "pmnCalculation.create"} else 5_000,
                "payload": payload,
            }
        )
        response = dispatcher.dispatch(envelope)
        assert len(response.model_dump_json().encode("utf-8")) < 1_048_576
        return response.result

    request("project.open", {"path": str(project_path), "applicationInstanceId": str(uuid4())}, 1)
    source = request("pmnCalculation.getSourceInputs", {"executionId": execution.execution_id, "planSelection": "effective"}, 2)
    assert isinstance(source, PmnPlanSourceResult)
    assert source.sourceValues.speedFactor == "1.1"
    assert source.sourceValues.targetCycles == "2"
    assert source.methodicalRequirements.targetMaxRpmExact == "1650"
    assert source.executionTargets.targetCycles == "2"
    input_id, calculation_id = str(uuid4()), str(uuid4())
    written = request(
        "pmnCalculation.create",
        {
            "analysisInputSnapshotId": input_id,
            "calculationSnapshotId": calculation_id,
            "executionId": execution.execution_id,
            "planSelection": "effective",
            "selections": [
                {"field": field, "origin": "source"}
                for field in (
                    "nominal_rpm",
                    "speed_factor",
                    "target_cycles",
                    "acceleration_duration_s",
                    "steady_duration_s",
                    "deceleration_duration_s",
                )
            ],
            "failureEvidence": None,
            "actor": "local_user",
            "reason": "Проверка production dispatcher",
        },
        3,
    )
    assert isinstance(written, PmnCalculationWriteResultModel)
    assert written.detail.calculationSnapshot.resultSnapshot.maximum_rpm.decimal == "1650"
    page = request("pmnCalculation.listPage", {"wheelModelId": execution.wheel_model_id}, 4)
    assert isinstance(page, PmnCalculationPageResult)
    assert [item.calculationSnapshotId for item in page.items] == [calculation_id]
    detail = request("pmnCalculation.getDetail", {"calculationSnapshotId": calculation_id}, 5)
    assert isinstance(detail, PmnCalculationDetailResult)
    assert detail == written.detail
    dispatcher.close()


@pytest.mark.parametrize(
    "damage",
    [
        "speed_factor_nan",
        "target_cycles_fraction",
        "target_cycles_zero",
        "cycle_zero",
        "failure_duration_nan",
        "failure_duration_too_large",
        "wrong_unit",
        "wrong_coordinate",
        "wrong_plan_path",
        "wrong_nested_type",
        "wrong_failure_reason",
        "wrong_result_frequency",
        "wrong_result_cycle_duration",
        "wrong_result_failure_count",
        "changed_imported_speed_factor",
        "changed_imported_target_cycles",
    ],
)
def test_pmn_resealed_invalid_snapshot_is_rejected_by_detail_and_reopen(tmp_path: Path, damage: str) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_pmn.r130run"))
    document = service.create_case_document(
        str(uuid4()),
        {
            "documentKind": "measurement_or_attestation_record",
            "title": "Протокол отказа",
            "designation": "ПМН-ОТК-02",
            "revisionLabel": "01",
            "documentDate": "2024-07-02",
            "issuer": "ЛИЦ ВВУ",
            "notes": "",
        },
        (),
        (),
        None,
    )
    evidence = PmnEvidenceReference(document.case_document_id, document.record_revision, "раздел 3")
    chosen: dict[PmnInputField, str] = {
        "nominal_rpm": "1500",
        "speed_factor": "1.2",
        "target_cycles": "2",
        "acceleration_duration_s": "2",
        "steady_duration_s": "1",
        "deceleration_duration_s": "2",
    }
    saved = service.create_pmn_calculation(
        analysis_input_snapshot_id=str(uuid4()),
        calculation_snapshot_id=str(uuid4()),
        execution_id=execution.execution_id,
        selection="original",
        selections=tuple(PmnFieldSelection(field=field, origin="manual", manual_value=value, basis="Сценарий инженера") for field, value in chosen.items()),
        failure=PmnFailureEvidence("exact_supported", "8", "От начала до отказа", evidence),
        actor="local_user",
        reason="Проверка сохранённого входа",
        deadline=None,
    )
    input_payload = dict(saved.detail.input_snapshot.input_snapshot)
    result_payload = dict(saved.detail.calculation_snapshot.result_snapshot)
    if damage in {"speed_factor_nan", "target_cycles_fraction", "target_cycles_zero", "cycle_zero"}:
        field = "speed_factor" if damage == "speed_factor_nan" else "target_cycles" if damage.startswith("target_cycles") else "steady_duration_s"
        value = "NaN" if damage == "speed_factor_nan" else "2.5" if damage == "target_cycles_fraction" else "0"
        operation = OBJECT_ADAPTER.validate_python(input_payload["operation"])
        operation_selections = FIELD_SELECTIONS_ADAPTER.validate_python(operation["selections"])
        saved_selections = FIELD_SELECTIONS_ADAPTER.validate_python(input_payload["fieldSelections"])
        for item in operation_selections:
            if item["field"] == field or (damage == "cycle_zero" and item["field"] in {"acceleration_duration_s", "deceleration_duration_s"}):
                item["manual_value"] = value
        for item in saved_selections:
            if item["field"] == field or (damage == "cycle_zero" and item["field"] in {"acceleration_duration_s", "deceleration_duration_s"}):
                item["value"] = value
        operation["selections"] = operation_selections
        input_payload["operation"] = operation
        input_payload["fieldSelections"] = saved_selections
    elif damage in {"failure_duration_nan", "failure_duration_too_large"}:
        value = "NaN" if damage.endswith("nan") else "1000000000001"
        operation = OBJECT_ADAPTER.validate_python(input_payload["operation"])
        operation_failure = OBJECT_ADAPTER.validate_python(operation["failureEvidence"])
        saved_failure = OBJECT_ADAPTER.validate_python(input_payload["failureEvidence"])
        operation_failure["duration_to_failure_s"] = value
        saved_failure["durationToFailureS"] = value
        operation["failureEvidence"] = operation_failure
        input_payload["operation"] = operation
        input_payload["failureEvidence"] = saved_failure
    elif damage in {"wrong_unit", "wrong_coordinate"}:
        saved_selections = FIELD_SELECTIONS_ADAPTER.validate_python(input_payload["fieldSelections"])
        selected = next(item for item in saved_selections if item["field"] == "target_cycles")
        selected["unit" if damage == "wrong_unit" else "sourceReference"] = "rpm" if damage == "wrong_unit" else "plan/original.json#/execution_targets/target_cycles"
        input_payload["fieldSelections"] = saved_selections
    elif damage == "wrong_nested_type":
        source = OBJECT_ADAPTER.validate_python(input_payload["source"])
        requirements = OBJECT_ADAPTER.validate_python(source["methodicalRequirements"])
        requirements["target_max_rpm_exact"] = ["invalid"]
        source["methodicalRequirements"] = requirements
        input_payload["source"] = source
    elif damage == "wrong_plan_path":
        source = OBJECT_ADAPTER.validate_python(input_payload["source"])
        source["payloadPath"] = "plan/effective.json"
        input_payload["source"] = source
    elif damage == "changed_imported_speed_factor":
        source = OBJECT_ADAPTER.validate_python(input_payload["source"])
        source_values = OBJECT_ADAPTER.validate_python(source["sourceValues"])
        source_values["speed_factor"] = "1.3"
        source["sourceValues"] = source_values
        input_payload["source"] = source
        selections = FIELD_SELECTIONS_ADAPTER.validate_python(input_payload["fieldSelections"])
        next(item for item in selections if item["field"] == "speed_factor")["rawSourceValue"] = "1.3"
        input_payload["fieldSelections"] = selections
    elif damage == "changed_imported_target_cycles":
        source = OBJECT_ADAPTER.validate_python(input_payload["source"])
        targets = OBJECT_ADAPTER.validate_python(source["executionTargets"])
        targets["target_cycles"] = "3"
        source["executionTargets"] = targets
        input_payload["source"] = source
    elif damage == "wrong_failure_reason":
        failure_result = OBJECT_ADAPTER.validate_python(result_payload["failure_result"])
        failure_result.update(status="not_applicable", cycles_to_failure=None, reason_code="failure_cycle_variable")
        result_payload["failure_result"] = failure_result
    elif damage == "wrong_result_frequency":
        changed = asdict(exact_value(Fraction(1801)))
        result_payload["maximum_rpm"] = changed
        phases = FIELD_SELECTIONS_ADAPTER.validate_python(result_payload["phases"])
        phases[0]["end_rpm"] = changed
        phases[1]["start_rpm"] = changed
        phases[1]["end_rpm"] = changed
        phases[2]["start_rpm"] = changed
        result_payload["phases"] = phases
    elif damage == "wrong_result_cycle_duration":
        result_payload["cycle_duration_s_exact"] = asdict(exact_value(Fraction(6)))
        result_payload["total_duration_s_exact"] = asdict(exact_value(Fraction(12)))
        result_payload["total_duration_min_exact"] = asdict(exact_value(Fraction(1, 5)))
        result_payload["total_duration_h_exact"] = asdict(exact_value(Fraction(1, 300)))
        phases = FIELD_SELECTIONS_ADAPTER.validate_python(result_payload["phases"])
        phases[2]["end_s"] = asdict(exact_value(Fraction(6)))
        result_payload["phases"] = phases
    else:
        failure_result = OBJECT_ADAPTER.validate_python(result_payload["failure_result"])
        failure_result["cycles_to_failure"] = "3"
        result_payload["failure_result"] = failure_result
    if damage.startswith("wrong_result_"):
        PmnReferenceResultModel.model_validate_json(_canonical_package_json(result_payload))
    _reseal_saved_calculation_snapshot(project_path, saved, input_payload, result_payload, "pmn")
    with pytest.raises(ProjectOperationError) as detail_corrupted:
        service.get_pmn_calculation_detail(saved.detail.calculation_snapshot.calculation_snapshot_id, None)
    assert detail_corrupted.value.code == "corrupt_project"
    service.close()
    with pytest.raises(ProjectOperationError) as reopen_corrupted:
        service.open(path=str(project_path), application_instance_id="pmn-resealed-invalid")
    assert reopen_corrupted.value.code == "corrupt_project"


def test_pmn_source_reader_keeps_importer_valid_lexeme_outside_analytic_range(tmp_path: Path) -> None:
    synthetic = _package_with_plan_values(
        tmp_path,
        base_name="normal_final_pmn.r130run",
        output_name="pmn-wide-source.r130run",
        source_updates={"speed_factor": "1000001"},
        requirement_updates={},
        target_updates={},
    )
    service, _, _, execution = _project_with_rbd_execution(tmp_path, synthetic)
    source = service.get_pmn_source_inputs(execution.execution_id, "effective", None)
    assert source.source_values.speed_factor == "1000001"
    service.close()


def test_pmn_source_reader_distinguishes_missing_null_and_zero(tmp_path: Path) -> None:
    synthetic = _package_with_plan_values(
        tmp_path,
        base_name="normal_final_pmn.r130run",
        output_name="pmn-missing-null-zero.r130run",
        source_updates={"steady_duration_s": None, "acceleration_duration_s": 0},
        requirement_updates={},
        target_updates={},
        source_removals=("deceleration_duration_s",),
    )
    service, _, _, execution = _project_with_rbd_execution(tmp_path, synthetic)
    source = service.get_pmn_source_inputs(execution.execution_id, "original", None)
    assert source.source_values.steady_duration_s is None
    assert source.source_values.deceleration_duration_s is None
    assert source.source_values.acceleration_duration_s == "0"
    service.close()


def test_pmn_source_reader_preserves_decimal_json_lexeme_without_float_rounding(tmp_path: Path) -> None:
    base = _package("normal_final_pmn.r130run")
    with ZipFile(base) as archive:
        original_bytes = archive.read("plan/original.json")
        effective = OBJECT_ADAPTER.validate_json(archive.read("plan/effective.json"))
    old_lexeme = b'"speed_factor":"1.1"'
    new_lexeme = b'"speed_factor":0.10000000000000001'
    assert original_bytes.count(old_lexeme) == 1
    original_bytes = original_bytes.replace(old_lexeme, new_lexeme)
    effective_container = OBJECT_ADAPTER.validate_python(effective["effective_plan"])
    effective_container["original_plan_sha256"] = hashlib.sha256(original_bytes).hexdigest()
    effective["effective_plan"] = effective_container
    effective_bytes = _canonical_package_json(effective)
    assert effective_bytes.count(old_lexeme) == 1
    effective_bytes = effective_bytes.replace(old_lexeme, new_lexeme)
    synthetic = build_synthetic_r130run(
        tmp_path / "pmn-raw-numeric-lexeme.r130run",
        base_package=base,
        payload_overrides={"plan/original.json": original_bytes, "plan/effective.json": effective_bytes},
    )
    service, _, _, execution = _project_with_rbd_execution(tmp_path, synthetic)
    for selection in ("original", "effective"):
        source = service.get_pmn_source_inputs(execution.execution_id, selection, None)
        assert source.source_values.speed_factor == "0.10000000000000001"
    service.close()


@pytest.mark.parametrize("invalid", [True, [], {"unexpected": "value"}])
def test_pmn_source_reader_rejects_wrong_nested_scalar_type(tmp_path: Path, invalid: object) -> None:
    synthetic = _package_with_plan_values(
        tmp_path,
        base_name="normal_final_pmn.r130run",
        output_name=f"pmn-wrong-scalar-{uuid4()}.r130run",
        source_updates={"speed_factor": invalid},
        requirement_updates={},
        target_updates={},
    )
    service, _, _, execution = _project_with_rbd_execution(tmp_path, synthetic)
    with pytest.raises(ProjectOperationError) as rejected:
        service.get_pmn_source_inputs(execution.execution_id, "original", None)
    assert rejected.value.code == "corrupt_project"
    service.close()


def test_rbd_verified_plan_reader_keeps_importer_valid_large_plan(tmp_path: Path) -> None:
    synthetic = _package_with_plan_values(
        tmp_path,
        base_name="normal_final_rbd.r130run",
        output_name="rbd-large-plan.r130run",
        source_updates={"unrelated_note": "x" * 70_000},
        requirement_updates={},
        target_updates={},
    )
    service, _, _, execution = _project_with_rbd_execution(tmp_path, synthetic)
    source = service.get_rbd_source_inputs(execution.execution_id, "original", None)
    assert source.source_values.nominal_rpm is not None
    service.close()


@pytest.mark.parametrize("selection", ["original", "effective"])
@pytest.mark.parametrize(
    ("package_name", "policy", "lower_rpm"),
    [
        ("normal_final_rpt_one_percent.r130run", "one_percent", "15"),
        ("normal_final_rpt_full_stop.r130run", "full_stop", "0"),
    ],
)
def test_rpt_source_inputs_keep_original_fields_requirements_targets_and_coordinates(
    tmp_path: Path,
    selection: Literal["original", "effective"],
    package_name: str,
    policy: str,
    lower_rpm: str,
) -> None:
    service, project_path, imported, execution = _project_with_rbd_execution(tmp_path, _package(package_name))
    source = service.get_rpt_source_inputs(execution.execution_id, selection, None)
    assert source.execution_id == execution.execution_id
    assert source.local_import_id == imported.local_import_id
    assert source.selection == selection
    assert source.source_values.steady_duration_s == "0"
    assert source.source_values.lower_point_policy == policy
    assert source.source_values.explicit_lower_rpm is None
    assert source.methodical_requirements.required_cycles_exact == "2"
    assert source.methodical_requirements.cycle_duration_s_exact == "4"
    assert source.methodical_requirements.required_total_duration_s_exact == "8"
    assert source.execution_targets.target_cycles == "2"
    assert source.execution_targets.lower_rpm == lower_rpm
    assert source.execution_targets.lower_point_policy == policy
    assert source.execution_targets.rounding_policy == "ceiling"

    expected_member = "plan/original.json" if selection == "original" else "plan/effective.json"
    expected_prefix = "/source_values/" if selection == "original" else "/effective_plan/effective_plan/source_values/"
    assert source.payload_path == expected_member
    with ZipFile(_managed_path(project_path, imported)) as archive:
        payload = archive.read(expected_member)
        assert hashlib.sha256(payload).hexdigest() == source.payload_sha256
        for field in (
            "nominal_rpm",
            "design_cycles",
            "reserve_factor",
            "acceleration_duration_s",
            "steady_duration_s",
            "deceleration_duration_s",
        ):
            reference = r130sh_sources_module.plan_source_field_reference(selection, field)
            member, pointer = reference.split("#", 1)
            assert member == expected_member
            assert pointer == f"{expected_prefix}{field}"
            current: object = OBJECT_ADAPTER.validate_json(archive.read(member))
            for segment in pointer[1:].split("/"):
                current = OBJECT_ADAPTER.validate_python(current)[segment]
            assert current == getattr(source.source_values, field)
    service.close()


def test_rpt_source_reader_rejects_rbd_method_and_wrong_import_pair(tmp_path: Path) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_rpt_one_percent.r130run"))
    other = _import(service, project_path, _package("normal_final_rpt_full_stop.r130run"))
    with pytest.raises(ProjectOperationError) as wrong_import:
        service.read_rpt_plan_source(execution.execution_id, other.local_import_id, "original", None)
    assert wrong_import.value.code == "entity_not_found"
    service.close()

    rbd_project_root = tmp_path / "rbd"
    rbd_project_root.mkdir()
    rbd_service, _, _, rbd_execution = _project_with_rbd_execution(rbd_project_root)
    with pytest.raises(ProjectOperationError) as wrong_method:
        rbd_service.get_rpt_source_inputs(rbd_execution.execution_id, "original", None)
    assert wrong_method.value.code == "entity_not_found"
    rbd_service.close()


@pytest.mark.parametrize("selection", ["original", "effective"])
def test_rpt_source_reader_preserves_explicit_lower_point(tmp_path: Path, selection: Literal["original", "effective"]) -> None:
    synthetic = _package_with_explicit_rpt_lower_point(tmp_path)
    service, _, _, execution = _project_with_rbd_execution(tmp_path, synthetic)
    source = service.get_rpt_source_inputs(execution.execution_id, selection, None)
    assert source.source_values.lower_point_policy == "explicit_rpm"
    assert source.source_values.explicit_lower_rpm == "125.5"
    assert source.execution_targets.lower_point_policy == "explicit_rpm"
    assert source.execution_targets.lower_rpm == "125.5"
    assert source.methodical_requirements.required_cycles_exact == "2"
    service.close()


def test_rpt_source_reader_keeps_importer_valid_lexeme_outside_analytic_range(tmp_path: Path) -> None:
    design_cycles = "1000000000001"
    synthetic = _package_with_rpt_plan_values(
        tmp_path,
        suffix="large-design-cycles",
        source_updates={"design_cycles": design_cycles},
        requirement_updates={"required_cycles_exact": design_cycles, "required_total_duration_s_exact": "4000000000004"},
        target_updates={"target_cycles": 1000000000001, "total_duration_s": "4000000000004"},
    )
    service, _, _, execution = _project_with_rbd_execution(tmp_path, synthetic)
    source = service.get_rpt_source_inputs(execution.execution_id, "effective", None)
    assert source.source_values.design_cycles == design_cycles
    assert source.methodical_requirements.required_cycles_exact == design_cycles
    service.close()


def test_rpt_source_reader_preserves_importer_valid_numeric_json_scalars(tmp_path: Path) -> None:
    synthetic = _package_with_rpt_plan_values(
        tmp_path,
        suffix="numeric-scalars",
        source_updates={"reserve_factor": 1.25, "steady_duration_s": 0},
        requirement_updates={"required_cycles_exact": "2.5", "required_total_duration_s_exact": "10"},
        target_updates={"target_cycles": 3, "total_duration_s": "12"},
    )
    service, _, _, execution = _project_with_rbd_execution(tmp_path, synthetic)
    source = service.get_rpt_source_inputs(execution.execution_id, "effective", None)
    assert source.source_values.reserve_factor == "1.25"
    assert source.source_values.steady_duration_s == "0"
    assert source.methodical_requirements.required_cycles_exact == "2.5"
    assert source.execution_targets.target_cycles == "3"
    service.close()


def test_rpt_source_reader_preserves_numeric_json_lexeme_without_float_rounding(tmp_path: Path) -> None:
    base = _package("normal_final_rpt_full_stop.r130run")
    with ZipFile(base) as archive:
        original_bytes = archive.read("plan/original.json")
        effective = OBJECT_ADAPTER.validate_json(archive.read("plan/effective.json"))
    old_lexeme = b'"reserve_factor":"1"'
    new_lexeme = b'"reserve_factor":0.10000000000000001'
    assert original_bytes.count(old_lexeme) == 1
    original_bytes = original_bytes.replace(old_lexeme, new_lexeme)
    effective_container = OBJECT_ADAPTER.validate_python(effective["effective_plan"])
    effective_container["original_plan_sha256"] = hashlib.sha256(original_bytes).hexdigest()
    effective["effective_plan"] = effective_container
    effective_bytes = _canonical_package_json(effective)
    assert effective_bytes.count(old_lexeme) == 1
    effective_bytes = effective_bytes.replace(old_lexeme, new_lexeme)
    synthetic = build_synthetic_r130run(
        tmp_path / "rpt-raw-numeric-lexeme.r130run",
        base_package=base,
        payload_overrides={"plan/original.json": original_bytes, "plan/effective.json": effective_bytes},
    )
    service, _, _, execution = _project_with_rbd_execution(tmp_path, synthetic)
    for selection in ("original", "effective"):
        source = service.get_rpt_source_inputs(execution.execution_id, selection, None)
        assert source.source_values.reserve_factor == "0.10000000000000001"
    service.close()


def test_rpt_source_reader_keeps_bounded_access_to_importer_valid_large_plan(tmp_path: Path) -> None:
    synthetic = _package_with_rpt_plan_values(
        tmp_path,
        suffix="large-plan",
        source_updates={"unrelated_note": "x" * 70_000},
        requirement_updates={},
        target_updates={},
    )
    service, _, _, execution = _project_with_rbd_execution(tmp_path, synthetic)
    source = service.get_rpt_source_inputs(execution.execution_id, "original", None)
    assert source.source_values.design_cycles == "2"
    service.close()


@pytest.mark.parametrize("damage", ["missing_archive", "modified_archive", "plan_id", "plan_hash", "oversized_plan"])
def test_rpt_source_reader_rejects_missing_modified_or_conflicting_evidence(tmp_path: Path, damage: str) -> None:
    service, project_path, imported, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_rpt_one_percent.r130run"))
    assert service.get_rpt_source_inputs(execution.execution_id, "original", None).execution_id == execution.execution_id
    managed_path = _managed_path(project_path, imported)
    if damage == "missing_archive":
        managed_path.unlink()
    elif damage == "modified_archive":
        managed_path.write_bytes(b"modified")
    elif damage == "oversized_plan":
        with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
            connection.execute("DROP TRIGGER r130sh_source_inventory_no_update")
            connection.execute(
                "UPDATE r130sh_source_inventory SET size_bytes=? WHERE local_import_id=? AND path='plan/original.json'",
                (MAX_JSON_BYTES + 1, imported.local_import_id),
            )
            connection.commit()
    else:
        column = "original_plan_id" if damage == "plan_id" else "original_plan_sha256"
        value = "wrong-plan" if damage == "plan_id" else "0" * 64
        with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
            connection.execute("DROP TRIGGER r130sh_run_projections_no_update")
            connection.execute(
                f"UPDATE r130sh_run_projections SET {column}=? WHERE local_import_id=?",
                (value, imported.local_import_id),
            )
            connection.commit()
    with pytest.raises(ProjectOperationError) as rejected:
        service.get_rpt_source_inputs(execution.execution_id, "original", None)
    assert rejected.value.code == ("file_integrity_mismatch" if damage in {"missing_archive", "modified_archive"} else "corrupt_project")
    service.close()


def test_rpt_calculation_source_pair_history_and_reopen(tmp_path: Path) -> None:
    service, project_path, imported, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_rpt_full_stop.r130run"))
    input_id = str(uuid4())
    calculation_id = str(uuid4())
    selections = tuple(
        RptFieldSelection(field=field, origin="source")
        for field in (
            "nominal_rpm",
            "design_cycles",
            "reserve_factor",
            "acceleration_duration_s",
            "steady_duration_s",
            "deceleration_duration_s",
        )
    )
    saved = service.create_rpt_calculation(
        analysis_input_snapshot_id=input_id,
        calculation_snapshot_id=calculation_id,
        execution_id=execution.execution_id,
        selection="effective",
        selections=selections,
        failure=None,
        actor=" local_user ",
        reason="  Расчёт РПТ по выбранному источнику  ",
        deadline=None,
    )
    assert saved.disposition == "created"
    assert saved.detail.input_snapshot.actor == "local_user"
    assert saved.detail.input_snapshot.decision_reason == "Расчёт РПТ по выбранному источнику"
    source_snapshot = OBJECT_ADAPTER.validate_python(saved.detail.input_snapshot.input_snapshot["source"])
    targets = OBJECT_ADAPTER.validate_python(source_snapshot["executionTargets"])
    minimum = OBJECT_ADAPTER.validate_python(saved.detail.calculation_snapshot.result_snapshot["minimum_rpm"])
    total = OBJECT_ADAPTER.validate_python(saved.detail.calculation_snapshot.result_snapshot["total_duration_s_exact"])
    failure_result = OBJECT_ADAPTER.validate_python(saved.detail.calculation_snapshot.result_snapshot["failure_result"])
    assert source_snapshot["localImportId"] == imported.local_import_id
    assert targets["lower_point_policy"] == "full_stop"
    assert minimum["decimal"] == "15"
    assert total["decimal"] == "8"
    assert failure_result["status"] == "not_applicable"
    comparison = OBJECT_ADAPTER.validate_python(saved.detail.calculation_snapshot.result_snapshot["lower_point_comparison"])
    assert comparison["source_policy"] == "full_stop"
    assert comparison["target_lower_rpm"] == "0"
    assert comparison["status"] == "differs_from_typical_formula"
    assert service.get_rpt_calculation_detail(calculation_id, None) == saved.detail
    page = service.list_rpt_calculation_page(execution.wheel_model_id, None, 25, None)
    assert [item.calculation_snapshot_id for item in page.items] == [calculation_id]
    assert page.next_cursor is None
    assert (
        service.create_rpt_calculation(
            analysis_input_snapshot_id=input_id,
            calculation_snapshot_id=calculation_id,
            execution_id=execution.execution_id,
            selection="effective",
            selections=selections,
            failure=None,
            actor=" local_user ",
            reason="  Расчёт РПТ по выбранному источнику  ",
            deadline=None,
        ).disposition
        == "existing"
    )
    service.close()
    _managed_path(project_path, imported).unlink()
    service.open(path=str(project_path), application_instance_id="rpt-reopen")
    assert service.get_rpt_calculation_detail(calculation_id, None) == saved.detail
    service.close()


@pytest.mark.parametrize(("failure_duration", "expected_cycles"), [("8", "2"), ("0", "0")])
def test_rpt_manual_inputs_and_documented_table_4_survive_document_change(tmp_path: Path, failure_duration: str, expected_cycles: str) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_rpt_full_stop.r130run"))
    document = service.create_case_document(
        str(uuid4()),
        {
            "documentKind": "measurement_or_attestation_record",
            "title": "Протокол точного времени до отказа",
            "designation": "РПТ-ОТК-01",
            "revisionLabel": "01",
            "documentDate": "2024-07-02",
            "issuer": "ЛИЦ ВВУ",
            "notes": "",
        },
        (),
        (),
        None,
    )
    evidence = RptEvidenceReference(
        document_id=document.case_document_id,
        document_record_revision=document.record_revision,
        document_locator="раздел 3, момент отказа и начало отсчёта",
    )
    selections = tuple(
        RptFieldSelection(
            field=field,
            origin="manual" if field == "reserve_factor" else "source",
            manual_value="1.5" if field == "reserve_factor" else None,
            basis="Коэффициент принят инженером для сценария" if field == "reserve_factor" else "",
            evidence=evidence if field == "reserve_factor" else None,
        )
        for field in (
            "nominal_rpm",
            "design_cycles",
            "reserve_factor",
            "acceleration_duration_s",
            "steady_duration_s",
            "deceleration_duration_s",
        )
    )
    failure = RptFailureEvidence(
        applicability="exact_supported",
        duration_to_failure_s=failure_duration,
        basis="Время до установленного отказа соответствует одному запуску и началу отсчёта",
        evidence=evidence,
    )
    input_id, calculation_id = str(uuid4()), str(uuid4())
    saved = service.create_rpt_calculation(
        analysis_input_snapshot_id=input_id,
        calculation_snapshot_id=calculation_id,
        execution_id=execution.execution_id,
        selection="original",
        selections=selections,
        failure=failure,
        actor="local_user",
        reason="Ручное дополнение с документом",
        deadline=None,
    )
    saved_fields = FIELD_SELECTIONS_ADAPTER.validate_python(saved.detail.input_snapshot.input_snapshot["fieldSelections"])
    reserve = next(item for item in saved_fields if item["field"] == "reserve_factor")
    assert reserve["rawSourceValue"] == "1"
    assert reserve["value"] == "1.5"
    assert reserve["origin"] == "manual"
    assert OBJECT_ADAPTER.validate_python(reserve["document"])["recordRevision"] == document.record_revision
    result = saved.detail.calculation_snapshot.result_snapshot
    assert OBJECT_ADAPTER.validate_python(result["required_cycles_exact"])["decimal"] == "3"
    assert OBJECT_ADAPTER.validate_python(result["total_duration_s_exact"])["decimal"] == "12"
    assert OBJECT_ADAPTER.validate_python(result["failure_result"])["cycles_to_failure"] == expected_cycles
    updated = service.update_case_document(
        document.case_document_id,
        document.record_revision,
        {
            "documentKind": "measurement_or_attestation_record",
            "title": "Протокол уточнён",
            "designation": "РПТ-ОТК-01",
            "revisionLabel": "02",
            "documentDate": "2024-07-02",
            "issuer": "ЛИЦ ВВУ",
            "notes": "",
        },
        (),
        (),
        None,
    )
    service.set_case_document_archived(document.case_document_id, updated.record_revision, True, None)
    assert service.get_rpt_calculation_detail(calculation_id, None) == saved.detail
    assert (
        service.create_rpt_calculation(
            analysis_input_snapshot_id=input_id,
            calculation_snapshot_id=calculation_id,
            execution_id=execution.execution_id,
            selection="original",
            selections=selections,
            failure=failure,
            actor="local_user",
            reason="Ручное дополнение с документом",
            deadline=None,
        ).disposition
        == "existing"
    )
    with pytest.raises(ProjectOperationError) as archived:
        service.create_rpt_calculation(
            analysis_input_snapshot_id=str(uuid4()),
            calculation_snapshot_id=str(uuid4()),
            execution_id=execution.execution_id,
            selection="original",
            selections=selections,
            failure=failure,
            actor="local_user",
            reason="Новая запись по архивному документу",
            deadline=None,
        )
    assert archived.value.code in {"entity_archived", "validation_error"}
    service.close()
    service.open(path=str(project_path), application_instance_id="rpt-document-reopen")
    assert service.get_rpt_calculation_detail(calculation_id, None) == saved.detail
    service.close()


def test_rpt_calculation_rollback_conflicting_retry_and_method_scoped_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_rpt_one_percent.r130run"))
    selections = tuple(
        RptFieldSelection(field=field, origin="source")
        for field in (
            "nominal_rpm",
            "design_cycles",
            "reserve_factor",
            "acceleration_duration_s",
            "steady_duration_s",
            "deceleration_duration_s",
        )
    )
    input_id, calculation_id = str(uuid4()), str(uuid4())
    with monkeypatch.context() as patch_context:

        def fail_audit(*args: object, **kwargs: object) -> None:
            raise RuntimeError("injected rpt audit failure")

        patch_context.setattr(rpt_calculations_module, "insert_audit", fail_audit)
        with pytest.raises(RuntimeError, match="injected rpt audit failure"):
            service.create_rpt_calculation(
                analysis_input_snapshot_id=input_id,
                calculation_snapshot_id=calculation_id,
                execution_id=execution.execution_id,
                selection="original",
                selections=selections,
                failure=None,
                actor="local_user",
                reason="Проверка атомарности",
                deadline=None,
            )
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        assert connection.execute("SELECT count(*) FROM rpt_analysis_input_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM rpt_calculation_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM project_audit_events WHERE event_type='rpt_calculation.created'").fetchone()[0] == 0
    saved = service.create_rpt_calculation(
        analysis_input_snapshot_id=input_id,
        calculation_snapshot_id=calculation_id,
        execution_id=execution.execution_id,
        selection="original",
        selections=selections,
        failure=None,
        actor="local_user",
        reason="Проверка атомарности",
        deadline=None,
    )
    assert saved.disposition == "created"
    for changed_input_id, changed_calculation_id, changed_reason in (
        (input_id, calculation_id, "Иное основание"),
        (input_id, str(uuid4()), "Проверка атомарности"),
        (str(uuid4()), calculation_id, "Проверка атомарности"),
    ):
        with pytest.raises(ProjectOperationError) as conflicting:
            service.create_rpt_calculation(
                analysis_input_snapshot_id=changed_input_id,
                calculation_snapshot_id=changed_calculation_id,
                execution_id=execution.execution_id,
                selection="original",
                selections=selections,
                failure=None,
                actor="local_user",
                reason=changed_reason,
                deadline=None,
            )
        assert conflicting.value.code == "revision_conflict"
    with pytest.raises(ProjectOperationError) as wrong_method:
        service.get_rbd_calculation_detail(calculation_id, None)
    assert wrong_method.value.code == "entity_not_found"
    service.close()


def test_rpt_calculation_four_typed_dispatch_operations_and_bounded_envelope(tmp_path: Path) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_rpt_one_percent.r130run"))
    service.close()
    dispatcher = Dispatcher(tmp_path)

    def request(operation: str, payload: dict[str, object], revision: int) -> object:
        envelope = REQUEST_ENVELOPE_ADAPTER.validate_python(
            {
                "protocolVersion": 1,
                "requestId": f"rpt-{revision}",
                "kind": "request",
                "operation": operation,
                "revision": revision,
                "deadlineMs": 30_000 if operation in {"rptCalculation.getSourceInputs", "rptCalculation.create"} else 5_000,
                "payload": payload,
            }
        )
        response = dispatcher.dispatch(envelope)
        assert len(response.model_dump_json().encode("utf-8")) < 1_048_576
        return response.result

    request("project.open", {"path": str(project_path), "applicationInstanceId": str(uuid4())}, 1)
    source = request(
        "rptCalculation.getSourceInputs",
        {"executionId": execution.execution_id, "planSelection": "effective"},
        2,
    )
    assert isinstance(source, RptPlanSourceResult)
    assert source.sourceValues.lowerPointPolicy == "one_percent"
    input_id, calculation_id = str(uuid4()), str(uuid4())
    written = request(
        "rptCalculation.create",
        {
            "analysisInputSnapshotId": input_id,
            "calculationSnapshotId": calculation_id,
            "executionId": execution.execution_id,
            "planSelection": "effective",
            "selections": [
                {"field": field, "origin": "source"}
                for field in (
                    "nominal_rpm",
                    "design_cycles",
                    "reserve_factor",
                    "acceleration_duration_s",
                    "steady_duration_s",
                    "deceleration_duration_s",
                )
            ],
            "failureEvidence": None,
            "actor": "local_user",
            "reason": "Проверка production dispatcher",
        },
        3,
    )
    assert isinstance(written, RptCalculationWriteResultModel)
    assert written.detail.calculationSnapshot.resultSnapshot.minimum_rpm.decimal == "15"
    page = request("rptCalculation.listPage", {"wheelModelId": execution.wheel_model_id}, 4)
    assert isinstance(page, RptCalculationPageResult)
    assert [item.calculationSnapshotId for item in page.items] == [calculation_id]
    detail = request("rptCalculation.getDetail", {"calculationSnapshotId": calculation_id}, 5)
    assert isinstance(detail, RptCalculationDetailResult)
    assert detail == written.detail
    dispatcher.close()


def test_reopen_rejects_resealed_rpt_result_with_invalid_nested_profile(tmp_path: Path) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_rpt_one_percent.r130run"))
    input_id, calculation_id = str(uuid4()), str(uuid4())
    saved = service.create_rpt_calculation(
        analysis_input_snapshot_id=input_id,
        calculation_snapshot_id=calculation_id,
        execution_id=execution.execution_id,
        selection="original",
        selections=tuple(
            RptFieldSelection(field=field, origin="source")
            for field in (
                "nominal_rpm",
                "design_cycles",
                "reserve_factor",
                "acceleration_duration_s",
                "steady_duration_s",
                "deceleration_duration_s",
            )
        ),
        failure=None,
        actor="local_user",
        reason="Проверка строгого результата РПТ",
        deadline=None,
    )
    service.close()
    result = dict(saved.detail.calculation_snapshot.result_snapshot)
    phases = TypeAdapter(list[object]).validate_python(result["phases"])
    phases[1] = None
    result["phases"] = phases

    def canonical_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))

    calculation_content = {
        "calculationSnapshotId": calculation_id,
        "analysisInputSnapshotId": input_id,
        "inputContentSha256": saved.detail.input_snapshot.content_sha256,
        "result": result,
        "createdAtUtc": saved.detail.calculation_snapshot.created_at_utc,
    }
    content_sha256 = hashlib.sha256(canonical_json(calculation_content).encode("utf-8")).hexdigest()
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        for trigger_name in ("rpt_calculation_snapshots_no_update", "project_audit_events_no_update"):
            trigger_row = connection.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (trigger_name,)).fetchone()
            assert trigger_row is not None
            connection.execute(f"DROP TRIGGER {trigger_name}")
            if trigger_name == "rpt_calculation_snapshots_no_update":
                connection.execute(
                    "UPDATE rpt_calculation_snapshots SET result_snapshot_json=?, content_sha256=? WHERE calculation_snapshot_id=?",
                    (canonical_json(result), content_sha256, calculation_id),
                )
            else:
                audit_row = connection.execute(
                    "SELECT payload_json FROM project_audit_events WHERE event_type='rpt_calculation.created' AND json_extract(payload_json, '$.calculationSnapshotId')=?",
                    (calculation_id,),
                ).fetchone()
                assert audit_row is not None
                audit_payload = OBJECT_ADAPTER.validate_json(str(audit_row[0]))
                audit_payload["calculationContentSha256"] = content_sha256
                connection.execute(
                    "UPDATE project_audit_events SET payload_json=? WHERE event_type='rpt_calculation.created' AND json_extract(payload_json, '$.calculationSnapshotId')=?",
                    (canonical_json(audit_payload), calculation_id),
                )
            connection.execute(str(trigger_row[0]))
        connection.commit()
    with pytest.raises(ProjectOperationError) as corrupted:
        service.open(path=str(project_path), application_instance_id="rpt-resealed-result")
    assert corrupted.value.code == "corrupt_project"


def _reseal_saved_rpt_snapshot(
    project_path: Path,
    saved: RptCalculationWriteResult,
    input_payload: dict[str, object],
    result_payload: dict[str, object],
) -> None:
    _reseal_saved_calculation_snapshot(project_path, saved, input_payload, result_payload, "rpt")


def _reseal_saved_calculation_snapshot(
    project_path: Path,
    saved: RptCalculationWriteResult | PmnCalculationWriteResult,
    input_payload: dict[str, object],
    result_payload: dict[str, object],
    method: Literal["rpt", "pmn"],
) -> None:
    # The test changes all linked hashes and audit evidence, leaving only domain validation to detect corruption.
    detail = saved.detail
    input_snapshot = detail.input_snapshot
    calculation = detail.calculation_snapshot

    def canonical_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))

    def sha256(value: object) -> str:
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

    operation_hash = sha256(input_payload["operation"])
    input_hash = sha256(
        {
            "analysisInputSnapshotId": input_snapshot.analysis_input_snapshot_id,
            "executionId": input_snapshot.execution_id,
            "input": input_payload,
            "actor": input_snapshot.actor,
            "reason": input_snapshot.decision_reason,
            "createdAtUtc": input_snapshot.created_at_utc,
        }
    )
    result_hash = sha256(
        {
            "calculationSnapshotId": calculation.calculation_snapshot_id,
            "analysisInputSnapshotId": input_snapshot.analysis_input_snapshot_id,
            "inputContentSha256": input_hash,
            "result": result_payload,
            "createdAtUtc": calculation.created_at_utc,
        }
    )
    failure_status = OBJECT_ADAPTER.validate_python(result_payload["failure_result"])["status"]
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        triggers: dict[str, str] = {}
        for name in (
            f"{method}_analysis_input_snapshots_no_update",
            f"{method}_calculation_snapshots_no_update",
            "project_audit_events_no_update",
        ):
            row = connection.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()
            assert row is not None
            triggers[name] = str(row[0])
            connection.execute(f"DROP TRIGGER {name}")
        connection.execute(
            f"UPDATE {method}_analysis_input_snapshots SET input_snapshot_json=?, operation_sha256=?, content_sha256=? WHERE analysis_input_snapshot_id=?",
            (canonical_json(input_payload), operation_hash, input_hash, input_snapshot.analysis_input_snapshot_id),
        )
        connection.execute(
            f"UPDATE {method}_calculation_snapshots SET result_snapshot_json=?, failure_status=?, input_content_sha256=?, operation_sha256=?, content_sha256=? WHERE calculation_snapshot_id=?",
            (canonical_json(result_payload), failure_status, input_hash, operation_hash, result_hash, calculation.calculation_snapshot_id),
        )
        audit_row = connection.execute(
            "SELECT payload_json FROM project_audit_events WHERE event_type=? AND json_extract(payload_json, '$.calculationSnapshotId')=?",
            (f"{method}_calculation.created", calculation.calculation_snapshot_id),
        ).fetchone()
        assert audit_row is not None
        audit_payload = OBJECT_ADAPTER.validate_json(str(audit_row[0]))
        audit_payload.update(inputContentSha256=input_hash, calculationContentSha256=result_hash, operationSha256=operation_hash)
        connection.execute(
            "UPDATE project_audit_events SET payload_json=? WHERE event_type=? AND json_extract(payload_json, '$.calculationSnapshotId')=?",
            (canonical_json(audit_payload), f"{method}_calculation.created", calculation.calculation_snapshot_id),
        )
        for sql in triggers.values():
            connection.execute(sql)
        connection.commit()


@pytest.mark.parametrize(
    ("damage", "value"),
    [
        ("reserve_factor", "NaN"),
        ("design_cycles", "3.5"),
        ("design_cycles", "0"),
        ("all_durations", "0"),
        ("failure_duration", "NaN"),
        ("failure_duration", "1000000000001"),
        ("failure_reason", "failure_cycle_variable"),
    ],
)
def test_reopen_rejects_resealed_rpt_inputs_outside_algorithm_domain(tmp_path: Path, damage: str, value: str) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path, _package("normal_final_rpt_full_stop.r130run"))
    document = service.create_case_document(
        str(uuid4()),
        {
            "documentKind": "measurement_or_attestation_record",
            "title": "Протокол отказа",
            "designation": "РПТ-ОТК-01",
            "revisionLabel": "01",
            "documentDate": "2024-07-02",
            "issuer": "ЛИЦ ВВУ",
            "notes": "",
        },
        (),
        (),
        None,
    )
    evidence = RptEvidenceReference(document.case_document_id, document.record_revision, "раздел 3")
    chosen: dict[RptInputField, str] = {
        "nominal_rpm": "1500",
        "design_cycles": "2",
        "reserve_factor": "1.5",
        "acceleration_duration_s": "1",
        "steady_duration_s": "2",
        "deceleration_duration_s": "1",
    }
    saved = service.create_rpt_calculation(
        analysis_input_snapshot_id=str(uuid4()),
        calculation_snapshot_id=str(uuid4()),
        execution_id=execution.execution_id,
        selection="original",
        selections=tuple(RptFieldSelection(field=field, origin="manual", manual_value=selected, basis="Сценарий инженера") for field, selected in chosen.items()),
        failure=RptFailureEvidence("exact_supported", "8", "От начала до отказа", evidence),
        actor="local_user",
        reason="Проверка сохранённого входа",
        deadline=None,
    )
    input_payload = dict(saved.detail.input_snapshot.input_snapshot)
    result_payload = dict(saved.detail.calculation_snapshot.result_snapshot)
    if damage in chosen:
        operation = OBJECT_ADAPTER.validate_python(input_payload["operation"])
        operation_selections = FIELD_SELECTIONS_ADAPTER.validate_python(operation["selections"])
        for item in operation_selections:
            if item["field"] == damage:
                item["manual_value"] = value
        operation["selections"] = operation_selections
        input_payload["operation"] = operation
        selections = FIELD_SELECTIONS_ADAPTER.validate_python(input_payload["fieldSelections"])
        for item in selections:
            if item["field"] == damage:
                item["value"] = value
        input_payload["fieldSelections"] = selections
    elif damage == "all_durations":
        operation = OBJECT_ADAPTER.validate_python(input_payload["operation"])
        operation_selections = FIELD_SELECTIONS_ADAPTER.validate_python(operation["selections"])
        selections = FIELD_SELECTIONS_ADAPTER.validate_python(input_payload["fieldSelections"])
        for item in operation_selections:
            if item["field"] in {"acceleration_duration_s", "steady_duration_s", "deceleration_duration_s"}:
                item["manual_value"] = value
        for item in selections:
            if item["field"] in {"acceleration_duration_s", "steady_duration_s", "deceleration_duration_s"}:
                item["value"] = value
        operation["selections"] = operation_selections
        input_payload["operation"] = operation
        input_payload["fieldSelections"] = selections
    else:
        operation = OBJECT_ADAPTER.validate_python(input_payload["operation"])
        operation_failure = OBJECT_ADAPTER.validate_python(operation["failureEvidence"])
        saved_failure = OBJECT_ADAPTER.validate_python(input_payload["failureEvidence"])
        if damage == "failure_duration":
            operation_failure["duration_to_failure_s"] = value
            saved_failure["durationToFailureS"] = value
        else:
            operation_failure["applicability"] = "unknown_start"
            saved_failure["applicability"] = "unknown_start"
            failure_result = OBJECT_ADAPTER.validate_python(result_payload["failure_result"])
            failure_result.update(status="not_applicable", cycles_to_failure=None, reason_code=value)
            result_payload["failure_result"] = failure_result
        operation["failureEvidence"] = operation_failure
        input_payload["operation"] = operation
        input_payload["failureEvidence"] = saved_failure
    _reseal_saved_rpt_snapshot(project_path, saved, input_payload, result_payload)
    with pytest.raises(ProjectOperationError) as detail_corrupted:
        service.get_rpt_calculation_detail(saved.detail.calculation_snapshot.calculation_snapshot_id, None)
    assert detail_corrupted.value.code == "corrupt_project"
    service.close()
    with pytest.raises(ProjectOperationError) as corrupted:
        service.open(path=str(project_path), application_instance_id="rpt-invalid-saved-input")
    assert corrupted.value.code == "corrupt_project"


@pytest.mark.parametrize("selection", ["original", "effective"])
def test_saved_rbd_plan_field_references_resolve_to_source_lexemes(
    tmp_path: Path,
    selection: Literal["original", "effective"],
) -> None:
    service, project_path, imported, execution = _project_with_rbd_execution(tmp_path)
    saved = service.create_rbd_calculation(
        analysis_input_snapshot_id=str(uuid4()),
        calculation_snapshot_id=str(uuid4()),
        execution_id=execution.execution_id,
        selection=selection,
        selections=tuple(
            RbdFieldSelection(field=field, origin="source")
            for field in (
                "base_cycles",
                "reserve_factor",
                "nominal_rpm",
                "acceleration_duration_s",
                "deceleration_duration_s",
            )
        ),
        failure=None,
        actor="local_user",
        reason="Проверка координат исходных полей",
        deadline=None,
    )
    fields = FIELD_SELECTIONS_ADAPTER.validate_python(saved.detail.input_snapshot.input_snapshot["fieldSelections"])
    assert saved.detail.input_snapshot.input_snapshot["schemaVersion"] == 2
    expected_member = "plan/original.json" if selection == "original" else "plan/effective.json"
    expected_prefix = "/source_values/" if selection == "original" else "/effective_plan/effective_plan/source_values/"
    with ZipFile(_managed_path(project_path, imported)) as archive:
        for field in fields:
            stored = OBJECT_ADAPTER.validate_python(field)
            reference = stored["sourceReference"]
            assert isinstance(reference, str)
            member, pointer = reference.split("#", 1)
            assert member == expected_member
            assert pointer == f"{expected_prefix}{stored['field']}"
            current: object = OBJECT_ADAPTER.validate_json(archive.read(member))
            for segment in pointer[1:].split("/"):
                current = OBJECT_ADAPTER.validate_python(current)[segment]
            assert current == stored["rawSourceValue"] == stored["value"]
    service.close()


@pytest.mark.parametrize("selection", ["original", "effective"])
def test_rbd_calculation_reopens_with_nullable_plan_references(
    tmp_path: Path,
    selection: Literal["original", "effective"],
) -> None:
    source = _package_with_nullable_plan_references(tmp_path)
    service, project_path, imported, execution = _project_with_rbd_execution(tmp_path, source)
    for plan_name in ("originalPlan", "effectivePlan"):
        plan = OBJECT_ADAPTER.validate_python(execution.planned_parameters_snapshot[plan_name])
        assert plan["laboratory_case_reference"] is None
        assert plan["customer_order_reference"] is None

    calculation = service.create_rbd_calculation(
        analysis_input_snapshot_id=str(uuid4()),
        calculation_snapshot_id=str(uuid4()),
        execution_id=execution.execution_id,
        selection=selection,
        selections=tuple(
            RbdFieldSelection(field=field, origin="source")
            for field in (
                "base_cycles",
                "reserve_factor",
                "nominal_rpm",
                "acceleration_duration_s",
                "deceleration_duration_s",
            )
        ),
        failure=None,
        actor="local_user",
        reason="Проверка сохранения расчёта при пустых ссылках плана",
        deadline=None,
    )
    assert calculation.disposition == "created"
    calculation_source = OBJECT_ADAPTER.validate_python(calculation.detail.input_snapshot.input_snapshot["source"])
    assert calculation_source["localImportId"] == imported.local_import_id
    service.close()

    service.open(path=str(project_path), application_instance_id="nullable-rbd-reopen")
    assert service.get_reliability_execution(execution.execution_id, None) == execution
    assert service.get_rbd_calculation_detail(calculation.detail.calculation_snapshot.calculation_snapshot_id, None) == calculation.detail
    service.close()


def test_manual_rbd_field_resolution_preserves_missing_raw_source_value(tmp_path: Path) -> None:
    service, project_path, imported, execution = _project_with_rbd_execution(tmp_path)
    source = service.read_rbd_plan_source(execution.execution_id, imported.local_import_id, "effective")
    missing_field_source = replace(source, source_values=replace(source.source_values, base_cycles=None))
    selections = tuple(
        RbdFieldSelection(
            field=field,
            origin="manual" if field == "base_cycles" else "source",
            manual_value="100" if field == "base_cycles" else None,
            basis="Дополнение инженера" if field == "base_cycles" else "",
        )
        for field in (
            "base_cycles",
            "reserve_factor",
            "nominal_rpm",
            "acceleration_duration_s",
            "deceleration_duration_s",
        )
    )
    # A valid .r130run v1 contains all five fields. Characterize the nullable
    # resolution seam without persisting a claim contradicted by the archive.
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        fields = _RbdFieldResolutionProbe(connection).field_snapshots(
            missing_field_source,
            selections,
            (execution.wheel_model_id, execution.local_specimen_id, execution.source_specimen_id),
        )
    assert fields[0]["origin"] == "manual"
    assert fields[0]["value"] == "100"
    assert fields[0]["rawSourceValue"] is None
    assert fields[0]["sourceReference"] == "plan/effective.json#/effective_plan/effective_plan/source_values/base_cycles"
    assert RbdSavedFieldSelectionModel.model_validate(fields[0]).model_dump(mode="json")["rawSourceValue"] is None
    service.close()


@pytest.mark.parametrize("legacy_history", [False, True])
def test_effective_rbd_reopen_distinguishes_legacy_history_from_new_coordinates(
    tmp_path: Path,
    legacy_history: bool,
) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path)
    saved = service.create_rbd_calculation(
        analysis_input_snapshot_id=str(uuid4()),
        calculation_snapshot_id=str(uuid4()),
        execution_id=execution.execution_id,
        selection="effective",
        selections=tuple(
            RbdFieldSelection(field=field, origin="source")
            for field in (
                "base_cycles",
                "reserve_factor",
                "nominal_rpm",
                "acceleration_duration_s",
                "deceleration_duration_s",
            )
        ),
        failure=None,
        actor="local_user",
        reason="Проверка версии происхождения",
        deadline=None,
    )
    service.close()
    input_payload = OBJECT_ADAPTER.validate_python(saved.detail.input_snapshot.input_snapshot)
    fields = FIELD_SELECTIONS_ADAPTER.validate_python(input_payload["fieldSelections"])
    input_payload["fieldSelections"] = [
        {
            **OBJECT_ADAPTER.validate_python(field),
            "sourceReference": f"plan/effective.json#/source_values/{OBJECT_ADAPTER.validate_python(field)['field']}",
        }
        for field in fields
    ]
    if legacy_history:
        input_payload["schemaVersion"] = 1
    _reseal_saved_rbd_input_snapshot(project_path, saved, input_payload)
    if legacy_history:
        service.open(path=str(project_path), application_instance_id="legacy-effective-history")
        detail = service.get_rbd_calculation_detail(saved.detail.calculation_snapshot.calculation_snapshot_id, None)
        assert detail.input_snapshot.input_snapshot == input_payload
        service.close()
    else:
        with pytest.raises(ProjectOperationError) as invalid:
            service.open(path=str(project_path), application_instance_id="invalid-effective-reference")
        assert invalid.value.code == "corrupt_project"


def test_rbd_accepts_active_global_document_and_rejects_archived_new_evidence(
    tmp_path: Path,
) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path)
    document = service.create_case_document(
        str(uuid4()),
        {
            "documentKind": "measurement_or_attestation_record",
            "title": "Общее основание расчёта",
            "designation": "РБД-01",
            "revisionLabel": "01",
            "documentDate": "2024-07-02",
            "issuer": "ЛИЦ ВВУ",
            "notes": "",
        },
        (),
        (),
        None,
    )
    selections = tuple(
        RbdFieldSelection(
            field=field,
            origin="manual" if field == "base_cycles" else "source",
            manual_value="100" if field == "base_cycles" else None,
            basis="По документу" if field == "base_cycles" else "",
            evidence=RbdEvidenceReference(
                document_id=document.case_document_id,
                document_record_revision=document.record_revision,
                document_locator="раздел 2",
            )
            if field == "base_cycles"
            else None,
        )
        for field in (
            "base_cycles",
            "reserve_factor",
            "nominal_rpm",
            "acceleration_duration_s",
            "deceleration_duration_s",
        )
    )
    input_id, result_id = str(uuid4()), str(uuid4())
    saved = service.create_rbd_calculation(
        analysis_input_snapshot_id=input_id,
        calculation_snapshot_id=result_id,
        execution_id=execution.execution_id,
        selection="original",
        selections=selections,
        failure=None,
        actor="local_user",
        reason="Общее основание",
        deadline=None,
    )
    updated_document = service.update_case_document(
        document.case_document_id,
        document.record_revision,
        {
            "documentKind": "measurement_or_attestation_record",
            "title": "Уточнённое общее основание расчёта",
            "designation": "РБД-01",
            "revisionLabel": "02",
            "documentDate": "2024-07-02",
            "issuer": "ЛИЦ ВВУ",
            "notes": "",
        },
        (),
        (),
        None,
    )
    assert updated_document.record_revision == 2
    with pytest.raises(ProjectOperationError) as stale_revision:
        service.create_rbd_calculation(
            analysis_input_snapshot_id=str(uuid4()),
            calculation_snapshot_id=str(uuid4()),
            execution_id=execution.execution_id,
            selection="original",
            selections=selections,
            failure=None,
            actor="local_user",
            reason="Устаревшая редакция основания",
            deadline=None,
        )
    assert stale_revision.value.code == "validation_error"
    archived = service.set_case_document_archived(document.case_document_id, updated_document.record_revision, True, None)
    audit_before = _audit_count(project_path)
    repeated = service.create_rbd_calculation(
        analysis_input_snapshot_id=input_id,
        calculation_snapshot_id=result_id,
        execution_id=execution.execution_id,
        selection="original",
        selections=selections,
        failure=None,
        actor="local_user",
        reason="Общее основание",
        deadline=None,
    )
    assert repeated.disposition == "existing"
    assert repeated.detail == saved.detail
    assert _audit_count(project_path) == audit_before
    archived_selections = tuple(
        replace(choice, evidence=replace(choice.evidence, document_record_revision=archived.record_revision)) if choice.evidence is not None else choice for choice in selections
    )
    with pytest.raises(ProjectOperationError) as rejected:
        service.create_rbd_calculation(
            analysis_input_snapshot_id=str(uuid4()),
            calculation_snapshot_id=str(uuid4()),
            execution_id=execution.execution_id,
            selection="original",
            selections=archived_selections,
            failure=None,
            actor="local_user",
            reason="Новое решение с архивным документом",
            deadline=None,
        )
    assert rejected.value.code == "entity_archived"
    assert _audit_count(project_path) == audit_before
    service.close()
    service.open(path=str(project_path), application_instance_id="archived-document-reopen")
    assert service.get_rbd_calculation_detail(result_id, None) == saved.detail
    service.close()


def test_imported_numeric_lexeme_is_available_for_documented_manual_replacement(
    tmp_path: Path,
) -> None:
    base_package = _package("normal_final_rbd.r130run")
    long_base_cycles = "0" * 62 + "100"
    with ZipFile(base_package) as archive:
        original = OBJECT_ADAPTER.validate_json(archive.read("plan/original.json"))
        effective = OBJECT_ADAPTER.validate_json(archive.read("plan/effective.json"))
    original_values = OBJECT_ADAPTER.validate_python(original["source_values"])
    original_values["base_cycles"] = long_base_cycles
    original["source_values"] = original_values
    effective_outer = OBJECT_ADAPTER.validate_python(effective["effective_plan"])
    effective_plan = OBJECT_ADAPTER.validate_python(effective_outer["effective_plan"])
    effective_values = OBJECT_ADAPTER.validate_python(effective_plan["source_values"])
    effective_values["base_cycles"] = long_base_cycles
    effective_plan["source_values"] = effective_values
    effective_outer["effective_plan"] = effective_plan
    original_bytes = (json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    effective_outer["original_plan_sha256"] = hashlib.sha256(original_bytes).hexdigest()
    effective["effective_plan"] = effective_outer
    effective_bytes = (json.dumps(effective, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    package_path = build_synthetic_r130run(
        tmp_path / "long-imported-numeric-text.r130run",
        payload_overrides={
            "plan/original.json": original_bytes,
            "plan/effective.json": effective_bytes,
        },
    )
    service, project_path, imported, execution = _project_with_rbd_execution(tmp_path, package_path)
    source = service.read_rbd_plan_source(execution.execution_id, imported.local_import_id, "original")
    response_values = RbdPlanSourceValuesResult(baseCycles=source.source_values.base_cycles)
    assert response_values.baseCycles == long_base_cycles
    saved = service.create_rbd_calculation(
        analysis_input_snapshot_id=str(uuid4()),
        calculation_snapshot_id=str(uuid4()),
        execution_id=execution.execution_id,
        selection="original",
        selections=tuple(
            RbdFieldSelection(
                field=field_name,
                origin="manual" if field_name == "base_cycles" else "source",
                manual_value="100" if field_name == "base_cycles" else None,
                basis="Исходная запись содержит неканонические ведущие нули" if field_name == "base_cycles" else "",
            )
            for field_name in (
                "base_cycles",
                "reserve_factor",
                "nominal_rpm",
                "acceleration_duration_s",
                "deceleration_duration_s",
            )
        ),
        failure=None,
        actor="local_user",
        reason="Документированное замещение исходной записи",
        deadline=None,
    )
    saved_selections = saved.detail.input_snapshot.input_snapshot["fieldSelections"]
    assert isinstance(saved_selections, list)
    first_selection = OBJECT_ADAPTER.validate_python(saved_selections[0])
    assert first_selection.get("rawSourceValue") == long_base_cycles
    service.close()
    service.open(path=str(project_path), application_instance_id="long-lexeme-reopen")
    assert service.get_rbd_calculation_detail(saved.detail.calculation_snapshot.calculation_snapshot_id, None) == saved.detail
    service.close()


def test_rbd_calculation_accepts_unordered_choices_and_normalized_manual_evidence(
    tmp_path: Path,
) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path)
    source_fields: tuple[RbdInputField, ...] = (
        "base_cycles",
        "reserve_factor",
        "nominal_rpm",
        "acceleration_duration_s",
        "deceleration_duration_s",
    )
    unordered_calculation = service.create_rbd_calculation(
        analysis_input_snapshot_id=str(uuid4()),
        calculation_snapshot_id=str(uuid4()),
        execution_id=execution.execution_id,
        selection="original",
        selections=tuple(RbdFieldSelection(field=field, origin="source") for field in reversed(source_fields)),
        failure=None,
        actor="local_user",
        reason="Явный выбор исходных значений",
        deadline=None,
    )
    assert unordered_calculation.disposition == "created"
    document = service.create_case_document(
        str(uuid4()),
        {
            "documentKind": "measurement_or_attestation_record",
            "title": "Основание аналитического дополнения",
            "designation": "РБД-ДОП-01",
            "revisionLabel": "01",
            "documentDate": "2024-07-02",
            "issuer": "ЛИЦ ВВУ",
            "notes": "",
        },
        (execution.wheel_model_id,),
        (execution.local_specimen_id,),
        None,
    )
    manual_calculation = service.create_rbd_calculation(
        analysis_input_snapshot_id=str(uuid4()),
        calculation_snapshot_id=str(uuid4()),
        execution_id=execution.execution_id,
        selection="original",
        selections=tuple(
            RbdFieldSelection(
                field=field,
                origin="manual" if field == "base_cycles" else "source",
                manual_value="100" if field == "base_cycles" else None,
                basis="  Подтверждено документом  " if field == "base_cycles" else "",
                evidence=RbdEvidenceReference(
                    document_id=document.case_document_id,
                    document_record_revision=document.record_revision,
                    document_locator="  раздел 2  ",
                )
                if field == "base_cycles"
                else None,
            )
            for field in source_fields
        ),
        failure=None,
        actor="local_user",
        reason="Документированное уточнение",
        deadline=None,
    )
    assert manual_calculation.disposition == "created"
    service.close()
    service.open(path=str(project_path), application_instance_id="normalized-evidence-reopen")
    assert service.get_rbd_calculation_detail(unordered_calculation.detail.calculation_snapshot.calculation_snapshot_id, None) == unordered_calculation.detail
    assert service.get_rbd_calculation_detail(manual_calculation.detail.calculation_snapshot.calculation_snapshot_id, None) == manual_calculation.detail
    service.close()


def test_rbd_plan_source_read_fails_typed_for_missing_modified_and_expired_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project_path, imported, execution = _project_with_rbd_execution(tmp_path)
    managed_path = _managed_path(project_path, imported)

    with pytest.raises(ProjectOperationError) as expired:
        service.read_rbd_plan_source(
            execution.execution_id,
            imported.local_import_id,
            "original",
            RequestDeadline.start(0),
        )
    assert expired.value.code == "timeout"

    validated_report, _ = _validated(_package("normal_final_rbd.r130run"))
    mismatched_report = validated_report.model_copy(update={"outerPackageSha256": "0" * 64})
    with monkeypatch.context() as patch_context:

        def mismatched_validation_report(
            self: RunPackageValidator,
            source_path: Path,
            control: ValidationControl,
        ) -> RunPackageValidationReport:
            return mismatched_report

        patch_context.setattr(
            RunPackageValidator,
            "validate",
            mismatched_validation_report,
        )
        with pytest.raises(ProjectOperationError) as receipt_mismatch:
            service.read_rbd_plan_source(execution.execution_id, imported.local_import_id, "original")
        assert receipt_mismatch.value.code == "file_integrity_mismatch"

    with monkeypatch.context() as patch_context:

        def malformed_zip(*args: object, **kwargs: object) -> ZipFile:
            raise zlib.error("malformed stream")

        patch_context.setattr(
            r130sh_sources_module,
            "ZipFile",
            malformed_zip,
        )
        with pytest.raises(ProjectOperationError) as malformed_member:
            service.read_rbd_plan_source(execution.execution_id, imported.local_import_id, "original")
        assert malformed_member.value.code == "file_integrity_mismatch"

    managed_path.unlink()
    with pytest.raises(ProjectOperationError) as missing:
        service.read_rbd_plan_source(execution.execution_id, imported.local_import_id, "original")
    assert missing.value.code == "file_integrity_mismatch"

    shutil.copyfile(_package("normal_final_rbd.r130run"), managed_path)
    assert (
        service.read_rbd_plan_source(
            execution.execution_id,
            imported.local_import_id,
            "original",
        ).execution_id
        == execution.execution_id
    )
    managed_path.write_bytes(b"modified")
    with pytest.raises(ProjectOperationError) as modified:
        service.read_rbd_plan_source(execution.execution_id, imported.local_import_id, "original")
    assert modified.value.code == "file_integrity_mismatch"
    service.close()


@pytest.mark.parametrize("damage", ["result_payload", "orphan_result", "oversized_input"])
def test_saved_rbd_result_survives_source_loss_and_reopen_rejects_tampered_snapshots(
    tmp_path: Path,
    damage: str,
) -> None:
    service, project_path, imported, execution = _project_with_rbd_execution(tmp_path)
    source_selections = tuple(
        RbdFieldSelection(field=field, origin="source")
        for field in (
            "base_cycles",
            "reserve_factor",
            "nominal_rpm",
            "acceleration_duration_s",
            "deceleration_duration_s",
        )
    )
    input_snapshot_id = str(uuid4())
    calculation_snapshot_id = str(uuid4())
    saved = service.create_rbd_calculation(
        analysis_input_snapshot_id=input_snapshot_id,
        calculation_snapshot_id=calculation_snapshot_id,
        execution_id=execution.execution_id,
        selection="original",
        selections=source_selections,
        failure=None,
        actor="local_user",
        reason="Расчёт из проверенной редакции источника",
        deadline=None,
    )
    assert saved.disposition == "created"
    service.close()
    _managed_path(project_path, imported).write_bytes(b"source modified after calculation")
    service.open(path=str(project_path), application_instance_id="source-changed")
    assert service.get_rbd_calculation_detail(calculation_snapshot_id, None) == saved.detail
    service.close()

    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        if damage == "result_payload":
            trigger = "rbd_calculation_snapshots_no_update"
            statement = "UPDATE rbd_calculation_snapshots SET result_snapshot_json='{}' WHERE calculation_snapshot_id=?"
            parameters: tuple[str, ...] = (calculation_snapshot_id,)
        elif damage == "orphan_result":
            trigger = "rbd_calculation_snapshots_no_delete"
            statement = "DELETE FROM rbd_calculation_snapshots WHERE calculation_snapshot_id=?"
            parameters = (calculation_snapshot_id,)
        else:
            trigger = "rbd_analysis_input_snapshots_no_update"
            connection.execute("PRAGMA ignore_check_constraints = ON")
            statement = "UPDATE rbd_analysis_input_snapshots SET input_snapshot_json=? WHERE analysis_input_snapshot_id=?"
            parameters = (json.dumps({"padding": "Ж" * 40_000}, ensure_ascii=False), input_snapshot_id)
        trigger_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
            (trigger,),
        ).fetchone()
        assert trigger_row is not None
        trigger_sql = str(trigger_row[0])
        connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute(statement, parameters)
        connection.execute(trigger_sql)
        connection.commit()
    with pytest.raises(ProjectOperationError) as corrupted:
        service.open(path=str(project_path), application_instance_id="tampered-calculation")
    assert corrupted.value.code == "corrupt_project"


@pytest.mark.parametrize(
    ("field", "malformed_value"),
    [
        ("maximum_rpm", {"numerator": "1500", "denominator": "0", "decimal": "1500", "decimal_preview": "1500"}),
        ("maximum_rpm", {"numerator": "1500", "denominator": "1", "decimal_preview": "1500"}),
        ("phases", ["bad", None, 3]),
        ("failure_result", {"status": "not_applicable", "cycles_to_failure": [], "reason_code": None}),
        ("failure_result", {"status": "not_applicable", "cycles_to_failure": None}),
        ("failure_result", {"status": "calculated", "cycles_to_failure": "1", "reason_code": None}),
        ("diagram_points", [None, None, None, None]),
        ("formula_references", [None, None, None]),
    ],
)
def test_reopen_rejects_resealed_rbd_result_with_malformed_nested_structure(
    tmp_path: Path,
    field: str,
    malformed_value: object,
) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path)
    source_selections = tuple(
        RbdFieldSelection(field=field_name, origin="source")
        for field_name in (
            "base_cycles",
            "reserve_factor",
            "nominal_rpm",
            "acceleration_duration_s",
            "deceleration_duration_s",
        )
    )
    input_snapshot_id = str(uuid4())
    calculation_snapshot_id = str(uuid4())
    saved = service.create_rbd_calculation(
        analysis_input_snapshot_id=input_snapshot_id,
        calculation_snapshot_id=calculation_snapshot_id,
        execution_id=execution.execution_id,
        selection="original",
        selections=source_selections,
        failure=None,
        actor="local_user",
        reason="Проверка структуры сохранённого результата",
        deadline=None,
    )
    service.close()

    result = dict(saved.detail.calculation_snapshot.result_snapshot)
    result[field] = malformed_value
    failure_status = "not_applicable"
    if field == "failure_result" and isinstance(malformed_value, dict):
        failure = OBJECT_ADAPTER.validate_python(malformed_value)
        if failure.get("status") == "calculated":
            failure_status = "calculated"
    calculation_content = {
        "calculationSnapshotId": calculation_snapshot_id,
        "analysisInputSnapshotId": input_snapshot_id,
        "inputContentSha256": saved.detail.input_snapshot.content_sha256,
        "result": result,
        "createdAtUtc": saved.detail.calculation_snapshot.created_at_utc,
    }

    def canonical_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))

    content_sha256 = hashlib.sha256(canonical_json(calculation_content).encode("utf-8")).hexdigest()
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        for trigger_name in ("rbd_calculation_snapshots_no_update", "project_audit_events_no_update"):
            trigger_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                (trigger_name,),
            ).fetchone()
            assert trigger_row is not None
            connection.execute(f"DROP TRIGGER {trigger_name}")
            if trigger_name == "rbd_calculation_snapshots_no_update":
                connection.execute(
                    "UPDATE rbd_calculation_snapshots SET result_snapshot_json=?, failure_status=?, content_sha256=? WHERE calculation_snapshot_id=?",
                    (canonical_json(result), failure_status, content_sha256, calculation_snapshot_id),
                )
            else:
                audit_row = connection.execute(
                    "SELECT payload_json FROM project_audit_events WHERE event_type='rbd_calculation.created' AND json_extract(payload_json, '$.calculationSnapshotId')=?",
                    (calculation_snapshot_id,),
                ).fetchone()
                assert audit_row is not None
                audit_payload = OBJECT_ADAPTER.validate_json(str(audit_row[0]))
                audit_payload["calculationContentSha256"] = content_sha256
                connection.execute(
                    "UPDATE project_audit_events SET payload_json=? WHERE event_type='rbd_calculation.created' AND json_extract(payload_json, '$.calculationSnapshotId')=?",
                    (canonical_json(audit_payload), calculation_snapshot_id),
                )
            connection.execute(str(trigger_row[0]))
        connection.commit()
    with pytest.raises(ProjectOperationError) as corrupted:
        service.open(path=str(project_path), application_instance_id="malformed-result")
    assert corrupted.value.code == "corrupt_project"


def _reseal_saved_rbd_input_snapshot(
    project_path: Path,
    saved: RbdCalculationWriteResult,
    input_payload: dict[str, object],
) -> None:
    input_snapshot = saved.detail.input_snapshot
    calculation = saved.detail.calculation_snapshot

    def canonical_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))

    operation = OBJECT_ADAPTER.validate_python(input_payload["operation"])
    operation_hash = hashlib.sha256(canonical_json(operation).encode("utf-8")).hexdigest()
    input_content = {
        "analysisInputSnapshotId": input_snapshot.analysis_input_snapshot_id,
        "executionId": input_snapshot.execution_id,
        "input": input_payload,
        "actor": input_snapshot.actor,
        "reason": input_snapshot.decision_reason,
        "createdAtUtc": input_snapshot.created_at_utc,
    }
    input_hash = hashlib.sha256(canonical_json(input_content).encode("utf-8")).hexdigest()
    calculation_content = {
        "calculationSnapshotId": calculation.calculation_snapshot_id,
        "analysisInputSnapshotId": input_snapshot.analysis_input_snapshot_id,
        "inputContentSha256": input_hash,
        "result": calculation.result_snapshot,
        "createdAtUtc": calculation.created_at_utc,
    }
    calculation_hash = hashlib.sha256(canonical_json(calculation_content).encode("utf-8")).hexdigest()
    with closing(sqlite3.connect(project_path / "project.sqlite")) as connection:
        triggers: dict[str, str] = {}
        for trigger_name in (
            "rbd_analysis_input_snapshots_no_update",
            "rbd_calculation_snapshots_no_update",
            "project_audit_events_no_update",
        ):
            trigger_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                (trigger_name,),
            ).fetchone()
            assert trigger_row is not None
            triggers[trigger_name] = str(trigger_row[0])
            connection.execute(f"DROP TRIGGER {trigger_name}")
        connection.execute(
            "UPDATE rbd_analysis_input_snapshots SET input_snapshot_json=?, operation_sha256=?, content_sha256=? WHERE analysis_input_snapshot_id=?",
            (canonical_json(input_payload), operation_hash, input_hash, input_snapshot.analysis_input_snapshot_id),
        )
        connection.execute(
            "UPDATE rbd_calculation_snapshots SET input_content_sha256=?, operation_sha256=?, content_sha256=? WHERE calculation_snapshot_id=?",
            (input_hash, operation_hash, calculation_hash, calculation.calculation_snapshot_id),
        )
        audit_row = connection.execute(
            "SELECT payload_json FROM project_audit_events WHERE event_type='rbd_calculation.created' AND json_extract(payload_json, '$.calculationSnapshotId')=?",
            (calculation.calculation_snapshot_id,),
        ).fetchone()
        assert audit_row is not None
        audit_payload = OBJECT_ADAPTER.validate_json(str(audit_row[0]))
        audit_payload["inputContentSha256"] = input_hash
        audit_payload["calculationContentSha256"] = calculation_hash
        audit_payload["operationSha256"] = operation_hash
        connection.execute(
            "UPDATE project_audit_events SET payload_json=? WHERE event_type='rbd_calculation.created' AND json_extract(payload_json, '$.calculationSnapshotId')=?",
            (canonical_json(audit_payload), calculation.calculation_snapshot_id),
        )
        for trigger_sql in triggers.values():
            connection.execute(trigger_sql)
        connection.commit()


@pytest.mark.parametrize(
    "damage",
    [
        "malformed_selection",
        "contradictory_source_value",
        "nominal_source_mismatch",
        "operation_plan_mismatch",
        "source_producer_mismatch",
        "source_requirement_mismatch",
        "source_target_mismatch",
        "missing_failure_observation",
    ],
)
def test_reopen_rejects_resealed_rbd_input_with_invalid_provenance(
    tmp_path: Path,
    damage: str,
) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path)
    input_snapshot_id = str(uuid4())
    calculation_snapshot_id = str(uuid4())
    saved = service.create_rbd_calculation(
        analysis_input_snapshot_id=input_snapshot_id,
        calculation_snapshot_id=calculation_snapshot_id,
        execution_id=execution.execution_id,
        selection="original",
        selections=tuple(
            RbdFieldSelection(field=field_name, origin="source")
            for field_name in (
                "base_cycles",
                "reserve_factor",
                "nominal_rpm",
                "acceleration_duration_s",
                "deceleration_duration_s",
            )
        ),
        failure=None,
        actor="local_user",
        reason="Проверка происхождения сохранённых входов",
        deadline=None,
    )
    service.close()

    input_payload = dict(saved.detail.input_snapshot.input_snapshot)
    selections = input_payload["fieldSelections"]
    assert isinstance(selections, list)
    first_selection = OBJECT_ADAPTER.validate_python(selections[0])
    if damage == "malformed_selection":
        first_selection.update(origin="vendor", value={"unexpected": True}, evidence=[])
    elif damage == "contradictory_source_value":
        first_selection["value"] = "999"
    input_payload["fieldSelections"] = [first_selection, *selections[1:]]
    if damage == "nominal_source_mismatch":
        nominal_selection = OBJECT_ADAPTER.validate_python(selections[2])
        nominal_selection["value"] = "999"
        nominal_selection["rawSourceValue"] = "999"
        input_payload["fieldSelections"] = [first_selection, selections[1], nominal_selection, *selections[3:]]

    if damage in {"operation_plan_mismatch", "missing_failure_observation"}:
        operation = OBJECT_ADAPTER.validate_python(input_payload["operation"])
        if damage == "operation_plan_mismatch":
            operation["planSelection"] = "effective"
        else:
            missing_observation_id = str(uuid4())
            operation["failureEvidence"] = {
                "applicability": "unavailable",
                "duration_to_failure_s": None,
                "basis": "Не подтверждено точное время",
                "failure_observation_ids": [missing_observation_id],
                "evidence": None,
            }
            input_payload["failureEvidence"] = {
                "applicability": "unavailable",
                "durationToFailureS": None,
                "basis": "Не подтверждено точное время",
                "failureObservations": [
                    {
                        "failureObservationId": missing_observation_id,
                        "failureType": "technical_interruption",
                        "subjectKind": "unknown",
                        "sourceEventReference": "event-id",
                        "sourceFieldReference": "run-summary.json#/termination_reason",
                        "durationS": None,
                        "rpm": None,
                        "observedAtUtc": None,
                        "sourceOuterPackageSha256": saved.detail.input_snapshot.source_outer_package_sha256,
                    }
                ],
                "evidence": None,
            }
        input_payload["operation"] = operation
    if damage == "source_producer_mismatch":
        source = OBJECT_ADAPTER.validate_python(input_payload["source"])
        producer = OBJECT_ADAPTER.validate_python(source["producer"])
        producer["gitCommit"] = "fabricated-producer-commit"
        source["producer"] = producer
        input_payload["source"] = source
    if damage == "source_requirement_mismatch":
        source = OBJECT_ADAPTER.validate_python(input_payload["source"])
        requirements = OBJECT_ADAPTER.validate_python(source["methodicalRequirements"])
        requirements["required_cycles_exact"] = "999"
        source["methodicalRequirements"] = requirements
        input_payload["source"] = source
    if damage == "source_target_mismatch":
        source = OBJECT_ADAPTER.validate_python(input_payload["source"])
        targets = OBJECT_ADAPTER.validate_python(source["executionTargets"])
        targets["rounding_policy"] = "not-the-selected-plan"
        source["executionTargets"] = targets
        input_payload["source"] = source
    _reseal_saved_rbd_input_snapshot(project_path, saved, input_payload)
    with pytest.raises(ProjectOperationError) as corrupted:
        service.open(path=str(project_path), application_instance_id="invalid-input-provenance")
    assert corrupted.value.code == "corrupt_project"


def test_reopen_rejects_resealed_observation_evidence_with_changed_classification(
    tmp_path: Path,
) -> None:
    service, project_path, _, execution = _project_with_rbd_execution(tmp_path)
    document = service.create_case_document(
        str(uuid4()),
        {
            "documentKind": "typical_test_method",
            "title": "ПМИ Р130У",
            "designation": "ПМИ Р130У",
            "revisionLabel": "01",
            "documentDate": "2024-07-02",
            "issuer": "ЛИЦ ВВУ",
            "notes": "",
        },
        (execution.wheel_model_id,),
        (execution.local_specimen_id,),
        None,
    )
    observation_version_id = str(uuid4())
    service.create_reliability_observation_version(
        observation_id=str(uuid4()),
        observation_version_id=observation_version_id,
        execution_id=execution.execution_id,
        expected_previous_version_id=None,
        classification="right_censored",
        endpoint_kind="right_bound",
        metric_kind="rbd_steady_rotation_time",
        metric_unit="hours",
        metric_origin="analyst_provided",
        lower_value="12.5",
        upper_value=None,
        origin_basis="Начало зачтённого вращения",
        endpoint_basis="Граница по записи инженера",
        document_id=document.case_document_id,
        document_locator="раздел 10",
        failure_ids=(),
        actor="local_user",
        reason="Документированная граница наблюдения",
        deadline=None,
    )
    saved = service.create_rbd_calculation(
        analysis_input_snapshot_id=str(uuid4()),
        calculation_snapshot_id=str(uuid4()),
        execution_id=execution.execution_id,
        selection="original",
        selections=tuple(
            RbdFieldSelection(field=field_name, origin="source")
            for field_name in (
                "base_cycles",
                "reserve_factor",
                "nominal_rpm",
                "acceleration_duration_s",
                "deceleration_duration_s",
            )
        ),
        failure=RbdFailureEvidence(
            applicability="unavailable",
            duration_to_failure_s=None,
            basis="Время отказа не документировано",
            evidence=RbdEvidenceReference(observation_version_id=observation_version_id),
        ),
        actor="local_user",
        reason="Свидетельство наблюдения не задаёт T_OTK",
        deadline=None,
    )
    service.close()
    input_payload = dict(saved.detail.input_snapshot.input_snapshot)
    failure = OBJECT_ADAPTER.validate_python(input_payload["failureEvidence"])
    evidence = OBJECT_ADAPTER.validate_python(failure["evidence"])
    observation = OBJECT_ADAPTER.validate_python(evidence["observation"])
    observation["classification"] = "invalid"
    evidence["observation"] = observation
    failure["evidence"] = evidence
    input_payload["failureEvidence"] = failure
    _reseal_saved_rbd_input_snapshot(project_path, saved, input_payload)
    with pytest.raises(ProjectOperationError) as corrupted:
        service.open(path=str(project_path), application_instance_id="changed-observation-evidence")
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
        (),
        (),
        None,
    )
    observation_id = str(uuid4())
    version_id = str(uuid4())
    observation_origin_basis = "Начало зачтённого вращения\r\nПо разделу 10 ПМИ"
    observation_endpoint_basis = "Граница наблюдения\rПо записи инженера"
    observation_reason = "Отказ не установлен\nдо документированной границы"
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
        origin_basis=observation_origin_basis,
        endpoint_basis=observation_endpoint_basis,
        document_id=document.case_document_id,
        document_locator="Раздел 10; журнал испытания, строка 42",
        failure_ids=(),
        actor="local_user",
        reason=observation_reason,
        deadline=None,
    )
    assert first.disposition == "created"
    assert first.version.classification == "right_censored"
    assert first.version.lower_value == "12.5"
    assert first.version.origin_basis == "Начало зачтённого вращения\nПо разделу 10 ПМИ"
    assert first.version.endpoint_basis == "Граница наблюдения\nПо записи инженера"
    assert first.version.decision_reason == "Отказ не установлен\nдо документированной границы"
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
        origin_basis=observation_origin_basis,
        endpoint_basis=observation_endpoint_basis,
        document_id=document.case_document_id,
        document_locator="Раздел 10; журнал испытания, строка 42",
        failure_ids=(),
        actor="local_user",
        reason=observation_reason,
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
            origin_basis=observation_origin_basis,
            endpoint_basis=observation_endpoint_basis,
            document_id=document.case_document_id,
            document_locator="Раздел 10; журнал испытания, строка 42",
            failure_ids=(),
            actor="local_user",
            reason=observation_reason,
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
        population_basis="Рабочие колёса модели РБД-01\r\nИз заказа ЛИЦ ВВУ",
        methodology_basis="ПМИ Р130У, редакция 01\rРазделы 9.3.1 и 10",
        comparability_basis="Одинаковый метод РБД\nУсловия отобраны инженером",
        decisions=(
            {
                "observationVersionId": version_id,
                "decision": "included",
                "reason": "Документированная правая граница наблюдения",
            },
        ),
        actor="local_user",
        reason="Первая зафиксированная выборка\r\nПосле проверки документов",
        deadline=None,
    )
    assert dataset.disposition == "created"
    assert dataset.version.members[0].policy_eligibility == "eligible"
    assert dataset.version.members[0].decision == "included"
    assert dataset.version.population_basis == "Рабочие колёса модели РБД-01\nИз заказа ЛИЦ ВВУ"
    assert dataset.version.methodology_basis == "ПМИ Р130У, редакция 01\nРазделы 9.3.1 и 10"
    assert dataset.version.comparability_basis == "Одинаковый метод РБД\nУсловия отобраны инженером"
    assert dataset.version.decision_reason == "Первая зафиксированная выборка\nПосле проверки документов"

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

    noncanonical_text_path = tmp_path / "m04b-noncanonical-multiline.irproj"
    shutil.copytree(project_path, noncanonical_text_path)
    noncanonical_basis = "Рабочие колёса модели РБД-01\rНенормализованная строка"
    noncanonical_payload = dict(tampered_payload)
    noncanonical_payload["metricKind"] = tampered_version.metric_kind
    noncanonical_payload["metricUnit"] = tampered_version.metric_unit
    noncanonical_payload["populationBasis"] = noncanonical_basis
    noncanonical_hash = hashlib.sha256(
        json.dumps(
            noncanonical_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with closing(sqlite3.connect(noncanonical_text_path / "project.sqlite")) as connection:
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
        connection.execute(
            "UPDATE reliability_dataset_versions SET population_basis=?, content_sha256=? WHERE dataset_version_id=?",
            (noncanonical_basis, noncanonical_hash, tampered_version.dataset_version_id),
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
        audit_payload["contentSha256"] = noncanonical_hash
        connection.execute(
            "UPDATE project_audit_events SET payload_json=? WHERE sequence=?",
            (
                json.dumps(audit_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                int(audit_row[0]),
            ),
        )
        connection.execute(dataset_trigger_sql)
        connection.execute(audit_trigger_sql)
        connection.commit()
    with pytest.raises(ProjectOperationError) as noncanonical_text:
        ProjectService().open(
            path=str(noncanonical_text_path),
            application_instance_id="m04b-noncanonical-multiline",
        )
    assert noncanonical_text.value.code == "corrupt_project"

    noncanonical_document_path = tmp_path / "m04b-noncanonical-document-snapshot.irproj"
    shutil.copytree(project_path, noncanonical_document_path)
    with closing(sqlite3.connect(noncanonical_document_path / "project.sqlite")) as connection:
        trigger_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='reliability_observation_versions_no_update'",
            ).fetchone()[0]
        )
        snapshot_row = connection.execute(
            "SELECT document_snapshot_json FROM reliability_observation_versions WHERE observation_version_id=?",
            (version_id,),
        ).fetchone()
        assert snapshot_row is not None
        document_snapshot = OBJECT_ADAPTER.validate_json(str(snapshot_row[0]))
        document_snapshot["title"] = f"{document_snapshot['title']}\r"
        connection.execute("DROP TRIGGER reliability_observation_versions_no_update")
        connection.execute(
            "UPDATE reliability_observation_versions SET document_snapshot_json=? WHERE observation_version_id=?",
            (
                json.dumps(document_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                version_id,
            ),
        )
        connection.execute(trigger_sql)
        connection.commit()
    with pytest.raises(ProjectOperationError) as noncanonical_document:
        ProjectService().open(
            path=str(noncanonical_document_path),
            application_instance_id="m04b-noncanonical-document-snapshot",
        )
    assert noncanonical_document.value.code == "corrupt_project"

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


def _package_with_nullable_plan_references(tmp_path: Path) -> Path:
    source = _package("normal_final_rbd.r130run")
    with ZipFile(source) as archive:
        original = OBJECT_ADAPTER.validate_json(archive.read("plan/original.json"))
        effective = OBJECT_ADAPTER.validate_json(archive.read("plan/effective.json"))
        summary = OBJECT_ADAPTER.validate_json(archive.read("run-summary.json"))
    for key in ("laboratory_case_reference", "customer_order_reference"):
        original[key] = None
    effective_container = OBJECT_ADAPTER.validate_python(effective["effective_plan"])
    effective_plan = OBJECT_ADAPTER.validate_python(effective_container["effective_plan"])
    for key in ("laboratory_case_reference", "customer_order_reference"):
        effective_plan[key] = None
    effective_container["effective_plan"] = effective_plan
    effective_container["original_plan_sha256"] = hashlib.sha256(
        _canonical_package_json(original),
    ).hexdigest()
    effective["effective_plan"] = effective_container
    run_card = OBJECT_ADAPTER.validate_python(summary["run_card"])
    for key in ("laboratory_case_reference", "customer_order_reference"):
        run_card[key] = None
    summary["run_card"] = run_card

    return build_synthetic_r130run(
        tmp_path / "nullable-plan-references.r130run",
        payload_overrides={
            "plan/original.json": _canonical_package_json(original),
            "plan/effective.json": _canonical_package_json(effective),
            "run-summary.json": _canonical_package_json(summary),
        },
    )


def _package_with_explicit_rpt_lower_point(tmp_path: Path) -> Path:
    return _package_with_rpt_plan_values(
        tmp_path,
        suffix="explicit-lower-point",
        source_updates={"lower_point_policy": "explicit_rpm", "explicit_lower_rpm": "125.5"},
        requirement_updates={},
        target_updates={"lower_point_policy": "explicit_rpm", "lower_rpm": "125.5"},
    )


def _package_with_rpt_plan_values(
    tmp_path: Path,
    *,
    suffix: str,
    source_updates: dict[str, object],
    requirement_updates: dict[str, object],
    target_updates: dict[str, object],
) -> Path:
    return _package_with_plan_values(
        tmp_path,
        base_name="normal_final_rpt_full_stop.r130run",
        output_name=f"rpt-{suffix}.r130run",
        source_updates=source_updates,
        requirement_updates=requirement_updates,
        target_updates=target_updates,
    )


def _package_with_plan_values(
    tmp_path: Path,
    *,
    base_name: str,
    output_name: str,
    source_updates: dict[str, object],
    requirement_updates: dict[str, object],
    target_updates: dict[str, object],
    source_removals: tuple[str, ...] = (),
) -> Path:
    base = _package(base_name)
    with ZipFile(base) as archive:
        original = OBJECT_ADAPTER.validate_json(archive.read("plan/original.json"))
        effective = OBJECT_ADAPTER.validate_json(archive.read("plan/effective.json"))
    for key, updates in (
        ("source_values", source_updates),
        ("methodical_requirements", requirement_updates),
        ("execution_targets", target_updates),
    ):
        values = OBJECT_ADAPTER.validate_python(original[key])
        values.update(updates)
        if key == "source_values":
            for field in source_removals:
                values.pop(field, None)
        original[key] = values

    effective_container = OBJECT_ADAPTER.validate_python(effective["effective_plan"])
    effective_plan = OBJECT_ADAPTER.validate_python(effective_container["effective_plan"])
    for key, updates in (
        ("source_values", source_updates),
        ("methodical_requirements", requirement_updates),
        ("execution_targets", target_updates),
    ):
        values = OBJECT_ADAPTER.validate_python(effective_plan[key])
        values.update(updates)
        if key == "source_values":
            for field in source_removals:
                values.pop(field, None)
        effective_plan[key] = values
    effective_container["effective_plan"] = effective_plan
    effective_container["original_plan_sha256"] = hashlib.sha256(_canonical_package_json(original)).hexdigest()
    effective["effective_plan"] = effective_container
    return build_synthetic_r130run(
        tmp_path / output_name,
        base_package=base,
        payload_overrides={
            "plan/original.json": _canonical_package_json(original),
            "plan/effective.json": _canonical_package_json(effective),
        },
    )


def _canonical_package_json(value: dict[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


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
