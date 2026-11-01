"""Data-transfer objects for evidence storage.

These dataclasses are deliberately thin: the database schema is the contract,
and these types describe what callers hand to the repository. They contain no
SQL and no LLM logic.

Rule enforced by design: ``original_text`` is the untouched source text. It is
never rewritten, trimmed in storage, or replaced by a summary. ``normalized_text``
is a separate, derived column used for hashing and search.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class EvidenceInput:
    """A single evidence record ready to be stored."""

    original_text: str
    source_name: str
    source_type: str
    platform: str | None = None
    author: str | None = None
    source_url: str | None = None
    source_date: str | None = None
    collection_date: str | None = None
    source_locator: str | None = None
    source_metadata: Mapping[str, Any] = field(default_factory=dict)
    is_synthetic: bool = False
    themes: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class InsertResult:
    """Outcome of storing one evidence record."""

    evidence_id: str
    created: bool
    duplicate_status: str
    duplicate_of: str | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_status == "duplicate"


@dataclass(slots=True)
class IngestionRunInfo:
    """Read model for one ingestion run."""

    run_id: int
    file_name: str
    file_hash: str
    status: str
    rows_read: int
    records_created: int
    records_flagged_duplicate: int
    records_invalid: int
    started_at: str
    completed_at: str | None = None
