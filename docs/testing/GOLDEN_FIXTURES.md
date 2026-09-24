# Golden Fixtures

Каждый golden case хранит source, canonical input, intermediate values, result,
units, rounding policy, tolerance и reviewer. Демонстрационные значения из
исходного ТЗ не становятся golden автоматически.

`fixtures/golden/rbd-reference-v1.json` — первый утверждённый downstream golden
для `rbd_reference` 1.0.0. Примеры A и C взяты из постановки и визуально
сверенного ПМИ Р130У, редакция 01; пример B одновременно совпадает с frozen
producer package `exact_methodical_rounding` и отдельно фиксирует различие exact
требования `1500.3 / 60.012 / 70.012` и исполняемой уставки
`1501 / 60.04 / 70.04`. Expected values заданы в fixture вручную и не
вычисляются production helper-ом.
Fixture закрепляет SHA-256 исходного PDF, проверенные разделы, единицы,
математический floor для `N_OTK`, exact tolerance policy и способ review.
