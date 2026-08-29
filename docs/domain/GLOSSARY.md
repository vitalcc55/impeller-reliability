# Glossary

- **Project** — аналитическое дело Impeller Reliability; может быть создано до или после результата R130SH и ничего не передаёт стенду.
- **Project container** — рабочий каталог `*.irproj` с manifest, `project.sqlite`, managed assets и SQLite backups.
- **ProjectSession** — единственная активная Python-сессия, удерживающая OS lock и последовательный SQLite writer.
- **Record revision** — optimistic revision изменяемой строки проекта; устаревший draft не может перезаписать новую редакцию.
- **Specimen** — физический образец с системным UUID/ULID; заводской номер не primary key.
- **ImportedRunPlanSnapshot** — неизменяемый снимок исходного/фактически применённого плана внутри будущего `.r130run`; принадлежит `r130sh_source`.
- **ImportedTestRun** — неизменяемая импортная ревизия результата R130SH.
- **AnalysisInputSnapshot** — выбранные source/enrichment values конкретного анализа с provenance.
- **CalculationSnapshot** — зафиксированный результат алгоритма с версией, input hash, evidence и warnings.
- **TestCampaign** — будущая аналитическая группировка уже импортированных запусков; не является программой стендового испытания.
- **Analyst enrichment** — редактируемые сведения дела, добавленные или уточнённые инженером отдельно от первичных фактов R130SH.
- **AnalystSourceDocument / CaseDocument** — документ аналитического дела в `analyst_enrichment`; не является импортированным документом R130SH и может существовать без файла.
- **Managed document file** — однократно прикреплённая неизменяемая копия внутри `assets/documents` с зарегистрированными size/SHA-256 и project-relative path.
- **Applicability link** — явная историческая связь документа с WheelModel или Specimen; отсутствие связей означает применимость ко всему делу.
- **Document integrity status** — локальный результат проверки managed copy: `not_attached`, `verified`, `missing`, `modified` или `verification_error`; не является общей оценкой целостности проекта.
- **Right-censored** — испытание завершено без наблюдаемого отказа.
- **SourceReference** — будущая точная привязка значения к источнику/page/clause; не создаётся до реального расчётного сценария.
