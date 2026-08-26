from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
import sqlite3

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.worker.deadline import RequestDeadline


@contextmanager
def sqlite_deadline_guard(
    connection: sqlite3.Connection,
    deadline: RequestDeadline | None,
    stage: str,
    *,
    progress_steps: int = 1_000,
) -> Generator[None]:
    if deadline is None:
        yield
        return
    deadline.check(stage)
    timeout_errors: list[ProjectOperationError] = []

    def interrupt_when_expired() -> int:
        try:
            deadline.check(stage)
        except ProjectOperationError as error:
            timeout_errors.append(error)
            return 1
        return 0

    connection.set_progress_handler(interrupt_when_expired, progress_steps)
    try:
        yield
    except sqlite3.OperationalError as error:
        if timeout_errors:
            raise timeout_errors[0] from error
        raise
    finally:
        connection.set_progress_handler(None, 0)
    deadline.check(stage)


def sqlite_query_rows_with_deadline(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...],
    deadline: RequestDeadline | None,
    stage: str,
    *,
    progress_steps: int = 1_000,
) -> Generator[Sequence[object]]:
    with sqlite_deadline_guard(connection, deadline, stage, progress_steps=progress_steps):
        yield from connection.execute(sql, parameters)
