# Windows Portable

Внешний артефакт: `ImpellerReliabilityCalc-<version>-portable-x64.exe`. Electron содержит `app.asar`; `resources/python-worker` — PyInstaller onedir с manifest/SHA-256; Main проверяет digest перед запуском. `afterPack` применяет и перечитывает production fuses, повторяет integrity check и worker self-test. `scripts/smoke/desktop.ps1` получает version из package metadata и проверяет handshake/ping/SQLite/shutdown, fuses, TCP и orphan worker только в дереве конкретного запуска.

Текущий GitHub quality workflow намеренно не собирает PyInstaller worker, `win-unpacked` и portable: packaging уже подтверждён локальным полным gate и остаётся обязательной локальной проверкой для затрагивающих поставку изменений. Отдельный manual/release workflow рационально добавить ближе к производственной поставке после выбора installer/installed/portable, а не расширять обычный quality check на M02.

Оба packaged smoke выполняют полный текущий Project scenario через bundled worker: создать/изменить Project → создать Customer/WheelModel/Specimen → создать CaseDocument с managed PDF и applicability → закрыть → открыть → сверить revisions, связи и `verified` integrity. Временный `.irproj` и document fixture находятся внутри точного smoke-каталога; network/orphan проверки продолжают относиться только к дереву запущенного экземпляра.

Размер/hash/timing конкретного старого артефакта не являются текущим контрактом и измеряются заново полным packaging gate каждого затрагивающего поставку этапа. Успех обоих smoke требует `projectScenarioPassed=true`.
