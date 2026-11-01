"""Map arbitrary research files onto the canonical evidence fields.

The normaliser never discards information: every unrecognised column is kept in
``source_metadata`` so source-specific detail survives ingestion.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

CANONICAL_FIELDS: tuple[str, ...] = (
    "original_text",
    "source_name",
    "source_type",
    "platform",
    "source_url",
    "source_date",
    "collection_date",
    "source_locator",
)

#: Canonical field -> accepted source column names (lower-case, normalised).
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "original_text": (
        "original_text",
        "text",
        "review_text",
        "review",
        "comment",
        "comment_text",
        "content",
        "body",
        "post",
        "message",
        "evidence_text",
        "quote",
        "snippet",
        "description",
    ),
    "source_name": ("source_name", "source", "dataset", "file", "origin"),
    "source_type": (
        "source_type",
        "type",
        "data_source",
        "channel",
        "platform_type",
    ),
    "platform": ("platform", "app", "site", "service", "channel_name"),
    "source_url": ("source_url", "url", "link", "permalink", "post_url", "href"),
    "source_date": (
        "source_date",
        "date",
        "review_date",
        "comment_date",
        "published_at",
        "created_at",
        "post_date",
        "timestamp",
    ),
    "collection_date": (
        "collection_date",
        "collected_at",
        "scraped_at",
        "extracted_at",
    ),
    "source_locator": (
        "source_locator",
        "locator",
        "row",
        "row_id",
        "comment_id",
        "review_id",
        "page",
        "page_number",
        "position",
        "index",
    ),
}

_PLATFORM_HINTS: tuple[tuple[str, str], ...] = (
    ("google play", "google_play"),
    ("play store", "google_play"),
    ("android", "google_play"),
    ("reddit", "reddit"),
    ("youtube", "youtube"),
    ("forum", "website_forum"),
    ("website", "website_forum"),
    ("survey", "survey"),
    ("interview", "interview"),
)


def normalize_column_name(name: str) -> str:
    """lowercase, strip punctuation/whitespace: 'Review Text' -> 'review_text'."""
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", str(name).strip().lower())
    return cleaned.strip("_")


def suggest_column_mapping(columns: list[str]) -> dict[str, str]:
    """Suggest canonical -> original column. Unmapped canonical fields are absent."""
    normalised = {column: normalize_column_name(column) for column in columns}
    mapping: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            match = next(
                (column for column, norm in normalised.items() if norm == alias), None
            )
            if match is not None:
                mapping[field] = match
                break
    return mapping


def infer_source_type(value: str | None, fallback: str = "other") -> str:
    """Map a free-text platform/source label onto the controlled vocabulary."""
    if not value:
        return fallback
    lowered = str(value).strip().lower()
    for hint, source_type in _PLATFORM_HINTS:
        if hint in lowered:
            return source_type
    return fallback


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_record(
    raw: Mapping[str, Any],
    mapping: Mapping[str, str],
    *,
    default_source_name: str | None = None,
    default_source_type: str | None = None,
) -> dict[str, Any]:
    """Convert one raw row into canonical fields plus preserved metadata.

    Returns a dict with keys from ``CANONICAL_FIELDS`` plus ``source_metadata``.
    No database access, no LLM, no side effects.
    """
    canonical: dict[str, Any] = {
        field: _clean(raw.get(mapping[field])) if field in mapping else None
        for field in CANONICAL_FIELDS
    }

    canonical["source_name"] = (
        canonical["source_name"] or default_source_name or "unspecified_source"
    )
    canonical["source_type"] = (
        canonical["source_type"]
        or default_source_type
        or infer_source_type(canonical["platform"] or canonical["source_name"])
    )

    mapped_columns = set(mapping.values())
    canonical["source_metadata"] = {
        str(column): raw.get(column)
        for column in raw.keys()
        if column not in mapped_columns
    }
    return canonical
