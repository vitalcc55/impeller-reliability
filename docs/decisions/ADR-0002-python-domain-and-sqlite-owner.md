# ADR-0002 — Python worker владеет инженерными правилами и SQLite

- Статус: принято
- Дата: 2026-08-25

## Контекст

Расчёты требуют Decimal, NumPy/SciPy, строгой воспроизводимости и единого владельца проектной истины. Дублирование формул в TypeScript недопустимо.

## Решение

Один долгоживущий Python worker валидирует предметные данные, выполняет будущие расчёты и единолично работает с SQLite. TypeScript оркестрирует сценарии и отображает канонические результаты.

## Альтернативы

Формулы в Renderer создавали бы два источника истины. Native Node SQLite переносил бы владение БД в Main. ORM добавлял бы раннюю абстракцию поверх небольшого явного migration layer.

## Последствия

Межъязыковой контракт становится release boundary; worker поставляется с собственным CPython в PyInstaller onedir. Проект может развивать scientific stack независимо от UI.

## Риски

Process crash, serialization drift и размер SciPy. Они контролируются typed contracts, lifecycle/restart, contract tests, integrity manifest и packaged self-test.
