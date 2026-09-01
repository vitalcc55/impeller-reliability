# R130SH Run Contract

Владелец schema `.r130run`, vocabulary, exporter и golden packages — R130SH. Production baseline привязан к exact `vitalcc55/R130SH@01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63` (branch `codex/data-and-protocol-improvements` указан только как provenance). M9a завершён: опубликован 21 producer-generated package file для 18 сценариев, package-index содержит реальные outer SHA-256. M9b выполняется в Impeller Reliability; обратной связи с R130SH нет.

M03A остаётся read-only validation foundation. Его downstream synthetic fixtures служат unit/negative/safety tests и не являются producer compatibility proof. M03B хранит отдельный immutable offline snapshot `fixtures/contracts/r130run/v1/m9a`: exact index, все 21 archives и `UPSTREAM_SOURCE.json`; CI не зависит от сети или соседнего checkout. Drift gate запрещает missing/extra package и проверяет size/outer SHA каждого файла; snapshot не обновляется автоматически.

Impeller Reliability не создаёт исходящий контракт, не передаёт план, не запускает R130SH, не читает его SQLite и не меняет первичные факты пакета. M03B разделяет immutable `r130sh_source`, editable `analyst_enrichment` и будущий `derived_analysis`. Original/effective plan сохраняются как source snapshots, а не исполняемый план Impeller Reliability; полный `measurements.csv`, включая rejected rows, остаётся в exact archive, узкая projection хранит только необходимые summaries/counts.

M02.2B CaseDocument остаётся редактируемым `analyst_enrichment` и не смешивается с R130SH inventory/attachments.

M03B принимает `final` и, после отдельного подтверждения, `diagnostic_partial`. Exact `package_id + export_revision + outer SHA-256` повтор является no-op; другой SHA даёт `import_integrity_conflict`; новая revision сосуществует. UUID producer-а (v4/v7) и bounded source identities отделены от local UUIDv4. Imported outcome/validity/completeness не означают analysis eligibility; `supportedPlanSchemas` пуст, а расчётные claims появятся только в M04.
