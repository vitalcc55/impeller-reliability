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
| Bounded shutdown/no orphan | WorkerClient sequential dispatch + lifecycle drain/smoke | Vitest + packaged smoke |
| Integrity worker | manifest/Main/afterPack | packaging |
| ASAR integrity and production fuses | afterPack fuse policy | packaging + smoke |
| Offline/no TCP | production CSP + process-tree check | build + packaged smoke |
| Portable x64 | electron-builder | real portable smoke |
| Windows quality automation | `.github/workflows/quality.yml` | GitHub Actions status check |
| No business logic | capability/DB allowlists | source/test review |
| Atomic `.irproj` container | Python ProjectService staging/rename | pytest integration + packaged smoke |
| Python-owned project SQLite | ProjectSession/ProjectMigrator | pytest + E2E |
| Single writer OS lock | `msvcrt` lock + one active session | two-process contention/crash test |
| Optimistic project revision | `record_revision` command contract | pytest + Electron E2E |
| Append-only audit | canonical schema contract + transaction + UPDATE/DELETE triggers | pytest integration |
| Audit evidence and rollback | versioned evidence-chain validation + changed-fields before/after + atomic transaction | pytest integration |
| Bounded stateful operations | sequential dispatch + domain checkpoints + larger transport timeout + worker termination | Vitest + pytest injected deadlines |
| Unsaved draft lifecycle | renderer draft guard + detached/revision-checked reattach | Electron E2E |
| Migration backup/rollback | SQLite Backup API + forward migrator | pytest integration |
| Main-owned project paths | dialogs + recent allowlist | contracts + E2E + preload review |
| Project persistence packaged | create/update/close/reopen smoke | win-unpacked + portable smoke |
| Existing project validation | bounded manifest + reserved topology + immutable SQLite schema/evidence | pytest corruption/link/deadline integration |
| Manual backup request atomicity | copy + quick_check + SHA-256 + final deadline owned cleanup | hash-timeout/read-error pytest integration |
| Permanently detached workspace exit | confirmed Renderer-only local discard | Electron E2E moved-project recovery |
| Published project schema integrity | exact schema objects/triggers/migration ledger before lock and WAL | pytest mutation integration |
| Canonical UTC project timestamps | Python manifest/schema/evidence validation + Zod/Pydantic boundary | pytest mutation + Vitest contract |
