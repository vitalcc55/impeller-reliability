# M04B — Statistical Classification & Life-Metric Foundation

## Результат этапа

M04B создаёт законченную production-цепочку от уже материализованного
`TestExecution` до явного решения инженера и неизменяемой версии
`ReliabilityDataset`. После закрытия и повторного открытия проекта инженер видит
тот же исходный результат, версии интерпретации, использованные свидетельства,
значение наработки или честно зафиксированное его отсутствие, а также все
рассмотренные включения и исключения выборки.

Этап не рассчитывает РБД, РПТ, ПМН, Weibull/MLE, доверительные интервалы или
иные показатели надёжности. `AnalysisInputSnapshot`, `CalculationSnapshot`,
расчётные jobs, графики и отчёты остаются M04C и последующим этапам.

## Источники и границы утверждений

- Продуктовые решения и ограничения этого этапа имеют приоритет.
- ПМИ Р130У, разделы 9.3, 10 и 11 и таблицы 3–5: для РБД отдельно названы
  продолжительность до отказа и время вращения в установившемся режиме; для
  РПТ — число циклов «разгон–торможение»; для ПМН — прочностной результат.
- ПМА Р130У, разделы 4–10: РБД, РПТ и ПМН имеют разные назначения и измеряемые
  величины.
- ТЗ «Показатели надежности крыльчаток вентиляторов», разделы 3, 6, 11–14 и
  приложения А–Б, использовано только как неотрецензированный ориентир состава
  будущего продукта. Его Python-фрагменты, стек, демонстрационные формулы и
  примеры не являются эталонами M04B.
- NIST Engineering Statistics Handbook, §8.1.3.1 «Censoring»: точное время
  отказа, правая граница наблюдения и интервал возникновения — разные данные;
  failure modes нельзя смешивать молча.
- SciPy `CensoredData`, разделы «Conventional terminology», «Parameters» и
  «Notes»: interval observation хранит две границы, а right-censored — известную
  нижнюю границу. SciPy в зависимости проекта не добавляется.

Если документ не содержит правила вычисления наработки из сложного запуска,
M04B не выводит его самостоятельно.

## Текущие владельцы

| Факт или действие | Канонический владелец |
| --- | --- |
| архив `.r130run`, export revision, source outcome и projection | `r130sh_source` / Python importer |
| физический образец и документы дела | существующие `Specimen` и `CaseDocument` в `analyst_enrichment` |
| materialized запуск и fact-level evidence | `TestExecution` и `FailureObservation` |
| статистическая интерпретация | новая versioned `ReliabilityObservation` в Python |
| состав выборки | новая versioned `ReliabilityDataset` в Python |
| SQLite, transactions, audit и reopen validation | одна существующая `ProjectSession` |
| wire types и отображение | `packages/contracts` и `apps/desktop` без предметной логики |

`Project` остаётся единственным ReliabilityCase. Вторые `Specimen`,
`TestExecution`, реестр документов, evidence engine, event store и generic
snapshot framework не создаются.

## Целевая модель

### ReliabilityObservation

Одна логическая observation принадлежит одному immutable `TestExecution`.
Сохранение и исправление создают последовательные immutable версии. Версия
фиксирует:

- `classification`: `failure | right_censored | withdrawn | invalid`;
- форму endpoint: `exact | right_bound | interval | unavailable`;
- metric kind и canonical unit либо явное отсутствие значения;
- нижнее/верхнее значение по форме endpoint;
- `scope=execution`, основание начала отсчёта и границы наблюдения;
- машинно-типизированное `metricOrigin=analyst_provided` для введённого числа и snapshot выбранного `CaseDocument` с его
  revision, locator и зарегистрированным file SHA-256 при наличии файла;
- выбранные `FailureObservation` evidence references;
- основание решения, автора, время, predecessor и canonical content hash.

M04B поддерживает только ближайшие life metrics:

| Method | Metric kind | Canonical unit | Численный контракт |
| --- | --- | --- | --- |
| `rbd` | `rbd_steady_rotation_time` | `hours` | canonical finite decimal, `0 <= value <= 1000000000` |
| `rpt` | `rpt_start_stop_cycles` | `count` | canonical integer, `0 <= value <= 1000000000000` |

Одинаковая размерность не делает разные metric kinds совместимыми. PMN остаётся
доступным как source execution/evidence, но текущая life-dataset policy считает
его неприменимым. Никакие значения не выводятся из `acceptedElapsedS`, samples,
target, timestamps или плановых коэффициентов.

