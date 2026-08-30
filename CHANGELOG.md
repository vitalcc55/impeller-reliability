# Changelog

## Unreleased

- Первый публикуемый формат `.irproj` сведён к чистой schema v1: Project metadata/audit, CustomerProfile, WheelModel, Specimen и CaseDocument создаются атомарно без поддержки невыпущенных промежуточных схем.
- M02.2B: добавлены документы аналитического дела, однократно прикрепляемые управляемые копии с SHA-256, applicability к моделям/образцам, integrity status, completeness warnings, optimistic revision/audit и полный typed Electron↔Python поток без передачи абсолютных путей Renderer.
- Ручная операция честно названа резервной копией базы проекта; полный перенос до `.irpkg` выполняется копированием закрытого каталога `.irproj`.
- Закреплены interaction-state, draft, focus, keyboard и responsive-контракты текущего engineering workspace; последующее развитие commands/jobs/tables/charts/recovery распределено по предметным этапам.
- M03A: добавлена диагностическая read-only проверка candidate `.r130run` по зафиксированному синтетическому baseline R130SH: потоковые ZIP/CRC/SHA-256 и покрытые semantic checks, ограниченная отменяемая job, typed progress/findings/provenance и экран в «Диагностике». Project schema остаётся v1; импорт, `r130sh_source` и допуск к расчётам не создаются.

## 0.1.0 — 2026-08-25

- Созданы M00 repository constitution и M01 walking skeleton.
- Добавлены строгие TypeScript/Python gates, Electron E2E и packaging smoke.
- Предметные расчёты намеренно отсутствуют.
- M01.1: добавлены operation-specific IPC, response revision, lifecycle/restart worker, WAL health verdict, раздельная CSP, production Electron fuses, process-tree smoke и Windows quality workflow.
