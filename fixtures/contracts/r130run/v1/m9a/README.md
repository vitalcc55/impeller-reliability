# R130SH M9a downstream snapshot

Этот каталог — автономный immutable snapshot producer-generated M9a golden packages из `vitalcc55/R130SH@01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63`.

- `package-index.json` и 21 archive в `packages/` скопированы без изменения.
- Набор покрывает 18 authored M9a scenarios и является cross-repository M9b acceptance evidence.
- CI проверяет точный набор имён, size и outer SHA-256 offline; сеть и соседний checkout R130SH не используются.
- Snapshot не обновляется автоматически. Любое обновление требует нового exact upstream commit, provenance и review.
- M03A synthetic fixtures остаются отдельными unit/negative/safety fixtures и не доказывают producer compatibility.
