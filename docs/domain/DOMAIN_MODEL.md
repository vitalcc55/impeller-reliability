# Domain Model

`Project` агрегирует `Customer`, `WheelModel`, `Specimen`, `TestCampaign`, исследования и audit. Кампания содержит `TestPlan`/revisions, `ImportedTestRun`/revisions, `AnalysisSnapshot` и `ReportRelease`. Все основные сущности имеют UUID/ULID. Application, worker, protocol, database, algorithms, reference data и report templates версионируются независимо.

В M02.1 `Project` представлен контейнером `.irproj` и единственной строкой `project_metadata`: UUID, название, номер, описание, статус, `record_revision`, даты и версия создавшего приложения. Начальные нормализованные metadata и `project.created` создаются атомарно. Реальное изменение metadata и `project.metadata_updated` также атомарны; audit перечисляет только изменённые поля и их `before`/`after`, а no-op не создаёт новую ревизию. `project_audit_events` append-only и не заменяет текущее состояние `project_metadata`; Customer, WheelModel, Specimen, TestCampaign и SourceDocument появляются только в M02.2.

App-level `health.sqlite` продолжает содержать только инфраструктурную `schema_info` и не является частью Project.
