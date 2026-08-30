# Модель состояния и хранения

Карта отделяет persisted truth, runtime state, renderer drafts и derived artifacts.

```d2
direction: down

Persisted: "Project source of truth" {
  Sqlite: "project.sqlite schema v1: metadata, analyst_enrichment, file registry, revisions, audit" { shape: cylinder }
  Manifest: "project-manifest.json: container identity"
  Backups: "verified SQLite-only backups; future migration backups"
  Assets: "assets/documents: immutable managed copies"
}

WorkerRuntime: "Python worker runtime" {
  Connection: "one ProjectSession + sequential SQLite writer"
  Lock: "bounded read-only validation → Windows OS-held lock"
  Jobs: "one transient R130SH validation job + request progress/cancellation"
  Canonical: "validated domain values and report inputs"
}

MainRuntime: "Electron Main runtime" {
  WorkerProcess: "one worker process + controlled restart"
  Lifecycle: "starting/ready/unavailable/stopping/stopped + one close state"
  Queue: "one active + one queued; backpressure; deadline at dispatch"
  Pending: "one sent requestId + operation + revision + domain/transport deadlines"
  Paths: "approved file/project paths"
  Recent: "recent project allowlist JSON"
  Status: "handshake and health read model"
}

RendererRuntime: "React runtime" {
  Drafts: "one active owner: clean/dirty/validating/saving/conflict/detached"
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

App-level `health.sqlite` остаётся отдельной инфраструктурной диагностикой. Project truth находится только в `.irproj`: первая публикуемая schema v1 хранит metadata, dossier, CaseDocument, immutable file registry и audit; будущие `r130sh_source` и `derived_analysis` будут отдельными владельцами. Невыпущенные промежуточные schema не мигрируются и не получают compatibility layer.

Renderer хранит один активный draft-owner и replaceable query cache, Main — разрешённые пути, lifecycle и очередь, Python — активную сериализованную ProjectSession. Dirty draft проходит `validate → save → persisted revision`; validation/conflict/transport failure оставляют его dirty. Попытка navigation/select/attach/archive/close/open/restart/window close требует keep/discard/save решения по существующему общему guard. Потеря worker переводит draft в detached, но не размонтирует форму; permanently detached draft можно удалить локально без изменения project truth или recent list. Recovery checkpoint после crash всего Electron остаётся отдельным будущим уровнем и не является domain autosave.

CaseDocument metadata и applicability меняются одной revision/audit transaction. Attach публикует staged managed file атомарным rename и регистрирует immutable row; успешный ответ означает существующие совпадающие DB/file size/hash. Failed response не оставляет зарегистрированной ссылки на непроверенный файл. Orphan staging не является persisted document и узко очищается при открытии. Missing/modified managed copy вычисляется как локальный integrity status после открытия ProjectSession и не делает всю schema/evidence corrupt. Browser preview ничего не сохраняет и не является evidence persistence.

M03A `RunPackageValidationJob` живёт только в памяти worker: caller-created UUID связывает retry, terminal result хранится до явного discard или атомарной замены, application restart/window shutdown выполняют bounded cooperative stop. Renderer хранит только transient копию последнего job/report для отображения; active snapshot после worker loss помечается прерванным, а уже terminal report может оставаться видимым до clear/retry и не становится persisted evidence. Report, progress и approved source path не входят в Project truth, recent paths, audit или logs; path существует только внутри Main/worker runtime. Открытый Project может сосуществовать с validation job, но между job и `project.sqlite` нет write edge.
