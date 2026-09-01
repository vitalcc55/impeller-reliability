# M03B — Production `.r130run` Import and R130SH M9b Downstream Acceptance

## Outcome contract

M03B добавляет первую изменяющую аналитическое дело вертикаль: инженер выбирает выпущенный R130SH `.r130run`, Impeller Reliability проверяет его, сохраняет точную неизменяемую копию в `.irproj`, регистрирует immutable `r130sh_source`, показывает первичные значения отдельно от `analyst_enrichment`, позволяет явно связать исходный образец и зафиксировать разрешение поддерживаемых расхождений, а после закрытия и повторного открытия получает тот же источник и provenance.

Этап не выполняет расчёты РБД/РПТ/ПМН, не создаёт `TestCampaign`, `AnalysisInputSnapshot`, `CalculationSnapshot`, FMEA/FMECA, статистику, вибродиагностику, графики или итоговую отчётность. Impeller Reliability не запускает R130SH, не читает его SQLite и ничего ему не передаёт.

## Exact baselines и release boundary

- downstream: `vitalcc55/impeller-reliability@d0708924e8c9f19ac64668571846bccb1d6e21fa`, `main == origin/main`, GitHub Quality run `33306128933` — `success`;
- upstream producer contract: `vitalcc55/R130SH`, branch только как справочная provenance `codex/data-and-protocol-improvements`, exact commit `01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63`;
- upstream M9a завершён: опубликованы 21 producer-generated package files для 18 сценариев с реальными outer SHA-256; M9b выполняется downstream в Impeller Reliability;
- GitHub releases и tags Impeller Reliability отсутствуют, `CHANGELOG.md` сохраняет `Unreleased`, подтверждённых пользовательских `.irproj` нет;
- поэтому единая clean pre-release project schema остаётся `PROJECT_SCHEMA_VERSION = 1`, а M03B расширяет её exact published DDL и единственную запись ledger `0001 create_project_database`; migration v1→v2, compatibility views, dual-write и legacy adapters не создаются;
- если до merge появится доказательство фактического выпуска schema v1 или обязательных пользовательских данных, изменение schema останавливается и release owner выбирает forward migration отдельно.

Upstream-файлы `tests/fixtures/r130run/v1/m9a/README.md` и `tests/integration/test_m9a_run_package_goldens.py`, названные в исходной постановке, в exact commit отсутствуют. Фактические владельцы — `docs/contracts/r130run/v1/README.md`, `tests/helpers/m9a_package_reader.py` и `tests/unit/test_m9a_golden_packages.py`; downstream привязывается к Git tree exact commit, а не к предположенному пути или плавающей ветке.

## Product и ownership boundary

Слои не смешиваются:

- `r130sh_source` — неизменяемый producer package, inventory, узкая projection и producer provenance;
- `analyst_enrichment` — существующие редактируемые `CustomerProfile`, `WheelModel`, `Specimen`, `CaseDocument`;
- `derived_analysis` — будущие versioned analysis/calculation snapshots, отсутствующие в M03B.

Python worker остаётся единственным владельцем validation, managed archive, SQLite, idempotency, projection, binding, resolution и audit. Main единолично показывает file dialog и передаёт approved absolute `sourcePath` worker-у. Renderer получает только typed IDs, basename, progress, source values и статусы; filesystem path, ZIP bytes, полный CSV/JSONL и stack trace в Renderer не попадают. TypeScript не повторяет semantic validation и не вычисляет eligibility.

## Upstream identity и contract acceptance

`package_id` — canonical RFC UUID producer-а; downstream принимает UUIDv4 и UUIDv7 и не смешивает его с локальными entity UUIDv4. Реальные M9a `run_id`, `plan_id` и `specimen_id` являются bounded upstream identities и в published goldens представлены slug identifiers; contract принимает их как отдельный nominal type и также допускает canonical UUIDv7. Marking/label никогда не заменяют identity.

M03A synthetic fixtures сохраняются для unit/negative/safety validation и не являются producer compatibility proof. Новый offline snapshot `fixtures/contracts/r130run/v1/m9a` хранит exact M9a index, 21 archive и `UPSTREAM_SOURCE.json`; CI не обращается к сети или соседнему checkout R130SH. Snapshot обновляется только отдельным осознанным change set, не автоматически.

