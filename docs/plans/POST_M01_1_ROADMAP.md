# План после M01.1

Этот файл сохраняет согласованный порядок будущей работы, которая намеренно не входит в closure walking skeleton. Он не является текущим status-документом и не разрешает реализацию этапов без отдельной задачи.

## M02 — Project Storage

M02.1 создаёт устойчивый контейнер и сессию проекта: `.irproj`, manifest, `project.sqlite`, OS-held lock, forward-only migrations с backup, Project metadata и append-only audit. Точный текущий контракт принадлежит `M02_1_PROJECT_CONTAINER.md`; `health.sqlite` остаётся отдельной app-level диагностикой.

M02.2A добавляет только analyst dossier: CustomerProfile, WheelModel и Specimen в редактируемом `analyst_enrichment`. M02.2B отдельно добавляет нормативные источники и project documents. `TestCampaign` появляется позднее только как downstream-группировка импортированных запусков.

Одновременно расширяются существующие owners требований, domain model, glossary и traceability. Новые параллельные спецификации не создаются.

## R130SH M0/M1 — frozen vocabulary и examples

R130SH владеет package schema и замораживает vocabulary/examples. До этого Impeller Reliability не объявляет production importer готовым.

## M03A/M03B — входной результат R130SH

M03A создаёт contract/import foundation по frozen examples. После R130SH M8 exporter и M9a independent golden matrix M03B реализует полный импорт: staging → immutable `r130sh_source` → source/enrichment resolution → повторное открытие. Прямое чтение SQLite R130SH, исходящий план и управление стендом запрещены.

## M04 — расчёты испытаний

Сначала один полный РБД vertical slice на импортированных и дополненных данных: `ImportedRunPlanSnapshot` + explicit source/enrichment selection → `AnalysisInputSnapshot` → Python validation/calculation → `CalculationSnapshot`. Только после утверждённого математического контракта и golden fixtures добавляются РПТ и ПМН. Расчёты не формируют задание для R130SH.

## M05 и последующие предметные этапы

После входного run contract и расчётной вертикали реализуются анализ запуска и классификация, затем только по утверждённым методикам — FMEA/FMECA, статистика/Вейбулл, тренды и спектры вибрации, Марков, Монте-Карло и полная отчётность. Формулы не переносятся из демонстрационного кода без contract, source, invariants, rounding policy и независимых fixtures.

## Поставка и эксплуатация

Portable остаётся единым переносимым артефактом, но его холодный запуск около 26 секунд не назначает его автоматически основным ежедневным вариантом. После измерений на лабораторных Windows 10/11 с Defender отдельно выбирается production-поставка: installer/installed, onedir или допустимый медленный portable. Тогда же отдельно решаются подпись и release-процесс.

GitHub quality workflow до production release выполняет static/unit gate, production build и Electron E2E, но намеренно не собирает PyInstaller worker, `win-unpacked` и portable. Packaging остаётся обязательным локальным полным gate; manual/release workflow появляется ближе к поставке, когда определён production-вариант.

## Отложенные обновления toolchain

TypeScript 7/Vite 8 переходят только цельной совместимой матрицей после stable-поддержки со стороны `typescript-eslint` и `electron-vite`. Свежие пакеты не обходят pnpm minimum-release-age policy. Обновление Node major сопровождается обновлением реального runtime, `@types/node`, CI и packaged verification.

## Последовательность веток

1. `codex/m02-2a-analyst-dossier`
2. M02.2B normative sources
3. M03A import contract foundation после frozen R130SH examples
4. M03B production importer после R130SH M9a golden packages
5. M04 RBD/RPT/PMN analysis

Каждая ветка заканчивается наблюдаемым вертикальным результатом и собственным verification gate; M02 не начинается из M01.1 автоматически.
