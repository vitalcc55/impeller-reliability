# Аудит и упрощение тестового контура

## Контракт результата

- Outcome: компактный тестовый контур с меньшим временем выполнения и потреблением ресурсов; существенные предметные, persistence, IPC, security и packaged-runtime гарантии сохранены.
- Verification surface: исходный и итоговый `pnpm verify -- --IncludePackaging` на одной машине; между ними только узкие проверки изменённых владельцев. Логи и измерения находятся в ignored `.tmp/.codex/evidence/test-contour/`.
- Invariants: единственный имплементёр — главный агент; восемь субагентов выполняют только чтение и review. Нет тестов самого тестового контура, новых production-зависимостей, ослабления assertions, coverage gate, дедлайнов, retries или скрытых пропусков. Не меняются инженерные формулы, публичные контракты, persisted data и пользовательское поведение.
- Scope: Python unit/integration и fixtures, TypeScript unit/renderer/process tests, Electron E2E, packaged smoke, test/build orchestration, CI и непосредственно затронутые инструкции.
- Non-goals: развитие продукта, обновление стека, production/deploy/publish, массовый косметический рефакторинг, создание новой инфраструктуры ради проверки инфраструктуры.
- Blocked condition: требование удалить единственное доказательство критического контракта либо необходимость продуктового решения. Обычные обратимые технические решения принимаются самостоятельно.

Этот файл — единственный журнал ветки, включая состояние для возобновления. Сырые логи не копируются в документацию. Названия сущностей определяются ответственностью, а не номером этапа.

## Исходная точка

- Ветка: `codex/test-contour-refactoring`, создана от `4c4428368a2bb8f3049e49cf44ab7c1bf2153c77` (`main`). Исходное рабочее дерево чистое.
- Сразу после создания ветки запущен единственный исходный `pnpm verify -- --IncludePackaging`, без предварительных изменений кода или конфигурации.
- Лог: `.tmp/.codex/evidence/test-contour/baseline-verify.log`; итог и wall-clock: `baseline-verify.json` в том же каталоге.
- Результат: SUCCESS, exit code 0; 23 октября 2026, 01:25:27–01:37:04 МСК; **696,644 с** (11 мин 36,6 с).
- Машина: Windows x64, 12 logical CPU, 15,7 GiB RAM. Исходный запуск без конкурирующих тестов/сборок/субагентов; прочие процессы пользователя не останавливались.
- Повторять полный verify до завершения реализации и review запрещено постановкой задачи.

| Поверхность | Исходное подтверждение |
| --- | --- |
| TypeScript | 21 файл, 230 passed; Vitest 20,06 с; aggregate environment 100,13 с, import 24,39 с, tests 14,11 с |
| Python | 836 passed за 42,65 с; branch coverage 86,31%, исходный порог 85% |
| Electron E2E | 21 passed, 127,296 с; skipped/flaky/unexpected = 0 |
| Поставки | WinUnpacked и Portable smoke: passed, persistence/import/РБД/РПТ/ПМН/materials = true |
| Детерминированная работа | 3 Electron builds, 2 clean PyInstaller builds, 2 unpacked packaging прохода, 1 portable compression |
| Worker artifact | 217 файлов, 89,49 MiB |

`environment` — сумма измерений окружений разных Vitest workers, а не CPU time или wall-clock полного запуска. Отдельный снимок portable-сжатия показал 720 MiB working set и 136,6 с накопленного CPU у `7za.exe`; это не peak и не измерение всего контура. Снимок Python-этапа: 14 процессов собственного дерева, 703 MiB суммарного working set. Эффект кешей и фоновой нагрузки учитывается как ограничение сравнения одного исходного и одного итогового запуска.

Исходные подробные отчёты сохранены как `baseline-python-coverage.xml` и `baseline-playwright.json` рядом с логом, чтобы целевые проверки их не перезаписали.

## Процесс

