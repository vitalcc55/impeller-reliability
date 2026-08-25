from contextlib import closing
from pathlib import Path
import sqlite3

SCHEMA_VERSION = 1


def storage_is_healthy(*, quick_check: str, foreign_keys: int, journal_mode: str, version: int) -> bool:
    return quick_check == "ok" and foreign_keys == 1 and journal_mode == "wal" and version == SCHEMA_VERSION


def _configure(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")


def _migrate(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS schema_info (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL)")
        row = connection.execute("SELECT version FROM schema_info WHERE singleton = 1").fetchone()
        if row is None:
            connection.execute("INSERT INTO schema_info(singleton, version) VALUES (1, ?)", (SCHEMA_VERSION,))
        elif int(row[0]) != SCHEMA_VERSION:
            raise RuntimeError(f"unsupported_schema_version:{row[0]}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def check_storage(database_path: Path) -> dict[str, object]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path, timeout=5.0)) as connection:
        _configure(connection)
        _migrate(connection)
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        version = int(connection.execute("SELECT version FROM schema_info WHERE singleton = 1").fetchone()[0])
    status = (
        "ok"
        if storage_is_healthy(
            quick_check=quick_check,
            foreign_keys=foreign_keys,
            journal_mode=journal_mode,
            version=version,
        )
        else "error"
    )
    return {
        "status": status,
        "databaseSchemaVersion": version,
        "quickCheck": quick_check,
        "foreignKeys": foreign_keys == 1,
        "journalMode": journal_mode,
    }
