from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_schema import MAX_AUDIT_PAYLOAD_BYTES
from impeller_reliability.persistence.timestamps import utc_now


def audit_now(connection: sqlite3.Connection) -> str:
    row = connection.execute("SELECT occurred_at_utc FROM project_audit_events ORDER BY sequence DESC LIMIT 1").fetchone()
    current = utc_now()
    return current if row is None else max(current, str(row[0]))


def insert_audit(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    actor_kind: str,
    occurred_at_utc: str,
    payload: dict[str, object],
) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_AUDIT_PAYLOAD_BYTES:
        raise ProjectOperationError(
            "validation_error",
            "Изменение содержит слишком много данных для audit evidence.",
        )
    connection.execute(
        "INSERT INTO project_audit_events (event_id, event_type, occurred_at_utc, actor_kind, payload_json) VALUES (?, ?, ?, ?, ?)",
        (
            str(uuid4()),
            event_type,
            occurred_at_utc,
            actor_kind,
            serialized,
        ),
    )
