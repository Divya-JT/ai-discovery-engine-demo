"""Duplicate detection and the re-upload guard.

Policy (approved as D-M2-1):
* a *true* duplicate requires the same normalised text **and** a known,
  matching source URL **and** a known, matching author;
* matching text with a different or missing URL/author is a
  ``possible_duplicate``;
* every match is **flagged**, never deleted;
* re-uploading an already-ingested file is **blocked before import**, so a
  cumulative upload cannot double the evidence base;
* file identity is a SHA-256 of the raw bytes.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from database.migrations import utc_now
from ingestion.identity import file_hash, identity_key

__all__ = [
    "hash_file_path",
    "is_file_already_ingested",
    "find_exact_duplicate_groups",
    "flag_unflagged_duplicates",
    "duplicate_summary",
]


def hash_file_path(path: str | Path) -> str:
    """SHA-256 of a file's bytes, computed in chunks (safe for large files)."""
    source = Path(path)
    hasher = hashlib.sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def is_file_already_ingested(conn: sqlite3.Connection, file_hash_value: str) -> bool:
    """True when a *completed* run already exists for identical file bytes."""
    row = conn.execute(
        "SELECT 1 FROM ingestion_runs WHERE file_hash = ? AND status = 'completed' LIMIT 1",
        (file_hash_value,),
    ).fetchone()
    return row is not None


def find_exact_duplicate_groups(
    conn: sqlite3.Connection, *, min_count: int = 2
) -> list[dict[str, Any]]:
    """Groups of records sharing a content hash (read-only report)."""
    rows = conn.execute(
        "SELECT content_hash, COUNT(*) AS n, GROUP_CONCAT(evidence_id) AS ids "
        "FROM evidence GROUP BY content_hash HAVING COUNT(*) >= ? "
        "ORDER BY n DESC",
        (min_count,),
    ).fetchall()
    return [
        {
            "content_hash": row["content_hash"],
            "count": int(row["n"]),
            "evidence_ids": str(row["ids"]).split(","),
        }
        for row in rows
    ]


def flag_unflagged_duplicates(conn: sqlite3.Connection) -> int:
    """Re-apply the approved duplicate policy to rows still marked ``unique``.

    Only the ``duplicate_status``/``duplicate_of`` columns are touched; text and
    identity never change, and nothing is deleted. Returns rows updated.
    """
    rows = conn.execute(
        "SELECT evidence_id, content_hash, source_url, author, duplicate_status "
        "FROM evidence WHERE is_synthetic = 0 ORDER BY row_id"
    ).fetchall()

    first_same_text: dict[str, str] = {}
    first_true_identity: dict[tuple[str, str, str], str] = {}
    updated = 0

    for row in rows:
        text_hash = row["content_hash"]
        url = identity_key(row["source_url"])
        author = identity_key(row["author"])
        true_identity = (text_hash, url, author) if url and author else None

        if row["duplicate_status"] == "unique" and text_hash in first_same_text:
            if true_identity and true_identity in first_true_identity:
                status = "duplicate"
                reference = first_true_identity[true_identity]
            else:
                status = "possible_duplicate"
                reference = first_same_text[text_hash]
            conn.execute(
                "UPDATE evidence SET duplicate_status = ?, duplicate_of = ?, "
                "updated_at = ? WHERE evidence_id = ?",
                (status, reference, utc_now(), row["evidence_id"]),
            )
            updated += 1

        first_same_text.setdefault(text_hash, row["evidence_id"])
        if true_identity is not None:
            first_true_identity.setdefault(true_identity, row["evidence_id"])

    return updated


def duplicate_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Counts used by the dashboard quality indicators."""
    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN duplicate_status = 'unique' THEN 1 ELSE 0 END) AS unique_n, "
        "SUM(CASE WHEN duplicate_status = 'duplicate' THEN 1 ELSE 0 END) AS dup_n, "
        "SUM(CASE WHEN duplicate_status = 'possible_duplicate' THEN 1 ELSE 0 END) "
        "AS possible_n "
        "FROM evidence WHERE is_synthetic = 0"
    ).fetchone()
    groups = find_exact_duplicate_groups(conn)
    return {
        "unique": int(row["unique_n"] or 0),
        "exact_duplicates": int(row["dup_n"] or 0),
        "possible_duplicates": int(row["possible_n"] or 0),
        "duplicate_groups": len(groups),
    }


def bytes_hash(data: bytes) -> str:
    """Convenience wrapper so callers need only one import."""
    return file_hash(data)
