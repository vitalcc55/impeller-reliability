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

Worker -> Main: "unexpected close/error"
Main -> Renderer: "unavailable status event"
User -> Renderer: "Перезапустить ядро"
Renderer -> User: "dirty draft guard"
Renderer -> Main: "controlled restart"
Main -> Worker: "close intake → drain accepted operations → bounded stop"
Main -> Worker: "one new process after old close"
Worker -> Renderer: "new handshake/health + revision-checked reattach"

User -> Renderer: "Создать/открыть проект"
Renderer -> Main: "typed command; no path"
Main -> Worker: "approved dialog/recent path + project command"
Worker -> Project: "bounded structure/schema/evidence validation → OS lock → WAL"
Project -> Worker
Worker -> Renderer: "ProjectOverview"
Worker -> Renderer: "failed reattach: detached local draft"
User -> Renderer: "confirmed local discard; no project write"
User -> Renderer: "Изменить metadata с record_revision"
Renderer -> Worker: "project.updateMetadata"
Worker -> Project: "changed fields + evidence audit, one transaction"
User -> Renderer: "Закрыть окно с dirty draft"
Renderer -> Main: "acknowledge delivery; save drains before decision"
Renderer -> User: "confirm/cancel only for remaining local draft"
User -> Renderer: "Закрыть и открыть повторно"
Worker -> Project: "release/reacquire OS lock"
Project -> Renderer: "persisted revision and values"

User -> Renderer: "Заполнить сведения дела (M02.2A)"
Renderer -> Worker: "CustomerProfile / WheelModel / Specimen + expected revision"
Worker -> Project: "analyst_enrichment + changed-fields audit, one transaction"
Project -> Renderer: "detail/list DTO + completeness warnings"

R130SH -> Main: "result package после независимого испытания (M03)"
Main -> Worker: "staged package after path/size gate"
Worker -> Project: "checksums, validation, immutable import revision"
Worker -> Renderer: "receipt, differences, completeness/classification"

User -> Renderer: "Выбрать значения и выполнить расчёты (M04+)"
Renderer -> Worker: "r130sh_source + analyst_enrichment selection"
Worker -> Project: "AnalysisInputSnapshot + CalculationSnapshot"

User -> Renderer: "Запустить анализ/выпустить отчёт (M05+)"
Renderer -> Worker: "selected immutable inputs"
Worker -> Project: "analysis/report snapshot"
Project -> Main: "canonical report data"
Main -> User: "preview/exported document + SHA-256"
```

Browser preview воспроизводит renderer/project/diagnostics states через typed synthetic adapter. Он не запускает worker, не открывает файлы и не доказывает persistence; create/update/close/reopen подтверждают Electron E2E и packaged smoke.
