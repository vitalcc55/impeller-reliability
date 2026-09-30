# R130SH Run Contract

Владелец schema `.r130run`, vocabulary, exporter и golden packages — R130SH. Frozen package provenance привязан к exact `vitalcc55/R130SH@01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63`: 21 producer-generated package file для 18 сценариев и package-index с реальными outer SHA-256. Downstream acceptance правил материалов привязан отдельно к проверенному локальному R130SH 0.9.48 `b7792758b407ffc52d2fff051243056f63dbf18f`; validator `m03b.3`, текущий validation report и новые import registry rows используют этот SHA. Опубликованный producer `main` на момент сверки — `d480509d45cf4eb1d1f4a959294797efc9fd9d27` / 0.9.47; wire-контракт материалов в этой дельте не изменён. Исторические receipts `m03b.2` и их контрактный SHA `09097561a6a58b1663a6912357a3c8d1daf7f28c` не переписываются. Обратной runtime-связи с R130SH нет.

Проверка материалов включает optional пару `protocol/protocol.pdf` и `protocol/release.json`: schema, run identity, положительные целочисленные release ID/revision без bool, номер/шаблон, исходный UTC timestamp, объект сотрудника, список photo IDs и связь с проверенным хешем PDF. Отсутствие пары допустимо и означает только отсутствие протокола в выбранном пакете; содержание PDF, подпись и заключение лаборатории не проверяются. Inventory release получает semantic coverage, PDF остаётся `structural_only`.

Осмотры проверяются по известным полям, этапам, trip index, UTC-времени и неотрицательной наработке (pre-test — ноль), сотруднику, findings/outcome и уникальности attachment IDs внутри записи. Канонические осмотр, findings и actor следуют round-trip проверке producer: actor содержит ровно три исходных поля, строки комментария/findings обрезаны producer, decimal text сохраняется в каноническом виде, время — ISO UTC с `+00:00` и исходной микросекундной точностью. Записи JPEG/PNG проверяются по ID, явно заданной доступности, типу/размеру/размерам изображения, сотруднику и исходному UTC-времени с `Z`; доступный member должен совпадать с inventory по path/media/size/hash. Недоступное фото с null path и причиной допустимо только в diagnostic partial. Неоднозначный ID и неразрешённая справочная ссылка дают отдельные warning findings; обратная полнота списков осмотра и протокола не навязывается. Семантика индекса не доказывает отображаемость содержимого изображения. Текущая проверка не заменяет сохранённый receipt импорта и не создаёт аналитические сущности.

M03A остаётся read-only validation foundation. Его downstream synthetic fixtures служат unit/negative/safety tests и не являются producer compatibility proof. M03B хранит отдельный immutable offline snapshot `fixtures/contracts/r130run/v1/m9a`: exact index, все 21 archives и `UPSTREAM_SOURCE.json`; CI не зависит от сети или соседнего checkout. Drift gate запрещает missing/extra package и проверяет size/outer SHA каждого файла; snapshot не обновляется автоматически.

Impeller Reliability не создаёт исходящий контракт, не передаёт план, не запускает R130SH, не читает его SQLite и не меняет первичные факты пакета. M03B разделяет immutable `r130sh_source`, editable `analyst_enrichment` и `derived_analysis`. Original/effective plan сохраняются как source snapshots, а не исполняемый план Impeller Reliability; полный `measurements.csv`, включая rejected rows, остаётся в exact archive, узкая projection хранит только необходимые summaries/counts.

M02.2B CaseDocument остаётся редактируемым `analyst_enrichment` и не смешивается с R130SH inventory/attachments.

Python предоставляет source-only страницы осмотров и фотографий, деталь осмотра и сведения о включённом протоколе через текущую ProjectSession. Привязка Specimen и materialization не требуются. Каждый запрос сверяет точный local import, package/revision/outer SHA, обычный managed ZIP и его inventory; на одном открытом handle проверяет SHA архива до и после адресного чтения manifest/JSON материалов, без разбора CSV. Повторное хеширование не позволяет восстановленному времени файла скрыть изменение невыбранных bytes. Материал-only путь сохраняет существующий запрет downstream eligibility claims в читаемых JSON. Текущий отчёт ограничен `source_material_metadata` и не подменяет исторический receipt. Чтение не пишет SQLite, audit или аналитические сущности. Отсутствующий/изменённый ZIP локализован в операции материалов.

