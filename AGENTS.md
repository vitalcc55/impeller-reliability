# AGENTS.md

## Repository context

Репозиторий развивается как локальная типизированная инженерная система на текущем стеке:

- Electron 43 + React 19 + TypeScript 6 strict — desktop shell и UI;
- sandboxed Preload — единственная renderer → Main boundary;
- Python 3.14 worker — предметная валидация, будущие расчёты и SQLite;
- UTF-8 JSONL stdin/stdout — process IPC без HTTP, TCP-порта и локального сервера;
- Browser dev preview — основная интерактивная поверхность разработки renderer;
- PyInstaller onedir внутри electron-builder portable — Windows x64 поставка.

M00–M01 реализуют walking skeleton; M02.1 добавляет `.irproj` и ProjectSession. M02.2A добавляет schema v2 и редактируемый analyst dossier: CustomerProfile, WheelModel и Specimen. TestCampaign, SourceDocument, РБД, РПТ, ПМН, FMEA, статистика, Марков, Монте-Карло, вибрация и импорт результатов R130SH не считаются реализованными до появления утверждённых контрактов и тестов.

## Scope

Эти правила действуют для всего репозитория. Локальные `AGENTS.md` добавляют только правила своего контура и не копируют корневой файл. `AGENTS.override.md` не используется. Временное состояние остаётся в ignored `.tmp/` и не превращается в versioned status/documentation layer.

## Repository-wide invariants

- Код, типы, сущности и имена модулей — на английском; Markdown — на русском, если внешний контракт не задаёт другой язык.
- TypeScript strict. `any`, unchecked casts, suppressions и silent success-shaped fallbacks не используются.
- Внешние данные принимаются как недоверенные и проходят runtime validation; ошибки typed, коррелированы и наблюдаемы.
- Python — единственный источник инженерной истины и единственный владелец SQLite. TypeScript не дублирует формулы.
- Renderer не знает Node, filesystem, SQLite, worker process или import internals.
- Один факт имеет одного owner. Parallel status, generated inventory, compatibility alias, legacy documentation и исторические architecture snapshots не создаются.
- Утверждённые внешние file schemas версионируются явно; несовместимость создаёт новую major schema, а не compatibility layer.
- Production/VM, secrets, permissions, Releases, signing и внешние настройки не изменяются без отдельного разрешения.

## Architectural boundaries

- `apps/desktop` владеет Electron Main, sandboxed Preload, React Renderer и DEV-only browser preview adapter.
- `packages/contracts` владеет Zod/TypeScript boundary types и не зависит от UI/runtime hosts.
- `packages/application` владеет orchestration, revisions, jobs и view models без инженерных формул.
- `packages/reporting` владеет adapter boundary immutable report snapshots; Electron printing остаётся в Main.
- `tools/python-worker` владеет protocol, domain/application, calculations, SQLite, migrations и R130SH integration.
- `schemas` и `fixtures` появляются только вместе с утверждённым контрактом или эталоном.

Точные направления и запреты принадлежат `docs/architecture/dependency-boundaries-rules.md`.

## Browser-first verification

- После существенного renderer-изменения запустить `pnpm dev:preview` и проверить через встроенный Browser:
  - `http://127.0.0.1:5173/?preview=ready`;
  - `http://127.0.0.1:5173/?preview=unavailable`.
- Проверять роли, landmarks, status messages, keyboard semantics, desktop и узкий viewport, затем консоль.
- Browser preview использует synthetic typed adapter, не запускает worker, не пишет SQLite и не заменяет Electron E2E или packaged smoke.
- Playwright является автоматическим gate, но не заменяет observable Browser-проверку.

## Impeccable contour

- Для нетривиальных UI/UX/layout/design-system задач использовать внешний skill `impeccable` из Codex home; локальную копию skill не хранить.
- `PRODUCT.md` владеет продуктовой истиной, `DESIGN.md` — развивающейся дизайн-системой.
- Визуальное направление пока открыто; инфраструктурный экран M01 не является утверждённым брендом.
- Новый system-level token/component/rule появляется только из подтверждённого решения или повторяемого применения и синхронизируется с `DESIGN.md` в том же change set.
- После UI-правок запускать Impeccable detector один раз на изменённых targets после Browser-прохода.

## Observability

- Operational logging — low-noise JSONL; stdout worker зарезервирован под protocol, stderr — diagnostics.
- Audit будущих предметных действий отделён от debug logs.
- Investigation/test artifacts находятся в ignored `.tmp/.codex/evidence/` и не являются документацией или source of truth.

## Instruction routing

Перед работой читать ближайший scoped-файл, если затронут его каталог:

- `apps/desktop/AGENTS.md` — Electron/Preload/Renderer, UI и preview;
- `packages/AGENTS.md` — TypeScript packages;
- `tools/python-worker/AGENTS.md` — Python/domain/SQLite;
- `schemas/AGENTS.md`, `fixtures/AGENTS.md` — schemas и fixtures;
- `scripts/AGENTS.md` — PowerShell control plane;
- `docs/AGENTS.md` — ownership документации;
- `docs/architecture/AGENTS.md` — четыре архитектурные карты.

## Canonical project surfaces

- Product context: `PRODUCT.md`.
- Design system and Impeccable rules: `DESIGN.md`.
- Current topology, boundaries, state and flows: `docs/architecture/*.md` по локальной карте ownership.
- Functional/non-functional requirements: `docs/requirements/`.
- Domain rules: `docs/domain/`.
- IPC/R130SH contracts: `docs/integration/`.
- Security/testing/packaging/observability: соответствующие профильные каталоги.
- Unresolved product/methodology choices: `docs/backlog/OPEN_QUESTIONS.md`.
- Current milestone plans and deferred roadmap: `docs/plans/`.
- Reasons for approved cross-cutting decisions: `docs/decisions/`.
- `README.md` — краткая публичная поверхность без дублирования рабочих команд и внутренних правил.

## Quick start and stable checks

- Install: `pnpm install --frozen-lockfile`; `uv sync --project tools/python-worker --frozen`.
- Desktop dev: `pnpm dev`.
- Browser renderer preview: `pnpm dev:preview`.
- Static/unit gate: `pnpm check`.
- Electron build/E2E: `pnpm build`; `pnpm test:e2e`.
- Worker artifact: `pnpm build:worker`; `pnpm smoke:worker`.
- Packaging gates: `pnpm package:win-unpacked`; `pnpm smoke:win-unpacked`; `pnpm package:portable`; `pnpm smoke:portable`.
- Full local gate: `pnpm verify -- --IncludePackaging`.

Retry, timeout inflation, `skip`, weak assertions и auth/integrity bypass не используются для маскировки failures.

## Editing and cleanup

- Сохранять один owner и ссылаться на него вместо копирования.
- Обновлять только реально затронутые docs/maps/contracts; архитектурное изменение без обновления карт требует явного объяснения.
- Не оставлять obsolete файлы, aliases или compatibility layers после переноса.
- Не коммитить `.tmp`, secrets, local evidence, build outputs или machine-specific absolute paths.
- Перед завершением проверить `git diff --check`, broken local references, relevant tests и отсутствие orphan processes.
