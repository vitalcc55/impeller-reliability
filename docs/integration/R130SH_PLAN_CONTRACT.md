# R130SH Plan Contract

`.r130plan` — ZIP с `manifest.json`, `plan.json`, `references.json`, `checksums.sha256`. Manifest содержит schema/package/project/campaign/specimen/plan identifiers, revision/hash, producer и UTC timestamp. Пакет не содержит Modbus, control words, secrets, внутренние пути или code. R130SH исполняет verified `executionTargets`.

M01 фиксирует только контракт документа; JSON Schema будет реализована в M04.
