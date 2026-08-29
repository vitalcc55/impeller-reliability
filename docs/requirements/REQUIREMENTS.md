# Functional Requirements

Канонические функциональные области: проекты/справочники, односторонний импорт результатов R130SH, расчёты РБД/РПТ/ПМН, анализ запусков, FMEA/FMECA, статистика/Вейбулл, Марков, Монте-Карло, вибрация и отчётность. Каждый расчёт хранит versioned algorithm id, input hash, evidence, warnings, units, sources и rounding policy. Импортированные факты и выпущенные snapshots неизменяемы. Impeller Reliability не формирует задания для R130SH и не участвует в проведении стендового испытания.

M01 capabilities ограничены handshake, ping, shutdown и SQLite health.

Первый публикуемый `.irproj` использует одну чистую schema v1: manifest, Python-owned `project.sqlite`, Project metadata/audit, Customer/WheelModel/Specimen и CaseDocument. Невыпущенные промежуточные schema не являются поддерживаемыми входными форматами; первая forward migration будет добавлена после выпуска baseline. Одна эксклюзивная ProjectSession удерживает OS lock и последовательный SQLite writer. Несохранённый renderer draft не уничтожается при controlled restart, закрытии окна без подтверждения или неожиданной потере worker.

M02.2A реализует слой `analyst_enrichment`: одну optional-карточку заказчика, каталог моделей рабочих колёс и каталог физических образцов. Неполные сведения допускаются с отдельными warnings. Изменение, архивирование и восстановление используют optimistic revision и атомарный audit; hard delete отсутствует. Эти сведения не являются планом стенда и не изменяют будущий `r130sh_source`.

M02.2B добавляет `AnalystSourceDocument` («Документ дела») только в `analyst_enrichment`: metadata, optional immutable managed file, explicit WheelModel/Specimen applicability, integrity status и неблокирующие completeness warnings. Metadata-only запись допустима; файл прикрепляется один раз, duplicate SHA-256 детерминированно отклоняется, новая фактическая редакция создаётся новой записью. Archive/restore сохраняют файл и links. Create/update/attach/archive/restore используют bounded deadline, optimistic revision и атомарный audit; no-op не создаёт revision/event.

Renderer не получает и не передаёт произвольный путь. Main показывает file dialog, выполняет первичный gate и передаёт approved path только worker. Python повторно проверяет regular file, allowlisted extension, размер до 100 MiB, базовую signature/UTF-8, containment и deadline; затем выполняет streaming copy через project-owned staging, SHA-256, atomic rename и SQLite transaction. Успех означает совпадающие DB/file size/hash; ошибка не оставляет зарегистрированную ссылку на непроверенный файл. Staging не является документом и узко очищается при следующем открытии.

Missing/modified managed file не превращает `.irproj` в corrupt project, не удаляется, не перехешируется и не открывается как доказательный источник. `resolveFile` возвращает путь только Main после containment/existence/SHA-256; Renderer получает только typed outcome.

Текущий renderer следует общему interaction-state/draft/focus contract, работает с клавиатуры и без горизонтального overflow на 640 px. WCAG 2.2 AA является инженерной целью; детальное развитие commands/jobs/tables/charts/recovery распределено в `docs/plans/UX_INTERACTION_EVOLUTION.md` и не входит в M02.2B.

Будущий importer хранит `ImportedRunPlanSnapshot` как неизменяемую часть `.r130run`. Конкретный анализ создаёт `AnalysisInputSnapshot`, а выполненный алгоритм — `CalculationSnapshot`. `TestCampaign` допускается только как downstream-группировка импортированных запусков.
