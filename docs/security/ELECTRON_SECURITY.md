# Electron Security

Required window flags: nodeIntegration false, contextIsolation true, sandbox true, webSecurity true, insecure content false. CSP разрешает same-origin resources и локальный Vite WebSocket только на `127.0.0.1:5173` для renderer preview; production code не инициирует network connections. External navigation/window.open and permissions are denied. Preload exposes only typed methods. Production keeps ASAR integrity and disables signing only as an explicitly recorded M01 limitation.
