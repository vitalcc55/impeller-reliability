# План после M01.1

Этот файл сохраняет согласованный порядок будущей работы, которая намеренно не входит в closure walking skeleton. Он не является текущим status-документом и не разрешает реализацию этапов без отдельной задачи.

## M02 — Project Storage

M02.1 создаёт устойчивый контейнер и сессию проекта: `.irproj`, manifest, `project.sqlite`, OS-held lock, forward-only migrations с backup, Project metadata и append-only audit. Точный текущий контракт принадлежит `M02_1_PROJECT_CONTAINER.md`; `health.sqlite` остаётся отдельной app-level диагностикой.

M02.2A добавляет только analyst dossier: CustomerProfile, WheelModel и Specimen в редактируемом `analyst_enrichment`. M02.2B отдельно добавляет нормативные источники и project documents. `TestCampaign` появляется позднее только как downstream-группировка импортированных запусков.

M02.2B одновременно закрепил UX-фундамент: общий interaction-state vocabulary, draft/pending/focus/error contracts, keyboard semantics и существующий defensive reflow. Уже реализованный reflow не удаляется, но для новых этапов он не создаёт mobile/640 px acceptance: целевая Windows desktop-композиция начинается с 1280×720. Дальнейшая последовательность command/jobs/tables/charts/recovery и release accessibility принадлежит `UX_INTERACTION_EVOLUTION.md`; она не разрешает преждевременные компоненты в M02.2B.

Одновременно расширяются существующие owners требований, domain model, glossary и traceability. Новые параллельные спецификации не создаются.

## R130SH baseline и M9a goldens

Проверенный baseline `vitalcc55/R130SH@01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63`: M9a завершён, опубликован 21 producer-generated package file для 18 сценариев с реальными outer SHA-256. Exact offline snapshot принадлежит M03B acceptance; ветка upstream указана только как provenance, runtime/CI связи между репозиториями нет.

## M03A/M03B — входной результат R130SH

M03A — read-only validation foundation в Diagnostics; он не меняет Project и не является import/eligibility. M03B — production import: staged revalidation → immutable managed archive → `r130sh_source` registry/inventory/projection → explicit binding/source-enrichment resolution → reopen. Clean pre-release schema остаётся v1 без фиктивной migration/compatibility. Прямое чтение SQLite R130SH, исходящий план и управление стендом запрещены. M03B не создаёт расчётный snapshot.

## M04 — расчёты испытаний

Сначала один полный РБД vertical slice на импортированных и дополненных данных: `ImportedRunPlanSnapshot` + explicit source/enrichment selection → `AnalysisInputSnapshot` → Python validation/calculation → `CalculationSnapshot`. Только после утверждённого математического контракта и golden fixtures добавляются РПТ и ПМН. Расчёты не формируют задание для R130SH.

До первого расчётного экрана фиксируются command availability, неблокирующий job feedback и chart/data-alternative contract. DataGrid не появляется до реального редактируемого табличного сценария FMEA; navigation history и command/shortcut layer вводятся перед несколькими повторяемыми рабочими командами, а не как M02.2B-заготовка.

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
3. M03A run-package contract validation foundation после frozen R130SH examples
4. M03B production importer + immutable `r130sh_source` + M9b acceptance по 21 M9a packages
5. M04 RBD/RPT/PMN analysis

Каждая ветка заканчивается наблюдаемым вертикальным результатом и собственным verification gate; M02 не начинается из M01.1 автоматически.