Для `failure` значение может быть exact, interval или unavailable. Для
`right_censored` требуется доказанная `right_bound`. `withdrawn` и `invalid`
могут сохраняться без числа; они не становятся right-censored автоматически.
Interval сохраняется честно, но не включается как точная числовая точка текущей
политики.

### ReliabilityDataset

Логический dataset принадлежит одной `WheelModel`. Каждое изменение состава
создаёт immutable version, которая фиксирует:

- policy id/version `life_metric_exact_v1`;
- один method, metric kind и canonical unit;
- название, границу совокупности, применимую методику и явное основание
  сопоставимости условий;
- predecessor, автора, время и canonical content hash;
- каждую рассмотренную exact observation version;
- отдельно policy eligibility с кодом/объяснением и решение инженера
  `included | excluded` с причиной.

Membership никогда не хранит вторую classification. `included` допустим только
для policy-eligible observation: RBD/RPT, final package, совместимые
method/metric/unit, `failure + exact` либо `right_censored + right_bound`,
документированное происхождение и известные границы. `diagnostic_partial`, PMN,
interval/unavailable failure, withdrawn и invalid сохраняются как кандидаты с
явной причиной, но не включаются.

В одной dataset version рассматривается ровно одна выбранная observation
version каждого execution; предыдущие interpretation versions остаются в
истории observation, а не дублируются как независимые кандидаты. Разрешено не более одного included observation на
`local_specimen_id` и не более одного included observation на source `run_id`.
Идентичность берётся из immutable execution/source snapshot, а не из текущего
binding, marking или package id. Excluded alternatives остаются в составе.

## Инварианты persistence и revisions

- Clean pre-release schema v1 изменяется напрямую; migration, backfill и alias
  для M04A stub не создаются.
- Observation version, references, dataset version и memberships append-only и
  защищены FK, CHECK, UNIQUE и immutable triggers.
- Создание версии, refs/memberships и одного audit event атомарно.
- Caller создаёт UUID v4 версии. Exact retry с тем же identity и canonical
  content возвращает существующий результат без audit; то же identity с иным
  content даёт conflict.
- Новая версия требует ожидаемую текущую head version; stale head даёт
  `revision_conflict`, не ветвление и не перезапись.
- Dataset membership ссылается на exact observation version; последующая
  переклассификация старый dataset не меняет.
- Snapshot документа содержит использованные реквизиты. Изменение текущей
  карточки документа не меняет старое решение.
- Reopen проверяет exact schema, version chains, hashes, audit, typed refs,
  membership eligibility snapshots и уникальности; silent repair отсутствует.
- Missing/modified managed source остаётся наблюдаемым integrity-состоянием и не
  переписывает уже сохранённые snapshots.

## Минимальные production-операции

| Operation | Назначение и bounded contract |
| --- | --- |
| `reliabilityExecution.listPage` | compact keyset page, default 25/max 50, stable `materializedAtUtc DESC, executionId ASC` |
| `reliabilityExecution.getDetail` | один bounded execution, source identity, outcome/validity/completeness, evidence и ограничения |
| `reliabilityObservation.listVersions` | не более 50 bounded immutable versions одного execution |
| `reliabilityObservation.getVersion` | одна exact immutable version со всеми provenance snapshots |
| `reliabilityObservation.createVersion` | явное решение инженера, caller UUID, expected head, exact retry/conflict |
| `reliabilityDataset.listPage` | compact dataset heads выбранной WheelModel, default 25/max 50 |
| `reliabilityDataset.getVersion` | exact version с жёстко ограниченными 100 members; превышение считается повреждением |
| `reliabilityDataset.createVersion` | 1..100 рассмотренных decisions, exact observation versions, expected head |

Старый unbounded `listByWheel` и внутренний `create_dataset` удаляются, а не
сохраняются как compatibility paths. SQL использует `LIMIT max+1`, уникальный
tie-breaker и opaque cursor, связанный с фильтром. Полные execution/dataset JSON
snapshots не входят в page DTO. Hard wire limit остаётся 1 MiB UTF-8 с учётом
JSONL newline: Main проверяет request до записи, Python — response до stdout,
Main повторно ограничивает ответ.

## UI-вертикаль

В существующем project workspace появляется предметно выделенная поверхность
«Данные надёжности», использующая Mantine и текущую visual system:

1. compact paged executions выбранной WheelModel;
2. detail выбранного запуска с раздельными source outcome, technical status,
   validity/completeness, evidence и ограничениями;