1. Завершить и зафиксировать исходную проверку.
2. Параллельный аудит восьмью read-only субагентами; до получения всех design-relevant результатов код не изменять.
3. Проверить предложения по исходникам и логам, назначить каждому существенному инварианту минимальную достаточную проверку.
4. Удалить подтверждённые дубли/малозначимые проверки, оптимизировать setup, процессы и сборку; выполнять только целевые проверки.
5. Итеративный review тем же пулом, проверка каждого finding главным агентом и исправление подтверждённого.
6. После завершения review выполнить итоговый полный verify, сравнить с исходным, проверить diff и зафиксировать результат коммитом.

## Решения и подтверждения

После завершения исходной проверки до начала реализации запущен пул из восьми read-only исследователей:

| Исследователь | Ответственность |
| --- | --- |
| python_domain_tests | Python domain/calculations/protocol unit |
| python_persistence_tests | SQLite/dossier/documents/import integration |
| python_import_tests | ZIP/validation/materials/jobs/process/fixtures |
| main_process_tests | Main/process/timers/open/consent unit |
| renderer_tests | Renderer/preview/draft/reconciliation unit |
| contract_tests | Shared contracts/application boundaries |
| electron_scenarios | GUI scenarios, meaningful cross-layer coverage |
| verification_orchestration | Verify/build/package/smoke/CI/config/dependencies |

Каждое предложение удаления проверяется по сохраняемому инварианту и существующему доказательству у canonical owner. Низкая стоимость сама по себе не делает тест бесполезным; большой test count сам по себе не является дефектом. Кандидаты на оптимизацию окружений/повторных сборок пока являются гипотезами, до синтеза всего пула решение не принято.

Prediction: сокращение повторных сборок и ненужных DOM-окружений уменьшит детерминированный объём работы. Validation: исходники orchestration, целевые команды и итоговые логи; сохранены обе реальные формы поставки. Rollback/remove condition: выявленный stale artifact, потеря изоляции, утечка процесса или утрата единственного доказательства значимого инварианта требует пересмотра оптимизации.

## Текущее состояние

### Синтез аудита и implementation contract

Все восемь направлений завершены; главный агент проверил конкретные исходники, границы и начальные условия кандидатов.

Принято:

