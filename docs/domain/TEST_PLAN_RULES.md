# Test Plan Rules

РБД/РПТ/ПМН будут реализованы только после утверждения input/output contracts, invariants, sources, golden examples, units и rounding. Точные десятичные входы пересекают границы как canonical decimal strings; счётчики — integers; measurement series — finite float64. План разделяет source values и execution targets. TypeScript не повторяет формулы.
