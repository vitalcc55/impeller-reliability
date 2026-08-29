# M02.2B — Normative Sources and Case Documents

## Outcome contract

M02.2B добавляет управляемые документы аналитического дела без импорта R130SH и без извлечения инженерных значений:

```text
создать или открыть первый release-baseline *.irproj schema v1
→ зарегистрировать metadata-only документ либо выбрать локальный файл
→ сохранить неизменяемую managed copy внутри проекта
→ связать документ с моделями и образцами
→ проверить целостность и открыть только проверенную managed copy
→ изменить metadata/applicability с optimistic revision
→ архивировать/восстановить документ
→ закрыть и открыть проект повторно
→ получить те же metadata, links, file registry, warnings и audit
```

Успешный ответ файловой операции означает, что registry row и audit зафиксированы, managed file существует и совпадает по размеру и SHA-256. Ошибка не оставляет зарегистрированную ссылку на отсутствующий или непроверенный файл. Crash может оставить только незарегистрированный staging/final orphan, который не считается документом и обрабатывается узкой recovery-политикой после получения project lock.

## Product boundary и domain ownership

M02.2B создаёт только редактируемый `analyst_enrichment` entity `AnalystSourceDocument`, в UI — «Документ дела». Он регистрирует ТУ, индивидуальную или типовую ПМИ, требования и заявку заказчика, эксплуатационную документацию, нормативный документ, чертёж, документ о поверке/аттестации и иные материалы дела.

Слои не смешиваются:

- `r130sh_source` — будущие неизменяемые значения и файлы из `.r130run`;
- `analyst_enrichment` — CaseDocument, его metadata, applicability и локальная managed copy;
- `derived_analysis` — будущие immutable input/calculation snapshots.

Python worker единолично владеет project schema, registry, file validation, copy/hash, integrity verdict и audit. Main владеет системным file dialog и внешним открытием. Preload публикует только конкретные методы. Renderer владеет формой и локальным draft, но не путями, filesystem, SQLite, hash или MIME-истиной.

M02.2B не создаёт импорт `.r130run`, `TestCampaign`, source/enrichment resolution, normative value extraction, SourceReference/page/clause bindings, OCR, document parsing/preview, универсальную EAV/link/file модель, расчёты, FMEA, статистику, вибрацию, отчётность, `.irpkg`, installer, signing или network.

## Upstream R130SH baseline

Read-only baseline — `vitalcc55/R130SH`, branch `codex/data-and-protocol-improvements`, commit `f02f6d954246a5ab6f57d33dac724ce03d7fb841`.

- M0, M1, M2, M3, M4a и M5a завершены; следующий upstream-этап — M4b;
- frozen target examples `.r130run` v1 существуют, являются синтетическими и не считаются M9a golden packages;
- production exporter M8 `.r130run` ещё не реализован;
- M03A может начать contract/validation foundation после M02.2B по frozen examples;
- M03B production importer заблокирован до появления независимых R130SH M9a golden packages.

R130SH в этой ветке не изменяется, production-код не копируется, shared package/library dependency не создаётся.

## Первый release-baseline: project schema v1

До M02.2B публичного выпуска и пользовательских `.irproj` не было. Поэтому предрелизные schema v1/v2 из истории разработки не поддерживаются как legacy formats: первый реальный формат фиксируется одной чистой `PROJECT_SCHEMA_VERSION = 1`, которая сразу содержит Project metadata/audit, Customer/WheelModel/Specimen и CaseDocument. Migration ledger содержит одну запись `0001 create_project_database`. Dual-write, compatibility tables, source/import tables и aliases отсутствуют. Первая настоящая forward migration появится только после выпуска этого baseline.

### `case_documents`

