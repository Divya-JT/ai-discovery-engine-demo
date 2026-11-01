"""Repository layer: the only place that writes SQL for evidence storage.

Kept separate from ``database/db.py`` (connection handling) so that schema
concerns and query concerns stay readable. All writes are explicit; nothing is
deleted, and original text is never updated.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping, Sequence

from config import (
    REJECTION_REASON_CODES,
    SOURCE_TYPE_VALUES,
)
from database.migrations import utc_now
from database.models import EvidenceInput, IngestionRunInfo, InsertResult
from ingestion.identity import (
    content_hash,
    evidence_id_for,
    identity_key,
    normalize_for_search,
)


class RepositoryError(ValueError):
    """Raised for invalid repository input (bad enum, empty text, ...)."""


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def get_or_create_source(
    conn: sqlite3.Connection,
    source_name: str,
    source_type: str,
    *,
    platform: str | None = None,
    domain: str | None = None,
) -> int:
    """Return the source_id for (name, type), creating it on first use."""
    if source_type not in SOURCE_TYPE_VALUES:
        raise RepositoryError(f"unknown source_type: {source_type!r}")
    name = (source_name or "").strip() or source_type
    row = conn.execute(
        "SELECT source_id FROM sources WHERE source_name = ? AND source_type = ?",
        (name, source_type),
    ).fetchone()
    if row:
        return int(row["source_id"])

    cur = conn.execute(
        "INSERT INTO sources (source_name, source_type, platform, domain, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, source_type, platform, domain, utc_now()),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# Ingestion runs
# ---------------------------------------------------------------------------


def start_ingestion_run(
    conn: sqlite3.Connection,
    *,
    file_name: str,
    file_hash_value: str,
    file_size_bytes: int | None = None,
    sheet_or_table: str | None = None,
) -> int:
    """Open a run in 'started' state. Returns run_id."""
    cur = conn.execute(
        "INSERT INTO ingestion_runs "
        "(file_name, file_hash, file_size_bytes, sheet_or_table, status, started_at) "
        "VALUES (?, ?, ?, ?, 'started', ?)",
        (file_name, file_hash_value, file_size_bytes, sheet_or_table, utc_now()),
    )
    return int(cur.lastrowid)


def finish_ingestion_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str = "completed",
    rows_read: int = 0,
    records_created: int = 0,
    records_flagged_duplicate: int = 0,
    records_invalid: int = 0,
    message: str | None = None,
) -> None:
    if status not in {"completed", "failed", "cancelled"}:
        raise RepositoryError(f"invalid run status: {status!r}")
    conn.execute(
        "UPDATE ingestion_runs SET status = ?, rows_read = ?, records_created = ?, "
        "records_flagged_duplicate = ?, records_invalid = ?, message = ?, "
        "completed_at = ? WHERE run_id = ?",
        (
            status,
            rows_read,
            records_created,
            records_flagged_duplicate,
            records_invalid,
            message,
            utc_now(),
            run_id,
        ),
    )


def find_runs_by_file_hash(
    conn: sqlite3.Connection, file_hash_value: str
) -> list[IngestionRunInfo]:
    """Runs previously recorded for identical file bytes (duplicate-upload guard)."""
    rows = conn.execute(
        "SELECT * FROM ingestion_runs WHERE file_hash = ? ORDER BY run_id",
        (file_hash_value,),
    ).fetchall()
    return [
        IngestionRunInfo(
            run_id=row["run_id"],
            file_name=row["file_name"],
            file_hash=row["file_hash"],
            status=row["status"],
            rows_read=row["rows_read"],
            records_created=row["records_created"],
            records_flagged_duplicate=row["records_flagged_duplicate"],
            records_invalid=row["records_invalid"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def _require_enum(value: str | None, allowed: Sequence[str], field_name: str) -> None:
    if value is not None and value not in allowed:
        raise RepositoryError(f"invalid {field_name}: {value!r}")


def classify_duplicate(
    conn: sqlite3.Connection,
    *,
    text_hash: str,
    source_url: str | None,
    author: str | None,
    exclude_evidence_id: str | None = None,
) -> tuple[str, str | None]:
    """Apply the approved duplicate policy against already-stored evidence.

    * ``duplicate`` — same text **and** a known, matching source URL **and** a
      known, matching author. Missing URL/author values are unknown, never a
      match, so two rows that both lack provenance cannot become a true
      duplicate.
    * ``possible_duplicate`` — same normalised text, but the URL or author
      differs or is missing.
    * ``unique`` — no earlier record with the same text.

    Synthetic rows are never used as duplicate references. Nothing is deleted.
    """
    target_url = identity_key(source_url)
    target_author = identity_key(author)

    rows = conn.execute(
        "SELECT evidence_id, source_url, author FROM evidence "
        "WHERE content_hash = ? AND is_synthetic = 0 ORDER BY row_id",
        (text_hash,),
    ).fetchall()

    first_same_text: str | None = None
    for row in rows:
        if exclude_evidence_id and row["evidence_id"] == exclude_evidence_id:
            continue
        if first_same_text is None:
            first_same_text = row["evidence_id"]
        if (
            target_url is not None
            and target_author is not None
            and identity_key(row["source_url"]) == target_url
            and identity_key(row["author"]) == target_author
        ):
            return "duplicate", row["evidence_id"]

    if first_same_text is not None:
        return "possible_duplicate", first_same_text
    return "unique", None


def insert_evidence(
    conn: sqlite3.Connection,
    item: EvidenceInput,
    *,
    run_id: int | None = None,
) -> InsertResult:
    """Store one evidence record.

    Behaviour
    ---------
    * Empty/whitespace-only text is rejected (never stored as evidence).
    * ``original_text`` is stored byte-exactly; it is never stripped or
      rewritten. A separate ``normalized_text`` column is derived for search.
    * Re-inserting an identical record (same ID) is a no-op: ``created=False``.
    * Same text + same known URL + same known author is flagged ``duplicate``.
    * Same text with a different/missing URL or author is stored and flagged
      ``possible_duplicate``. Nothing is ever deleted.
    """
    raw_text = item.original_text
    if raw_text is None or not str(raw_text).strip():
        raise RepositoryError("evidence text is empty; record not stored")
    text = str(raw_text)
    _require_enum(item.source_type, SOURCE_TYPE_VALUES, "source_type")

    text_hash = content_hash(text)
    evidence_id = evidence_id_for(
        item.source_type,
        text_hash,
        source_url=item.source_url,
        source_locator=item.source_locator,
    )

    existing = conn.execute(
        "SELECT evidence_id, duplicate_status, duplicate_of FROM evidence "
        "WHERE evidence_id = ?",
        (evidence_id,),
    ).fetchone()
    if existing:
        return InsertResult(
            evidence_id=evidence_id,
            created=False,
            duplicate_status=existing["duplicate_status"],
            duplicate_of=existing["duplicate_of"],
        )

    if item.is_synthetic:
        duplicate_status, duplicate_of = "unique", None
    else:
        duplicate_status, duplicate_of = classify_duplicate(
            conn,
            text_hash=text_hash,
            source_url=item.source_url,
            author=item.author,
        )

    now = utc_now()
    source_id = get_or_create_source(
        conn,
        source_name=item.source_name,
        source_type=item.source_type,
        platform=item.platform,
    )

    conn.execute(
        "INSERT INTO evidence ("
        "evidence_id, source_id, source_type, platform, author, source_url, "
        "source_date, collection_date, original_text, normalized_text, "
        "source_metadata_json, source_locator, content_hash, run_id, ingested_at, "
        "is_synthetic, duplicate_status, duplicate_of, validation_status, "
        "review_status, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "'unclassified', 'not_queued', ?, ?)",
        (
            evidence_id,
            source_id,
            item.source_type,
            item.platform,
            item.author,
            item.source_url,
            item.source_date,
            item.collection_date,
            text,
            normalize_for_search(text),
            json.dumps(dict(item.source_metadata or {}), ensure_ascii=False),
            item.source_locator,
            text_hash,
            run_id,
            now,
            1 if item.is_synthetic else 0,
            duplicate_status,
            duplicate_of,
            now,
            now,
        ),
    )

    for theme in sorted(set(item.themes)):
        conn.execute(
            "INSERT OR IGNORE INTO evidence_themes (evidence_id, theme) VALUES (?, ?)",
            (evidence_id, theme),
        )

    detail = f"duplicate_status={duplicate_status}"
    if duplicate_of:
        detail += f" duplicate_of={duplicate_of}"
    log_audit(
        conn,
        entity="evidence",
        entity_id=evidence_id,
        action="inserted",
        detail=detail,
    )

    return InsertResult(
        evidence_id=evidence_id,
        created=True,
        duplicate_status=duplicate_status,
        duplicate_of=duplicate_of,
    )


def get_evidence(conn: sqlite3.Connection, evidence_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,)
    ).fetchone()
    if row is None:
        return None
    record = dict(row)
    record["themes"] = get_themes(conn, evidence_id)
    return record


def get_themes(conn: sqlite3.Connection, evidence_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT theme FROM evidence_themes WHERE evidence_id = ? ORDER BY theme",
        (evidence_id,),
    ).fetchall()
    return [row["theme"] for row in rows]


def list_evidence(
    conn: sqlite3.Connection,
    *,
    source_type: str | None = None,
    relevance: str | None = None,
    theme: str | None = None,
    validation_status: str | None = None,
    include_duplicates: bool = True,
    include_synthetic: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Filtered evidence listing used by the UI (section F)."""
    clauses: list[str] = []
    params: list[Any] = []

    if not include_synthetic:
        clauses.append("e.is_synthetic = 0")
    if not include_duplicates:
        clauses.append("e.duplicate_status <> 'duplicate'")
    if source_type:
        clauses.append("e.source_type = ?")
        params.append(source_type)
    if relevance:
        clauses.append("e.relevance = ?")
        params.append(relevance)
    if validation_status:
        clauses.append("e.validation_status = ?")
        params.append(validation_status)
    if theme:
        clauses.append(
            "EXISTS (SELECT 1 FROM evidence_themes t "
            "WHERE t.evidence_id = e.evidence_id AND t.theme = ?)"
        )
        params.append(theme)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])

    rows = conn.execute(
        f"SELECT e.* FROM evidence e {where} ORDER BY e.row_id LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


# ``set_classification`` was retired with migration v3 (ADR-016). The four
# legacy interpretation columns on ``evidence`` are frozen by trigger; M3
# classifications are written only to the append-only ``screening_results``
# store. ``validation_status`` and ``review_status`` remain ingestion/M2
# lifecycle fields and are still written by ``insert_evidence`` and
# ``queue_for_review``.


# ---------------------------------------------------------------------------
# Rejected source rows
# ---------------------------------------------------------------------------


def log_rejected_row(
    conn: sqlite3.Connection,
    *,
    raw_row: Mapping[str, Any],
    reason_code: str,
    reason: str,
    source_locator: str,
    source_file: str | None = None,
    source_record_number: str | None = None,
    source_native_id: str | None = None,
    run_id: int | None = None,
) -> int:
    """Record a source row that cannot become evidence.

    Rejected rows are kept forever in ``ingestion_rejected_rows`` with their
    complete raw payload. They are not evidence, are not counted as evidence,
    and are never silently discarded. Logging is idempotent for the same
    locator and reason.
    """
    if reason_code not in REJECTION_REASON_CODES:
        raise RepositoryError(f"invalid rejection reason_code: {reason_code!r}")
    if not source_locator or not str(source_locator).strip():
        raise RepositoryError("rejected row needs a source_locator")
    if not reason or not str(reason).strip():
        raise RepositoryError("rejected row needs a human-readable reason")

    existing = conn.execute(
        "SELECT rejection_id FROM ingestion_rejected_rows "
        "WHERE source_locator = ? AND reason_code = ?",
        (source_locator, reason_code),
    ).fetchone()
    if existing:
        return int(existing["rejection_id"])

    payload = json.dumps(
        dict(raw_row), ensure_ascii=False, sort_keys=True, default=str
    )
    cur = conn.execute(
        "INSERT INTO ingestion_rejected_rows ("
        "run_id, source_file, source_record_number, source_native_id, "
        "source_locator, reason_code, reason, raw_row_json, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            source_file,
            source_record_number,
            source_native_id,
            source_locator,
            reason_code,
            reason,
            payload,
            utc_now(),
        ),
    )
    rejection_id = int(cur.lastrowid)
    log_audit(
        conn,
        entity="ingestion_rejected_row",
        entity_id=str(rejection_id),
        action="logged",
        detail=f"{reason_code} locator={source_locator}",
    )
    return rejection_id


