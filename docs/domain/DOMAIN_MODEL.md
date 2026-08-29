# Domain Model

`Project` — аналитическое дело Impeller Reliability. Оно агрегирует редактируемый `analyst_enrichment` (`CustomerProfile`, `WheelModel`, `Specimen`, `AnalystSourceDocument`), будущий неизменяемый `r130sh_source`, производный `derived_analysis` и audit. `TestCampaign` в будущем будет только downstream-группировкой импортированных запусков. Impeller Reliability не имеет собственного исполняемого `TestPlan`.

`ImportedRunPlanSnapshot` — неизменяемый original/effective plan из будущего `.r130run`; его владельцем остаётся R130SH. `AnalysisInputSnapshot` фиксирует выбранные source/enrichment values конкретного анализа. `CalculationSnapshot` фиксирует входной hash, алгоритм, evidence, warnings и результат вычисления.

Первый публикуемый `Project` представлен контейнером `.irproj` и schema v1: `project_metadata`, `customer_profile`, `wheel_models`, `specimens`, `case_documents`, document applicability links, immutable file registry и append-only audit. Все изменяемые записи используют optimistic revision; реальное изменение и audit атомарны, no-op не создаёт revision или event.

`AnalystSourceDocument` («Документ дела») — редактируемая регистрационная запись analyst enrichment. Она имеет вид, название, обозначение, редакцию, дату, issuer, notes и применимость к нескольким WheelModel/Specimen; отсутствие links означает всё дело. У записи не более одной неизменяемой управляемой копии. Файл не является imported R130SH source: новая фактическая редакция создаёт новый документ, hard delete и supersedes-chain отсутствуют.

`ManagedDocumentFile` хранит original name, media type, size, SHA-256, project-relative POSIX path и время attach. Содержимое находится только внутри локального `.irproj`; абсолютный исходный путь не является domain data. Integrity status вычисляется при чтении файла и не изменяет зарегистрированный hash: `not_attached`, `verified`, `missing`, `modified`, `verification_error`.

App-level `health.sqlite` продолжает содержать только инфраструктурную `schema_info` и не является частью Project.
