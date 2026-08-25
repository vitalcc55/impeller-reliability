# IPC Protocol

Один долгоживущий worker; UTF-8 JSONL stdin/stdout; protocol v1. Request: requestId, operation, revision, deadlineMs, payload. Response: ok + result/evidence/warnings или typed error. Message limit 1 MiB, deadline ≤30 s. Unknown operations rejected. M01 allowlist: handshake, ping, shutdown, storage health. Stack traces не передаются Renderer.
