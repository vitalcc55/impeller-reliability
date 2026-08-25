# Requirements Traceability

| Requirement | M01 surface | Evidence |
| --- | --- | --- |
| Sandboxed Electron | BrowserWindow/preload | Playwright E2E |
| Narrow typed IPC | contracts/channels | Vitest + typecheck |
| Operation-specific result and response revision | operation map/RevisionGate | Vitest + pytest + E2E |
| JSONL worker | WorkerClient/worker main | pytest + E2E |
| Python-owned SQLite | sqlite_health | integration tests |
| WAL health invariant | sqlite_health verdict | pytest |
| Worker crash/restart state | lifecycle event/Main/Renderer | Electron E2E |
| Bounded shutdown/no orphan | WorkerClient/smoke | packaged smoke |
| Integrity worker | manifest/Main/afterPack | packaging |
| ASAR integrity and production fuses | afterPack fuse policy | packaging + smoke |
| Offline/no TCP | production CSP + process-tree check | build + packaged smoke |
| Portable x64 | electron-builder | real portable smoke |
| Windows quality automation | `.github/workflows/quality.yml` | GitHub Actions status check |
| No business logic | capability/DB allowlists | source/test review |
