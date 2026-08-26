# ADR-0007 — каталог проекта и Python-owned ProjectSession

- Статус: принято
- Дата: 2026-08-25

## Контекст

Проект связывает будущие образцы, испытания, анализ и отчёты, тогда как R130SH владеет только фактическим запуском. SQLite требует устойчивого рабочего каталога и единственного writer.

## Решение

Рабочий проект — каталог `.irproj` с manifest и Python-owned `project.sqlite`. Existing container проходит bounded read-only structure/schema/evidence validation до OS lock и SQLite-записей. Один worker удерживает максимум одну ProjectSession и Windows OS lock; Main зеркалит serial worker одной очередью и дренирует её перед controlled lifecycle. Создание выполняется sibling staging + atomic rename, migrations только forward с проверенным backup.

## Альтернативы

ZIP для текущей работы требовал бы постоянной перепаковки. Общая app-level база смешивала бы жизненные циклы проектов. PID-only lock оставлял бы stale ownership и допускал повторное использование PID.

## Последствия

Проект переносим как каталог, SQLite и audit имеют одного владельца, crash освобождает lock автоматически. Main выбирает путь, Renderer работает только через typed project API.

## Риски

Повреждённый manifest/SQLite, несовместимая schema, lifecycle во время stateful operation и lock contention. Они закрываются typed errors, schema/evidence contract, последовательным dispatch/drain и OS lock; актуальная граница файловых угроз принадлежит `docs/security/THREAT_MODEL.md`.
