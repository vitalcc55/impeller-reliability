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
  Preload: "Sandboxed Preload: narrow window.impeller API"
  Main: "Electron Main: windows, dialogs, paths, worker lifecycle, PDF"
}

TypeScript: "TypeScript packages" {
  Contracts: "packages/contracts: Zod + IPC types"
  Application: "packages/application: orchestration, revisions, read models"
  Reporting: "packages/reporting: report renderer boundary"
}

Worker: "tools/python-worker" {
  Protocol: "Pydantic JSONL envelopes"
  Domain: "Future domain rules and calculations"
  Persistence: "sqlite3, migrations, repositories"
  Integration: "Future R130SH import/export"
}

Project: "Future *.irproj" {
  Database: "project.sqlite" { shape: cylinder }
  Assets: "immutable imports, documents, spectra, photos"
  Exports: "derived reports and packages"
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
Worker.Persistence -> Project.Assets
TypeScript.Application -> TypeScript.Reporting
Desktop.Main -> TypeScript.Reporting
TypeScript.Reporting -> Project.Exports

Desktop.Main -> PlanPackage
PlanPackage -> R130SH
R130SH -> RunPackage
RunPackage -> Desktop.Main
```

M01 реализует Renderer/Preload/Main, typed contracts, worker protocol и SQLite health. Предметные модули, project storage и R130SH packages остаются будущими границами, а не заглушками с фиктивным результатом.
