# R130SH Run Contract

Владелец schema `.r130run`, vocabulary, exporter и golden packages — R130SH. Frozen package provenance привязан к exact `vitalcc55/R130SH@01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63`: 21 producer-generated package file для 18 сценариев и package-index с реальными outer SHA-256. Downstream acceptance contract привязан отдельно к R130SH 0.9.45 `09097561a6a58b1663a6912357a3c8d1daf7f28c`; validator `m03b.2`, validation report и новые import registry rows используют этот SHA. Обратной runtime-связи с R130SH нет.

M03A остаётся read-only validation foundation. Его downstream synthetic fixtures служат unit/negative/safety tests и не являются producer compatibility proof. M03B хранит отдельный immutable offline snapshot `fixtures/contracts/r130run/v1/m9a`: exact index, все 21 archives и `UPSTREAM_SOURCE.json`; CI не зависит от сети или соседнего checkout. Drift gate запрещает missing/extra package и проверяет size/outer SHA каждого файла; snapshot не обновляется автоматически.

Impeller Reliability не создаёт исходящий контракт, не передаёт план, не запускает R130SH, не читает его SQLite и не меняет первичные факты пакета. M03B разделяет immutable `r130sh_source`, editable `analyst_enrichment` и будущий `derived_analysis`. Original/effective plan сохраняются как source snapshots, а не исполняемый план Impeller Reliability; полный `measurements.csv`, включая rejected rows, остаётся в exact archive, узкая projection хранит только необходимые summaries/counts.

M02.2B CaseDocument остаётся редактируемым `analyst_enrichment` и не смешивается с R130SH inventory/attachments.

M03B принимает `final` и, после отдельного подтверждения, `diagnostic_partial`. Для partial `resume_available=true` является допустимым producer fact: downstream сохраняет его без подмены, не превращает source в final, не продолжает run и не создаёт eligibility. Exact `package_id + export_revision + outer SHA-256` повтор является no-op; другой SHA даёт `import_integrity_conflict`; новая revision сосуществует. UUID producer-а (v4/v7) и bounded source identities отделены от local UUIDv4. Imported outcome/validity/completeness не означают analysis eligibility; `supportedPlanSchemas` пуст.

В `measurements.csv` действует единственная формула v1:

```text
accepted = attempt_disposition in {active, accepted}
           AND segment_disposition == included
```

Маркер обязан точно совпадать с формулой; неизвестные disposition invalid.
При `accepted=false` `accepted_elapsed_s` пуст. При `accepted=true` значение
обязательно, конечно, неотрицательно и для каждой строки совпадает с потоковым
пересчётом накопленных промежутков между соседними accepted samples того же
included segment. Первый accepted sample потока и первый sample нового сегмента
дают нулевое приращение; excluded/rejected rows, паузы, head и tail не
начисляются. Accepted summary одновременно совпадает по count и final elapsed.
Это source evidence, но не duration, resource exposure, censor endpoint или
life metric.

`.r130run` v1 не содержит обязательных `TrialRun`, `VibrationBaseline` или
relative `×1,5` criterion. Их отсутствие не является повреждением package;
downstream не реконструирует их из первой measurement или минимумов X/Y/Z.