- Node — обычная среда Vitest; только два React DOM-файла явно используют happy-dom. Ограничить параллелизм четырьмя workers, сохранив изоляцию и существующие дедлайны.
- В полном verify одна свежая сборка Electron и worker; Portable создаётся из только что проверенного WinUnpacked через штатный `--prepackaged`. Оба smoke и compression без изменений; standalone packaging сохраняет свежую сборку. [CLI electron-builder](https://www.electron.build/v26/docs/cli/).
- Исключить build-only mypy/Hypothesis и необязательные pytest plugins из PyInstaller graph; убрать неиспользуемую Hypothesis dev-зависимость. Runtime NumPy/SciPy и остальные версии сохраняются.
- В packaged smoke — обязательные process/TCP snapshots и один unfiltered TCP запрос на sample с фильтрацией собственных PID в памяти. Прямой `Stop` на filtered `-OwningProcess` отклонён: штатное отсутствие сокетов даёт not-found error. Ошибки наблюдения не должны выдавать ложное подтверждение no TCP/no orphan.
- Удалить сценарии, тестирующие фиксированные DEV-ответы preview, и tautology с самостоятельно созданным массивом. Сохранить компактные ready/unavailable, полезные DOM/draft/reconciliation проверки и runtime-boundary validation.
- Заменить повторную 70-opening unit-серию точной live-owner проверкой перехода 63 → 64 → consent/release → последующие открытия; полноценные 64 настоящих открытия сохраняются в Electron E2E. Проверку queue capacity перенести в существующий real-process deadline case; нормализацию rejection оставить у общего dialog owner, не повторять её матрицу в каждом потребителе.
- В Python сохранить все четыре начальных сочетания attachment/damage; перед каждой целевой операцией заново prime verified cache. Для metadata mutations один большой файл, но fresh repository перед каждой из шести операций. Никакого последовательного переиспользования уже повреждённого кеша.
- Перенести проверку shared/distinct bindings в существующую full producer acceptance: три identity в выбранных пяти импортах и точное множество всех bindings без orphan records; удалить повторный импорт.
- Для отказов jobs до ZIP parsing использовать обычные fingerprintable файлы; убрать повторное чтение manifest из synthetic builder. Независимые stage/outcome проверки заменить полным покрытием каждой независимой оси вместо декартова произведения.
- Удалить слабый Python process case, который проверяет только correlation без terminal verdict: живой validation protocol уже доказан dispatcher, Electron Diagnostics и packaged smoke; framing остаётся в worker lifecycle tests.
- В Electron объединить самостоятельные dirty-Specimen close и corrupt-recent-store recovery в уже существующие dossier/lifecycle сценарии. Коррупция optional store вводится после обычного recent-reopen и перед worker failure; проверяются сохранённый draft, честная ошибка списка, успешные reattach/save и отдельный отказ openRecent после освобождения дела. Удалить перекрытый synthetic-material сценарий и недоказанный active-import race. Сохранить meaningful document error UI, обе реальные ветки создания/attach и недоступность открытия потерянного файла.
- Убрать unconditional screenshots без pixel assertions: failure screenshot/trace остаются у Playwright. Имена touched tests/evidence — по ответственности, а не milestone.
- Уточнить существующие Unicode/scalar boundary cases вместо добавления новых; удалить отдельные повторные parse/assertions.

Отклонено или сохранено:

- Pure математические матрицы РБД/РПТ/ПМН имеют разные округления и границы; сохраняются. Убирается только одна точная повторная assertion длины диаграммы.
- 49 дополнительных reliability versions нужны настоящей UI-пагинации старше 50 записей; 64 native openings нужны cross-process recovery — сохраняются.
- Crash, reload, no ACK и accepted save проверяют разные lifecycle events — сохраняются отдельно. Числа timeout/retries/coverage не меняются.
- Удаление document-errors E2E целиком отклонено: это sole UI proof metadata-only/attach, cancel/error и managed-file loss; низкоуровневые дубли можно сократить внутри него.
- Кеширование authoritative ZIP validation и новый fake import pipeline ради трёх разных registration errors отклонены как дополнительная сложность и риск stale evidence без измеренного существенного bottleneck.
- Frozen source/provenance tests защищают контракт и первичные байты, а не тестовый helper. Они сохраняются; M9a в upstream paths — имя действительного внешнего контракта.
- Compression/ZIP/store, parallel Electron, coverage threshold, runtime versions и CI packaging policy не меняются.

### Реализация и целевые проверки

- Реализованы изменения Vitest, verify/build/smoke, PyInstaller/dev dependency, TS/React/Python/Electron tests и canonical testing strategy. Runtime приложения, формулы, file schemas и frozen fixtures не менялись. `walking-skeleton.spec.ts` переименован в `desktop-workflows.spec.ts`; obsolete `SkipWorkerCheck` убран из standalone wrapper, которому он больше не нужен.
- Targeted characterization Main: 111 passed. Исходная 70-opening серия занимала 6 022 мс (первые 64 — 5 444 мс). Попытка собрать её в один тест закономерно превысила прежние 5 с; таймаут не увеличен. Unit теперь проверяет точную границу с существующим file-store fixture, E2E сохраняет все 64 настоящих открытия. `targeted-material-open.json`: 33/33 passed.
- Targeted characterization documents: 22 passed за 4,00 с. В реализации 16 отдельных cache setup заменены четырьмя; шесть metadata cases используют одно большое копирование и шесть независимых reopen. Полный изменённый набор document/jobs/materials/PMN/process + producer acceptance: **190 passed за 8,75 с**, `targeted-python.log`.
- Первый TS target: 100/101 passed; единственный отказ — указанная длительность объединённой серии. После замены избыточного unit stress точной границей material suite 33/33 passed; остальные восемь изменённых suites уже прошли. Логи: `targeted-typescript.json`, `targeted-material-open.json`.
- Ruff format/check, targeted strict mypy/pyright (7 файлов), scoped Desktop/Contracts typecheck и ESLint изменённых TS targets прошли. PowerShell parser подтвердил три изменённых scripts; реальный unfiltered TCP snapshot доступен.
- Targeted Electron: dossier + native dirty-close, document error/integrity, producer materials/reopen и real 64-copy cross-process recovery прошли. При композиции recovery исправлены две начальные предпосылки: optional recent-list error заменяет обычный restart feedback; openRecent проверяется после освобождения активного дела (иначе корректен project_locked). Остаточная ссылка на старое имя candidate исправлена. Последний узкий запуск recovery + Diagnostics: **2 passed за 19,9 с**, `targeted-recovery-validation-fixed.log/.json`.
- Electron targets использовали runtime, собранный исходным verify: production sources не менялись. Новых app/worker builds и полного verify после исходного запуска не было.
- `uv` локально 0.13.4, в CI pinned 0.11.32. Генератор убрал только Hypothesis/sortedcontainers; совместимость lockfile проверена изолированным `uv==0.11.32 lock --check --offline`. Глобальные инструменты не менялись. `uv sync --frozen` удалил только эти две неиспользуемые dev-зависимости.
- Сырые логи и промежуточные failures сохранены в evidence; ни retries, ни повышения таймаутов, ни обхода assertions нет. PyInstaller graph и обе поставки ещё предстоит подтвердить итоговой сборкой.

- Done: исходный полный verify, восемь исследований, root synthesis, реализация, целевые проверки, полный первый review и повторный review исправлений.
- Next: работа завершена; ветка готова к merge. Само слияние оставлено за границей этой задачи. Review-loop завершён без новых подтверждённых findings.
- Edited files: Vitest/Playwright config; `scripts/{codex/verify,build/package,smoke/desktop}.ps1`; `tools/python-worker/{pyproject.toml,uv.lock,worker.spec}`; изменённые Main/Renderer/Contracts tests; `apps/desktop/tests/e2e/{desktop-workflows,source-materials}.spec.ts`; Python document/import/process/jobs/materials/PMN tests и synthetic builder; existing `docs/testing/TEST_STRATEGY.md`; этот единый журнал.
- Verification: исходный и итоговый full SUCCESS; scoped checks и итоговое сравнение описаны в этом журнале.
- Open questions: отсутствуют.

### Первый независимый review

Все восемь reviewers завершили read-only pass. Главный агент проверил каждое замечание по владельцу и прежним assertions.

Приняты и исправлены: точное множество всей таблицы bindings вместо JOIN, скрывающего orphan; ближайшая stale revision (`current_revision - 1`); `targetCycles` в существующем plan assertion; producer inspection detail → UI с `0`/`false` → точное чтение после reopen внутри уже имеющегося сценария; unsupported file error в существующем document сценарии. Новых test cases и GUI launches нет.

В packaged smoke TCP snapshots продолжаются после summary до завершения приложения и итогового process snapshot; сетевой assertion перенесён после наблюдения. Обновлён существующий packaging owner с реальным порядком однократной сборки. Это исправляет подтверждённый пробел наблюдения, а не создаёт тесты скриптов проверки.

P3-предложение вернуть отдельные assertions фиксированных unavailable/closed material ответов preview отклонено: это DEV fixture adapter, базовые режимы уже проверены, а material errors и закрытый проект имеют реальных владельцев в Python/Main/Renderer. Не возвращается проверка синтетических ответов ради самой проверки.

Checkpoint: первый review завершён, исправления реализованы; далее узкие проверки SQL/revision/contracts и двух дополненных E2E, затем повторный review затронутых исправлений. Итоговый полный verify ещё НЕ запускался.

### Повторный review и выход на итоговый gate

Четыре независимых повторных направления (SQL/revision, Electron materials/documents, contracts/preview, orchestration/packaging) завершены без новых подтверждённых findings. Все принятые замечания закрыты; необязательные предложения не расширили scope.

Целевые проверки исправлений: Python 2 passed за 2,26 с; Contracts 24 passed; Electron 2 passed за 7,4 с; desktop strict typecheck, targeted ESLint, Ruff и PowerShell parse прошли. Ссылок на obsolete spec/SkipWorkerCheck вне исторической записи этого журнала не осталось. Новых тестов самого тестового контура нет.

Начинается единственный итоговый `pnpm verify -- --IncludePackaging` после завершения review-loop. До него после baseline выполнялись только узкие проверки; дополнительных полных прогонов не было. Лог и timing: `final-verify.log` / `final-verify.json` в том же evidence-каталоге.

## Итог

`pnpm verify -- --IncludePackaging`: **SUCCESS**, exit 0; 23 октября 2026, **02:25:01–02:31:46 МСК**. После baseline был ровно один полный запуск — этот итоговый; между ними только целевые проверки. Evidence: `final-verify.log`, `final-verify.json`, `final-playwright.json`, `final-python-coverage.xml`, `final-artifacts.json`, `final-worker-archive.txt` в исходном evidence-каталоге.

| Поверхность | Исходно | Итог |
| --- | --- | --- |
| Полный verify | 696,644 с | **404,582 с**, меньше на 292,062 с / **41,92%** |
| Vitest | 230 tests / 21 файл, 20,06 с | 146 tests / 20 файлов, **5,01 с** |
| Aggregate Vitest environment | 100,13 с | **1,43 с**, DOM только у двух файлов |
| Python | 836 passed, 42,65 с | 802 passed, **34,26 с** |
| Python branch coverage | 86,31%, gate 85% | **86,31%**, тот же gate |
| Electron | 21 passed, 127,296 с | 17 passed, **101,437 с**; skipped/flaky/unexpected = 0 |
| Electron / worker builds | 3 / 2 | **1 / 1** |
| Unpacked staging / Portable compression | 2 / 1 | **1 / 1** |
| Worker artifact | 217 файлов, 89,49 MiB | **139 файлов, 80,99 MiB** |
| Packaged smoke | обе поставки passed | обе поставки passed; сохранены import/calculations/reopen/materials и SQLite неизменность чтения материалов |

Сравнение XML coverage подтвердило: **новых непокрытых строк и изменений branch counts нет**, а не только совпадение округлённого процента. Recursive listing реально собранного EXE не содержит mypy/Hypothesis/pytest и исключённых Pydantic plugins; NumPy/SciPy/Pydantic остаются. Финальный обязательный снимок процессов обоих smoke не обнаружил оставшихся процессов их деревьев.

Инварианты сохранены: точные математические эталоны, frozen provenance/21 producer packages, SQLite transactions/audit/reopen/retry, граница истории старше 50, 64 реальных native openings, dirty/save/close/crash/restart и обе формы поставки. Нет новых тестов тестового контура, новых production dependencies, skip/retry/timeout inflation, снижения строгости типов или coverage. Runtime source приложения и внешние file schemas не менялись.

Это сравнение одного исходного и одного итогового запуска на одной машине; кеши и фоновая нагрузка могут влиять на wall-clock. Снижение повторных сборок, подготовок и числа DOM workers детерминировано конфигурацией и логами. Пиковая RAM/полный CPU исходно не измерялись, поэтому точный процент их снижения не заявляется. Упаковка по-прежнему выполняет прежнее сжатие и оба реальных smoke.

Единый журнал актуализирован; подтверждённых незакрытых findings и task-caused verify failures нет. Ветка остаётся отдельной, без merge/push. Финальная фиксация включает только изменения этого аудита.

### Готовность к слиянию

- 2026-10-23T02:35:09+03:00: выполнен completion audit по фактическому Git, журналу и сохранённым отчётам. Предыдущий этап завершил реализацию, review-loop и полный gate, а не только план.
- После `git fetch origin main` актуальный `origin/main` — `4c4428368a2bb8f3049e49cf44ab7c1bf2153c77` — является предком ветки. База не продвинулась относительно проверенного изменения; конфликтов слияния нет.
- Проверены итоговый exit 0, 146 TypeScript / 802 Python / 17 Electron passed, отсутствие skipped/flaky/unexpected, обе packaged smoke и однократные build/staging проходы. После итогового verify менялся только этот Markdown-журнал.
- В diff только согласованный тестовый/сборочный контур и его документация. `git diff --check origin/main...HEAD` проходит; исходная реализация зафиксирована коммитом `75a2e9a`.
- Outcome достигнут, открытых подтверждённых findings и необходимых исправлений нет. Ветка подготовлена к merge; merge и push не выполнялись.

## Рефакторинг политики тестирования

Продолжение в этой же ветке по отдельному запросу пользователя: единый источник правил — `tests/AGENTS.md`, явная ссылка из корневого `AGENTS.md`, без merge/push. Меняется только Markdown; runtime, тесты, конфигурация, gates и данные не меняются.

Контракт: краткие правила написания тестов и выбора уровня, изоляции/DRY, ресурсов и независимых эталонов; без coverage inventory по этапам. Проверка — перенос обязательных инвариантов, согласованность маршрутизации, реальные локальные ссылки, diff и независимый review. Non-goals: новые проверки тестовой инфраструктуры, изменение порогов/таймаутов/команд и повтор полного verify. Блокер — подтверждённая потеря обязательного контракта, которую нельзя разрешить по существующим источникам.

Два новых read-only исследователя проверили ownership/ссылки и обязательные правила. Root проверил findings: тесты физически остаются у владельцев, поэтому новая центральная политика подключается корневым маршрутом; provenance эталона РБД уже хранится в JSON вместе с source/hash/reviewer, units/rounding/tolerance и exact/producer значениями.

Реализация: добавлен `tests/AGENTS.md`; удалены прежняя стратегия и отдельная памятка golden fixtures; обновлены root/scoped docs и ссылки. Историческая запись об исходном `TEST_STRATEGY.md` выше остаётся частью этого единого журнала. Предметные контракты, security/packaging evidence и traceability не заменяются общей test policy.

Prediction: один доступный из root источник уменьшает повторное чтение и расхождения правил. Validation: проверка ссылок/переноса, review и `git diff --check`; rollback/remove condition — неоднозначная маршрутизация или потерянный обязательный инвариант требует исправления до commit. Результаты review и проверки переноса приведены ниже.

Независимый review завершён: восстановлены явные keyboard-only/focus order/restoration/reduced-motion проверки; уточнены supported desktop и сохранность существующего defensive reflow без mobile acceptance. Цель доступности остаётся у requirements/DESIGN, подробности сборки — у packaging owner. После этих уточнений оба reviewers подтвердили отсутствие новых findings.

Итог документационного изменения: 12 Markdown paths (включая удаление двух obsolete документов), 16 локальных ссылок проверены, broken links = 0. Нормативных ссылок на удалённые owners нет; исходные упоминания в журнале исторические. Информация о моделях и ссылки на рекомендации не включены в policy. `git diff --check` проходит; тесты, приложение и полный verify не запускались, поскольку код и конфигурация не менялись.

Рефакторинг документации завершён и фиксируется текущим коммитом. Канонические правила — только `tests/AGENTS.md`; предыдущие записи этого журнала описывают выполненную работу. Ветка остаётся подготовленной к merge, само слияние и push не выполняются.
