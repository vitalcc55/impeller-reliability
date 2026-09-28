from __future__ import annotations

import sqlite3

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.worker.deadline import RequestDeadline


def begin_calculation_write(
    connection: sqlite3.Connection,
    deadline: RequestDeadline | None,
    stage: str,
) -> None:
    if deadline is None:
        connection.execute("BEGIN IMMEDIATE")
        return
    deadline.check(stage)
    previous_timeout = connection.execute("PRAGMA busy_timeout").fetchone()
    if previous_timeout is None:
        raise ProjectOperationError("storage_error", "SQLite busy timeout недоступен.")
    previous_timeout_ms = int(previous_timeout[0])
    remaining_ms = max(1, int(deadline.remaining_seconds(5) * 1000))
    connection.execute(f"PRAGMA busy_timeout = {remaining_ms}")
    acquired = False
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            deadline.check(stage)
            raise ProjectOperationError(
                "storage_error",
                "Невозможно получить блокировку записи проекта.",
                retryable=True,
            ) from error
        acquired = True
        deadline.check(stage)
    except Exception:
        if acquired:
            connection.rollback()
        raise
    finally:
        connection.execute(f"PRAGMA busy_timeout = {previous_timeout_ms}")
