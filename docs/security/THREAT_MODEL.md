# Threat Model

Assets: project DB, immutable imports, reports, source documents and calculation provenance. Trust boundaries: untrusted renderer content, user-selected files, Main↔worker JSONL, external R130SH packages. Primary threats: renderer privilege escalation, generic IPC, worker substitution, ZIP traversal/bombs, contract confusion, CSV injection, stale response, data tampering and orphan processes. Controls are split across Electron hardening, integrity-checked worker, schema/checksum validation and immutable snapshots.
