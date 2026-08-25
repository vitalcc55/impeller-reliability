# Structured Event Schema

M01 fields: `schemaVersion`, `timestampUtc`, `service`, `severity`, `component`, `event`, optional `requestId`, `errorCode`, `details`. Do not log document content, secrets or PII. State transitions/outcomes are logged; heartbeat/tick exhaust is forbidden. Request/job/project identifiers are added when those concepts appear.
