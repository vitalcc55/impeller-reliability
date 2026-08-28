# M02.1 — Project Container and Session

> Контейнерные инварианты остаются актуальны. До первого выпуска schema v1 была расширена M02.2A/M02.2B dossier и CaseDocument tables в том же atomic `0001`; невыпущенные промежуточные варианты не являются поддерживаемыми форматами.

## Outcome contract

M02.1 создаёт первый сохраняемый продуктовый поток:

```text
создать каталог *.irproj
→ открыть и удерживать единственную ProjectSession
→ прочитать ProjectOverview
→ изменить метаданные с optimistic revision
→ записать append-only audit в той же транзакции
→ закрыть
→ открыть повторно
→ получить те же данные
```

`health.sqlite` остаётся app-level диагностической базой M01.1. Она не переименовывается, не мигрирует в проект и не получает доменные таблицы.

## Scope и граница этапа

В этап входят контейнер, manifest, `project.sqlite`, schema v1, forward-only migrator, backup, Windows OS-held lock, одна активная сессия, метаданные проекта, audit, recent paths в Main, start page, project overview и отдельная диагностика.

Не входят Customer, WheelModel, Specimen, TestCampaign, SourceDocument, фотографии, импорт/экспорт, расчёты, отчётность, `.irpkg`, installer, signing, cloud, server и network. Каталоги будущих функций не создаются заранее.

## Контейнер

```text
ProjectName.irproj/
├── project-manifest.json
├── project.sqlite
├── .project.lock
├── assets/
│   └── documents/
└── backups/
```

`.project.lock` — служебный файл с JSON-метаданными владельца; исключительность обеспечивает не его содержимое, а удерживаемая Windows-блокировка. Файл не является источником истины проекта.

Создание выполняется в sibling staging-каталоге `<final-name>.creating-<uuid>` на том же томе. Только после manifest, migration `0001`, integrity checks и закрытия временного соединения staging атомарно переименовывается в итоговый `.irproj`. При ошибке итогового каталога не существует, staging удаляется как незавершённый результат текущей операции.

## Manifest v1

`project-manifest.json` содержит только идентичность контейнера и совместимость:

```json
{
  "schemaVersion": "impeller.project-container.v1",
  "projectId": "UUID",
  "createdAtUtc": "2026-08-25T15:00:00Z",
  "createdWithApplicationVersion": "0.1.0",
  "databaseFile": "project.sqlite"
}
```

JSON — UTF-8 без BOM, неизвестные поля отклоняются. Изменяемые название, номер, описание и статус существуют только в SQLite. `projectId` дублируется намеренно и сверяется при каждом открытии.

## SQLite schema v1

Database invariants:

- `application_id = 0x49525043` (`IRPC`);
- `user_version = 1`;
- `foreign_keys = ON`;
- `journal_mode = WAL`;
- `synchronous = FULL`;
- `busy_timeout = 5000`;
- один последовательный writer внутри активной ProjectSession.

Первая опубликованная project schema — v1. `user_version=0` не является внешним форматом и не мигрируется при открытии existing container; будущие forward migrations начинаются только с подтверждённой v1.

Все persisted и IPC timestamps M02.1 имеют один канонический UTC-формат `YYYY-MM-DDTHH:mm:ss.sssZ`. Python проверяет календарную корректность manifest, migration ledger, metadata и audit evidence до ProjectSession; TypeScript повторно валидирует тот же формат на desktop boundary. Renderer не получает невалидную дату для форматирования.

### `schema_migrations`

```text
version INTEGER PRIMARY KEY
name TEXT NOT NULL UNIQUE
applied_at_utc TEXT NOT NULL
```

### `project_metadata`

```text
project_id TEXT PRIMARY KEY
name TEXT NOT NULL
project_number TEXT NOT NULL
description TEXT NOT NULL
status TEXT NOT NULL CHECK status IN ('draft', 'active', 'completed', 'archived')
record_revision INTEGER NOT NULL CHECK record_revision >= 1
created_at_utc TEXT NOT NULL
updated_at_utc TEXT NOT NULL
created_with_application_version TEXT NOT NULL
```

Таблица содержит ровно одну строку. Пустое после trim название запрещено; длины полей ограничиваются contract/domain validation. `record_revision` начинается с `1` и увеличивается только успешной командой update.

### `project_audit_events`

```text
sequence INTEGER PRIMARY KEY AUTOINCREMENT
event_id TEXT NOT NULL UNIQUE
event_type TEXT NOT NULL
occurred_at_utc TEXT NOT NULL
actor_kind TEXT NOT NULL CHECK actor_kind IN ('application', 'user')
payload_json TEXT NOT NULL
```

