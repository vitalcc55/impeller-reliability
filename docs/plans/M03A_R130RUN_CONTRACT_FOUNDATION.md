# M03A — R130SH Run Package Contract Foundation

## Closure note

M03A завершён и слит как read-only validation foundation. Его exact synthetic snapshot baseline `f02f6d954246a5ab6f57d33dac724ce03d7fb841` сохраняется только как историческая provenance созданных тогда unit/negative fixtures. Текущий production contract и M03B/M9b acceptance принадлежат exact R130SH M9a commit `01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63` и отдельному snapshot `fixtures/contracts/r130run/v1/m9a`; M03A больше не описывает текущий upstream status.

## Наблюдаемый результат и граница

Инженер выбирает candidate `.r130run` в системном диалоге → Electron Main разрешает один конкретный ordinary file → Python worker запускает одну ограниченную по ресурсам read-only job → пакет потоково проверяется без извлечения → Renderer показывает ход, contract provenance, structural/semantic verdict и ограниченный список findings.

M03A ничего не импортирует, не меняет `.irproj`, `project.sqlite`, manifest, assets, revisions или audit и не определяет пригодность результата для расчётов. Job существует только в памяти worker, а Renderer держит заменяемую transient-копию snapshot/report для отображения; persisted evidence не создаётся. `r130sh_source`, import receipts, TestCampaign, ImportedTestRun, source/enrichment resolution, AnalysisInputSnapshot и CalculationSnapshot относятся к M03B/M04 и не создаются. Project schema остаётся чистой pre-release v1; M03A не добавляет и не запускает новую forward migration или import/source tables.

## Владение контрактом и snapshot

Владелец `.r130run` schema, vocabulary, exporter и M9a golden packages — `vitalcc55/R130SH`. Baseline M03A закреплён на commit `f02f6d954246a5ab6f57d33dac724ce03d7fb841`, который является потомком завершённого M3 `9f2e602498bade39f51f3785ff131dc38c8799d6`.

Автономный snapshot хранится в `fixtures/contracts/r130run/v1/` и содержит только синтетические README/examples/expectations. `UPSTREAM_SOURCE.json` фиксирует repository, exact commit, исходный путь, Git blob и SHA-256 каждого файла, время snapshot и формулировку `synthetic target examples, not M9a golden packages`. CI проверяет snapshot локально и не зависит от сети или соседнего checkout. Обновление snapshot выполняется только отдельным осознанным commit.

Исторический `as-is-v7-baseline.example.json` не входит в snapshot и не является package contract/input. Production-код, SQLite, PDF, фотографии, пользовательские данные и нормативные документы R130SH не копируются.

## Фактический upstream status на момент M03A

- M0, M1, M2, M3, M4a и M5a завершены; следующий upstream-этап — M4b.
- Frozen target examples синтетические; production exporter M8 и M9a golden packages отсутствуют.
- M03A разрешён только как contract/validation foundation.
- M03B production importer заблокирован до M9a и cross-repository acceptance.
- Example UUID выглядят как v7, а текущий upstream runtime создаёт часть source IDs как v4; это не доказательство отсутствующего exporter. Локальный M03A acceptance profile принимает канонические source UUID v4/v7 и не использует локальный UUID v4 `entityIdSchema`. Другие версии не объявляются поддержанными без отдельного upstream решения.

## Матрица покрытия

| Поверхность | Structural / integrity | Semantic | Статус M03A |
| --- | --- | --- | --- |
| ZIP envelope и имена | EOCD/ZIP64 bounds, POSIX-relative paths, duplicate/case collision, flags, type, locally supported methods | deterministic byte policy не заморожена | safety profile strict; exporter byte policy `contract_gap` |
| `manifest.json` | bounded UTF-8/JSON, identity, producer, unique inventory, size/SHA-256/row_count shape | подтверждённые поля manifest | strict required fields, additive fields допустимы |
| Payload inventory | declared/missing/undeclared, actual decompressed size/SHA-256 | media-type vocabulary только на уровне example | strict integrity |
| RBD plan example | bounded JSON и identity | exact/rounded values и frozen shape | covered |
| Measurement example | bounded JSON / streaming package payload | frozen measurement model | covered только для example shape; production CSV schema отсутствует |
| Accepted projection | bounded JSON | frozen projection shape | covered |
| Event | streaming JSONL syntax | frozen event envelope/payload hash field | envelope covered; event registry gaps отмечаются |
| Inspection | bounded JSON | frozen inspection shape | covered |
| Provenance | bounded JSON | frozen provenance shape | covered |
| M9a expectations | bounded JSON | vocabulary 18 package scenarios + one non-package expectation | vocabulary covered; это не golden evidence |
| `measurements.csv` | bounded UTF-8 и streaming CSV parser | header/order/dialect/columns не заморожены | `not_available` |
| `run-summary`, `environment`, descriptors, baseline, attachment/protocol indexes | inventory integrity и syntax при известном JSON media type | полные schemas отсутствуют | `not_available` / `contract_gap` |
| `checksums.sha256` | ровно один opaque core entry и bounded bytes | line grammar не заморожена | содержимое не интерпретируется; hashes проверяются по manifest |

