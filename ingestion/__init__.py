"""Evidence ingestion foundation.

M1 exposes reading, profiling, normalising, validating and duplicate-guarding.
Writing evidence rows into SQLite is deliberately left to M2.
"""

from ingestion.deduplication import hash_file_path, is_file_already_ingested
from ingestion.identity import content_hash, evidence_id_for, normalize_for_search
from ingestion.normalizer import (
    CANONICAL_FIELDS,
    normalize_record,
    suggest_column_mapping,
)
from ingestion.validator import has_usable_text, validate_record

__all__ = [
    "CANONICAL_FIELDS",
    "content_hash",
    "evidence_id_for",
    "has_usable_text",
    "hash_file_path",
    "is_file_already_ingested",
    "normalize_for_search",
    "normalize_record",
    "suggest_column_mapping",
    "validate_record",
]
