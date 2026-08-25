# Threat Model

Assets: project DB, immutable imports, reports, source documents and calculation provenance. Trust boundaries: untrusted renderer content, user-selected files, Main↔worker JSONL, external R130SH packages. Primary threats: renderer privilege escalation, generic IPC, worker substitution, ZIP traversal/bombs, contract confusion, CSV injection, stale response, data tampering and orphan processes. Controls are split across Electron hardening, integrity-checked worker, schema/checksum validation and immutable snapshots.

M02.1 controls project-path confusion by allowing create/open selection only in Main; recent paths are Main-owned allowlisted entries. Python rejects non-absolute/non-`.irproj` paths, acquires an OS-held Windows lock before reading the container, checks strict manifest, SQLite `application_id`/schema/integrity и projectId equality. Atomic staging prevents a partial final project; a newer schema is never modified.
