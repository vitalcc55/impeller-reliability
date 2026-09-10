# M04C — First RBD Calculation Vertical Slice

## Результат этапа

M04C создаёт первый законченный расчётный сценарий РБД поверх конкретной
неизменяемой export revision R130SH:

`TestExecution + exact ImportedRunPlanSnapshot + explicit field selection`
`→ AnalysisInputSnapshot → Python RBD calculation → CalculationSnapshot`.

Инженер видит исходные и эффективные значения, отдельно — округлённые
исполняемые уставки producer-а, явно подтверждает происхождение каждого входа,
получает расчётные требования и при применимости результат таблицы 3, сохраняет
оба неизменяемых снимка одной операцией и после reopen читает тот же результат.
Новая операция с изменёнными входами создаёт новую пару снимков.

`ReliabilityDataset` не является обязательным входом расчёта одного исполнения.
Exact `ReliabilityObservation` или документ могут использоваться как
дополнительное versioned evidence только для физически соответствующего входа.

Не входят РПТ, ПМН, статистические оценки, gamma-resource, compliance conclusion,
интегрирование measurements, управление R130SH, отчёты и общий calculation
framework.

## Gate C0 — закрытие остаточных дефектов M04B

Research на baseline `530c32d625e0fe9cfa7e3309769de5d5b23581d8`
подтвердил четыре дефекта:

1. Загрузка страниц автоматически создаёт transient `candidateDecisions` для
   всех классифицированных исполнений. Просмотр должен только расширять список.
2. Строка historical member показывает `currentClassification`, хотя member
   удерживает exact `observationVersionId`.
3. Новая выборка получает готовые тексты оснований, которые можно сохранить как
   якобы инженерное решение.
4. Renderer использует `Textarea`, а единый Python `_bounded_text` запрещает LF.

Гипотеза reattach не подтверждена как самостоятельный дефект: ошибки exact
readback не считаются сохранением, а dirty draft остаётся локальным. Gate добавит
characterization/regression coverage; refactor допустим только при
воспроизведённом success-shaped результате.

### Контракт C0

- execution page item, draft candidate и persisted included/excluded member —
  разные состояния;
- кандидат добавляется и удаляется явной кнопкой, просмотр не меняет draft;
- попытка добавить 101-го кандидата отклоняется до изменения state с понятным
  сообщением; backend-limit `1..100` сохраняется без обрезания payload;
- persisted excluded members не исчезают при открытии версии;
- строка кандидата получает detail exact observation version; наличие latest
  показывается отдельно, переход на него — только явным действием с dirty state;
- новые basis/reason пусты; placeholders и help не попадают в payload;
- многострочные инженерные поля нормализуют CRLF и CR в LF, разрешают LF,
  запрещают остальные C0 controls и проверяют UTF-8 bytes; identifiers,
  canonical decimals и однострочные protocol fields не ослабляются;
- pending save, dirty guard, exact lost-response retry и reopen сохраняются.

Gate закрывается отдельным commit после узких тестов, read-only review,
проверки findings и зелёного релевантного gate.

## Первичные источники и терминология

Математический источник — ПМИ Р130У, редакция 01, 2024: разделы 5–6,
9.3.1, таблицы 2–3, формулы 1–3 и разделы 10–11. Оригинальный скан визуально
сверяется; OCR и ТЗ не являются oracle.

R130SH сохраняет три раздельных слоя одного плана:

| Слой       | Поле                               | Обозначение / смысл                       |
| ---------- | ---------------------------------- | ----------------------------------------- |
| source     | `base_cycles`                      | `N0`, базовое число циклов                |
| source     | `reserve_factor`                   | `k1`, коэффициент запаса                  |
| source     | `nominal_rpm`                      | `nP`, рабочая частота, об/мин             |
| source     | `acceleration_duration_s`          | `tP1`, время разгона РБД, с               |
| source     | `deceleration_duration_s`          | `tT1`, время торможения РБД, с            |
| methodical | `required_cycles_exact`            | точное `N0 × k1`, не gamma-resource       |
| methodical | `required_steady_duration_s_exact` | точное методическое время, с              |
| execution  | `target_cycles`                    | округлённая вверх уставка producer-а      |
| execution  | `target_steady_duration_s`         | удержание от округлённой уставки, с       |
| execution  | `total_duration_s`                 | плановая общая длительность producer-а, с |