```text
case_document_id TEXT PRIMARY KEY
document_kind TEXT NOT NULL
title TEXT NOT NULL
designation TEXT NOT NULL DEFAULT ''
revision_label TEXT NOT NULL DEFAULT ''
document_date TEXT NULL
issuer TEXT NOT NULL DEFAULT ''
notes TEXT NOT NULL DEFAULT ''
record_revision INTEGER NOT NULL CHECK (record_revision >= 1)
archived_at_utc TEXT NULL
created_at_utc TEXT NOT NULL
updated_at_utc TEXT NOT NULL
```

`case_document_id` — canonical RFC 4122 UUID v4, созданный Renderer до create-команды и используемый как idempotency key. `document_kind` принимает только:

```text
technical_specification
individual_test_method
typical_test_method
customer_requirement
test_request
operational_documentation
standard
drawing
measurement_or_attestation_record
other
```

`title` после trim обязателен. Optional strings нормализуются trim и хранятся как `''`. `document_date` при наличии — реальная календарная дата `YYYY-MM-DD`. Hard delete отсутствует. Новая фактическая редакция создаётся новым CaseDocument; замена файла и supersedes-chain отсутствуют.

### `case_document_files`

```text
case_document_id TEXT PRIMARY KEY REFERENCES case_documents(case_document_id)
original_file_name TEXT NOT NULL
stored_relative_path TEXT NOT NULL UNIQUE
media_type TEXT NOT NULL
size_bytes INTEGER NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 104857600)
sha256 TEXT NOT NULL UNIQUE
attached_at_utc TEXT NOT NULL
```

Metadata-only документ не имеет file row. PRIMARY KEY ограничивает документ одним файлом; UPDATE и DELETE блокируются schema triggers. `sha256` и `stored_relative_path` уникальны в проекте. Registry хранит только project-relative POSIX path точного вида:

```text
assets/documents/<case-document-id>/<sha256><normalized-extension>
```

Индексы project schema покрывают `document_kind`, `archived_at_utc`, `title`, `designation`, `sha256`, `wheel_model_id` и `specimen_id`.

### Applicability links

```text
case_document_wheel_models(case_document_id, wheel_model_id)
case_document_specimens(case_document_id, specimen_id)
```

Обе таблицы имеют composite PRIMARY KEY и точные foreign keys. Generic polymorphic link table не создаётся. Документ без links применим ко всему делу. Links могут включать существующие активные или архивные модели/образцы; архивирование любой стороны не удаляет историческую связь. Metadata и оба нормализованных набора links изменяются одной optimistic revision, одной `BEGIN IMMEDIATE` transaction и одним audit event.

## Initialization, published contract и evidence validation

Создание нового проекта выполняет:

```text
staging *.irproj
→ BEGIN IMMEDIATE
→ полная schema v1 + migration ledger + initial metadata/audit
→ COMMIT
→ quick_check
→ foreign_key_check
→ exact published schema v1 validation
→ semantic evidence validation
→ atomic rename контейнера
→ ProjectSession schema v1
```

Существующие предрелизные schema из прошлых этапов намеренно отклоняются; их автоматическое преобразование и backup не выполняются. Failed initialization полностью удаляет только operation-owned staging container. Repeated open schema v1 не создаёт новые objects/events. Newer schema отклоняется до write/backup.

Published v1 contract проверяет exact tables, indexes, triggers, foreign keys и migration ledger. Semantic validation проверяет canonical IDs, enum/date/timestamp/bounded text, relative path grammar, lowercase SHA-256, size/media type, link targets, immutable file row и полную document audit reconstruction. Физическое наличие/hash managed copies не входит в database evidence validation: missing/modified файл не превращает проект в `corrupt_project`.

## Managed file model

Renderer вызывает intent без пути. Main показывает системный file dialog и выполняет первичный extension/size/regular-file gate. Только Main передаёт approved absolute source path worker-у; этот путь не сохраняется, не возвращается Renderer, не входит в audit и не логируется.

Allowlist v1:

```text
.pdf .docx .xlsx .csv .json .txt .png .jpg .jpeg
```

Остальные расширения, включая executable/script/archive/web formats, отклоняются. Максимум — 100 MiB, минимум — один байт.

