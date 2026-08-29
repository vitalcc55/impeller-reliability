# Non-functional Requirements

- Windows 10/11 x64, offline, single-user; один portable EXE.
- UI показывается до полной загрузки SciPy; startup, worker-ready и размер измеряются отдельно.
- Строгая типизация и versioned contracts; deterministic checks и bounded shutdown.
- SQLite: FK, WAL, one writer, short transactions; первый выпущенный baseline создаётся атомарно без фиктивного predecessor, а каждая будущая forward-only migration начинается с verified SQLite backup.
- Project container создаётся через sibling staging и atomic rename; Windows lock удерживается ОС, PID используется только для диагностики.
- Security: sandboxed renderer, no network/API/server, integrity-checked worker/imports.
- UX responsiveness измеряется по реализованным сценариям (`app visible`, `project opened`, `section switched`, `save acknowledged`, первый результат); пороги утверждаются только после измерений реальной поставки и предметной нагрузки.