Structural verdict `passed` означает только целостность проверенного envelope/inventory. Partial semantic coverage допустимо и никогда не означает import acceptance или analysis eligibility.

## Package identity

Отдельные source types: `RunPackageId`, `RunId`, `SpecimenSourceId`, `PlanId`, `MeasurementId`, `EventId`, `InspectionId`. Они не смешиваются с `projectId`, `wheelModelId`, `specimenId` и `caseDocumentId`.

Manifest проверяет:

- `schema_version = r130sh.run-package.v1`;
- `export_revision >= 1`;
- `package_kind = final | diagnostic_partial`;
- канонические UUID v4/v7 `package_id` и `run_id`;
- календарно корректный UTC `created_at_utc` с явной зоной;
- lowercase SHA-256 `source_snapshot_sha256`;
- producer name/version/build/commit;
- уникальный нормализованный inventory.

Worker независимо вычисляет `outerPackageSha256`. Компоненты будущего idempotency tuple `package_id + export_revision + outerPackageSha256` уже отображаются в M03A report, но не сохраняются как receipt и не запускают deduplication/import.

Frozen final core: `manifest.json`, `plan/original.json`, `plan/effective.json`, `plan/amendments.jsonl`, `run-summary.json`, `environment.json`, `provenance.json`, `events.jsonl`, `measurements.csv`, `measurement-descriptors.json`, `accepted-summary.json`, `vibration-baseline.json`, `inspections.json`, `attachments/index.json`, `checksums.sha256`. Допустимые optional prefixes из upstream target — `attachments/` и `protocol/`, но их index/release schemas пока `not_available`; произвольные undeclared entries запрещены. Для `final` отсутствие core path — semantic failure. Для `diagnostic_partial` точные mandatory/partial-reasons rules ещё не заморожены: integrity заявленного inventory проверяется полностью, а completeness получает `contract_gap` и semantic `partial`, не вымышленную acceptance rule.

## ZIP/file safety и технические пределы

Пределы — operational guards M03A, а не нормативные ограничения R130SH. Они пересматриваются после реальных M9a packages.

| Ресурс | Предел M03A |
| --- | ---: |
| Outer source file | 8 GiB |
| Central directory | 16 MiB |
| Entries | 4 096 |
| UTF-8 package path | 512 bytes |
| Extra/comment metadata | 8 KiB per entry; archive comment 64 KiB |
| `manifest.json` | 2 MiB |
| Один materialized JSON | 16 MiB, depth 64, nodes 250 000 |
| Decimal scalar frozen plan | 128 chars; 64 significant digits; exponent and adjusted exponent within ±1 024 |
| JSONL line | 1 MiB |
| CSV logical record | 8 MiB; field 1 MiB |
| Cumulative decompressed payload | 32 GiB |
| Detailed findings | 200; aggregate counts сохраняются |
| JSONL IPC response | меньше 1 MiB |
| Job wall-clock budget | 30 minutes |
| Streaming chunk | 1 MiB с cancellation/deadline checkpoint |

Высокий compression ratio сам по себе не отклоняется. Разрешены `ZIP_STORED` и `ZIP_DEFLATED`; остальные методы возвращают finding `unsupported_compression`. ZIP64 допустим только при корректной bounded structure. Package открывается один раз read-only; initial/final `fstat` и `lstat` сверяют size, `mtime_ns` и доступную file identity. Изменение источника возвращает `source_changed`.