Worker повторно проверяет source как ordinary non-reparse file, size и lowercase-normalized extension. Базовый content gate ограничен:

- PDF — `%PDF-`;
- DOCX/XLSX — ZIP central directory без extraction, обязательные `[Content_Types].xml` и соответствующий `word/document.xml` либо `xl/workbook.xml`;
- PNG/JPEG — magic bytes;
- TXT/CSV/JSON — полное потоковое UTF-8 декодирование без бизнес-парсинга.

Antivirus, macro inspection, OCR, MIME framework, archive extraction и document parser отсутствуют.

### Copy/register algorithm

1. После повторного source gate worker создаёт operation-owned `.part` в `assets/documents/.staging/`.
2. Один streaming pass копирует bytes, считает SHA-256 и размер, проверяет deadline между chunks и завершает flush/fsync.
3. Worker открывает короткую `BEGIN IMMEDIATE`, повторно читает entity/file/revision state и классифицирует idempotent retry, conflict, duplicate content либо new attach.
4. Для новой managed copy создаётся exact destination directory, staging атомарно переименовывается в final path на том же volume.
5. После rename в той же DB transaction вставляется immutable file row, изменяется document revision где требуется, заменяются links для create/update и добавляется audit.
6. Deadline проверяется до commit. После commit cleanup зарегистрированного final path запрещён.
7. Обычная ошибка до commit откатывает SQLite и удаляет только operation-owned staging/final path. При неоднозначном transport timeout outcome определяется registry после следующего открытия/retry, а не success-shaped fallback.

Rename идёт до DB commit: поэтому commit никогда штатно не создаёт file row без готового final file. Crash между rename и commit может оставить только незарегистрированный orphan.

### Idempotency и duplicate content

- `create` с тем же UUID возвращает revision 1 только при полном совпадении normalized metadata/applicability и отсутствии file row; иначе `duplicate_entity`.
- `createWithFile` с тем же UUID, normalized initial snapshot и exact file snapshot возвращает существующую revision 1 без нового event; metadata-only запись с тем же UUID не превращается неявно в attach.
- SHA-256, уже зарегистрированный у другого CaseDocument, даёт `duplicate_document_content`; новый document/file row не создаётся.
- `attachFile` требует `expectedRevision`. Первый attach увеличивает revision и пишет event.
- Retry потерянного attach-response с прежней expectedRevision идемпотентен только при полном совпадении `sha256`, size, media type, normalized extension и original basename; возвращается текущая запись без новой revision/event.
- Иной файл, иной basename при уже существующем attachment либо попытка замены дают `file_already_attached`.

## Crash recovery и failure postconditions

Recovery запускается после database validation и получения project lock, чтобы не удалить staging активного владельца.

- exact regular `.part` entries в managed `.staging` удаляются;
- exact generated final paths, отсутствующие в `case_document_files`, удаляются как crash-orphans;
- пустые generated UUID directories могут быть удалены;
- неизвестные имена, reparse entries и paths вне exact managed grammar не удаляются рекурсивно и не считаются зарегистрированными документами;
- registered missing/modified files не удаляются и не исправляются автоматически.

Если `assets/documents` отсутствует, проект с registry rows открывается, документы получают `missing`, а ordinary root создаётся только перед новой file operation/recovery. Reparse/non-directory managed root не используется и даёт безопасную storage/integrity error; database evidence остаётся отдельно.

Failure classes:

- dialog cancel — `cancelled`, worker не вызывается;
- системный dialog не открылся — `storage_error`, Renderer Promise остаётся typed result и worker не вызывается;
- validation/signature/size — typed file error, без DB/file result;
- revision/duplicate/archive conflict — staging удалён, состояние не меняется;
- copy/hash/rename/SQLite/audit failure — rollback и operation-owned cleanup;
- worker crash/transport timeout — OS lock release, orphan recovery и idempotent reconcile/retry;
- external missing/modified bytes — ProjectSession остаётся открытой, registry/audit неизменны.

