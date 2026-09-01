# M04A — Reliability Analysis Domain Foundation

## Outcome contract

M04A переводит принятый R130SH запуск из состояния «неизменяемый импорт» в
явную предметную основу будущего анализа. `Project` остаётся единственным
`ReliabilityCase`; существующий analyst-owned `Specimen` остаётся единственным
локальным объектом рабочего колеса. Python materializes неизменяемый
`TestExecution` для связанного с ним импортированного запуска, его
`FailureObservation` и пустой до явного отбора `ReliabilityDataset`.

Этап не рассчитывает Weibull, Markov, Monte-Carlo, FMEA/FMECA, РБД/РПТ/ПМН,
не строит графики и не формирует отчёты. Он не создаёт executable TestPlan,
не меняет R130SH, не читает его SQLite и не возвращает общий признак
готовности к расчёту.

## Граница источника и аналитической модели

| Слой | Владелец | Режим | Содержимое |
| --- | --- | --- | --- |
| `r130sh_source` | R130SH producer, принятый Python importer | immutable | archive, inventory, package/run identity, original/effective plan, producer outcomes, measurements/events/inspections evidence |
| `analyst_enrichment` | инженер в Impeller Reliability | mutable и audited | CustomerProfile, WheelModel, Specimen, документы и явная specimen binding |
| `derived_analysis` M04A | Python worker | immutable materialized snapshots | TestExecution, FailureObservation и membership ReliabilityDataset |

Materialization разрешена только для `local_import_id` с verified source integrity
и явной binding исходного specimen к неархивному local `Specimen`. Она никогда
не обновляет source row, archive, projection или binding. Повтор с тем же
source identity возвращает существующую derived snapshot; новая export revision
создаёт самостоятельный execution.

## Поля и provenance

`ReliabilityCase` — semantic alias текущего `Project`, поэтому отдельная
таблица и второй identity не создаются.

`Specimen` использует существующий analyst-owner: `specimen_id`, marking,
wheel name и material берутся из локального dossier; `diameter_mm` — existing
`working_diameter_mm`; связь с producer — только через explicit
`r130sh_specimen_bindings.local_specimen_id` и `source_import_id` execution.
Материал отсутствует в M9a и никогда не копируется автоматически.

Каждый `TestExecution` хранит local `execution_id`, `imported_run_id`, local и
source specimen identities, method (`rbd`, `rpt`, `pmn`), неизменяемые JSON
snapshots planned parameters/result summary, lifecycle status и package-level
provenance. Это producer facts, а не расчётные input values.

Каждый `FailureObservation` хранит local `failure_id`, `execution_id`,
`failure_type`, subject (`specimen`, `equipment`, `unknown`), source event or
inspection reference, nullable cycles/duration/RPM и bounded vibration summary.
Equipment interruption, accepted measurement и planned target не преобразуются
в отказ образца, completed cycles или ресурс. Неизвестное сохраняется как
`NULL`, а не как zero.

`ReliabilityDataset` — persistence owner будущего явного отбора: local
`dataset_id`, единица life metric, bounded censoring policy и append-only
membership execution/observation с inclusion decision/reason. В M04A нет
calculator, automatic eligibility и UI редактирования dataset.

Для каждой derived строки обязательны `source_import_id`, outer package SHA-256,
`source_payload_path`, bounded record key и field reference. Absolute path,
ZIP bytes и row ordinal CSV не сохраняются. Package provenance включает producer,
schema, package/export/run identities и source snapshot SHA-256.

## Данные будущих модулей

| Будущий модуль | Что должно быть явно отобрано и versioned |
| --- | --- |
| Weibull | life metric и unit, population boundary, failure/right-censored/withdrawn/invalid, censor endpoint, failure mode; часы и cycles не смешиваются |
| Markov | утверждённые states, transition events, time/dwell, repair/withdrawal/absorbing states и observation window |
| Monte-Carlo | random variables/distributions, dependencies, limit-state, seed/sample count и model version |
| FMEA | methodology edition, item/function/failure mode/cause/effect, S/O/D scales/rationale, controls/actions/owners and residual-risk evidence |

Это список будущих контрактов, а не разрешение на расчёт или скрытые defaults.

## Persistence, IPC и UX

Поскольку release и пользовательские `.irproj` отсутствуют, clean pre-release
schema v1 получает M04A tables, indexes и immutable triggers непосредственно:
без migration, compatibility layer или backfill. Один ProjectSession/SQLite
connection и append-only project audit остаются единственными owners записи.

Python exposes narrow typed operations: materialize one eligible source и list
executions by wheel model. Main/Preload/Renderer получают только bounded DTO,
без paths, archive contents, SQLite или worker internals. Экран выбранной модели
колеса показывает read-only «Испытания» с RBD/RPT/PMN и честными integrity/outcome
labels; unbound source не приписывается колесу по marking/name.

## Acceptance and stop condition

M04A завершён, когда unit, persistence and reopen tests доказывают:

1. новая clean v1 schema содержит M04A owners, а source evidence is unchanged;
2. only explicitly bound, verified imports materialize exactly one immutable execution;
3. failures preserve subject/provenance and nullable unavailable source facts;
4. reopen restores the same execution/dataset state and rejects tampered derived evidence;
5. UI displays executions by local wheel identity without calculation claim.

Следующий этап определяет расчётный input/output contract, units, rounding,
classification policy и golden fixtures before adding any formula.
