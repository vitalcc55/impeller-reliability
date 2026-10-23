# Contribution Guide

Работа ведётся Windows-native через PowerShell. Перед изменением прочитайте цепочку ближайших `AGENTS.md` и профильный owner-документ. Для продуктовых/UI-изменений обязательны `PRODUCT.md` и `DESIGN.md`. Устанавливайте зависимости только через `pnpm install --frozen-lockfile` и `uv sync --frozen`. Изменение архитектуры обновляет затронутые карты в том же change set. Правила написания тестов и выбора проверок находятся в [tests/AGENTS.md](tests/AGENTS.md).