После полного M9b gate handshake объявляет `supportedRunPackageSchemas = ["r130sh.run-package.v1"]`. `supportedPlanSchemas` остаётся пустым. Ни validation, ни import DTO не содержат `calculationEligible` или `readyForCalculation`.

## Clean project schema v1

M03B добавляет в clean v1 ровно пять предметных таблиц; имена ниже являются окончательным contract этапа.

### `r130sh_sources`

Одна immutable строка на принятую export revision:

- `local_import_id TEXT PRIMARY KEY` — local application UUIDv4;
- `package_id TEXT NOT NULL`;
- `export_revision INTEGER NOT NULL CHECK (export_revision >= 1)`;
- `outer_package_sha256 TEXT NOT NULL`;
- `run_id TEXT NOT NULL`;
- `package_kind TEXT NOT NULL CHECK (package_kind IN ('final', 'diagnostic_partial'))`;
- `package_schema TEXT NOT NULL`;
- `package_created_at_utc TEXT NOT NULL`;
- `source_snapshot_sha256 TEXT NOT NULL`;
- `producer_name`, `producer_version`, `producer_build_id`, `producer_git_commit TEXT NOT NULL`;
- `managed_relative_path TEXT NOT NULL UNIQUE` — только project-relative POSIX;
- `outer_size_bytes INTEGER NOT NULL CHECK (outer_size_bytes > 0)`;
- `imported_at_utc TEXT NOT NULL`;
- `validator_version TEXT NOT NULL`;
- `validation_contract_commit TEXT NOT NULL`;
- `structural_verdict TEXT NOT NULL CHECK (structural_verdict = 'passed')`;
- `semantic_verdict TEXT NOT NULL CHECK (semantic_verdict IN ('passed', 'passed_with_warnings'))`;
- `semantic_coverage_json TEXT NOT NULL CHECK (json_valid(semantic_coverage_json))`;
- `validation_findings_json TEXT NOT NULL CHECK (json_valid(validation_findings_json))`;
- `UNIQUE(package_id, export_revision)`.

`BEFORE UPDATE` и `BEFORE DELETE` triggers запрещают изменение/hard delete. Source row не получает mutable `record_revision`.

Idempotency:

- тот же `package_id + export_revision + outer SHA-256` возвращает существующий import как `existing`, не меняет revision и audit;
- та же пара `package_id + export_revision` с другим outer SHA-256 возвращает typed `import_integrity_conflict`, исходная строка и archive остаются неизменными;
- новая export revision создаёт отдельный immutable source той же producer series;
- повтор после потерянного ответа сначала сверяет registry и возвращает существующий результат без нового audit.

### `r130sh_source_inventory`

Immutable manifest inventory:

- `local_import_id TEXT NOT NULL REFERENCES r130sh_sources(local_import_id)`;
- `path TEXT NOT NULL`;
- `media_type TEXT NOT NULL`;
- `size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0)`;
- `sha256 TEXT NOT NULL`;
- `row_count INTEGER NULL CHECK (row_count IS NULL OR row_count >= 0)`;
- `semantic_coverage TEXT NOT NULL`;
- `PRIMARY KEY(local_import_id, path)`.

Парные immutable triggers запрещают UPDATE/DELETE. Таблица соответствует `manifest.files`; полный payload не извлекается в project tree.

### `r130sh_run_projections`

Одна immutable узкая projection на source, без physical measurement rows и без расчётных выводов:

- identities: `local_import_id` PK/FK, `run_id`, `source_specimen_id`, `mode`, `package_kind`;
- outcome: `technical_status`, `termination_reason`, `specimen_outcome`, `run_validity`, `data_completeness`, `partial_reasons_json`, `resume_available`;
- plans: `original_plan_id`, `original_plan_revision`, `original_plan_sha256`, `effective_plan_id`, `effective_plan_revision`, `effective_plan_sha256`, `original_plan_summary_json`, `effective_plan_summary_json`;
- time: `started_at_utc`, nullable `finished_at_utc`;
- source card: nullable `customer_full_name`, `customer_address`, `customer_order_reference`, `wheel_full_name`, `wheel_identifier`, `working_diameter_mm`, `sample_label`;
- environment/provenance: `environment_status`, `environment_summary_json`, `provenance_summary_json`;
- counts: `measurement_count`, `accepted_measurement_count`, `event_count`, `inspection_count`, `attachment_count`, `amendment_count`;
- accepted aggregate: nullable `crediting_policy`, `accepted_elapsed_s`.

