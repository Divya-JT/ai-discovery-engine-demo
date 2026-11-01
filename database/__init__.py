"""Storage foundation for the AI Discovery Engine.

Public entry points
-------------------
* :func:`database.db.init_db` — create directories, open the database, migrate.
* :func:`database.repository.insert_evidence` — store one evidence record.
* :func:`database.repository.counts_summary` — honest evidence counts.
"""

from database.db import connect, health_check, init_db, session, table_names

__all__ = ["connect", "health_check", "init_db", "session", "table_names"]
