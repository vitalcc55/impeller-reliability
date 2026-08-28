# M02.2A — Analyst Dossier

> Нумерация schema в этом предрелизном плане заменена решением M02.2B: пользовательских данных и выпуска ещё не было, поэтому первый публикуемый формат создаётся как единая schema v1 без migration/compatibility с промежуточными v1/v2. Предметные и audit-инварианты M02.2A сохранены.

## Outcome contract

M02.2A добавляет редактируемые сведения аналитического дела без участия в управлении стендом:

```text
открыть существующий *.irproj
→ заполнить заказчика
→ создать модель рабочего колеса
→ зарегистрировать физический образец
→ изменить сведения с optimistic revision
→ архивировать/восстановить модель или образец
→ закрыть и открыть проект повторно
→ получить те же данные и доказательный audit
```

## Product boundary и ownership

`Project` — аналитическое дело Impeller Reliability, которое можно создать до или после независимого испытания R130SH. Приложение ничего не передаёт в R130SH и не владеет исполняемым планом стенда.

M02.2A создаёт только слой `analyst_enrichment`: `customer_profile`, `wheel_models` и `specimens`. Будущие `r130sh_source` и `derived_analysis` остаются отдельными владельцами. Таблицы импорта, автоматическое разрешение расхождений и универсальная EAV-модель не создаются.

## Published project schema baseline

В финальную первую schema v1 входят:

- одну optional-карточку `customer_profile` на проект;
- каталог `wheel_models` с UUID v4, optional инженерными характеристиками, revision и archive timestamp;
- каталог `specimens` с UUID v4, обязательной моделью, уникальным идентификационным номером в пределах модели, revision и archive timestamp;
- UUID v4 новой модели или образца создаётся до отправки create-команды и является её idempotency key: безопасный повтор не создаёт вторую сущность;
- индексы по архивному состоянию, названию/обозначению модели, связи specimen → model и идентификационному номеру.

Поскольку предыдущий формат не выпускался и пользовательских данных нет, dossier tables создаются сразу в полном `0001 create_project_database`. Промежуточные `0001/0002` не принимаются как legacy input; dual-write, compatibility tables и backup фиктивного predecessor отсутствуют. Future forward migration начинается от выпущенной schema v1.

## Domain rules

- обязательные строки нормализуются trim; optional-строки хранятся как `''`;
- diameter хранится как положительная canonical decimal string;
- RPM и blade count при наличии — положительные integers;
- даты при наличии — календарно корректные `YYYY-MM-DD`;
- никакие BV/G/support/material defaults и предел стенда 5000 RPM не применяются автоматически;
- archived model нельзя назначить образцу или восстановить вместе с образцом;
- model с активными specimens нельзя архивировать;
- update/archive/restore используют `expectedRevision`; no-op не меняет revision и не пишет audit;
- mutation и audit выполняются одной `BEGIN IMMEDIATE` transaction.

## Incomplete-data policy

Неполное дело сохраняется. Python возвращает неблокирующие warnings рядом с соответствующей сущностью: `customer_address_missing`, `wheel_nominal_diameter_missing`, `wheel_nominal_speed_missing`, `specimen_working_diameter_missing`. Общий процент готовности не вычисляется; конкретный расчёт позднее проверит собственные обязательные входы.

## Audit

События `customer_profile.*`, `wheel_model.*` и `specimen.*` содержат entity type/id, фактические revision, только реально изменённые поля и `before`/`after`; create содержит нормализованный initial snapshot. Адреса, материалы и состояние образца не попадают в технические JSONL-логи.

## IPC

Operation-specific Zod/Pydantic contracts:

```text
caseCustomer.get / caseCustomer.upsert
wheelModel.create / list / get / update / archive / restore
specimen.create / list / get / update / archive / restore
```

List имеет `includeArchived` и детерминированную сортировку. Detail DTO возвращает `recordRevision` и completeness warnings. Ошибки: `entity_not_found`, `entity_archived`, `entity_in_use`, `duplicate_entity`, `revision_conflict`, `validation_error`. Записывающие операции используют существующую bounded domain/transport timeout policy; create retry с тем же entity UUID возвращает исходную revision 1 только при полном совпадении initial snapshot. Read operations проверяют domain deadline до и после SQLite query; dispatcher исчерпывающий.

## Archive policy

Архивирование обратимо и не удаляет строку. Архивная сущность видима только при явном `includeArchived`; restore требует актуальную revision и все текущие инварианты. Hard delete в M02.2A отсутствует.

## UI и dirty drafts

В открытом деле доступны только `Обзор` и `Сведения дела`: `Заказчик`, `Модели колёс`, `Образцы`. Короткие списки не используют TanStack Table. Каждый раздел имеет empty/loading/error/ready состояния, форму, revision, warnings и архивные действия.

Один общий draft-owner contract защищает Project metadata, Customer, WheelModel и Specimen при навигации, выборе сущности, смене проекта, restart worker, закрытии окна и detached/discard. Четыре независимых модальных автомата не создаются.

## Verification

- Python: atomic full-schema create, CRUD, no-op/conflict, archive invariants, canonical values, warnings, transactional audit, persistence и timeouts;
- TypeScript: Zod/Pydantic-equivalent operation maps, response parsing, errors и общий draft owner;
- Electron E2E: customer → model → specimen → update/archive/restore → close/reopen, а также dirty lifecycle;
- Browser QA: desktop/640 px, keyboard/focus, empty/warning/error states и clean console;
- packaged scenario: dossier persistence через bundled worker;
- полный gate: `pnpm verify -- --IncludePackaging` и `git diff --check`.

## Closure pass

- create модели и образца идемпотентен по заранее созданному UUID v4; singleton Customer и revision-based mutations сохраняют собственные существующие гарантии;
- TypeScript и Python одинаково принимают только bounded decimal без exponent notation, календарную дату и canonical UUID v4;
- read-only preflight учитывает непустой WAL после аварийного commit, но не создаёт WAL для чисто закрытой отклонённой базы;
- evidence validation запрещает пустую audit revision, неверный тип archive/restore transition и активный образец под архивной моделью;
- один draft guard применяется также к archive/restore и выбору модели нового образца; быстрый выбор записей защищён от устаревшего ответа revision token;
- read operations проверяют deadline, а packaged smoke после reopen сверяет значения и связи, не только количество строк.

## Definition of Done

Этап завершён, когда Analyst Dossier входит в первый atomic schema v1 baseline, сохраняется после повторного открытия, все изменения дают доказательный audit, общий draft guard не теряет ввод, локальный и GitHub gates зелёные, а owner-документы согласованы с downstream-only моделью R130SH.

## Stop condition

Эта stop condition была выполнена после PR #2. Текущая последующая работа ограничена отдельным outcome contract M02.2B; TestCampaign, импорт `.r130run`, source bindings, расчёты, FMEA, статистика, вибрация, отчётность, installer и signing по-прежнему не начинаются.
