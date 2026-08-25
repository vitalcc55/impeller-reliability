# Windows Portable

Внешний артефакт: `ImpellerReliabilityCalc-<version>-portable-x64.exe`. Electron содержит `app.asar`; `resources/python-worker` — PyInstaller onedir с manifest/SHA-256; Main проверяет digest перед запуском. `afterPack` повторяет integrity check и worker self-test. `scripts/smoke/desktop.ps1` проверяет handshake/ping/SQLite/shutdown, TCP и orphan worker.

M01 measurement on Windows 11 после обновления зависимостей: worker onedir 93,229,600 bytes; `win-unpacked` 493,284,450 bytes; portable 121,362,139 bytes (ниже research target 200 MB), SHA-256 `660d1491be0f9cfecdff821b794321aedcf7ee1ee43eda2f1993face72564b53`. Полная готовность от launcher в финальном gate: `win-unpacked` 4.735 s, portable с распаковкой 26.183 s. Цель `<5 s` выполнена для `win-unpacked`, но не для portable; это остаётся измеренной целью исследования, а не скрытым обещанием. Внутренний post-window smoke занимает 8–11 ms и не подменяет full startup measurement.
