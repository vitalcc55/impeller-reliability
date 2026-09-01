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

User -> Renderer: "Создать документ дела (M02.2B)"
Renderer -> Main: "typed metadata; createWithFile/attach без path"
Main -> User: "system file dialog + preliminary gate"
Main -> Worker: "approved source path только внутри worker request"
Worker -> Project: "revalidate → staged streaming SHA-256 → atomic rename → file row/audit"
Project -> Renderer: "metadata/file DTO + applicability + integrity/warnings; no path"
User -> Renderer: "Проверить или открыть managed copy"
Renderer -> Main: "verify/open by caseDocumentId"
Main -> Worker: "registry-owned resolve + containment/existence/SHA-256"
Worker -> Main: "verified absolute path only for Main"
Main -> User: "shell.openPath; Renderer получает typed outcome"

User -> Renderer: "Попытаться перейти с dirty draft"
Renderer -> User: "единый keep/discard guard; focus возвращается к исходному действию"
User -> Renderer: "validation/conflict/runtime failure"
Renderer -> User: "ввод сохранён; error class и доступное следующее действие"

User -> Renderer: "Выбрать candidate .r130run для проверки (M03A)"
Renderer -> Main: "selectAndStart; no path"
Main -> Worker: "dialog-approved read-only file"
Worker -> Worker: "outer hash → ZIP/inventory → payload integrity → covered semantics"
Worker -> Renderer: "progress + bounded report; no import/eligibility claim"

User -> Renderer: "Импортировать результат R130SH (M03B)"
Renderer -> Main: "selectAndStart; no path"
Main -> Worker: "approved ordinary file"
Worker -> Worker: "validate → streaming copy/SHA → staged revalidation"
Worker -> Project: "atomic archive + registry/inventory/projection/audit"
Project -> Renderer: "persisted run + integrity + source/analyst differences"
User -> Renderer: "Явно связать source specimen / разрешить поле"
Renderer -> Worker: "optimistic bind/resolution + actor/reason"
Worker -> Project: "binding mutation или append-only provenance; source unchanged"
User -> Renderer: "Закрыть/открыть дело"
Project -> Renderer: "тот же source/archive/provenance; broken archive локализован"

User -> Renderer: "Выбрать значения и выполнить расчёты (M04+)"
Renderer -> Worker: "r130sh_source + analyst_enrichment selection"
Worker -> Project: "AnalysisInputSnapshot + CalculationSnapshot"

User -> Renderer: "Запустить анализ/выпустить отчёт (M05+)"
Renderer -> Worker: "selected immutable inputs"
Worker -> Project: "analysis/report snapshot"
Project -> Main: "canonical report data"
Main -> User: "preview/exported document + SHA-256"
```

Browser preview воспроизводит renderer/project/diagnostics/document states через typed synthetic adapter. Он не запускает worker, не показывает file dialog, не открывает файлы и не доказывает persistence; create/update/attach/verify/archive/close/reopen подтверждают Electron E2E и packaged smoke.
