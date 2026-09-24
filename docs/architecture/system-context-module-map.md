# Карта системы и модулей

Карта отвечает только за текущий состав системы и крупные связи.

```d2
direction: down

Engineer: "Инженер / специалист лаборатории"
R130SH: "R130SH — отдельная программа стенда"
RunPackage: "producer .r130run (M03A validation / M03B import)"

Desktop: "apps/desktop" {
  Renderer: "React Renderer: формы, таблицы, графики, browser preview"
  Preload: "Sandboxed Preload: narrow commands + status event"
  Main: "Electron Main: windows, dialogs, lifecycle/restart, app protocol, CSP/fuses"
}

TypeScript: "TypeScript packages" {
  Contracts: "packages/contracts: operation map + Zod"
  Application: "packages/application: orchestration + revision gate"
  Reporting: "packages/reporting: report renderer boundary"
}

Worker: "tools/python-worker" {
  Protocol: "operation-specific Pydantic JSONL envelopes"
  Domain: "dossier + reliability evidence + pure Python RBD calculation"
  Persistence: "sqlite3, migrations, repositories"
  Integration: "R130SH M9a validator + import job/projection"
}

Project: "*.irproj — schema v1 project container" {
  Manifest: "project-manifest.json: container identity"
  Database: "project.sqlite schema v1: dossier + immutable R130SH source + versioned derived analysis + audit" { shape: cylinder }
  Lock: ".project.lock: OS-held session lock"
  Assets: "assets/documents: immutable managed copies"
  Imports: "imports/r130sh: exact immutable .r130run archives"
  Backups: "verified SQLite-only backups"
}

Engineer -> Desktop.Renderer
Desktop.Renderer -> Desktop.Preload
Desktop.Preload -> Desktop.Main
Desktop.Main -> TypeScript.Application
TypeScript.Application -> TypeScript.Contracts
Desktop.Main -> Worker.Protocol: "UTF-8 JSONL stdin/stdout"
Worker.Protocol -> Worker.Domain
Worker.Domain -> Worker.Persistence
Worker.Persistence -> Project.Database
Worker.Persistence -> Project.Manifest
Worker.Persistence -> Project.Lock
Worker.Persistence -> Project.Assets
Worker.Persistence -> Project.Imports
TypeScript.Application -> TypeScript.Reporting
Desktop.Main -> TypeScript.Reporting
TypeScript.Reporting -> Project.Exports

R130SH -> RunPackage
RunPackage -> Desktop.Main
Desktop.Main -> Worker.Protocol: "approved path over typed JSONL"
Worker.Protocol -> Worker.Integration: "validate/import job; no extraction tree"
```

Clean pre-release schema v1 содержит dossier, `r130sh_source` и `derived_analysis`, включая immutable `AnalysisInputSnapshot` и `CalculationSnapshot` для расчёта РБД. Main владеет file dialog; Python единолично владеет validation, classification rules, managed archive, расчётом и SQLite. Renderer получает compact pages/details и отправляет только typed decisions и выбранные расчётные входы. R130SH владеет package schema и первичными фактами; `TestCampaign` и аналитическая отчётность остаются будущими границами.
