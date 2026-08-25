# Observability Architecture

Main writes low-noise JSONL events with schemaVersion/timestamp/service/severity/component/event/request/error/details. Worker stderr is routed into Main log; stdout stays protocol-only. Validation artifacts live under `.tmp/.codex/evidence`; targeted `node scripts/codex/diagnose-electron.mjs` captures renderer URL/DOM/console/page errors. Project audit will be separate and append-only in M02+.
