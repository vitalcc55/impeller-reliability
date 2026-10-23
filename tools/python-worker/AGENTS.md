# Python worker rules

## Scope

Python worker — единственный владелец предметной валидации, будущих инженерных расчётов, SQLite, migrations и canonical snapshots.

## Invariants

- stdout содержит только UTF-8 JSONL protocol; stderr — diagnostics.
- Operation allowlist явный; generic execute и произвольное открытие пользовательских путей запрещены.
- Exact scalars используют Decimal на доменной границе; scientific algorithms получают finite NumPy float64 только явно.
- SQLite: foreign keys, WAL, one writer, short transactions, forward-only migrations. ORM не добавлять без доказанной необходимости.
- Алгоритм появляется только вместе с input/output contract, source, invariants, golden fixtures и независимыми тестами.
