# Functional Requirements

Канонические функциональные области: проекты/справочники, планы РБД/РПТ/ПМН, файловый обмен R130SH, анализ запусков, FMEA/FMECA, статистика/Вейбулл, Марков, Монте-Карло, вибрация и отчётность. Каждый расчёт хранит versioned algorithm id, input hash, evidence, warnings, units, sources и rounding policy. Импортированные факты и выпущенные snapshots неизменяемы.

M01 capabilities ограничены handshake, ping, shutdown и SQLite health.
