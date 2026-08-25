# IPC Protocol

Один долгоживущий worker; UTF-8 JSONL stdin/stdout; protocol v1. Request содержит `requestId`, конкретную `operation`, `revision`, `deadlineMs` и operation-specific `payload`. Response возвращает те же `requestId` и `revision`, затем operation-specific `result`/evidence/warnings либо typed error. Main связывает ответ с pending operation и применяет `RevisionGate`; несовпавшая revision отклоняется.

M01.1 allowlist: `system.handshake`, `system.ping`, `system.shutdown`, `storage.health`. TypeScript `WorkerOperationMap` и Python discriminated request union валидируют payload/result каждой операции; generic execute и `Record<string, unknown>` для известных результатов отсутствуют. Message limit — 1 MiB, deadline ≤30 s, неизвестные операции и лишние payload fields отклоняются, stack trace не передаётся Renderer.

Worker lifecycle передаётся отдельно через узкое событие status changed. Preload предоставляет `getStatus`, `ping`, controlled `restart`, `openLog` и подписку; произвольных IPC channel нет.

M02.1 добавляет operation-specific `project.create`, `project.open`, `project.close`, `project.getOverview`, `project.updateMetadata` и `project.createBackup`. Путь существует только в create/open payload Main → worker; остальные операции работают с активной ProjectSession. Renderer-facing `project.create/open` сами запускают системный диалог Main, а отмена возвращается typed `cancelled`.

Проектные ошибки: `project_locked`, `corrupt_project`, `incompatible_schema`, `revision_conflict`, `storage_error`. Response echo revision и pending request gate применяются без изменений.
