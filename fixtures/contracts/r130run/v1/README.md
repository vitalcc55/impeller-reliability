# Frozen target examples `.r130run` v1

## Статус

- Контракт заморожен в M0.
- Production-реализация отсутствует.
- Примеры синтетические и не являются результатами реального испытания.
- Source of truth по решениям:
  `docs/analysis/r130sh_data_protocol_improvements_context.md`.
- План реализации:
  `docs/plans/r130sh_data_protocol_improvements_implementation_plan.md`.

## Состав

- `docs/contracts/r130run/v1/as-is-v7-baseline.example.json` — только
  исторический M0 baseline прежних mode parameters и lifecycle gaps. Это не
  `.r130run` fixture, он не импортируется downstream-приложением и не создаёт
  обязательства backward compatibility с предрелизной SQLite v7.
- `docs/contracts/r130run/v1/manifest.final.example.json` — нормативный shape
  manifest и полный обязательный payload inventory без downstream eligibility.
- `docs/contracts/r130run/v1/plan.rbd-rounding.example.json` — точное
  методическое требование и округлённая уставка исполнения.
- `docs/contracts/r130run/v1/measurement.example.json` — неизменяемый
  physical measurement без зачтённого времени.
- `docs/contracts/r130run/v1/accepted-projection.example.json` — производная
  mode-specific зачтённая проекция и payload shape обязательного package-файла
  accepted summary.
- `docs/contracts/r130run/v1/event.example.json` — typed durable event.
- `docs/contracts/r130run/v1/inspection.example.json` — structured inspection.
- `docs/contracts/r130run/v1/provenance.example.json` — run-bound provenance.
- `docs/contracts/r130run/v1/m09a-expected-fixtures.json` — ожидаемая матрица
  локальных golden fixtures и отдельное непакетное ожидание: несовместимая
  предрелизная SQLite отклоняется до экспорта без изменения и миграции.

## Идентичность package revision

`package_id` стабилен для серии экспортов одного `run_id`, а
`export_revision` монотонно увеличивается. Импортёр вычисляет SHA-256 готового
ZIP целиком и использует ключ идемпотентности:

```text
package_id + export_revision + outer_package_sha256
```

`outer_package_sha256` не помещается внутрь ZIP, потому что это создало бы
самореферентный hash. Внутренний manifest содержит `source_snapshot_sha256` и
перечень payload-файлов с их собственными SHA-256. Корневой manifest и индекс
контрольных сумм не входят в `files`: manifest не хеширует сам себя, а индекс
перечисляет payload-файлы и проверяется validator-ом отдельно.
Синтетические повторяющиеся SHA в shape examples не являются M9a golden
checksums; M9a создаёт отдельные реальные пакеты и вычисляет их hashes.

## Владение eligibility

R130SH не экспортирует authoritative `calculation_eligible`. Он экспортирует
факты package/run integrity и лабораторный outcome. Конкретную пригодность для
ресурсного расчёта, Weibull, vibration trend, comparison или FMEA вычисляет
ImpellerReliabilityCalc по versioned downstream rules.

## Временная модель

Primary measurement хранит полный неизменяемый physical fact stream, включая
четыре физические временные шкалы:

- `epoch_monotonic_elapsed_s`;
- `run_elapsed_s`;
- `attempt_elapsed_s`;
- `segment_elapsed_s`.

`accepted_elapsed_s` существует только в производной accepted projection и
рассчитывается из attempt/segment disposition и mode-specific crediting policy.
Первичные measurements не переписываются после изменения disposition.

## Specimen identity

Все запуски одного физического образца используют один `specimen_id`. M3 хранит
каталог последних карточек образцов; подключение выбора, создания и копирования
к production-формам выполняется единым cutover в M6a. Одинаковая маркировка
сама по себе не объединяет разные образцы.

## Clean SQLite schema первого релиза

- До первого выпуска используется один clean DDL с `SCHEMA_VERSION = 1`.
- M3–M8 добавляют реализованные целевые группы прямо в этот DDL без
  промежуточной migration chain и backup sidecars.
- Несовместимая предрелизная база отклоняется без изменения.
- Первая migration v1→v2 допустима только после реального выпуска v1, если
  потребуется сохранить пользовательские данные.
- Версия внутренней SQLite schema независима от версии `.r130run`.