## Integrity status и resolve/open

Read-only status:

```text
not_attached
verified
missing
modified
verification_error
```

`get` и explicit `verifyFile` вычисляют статус конкретного документа с bounded streaming verification; list не хеширует весь каталог. Дешёвая presence-проверка всегда заново выполняет `lstat`, проверяет containment, ordinary/non-reparse type и размер. Ранее подтверждённый hash-статус используется только при совпадении registry snapshot и текущей физической сигнатуры `(size, mtime_ns)` с полной проверкой; удаление, замена, изменение размера или времени модификации немедленно инвалидируют `verified`. Verify не меняет revision и не пишет audit. Missing/modified не переписывает сохранённый hash, не удаляет файл и не считается корректным доказательным источником.

`resolveFile` принимает только `caseDocumentId`, читает relative path из Python-owned registry, проверяет POSIX grammar, containment внутри `assets/documents`, regular/non-reparse file, size и SHA-256. Только verified result возвращает абсолютный managed path внутреннему Main handler. Main вызывает `shell.openPath`; Renderer получает только typed opened/error result, никогда path.

## Completeness warnings

Warnings неблокирующие и не задают applicability, priority или общий процент готовности:

- `case_document_file_missing` — file row отсутствует либо зарегистрированный file отсутствует;
- `case_document_designation_missing` — пустое designation для `technical_specification`, `individual_test_method`, `typical_test_method`, `standard`;
- `case_document_revision_missing` — пустое revision label для тех же нормативных kinds.

Modified/verification_error отображаются отдельным integrity state, а не маскируются completeness warning. Конкретный будущий analysis сам проверит необходимый набор источников.

## Optimistic concurrency и audit

Create, createWithFile, update, attach, archive и restore используют существующую bounded deadline/transport policy. Existing-document mutations требуют `expectedRevision`; no-op update возвращает текущую запись без revision/event; conflict ничего не меняет. Update metadata + applicability + audit выполняется одной `BEGIN IMMEDIATE` transaction. Attach увеличивает document revision. Archive/restore увеличивают revision, не меняют/не удаляют file row и links. Archived document нельзя update/attach, но можно get/list/verify/open/restore.

Events:

```text
case_document.created
case_document.updated
case_document.file_attached
case_document.archived
case_document.restored
```

Create и createWithFile пишут один `case_document.created` с normalized initial metadata, applicability и optional file snapshot; createWithFile не создаёт фиктивный второй revision. Поздний attach пишет `file_attached` с `fromRevision/toRevision`.

Payload содержит `entityType`, `entityId`, `fromRevision`, `toRevision`, `changedFields`, before/after только изменённых metadata/applicability и file metadata (`originalFileName`, `mediaType`, `sizeBytes`, `sha256`, `storedRelativePath`). Absolute source path, content, extracted text и персональные данные из содержимого запрещены. Технические logs содержат operation/request/entity IDs и безопасные error codes, но не source/final absolute paths или original filename.

## IPC

Worker operations:

```text
caseDocument.create
caseDocument.createWithFile
caseDocument.list
caseDocument.get
caseDocument.update
caseDocument.attachFile
caseDocument.verifyFile
caseDocument.archive
caseDocument.restore
caseDocument.resolveFile
```

Renderer-facing preload API содержит те же операции, кроме internal `resolveFile`, вместо которого публикуется `openFile`. Renderer requests никогда не имеют `sourcePath`; Main-only worker payload для createWithFile/attachFile добавляет approved path после dialog/pre-gate.

List принимает `includeArchived` и optional `documentKind`, сортирует детерминированно по archive/title/designation/id. Summary не выполняет bulk hashing. Detail возвращает metadata, revision/archive timestamps, optional file metadata и integrity status, wheel/specimen IDs и warnings.

Typed errors синхронно добавляются в Python, Zod, Main mapping и UI:

