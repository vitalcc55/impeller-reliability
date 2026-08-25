# Карта системы и модулей

Карта отвечает только за текущий состав системы и крупные связи.

```d2
direction: down

Engineer: "Инженер / специалист лаборатории"
R130SH: "R130SH — отдельная программа стенда"
PlanPackage: "*.r130plan (future M04)"
RunPackage: "*.r130run (future M04)"

Desktop: "apps/desktop" {
  Renderer: "React Renderer: формы, таблицы, графики, browser preview"
  Preload: "Sandboxed Preload: narrow commands + status event"
  Main: "Electron Main: windows, lifecycle/restart, app protocol, CSP/fuses, PDF"
}

TypeScript: "TypeScript packages" {
  Contracts: "packages/contracts: operation map + Zod"
  Application: "packages/application: orchestration + revision gate"
  Reporting: "packages/reporting: report renderer boundary"
}

Worker: "tools/python-worker" {
  Protocol: "operation-specific Pydantic JSONL envelopes"
  Domain: "Future domain rules and calculations"
  Persistence: "sqlite3, migrations, repositories"
  Integration: "Future R130SH import/export"
}

Project: "*.irproj — M02.1 container" {
  Manifest: "project-manifest.json: container identity"
  Database: "project.sqlite: metadata, migrations, audit" { shape: cylinder }
  Lock: ".project.lock: OS-held session lock"
  Assets: "assets/documents (empty until M02.2)"
  Backups: "verified SQLite backups"
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
TypeScript.Application -> TypeScript.Reporting
Desktop.Main -> TypeScript.Reporting
TypeScript.Reporting -> Project.Exports

Desktop.Main -> PlanPackage
PlanPackage -> R130SH
R130SH -> RunPackage
RunPackage -> Desktop.Main
```

M02.1 реализует container/manifest, ProjectSession, OS lock, schema v1, migration backup, metadata revision и append-only audit. Customer/WheelModel/Specimen/TestCampaign/SourceDocument, предметные модули и R130SH packages остаются будущими границами, а не заглушками.
