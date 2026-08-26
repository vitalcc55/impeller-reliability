# M01.1 — closure and hardening walking skeleton

## Outcome contract

M01.1 укрепляет существующую границу Renderer → Preload → Main → Python worker → SQLite перед появлением проектных и расчётных операций. Этап не создаёт `.irproj`, предметные сущности, расчёты или обмен R130SH.

Наблюдаемый результат: сбой и перезапуск worker отражаются в UI без ложного `ready`, IPC типизирован по операциям и ревизия ответа проверяется, storage health требует WAL, packaged renderer не разрешает Vite WebSocket, production fuses и smoke проверяются по фактическому артефакту и конкретному дереву процессов, а Windows CI воспроизводит автоматические проверки.

## Scope

1. Operation-specific TypeScript/Zod и Python/Pydantic IPC-контракты без `Record<string, unknown>` для известных операций.
2. `revision` в response envelope и реальное применение `RevisionGate`.
3. Явный exhaustive dispatch `system.shutdown`.
4. Lifecycle `starting → ready → unavailable → stopping → stopped`, контролируемый restart без второго worker.
5. Самостоятельные UI-состояния и обработка rejected `getStatus`/`ping`.
6. WAL как часть положительного storage-health verdict.
7. Раздельная development/production CSP.
8. Production Electron fuses и afterPack-проверка их фактического состояния.
9. Smoke без hardcoded application version и с проверкой process tree конкретного запуска.
10. Windows GitHub Actions quality workflow.
11. Шесть минимальных ADR по принятым системным решениям.
12. Краткий технический README при неизменной proprietary-модели распространения.
13. Согласование platform/author/copyright: desktop-продукт, владелец — Власов Виталий Андреевич, сведения о посторонних организациях отсутствуют.

## Out of scope

Отложенные намерения и порядок их реализации принадлежат `POST_M01_1_ROADMAP.md`. В M01.1 запрещены project storage, РБД/РПТ/ПМН, импорт результатов R130SH, FMEA, статистика, Марков, Монте-Карло, вибрация и конечная отчётность.

## Decisions

- Response возвращает ту же `revision`, что request; Main дополнительно связывает ответ с pending request и отклоняет несовпадение.
- Известные операции образуют единый typed operation map на TypeScript-границе и exhaustive registry на Python-границе.
- Restart — явная контролируемая операция Main: старый процесс должен завершиться до запуска нового; бесконечного автоматического restart нет.
- Production CSP задаётся packaged HTML и содержит `connect-src 'none'`; development preview получает отдельную CSP во время Vite-конфигурации.
- Fuses применяются к packaged executable и проверяются после упаковки; документация не утверждает свойства, которые не подтверждены артефактом.
- Smoke выбирает артефакт из package metadata и принимает только один точный кандидат; сеть и orphan проверяются в дереве запущенного экземпляра.
- ADR фиксируют причины шести действующих решений и не образуют параллельный status/history слой.

## Execution order

1. Контракты IPC, revision и exhaustive dispatcher.
2. Worker lifecycle/restart и UI error states.
3. WAL verdict, CSP и Electron fuses.
4. Packaging/smoke hardening и Windows CI.
5. ADR, README, product/ownership и синхронизация owner-документов.
6. Полный verification gate и Browser-проверка.

## Definition of Done

- `pnpm check`
- `pnpm build`
- `pnpm test:e2e`
- `pnpm build:worker` и `pnpm smoke:worker`
- `pnpm package:win-unpacked` и `pnpm smoke:win-unpacked`
- `pnpm package:portable` и `pnpm smoke:portable`
- `git diff --check`
- worker crash видим в Renderer; restart не создаёт второй worker
- response revision проверяется и stale response не применяется
- storage health не сообщает `ok` без WAL
- production CSP не разрешает WebSocket/сеть
- packaged Electron fuses проверены
- smoke не зависит от номера версии и не затрагивает чужие процессы
- нет TCP listener и orphan worker
- GitHub Actions workflow валиден; внешний зелёный status check проверяется после публикации отдельно
- документация соответствует коду и не дублирует владельцев фактов

## Stop condition

После подтверждения M01.1 работа останавливается. Переход к M02 требует отдельной задачи.
