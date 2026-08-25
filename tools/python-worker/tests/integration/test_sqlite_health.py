from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from impeller_reliability.persistence.sqlite_health import SCHEMA_VERSION, check_storage, storage_is_healthy


def test_health_creates_minimal_migrated_database(tmp_path: Path) -> None:
    database_path = tmp_path / "health.sqlite"
    result = check_storage(database_path)
    assert result == {
        "status": "ok",
        "databaseSchemaVersion": SCHEMA_VERSION,
        "quickCheck": "ok",
        "foreignKeys": True,
        "journalMode": "wal",
    }
    with closing(sqlite3.connect(database_path)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"schema_info"}


def test_health_rejects_unknown_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "health.sqlite"
    check_storage(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("UPDATE schema_info SET version = 999 WHERE singleton = 1")
        connection.commit()
    with pytest.raises(RuntimeError, match="unsupported_schema_version:999"):
        check_storage(database_path)


def test_health_verdict_requires_wal() -> None:
    assert storage_is_healthy(quick_check="ok", foreign_keys=1, journal_mode="wal", version=SCHEMA_VERSION)
    assert not storage_is_healthy(quick_check="ok", foreign_keys=1, journal_mode="delete", version=SCHEMA_VERSION)
