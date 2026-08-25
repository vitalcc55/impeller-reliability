# Модель состояния и хранения

Карта отделяет persisted truth, runtime state, renderer drafts и derived artifacts.

```d2
direction: down

Persisted: "Future project source of truth" {
  Sqlite: "project.sqlite: entities, revisions, audit, snapshots" { shape: cylinder }
  Imports: "imports/r130sh: immutable source packages + SHA-256"
  Assets: "photos, spectra, documents"
  Manifest: "project-manifest.json"
  Backups: "migration/project backups"
}

WorkerRuntime: "Python worker runtime" {
  Connection: "one sequential SQLite writer"
  Jobs: "request/job progress + cancellation"
  Canonical: "validated domain values and report inputs"
}

MainRuntime: "Electron Main runtime" {
  WorkerProcess: "one worker process + controlled restart"
  Lifecycle: "starting/ready/unavailable/stopping/stopped"
  Pending: "requestId + operation + revision + deadline"
  Paths: "approved file/project paths"
  Status: "handshake and health read model"
}

RendererRuntime: "React runtime" {
  Drafts: "unsaved form drafts"
  QueryCache: "replaceable read-model cache"
  Shell: "navigation and temporary UI preferences"
  Preview: "DEV-only synthetic browser adapter"
}

Derived: "Replaceable outputs" {
  Cache: "project cache"
  Exports: "PDF/DOCX/XLSX/CSV/JSON/PNG/SVG"
  Diagnostics: "bounded logs, traces, smoke artifacts"
}

Persisted.Sqlite -> WorkerRuntime.Connection
Persisted.Imports -> WorkerRuntime.Canonical
Persisted.Assets -> WorkerRuntime.Canonical
WorkerRuntime.Canonical -> Persisted.Sqlite
WorkerRuntime.Canonical -> MainRuntime.Status
MainRuntime.Status -> RendererRuntime.QueryCache
MainRuntime.Lifecycle -> MainRuntime.Status
MainRuntime.Pending -> MainRuntime.Status: "validated operation-specific response"
RendererRuntime.Drafts -> WorkerRuntime.Canonical: "validate/command; never direct persistence"
Persisted.Sqlite -> Derived.Exports: "through immutable report snapshot"
Persisted.Assets -> Derived.Exports
RendererRuntime.Preview -> RendererRuntime.QueryCache: "synthetic DEV state only"
```

M01 persistent state ограничен инфраструктурной `schema_info` в health database; положительный verdict требует foreign keys, `quick_check`, ожидаемую schema version и WAL. Lifecycle/revision являются runtime state и не сохраняются. Browser preview ничего не сохраняет и не является evidence предметного расчёта.
