# Investigate Startup

1. Run `pnpm build`.
2. Run `node scripts/codex/diagnose-electron.mjs`.
3. Inspect URL/body/console/pageErrors and app JSONL log.
4. Verify `.venv` or packaged worker path/manifest.
5. Reproduce through `pnpm test:e2e`; preserve trace/screenshot on failure.
