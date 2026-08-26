# Domain Model

`Project` — аналитическое дело Impeller Reliability. Оно агрегирует редактируемый `analyst_enrichment` (`CustomerProfile`, `WheelModel`, `Specimen`), будущий неизменяемый `r130sh_source`, производный `derived_analysis` и audit. `TestCampaign` в будущем будет только downstream-группировкой импортированных запусков. Impeller Reliability не имеет собственного исполняемого `TestPlan`.

`ImportedRunPlanSnapshot` — неизменяемый original/effective plan из будущего `.r130run`; его владельцем остаётся R130SH. `AnalysisInputSnapshot` фиксирует выбранные source/enrichment values конкретного анализа. `CalculationSnapshot` фиксирует входной hash, алгоритм, evidence, warnings и результат вычисления.

В M02.1 `Project` представлен контейнером `.irproj` и строкой `project_metadata`. M02.2A расширяет `analyst_enrichment` одной карточкой `customer_profile`, каталогом `wheel_models` и каталогом физических `specimens`. Все изменяемые записи используют optimistic revision; реальное изменение и append-only audit атомарны, no-op не создаёт revision или event.

App-level `health.sqlite` продолжает содержать только инфраструктурную `schema_info` и не является частью Project.