JSON columns содержат только bounded whitelisted summary, необходимый текущему read model; это не generic EAV и не копия полного payload. Полный `measurements.csv`, rejected rows, events, inspections и attachments остаются в immutable archive и читаются потоково будущими этапами. Projection имеет UPDATE/DELETE triggers.

### `r130sh_specimen_bindings`

Одна текущая optimistic binding на upstream specimen identity, общая для PMN/RPT/RBD imports:

- `source_specimen_id TEXT PRIMARY KEY`;
- `local_specimen_id TEXT NULL REFERENCES specimens(specimen_id)`;
- `record_revision INTEGER NOT NULL CHECK (record_revision >= 1)`;
- `updated_by_actor TEXT NULL`;
- `reason TEXT NOT NULL DEFAULT ''`;
- `created_at_utc`, `updated_at_utc TEXT NOT NULL`.

Import создаёт unresolved row с `local_specimen_id = NULL`, не создавая отдельного bind event. Явная bind/rebind/unbind mutation требует expected revision, actor, UTC и reason, выполняется атомарно с `r130sh_source.specimen_bound` audit. Одинаковая маркировка не создаёт binding и не объединяет разные `source_specimen_id`. Архивный local Specimen нельзя выбрать для новой binding; существующая historical binding остаётся видимой.

### `r130sh_enrichment_resolutions`

Append-only provenance для конечного whitelist, без универсального EAV:

- `resolution_id TEXT PRIMARY KEY` — local UUIDv4;
- `local_import_id TEXT NOT NULL REFERENCES r130sh_sources(local_import_id)`;
- `source_payload_path`, `source_field TEXT NOT NULL`;
- `target_entity_type TEXT NOT NULL CHECK (target_entity_type IN ('customer_profile', 'wheel_model', 'specimen'))`;
- `target_entity_id`, `target_field TEXT NOT NULL`;
- `decision TEXT NOT NULL CHECK (decision IN ('use_source', 'use_analyst', 'copied_to_analyst'))`;
- `actor`, `occurred_at_utc`, `reason TEXT NOT NULL`;
- `UNIQUE(local_import_id, source_payload_path, source_field, target_entity_type, target_entity_id, target_field)`.

UPDATE/DELETE запрещены. Запись выполняется атомарно с `r130sh_source.enrichment_resolution_recorded` audit. `copied_to_analyst` разрешён только для пустого поля и только с expected target revision; заполненное поле никогда не перезаписывается автоматически. Для конфликта `use_source` или `use_analyst` reason обязателен. Source value не изменяется.

## Supported source/enrichment whitelist

Whitelisted relationships основаны только на published M9a fields:

| Source | Допустимый analyst target |
| --- | --- |
| `run-summary.json / run_card.customer_name` | `customer_profile.fullName` |
| `run-summary.json / run_card.customer_address` | `customer_profile.legalAddress` или `actualAddress` |
| `run-summary.json / run_card.wheel_full_name` | `wheel_model.fullName` |
| `run-summary.json / run_card.wheel_identifier` | `wheel_model.designation` |
| `plan/original.json / source_values.nominal_rpm` | `wheel_model.nominalSpeedRpm` |
| `run-summary.json / run_card.working_diameter_mm` | `specimen.workingDiameterMm` |
| `run-summary.json / specimen_id` | `specimen.identificationNumber` только при явном создании нового Specimen |
| `run-summary.json / sample_label` | `specimen.marking` |

Batch, material/geometry descriptions, nominal diameter и иные отсутствующие M9a поля не выдумываются. Новая analyst entity может получить выбранные значения только после явного подтверждения и без перезаписи. Существующие analyst values редактируются существующими dossier operations; source/enrichment UI только показывает различия и вызывает узкие bind/resolution commands.

## Managed archive и failure postconditions

Финальный путь фиксирован:

```text
imports/r130sh/<package-id>/rev-<export-revision>/<outer-sha256>.r130run
```

Operation staging имеет точную grammar `imports/r130sh/.staging/<local-import-id>.part`. SQLite хранит только final project-relative POSIX path. Исходный absolute path не сохраняется, не возвращается Renderer, не входит в audit/logs.

Последовательность:

