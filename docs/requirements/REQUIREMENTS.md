# Functional Requirements

Канонические функциональные области: проекты/справочники, планы РБД/РПТ/ПМН, файловый обмен R130SH, анализ запусков, FMEA/FMECA, статистика/Вейбулл, Марков, Монте-Карло, вибрация и отчётность. Каждый расчёт хранит versioned algorithm id, input hash, evidence, warnings, units, sources и rounding policy. Импортированные факты и выпущенные snapshots неизменяемы.

M01 capabilities ограничены handshake, ping, shutdown и SQLite health.

M02.1 реализует каталог `.irproj`, manifest, Python-owned `project.sqlite`, одну эксклюзивную ProjectSession, forward-only migration/backup, Project metadata с optimistic `record_revision`, append-only audit, recent projects и поток create → update → close → reopen. Customer, WheelModel, Specimen, TestCampaign и SourceDocument относятся только к M02.2.
