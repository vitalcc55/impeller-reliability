# Windows Portable

Внешний артефакт: `ImpellerReliabilityCalc-<version>-portable-x64.exe`. Electron содержит `app.asar`; `resources/python-worker` — PyInstaller onedir с manifest/SHA-256; Main проверяет digest перед запуском. `afterPack` применяет и перечитывает production fuses, повторяет integrity check и worker self-test. `scripts/smoke/desktop.ps1` получает version из package metadata и проверяет handshake/ping/SQLite/shutdown, fuses, TCP и orphan worker только в дереве конкретного запуска.

Текущий GitHub quality workflow намеренно не собирает PyInstaller worker, `win-unpacked` и portable: packaging уже подтверждён локальным полным gate и остаётся обязательной локальной проверкой для затрагивающих поставку изменений. Отдельный manual/release workflow рационально добавить ближе к производственной поставке после выбора installer/installed/portable, а не расширять обычный quality check на M02.

Начиная с M02.1 оба packaged smoke выполняют Project container scenario через bundled worker: создать → изменить → закрыть → открыть и сверить редакцию/данные. Временный `.irproj` находится внутри точного smoke-каталога; network/orphan проверки продолжают относиться только к дереву запущенного экземпляра.

M02.1 final measurement на той же Windows 11: portable 121,490,180 bytes, SHA-256 `F01CAB7B5FC38572A27438C7F406894090775B8881C575977961ECB70E027FE8`; launcher readiness `win-unpacked` 8.734 s, portable 25.567 s. Оба smoke вернули `projectScenarioPassed=true`.
