# Границы зависимостей

Карта отвечает только на вопрос, кому от кого разрешено зависеть.

```d2
direction: right

Renderer: "React Renderer"
Preload: "Sandboxed Preload"
Main: "Electron Main"
TsApplication: "TypeScript Application"
Contracts: "Typed Contracts"
Reporting: "Reporting adapters"
WorkerProtocol: "Python protocol/lifecycle"
WorkerApplication: "Python application/domain"
Persistence: "Python SQLite adapters"
Electron: "Electron / Node API"
React: "React / Mantine"
SQLite: "sqlite3"
Network: "HTTP/TCP"
StandControl: "Modbus / stand control"

Renderer -> Contracts
Renderer -> React
Preload -> Contracts
Preload -> Electron
Main -> Contracts
Main -> Electron
Main -> TsApplication
Main -> Reporting
Main -> WorkerProtocol: "JSONL only"
TsApplication -> Contracts
TsApplication -> Reporting
WorkerProtocol -> WorkerApplication
WorkerApplication -> Persistence
Persistence -> SQLite

Renderer -> Electron: "FORBIDDEN: no Node/FS/process" { style.stroke-dash: 5 }
Renderer -> WorkerProtocol: "FORBIDDEN: Main owns worker" { style.stroke-dash: 5 }
Renderer -> SQLite: "FORBIDDEN" { style.stroke-dash: 5 }
Preload -> SQLite: "FORBIDDEN" { style.stroke-dash: 5 }
TsApplication -> SQLite: "FORBIDDEN" { style.stroke-dash: 5 }
TsApplication -> WorkerApplication: "FORBIDDEN: no duplicated formulas" { style.stroke-dash: 5 }
Contracts -> React: "FORBIDDEN" { style.stroke-dash: 5 }
Contracts -> Electron: "FORBIDDEN" { style.stroke-dash: 5 }
WorkerApplication -> Electron: "FORBIDDEN" { style.stroke-dash: 5 }
Main -> SQLite: "FORBIDDEN: Python owns storage" { style.stroke-dash: 5 }
Main -> Network: "FORBIDDEN: offline product" { style.stroke-dash: 5 }
WorkerApplication -> Network: "FORBIDDEN" { style.stroke-dash: 5 }
Main -> StandControl: "FORBIDDEN" { style.stroke-dash: 5 }
WorkerApplication -> StandControl: "FORBIDDEN" { style.stroke-dash: 5 }
```

Изменение направления зависимости требует обновления этой карты и ADR. Browser preview заменяет только preload API синтетическим typed adapter в `import.meta.env.DEV`; production graph не меняется.