Отклоняются non-ZIP/multidisk/malformed envelope, encrypted entries, absolute/drive/UNC/backslash/`..`/control/NUL/noncanonical paths, exact duplicates, Unicode-normalized case-insensitive collisions, symlink/reparse-like и special entries, overlap/local-header mismatch, excess metadata, missing/multiple manifest, undeclared/missing inventory и фактические size/SHA-256 mismatches. Извлечение, staging tree, antivirus, macro/XML inspection и защита от злонамеренного same-user TOCTOU с восстановлением stat-атрибутов не входят в M03A.

Canonical path algorithm: raw central-directory name обязан быть UTF-8 при non-ASCII, не иметь BOM/control/legacy decoding, совпадать со своей Unicode NFC-формой и состоять из непустых POSIX segments. Collision key — `NFC(path).casefold()`. Explicit directory entries не входят в manifest и отклоняются как undeclared. Разрешены только UTF-8/data-descriptor flags; encryption/reserved flags отклоняются. EOCD обязан завершать archive с bounded comment; multiple EOCD, leading/trailing payload, overlapping ranges и расхождение local/central names отклоняются.

Bounded JSON parser отклоняет BOM, invalid UTF-8, duplicate keys, `NaN`/`Infinity`, excess depth/nodes. Frozen semantic models требуют известные обязательные поля и типы, но локально допускают additive fields в соответствии с целевой upstream M9a policy из implementation plan; до реальных M9a packages это отдельно отмечается как contract coverage gap. Cross-file checks связывают доступные `run_id` и проверяют source-ID shape в plan/event/inspection/provenance/accepted projection. Членство `accepted measurement_id` в `measurements.csv` не проверяется до фиксации production CSV header/order/dialect: эта связь остаётся `contract_gap`, а не догадкой.

## Порядок validation

`approved file → read-only open/fstat → streaming outer SHA-256 → bounded ZIP index → bounded manifest parse → strict identity/inventory → streaming payload size/hash/row counters → covered syntax/semantics → final stat → bounded report`.

Manifest и checksum index не хешируют себя. Payload JSON не загружается сверх bound, JSONL читается line-by-line, CSV — потоково. `checksums.sha256` grammar не придумывается; authoritative для M03A declared hashes берутся из manifest.

## Job lifecycle

Python владеет одним `RunPackageValidationJobManager`: одна in-memory job, один background thread, `Lock` и cooperative cancellation event. Background thread не вызывает ProjectService/ProjectSession/SQLite и не пишет stdout. Только main worker thread сериализует request/response JSONL.

States: `queued → running → completed | failed`, `queued|running → cancelling → cancelled`. Terminal state монотонен. Некорректный package завершает job как `completed` с failed verdict/findings; `failed` означает невозможность закончить validator (`storage_error`, `timeout`, `source_changed`, sanitized internal validation error) и не содержит report. Caller-generated UUID v4 `jobId` делает retry `start` идемпотентным: тот же `jobId` и тот же immutable internal source fingerprint/budget возвращают существующий snapshot; другой fingerprint/budget возвращает `job_id_conflict`, а другой ID при занятом slot — `operation_in_progress`. Fingerprint существует только внутри worker job record и включает resolved source identity, initial size/mtime/file identity; path не попадает в DTO/log. Main задаёт fixed `validationBudgetMs = 1 800 000`; worker contract допускает только `1 000..1 800 000`. `cancel` terminal job идемпотентен; `discard` разрешён только после завершения thread, после него `get` возвращает `entity_not_found`.

Cancel/completion/source-change race линеаризуется одним lock: принятая до terminal publication cancellation побеждает и даёт `cancelled`; уже опубликованный `completed`/`failed` не меняется. `source_changed` публикуется как `failed + typedError`, без partial report.

Phases: `source_check`, `outer_hash`, `zip_index`, `manifest`, `payload_integrity`, `semantic_validation`, `finalizing`. Progress бывает `known` или `unknown` и содержит непротиворечивые byte/entry counters.

Job является application-global Diagnostics state и не зависит от ProjectSession: `project.close` её не отменяет. Controlled restart/application shutdown выполняет `stop intake → set cancel → join до 1 500 ms внутри system.shutdown deadline → close ProjectSession → shutdown acknowledgement`. Если cooperative thread не завершился, daemon thread уничтожается вместе с старым worker; Main status transition однозначно переводит Renderer job в infrastructure-interrupted/failed, а новый worker стартует с пустым registry. Unexpected crash имеет тот же наблюдаемый исход без persisted result. Persistence journal не создаётся.

## IPC

Worker operations:

