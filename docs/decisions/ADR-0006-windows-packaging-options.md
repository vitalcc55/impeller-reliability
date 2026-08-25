# ADR-0006 — Windows packaging и компромисс portable startup

- Статус: принято для M01; production-вариант требует отдельного решения
- Дата: 2026-08-25

## Контекст

Нужен единый переносимый EXE, но PyInstaller onedir внутри Electron portable увеличивает распаковку и холодный запуск. Текущий portable меньше 200 МБ, но запускается около 26 секунд.

## Решение

Для M01 поддерживать и проверять `win-unpacked` и portable. Python остаётся onedir; вложенный PyInstaller onefile запрещён. Portable служит переносимым артефактом, но не назначается основным ежедневным вариантом до лабораторных измерений.

## Альтернативы

Installer/installed или onedir быстрее запускаются, но не являются одним файлом. Вложенный onefile создавал бы двойную распаковку. Смена Electron/Python не оправдана одной метрикой startup.

## Последствия

Каждый packaging change проходит worker self-test, fuses/integrity checks, `win-unpacked` и настоящий portable smoke. Выбор production installer/installed/portable отложен в post-M01.1 roadmap.

## Риски

Медленный cold start, ложные smoke при чужих процессах и неподписанный EXE. Smoke привязан к конкретному process tree; подпись и production delivery требуют отдельного решения.
