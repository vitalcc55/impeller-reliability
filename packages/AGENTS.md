# TypeScript package rules

## Scope

Правила действуют для `packages/contracts`, `packages/application` и `packages/reporting`.

## Ownership

- `contracts` владеет Zod/TypeScript boundary types и не зависит от React, Electron, SQLite или application.
- `application` владеет orchestration, revisions, jobs и view models; инженерные формулы запрещены.
- `reporting` владеет адаптерами immutable report snapshots; Electron printing остаётся в Main.

Публичные границы типизируются явно; `any`, unchecked casts и generic operations запрещены. Новый package появляется только при доказанной самостоятельной ответственности, а не ради структуры.