В Impeller результаты называются «Расчётное требование», а source targets —
«Уставка в источнике R130SH». `N_gamma` не используется: такого owner-а в
актуальном R130SH и ПМИ для произведения `N0 × k1` нет.

## Получение входов и provenance

Существующая `_plan_summary` намеренно остаётся сокращённой. Новый bounded
read-only seam принадлежит существующему `R130shSourceRepository` и:

- принимает `localImportId`, `executionId` и explicit `original | effective`,
  но не filesystem path;
- проверяет exact execution/source/export tuple и текущую integrity managed
  archive;
- через существующий ZIP/inventory contract читает только bounded
  `plan/original.json` либо inner plan из `plan/effective.json`;
- не читает `measurements.csv`, не создаёт второй ZIP validator и не меняет
  source projection, execution или binding;
- возвращает source values, methodical exact values, producer targets, plan
  id/revision/path, inventory payload SHA-256, outer archive SHA-256 и producer
  provenance;
- не смешивает original/effective и не переключает export revision на latest.

Для отсутствующего или отклонённого source field доступно explicit manual
supplement: canonical value, unit, actor, reason и optional exact
document/observation revision. ПМИ-default может быть только отдельным
предложенным предположением, явно принятым инженером; source-фактом оно не
становится.

## Математический контракт `rbd_reference_v1`

### Обязательная часть

Входы: `nP > 0`, integer `N0 > 0`, `k1 > 0`, `tP1 >= 0`, `tT1 >= 0`.

```text
nmax1 = nP
required_cycles_exact = N0 × k1
tUST1_exact_s = 60 × required_cycles_exact / nmax1
TC1_exact_s = tP1 + tUST1_exact_s + tT1
T01_exact_s = TC1_exact_s
```

Это параметры выбранного РБД-сценария, не фактическая наработка и не
статистическая оценка ресурса. Producer targets сохраняются рядом и не
подменяют exact results.

### Таблица 3

Опциональный результат:

```text
N_OTK = floor((T_OTK - tP1) × nP / 60)
```

`T_OTK` — документированная продолжительность до отказа с совместимым началом
отсчёта. `accepted_elapsed_s`, timestamps, время обнаружения повреждения,
плановая длительность и M04B steady-time не подходят автоматически. Формула
использует `nP`, не измеренное `nOTK`.

Результат `not_applicable` с typed reason сохраняется при отсутствии exact
endpoint, interval/right-censored результате, неподдерживаемой фазе отказа,
паузах/повторных разгонах или неоднозначном начале отсчёта. Отсутствие
`T_OTK` не блокирует обязательную часть. Отрицательное подвыражение — validation
error, не ноль.

### Численный контракт

- wire/input: canonical finite ASCII decimal без exponent, знака `+`, пробелов,
  `_`, Unicode digits, NaN/Infinity; длина, digits, scale и magnitude bounded до
  создания дорогого числа;
- `N0` — canonical positive integer; остальные scalars — canonical decimal;
- pure Python owner преобразует проверенные Decimal в `Fraction` и выполняет
  все операции точно; ambient Decimal context и binary float не используются;
- exact rational хранится как canonical numerator/denominator и как decimal
  только когда дробь конечна; periodic value отображается bounded decimal
  preview с явной меткой, но preview не участвует в следующих операциях;
- `N_OTK` использует mathematical floor exact Fraction;
- seconds и hours хранятся раздельно; UI может показывать bounded derived
  human duration, не изменяя canonical result;
- algorithm id/version, numeric policy/version и formula references входят в
  immutable result hash.

