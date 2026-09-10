# Analysis Input Rules

РБД реализуется как последующий анализ exact export revision импортированного результата. РПТ и ПМН остаются следующими этапами и не используют этот контракт автоматически. M04B уже формирует подготовительную versioned `ReliabilityDataset`, но она не является `AnalysisInputSnapshot`, не обязательна для расчёта одного исполнения и не запускает расчёт.

`AnalysisInputSnapshot` явно фиксирует выбранные значения из неизменяемого `r130sh_source` и редактируемого `analyst_enrichment`. `CalculationSnapshot` сохраняет результат, версию алгоритма и доказательства. TypeScript не повторяет формулы, а расчёт не формирует исполняемый план R130SH.

## РБД `rbd_reference` 1.0.0

Исходные значения соответствуют текущему R130SH: `base_cycles` (`N0`),
`reserve_factor` (`k1`), `nominal_rpm` (`nP`),
`acceleration_duration_s` (`tP1`) и `deceleration_duration_s` (`tT1`).
Original и effective plan выбираются явно и фиксируются с exact payload hash.
Отсутствующее значение может быть заменено только документированным ручным
дополнением, не изменяющим source.

Обязательные результаты по формулам 1–3 ПМИ Р130У:

```text
nmax1 = nP
required_cycles_exact = N0 × k1
tUST1_exact_s = 60 × required_cycles_exact / nmax1
TC1_exact_s = tP1 + tUST1_exact_s + tT1
T01_exact_s = TC1_exact_s
```

`required_cycles_exact` — расчётное требование выбранного сценария, а не
gamma-resource или фактическая наработка. `target_cycles`,
`target_steady_duration_s` и `total_duration_s` — отдельные producer targets,
полученные после ceiling, и сохраняются без подмены exact results.

Опциональная таблица 3 рассчитывает
`N_OTK = floor((T_OTK - tP1) × nP / 60)` только для документированного exact
`T_OTK` с доказанной применимостью. Interval/right-censored endpoint,
неподдерживаемая фаза, неоднозначные паузы и отсутствие значения дают typed
`not_applicable`; отрицательное подвыражение является validation error.

Граница принимает bounded ASCII decimal без exponent, специальных значений,
пробелов, `_` и Unicode digits. Технические пределы вычислительной стоимости:
`N0` — positive integer до `10^12`; `nP` и `k1` — positive decimal до `10^6`;
`tP1` и `tT1` — non-negative decimal до `10^9` секунд; `T_OTK` —
non-negative decimal до `10^12` секунд; scale decimal не превышает 12 знаков.
Это пределы текущей реализации, а не диапазоны оборудования или требования ПМИ.
Python преобразует проверенные Decimal в
Fraction и выполняет арифметику точно. Результат хранит numerator/denominator;
конечная decimal записывается canonical, периодическая получает только bounded
display preview, который не участвует в дальнейшей арифметике. Binary float и
ambient Decimal context не используются.

Координаты небольшой схемы нагрузки следуют схематическому рисунку 1 ПМИ:
`0 → nP → 0`. Это профиль выбранного расчёта, а не измеренный ряд и не попытка
согласовать все табличные обозначения минимальной частоты с executable plan.
