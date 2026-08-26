# Analysis Input Rules

РБД/РПТ/ПМН будут реализованы как последующий анализ импортированного результата только после утверждения input/output contracts, invariants, sources, golden examples, units и rounding. Точные десятичные значения пересекают границы как canonical decimal strings; счётчики — integers; measurement series — finite float64.

`AnalysisInputSnapshot` явно фиксирует выбранные значения из неизменяемого `r130sh_source` и редактируемого `analyst_enrichment`. `CalculationSnapshot` сохраняет результат, версию алгоритма и доказательства. TypeScript не повторяет формулы, а расчёт не формирует исполняемый план R130SH.
