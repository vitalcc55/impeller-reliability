# R130SH Run Contract

Владелец schema `.r130run`, vocabulary и независимых golden packages — R130SH. Проверенный внешний baseline: `vitalcc55/R130SH@de10ff83a2a4e3074d2030bb6a7af69c180a998e`. Будущий пакет содержит manifest, original/effective plan и amendments, run summary, provenance, events, full measurements, inspections, attachments, optional spectra/protocol и checksums. Original/effective plan импортируется как неизменяемый `ImportedRunPlanSnapshot`, а не как исполняемый план Impeller Reliability.

Production importer нельзя объявить готовым до frozen schema/examples R130SH M0/M1. M03A фиксирует только contract/import foundation по этим примерам. Полная cross-repository acceptance выполняется после exporter M8 и независимых golden packages M9a; до этого M03B не считается завершённым.

Impeller Reliability не создаёт исходящий контракт, не передаёт план и не меняет первичные факты пакета. Будущий импорт разделяет `r130sh_source`, `analyst_enrichment` и `derived_analysis`; разрешение расхождений и provenance выбора относятся к отдельной импортной вертикали.

M02.2A не реализует importer или таблицы source bindings.