```text
entity_not_found
entity_archived
revision_conflict
duplicate_entity
duplicate_document_content
file_already_attached
unsupported_file_type
file_too_large
file_missing
file_integrity_mismatch
validation_error
cancelled
timeout
storage_error
```

Dispatcher/capability/request/result maps исчерпывающие. SQLite/OS exception, stack trace и path Renderer не получает. File stateful operations terminate worker on indeterminate transport timeout; list/get/verify remain read-only. Cancellation после принятия worker operation не имитируется `Promise.race`: dialog cancellation — единственный user cancellation M02.2B, а accepted copy завершается typed outcome либо bounded timeout/reconcile.

## UI workflow

В «Сведения дела» появляется раздел «Документы дела» в существующем Operate/master-detail направлении. Отдельная таблица, nested-card mosaic, inline viewer и новый design token не создаются.

Экран содержит empty/loading/ready/error states, kind filter, archive toggle, короткий список и detail form:

```text
Вид документа
Название
Обозначение
Редакция
Дата документа
Организация/автор
Применимые модели
Применимые образцы
Примечание
```

Actions: создать metadata-only, создать с файлом, сохранить metadata/applicability, прикрепить файл один раз, проверить, открыть verified managed copy, архивировать/восстановить. Detail показывает revision, original filename, media type, size, SHA-256, integrity status и warnings. Длинные имена/hash переносятся; при 640 px master-detail становится одной колонкой и page horizontal overflow отсутствует. Keyboard order, visible focus, `aria-current`, selection state, labels и `role=status/alert` проверяются.

Manual backup называется «Создать резервную копию базы проекта». Рядом постоянно указано: копия содержит `project.sqlite` и не содержит `assets/documents`; полный перенос до `.irpkg` выполняется копированием закрытого каталога `*.irproj`.

## Shared dirty-draft lifecycle

CaseDocument расширяет один workspace-level draft-owner contract. Новый независимый modal state machine запрещён.

Owner хранит discriminated draft identity, expected revision, baseline, editable metadata, canonical sorted applicability IDs и raw input, если он нужен. Filters не являются draft. `dirty` всегда false при закрытом project. Pending transition замораживает исходную форму и все конкурирующие действия до keep/discard решения, поэтому сохранённый action/discard intent не может устареть из-за новой revision.

Один resolver защищает draft при:

- смене раздела;
- выборе/создании другого документа, модели или образца;
- изменении applicability selection;
- attach, archive/restore;
- закрытии/открытии проекта;
- controlled restart/window close;
- detached reattach/discard.

Accepted save/file operation регистрируется в общем pending-operation owner до IPC-вызова. Lifecycle drain ждёт все принятые operations, а не ref текущего размонтируемого child. Во время pending mutation переходы либо disabled, либо ставятся как typed intent; component unmount не теряет promise. При timeout draft остаётся, runtime становится unavailable/detached, success не показывается. Reattach сверяет identity/revision всех persisted dirty owners; local discard не меняет SQLite/assets.

## Backup semantics

Migration backup в первом baseline не создаётся, потому что поддерживаемого предыдущего формата нет. Механизм verified backup сохраняется для будущей первой послерелизной migration. Manual backup остаётся verified SQLite-only snapshot с SHA-256. Он не называется полной резервной копией дела. `.irpkg` и full backup archive в M02.2B не реализуются. Полный перенос требует закрыть проект и скопировать весь каталог `.irproj`.

## Threat model

Security controls M02.2B:

- Renderer не передаёт path и не получает filesystem authority;
- source path недоверенный и дважды проходит gate;
- allowlist исключает executable/script/archive/web formats;
- managed filename генерируется из canonical UUID/hash/extension, original basename — только metadata;
- containment, traversal, reparse и ordinary-file checks применяются к staging/final/resolve;
- Office ZIP только инспектируется без extraction;
- 100 MiB и bounded deadlines ограничивают resource consumption;
- immutable file row/hash предотвращают silent replacement;
- missing/modified fail closed как evidence, но не блокируют ProjectSession;
- logs/audit/Renderer не получают source path/content/extracted text;
- external open выполняется только после fresh verification;
- no HTTP/TCP/cloud/external fetch.

