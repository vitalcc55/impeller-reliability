# ADR-0001 — Electron и React как Windows desktop shell

- Статус: принято
- Дата: 2026-08-25

## Контекст

Продукту нужны локальный Windows UI, сложные таблицы и графики, печать HTML в PDF, offline-работа и поставка без внешнего Node.

## Решение

Использовать Electron Main, sandboxed Preload и React Renderer. Renderer не получает Node/Electron API; системные действия доступны только через явный typed preload.

## Альтернативы

Qt/PySide концентрировал бы UI и домен в Python, но расходился бы с выбранным TypeScript UI-стеком. Tauri добавлял бы Rust-границу без продуктовой необходимости. Web-server нарушал бы offline process boundary.

## Последствия

Доступны Chromium UI и `printToPDF`; поставка тяжелее нативного shell и требует Electron security hardening, CSP, fuses и packaged smoke. Packaged renderer обслуживается ограниченным custom protocol вместо привилегированного `file://`.

## Риски

Рост размера и холодного старта, уязвимости при ослаблении sandbox/preload. Риски контролируются закреплённой версией Electron, запретом навигации/permissions и проверкой артефакта.
