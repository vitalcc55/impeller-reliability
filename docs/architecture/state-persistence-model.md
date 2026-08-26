# Модель состояния и хранения

Карта отделяет persisted truth, runtime state, renderer drafts и derived artifacts.

```d2
direction: down

Persisted: "M02.1 project source of truth" {
  Sqlite: "project.sqlite: metadata, record_revision, audit" { shape: cylinder }
  Manifest: "project-manifest.json: container identity"
  Backups: "verified migration/manual SQLite backups"
  Assets: "assets/documents: reserved for M02.2 sources"
}

WorkerRuntime: "Python worker runtime" {
  Connection: "one ProjectSession + sequential SQLite writer"
  Lock: "read-only identity preflight → Windows OS-held lock"
  Jobs: "request/job progress + cancellation"
  Canonical: "validated domain values and report inputs"
}

MainRuntime: "Electron Main runtime" {
  WorkerProcess: "one worker process + controlled restart"
  Lifecycle: "starting/ready/unavailable/stopping/stopped"
  Queue: "one active + one queued; backpressure; deadline at dispatch"
  Pending: "one sent requestId + operation + revision + domain/transport deadlines"
  Paths: "approved file/project paths"
  Recent: "recent project allowlist JSON"
  Status: "handshake and health read model"
}

RendererRuntime: "React runtime" {
  Drafts: "unsaved form drafts survive detached worker state"
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
Persisted.Assets -> WorkerRuntime.Canonical
Persisted.Manifest -> WorkerRuntime.Connection
WorkerRuntime.Lock -> Persisted.Sqlite: "one writer"
WorkerRuntime.Canonical -> Persisted.Sqlite
WorkerRuntime.Canonical -> MainRuntime.Status
MainRuntime.Status -> RendererRuntime.QueryCache
MainRuntime.Lifecycle -> MainRuntime.Status
MainRuntime.Lifecycle -> MainRuntime.Queue: "close intake → drain → shutdown"
MainRuntime.Queue -> MainRuntime.Pending
MainRuntime.Pending -> MainRuntime.Status: "validated operation-specific response"
RendererRuntime.Drafts -> WorkerRuntime.Canonical: "validate/command; never direct persistence"
Persisted.Sqlite -> Derived.Exports: "through immutable report snapshot"
Persisted.Assets -> Derived.Exports
RendererRuntime.Preview -> RendererRuntime.QueryCache: "synthetic DEV state only"
```

App-level `health.sqlite` остаётся отдельной инфраструктурной диагностикой. M02.1 project truth находится только в `.irproj`; Renderer хранит draft, Main — allowlist недавних путей и последовательную очередь, Python — активную ProjectSession. Existing project становится writable только после read-only identity/topology/schema/evidence preflight и повторной проверки под OS lock до WAL. Stateful domain timeout проверяется до commit; graceful lifecycle сначала дренирует принятую bounded-очередь, а indeterminate transport timeout завершает worker и однозначно снимает ProjectSession/OS lock. Потеря worker не размонтирует форму: повторное присоединение возможно только при совпадении `projectId` и `record_revision`; permanently detached draft можно удалить локально без изменения project truth или recent list. Browser preview ничего не сохраняет и не является evidence persistence.