Контрольные expected values задаются вручную: A — `3000000`, `120000`,
`120020`; B — `1500.3`, `60.012`, `70.012` при producer targets `1501`,
`60.04`, `70.04`; C — `1499750`; boundary `10.039→0`, `10.040→1`,
`10.041→1`.

## Владельцы и минимальные операции

| Ответственность                                   | Владелец                                       |
| ------------------------------------------------- | ---------------------------------------------- |
| formulas, units, applicability, phase coordinates | pure `calculations/rbd.py`                     |
| managed plan read/integrity                       | existing `R130shSourceRepository`              |
| orchestration/selection validation                | Python `ProjectService` / session              |
| atomic snapshots, hashes, audit, reopen           | narrow `RbdCalculationRepository`              |
| boundary DTO                                      | Pydantic + `packages/contracts` Zod/TypeScript |
| queue/deadline/process lifecycle                  | existing WorkerClient/Main                     |
| draft, selection token, rendering                 | Renderer                                       |

Narrow operations:

1. `rbdCalculation.getSourceInputs(executionId, planSelection)` — exact bounded
   source candidates/provenance and producer targets.
2. `rbdCalculation.create` — принимает caller UUID пары снимков, exact execution
   and source selection, field selections/manual supplements, optional failure
   evidence, actor/reason; Python повторно проверяет source, рассчитывает и
   атомарно сохраняет input + result + audit. Calculated outputs не принимаются.
3. `rbdCalculation.listPage(wheelModelId, cursor, limit)` — compact keyset
   history, default 25/max 50.
4. `rbdCalculation.getDetail(calculationSnapshotId)` — одна bounded immutable
   пара input/result с phase coordinates и provenance.

Отдельный job/process/progress engine не создаётся. Pending является честным
состоянием обычной typed operation.

## Persistence и reopen

Clean schema v1 расширяется напрямую только после read-only подтверждения, что
вне ignored test/smoke artifacts нет реальных пользовательских `.irproj`.

Две предметные таблицы `rbd_analysis_input_snapshots` и
`rbd_calculation_snapshots` имеют exact FK к execution/source, unique 1:1 link,
content hashes, byte-bounded canonical JSON/columns, immutable update/delete
triggers и один audit event на атомарную пару. Полный archive не копируется.

Одна `BEGIN IMMEDIATE` transaction создаёт обе строки и audit. Validation,
source-integrity failure, deadline или injected write failure оставляют ноль
строк. Exact retry с теми же caller IDs и operation hash возвращает existing без
audit; те же IDs с другим content дают typed conflict; новые IDs создают новую
immutable пару. `requestId` транспорта в identity не входит.

Reopen до materialization проверяет размеры, typed JSON/columns, FK/ownership,
1:1 link, source/plan/document/observation provenance, hashes, algorithm policy,
audit correspondence и отсутствие orphan. Missing/modified archive остаётся
динамическим integrity status и не переписывает исторический snapshot; новый
расчёт требует verified source. Silent repair и automatic recompute запрещены.

## UI и lifecycle

Отдельный `RbdCalculationPanel` расширяет существующий project workspace, не
перестраивая M04B-компонент. Operate-композиция:

1. exact execution/export revision;
2. original/effective source table и explicit radio selection для каждого
   входа/manual supplement;
3. ограничения/applicability и действие «Рассчитать и зафиксировать»;
4. semantic phase diagram, созданная из bounded Python coordinates, плюс
   обязательная табличная альтернатива;
5. exact result/provenance и paged history/detail.

Draft/persisted/pending/error/detached различимы. Любой async response содержит
selection token (`workspaceGeneration`, execution, plan hash, operation). Late
response для другой selection игнорируется. Validation/conflict/transport error
сохраняют draft и caller IDs; lost-response retry использует те же IDs. Reattach
принимает успех только после exact detail readback и tuple/hash match. Изменение
draft во время pending либо запрещено на участвующих controls, либо остаётся
dirty новой редакцией и не помечается saved.

UI — русский, единицы и source coordinates видимы. Схема подписана как
«Схема выбранного расчётного профиля, не фактические измерения». ECharts/KaTeX,
long series, DataGrid и export изображений не нужны. Проверяются 1280×720,
основной desktop, existing narrow reflow, keyboard/focus/AX/status/console.

