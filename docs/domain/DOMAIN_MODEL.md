# Domain Model

`Project` агрегирует `Customer`, `WheelModel`, `Specimen`, `TestCampaign`, исследования и audit. Кампания содержит `TestPlan`/revisions, `ImportedTestRun`/revisions, `AnalysisSnapshot` и `ReportRelease`. Все основные сущности имеют UUID/ULID. Application, worker, protocol, database, algorithms, reference data и report templates версионируются независимо.

В M01 доменные таблицы отсутствуют: SQLite содержит только инфраструктурную `schema_info`.