`project.created` и `project.metadata_updated` являются событиями M02.1. Payload сериализуется канонически. UPDATE/DELETE блокируются триггерами. Изменение metadata и вставка audit выполняются одной `BEGIN IMMEDIATE` транзакцией.

## Lock и ProjectSession

После read-only manifest/SQLite validation Python открывает обычный `.project.lock` и удерживает через `msvcrt.locking(..., LK_NBLCK, 1)` эксклюзивную блокировку одного байта. В файл записываются диагностические поля `projectId`, `applicationInstanceId`, `pid`, `startedAtUtc`, `host`; PID не участвует в решении о владении.

Один worker владеет максимум одной ProjectSession. Повторный `project.open/create` при активной сессии отклоняется. Main передаёт JSONL-запросы последовательно: один запрос выполняется и максимум один ожидает dispatch; следующий получает observable busy failure. Domain/transport deadline начинается только при фактической отправке текущей операции. Controlled restart и shutdown сначала прекращают приём новых запросов и дренируют bounded-очередь, затем отправляют `system.shutdown`; отдельный exit timeout относится только к завершению process после ответа. Принудительное завершение остаётся для protocol failure или operation transport timeout. `project.close`, graceful shutdown и аварийное завершение процесса освобождают OS lock; отдельный process test доказывает освобождение после crash.

## Открытие, миграции и backup

Последовательность worker:

1. проверить абсолютный путь, расширение и сам каталог `.irproj`;
2. проверить `project-manifest.json` как отдельный regular file, ограничить чтение 4 KiB и только затем разобрать strict manifest;
3. проверить topology зарезервированных `project.sqlite`, optional `.project.lock`, `backups/` и SQLite sidecars; symlink/junction/reparse и hard-linked files запрещены;
4. открыть `project.sqlite` через immutable read-only URI для чисто закрытой базы либо через обычный read-only URI при наличии непустого WAL после аварийного завершения; проверить `application_id`, `user_version`, точный version-specific schema contract, migration ledger, `projectId` и согласованную цепочку metadata/audit;
5. захватить OS lock;
6. открыть подтверждённый SQLite в `mode=rw` и применить WAL/FK/FULL/busy timeout;
7. при `current < supported` создать SQLite Backup API snapshot в проверенном `backups/`, выполнить его `quick_check`, затем применить forward-only migrations;
8. выполнить `quick_check`, `foreign_key_check` и повторные structural/semantic проверки schema v1;
9. получить канонический `ProjectOverview` и только затем назначить активную ProjectSession.

При неверном формате или `current > supported` ошибка возвращается до WAL и backup. Неудачная migration откатывается, исходная база остаётся доступной, проверенный migration backup сохраняется. Ручной backup считается завершённым только после `quick_check`, SHA-256 и final deadline; новый незавершённый файл удаляется при ошибке. Отдельный `ProjectMigrator` не использует `check_storage()`.

## IPC и ownership путей

Renderer → Preload → Main:

```text
project.create(draft)
project.open()
project.close()
project.getOverview()
project.updateMetadata(command)
project.createBackup()
project.listRecent()
```

`create` и `open` не принимают путь от Renderer: Main показывает Windows dialog, проверяет выбранный путь и только затем вызывает worker. Recent paths хранятся атомарным JSON app-level read model в Main; Renderer получает только список разрешённых ранее контейнеров.

Main → worker:

```text
project.create        { path, applicationInstanceId, applicationVersion, draft }
project.open          { path, applicationInstanceId }
project.close         {}
project.getOverview   {}
project.updateMetadata { expectedRevision, patch }
project.createBackup  {}
```

Все payload/result имеют отдельные Zod/Pydantic-схемы. После открытия операции используют активную ProjectSession и не принимают путь. Worker error codes расширяются значениями `project_locked`, `corrupt_project`, `incompatible_schema`, `revision_conflict`; отмена системного диалога возвращается Renderer как typed `cancelled`, а не exception-shaped неизвестная ошибка.

## UI и визуальный источник

Первый продуктовый shell строится в режиме Operate. Подтверждённый визуальный авторитет — сайт ЛИЦ ВВУ `http://modelsss.wingrwz.beget.tech/`:

- локальная копия официального SVG-логотипа `logo_lic_vvu.svg`;
- тёмно-синий `#102133/#13202d`, сигнальный янтарный `#e49a2f`, холодный акцент `#5fb3c6`;
- светлый инженерный canvas `#eef3f3` с тонкой координатной сеткой;
- белые рабочие поверхности, выраженные заголовки, тонкие границы и функциональные радиусы;
- локальный Golos Text под SIL OFL 1.1 и официальный SVG загружаются только из bundled renderer; внешних CDN и runtime network нет.

