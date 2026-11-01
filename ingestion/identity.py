"""Evidence identity: normalisation, content hashing and evidence IDs.

Two different normalisations exist on purpose:

* ``normalize_for_hash`` is aggressive (case-folded, whitespace collapsed) so
  that the same review pasted twice with different spacing still hashes the
  same and is flagged as a duplicate.
* ``normalize_for_search`` keeps the original casing so excerpts stay readable
  in the UI, while still collapsing layout whitespace for search.

Evidence IDs are deterministic: re-ingesting the same record produces the same
ID, which makes cumulative uploads idempotent without a counter table.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

EVIDENCE_ID_PREFIX = "EVD-"
EVIDENCE_ID_HEX_LENGTH = 8

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_hash(text: str) -> str:
    """Canonical form used only for hashing (case-insensitive, whitespace-collapsed)."""
    if text is None:
        return ""
    folded = unicodedata.normalize("NFKC", str(text)).casefold()
    return _WHITESPACE_RE.sub(" ", folded).strip()


def normalize_for_search(text: str) -> str:
    """Readable canonical form stored alongside the original text."""
    if text is None:
        return ""
    normalised = unicodedata.normalize("NFKC", str(text))
    return _WHITESPACE_RE.sub(" ", normalised).strip()


def content_hash(text: str) -> str:
    """SHA-256 of the hash-normalised text."""
    canonical = normalize_for_hash(text)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def identity_key(value: str | None) -> str | None:
    """Comparison key for duplicate identity fields (URL / author).

    Missing and blank values are **unknown**, not equal to each other. Two
    records that both lack a source URL are not thereby evidence of the same
    location, so they can never be a *true* duplicate on that basis. The
    original value is never mutated; this helper only returns a comparison key.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def file_hash(data: bytes) -> str:
    """SHA-256 of raw file bytes, used to block duplicate uploads."""
    return hashlib.sha256(data).hexdigest()


def evidence_id_for(
    source_type: str,
    text_hash: str,
    *,
    source_url: str | None = None,
    source_locator: str | None = None,
) -> str:
    """Deterministic, human-readable evidence ID.

    The same text from the same source location yields the same ID. The same
    text from a *different* source location yields a different ID and is then
    flagged as a duplicate rather than silently dropped.
    """
    seed = "|".join(
        [
            normalize_for_hash(source_type),
            text_hash,
            normalize_for_hash(source_url or ""),
            normalize_for_hash(source_locator or ""),
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"{EVIDENCE_ID_PREFIX}{digest[:EVIDENCE_ID_HEX_LENGTH].upper()}"


def is_valid_evidence_id(value: str) -> bool:
    """Basic shape check, used when validating UI input and citations."""
    if not value.startswith(EVIDENCE_ID_PREFIX):
        return False
    suffix = value[len(EVIDENCE_ID_PREFIX) :]
    return len(suffix) == EVIDENCE_ID_HEX_LENGTH and all(
        c in "0123456789ABCDEF" for c in suffix
    )
