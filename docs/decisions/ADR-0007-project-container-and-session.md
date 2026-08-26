# ADR-0007 — каталог проекта и Python-owned ProjectSession

- Статус: принято
- Дата: 2026-08-25

## Контекст

Проект связывает будущие образцы, испытания, анализ и отчёты, тогда как R130SH владеет только фактическим запуском. SQLite требует устойчивого рабочего каталога и единственного writer.

## Решение

Рабочий проект — каталог `.irproj` с manifest и Python-owned `project.sqlite`. Existing container проходит read-only identity/topology preflight до OS lock и любых SQLite-записей; reserved reparse/hard-link objects запрещены. Один worker удерживает максимум одну ProjectSession и Windows OS lock; создание выполняется sibling staging + atomic rename, migrations только forward с проверенным backup.

## Альтернативы

ZIP для текущей работы требовал бы постоянной перепаковки. Общая app-level база смешивала бы жизненные циклы проектов. PID-only lock оставлял бы stale ownership и допускал повторное использование PID.

## Последствия

Проект переносим как каталог, SQLite и audit имеют одного владельца, crash освобождает lock автоматически. Main выбирает путь, Renderer работает только через typed project API.

## Риски

Повреждённый manifest/SQLite, path substitution, несовместимая схема и lock contention. Они отклоняются typed errors после read-only `application_id`, schema, projectId и file-identity checks; новая/чужая SQLite не модифицируется. Полное устранение malicious same-user TOCTOU потребовало бы custom SQLite VFS или отдельной directory ACL policy и остаётся за границей M02.1.
