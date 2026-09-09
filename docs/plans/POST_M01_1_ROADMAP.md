# План после M01.1

Этот файл сохраняет согласованный порядок будущей работы, которая намеренно не входит в closure walking skeleton. Он не является текущим status-документом и не разрешает реализацию этапов без отдельной задачи.

## M02 — Project Storage

M02.1 создаёт устойчивый контейнер и сессию проекта: `.irproj`, manifest, `project.sqlite`, OS-held lock, forward-only migrations с backup, Project metadata и append-only audit. Точный текущий контракт принадлежит `M02_1_PROJECT_CONTAINER.md`; `health.sqlite` остаётся отдельной app-level диагностикой.

M02.2A добавляет только analyst dossier: CustomerProfile, WheelModel и Specimen в редактируемом `analyst_enrichment`. M02.2B отдельно добавляет нормативные источники и project documents. `TestCampaign` появляется позднее только как downstream-группировка импортированных запусков.

M02.2B одновременно закрепил UX-фундамент: общий interaction-state vocabulary, draft/pending/focus/error contracts, keyboard semantics и существующий defensive reflow. Уже реализованный reflow не удаляется, но для новых этапов он не создаёт mobile/640 px acceptance: целевая Windows desktop-композиция начинается с 1280×720. Дальнейшая последовательность command/jobs/tables/charts/recovery и release accessibility принадлежит `UX_INTERACTION_EVOLUTION.md`; она не разрешает преждевременные компоненты в M02.2B.

Одновременно расширяются существующие owners требований, domain model, glossary и traceability. Новые параллельные спецификации не создаются.

## R130SH baseline и M9a goldens

Frozen package provenance `vitalcc55/R130SH@01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63` закрепляет 21 producer-generated M9a package file для 18 сценариев с реальными outer SHA-256. Текущий semantic acceptance baseline — `vitalcc55/R130SH@09097561a6a58b1663a6912357a3c8d1daf7f28c` (R130SH 0.9.45). Эти роли не смешиваются: первый SHA доказывает происхождение frozen bytes, второй — правила downstream validator/import registry. Runtime/CI связи между репозиториями нет.

## M03A/M03B — входной результат R130SH

M03A — read-only validation foundation в Diagnostics; он не меняет Project и не является import/eligibility. M03B — production import: staged revalidation → immutable managed archive → `r130sh_source` registry/inventory/projection → explicit binding/source-enrichment resolution → reopen. Clean pre-release schema остаётся v1 без фиктивной migration/compatibility. Прямое чтение SQLite R130SH, исходящий план и управление стендом запрещены. M03B не создаёт расчётный snapshot.

## M04A — фундамент предметной модели надёжности

M04A вводит Python-owned derived analysis поверх explicit specimen binding:
`Project` остаётся ReliabilityCase, существующий `Specimen` — объектом
анализа, а `TestExecution`, `FailureObservation` и `ReliabilityDataset`
сохраняют только неизменяемые source-linked snapshots и provenance. Этап не
materializes старые imports автоматически и не добавляет формулы, eligibility,
FMEA, charts или reports. Точный контракт принадлежит
`M04A_RELIABILITY_DOMAIN_FOUNDATION.md`.

## M04A.1 — R130SH 0.9.45 downstream acceptance closure

M04A.1 исправляет exact measurement acceptance predicate, потоково перепроверяет sample-span `accepted_elapsed_s`, сохраняет `diagnostic_partial + resume_available=true`, разделяет golden/acceptance provenance и прекращает вывод `FailureObservation.durationS` из accepted aggregate. Wire schema `.r130run` v1, frozen packages и project schema v1 не меняются. Trial run, vibration baseline и критерий `×1,5` остаются unavailable source evidence.

## M04B — Statistical Classification & Life-Metric Foundation

M04B реализован как `TestExecution → явная versioned ReliabilityObservation → immutable versioned ReliabilityDataset`: failure/right-censored/withdrawn/invalid classification, точный/right-bound/interval/unavailable endpoint, узкие RBD/RPT life metrics, frozen document/evidence provenance и раздельные eligibility/include-exclude decisions. Подробный контракт принадлежит `M04B_STATISTICAL_CLASSIFICATION_AND_LIFE_METRICS.md`. Weibull и расчётные формулы в этот этап не входят.

## M04C и далее — расчёты испытаний

M04C реализует первый полный РБД vertical slice: `ImportedRunPlanSnapshot` + explicit source/enrichment selection → `AnalysisInputSnapshot` → Python validation/calculation → `CalculationSnapshot`. После утверждённого математического контракта и golden fixtures отдельными этапами следуют РПТ, затем ПМН. Расчёты не формируют задание для R130SH.

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
5. M04A reliability domain foundation
6. M04A.1 R130SH 0.9.45 downstream acceptance closure
7. M04B Statistical Classification & Life-Metric Foundation
8. M04C first RBD calculation vertical slice
9. RPT calculation
10. PMN calculation

Каждая ветка заканчивается наблюдаемым вертикальным результатом и собственным verification gate; M02 не начинается из M01.1 автоматически.
