"""Append-only SQLite schema migrations.

Rules
-----
* Migrations are **append-only**. Never edit a shipped migration; add a new one.
* Each migration runs inside a single explicit transaction together with its
  ``schema_migrations`` bookkeeping row, so a failure leaves no partial state.
  ``executescript()`` is deliberately not used: it implicitly commits before
  the script runs and would leave a half-applied schema on failure.
* The evidence table enforces integrity at the database level, not only in
  Python: original text and identity columns are immutable, research evidence
  cannot be hard-deleted, and the legacy classification columns are frozen.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from database.m4_12h_schema import M8_INTERVAL_SQL
from database.m4_24h_schema import M7_INTERVAL_SQL
from database.m4_v9_signoff_schema import M9_SIGNOFF_SQL
from database.m4_v10_interval_schema import M10_INTERVAL_SQL
from database.m4_four_field_schema import M5_SCREENING_STORE_SQL
from database.m4_lifecycle_schema import M6_LIFECYCLE_SQL
from database.m4_screening_schema import M4_SCREENING_STORE_SQL
from database.screening_schema import M3_SCREENING_STORE_SQL

MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at  TEXT NOT NULL
);
"""


def utc_now() -> str:
    """ISO-8601 UTC timestamp, second precision, timezone-aware."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


_INITIAL_SCHEMA = """
-- ---------------------------------------------------------------------------
-- Sources: one row per distinct origin of evidence.
-- ---------------------------------------------------------------------------
CREATE TABLE sources (
    source_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN (
        'google_play', 'reddit', 'youtube', 'website_forum',
        'survey', 'interview', 'other'
    )),
    platform    TEXT,
    domain      TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (source_name, source_type)
);

-- ---------------------------------------------------------------------------
-- Ingestion runs: a cumulative, auditable log of every upload attempt.
-- Re-uploading a file is detected by file_hash before anything is imported.
-- ---------------------------------------------------------------------------
CREATE TABLE ingestion_runs (
    run_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name               TEXT NOT NULL,
    file_hash               TEXT NOT NULL,
    file_size_bytes         INTEGER,
    sheet_or_table          TEXT,
    rows_read               INTEGER NOT NULL DEFAULT 0,
    records_created         INTEGER NOT NULL DEFAULT 0,
    records_flagged_duplicate INTEGER NOT NULL DEFAULT 0,
    records_invalid         INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'started' CHECK (status IN (
        'started', 'completed', 'failed', 'cancelled'
    )),
    message                 TEXT,
    started_at              TEXT NOT NULL,
    completed_at            TEXT
);

CREATE INDEX idx_ingestion_runs_hash ON ingestion_runs (file_hash);

-- ---------------------------------------------------------------------------
-- Evidence: the system of record. One row per real evidence record.
-- Vector chunks are NEVER stored here (and never counted as evidence).
-- ---------------------------------------------------------------------------
CREATE TABLE evidence (
    row_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id          TEXT NOT NULL UNIQUE,
    source_id            INTEGER REFERENCES sources (source_id) ON DELETE RESTRICT,
    source_type          TEXT NOT NULL CHECK (source_type IN (
        'google_play', 'reddit', 'youtube', 'website_forum',
        'survey', 'interview', 'other'
    )),
    platform             TEXT,
    source_url           TEXT,
    source_date          TEXT,
    collection_date      TEXT,
    original_text        TEXT NOT NULL CHECK (length(trim(original_text)) > 0),
    normalized_text      TEXT NOT NULL,
    source_metadata_json TEXT NOT NULL DEFAULT '{}'
                         CHECK (json_valid(source_metadata_json)),
    source_locator       TEXT,
    content_hash         TEXT NOT NULL,
    run_id               INTEGER REFERENCES ingestion_runs (run_id) ON DELETE SET NULL,
    ingested_at          TEXT NOT NULL,
    is_synthetic         INTEGER NOT NULL DEFAULT 0 CHECK (is_synthetic IN (0, 1)),
    duplicate_status     TEXT NOT NULL DEFAULT 'unique' CHECK (duplicate_status IN (
        'unique', 'possible_duplicate', 'duplicate'
    )),
    duplicate_of         TEXT,
    relevance            TEXT CHECK (relevance IS NULL OR relevance IN (
        'relevant', 'indirectly_relevant', 'unrelated',
        'insufficient_information', 'manual_review_required'
    )),
    behaviour            TEXT CHECK (behaviour IS NULL OR behaviour IN (
        'sip_initiation', 'sip_continuation', 'missed_instalment', 'sip_reduction',
        'sip_pause', 'sip_cancellation', 'platform_migration',
        'complete_discontinuation', 'unspecified'
    )),
    directness           TEXT CHECK (directness IS NULL OR directness IN (
        'direct', 'indirect'
    )),
    confidence           REAL CHECK (confidence IS NULL OR (
        confidence >= 0.0 AND confidence <= 1.0
    )),
    validation_status    TEXT NOT NULL DEFAULT 'unclassified' CHECK (validation_status IN (
        'unclassified', 'llm_classified', 'needs_review',
        'human_validated', 'rejected'
    )),
    review_status        TEXT NOT NULL DEFAULT 'not_queued' CHECK (review_status IN (
        'not_queued', 'queued', 'in_review', 'resolved'
    )),
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    CHECK (duplicate_of IS NULL OR duplicate_of <> evidence_id)
);

