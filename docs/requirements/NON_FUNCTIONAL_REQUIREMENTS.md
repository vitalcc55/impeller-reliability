# Non-functional Requirements

- Windows 10/11 x64, offline, single-user; один portable EXE.
- UI показывается до полной загрузки SciPy; startup, worker-ready и размер измеряются отдельно.
- Строгая типизация и versioned contracts; deterministic checks и bounded shutdown.
- SQLite: FK, WAL, one writer, short transactions, forward-only migrations, backup before migration.
- Security: sandboxed renderer, no network/API/server, integrity-checked worker/imports.
- Цели исследования: startup <5 s, portable <200 MB, 10k Monte Carlo <10 s; не гарантии до измерений.
