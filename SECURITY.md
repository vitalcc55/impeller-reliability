# Security Policy

Приложение офлайн и не предоставляет сетевой API. Renderer работает с `nodeIntegration=false`, `contextIsolation=true`, `sandbox=true`; permissions, navigation и `window.open` запрещены. Main владеет процессами/файлами, worker запускается только после SHA-256 integrity check. Уязвимости не публикуйте в issue с проектными данными; передайте владельцу репозитория приватное описание и воспроизводимый сценарий без секретов/ПДн.
