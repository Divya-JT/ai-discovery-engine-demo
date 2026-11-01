"""Central configuration for the AI Discovery Engine.

Every runtime setting resolves once, from environment variables. A local
``.env`` file (never committed) may supply them. Nothing sensitive is
hard-coded here.

Design notes
------------
* Paths are absolute and rooted at the project directory, so the app behaves
  the same regardless of the shell's working directory.
* Vocabulary constants (relevance, behaviour, themes, ...) are the single
  source of truth for both the SQLite CHECK constraints and the UI.
* Configuration is deliberately explicit and boring: one dataclass, one
  accessor, no framework.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT: Path = Path(__file__).resolve().parent
DATA_DIR: Path = PROJECT_ROOT / "data"
UPLOADS_DIR: Path = DATA_DIR / "uploads"
EXPORTS_DIR: Path = DATA_DIR / "exports"
CHROMA_DIR: Path = DATA_DIR / "chroma"
DB_DIR: Path = DATA_DIR / "database"
MODELS_DIR: Path = DATA_DIR / "models"
DOCS_DIR: Path = PROJECT_ROOT / "docs"
DEFAULT_DB_PATH: Path = DATA_DIR / "demo_discovery_engine.db"

# ---------------------------------------------------------------------------
# Controlled vocabularies (section 7 of the master prompt)
# ---------------------------------------------------------------------------

SOURCE_TYPE_VALUES: tuple[str, ...] = (
    "google_play",
    "reddit",
    "youtube",
    "website_forum",
    "survey",
    "interview",
    "other",
)

#: Legacy v1/v2 vocabulary. ``manual_review_required`` is retained only because
#: the existing ``evidence.relevance`` CHECK constraint still accepts it. It is
#: **not** part of the M3 screening vocabulary below, and the legacy column is
#: frozen at NULL after migration v3.
RELEVANCE_VALUES: tuple[str, ...] = (
    "relevant",
    "indirectly_relevant",
    "unrelated",
    "insufficient_information",
    "manual_review_required",
)

BEHAVIOUR_VALUES: tuple[str, ...] = (
    "sip_initiation",
    "sip_continuation",
    "missed_instalment",
    "sip_reduction",
    "sip_pause",
    "sip_cancellation",
    "platform_migration",
    "complete_discontinuation",
    "unspecified",
)

# Directness was removed as an independent screening field (ADR-025, 2026-10-31).
# The legacy evidence.directness column remains only for historical rows and is
# never written by M4+ screening.

VALIDATION_VALUES: tuple[str, ...] = (
    "unclassified",
    "llm_classified",
    "needs_review",
    "human_validated",
    "rejected",
)

# ---------------------------------------------------------------------------
# M3 screening-store vocabularies (migration v3)
#
# These mirror the CHECK constraints in ``database/screening_schema.py``.
# They are deliberately separate from the legacy ``evidence`` vocabularies:
# review is a workflow state, never a relevance value.
# ---------------------------------------------------------------------------

SCREENING_RELEVANCE_VALUES: tuple[str, ...] = (
    "relevant",
    "indirectly_relevant",
    "unrelated",
    "insufficient_information",
)

FIRST_TIME_STATUS_VALUES: tuple[str, ...] = (
    "explicit_self_reported_first_time",
    "explicit_self_reported_experienced",
    "ambiguous_signal",
    "not_established",
)

SIP_TENURE_VALUES: tuple[str, ...] = (
    "within_first_12_months_explicit",
    "beyond_first_12_months_explicit",
    "not_established",
)

SCREENING_VALIDATION_VALUES: tuple[str, ...] = (
    "machine_validated",
    "needs_review",
    "human_validated",
    "rejected",
)

SCREENING_REVIEW_DECISIONS: tuple[str, ...] = (
    "confirmed",
    "changed",
    "rejected",
    "escalated",
)

REVIEWER_ROLES: tuple[str, ...] = ("reviewer", "adjudicator", "product_owner")

RUN_TYPES: tuple[str, ...] = ("pilot", "full", "qa")

SCREENING_EVENT_TYPES: tuple[str, ...] = (
    "run_created",
    "run_started",
    "run_completed",
    "run_aborted",
)

REVIEW_REASON_CODES: tuple[str, ...] = (
    "low_confidence",
    "ambiguous_evidence",
    "direct_first_time_claim",
    "direct_behaviour_claim",
    "possible_duplicate_corroboration",
    "off_topic_flag",
    "quote_invalid",
    "disagreement",
    "quality_flag",
    "reviewer_requested",
)

DUPLICATE_VALUES: tuple[str, ...] = ("unique", "possible_duplicate", "duplicate")

#: Reasons a source row may be logged as rejected instead of stored as evidence.
#: Must match the CHECK constraint on ``ingestion_rejected_rows.reason_code``.
REJECTION_REASON_CODES: tuple[str, ...] = (
    "EMPTY_TEXT",
    "MISSING_SOURCE_LOCATOR",
    "INVALID_SOURCE_TYPE",
    "MISSING_REQUIRED_FIELD",
    "SCRAPER_ERROR",
    "OTHER",
)

REVIEW_STATUS_VALUES: tuple[str, ...] = ("not_queued", "queued", "in_review", "resolved")

REVIEW_PRIORITY_VALUES: tuple[int, ...] = (1, 2, 3, 4, 5)

THEME_VOCABULARY: tuple[str, ...] = (
    "returns_and_performance_expectations",
    "market_volatility",
    "financial_uncertainty",
    "cash_flow_constraints",
    "mandate_and_auto_debit_problems",
    "transaction_failures",
    "app_usability",
    "information_gaps",
    "trust_and_transparency",
    "confusion_about_nav_or_units",
    "monitoring_behaviour",
    "external_influence",
    "investment_knowledge",
    "goal_changes",
)

#: Default weights for the M5 prioritisation model. Decision-support only.
DEFAULT_OPPORTUNITY_WEIGHTS: dict[str, float] = {
    "evidence_volume": 0.15,
    "source_diversity": 0.12,
    "direct_evidence": 0.12,
    "human_validation": 0.10,
    "user_impact": 0.13,
    "business_relevance": 0.14,
    "measurability": 0.08,
    "segment_coverage": 0.06,
    "contradictory_evidence": 0.05,
    "uncertainty": 0.05,
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _load_dotenv(path: Path) -> None:
    """Populate ``os.environ`` from a simple ``KEY=VALUE`` file.

    Existing environment variables always win. Implemented locally to avoid a
    dependency for something this small.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable snapshot of runtime configuration."""

    project_root: Path
    db_path: Path
    uploads_dir: Path
    exports_dir: Path
    chroma_dir: Path
    models_dir: Path

    # Classification
    confidence_threshold: float = 0.65

    # Local LLM (LM Studio, OpenAI-compatible). Not contacted during M1.
    llm_base_url: str = "http://localhost:1234/v1"
    llm_api_key: str = ""
    llm_model: str = "qwen3-4b-instruct-2507"
    llm_timeout_seconds: int = 120
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1200

    # Embeddings / vector store (not downloaded during M1)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_batch_size: int = 32
    chroma_collection: str = "evidence_chunks"
    chunk_size_chars: int = 900
    chunk_overlap_chars: int = 150

    # Retrieval
    retrieval_top_k: int = 12

    # Opportunity model (decision-support estimates, never "truth")
    opportunity_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_OPPORTUNITY_WEIGHTS)
    )

    @property
    def database_url(self) -> str:
        """SQLite URI, used for read-only connections in tests and exports."""
        return f"file:{self.db_path}?mode=rw"

    def as_redacted_dict(self) -> dict[str, object]:
        """Safe-to-display settings; secrets are masked."""
        return {
            "project_root": str(self.project_root),
            "db_path": str(self.db_path),
            "chroma_dir": str(self.chroma_dir),
            "confidence_threshold": self.confidence_threshold,
            "llm_base_url": self.llm_base_url,
            "llm_model": self.llm_model,
            "llm_api_key": "***" if self.llm_api_key else "",
            "embedding_model": self.embedding_model,
            "chroma_collection": self.chroma_collection,
            "retrieval_top_k": self.retrieval_top_k,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, loading ``.env`` on first use."""
    _load_dotenv(PROJECT_ROOT / ".env")

    db_path = Path(_env_str("ADE_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()
    chroma_dir = Path(_env_str("ADE_CHROMA_DIR", str(CHROMA_DIR))).expanduser()

    return Settings(
        project_root=PROJECT_ROOT,
        db_path=db_path,
        uploads_dir=UPLOADS_DIR,
        exports_dir=EXPORTS_DIR,
        chroma_dir=chroma_dir,
        models_dir=MODELS_DIR,
        confidence_threshold=_env_float("ADE_CONFIDENCE_THRESHOLD", 0.65),
        llm_base_url=_env_str("ADE_LLM_BASE_URL", "http://localhost:1234/v1"),
        llm_api_key=_env_str("ADE_LLM_API_KEY", ""),
        llm_model=_env_str("ADE_LLM_MODEL", "qwen3-4b-instruct-2507"),
        llm_timeout_seconds=_env_int("ADE_LLM_TIMEOUT_SECONDS", 120),
        llm_temperature=_env_float("ADE_LLM_TEMPERATURE", 0.1),
        llm_max_tokens=_env_int("ADE_LLM_MAX_TOKENS", 1200),
        embedding_model=_env_str("ADE_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
        embedding_batch_size=_env_int("ADE_EMBEDDING_BATCH_SIZE", 32),
        chroma_collection=_env_str("ADE_CHROMA_COLLECTION", "evidence_chunks"),
        chunk_size_chars=_env_int("ADE_CHUNK_SIZE_CHARS", 900),
        chunk_overlap_chars=_env_int("ADE_CHUNK_OVERLAP_CHARS", 150),
        retrieval_top_k=_env_int("ADE_RETRIEVAL_TOP_K", 12),
        opportunity_weights=dict(DEFAULT_OPPORTUNITY_WEIGHTS),
    )


def ensure_directories(settings: Settings | None = None) -> None:
    """Create the runtime data directories. Idempotent and side-effect free otherwise."""
    settings = settings or get_settings()
    for path in (
        settings.db_path.parent,
        settings.uploads_dir,
        settings.exports_dir,
        settings.chroma_dir,
        settings.models_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)


def reset_settings_cache() -> None:
    """Clear the cached settings. Used by tests that patch the environment."""
    get_settings.cache_clear()