1. Main dialog разрешает ordinary `.r130run` и передаёт approved path только worker-у.
2. M03A foundation, обновлённый до exact M9a contract, bounded-валидирует исходный файл.
3. Worker потоково копирует в operation-owned staging, независимо считает outer SHA-256 и сверяет source identity до/после.
4. Staged copy повторно открывается и валидируется.
5. Worker выполняет atomic rename в final grammar.
6. Одна `BEGIN IMMEDIATE` transaction вставляет registry, inventory, projection, unresolved binding при отсутствии и audit.
7. Commit является cancellation fence; успешный ответ возвращается только после проверки registry/archive size/SHA/projection/audit.

Ошибка до commit удаляет только operation-owned staging/final orphan и не оставляет source row. Crash между rename и commit может оставить только незарегистрированный final path exact grammar; recovery после получения project lock удаляет его или идемпотентно сверяет зарегистрированный tuple. Неизвестные/reparse paths recovery не трогает. Crash после commit оставляет завершённый import; retry возвращает `existing`. Универсальный filesystem journal и persistent jobs framework не создаются.

Python использует одну ProjectSession и один SQLite connection. Copy/validation выполняются background import job, а подготовленная регистрация передаётся dispatcher main thread и завершается там при очередном `get`/lifecycle drain; SQLite connection между threads не разделяется. Все прочие project mutations во время active import получают typed `operation_in_progress`. Close/restart сначала выполняют bounded cancel/drain, затем закрывают session. Второй SQLite writer/ProjectSession не создаётся.

## Final и diagnostic partial policy

- `final` импортируется как завершённая evidence revision; `valid`, `partially_valid` или `invalid` outcome сохраняется как producer fact и не равен analysis eligibility.
- `diagnostic_partial` требует отдельного понятного подтверждения UI, сохраняется как diagnostic source с постоянной заметной меткой и partial reasons, не маскируется под `final` и по умолчанию не используется будущими расчётами.
- M03B нигде не возвращает eligibility/readiness boolean.

## Import job lifecycle и IPC

Одна active import job на worker/ProjectSession. Runtime in-memory; persisted truth — registry и archive.

States: `queued`, `validating`, `copying`, `revalidating`, `registering`, `completed`, `failed`, `cancelling`, `cancelled`.

Snapshot: `jobId`, `state`, `phase`, `completedBytes`, `totalBytes`, `completedEntries`, `totalEntries`, `startedAtUtc`, `finishedAtUtc`, `result`, `typedError`. `jobId` — caller-created local UUIDv4 и сохраняется до start для lost-response reconciliation.

Worker operations:

- `runPackageImport.start/get/cancel/discard`;
- `importedRun.list/get/verifySource/getResolutionState/bindSpecimen/applyEnrichmentResolution`.

Renderer API:

- `runPackageImport.selectAndStart/get/cancel/discard`;
- path-less `importedRun` read/mutation methods.

Cancellation cooperative до начала commit. После commit операция возвращает `completed`/`existing`, а не `cancelled`. `discard` удаляет только terminal runtime job. Потеря transport response сохраняет тот же `jobId`; UI сверяет job и registry, не объявляя failure до reconciliation.

## Audit

Существующий `project_audit_events` остаётся единственным audit owner. Новые mutation events:

- `r130sh_import.completed`;
- `r130sh_source.specimen_bound`;
- `r130sh_source.enrichment_resolution_recorded`.

Import audit содержит local import ID, package ID, export revision, outer SHA-256, run ID, package kind, managed relative path, producer identity и validation baseline commit. Он не содержит absolute source path, bytes, measurements/JSONL contents или stack trace. Exact no-op не создаёт event/revision.

## Integrity after import

Managed source status: `verified`, `missing`, `modified`, `verification_error`.

Project открывается при missing/modified archive; registry/audit/hash не меняются, а конкретный source нельзя использовать будущему расчёту. `list` выполняет только contained lstat/size presence check и не хеширует все archives. `verifySource` потоково пересчитывает outer SHA-256, повторно открывает ZIP и выполняет bounded structural validation без revision/audit при отсутствии изменений.

## UI workflow

В открытом project workspace появляется раздел «Результаты R130SH», а не только Diagnostics. Он наследует утверждённую Operate-систему `DESIGN.md`: одно data-first master-detail workspace, без dashboard mosaic, DataGrid и графиков.