- `runPackageValidation.start`;
- `runPackageValidation.get`;
- `runPackageValidation.cancel`;
- `runPackageValidation.discard`.

Renderer-facing API:

- `runPackageValidation.selectAndStart({ jobId, replaceJobId? })`;
- `get(jobId)`;
- `cancel(jobId)`;
- `discard(jobId)`.

Operation-specific schemas:

- public `selectAndStart`: `{ jobId: UUIDv4, replaceJobId?: UUIDv4 }`; internal Main→worker `start`: `{ jobId, replaceJobId?, sourcePath, validationBudgetMs }`, где `sourcePath` отсутствует во всех public DTO. `replaceJobId` разрешает атомарно заменить только указанную terminal job после успешного выбора файла; active job сохраняется;
- `get/cancel/discard`: `{ jobId: UUIDv4 }`;
- `start/get/cancel`: `RunPackageValidationJobSnapshot`; `discard`: `{ jobId, discarded: true }`;
- snapshot: identity/state/phase/progress/timestamps и ровно одно из terminal `report`/`typedError` по state invariant;
- outer typed errors: `entity_not_found`, `operation_in_progress`, `job_id_conflict`, `validation_error`, `cancelled`, `timeout`, `storage_error`, `worker_unavailable`; terminal job errors: `cancelled`, `timeout`, `source_changed`, `storage_error`, `validation_error`;
- каждый response сохраняет существующие requestId/revision echo, Zod/Pydantic strict parsing и exhaustive operation/result/policy maps. Ошибка operation не меняет job state, если переход не был принят.

Main владеет `.r130run` dialog, ловит rejection как `storage_error`, различает resolved cancellation, выполняет ordinary-file/extension gate и внедряет approved `sourcePath` только во внутренний start request. Renderer/Preload payload и report не содержат absolute path. Generic channel/operation отсутствует.

Capabilities включают четыре validation operations, но `supportedRunPackageSchemas` и `supportedPlanSchemas` остаются пустыми. Report содержит `contractSchema = r130sh.run-package.v1` и `validationLevel = synthetic_contract_foundation`; это не import support.

## Validation report

Typed report: validator/version/validation level, upstream repository/commit, contract schema, source filename без каталога, outer size/SHA-256, package identity/kind/producer, entry and byte counters, structural/semantic verdicts, per-area semantic coverage, bounded findings/counts, start/finish UTC.

Structural verdict: `passed | failed`. Semantic verdict: `passed | partial | failed | not_available`. Finding: stable `code`, `severity`, package-relative `location`, fixed-template `message`, `contractSource`. Filename ограничен 255 UTF-8 bytes, code — 96 chars, location — 512, message — 512, contractSource — 160. Подробности ограничены 200 findings; остальные входят только в aggregate counts. Перед ответом весь JSONL envelope сериализуется с ceiling 900 KiB, оставляя запас transport limit 1 MiB; deterministic truncation не разрезает UTF-8 и добавляет `findings_truncated`. Evidence/warnings response envelope для job пусты. Report/error/details/logs не содержат file bytes, source path, stack, raw user strings, полные CSV/JSONL или extracted content. Поля `imported`, `readyForCalculation` и `calculationEligible` запрещены schema tests.

## UI / interaction contract

В «Диагностика» появляется отдельная поверхность «Проверка контракта R130SH». Постоянный текст:

> Проверка не импортирует данные в дело и не подтверждает пригодность результата для расчётов. Используется синтетический контрактный baseline; production exporter и M9a golden packages ещё не готовы.

Экран показывает file name без path, job state/phase/progress, cancel/retry, structural verdict, semantic coverage, package identity, outer SHA-256, findings и exact upstream commit. Clear вызывает `discard`. Polling использует последовательный recursive timeout, generation guard и останавливается при terminal/unmount/restart/cancel; late response игнорируется.

Status имеет polite live semantics, infrastructure errors — alert, progress — корректный progressbar. Focus не прыгает после polling/terminal response; cancel доступен во время job. Действия и длинные hashes переносятся на 640 px. Новый import navigation, DataGrid, preview, OCR и универсальный command/job framework не создаются. Поверхность наследует существующую дизайн-систему «Инженерный чертёж ЛИЦ ВВУ» и общий interaction vocabulary.

## Crash/failure/cancellation postconditions

