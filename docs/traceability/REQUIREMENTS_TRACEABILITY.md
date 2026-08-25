# Requirements Traceability

| Requirement | M01 surface | Evidence |
| --- | --- | --- |
| Sandboxed Electron | BrowserWindow/preload | Playwright E2E |
| Narrow typed IPC | contracts/channels | Vitest + typecheck |
| JSONL worker | WorkerClient/worker main | pytest + E2E |
| Python-owned SQLite | sqlite_health | integration tests |
| Bounded shutdown/no orphan | WorkerClient/smoke | packaged smoke |
| Integrity worker | manifest/Main/afterPack | packaging |
| Offline/no TCP | CSP + no network code | packaged smoke |
| Portable x64 | electron-builder | real portable smoke |
| No business logic | capability/DB allowlists | source/test review |
