# Electron Security

Required window flags: `nodeIntegration=false`, `contextIsolation=true`, `sandbox=true`, `webSecurity=true`, `allowRunningInsecureContent=false`. External navigation, `window.open` и permissions запрещены. Preload exposes only typed system/project methods/events; generic channel и передача произвольного project path запрещены. Запрос закрытия окна является отдельным Main → Renderer event: dirty draft требует явного решения, а Renderer может подтвердить только закрытие приложения, не произвольную системную операцию.

Development HTML разрешает Vite WebSocket только на `127.0.0.1:5173`. Packaged renderer обслуживается только из `app.asar` через ограниченный `impeller://app/` handler; path traversal и другой host отклоняются. Production build проверяет отдельную CSP с `connect-src 'none'` и без network origin, а renderer не получает расширенных привилегий `file://`.

После упаковки `@electron/fuses` явно отключает `RunAsNode`, `NODE_OPTIONS`, CLI inspect, browser-specific snapshot и extra file privileges; включает cookie encryption, embedded ASAR integrity, `OnlyLoadAppFromAsar` и WASM trap handlers. Конфигурация требует явного значения каждого fuse и перечитывается из packaged executable. Worker SHA-256/self-test проверяются отдельно. Подпись EXE остаётся явно отложенным production-решением.