3. draft новой observation version с классификацией, metric/absence,
   document/evidence provenance и причиной;
4. dataset builder, где каждый кандидат явно included/excluded и показывает
  policy eligibility после Python validation и persisted readback;
5. exact historical version viewer и создание следующей версии.

Черновик, persisted version и pending save визуально различимы. Validation,
conflict и transport failure сохраняют draft. Смена execution/page/section,
restart и close используют существующий dirty-owner guard; late response
проверяется selection/request token. Reattach выполняет authoritative readback,
а lost-response retry сверяется по version UUID. Поддерживаются Windows desktop
1280×720+ и существующий defensive narrow reflow; mobile redesign не создаётся.

## Матрица TDD и проверок

| Invariant | Canonical owner | Уровень | Ожидаемый результат |
| --- | --- | --- | --- |
| classification не выводится из producer status | Python domain | unit | explicit-only, unknown combinations fail closed |
| failure без metric сохраняется | Python domain/persistence | unit+integration | version exists, policy ineligible |
| finishedAt/detection не становятся exact | Python domain | unit | endpoint остаётся interval/unavailable |
| metric kind/unit/value contract | Python domain | exhaustive unit | zero distinct from NULL; negative/nonfinite/fractional count rejected |
| analyst provenance frozen | Python persistence | integration | document edits do not change old snapshot |
| foreign evidence/document/version rejected | SQLite/Python | integration | atomic typed failure |
| dataset stores eligibility and include/exclude separately | Python persistence | unit+integration | excluded candidates remain visible |
| one included per specimen and source run | SQLite + Python | integration | duplicate inclusion rolls back |
| immutable version/history | SQLite/Python | integration | old observation/dataset byte-semantic snapshot unchanged |
| stale/exact/conflicting retry | service/repository | integration | conflict or one existing row/audit |
| reopen/tamper/schema | project validator | integration | exact restore or `corrupt_project`, no repair |
| page stability/bounds/UTF-8 budget | repository/protocol | unit+process | no skip/duplicate; maximum response below budget |
| operation maps | Pydantic/Zod/Main/Preload | unit+integration | exhaustive typed parity |
| production workflow | Electron/real worker/SQLite | E2E+packaged smoke | classify → dataset → revise → reopen |
| renderer states/layout | Renderer/preview | component+Browser | draft/pending/error/stale/keyboard/1280×720 |
| frozen M04A.1 evidence | importer snapshot gate | integration | 21 archives/index/hashes unchanged |

Независимый синтетический scenario set задаёт expected classification,
eligibility и membership вручную, не через production helper.

## Подэтапы и commits

### M04B.1 — контракт, domain и persistence

- этот рабочий план и изменения domain/requirements/architecture ownership;
- meaningful-red semantic/persistence tests;
- clean-v1 tables, constraints/triggers, revisions, hashes, audit и reopen;
- observation/dataset repository + service/session operations;
- bounded execution repository page/detail.

Критерий: Python tests доказывают A–I и K для canonical owner; review profiles
«методология», «архитектура» и «persistence» закрыты; отдельный commit зелёный.

### M04B.2 — typed IPC и production UI

- Pydantic/Zod contracts, dispatcher, worker policy, Main, Preload, preview;
- отдельная UI-поверхность и общий draft/pending/reattach lifecycle;
- process IPC, renderer tests, Browser QA и Electron E2E.

Критерий: production workflow до reopen проходит реальный worker/SQLite;
IPC/UX и затронутые domain profiles закрыты; отдельный commit зелёный.

### M04B.3 — closure

- adversarial/tamper/lost-response/max-budget сценарии;
- architecture/domain/requirements/traceability/IPC/test strategy/roadmap/
  changelog синхронизированы без второй спецификации;
- `pnpm verify -- --IncludePackaging`, Browser, Electron E2E, packaged smoke,
  detector, `git diff --check` и frozen source checks;
- итоговый пяти-профильный review и проверка findings.

Критерий: один PR в `main`, актуальные Quality/review threads проверены, merge и
M04C не выполнены, рабочее дерево чистое.

## Условия завершения

M04B завершён, когда инженер может через production-приложение сохранить
обоснованную versioned интерпретацию, сформировать прослеживаемую versioned
выборку с видимыми исключениями, исправить оба решения новыми версиями и после
reopen получить те же данные без изменения source facts. Любое число в
included membership имеет точный metric kind, unit, endpoint и frozen
provenance. Приложение при этом не заявляет расчёт показателей надёжности или
полное соответствие ПМИ.
