# R130SH Run Contract

Владелец schema `.r130run`, vocabulary и независимых golden packages — R130SH. Проверенный внешний baseline: `vitalcc55/R130SH@f02f6d954246a5ab6f57d33dac724ce03d7fb841`. M0, M1, M2, M3, M4a и M5a завершены; следующий upstream-этап — M4b. Frozen target examples manifest/plan/measurement/accepted-projection/event/inspection/provenance/m09a-expected-fixtures доступны, являются синтетическими и не считаются M9a golden packages. Будущий пакет содержит manifest, original/effective plan и amendments, run summary, provenance, events, full measurements, inspections, attachments, optional spectra/protocol и checksums. Original/effective plan импортируется как неизменяемый `ImportedRunPlanSnapshot`, а не как исполняемый план Impeller Reliability.

Exporter M8 `.r130run` и независимые M9a golden packages ещё не реализованы. M03A может выполнять только contract/validation foundation по frozen synthetic examples. M03B production importer заблокирован до появления независимых R130SH M9a golden packages и полной cross-repository acceptance; в M02.2B/M03A importer не реализуется.

Impeller Reliability не создаёт исходящий контракт, не передаёт план и не меняет первичные факты пакета. Будущий импорт разделяет `r130sh_source`, `analyst_enrichment` и `derived_analysis`; разрешение расхождений и provenance выбора относятся к отдельной импортной вертикали.

M02.2B CaseDocument остаётся редактируемым `analyst_enrichment`, не смешивается с будущими импортированными документами и не создаёт source/import tables или SourceReference bindings.