## TDD-матрица

| Invariant                     | Owner                | Level                  | Expected result                                                 |
| ----------------------------- | -------------------- | ---------------------- | --------------------------------------------------------------- |
| C0 view не меняет draft       | Renderer             | component/E2E          | 101+ viewed, small explicit set saves                           |
| C0 max 100 atomic UI action   | Renderer/Python      | component/unit         | 101st rejected; prior 100 unchanged                             |
| C0 exact historical version   | Python DTO/Renderer  | integration/component  | v1 shown; latest v2 separate; explicit replace dirties          |
| C0 honest/multiline basis     | Renderer/Python      | component/integration  | blank defaults; CRLF→LF roundtrip; controls rejected            |
| source plan bounded/integral  | source repository    | integration            | exact fields/hashes; corrupt/missing typed; no CSV read         |
| exact arithmetic              | pure calculator      | exhaustive unit/golden | A/B/C, bounds, periodic, extremes, no pre-floor loss            |
| applicability separated       | pure calculator      | unit                   | reference result survives missing failure result                |
| source/manual provenance      | service/persistence  | integration            | exact coordinate/reason frozen; source unchanged                |
| snapshot atomicity/retry      | persistence          | integration            | pair+audit or zero; exact existing/conflicting retry            |
| immutable versions/reopen     | reopen validator     | integration            | old detail unchanged; tamper/orphan rejected                    |
| bounded IPC                   | protocol/contracts   | contract/process       | strict parity, Unicode/escaping, <1 MiB, no paths/results input |
| pending/restart/late response | Main/Renderer        | unit/E2E               | draft/caller IDs retained; wrong token ignored                  |
| production vertical           | real worker/Electron | E2E/packaged           | import→materialize→calculate→reopen exact detail                |
| regression                    | existing owners      | full gate              | M04A/M04B/frozen 21 packages unchanged                          |

## Подэтапы и критерии завершения

### C0 — M04B residual fixes

Characterization → tests → implementation → профильный review → findings
closure → narrow checks → commit. Статус: закрыт на ветке
`codex/m04c-rbd-calculation`: все четыре findings подтверждены и исправлены,
reattach-гипотеза уточнена и закрыта fail-closed сверкой, финальные профильные
review не содержат findings. `pnpm verify -- --IncludePackaging` прошёл: 71
Vitest, 438 Python tests (85.55%), 15 Electron E2E, worker build, WinUnpacked и
Portable smoke; Browser ready/unavailable и detector выполнены.

### C1 — mathematical/source contract

Domain docs + independent golden → pure calculator → bounded managed-plan read →
unit/integration review → commit. Критерий: exact A/B/C и frozen rounding case,
source projections неизменны. Статус: закрыт на ветке. Pure Python owner
использует exact `Fraction`, результаты таблицы 3 переходят границу как
canonical integer strings, golden закрепляет ПМИ и примеры A/B/C. Source seam
связан с exact execution/import tuple, проверяет registry hash receipt и читает
только выбранный bounded plan member. Два повторных профильных review не
содержат findings. `pnpm verify -- --IncludePackaging` прошёл: 71 Vitest,
469 Python tests (85.60%), 15 Electron E2E, worker build, WinUnpacked и Portable
smoke.

### C2 — snapshots and IPC

Clean schema v1 + atomic repository/reopen → four narrow operations through all
typed boundaries → retry/tamper/process tests → review → commit.

### C3 — production UI and closure

RBD panel + preview states + real-worker E2E/reopen + packaged calculation smoke
→ Browser/Impeccable detector → final profile reviews → findings closure →
`pnpm verify -- --IncludePackaging` → `git diff --check` → commit/push/PR.

Завершение этапа означает один прослеживаемый production-сценарий от exact
managed R130SH source до сохранённого результата после reopen. Это не
статистический ресурс модели колеса и не полное подтверждение соответствия ПМИ.
