# Glossary

- **Project** — аналитическое дело Impeller Reliability; может быть создано до или после результата R130SH и ничего не передаёт стенду.
- **Project container** — рабочий каталог `*.irproj` с manifest, `project.sqlite`, минимальными assets и backups.
- **ProjectSession** — единственная активная Python-сессия, удерживающая OS lock и последовательный SQLite writer.
- **Record revision** — optimistic revision изменяемой строки проекта; устаревший draft не может перезаписать новую редакцию.
- **Specimen** — физический образец с системным UUID/ULID; заводской номер не primary key.
- **ImportedRunPlanSnapshot** — неизменяемый снимок исходного/фактически применённого плана внутри будущего `.r130run`; принадлежит `r130sh_source`.
- **ImportedTestRun** — неизменяемая импортная ревизия результата R130SH.
- **AnalysisInputSnapshot** — выбранные source/enrichment values конкретного анализа с provenance.
- **CalculationSnapshot** — зафиксированный результат алгоритма с версией, input hash, evidence и warnings.
- **TestCampaign** — будущая аналитическая группировка уже импортированных запусков; не является программой стендового испытания.
- **Analyst enrichment** — редактируемые сведения дела, добавленные или уточнённые инженером отдельно от первичных фактов R130SH.
- **Right-censored** — испытание завершено без наблюдаемого отказа.
- **SourceReference** — точное происхождение нормативного/назначенного значения.
