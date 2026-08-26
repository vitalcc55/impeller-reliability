# Functional Requirements

Канонические функциональные области: проекты/справочники, односторонний импорт результатов R130SH, расчёты РБД/РПТ/ПМН, анализ запусков, FMEA/FMECA, статистика/Вейбулл, Марков, Монте-Карло, вибрация и отчётность. Каждый расчёт хранит versioned algorithm id, input hash, evidence, warnings, units, sources и rounding policy. Импортированные факты и выпущенные snapshots неизменяемы. Impeller Reliability не формирует задания для R130SH и не участвует в проведении стендового испытания.

M01 capabilities ограничены handshake, ping, shutdown и SQLite health.

M02.1 реализует каталог `.irproj`, manifest, Python-owned `project.sqlite`, одну эксклюзивную ProjectSession, forward-only migration/backup, Project metadata с optimistic `record_revision`, append-only evidence audit, operation-specific bounded deadlines, recent projects и поток create → update → close → reopen. Несохранённый renderer draft не уничтожается при controlled restart, закрытии окна без подтверждения или неожиданной потере worker. Customer, WheelModel, Specimen, TestCampaign и SourceDocument относятся только к M02.2.

M02.2A реализует слой `analyst_enrichment`: одну optional-карточку заказчика, каталог моделей рабочих колёс и каталог физических образцов. Неполные сведения допускаются с отдельными warnings. Изменение, архивирование и восстановление используют optimistic revision и атомарный audit; hard delete отсутствует. Эти сведения не являются планом стенда и не изменяют будущий `r130sh_source`.

Будущий importer хранит `ImportedRunPlanSnapshot` как неизменяемую часть `.r130run`. Конкретный анализ создаёт `AnalysisInputSnapshot`, а выполненный алгоритм — `CalculationSnapshot`. `TestCampaign` допускается только как downstream-группировка импортированных запусков.
