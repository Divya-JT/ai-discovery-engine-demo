"""SQLite connection management and schema bootstrap.

One small module owns:
* how a connection is opened (pragmas, row factory, timeouts),
* where the schema lives and how it is migrated,
* a lightweight health check used by tests and, later, the dashboard.

Nothing here knows about Streamlit or the LLM.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config import Settings, ensure_directories, get_settings
from database.migrations import apply_migrations, current_version


class DatabaseError(RuntimeError):
    """Raised when the database cannot be opened or initialised."""


def connect(
    db_path: str | Path | None = None,
    *,
    read_only: bool = False,
    timeout: float = 30.0,
) -> sqlite3.Connection:
    """Open a tuned SQLite connection.

    Parameters
    ----------
    db_path:
        Path to the database file. Defaults to the path in settings.
    read_only:
        Open in URI read-only mode. Used for exports and safe inspection.
    """
    path = Path(db_path) if db_path is not None else get_settings().db_path
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if read_only:
            uri = f"file:{path.as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=timeout)
        else:
            conn = sqlite3.connect(str(path), timeout=timeout)
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        raise DatabaseError(f"cannot open database at {path}: {exc}") from exc

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")

    return conn


def init_db(
    settings: Settings | None = None,
    *,
    db_path: str | Path | None = None,
) -> int:
    """Create directories, open the database and apply pending migrations.

    Returns the schema version after migration.
    """
    settings = settings or get_settings()
    ensure_directories(settings)
    path = Path(db_path) if db_path is not None else settings.db_path

    conn = connect(path)
    try:
        apply_migrations(conn)
        return current_version(conn)
    finally:
        conn.close()


@contextmanager
def session(
    settings: Settings | None = None,
    *,
    db_path: str | Path | None = None,
) -> Iterator[sqlite3.Connection]:
    """Connection context manager that commits on success and always closes."""
    settings = settings or get_settings()
    path = Path(db_path) if db_path is not None else settings.db_path
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def table_names(conn: sqlite3.Connection) -> list[str]:
    """User tables and views present in the database."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def health_check(conn: sqlite3.Connection) -> dict[str, object]:
    """Return a small, honest snapshot of database state."""
    version = current_version(conn)
    total = conn.execute("SELECT COUNT(*) AS n FROM evidence").fetchone()["n"]
    genuine = conn.execute("SELECT COUNT(*) AS n FROM v_genuine_evidence").fetchone()["n"]
    return {
        "schema_version": version,
        "tables": table_names(conn),
        "evidence_total": int(total),
        "evidence_genuine": int(genuine),
    }
