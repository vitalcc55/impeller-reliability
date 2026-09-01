# Domain Model

`Project` — аналитическое дело Impeller Reliability (`ReliabilityCase`). Оно агрегирует редактируемый `analyst_enrichment` (`CustomerProfile`, `WheelModel`, `Specimen`, `AnalystSourceDocument`), неизменяемый `r130sh_source`, M04A `derived_analysis` и audit. `TestCampaign` в будущем будет только downstream-группировкой импортированных запусков. Impeller Reliability не имеет собственного исполняемого `TestPlan`.

`ImportedTestRun` — принятая immutable export revision R130SH; её `r130sh_source` владеет exact managed archive, inventory и узкой projection. `ImportedRunPlanSnapshot` — неизменяемый original/effective plan внутри source projection; его владельцем остаётся R130SH. M04A `TestExecution` и `FailureObservation` — immutable derived snapshots с source-import and field provenance; `ReliabilityDataset` хранит явное inclusion/censoring решение без calculation. `AnalysisInputSnapshot` и `CalculationSnapshot` отсутствуют до следующего расчётного этапа.

M03A contract validation не создаёт новую domain entity: её transient job/report не являются `ImportedTestRun`, `ImportedRunPlanSnapshot`, project entity, audit event, import receipt, analysis input или признаком готовности к расчёту. Точная runtime-модель принадлежит Integration/IPC и карте состояния.

Первый публикуемый `Project` представлен контейнером `.irproj` и clean pre-release schema v1: dossier tables дополнены `r130sh_sources`, `r130sh_source_inventory`, `r130sh_run_projections`, `r130sh_specimen_bindings`, `r130sh_enrichment_resolutions` и append-only audit. Source registry/inventory/projection неизменяемы; binding optimistic, resolution append-only, exact import retry не создаёт revision/event.

`AnalystSourceDocument` («Документ дела») — редактируемая регистрационная запись analyst enrichment. Она имеет вид, название, обозначение, редакцию, дату, issuer, notes и применимость к нескольким WheelModel/Specimen; отсутствие links означает всё дело. У записи не более одной неизменяемой управляемой копии. Файл не является imported R130SH source: новая фактическая редакция создаёт новый документ, hard delete и supersedes-chain отсутствуют.

`ManagedDocumentFile` хранит original name, media type, size, SHA-256, project-relative POSIX path и время attach. Содержимое находится только внутри локального `.irproj`; абсолютный исходный путь не является domain data. Integrity status вычисляется при чтении файла и не изменяет зарегистрированный hash: `not_attached`, `verified`, `missing`, `modified`, `verification_error`.

App-level `health.sqlite` продолжает содержать только инфраструктурную `schema_info` и не является частью Project.
