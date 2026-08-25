# Сквозные пользовательские потоки

Карта показывает только основные сценарии от действия пользователя до наблюдаемого результата.

```d2
direction: right

User: "Инженер"
Renderer: "Renderer"
Main: "Electron Main"
Worker: "Python worker"
Project: "Project storage"
R130SH: "R130SH"

User -> Renderer: "Открыть приложение"
Renderer -> Main: "system.getStatus"
Main -> Worker: "system.handshake + storage.health"
Worker -> Main: "version/capabilities/SQLite health"
Main -> Renderer: "typed runtime status"
Renderer -> User: "готовность или actionable error"

User -> Renderer: "Создать/открыть проект (M02)"
Renderer -> Main: "approved dialog/path"
Main -> Worker: "project command"
Worker -> Project: "migration/lock/read model"
Project -> Worker
Worker -> Renderer: "canonical project read model"

User -> Renderer: "Утвердить план (M03/M04)"
Renderer -> Worker: "source values + references"
Worker -> Project: "immutable plan revision"
Worker -> Main: "verified *.r130plan payload"
Main -> R130SH: "atomic file export"

R130SH -> Main: "*.r130run (M04)"
Main -> Worker: "staged package after path/size gate"
Worker -> Project: "checksums, validation, immutable import revision"
Worker -> Renderer: "receipt, differences, completeness/classification"

User -> Renderer: "Запустить анализ/выпустить отчёт (M05+)"
Renderer -> Worker: "selected immutable inputs"
Worker -> Project: "analysis/report snapshot"
Project -> Main: "canonical report data"
Main -> User: "preview/exported document + SHA-256"
```

Browser preview воспроизводит только renderer-состояния через `?preview=ready` и `?preview=unavailable`. Он не запускает worker, не открывает файлы и не доказывает packaged behavior; для этого остаются Electron E2E и portable smoke.
