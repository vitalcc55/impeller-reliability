# План после M01.1

Этот файл сохраняет согласованный порядок будущей работы, которая намеренно не входит в closure walking skeleton. Он не является текущим status-документом и не разрешает реализацию этапов без отдельной задачи.

## M02 — Project Storage

M02.1 создаёт устойчивый контейнер и сессию проекта: `.irproj`, manifest, `project.sqlite`, OS-held lock, forward-only migrations с backup, Project metadata и append-only audit. Точный текущий контракт принадлежит `M02_1_PROJECT_CONTAINER.md`; `health.sqlite` остаётся отдельной app-level диагностикой.

M02.2 отдельной вертикалью добавляет Customer, WheelModel, Specimen, TestCampaign и SourceDocument. Эти сущности и их каталоги не создаются заранее в M02.1.

Одновременно расширяются существующие owners требований, domain model, glossary и traceability. Новые параллельные спецификации не создаются.

## M03 — входной результат R130SH

После получения реального обезличенного экспорта R130SH фиксируются входная schema, positive/negative fixtures, canonical hashing и importer Impeller Reliability. Вертикаль: независимый результат R130SH → безопасный staging → immutable import → привязка к проекту/образцу → повторное открытие. Прямое чтение SQLite R130SH, исходящий план и управление стендом запрещены.

## M04 — расчёты испытаний

Сначала один полный РБД vertical slice на импортированных и дополненных данных: Project → Specimen → Campaign → Python validation/calculation → evidence → immutable revision → повторное открытие. Только после утверждённого математического контракта и golden fixtures добавляются РПТ и ПМН. ECharts и KaTeX появляются вместе с реальной потребностью этой вертикали. Расчёты не формируют задание для R130SH.

## M05 и последующие предметные этапы

После входного run contract и расчётной вертикали реализуются анализ запуска и классификация, затем только по утверждённым методикам — FMEA/FMECA, статистика/Вейбулл, тренды и спектры вибрации, Марков, Монте-Карло и полная отчётность. Формулы не переносятся из демонстрационного кода без contract, source, invariants, rounding policy и независимых fixtures.

## Поставка и эксплуатация

Portable остаётся единым переносимым артефактом, но его холодный запуск около 26 секунд не назначает его автоматически основным ежедневным вариантом. После измерений на лабораторных Windows 10/11 с Defender отдельно выбирается production-поставка: installer/installed, onedir или допустимый медленный portable. Тогда же отдельно решаются подпись и release-процесс.

GitHub quality workflow до production release выполняет static/unit gate, production build и Electron E2E, но намеренно не собирает PyInstaller worker, `win-unpacked` и portable. Packaging остаётся обязательным локальным полным gate; manual/release workflow появляется ближе к поставке, когда определён production-вариант.

## Отложенные обновления toolchain

TypeScript 7/Vite 8 переходят только цельной совместимой матрицей после stable-поддержки со стороны `typescript-eslint` и `electron-vite`. Свежие пакеты не обходят pnpm minimum-release-age policy. Обновление Node major сопровождается обновлением реального runtime, `@types/node`, CI и packaged verification.

## Последовательность веток

1. `codex/m01-closure-hardening`
2. `codex/m02-project-storage`
3. `codex/m03-r130sh-run-import`
4. `codex/m04-rbd-analysis`
5. `codex/m04-rpt-pmn-analysis`
6. `codex/m05-run-analysis`

Каждая ветка заканчивается наблюдаемым вертикальным результатом и собственным verification gate; M02 не начинается из M01.1 автоматически.
