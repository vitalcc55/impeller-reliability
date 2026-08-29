# Requirements Traceability

| Requirement | Implementation surface | Evidence |
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
| Future migration backup/rollback | retained SQLite Backup API + forward migrator; no predecessor for first released v1 | future migration pytest integration |
| Main-owned project paths | dialogs + recent allowlist | contracts + E2E + preload review |
| Project persistence packaged | create/update/close/reopen smoke | win-unpacked + portable smoke |
| Existing project validation | bounded manifest + reserved topology + immutable SQLite schema/evidence | pytest corruption/link/deadline integration |
| Manual backup request atomicity | copy + quick_check + SHA-256 + final deadline owned cleanup | hash-timeout/read-error pytest integration |
| Permanently detached workspace exit | confirmed Renderer-only local discard | Electron E2E moved-project recovery |
| Published project schema integrity | exact schema objects/triggers/migration ledger before lock and WAL | pytest mutation integration |
| Canonical UTC project timestamps | Python manifest/schema/evidence validation + Zod/Pydantic boundary | pytest mutation + Vitest contract |
| Analyst dossier separation | `analyst_enrichment` Customer/WheelModel/Specimen; no R130SH plan owner | owner-doc review + schema/IPC tests |
| First released project schema v1 | one atomic full schema, exact published contract, no unreleased compatibility input | pytest create/reopen/newer/corrupt schema |
| Optimistic dossier revisions | expected revision; no-op leaves revision/audit unchanged | pytest + Electron E2E |
| Reversible archive invariants | archived model/specimen rules without hard delete | pytest domain/integration |
| Incomplete dossier warnings | non-blocking entity warnings without readiness score | pytest + UI/E2E |
| Shared draft lifecycle | one draft-owner contract across metadata/customer/model/specimen | Vitest + Electron E2E |
| Dossier persistence packaged | customer/model/specimen close/reopen | win-unpacked + portable smoke |
| CaseDocument separation | analyst-owned records/tables only; no `r130sh_source` or polymorphic link table | schema + owner-doc review |
| Managed document confinement | Main dialog + Python revalidation + project-relative immutable registry | Vitest + pytest containment/signature tests |
| File attach postconditions | staging/streaming SHA-256/atomic rename/transaction/cleanup | pytest failure/deadline/recovery integration |
| Document optimistic revision/audit | metadata+applicability transaction; immutable attach event; no-op/conflict invariants | pytest audit reconstruction + Electron E2E |
| Document integrity isolation | missing/modified status without corrupting ProjectSession | pytest close/reopen + Electron E2E |
| Renderer path secrecy | DTO/preload omit source/absolute/managed path; open resolves only in Main | Zod/Vitest + IPC review |
| Shared document draft/focus | one draft owner; transition confirmation restores focus and preserves input | Electron E2E keyboard/dirty/restart/close |
| Narrow document workspace | adaptive master-detail and wrapping metadata at 640 px | Browser + Electron E2E |
| Honest backup scope | UI/docs say SQLite-only; full transfer copies closed `.irproj` | copy assertions + E2E |
| Pinned R130SH synthetic contract | exact fixture snapshot + `UPSTREAM_SOURCE.json` | offline blob/SHA-256 drift test |
| Bounded read-only package validation | Python Integration validator + one in-memory job | pytest ZIP/semantic/resource/cancel matrix |
| Independent package identity | upstream UUID v4/v7 types separate from local entity UUID v4 | Zod/Pydantic regression tests |
| Strict ZIP and payload integrity | raw stored/DEFLATE stream, CRC/size/SHA-256, inventory and path profile | negative synthetic package matrix |
| Honest semantic coverage | covered/not_available/contract_gap report | frozen-shape/value + gap tests |
| No Project mutation in M03A | validation has no ProjectService/SQLite edge; schema remains v1 | overview before/after + pytest + E2E/packaged smoke |
| Validation job lifecycle | start/get/cancel/discard, atomic terminal replacement, bounded shutdown | pytest races + Vitest + Electron E2E |
| Run-package path secrecy | Main dialog injects path only into worker payload | Main seam + Zod/Preload review + E2E |
| Validation diagnostics UX | progress, verdicts, provenance, findings, focus and 640 px | Browser QA + Electron E2E |
| No import or analysis claim | empty supported schema claims; no import fields/tables/receipts | contract/source review + negative UI/E2E |