Активная malicious race другого процесса того же Windows-user после проверки остаётся вне существующей single-user threat model; custom VFS/ACL/antivirus не добавляются.

## Verification

### Python

- atomic initialization полной schema v1, отсутствие migration backup, rollback staging, repeated open, newer/corrupt schema;
- exact schema/index/FK/trigger and semantic evidence validation;
- metadata-only/createWithFile/attach once/idempotent retries/duplicate SHA/different file rejection;
- update/no-op/conflict, applicability links, archive/restore and audit reconstruction;
- extension/size/PDF/Office/image/UTF-8 gates and streaming SHA-256;
- deadline during copy/hash/verify, rename/SQLite/audit failure cleanup;
- crash staging/final orphan recovery contract;
- missing/modified while project remains open;
- invalidation cached `verified` после delete, same-size rewrite, reparse и close/reopen без повторного bulk SHA-256 неизменённых файлов;
- resolve containment/reparse and close/reopen persistence.

### TypeScript

- operation/request/result/error maps and exhaustive parsing;
- document kind/integrity/warning DTOs;
- Renderer commands reject `sourcePath` and all results/errors exclude source absolute path;
- Main dialog cancel/rejection/pre-gate and `shell.openPath` mapping; rejected dialog возвращает typed `storage_error` без worker request и rejected Renderer Promise;
- shared draft-owner typed intents, drain, detach and discard;
- retained projectId/entityId regression from M02.2A closure.

### Browser и Electron E2E

- Browser preview ready/unavailable: empty/list/detail/filter/archive/integrity/warnings, keyboard/focus, desktop/640 px, clean console;
- Electron: create/open project → wheel/specimen → normative metadata → deterministic Main-owned dialog seam attach → applicability → verify/open → update → archive/restore → close/reopen; реальное нативное окно Windows не автоматизируется;
- separate cancellation, unsupported/duplicate/metadata-only/missing/modified states;
- dirty document navigation/restart/window close/detached discard;
- accepted file operation drain and no path leak.

### Packaged и final gate

- worker onedir build/smoke;
- win-unpacked build/smoke;
- portable build/smoke;
- packaged create/reopen with dossier + managed document;
- production CSP, Electron fuses, no TCP, no orphan worker;
- `pnpm verify -- --IncludePackaging`;
- `git diff --check`.

GitHub Quality остаётся check/build/E2E без packaging; полный локальный gate — обязательное release evidence.

## Definition of Done

M02.2B завершён, когда:

- новый проект атомарно получает одну exact schema v1 со всеми Project/Dossier/CaseDocument objects, а неподдерживаемые предрелизные formats не получают compatibility path;
- metadata-only и managed-file CaseDocument сохраняются с immutable registry/hash, applicability, optimistic revision и доказательным audit;
- crash/failure/timeout не создаёт зарегистрированную ссылку на отсутствующий файл, orphan recovery узкая и проверенная;
- missing/modified document видим локально и не блокирует открытие `.irproj`;
- Renderer не получает source/managed absolute paths, Main не читает SQLite, worker остаётся единственным storage owner;
- один draft-owner защищает metadata/applicability и accepted file operations на всех lifecycle paths;
- SQLite-only backup назван честно, перенос полного дела описан как копирование закрытого `.irproj`;
- Browser, Electron, worker, packaged и полный локальный gates зелёные;
- существующие owner-документы согласованы с R130SH baseline `f02f6d9`, статусом M0/M1/M2/M3/M4a/M5a и порядком M02.2B → M03A → M03B;
- branch/PR/Quality/review closure выполнены без merge.

## Stop condition

После feature commit, push, PR, зелёного Quality и обработки подтверждённых P1/P2 работа останавливается. Merge выполняется только по отдельному решению владельца. M03A/M03B, TestCampaign, расчёты и иные запрещённые направления не начинаются.
