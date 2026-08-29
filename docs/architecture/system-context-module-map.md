# Карта системы и модулей

Карта отвечает только за текущий состав системы и крупные связи.

```d2
direction: down

Engineer: "Инженер / специалист лаборатории"
R130SH: "R130SH — отдельная программа стенда"
RunPackage: "candidate .r130run (M03A read-only validation)"

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
  Domain: "analyst enrichment: dossier + case documents; future calculations"
  Persistence: "sqlite3, migrations, repositories"
  Integration: "R130SH contract snapshot + read-only validator/job; future import"
}

Project: "*.irproj — schema v1 project container" {
  Manifest: "project-manifest.json: container identity"
  Database: "project.sqlite: schema v1 metadata, analyst enrichment, file registry, audit" { shape: cylinder }
  Lock: ".project.lock: OS-held session lock"
  Assets: "assets/documents: immutable managed copies"
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
TypeScript.Application -> TypeScript.Reporting
Desktop.Main -> TypeScript.Reporting
TypeScript.Reporting -> Project.Exports

R130SH -> RunPackage
RunPackage -> Desktop.Main
Desktop.Main -> Worker.Protocol: "approved path over typed JSONL"
Worker.Protocol -> Worker.Integration: "validate job; no extraction"
```

Первая публикуемая schema v1 содержит Project metadata/audit и редактируемые CustomerProfile/WheelModel/Specimen/CaseDocument. Main владеет file dialog и external open; Python единолично владеет SQLite/file registry. M03A Integration читает candidate `.r130run` отдельно от ProjectSession, не извлекает его и возвращает transient report; стрелки к Project storage у этого потока нет. Renderer не получает абсолютный путь. R130SH остаётся владельцем package schema, первичных фактов и будущего `ImportedRunPlanSnapshot`; importer, `TestCampaign` и расчётные модули остаются будущими границами, а не заглушками.
