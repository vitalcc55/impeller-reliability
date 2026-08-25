# Contribution Guide

Работа ведётся Windows-native через PowerShell. Перед изменением прочитайте цепочку ближайших `AGENTS.md` и профильный owner-документ. Для продуктовых/UI-изменений обязательны `PRODUCT.md` и `DESIGN.md`. Устанавливайте зависимости только через `pnpm install --frozen-lockfile` и `uv sync --frozen`. Изменение архитектуры обновляет затронутые карты в том же change set; исправление получает regression test. Минимальный gate — `pnpm check`; изменения упаковки требуют packaged smoke.