Страница содержит до 50 элементов (обычно 25), не более 256 KiB UTF-8; cursor привязан к делу, импорту, редакции, хешу и виду списка. Текст ограничен 16 KiB, запись — 64 KiB: слишком большой материал возвращается явным `too_large` без усечения. Ограничение объёма страницы даёт продолжение с `byte_limit`. Неоднозначные идентификаторы сохраняют отдельные позиции списка, но деталь по такому ID отклоняется; справочные связи получают `resolved/unresolved/ambiguous`. Release/revision протокола, trip index и размеры изображения передаются точными десятичными строками. Пути и бинарные данные в эти модели не входят.

Typed Main/Preload API проводит чтение по точной origin и системное открытие по identity одного JPEG/PNG/PDF. Python создаёт отдельную ограниченную копию на том же проверяемом ZIP handle; путь существует только в ответе Python → Main. Main повторно сверяет bytes/size/SHA и активную сессию перед `shell.openPath`; ошибка ОС не превращается в успех. Временные копии принадлежат Main, находятся вне дела и не создают документов или аналитических записей; безопасность и lifecycle описаны в [Electron Security](../security/ELECTRON_SECURITY.md). Действия просмотра в renderer ещё не подключены.

M03B принимает `final` и, после отдельного подтверждения, `diagnostic_partial`. Для partial `resume_available=true` является допустимым producer fact: downstream сохраняет его без подмены, не превращает source в final, не продолжает run и не создаёт eligibility. Exact `package_id + export_revision + outer SHA-256` повтор является no-op; другой SHA даёт `import_integrity_conflict`; новая revision сосуществует. UUID producer-а (v4/v7) и bounded source identities отделены от local UUIDv4. Imported outcome/validity/completeness не означают analysis eligibility; `supportedPlanSchemas` пуст.

В original/effective планах ключи `laboratory_case_reference` и `customer_order_reference` обязательны, а значения могут быть `null` либо строкой с хотя бы одним непробельным символом по правилу Python `str.strip()` у R130SH. Импорт сохраняет `null` без замены на пустую строку; отсутствие ключа, неверный тип и строка только из пробельных символов отклоняются.

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

M04C читает RBD input fields только из exact managed archive через существующий
`R130shSourceRepository`: explicit `plan/original.json` либо inner plan из
`plan/effective.json`, после полной integrity-проверки и с bounded member read.
Запрос связывает exact `execution_id` и `local_import_id`; их несоответствие
отклоняется, а новая export revision не выбирается автоматически. Даже без
внешнего request deadline проверка источника ограничена 30 секундами.
Renderer не передаёт path и не читает ZIP. `_plan_summary` остаётся сокращённой
проекцией; отсутствие `N0`, `k1`, разгона и торможения в summary не означает их
отсутствия в source. Inventory payload SHA-256, outer archive SHA-256,
plan id/revision и producer provenance сохраняются раздельно. `measurements.csv`
не materialize ради получения scalar plan fields.

РПТ использует тот же verified managed archive seam и exact identity `execution_id`/`local_import_id`, но читает шесть собственных полей плана, включая `steady_duration_s`, а также source methodical requirements, округлённые execution targets и политику нижней точки. Numeric JSON scalar сохраняется как исходная лексема без float-преобразования; расчётная граница проверяет её отдельно. Для importer-valid крупного plan member действует существующий лимит импортёра, а не прежний локальный RBD read-limit. Путь к архиву Renderer не получает; позднее отсутствие managed ZIP не переписывает сохранённый результат.

ПМН через тот же verified managed archive читает exact `execution_id`/`local_import_id`, original/effective план и шесть собственных полей: `nominal_rpm`, `speed_factor`, `target_cycles` и три длительности фаз. Source-лексема и координата member сохраняются без float-преобразования; methodical requirements и execution targets остаются отдельными свидетельствами. Измерения не подставляются вместо scalar plan inputs. Отдельный producer-generated эталон текущего R130SH и его происхождение зафиксированы в `fixtures/contracts/r130run/v1/pmn-reference/UPSTREAM_SOURCE.json`; он не меняет замороженные M9a пакеты. Поздняя недоступность ZIP не меняет сохранённый расчёт.
