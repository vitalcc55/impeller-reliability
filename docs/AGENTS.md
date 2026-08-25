# Documentation ownership

## Canonical owners

- `PRODUCT.md` — продуктовая истина; `DESIGN.md` — дизайн-система.
- `docs/requirements/` и `docs/domain/` — требования и предметные правила.
- `docs/integration/` — IPC и R130SH file contracts.
- `docs/security/`, `docs/testing/`, `docs/packaging/`, `docs/observability/` — профильные owner-документы.
- `docs/architecture/` — только topology, boundaries, state и end-to-end flows; правила каталога в локальном `AGENTS.md`.
- `docs/backlog/OPEN_QUESTIONS.md` — только нерешённые продуктовые/методические вопросы.

Не создавать parallel status, generated inventory, historical snapshot, compatibility alias или второй список команд. Один факт имеет одного владельца; другие документы оставляют только ссылку и локальное следствие. Текущая документация описывает текущую систему, а не историю её создания.