CREATE INDEX idx_evidence_content_hash  ON evidence (content_hash);
CREATE INDEX idx_evidence_source_type   ON evidence (source_type);
CREATE INDEX idx_evidence_relevance     ON evidence (relevance);
CREATE INDEX idx_evidence_validation    ON evidence (validation_status);
CREATE INDEX idx_evidence_duplicate     ON evidence (duplicate_status);
CREATE INDEX idx_evidence_review_status ON evidence (review_status);
CREATE INDEX idx_evidence_run           ON evidence (run_id);

-- ---------------------------------------------------------------------------
-- Themes: many-to-many. A record may legitimately have several themes,
-- or none at all (we never force a theme onto a record).
-- ---------------------------------------------------------------------------
CREATE TABLE evidence_themes (
    evidence_id TEXT NOT NULL REFERENCES evidence (evidence_id) ON DELETE CASCADE,
    theme       TEXT NOT NULL,
    PRIMARY KEY (evidence_id, theme)
);

CREATE INDEX idx_themes_theme ON evidence_themes (theme);

-- ---------------------------------------------------------------------------
-- Manual review queue.
-- ---------------------------------------------------------------------------
CREATE TABLE review_queue (
    review_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id TEXT NOT NULL REFERENCES evidence (evidence_id) ON DELETE CASCADE,
    reason      TEXT NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN (
        'queued', 'in_review', 'resolved', 'deferred'
    )),
    reviewer_note TEXT,
    created_at  TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE (evidence_id, reason)
);

CREATE INDEX idx_review_status ON review_queue (status, priority);

