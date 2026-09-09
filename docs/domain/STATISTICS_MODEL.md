# Statistics Model

M04B фиксирует подготовительные наблюдения, но не вычисляет статистические показатели. Типы: `failure`, `right_censored`, `withdrawn`, `invalid`; наличие подтверждённого отказа не означает наличие пригодной числовой точки.

Текущая политика `life_metric_exact_v1` принимает только final RBD/RPT observation с совместимыми kind/unit и `failure + exact` либо `right_censored + right_bound`. `diagnostic_partial`, PMN, interval/unavailable failure, withdrawn и invalid остаются доступными как evidence и явные исключения. В одной версии выборки рассматривается одна выбранная версия интерпретации каждого исполнения; included membership дополнительно уникальна по физическому `Specimen` и source `run_id`.

Поддерживаемые значения: `rbd_steady_rotation_time` в `hours` как canonical finite decimal от 0 до 1 000 000 000 и `rpt_start_stop_cycles` в `count` как canonical integer от 0 до 1 000 000 000 000. Ручное число обязательно имеет `metricOrigin=analyst_provided`; отсутствие числа имеет NULL origin. NULL и 0 различаются; одинаковая размерность не делает разные metric kinds взаимозаменяемыми. Weibull/MLE, CI, reliability/hazard, percentile life и bootstrap остаются будущими расчётами M04C+.