Desktop shell содержит start page с Create/Open/Recent/Diagnostics и открытый project overview с полями name, project number, description, status, path, revision и явным состоянием сохранения. M01.1 health переносится в Diagnostics. Пункты будущих модулей отсутствуют. На ширине 640 px навигация и форма остаются клавиатурно доступными без горизонтального overflow.

## Failure model

- `cancelled` — пользователь отменил системный диалог; состояние не меняется;
- `project_locked` — OS lock уже удерживается;
- `corrupt_project` — manifest/SQLite/integrity/projectId не согласованы;
- `incompatible_schema` — версия базы новее поддерживаемой;
- `revision_conflict` — expected revision не равна сохранённой;
- `operation_in_progress` — bounded transport не принял ещё одну операцию до завершения текущей очереди;
- `storage_error` — атомарное создание, backup, migration или запись не завершены;
- worker crash переводит runtime в unavailable; OS lock освобождается процессом Windows.

## Verification

- Python unit/integration: manifest, schema, atomic create, CRUD/revision, immutable audit, migrator/backup/rollback, corruption/newer schema, process lock contention и crash release;
- TypeScript/Vitest: operation maps, Renderer-facing typed result, recent-project persistence и validation;
- Electron E2E: create/update/close/reopen, cancelled dialog boundary, diagnostics и preload isolation;
- packaged smoke: create/update/close/reopen через bundled worker, CSP/fuses, exact process tree, no TCP, no orphan worker;
- Browser QA: start/project/diagnostics, ready/error states, desktop и 640 px, keyboard/focus/console;
- `pnpm check`, `pnpm build`, `pnpm test:e2e`, worker build/smoke, `win-unpacked` build/smoke, portable build/smoke, `git diff --check`.

GitHub quality workflow намеренно ограничен `check`, production build и E2E. PyInstaller и Electron packaging остаются обязательным локальным gate; отдельный manual/release workflow добавляется ближе к производственной поставке.

## Post-review findings

Review closure устраняет пять рисков до M02.2 без расширения предметной области:

1. Dispatcher обрабатывает `project.createBackup` только явной веткой и завершается статически проверяемой exhaustive-защитой.
2. Каждая worker operation получает отдельные domain deadline и больший transport timeout. Stateful timeout до commit не оставляет ProjectSession; transport timeout завершает worker, поэтому скрытая активная сессия и удерживаемый lock невозможны. SQLite backup проверяет deadline через progress callback и удаляет незавершённый файл.
3. Ошибка конфигурации SQLite закрывает уже созданное соединение при любом исключении.
4. `project.created` атомарно сохраняет фактические нормализованные metadata. `project.metadata_updated` перечисляет только реально изменённые поля с `before`/`after`; no-op не создаёт ревизию или audit event.
5. Несохранённый draft защищён при закрытии проекта, приложения и controlled restart. Неожиданная потеря worker переводит проект в detached-состояние без размонтирования формы; повторное присоединение допустимо только при совпадении `projectId` и `recordRevision`.

Права на официальный SVG подтверждены владельцем проекта 2026-08-26; внутреннее подтверждение хранится вне публичного репозитория. Права на Etelka не подтверждены, поэтому файлы удаляются текущим review-fix commit без переписывания опубликованной Git-истории. Заголовочный шрифт заменяется официальным Golos Text из Google Fonts под SIL Open Font License 1.1; лицензия поставляется рядом с приложением и описана в `THIRD_PARTY_ASSETS.md`.

Review closure считается завершённым после Python timeout/audit/connection tests, Electron E2E для dirty restart и window close, Browser QA desktop/640 px, Impeccable detector, полного локального packaging gate, review-fix commit, PR и зелёного Quality workflow. PR не сливается без отдельного решения владельца.

## Final PR review closure

Финальный контракт M02.1 сводится к пяти инвариантам: bounded validation недоверенного контейнера; один Python-owned SQLite writer под OS lock; атомарные metadata/audit и forward migrations с backup; последовательные worker operations с deadline; Renderer draft, который не теряется молча. Electron Main владеет одним автоматом закрытия `idle → waiting-for-decision → approved`; уже принятое сохранение завершается до решения, а недоступный Renderer не блокирует выход.

Исправления публикуются отдельным commit, каждый review thread получает ссылку на commit и regression test и разрешается только после push. Затем запрашивается повторный Codex review. Squash merge допустим только при отсутствии открытых P1/P2, зелёных Quality/package gates, совпадающем SHA и конечном дереве без Etelka; M02.2 в этом цикле не начинается.

## Stop condition

После подтверждённого create → update → close → reopen, полного gate и синхронизации затронутых owner-документов M02.1 останавливается. M02.2 и предметные расчёты не начинаются.