def list_rejected_rows(
    conn: sqlite3.Connection,
    *,
    run_id: int | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Read the rejection log, newest run first, for audit and the UI."""
    clauses: list[str] = []
    params: list[Any] = []
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT * FROM ingestion_rejected_rows {where} "
        f"ORDER BY rejection_id DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def rejected_row_count(conn: sqlite3.Connection, *, run_id: int | None = None) -> int:
    if run_id is None:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM ingestion_rejected_rows"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM ingestion_rejected_rows WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    return int(row["n"])


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


def queue_for_review(
    conn: sqlite3.Connection,
    evidence_id: str,
    reason: str,
    *,
    priority: int = 3,
) -> int:
    """Add a record to the manual-review queue (idempotent per reason)."""
    if not 1 <= priority <= 5:
        raise RepositoryError("priority must be between 1 and 5")
    cur = conn.execute(
        "INSERT OR IGNORE INTO review_queue (evidence_id, reason, priority, status, created_at) "
        "VALUES (?, ?, ?, 'queued', ?)",
        (evidence_id, reason, priority, utc_now()),
    )
    conn.execute(
        "UPDATE evidence SET review_status = 'queued', updated_at = ? "
        "WHERE evidence_id = ? AND review_status = 'not_queued'",
        (utc_now(), evidence_id),
    )
    return int(cur.lastrowid or 0)


def pending_review_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM review_queue WHERE status IN ('queued', 'in_review')"
    ).fetchone()
    return int(row["n"])


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def log_audit(
    conn: sqlite3.Connection,
    *,
    entity: str,
    entity_id: str | None,
    action: str,
    detail: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (entity, entity_id, action, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (entity, entity_id, action, detail, utc_now()),
    )


# ---------------------------------------------------------------------------
# Aggregates (evidence counts come from distinct records, never chunks)
# ---------------------------------------------------------------------------


def counts_summary(
    conn: sqlite3.Connection, *, include_synthetic: bool = False
) -> dict[str, Any]:
    """Counts for the dashboard, computed from distinct evidence records.

    ``genuine_records`` always excludes synthetic rows and flagged duplicates.
    ``total_records`` counts every stored row, so the UI can show the gap.
    """

    def scalar(sql: str, params: Sequence[Any] = ()) -> int:
        row = conn.execute(sql, params).fetchone()
        return int(row[0])

    genuine_filter = "" if include_synthetic else " WHERE is_synthetic = 0"

    total = scalar("SELECT COUNT(*) FROM evidence")
    genuine = scalar(
        "SELECT COUNT(*) FROM evidence WHERE duplicate_status <> 'duplicate'"
        + (" AND is_synthetic = 0" if not include_synthetic else "")
    )
    duplicates = scalar(
        "SELECT COUNT(*) FROM evidence WHERE duplicate_status = 'duplicate'"
        + (" AND is_synthetic = 0" if not include_synthetic else "")
    )
    possible_duplicates = scalar(
        "SELECT COUNT(*) FROM evidence WHERE duplicate_status = 'possible_duplicate'"
        + (" AND is_synthetic = 0" if not include_synthetic else "")
    )

    by_relevance = {
        (row["relevance"] or "unclassified"): int(row["n"])
        for row in conn.execute(
            "SELECT relevance, COUNT(*) AS n FROM evidence"
            + genuine_filter
            + " GROUP BY relevance ORDER BY n DESC"
        ).fetchall()
    }

    sources = {
        row["source_type"]: int(row["n"])
        for row in conn.execute(
            "SELECT source_type, COUNT(*) AS n FROM v_genuine_evidence "
            "GROUP BY source_type ORDER BY n DESC"
        ).fetchall()
    }

    themes = {
        row["theme"]: int(row["n"])
        for row in conn.execute(
            "SELECT t.theme AS theme, COUNT(*) AS n FROM evidence_themes t "
            "JOIN v_genuine_evidence e ON e.evidence_id = t.evidence_id "
            "GROUP BY t.theme ORDER BY n DESC"
        ).fetchall()
    }

    return {
        "total_records": total,
        "genuine_records": genuine,
        "duplicate_records": duplicates,
        "possible_duplicate_records": possible_duplicates,
        "rejected_records": rejected_row_count(conn),
        "synthetic_records": scalar(
            "SELECT COUNT(*) FROM evidence WHERE is_synthetic = 1"
        ),
        "by_relevance": by_relevance,
        "by_source": sources,
        "by_theme": themes,
        "human_validated": scalar(
            "SELECT COUNT(*) FROM evidence WHERE validation_status = 'human_validated'"
            + (" AND is_synthetic = 0" if not include_synthetic else "")
        ),
        "needs_review": scalar(
            "SELECT COUNT(*) FROM evidence WHERE validation_status = 'needs_review'"
            + (" AND is_synthetic = 0" if not include_synthetic else "")
        ),
        "review_queued": pending_review_count(conn),
    }


def distinct_sources_for_records(
    conn: sqlite3.Connection, evidence_ids: Sequence[str]
) -> int:
    """Number of independent sources supporting a given set of records.

    Used later by opportunity quantification; exposed now so the counting rule
    lives in exactly one place.
    """
    if not evidence_ids:
        return 0
    placeholders = ",".join("?" for _ in evidence_ids)
    row = conn.execute(
        f"SELECT COUNT(DISTINCT source_id) AS n FROM evidence "
        f"WHERE evidence_id IN ({placeholders}) AND source_id IS NOT NULL",
        list(evidence_ids),
    ).fetchone()
    return int(row["n"])


__all__ = [
    "RepositoryError",
    "get_or_create_source",
    "start_ingestion_run",
    "finish_ingestion_run",
    "find_runs_by_file_hash",
    "classify_duplicate",
    "insert_evidence",
    "get_evidence",
    "get_themes",
    "list_evidence",
    "log_rejected_row",
    "list_rejected_rows",
    "rejected_row_count",
    "queue_for_review",
    "pending_review_count",
    "log_audit",
    "counts_summary",
    "distinct_sources_for_records",
]
