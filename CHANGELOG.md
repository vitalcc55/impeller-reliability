# Changelog

## Unreleased

- Первый публикуемый формат `.irproj` сведён к чистой schema v1: Project metadata/audit, CustomerProfile, WheelModel, Specimen и CaseDocument создаются атомарно без поддержки невыпущенных промежуточных схем.
- M02.2B: добавлены документы аналитического дела, однократно прикрепляемые управляемые копии с SHA-256, applicability к моделям/образцам, integrity status, completeness warnings, optimistic revision/audit и полный typed Electron↔Python поток без передачи абсолютных путей Renderer.
- Ручная операция честно названа резервной копией базы проекта; полный перенос до `.irpkg` выполняется копированием закрытого каталога `.irproj`.
- Закреплены interaction-state, draft, focus, keyboard и responsive-контракты текущего engineering workspace; последующее развитие commands/jobs/tables/charts/recovery распределено по предметным этапам.
- M03A: добавлена диагностическая read-only проверка candidate `.r130run` по зафиксированному синтетическому baseline R130SH: потоковые ZIP/CRC/SHA-256 и покрытые semantic checks, ограниченная отменяемая job, typed progress/findings/provenance и экран в «Диагностике». Project schema остаётся v1; импорт, `r130sh_source` и допуск к расчётам не создаются.
- M03B: clean pre-release schema v1 расширена production import контуром `.r130run`: immutable managed archive, `r130sh_source` registry/inventory/projection, явная specimen binding и provenance решений source/enrichment. Все 21 producer-generated M9a packages exact R130SH commit `01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63` закреплены offline и проходят M9b import/reopen acceptance; расчётные eligibility и результаты не создаются.
- M04A: добавлены неизменяемые `TestExecution`, `FailureObservation` и `ReliabilityDataset` поверх явно связанного и проверенного R130SH source без формул и automatic eligibility.
- M04A.1: validator `m03b.2` синхронизирован с acceptance contract R130SH 0.9.45 `09097561a6a58b1663a6912357a3c8d1daf7f28c`: точная truth table и cumulative sample spans проверяются потоково, `diagnostic_partial + resume_available=true` сохраняется, а `acceptedElapsedS` больше не трактуется как duration/life metric. Frozen M9a provenance `01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63` и 21 archive остаются неизменными.
- M04B: добавлена production-цепочка `TestExecution → ReliabilityObservation → ReliabilityDataset`: инженер явно фиксирует classification, endpoint, документированную наработку или её отсутствие и evidence, затем сохраняет immutable version выборки с раздельными policy eligibility и include/exclude decisions. Появились bounded IPC/UI, exact retry, optimistic versions, reopen integrity и полный desktop-сценарий без расчёта показателей надёжности.

## 0.1.0 — 2026-08-25

- Созданы M00 repository constitution и M01 walking skeleton.
- Добавлены строгие TypeScript/Python gates, Electron E2E и packaging smoke.
- Предметные расчёты намеренно отсутствуют.
- M01.1: добавлены operation-specific IPC, response revision, lifecycle/restart worker, WAL health verdict, раздельная CSP, production Electron fuses, process-tree smoke и Windows quality workflow.
