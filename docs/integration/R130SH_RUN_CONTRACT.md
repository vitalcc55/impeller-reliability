# R130SH Run Contract

Владелец schema `.r130run`, vocabulary и независимых golden packages — R130SH. Проверенный внешний baseline: `vitalcc55/R130SH@ffc03d43f65874e3e6b1cd550b7272a66e68c4e8`. M0, M1, M2, M4a и M5a завершены; frozen target examples manifest/plan/inspection/event/m09a-expected-fixtures доступны. Будущий пакет содержит manifest, original/effective plan и amendments, run summary, provenance, events, full measurements, inspections, attachments, optional spectra/protocol и checksums. Original/effective plan импортируется как неизменяемый `ImportedRunPlanSnapshot`, а не как исполняемый план Impeller Reliability.

Exporter `.r130run` и независимые M9a golden packages ещё не реализованы. M03A может начать contract/import foundation после M02.2B по frozen examples. M03B production importer ждёт R130SH M9a golden packages и выполняет полную cross-repository acceptance; в M02.2B importer не реализуется.

Impeller Reliability не создаёт исходящий контракт, не передаёт план и не меняет первичные факты пакета. Будущий импорт разделяет `r130sh_source`, `analyst_enrichment` и `derived_analysis`; разрешение расхождений и provenance выбора относятся к отдельной импортной вертикали.

M02.2B CaseDocument остаётся редактируемым `analyst_enrichment`, не смешивается с будущими импортированными документами и не создаёт source/import tables или SourceReference bindings.
