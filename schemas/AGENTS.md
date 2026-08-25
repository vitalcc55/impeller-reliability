# Schema rules

`schemas/` хранит только утверждённые versioned JSON Schema внешних файловых контрактов и при необходимости IPC. Несовместимое изменение создаёт новую major schema version; смысл существующего поля не меняется молча. Схема добавляется вместе с positive/negative fixtures и cross-language contract tests. Пустые и speculative schemas запрещены.