-- ---------------------------------------------------------------------------
-- Audit log: why something changed, without ever mutating original text.
-- ---------------------------------------------------------------------------
CREATE TABLE audit_log (
    audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    entity     TEXT NOT NULL,
    entity_id  TEXT,
    action     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_audit_entity ON audit_log (entity, entity_id);

-- ---------------------------------------------------------------------------
-- Integrity guards
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_evidence_identity_immutable
BEFORE UPDATE OF evidence_id, original_text, content_hash ON evidence
BEGIN
    SELECT RAISE(ABORT,
        'evidence identity and original text are immutable; store corrections as new records');
END;

CREATE TRIGGER trg_evidence_no_hard_delete
BEFORE DELETE ON evidence
WHEN OLD.is_synthetic = 0
BEGIN
    SELECT RAISE(ABORT,
        'research evidence cannot be deleted; mark validation_status = rejected instead');
END;

-- ---------------------------------------------------------------------------
-- Reporting views
-- ---------------------------------------------------------------------------
-- Genuine research evidence: excludes synthetic test rows and flagged duplicates.
CREATE VIEW v_genuine_evidence AS
SELECT *
FROM evidence
WHERE is_synthetic = 0
  AND duplicate_status <> 'duplicate';
"""


_M2_CORRECTIONS = """
-- ---------------------------------------------------------------------------
-- Migration v2 — M2 pre-ingestion corrections.
--
-- * ``author`` is promoted to a first-class evidence column so that the
--   approved duplicate policy can compare text, source URL and author.
-- * ``ingestion_rejected_rows`` is an append-only rejection log. Blank or
--   otherwise unstorable source rows are recorded here with their complete raw
--   payload; they are never silently dropped and never stored as evidence.
-- ---------------------------------------------------------------------------

ALTER TABLE evidence ADD COLUMN author TEXT;

CREATE INDEX idx_evidence_dup_identity ON evidence (content_hash, source_url, author);
CREATE INDEX idx_evidence_author ON evidence (author);

-- Recreate the reporting view so its ``SELECT *`` includes the new column.
DROP VIEW IF EXISTS v_genuine_evidence;
CREATE VIEW v_genuine_evidence AS
SELECT *
FROM evidence
WHERE is_synthetic = 0
  AND duplicate_status <> 'duplicate';

CREATE TABLE ingestion_rejected_rows (
    rejection_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id               INTEGER REFERENCES ingestion_runs (run_id) ON DELETE SET NULL,
    source_file          TEXT,
    source_record_number TEXT,
    source_native_id     TEXT,
    source_locator       TEXT NOT NULL,
    reason_code          TEXT NOT NULL CHECK (reason_code IN (
        'EMPTY_TEXT', 'MISSING_SOURCE_LOCATOR', 'INVALID_SOURCE_TYPE',
        'MISSING_REQUIRED_FIELD', 'SCRAPER_ERROR', 'OTHER'
    )),
    reason               TEXT NOT NULL,
    raw_row_json         TEXT NOT NULL CHECK (json_valid(raw_row_json)),
    created_at           TEXT NOT NULL,
    UNIQUE (source_locator, reason_code)
);

CREATE INDEX idx_rejected_run ON ingestion_rejected_rows (run_id);
CREATE INDEX idx_rejected_source_file ON ingestion_rejected_rows (source_file);
CREATE INDEX idx_rejected_reason ON ingestion_rejected_rows (reason_code);

CREATE TRIGGER trg_rejected_rows_no_delete
BEFORE DELETE ON ingestion_rejected_rows
BEGIN
    SELECT RAISE(ABORT,
        'rejected ingestion rows are audit records and cannot be deleted');
END;
"""


MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "initial evidence schema", _INITIAL_SCHEMA),
    (2, "M2 corrections: author identity and rejection log", _M2_CORRECTIONS),
    (3, "M3 append-only screening store", M3_SCREENING_STORE_SQL),
    (4, "M4 single-researcher two-pass screening store", M4_SCREENING_STORE_SQL),
    (5, "four-field screening store: remove directness", M5_SCREENING_STORE_SQL),
    (6, "M4 lifecycle, freeze, Pass 2 and AI provenance", M6_LIFECYCLE_SQL),
    (7, "M4 Pass 2 interval amended to 24 hours", M7_INTERVAL_SQL),
    (8, "M4 Pass 2 interval amended to 12 hours", M8_INTERVAL_SQL),
    (9, "M4 sign-off v2 structural validation", M9_SIGNOFF_SQL),
    (10, "M4 Run 1 approved 6-hour interval deviation (ADR-033)", M10_INTERVAL_SQL),
]


def current_version(conn: sqlite3.Connection) -> int:
    """Highest applied migration version, or 0 for an empty database."""
    conn.execute(MIGRATIONS_TABLE_SQL)
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


class MigrationError(RuntimeError):
    """Raised when a migration script cannot be split or applied safely."""


def _split_statements(sql: str) -> list[str]:
    """Split a migration script without breaking trigger bodies.

    ``sqlite3.complete_statement()`` understands string literals, comments and
    ``CREATE TRIGGER ... BEGIN ... END;`` bodies, so this does not hand-roll a
    BEGIN/END parser and does not split on a semicolon inside a string.
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise MigrationError("incomplete SQL statement at end of migration")
    return statements


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply every migration newer than the current version atomically.

    One explicit transaction per migration version covers both the DDL and the
    ``schema_migrations`` row. A failure rolls the whole version back, leaving
    the database at its previous schema version and ready for a clean retry.
    """
    conn.execute(MIGRATIONS_TABLE_SQL)
    conn.commit()

    previous_isolation = conn.isolation_level
    conn.isolation_level = None  # explicit BEGIN/COMMIT control
    applied: list[int] = []
    try:
        for version, description, sql in sorted(MIGRATIONS, key=lambda m: m[0]):
            exists = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone()
            if exists:
                continue

            statements = _split_statements(sql)
            try:
                conn.execute("BEGIN IMMEDIATE")
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version, description, applied_at) "
                    "VALUES (?, ?, ?)",
                    (version, description, utc_now()),
                )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            applied.append(version)
    finally:
        conn.isolation_level = previous_isolation
    return applied
