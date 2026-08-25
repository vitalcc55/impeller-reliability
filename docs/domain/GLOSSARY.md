# Glossary

- **Project** — агрегат работы с образцами, кампаниями, анализами и отчётами.
- **Project container** — рабочий каталог `*.irproj` с manifest, `project.sqlite`, минимальными assets и backups.
- **ProjectSession** — единственная активная Python-сессия, удерживающая OS lock и последовательный SQLite writer.
- **Record revision** — optimistic revision изменяемой строки проекта; устаревший draft не может перезаписать новую редакцию.
- **Specimen** — физический образец с системным UUID/ULID; заводской номер не primary key.
- **TestPlanRevision** — утверждённая неизменяемая ревизия плана с hash.
- **ImportedTestRun** — неизменяемая импортная ревизия результата R130SH.
- **AnalysisSnapshot / ReportSnapshot** — неизменяемые входы и результаты вычисления/документа.
- **Right-censored** — испытание завершено без наблюдаемого отказа.
- **SourceReference** — точное происхождение нормативного/назначенного значения.
