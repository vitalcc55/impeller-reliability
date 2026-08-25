# Impeller Reliability

Локальное Windows-приложение для подготовки испытаний, анализа результатов и выпуска воспроизводимой документации по надёжности рабочих колёс вентиляторов.

Продукт строится как offline desktop modular monolith: sandboxed React renderer обращается через узкий Electron Preload к Main, а инженерные правила и SQLite принадлежат отдельному Python worker по UTF-8 JSONL без HTTP и сетевого backend.

Продуктовый контекст находится в [PRODUCT.md](PRODUCT.md), дизайн-система — в [DESIGN.md](DESIGN.md), архитектурные границы — в [docs/architecture](docs/architecture), правила разработки и проверочные команды — в [AGENTS.md](AGENTS.md).

## Proprietary source code

Исходный код опубликован только для портфолио, просмотра и демонстрации. Разрешение на использование, копирование, изменение, распространение или коммерческую эксплуатацию не предоставляется. Полные условия находятся в [LICENSE](LICENSE).

Copyright © 2026 Власов Виталий Андреевич. All rights reserved.