Раздел содержит import action, persisted list, active progress/cancel и detail с десятью секциями: обзор; original/effective plan; outcome; customer/wheel/specimen; environment; provenance; event/measurement counts; inventory; differences/binding; validation findings. Full measurements CSV в DOM не попадает.

Source и analyst values показываются двумя явно подписанными колонками/блоками. Source read-only; analyst edit идёт существующими dossier commands. Binding существующего/нового Specimen явный, marking не auto-match. Diagnostic partial имеет постоянный status: «Диагностический неполный результат. По умолчанию не используется в расчётах».

UI покрывает dialog cancel/reject, progress, cooperative cancel, commit fence, interrupted/lost-response reconciliation, source integrity, dirty binding/resolution draft и detached state. Navigation/project close/open/restart/window close используют существующий единый draft/pending guard; active import нельзя забыть. Целевая платформа — только Windows desktop/laptop: поддерживаемая композиция начинается с 1280×720 и оптимизирована для 1536×864–1920×1080. Mobile, 640 px и отдельная узкоэкранная композиция не входят в продуктовую границу. Keyboard/focus, `status`/`alert`, перенос UUID/SHA и clean console обязательны.

## Tests и M9b acceptance evidence

Snapshot gate проверяет exact package set, index equality, count 21/scenarios 18, size и outer SHA каждого файла; missing/extra package ломает тест, автоматическое обновление запрещено.

Python tests проводят все 21 M9a packages через production import job и покрывают validation/projection, UUIDv7 package acceptance и local UUIDv4 separation, streaming copy/revalidation/rename, registry/inventory, no-op/conflict/revision series, final/diagnostic partial, shared/distinct specimen identities, immutable source, resolution provenance/no overwrite, crash orphan cleanup, cancel/lost response, missing/modified, close/reopen, open with broken archive, no path leakage/audit duplication.

TypeScript tests покрывают exhaustive operation maps/DTO, source identity schemas, reducer transitions/progress, typed errors, bind/resolution commands, absence paths/eligibility и polling cleanup.

Electron E2E покрывает наблюдаемый основной workflow create/open → import final → progress/detail → bind → resolve/copy → close/reopen, явное подтверждение diagnostic partial и bounded window close при отказе worker во время импорта. Exact repeat, conflict, cancel, lost response, missing/modified archive, crash-orphan recovery и detached lifecycle проверяются детерминированными Python/TypeScript tests; Browser/Chrome QA отдельно подтверждает keyboard/focus, clean console и desktop-композицию от 1280×720 с целевыми 1536×864–1920×1080.

Packaged smoke покрывает worker onedir, WinUnpacked и Portable import/reopen, CSP/fuses/no TCP/no orphan/no external Python/no extracted package tree.

Cross-repository evidence строится как параметризованная матрица:

```text
M9a fixture -> authored expected assertions -> importer result
            -> persisted project state -> close/reopen state
```

Она подтверждает все 22 сценария постановки: terminal distinctions, environment/rounding, retained physical stream and accepted aggregate, shared/distinct specimen identity, new/existing/incomplete dossier, source immutability, analyst separation и отсутствие любого outbound R130SH interaction.

## Definition of Done

1. Exact upstream baseline и immutable offline snapshot доказаны drift gate.
2. Clean schema v1 содержит пять зафиксированных M03B owners без migration/compatibility/EAV.
3. Все 21 producer packages проходят validation/import/persistence/reopen M9b matrix.
4. Archive/registry/projection/audit postconditions атомарны; no-op/conflict/crash/cancel доказаны.
5. Source identity, local UUIDv4, binding и enrichment provenance не смешиваются и не перезаписывают producer facts.
6. Diagnostic partial честно отделён; eligibility/readiness отсутствуют.
7. Typed IPC/Main/Preload/Renderer не раскрывают paths и корректно дренируют import при lifecycle transitions.
8. «Результаты R130SH» доступен с клавиатуры, проверен Browser/Electron на Windows desktop 1280×720+ и оптимизирован для 1536×864–1920×1080; mobile/640 px не входят в acceptance.
9. Owner docs и четыре architecture maps синхронизированы; отдельный status/report/ADR не создан.
10. `pnpm verify -- --IncludePackaging` и `git diff --check` зелёные; ветка опубликована, PR в `main` открыт, GitHub Quality успешен, подтверждённые P1/P2 review закрыты.
11. PR не сливается без отдельного решения владельца.