- Dialog cancel/rejection не вызывают worker; Promise не отвергается.
- `start` быстро возвращает job snapshot; validation не блокирует dispatcher.
- Cancellation подтверждается terminal `cancelled`, а не локальным `Promise.race`.
- Timeout/cancel/source change закрывают descriptor/ZIP и не оставляют файлов.
- Process crash теряет только transient result; source/project не изменены.
- Любой report bounded/redacted; SQLite/OS exception и stack Renderer не получает.
- Project может быть открыт параллельно с job и остаётся неизменным; schema v1, metadata, audit, dossier и documents сохраняются byte/semantic-equivalent по применимому evidence.

## Downstream synthetic package builder

Так как M8/M9a отсутствуют, tests создают candidate в temp directory детерминированным downstream-owned builder, помеченным `downstream_synthetic_contract_fixture`. Builder использует snapshotted example payloads, добавляет минимальные syntactically valid uncovered payloads, вычисляет реальные decompressed size/SHA-256, строит согласованный manifest и fixed-metadata ZIP. Placeholder hashes/sizes upstream manifest не переиспользуются как фактические.

Builder не является R130SH exporter/golden evidence и не входит в production runtime. Один Python test-support owner используется Python tests, Electron E2E и packaged smoke через явный test harness; CI не требует R130SH checkout/network. Negative variants создаются программно: traversal/absolute/backslash/duplicate/case collision/symlink/encryption/unsupported compression/missing or duplicate manifest/malformed UTF-8/JSON/JSONL/inventory/size/hash/source change/cancellation/finding truncation/large streaming CSV.

## Tests

Python: snapshot hashes; strict manifest/source UUIDs; ZIP path NFC/casefold/legacy encoding/collisions/flags/methods/ZIP64/multidisk/EOCD/overlap/inventory/hash; duplicate JSON keys/non-finite/depth; bounded JSON/JSONL/CSV; cross-file identity; semantic coverage; report serialization/redaction/cap; source change; cancellation in every long phase; one active job; idempotent same-fingerprint start and different-fingerprint/budget conflict; get/cancel/discard; completion/cancel/source-change/shutdown races; 1 500 ms join/hard-kill outcome; project.close during job; worker restart; no Project/SQLite/audit mutation.

TypeScript: exhaustive operation/result/policy maps; upstream/local ID separation; progress invariants; findings/report schemas; no path/import/readiness fields; Main dialog cancel/reject/no worker call; public start не принимает budget, Main всегда внедряет `1 800 000`; internal schema отклоняет `999`, `1 800 001`, zero и non-integer; polling reducer/timer cleanup; error mapping.

Electron/Browser: deterministic candidate → observable progress → report/provenance; no import UI/project mutation; invalid ZIP/checksum mismatch; cancel; dialog cancel/reject; worker restart/app close; keyboard/focus/status/alert; clean console; desktop/640 px. Browser preview остаётся synthetic UI evidence, не доказывает worker/storage.

Packaged: worker onedir; WinUnpacked/Portable validation smoke with downstream synthetic candidate; production CSP; Electron fuses; no TCP/orphan worker/extracted temp tree. Полный gate: `pnpm verify -- --IncludePackaging` и `git diff --check`.

## Definition of Done

1. Exact upstream snapshot и drift test автономны и помечены synthetic/non-golden.
2. Coverage matrix честно различает structural, covered semantic и gaps.
3. Source IDs отделены от локальных UUID v4 entities.
4. Validator read-only, streaming, bounded, cancellation-aware и не извлекает ZIP.
5. One-job lifecycle typed, idempotent и отзывчив для get/cancel/discard.
6. Main/Preload/Renderer не раскрывают path; dialog outcomes typed.
7. Report не содержит import/readiness claim и остаётся меньше 1 MiB.
8. Project schema остаётся v1; M03A не добавляет/не запускает новую forward migration и не создаёт import/source table, receipt или project write; global job не создаёт второго persisted owner.
9. Diagnostics UI доступен с клавиатуры, корректен на 640 px и честно сообщает ограничения.
10. Owner-документы, четыре architecture maps, traceability и security/testing contracts синхронизированы без нового ADR/status layer: Python владеет execution/cancellation state, Renderer — только typed polling/read model, Main — dialog/path/process lifecycle; `packages/application` не получает premature second job machine.
11. Full local packaging gate и GitHub Quality зелёные; подтверждённые P1/P2 review закрыты.
12. M03A завершён и слит как PR #4 в baseline `d0708924e8c9f19ac64668571846bccb1d6e21fa`; дальнейший production import принадлежит M03B.
