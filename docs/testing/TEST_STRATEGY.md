# Test Strategy

TypeScript unit covers contracts/path/revision logic; Python unit/property/golden covers rules; integration covers worker JSONL and SQLite; Browser preview проверяет renderer visual/interactive states; Playwright Electron E2E proves renderer/preload/process boundaries; packaging smoke proves onedir, `win-unpacked` and portable. Critical algorithms require approved golden fixtures and branch/invariant coverage. Python coverage gate M01 — 85%; фактическое подтверждение даёт текущий `pnpm check`, а не отдельный status-документ.
