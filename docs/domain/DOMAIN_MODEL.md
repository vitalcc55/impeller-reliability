# Domain Model

`Project` агрегирует `Customer`, `WheelModel`, `Specimen`, `TestCampaign`, исследования и audit. Кампания содержит `TestPlan`/revisions, `ImportedTestRun`/revisions, `AnalysisSnapshot` и `ReportRelease`. Все основные сущности имеют UUID/ULID. Application, worker, protocol, database, algorithms, reference data и report templates версионируются независимо.

В M02.1 `Project` представлен контейнером `.irproj` и единственной строкой `project_metadata`: UUID, название, номер, описание, статус, `record_revision`, даты и версия создавшего приложения. Изменение metadata и `project.metadata_updated` audit event атомарны. `project_audit_events` append-only; Customer, WheelModel, Specimen, TestCampaign и SourceDocument появляются только в M02.2.

App-level `health.sqlite` продолжает содержать только инфраструктурную `schema_info` и не является частью Project.
