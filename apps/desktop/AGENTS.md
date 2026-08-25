# Desktop rules

## Scope

Правила действуют для `apps/desktop`: Electron Main, Preload и Renderer. Архитектурные направления принадлежат `docs/architecture/dependency-boundaries-rules.md`; здесь остаются локальные правила реализации.

## Boundaries

- Renderer зависит только от typed contracts, React/Mantine и renderer-owned UI code. Node, filesystem, SQLite и child processes запрещены.
- Preload sandboxed и публикует только явные методы `window.impeller`; generic `send/invoke(channel)` запрещён.
- Main единолично владеет окнами, файлами, внешним открытием, worker lifecycle, integrity checks и будущим PDF printing.
- Browser preview существует только при `import.meta.env.DEV`, использует typed synthetic adapter и не импортирует Electron/worker/storage code.

## UI and Impeccable

- Перед новым значимым экраном читать `PRODUCT.md` и `DESIGN.md`; работать через подходящий Impeccable flow.
- Дизайн-система растёт из подтверждённых повторений. Mantine — primitive library, не источник продуктового дизайна.
- Семантические tokens живут у renderer theme/styles; не дублировать их в компонентах или документах.
- Изменённую поверхность проверять `pnpm dev:preview` во встроенном Browser на desktop и узком viewport, затем запускать detector один раз.

## Checks

Для renderer/preload/Main: `pnpm typecheck`, `pnpm lint`, `pnpm test`, `pnpm test:e2e`; packaging-boundary changes дополнительно требуют packaged smoke.
