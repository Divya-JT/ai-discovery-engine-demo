"""AI Discovery Engine — mentor-ready Streamlit application.

Design rules
------------
* The research database is opened read-only. The app does not import, reconcile,
  sign off, or change the research schema.
* Uploads are processed into an isolated sandbox database so the stored research
  corpus is untouched.
* Theme, opportunity and Q&A output is derived from stored evidence text with
  transparent, deterministic rules. It is labelled exploratory, never presented
  as validated research findings.
* Every answer/opportunity shows the evidence IDs and source links behind it.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd
import streamlit as st

from config import (
    DATA_DIR,
    EXPORTS_DIR,
    SOURCE_TYPE_VALUES,
    UPLOADS_DIR,
    get_settings,
)
from database.db import connect
from database.migrations import apply_migrations
from database.models import EvidenceInput
from database.repository import insert_evidence
from ingestion.file_reader import read_table
from ingestion.ingestion_service import (
    ReIngestBlocked,
    close_run,
    ingestion_history,
    open_run,
    prepare_upload,
    preview_records,
    validate_upload,
)
from ingestion.normalizer import normalize_record, suggest_column_mapping
from ingestion.validator import validate_record

APP_TITLE = "AI Discovery Engine"
SANDBOX_DB = Path(
    os.environ.get("ADE_SANDBOX_DB", str(DATA_DIR / "app" / "upload_sandbox.db"))
)
UPLOAD_DIR = Path(os.environ.get("ADE_UPLOADS_DIR", str(UPLOADS_DIR)))
EVIDENCE_LIMIT = 2000

THEME_RULES: dict[str, dict[str, Any]] = {
    "transaction_failures": {
        "label": "Transaction failures",
        "keywords": ["failed", "failure", "declined", "transaction", "payment failed", "pending", "error"],
        "problem": "Payments or transactions fail, leaving users unsure whether money moved.",
        "opportunity": "Proactive failure detection, retry guidance and clear transaction status.",
    },
    "mandate_and_auto_debit_problems": {
        "label": "Mandate and auto-debit problems",
        "keywords": ["mandate", "auto debit", "auto-debit", "autopay", "e-mandate", "bank mandate"],
        "problem": "Mandate setup or auto-debit behaviour is confusing or unreliable.",
        "opportunity": "Mandate setup diagnostics, plain-language status and safe repair flows.",
    },
    "app_usability": {
        "label": "App usability and friction",
        "keywords": ["app", "ui", "interface", "usability", "bug", "crash", "update", "navigation", "slow", "glitch"],
        "problem": "Users encounter friction, bugs or confusing journeys in the product interface.",
        "opportunity": "Simplify high-friction journeys and surface actionable in-app help.",
    },
    "information_gaps": {
        "label": "Information and guidance gaps",
        "keywords": ["information", "unclear", "confus", "didn't know", "no idea", "explain", "support", "help", "guidance"],
        "problem": "Users lack clear explanations at the moment they need them.",
        "opportunity": "Contextual SIP education, definitions and guided next steps.",
    },
    "trust_and_transparency": {
        "label": "Trust, charges and transparency",
        "keywords": ["trust", "transparent", "hidden", "charges", "fee", "fraud", "scam", "reliable", "misleading"],
        "problem": "Users question charges, visibility or the reliability of the platform.",
        "opportunity": "Transparent charges, mandate and portfolio visibility with plain explanations.",
    },
    "cash_flow_constraints": {
        "label": "Cash-flow constraints",
        "keywords": ["cash", "money", "funds", "liquidity", "expense", "budget", "tight", "short of"],
        "problem": "Temporary cash-flow pressure makes a fixed SIP commitment hard to sustain.",
        "opportunity": "Flexible pause/resume and lower-commitment options with clear consequences.",
    },
    "market_volatility": {
        "label": "Market volatility and timing",
        "keywords": ["volatile", "volatility", "market fall", "market crash", "correction", "down market", "bear market"],
        "problem": "Market movements trigger uncertainty about continuing or changing a SIP.",
        "opportunity": "Volatility-aware guidance and behavioural nudges without return promises.",
    },
    "returns_and_performance_expectations": {
        "label": "Returns and performance expectations",
        "keywords": ["return", "returns", "profit", "loss", "xirr", "cagr", "nav", "performance", "gain"],
        "problem": "Users may hold unclear or unrealistic expectations about returns and losses.",
        "opportunity": "Clear return expectations, portfolio context and risk explanations.",
    },
    "monitoring_behaviour": {
        "label": "Monitoring and engagement",
        "keywords": ["track", "monitor", "portfolio", "check", "statement", "dashboard", "alert"],
        "problem": "Users want a simpler way to monitor progress and know when to act.",
        "opportunity": "Portfolio monitoring, statements and proactive alerts.",
    },
    "external_influence": {
        "label": "External influence",
        "keywords": ["friend", "advisor", "youtube", "telegram", "social media", "recommend", "family", "influencer"],
        "problem": "Investment decisions are shaped by social, family or influencer input.",
        "opportunity": "Trusted explanation and source-aware guidance for externally influenced decisions.",
    },
    "investment_knowledge": {
        "label": "Investment knowledge",
        "keywords": ["beginner", "new to", "knowledge", "learn", "understand", "educat", "first time"],
        "problem": "Users are learning SIP concepts while making live financial decisions.",
        "opportunity": "Beginner-safe explanations and progressive disclosure.",
    },
    "goal_changes": {
        "label": "Goal changes",
        "keywords": ["goal", "target", "marriage", "house", "education", "retirement", "plan changed", "financial goal"],
        "problem": "Life or goal changes require adjustments to an existing SIP plan.",
        "opportunity": "Goal-linked SIP planning and guided plan adjustments.",
    },
    "confusion_about_nav_or_units": {
        "label": "NAV, units and valuation confusion",
        "keywords": ["nav", "units", "allot", "unit price", "unit"],
        "problem": "Users struggle to interpret NAV, units or valuation statements.",
        "opportunity": "Plain-language NAV/unit explanations and statement walkthroughs.",
    },
    "financial_uncertainty": {
        "label": "Financial uncertainty",
        "keywords": ["uncertain", "job loss", "lost job", "salary", "income", "pandemic", "covid", "emergency fund", "financial trouble"],
        "problem": "Income or employment uncertainty creates anxiety about fixed commitments.",
        "opportunity": "Scenario planning and flexible commitment controls for uncertain income.",
    },
}

PREDEFINED_QUESTIONS: list[dict[str, Any]] = [
    {
        "title": "Why do users pause or stop their SIPs?",
        "terms": ["pause", "paused", "stop", "stopped", "cancel", "cancelled", "discontinue", "sip"],
    },
    {
        "title": "What transaction or auto-debit problems are reported?",
        "terms": ["failed", "failure", "declined", "transaction", "mandate", "auto debit", "payment"],
    },
    {
        "title": "What app or usability issues appear?",
        "terms": ["app", "interface", "ui", "bug", "crash", "slow", "navigation"],
    },
    {
        "title": "What information or trust gaps appear?",
        "terms": ["unclear", "confus", "hidden", "charges", "fee", "trust", "support"],
    },
    {
        "title": "What financial pressures affect investing?",
        "terms": ["salary", "income", "money", "expense", "cash", "job"],
    },
    {
        "title": "What do users say about returns and volatility?",
        "terms": ["return", "returns", "profit", "loss", "market", "volatility"],
    },
    {
        "title": "What causes missed or failed SIP instalments?",
        "terms": ["missed", "miss", "failed", "failure", "debit", "mandate", "bank", "balance", "insufficient"],
    },
    {
        "title": "What helps users continue their SIPs?",
        "terms": ["continue", "continued", "continuing", "discipline", "long term", "long-term", "reminder", "automatic", "automated", "stay invested"],
    },
    {
        "title": "How do life or goal changes affect SIPs?",
        "terms": ["goal", "marriage", "child", "children", "house", "home", "education", "job", "job loss", "relocation", "family"],
    },
]

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "are", "was", "were", "have",
    "has", "had", "from", "they", "their", "you", "your", "about", "what",
    "which", "why", "how", "does", "did", "into", "when", "where", "who",
    "will", "would", "can", "could", "should", "there", "then", "than",
}


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------


def _ro_connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fmt(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}"


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _table_count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


@st.cache_data(show_spinner=False, ttl=30)
def load_evidence(db_path: str, stamp: int) -> pd.DataFrame:
    conn = _ro_connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT e.row_id, e.evidence_id, e.source_type, e.platform, e.author,
                   e.source_date, e.source_url, e.source_locator, e.original_text,
                   e.content_hash, e.duplicate_status, e.validation_status,
                   e.created_at, COALESCE(s.source_name, e.platform, e.source_type) AS source_name
            FROM evidence e
            LEFT JOIN sources s ON s.source_id = e.source_id
            ORDER BY e.row_id
            LIMIT ?
            """,
            conn,
            params=(EVIDENCE_LIMIT,),
        )
    finally:
        conn.close()
    df["original_text"] = df["original_text"].fillna("")
    df["text_lower"] = df["original_text"].str.lower()
    for column in ("source_date", "platform", "source_locator", "content_hash"):
        df[column] = df[column].fillna("unknown")
    df["source_url"] = df["source_url"].fillna("")
    df["source_name"] = df["source_name"].fillna(df["platform"]).fillna(df["source_type"])
    return df


@st.cache_data(show_spinner=False, ttl=30)
def load_meta(db_path: str, stamp: int) -> dict[str, Any]:
    conn = _ro_connect(db_path)
    try:
        total = _table_count(conn, "SELECT COUNT(*) FROM evidence")
        genuine = _table_count(
            conn,
            "SELECT COUNT(*) FROM evidence WHERE duplicate_status <> 'duplicate'",
        )
        duplicate = _table_count(
            conn, "SELECT COUNT(*) FROM evidence WHERE duplicate_status='duplicate'"
        )
        possible = _table_count(
            conn,
            "SELECT COUNT(*) FROM evidence WHERE duplicate_status='possible_duplicate'",
        )
        sources = _table_count(conn, "SELECT COUNT(DISTINCT source_id) FROM evidence")
        last_created = conn.execute("SELECT MAX(created_at) FROM evidence").fetchone()[0]
        by_source = [
            dict(row)
            for row in conn.execute(
                "SELECT source_type, COUNT(*) AS n FROM evidence "
                "GROUP BY source_type ORDER BY n DESC"
            )
        ]
        by_platform = [
            dict(row)
            for row in conn.execute(
                "SELECT COALESCE(platform, 'Unknown') AS platform, COUNT(*) AS n "
                "FROM evidence GROUP BY 1 ORDER BY n DESC LIMIT 8"
            )
        ]
        ingestion = (
            _table_count(conn, "SELECT COUNT(*) FROM ingestion_runs")
            if _has_table(conn, "ingestion_runs")
            else 0
        )
        validated_themes = (
            _table_count(conn, "SELECT COUNT(*) FROM evidence_themes")
            if _has_table(conn, "evidence_themes")
            else 0
        )
        return {
            "total": total,
            "genuine": genuine,
            "duplicate": duplicate,
            "possible_duplicate": possible,
            "sources": sources,
            "by_source": by_source,
            "by_platform": by_platform,
            "last_created": last_created,
            "ingestion_runs": ingestion,
            "validated_themes": validated_themes,
        }
    finally:
        conn.close()


def sandbox_evidence_count() -> int:
    if not SANDBOX_DB.exists():
        return 0
    try:
        conn = _ro_connect(SANDBOX_DB)
        try:
            return _table_count(conn, "SELECT COUNT(*) FROM evidence")
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


# ---------------------------------------------------------------------------
# Deterministic exploratory analysis
# ---------------------------------------------------------------------------


def _hit(text: str, keyword: str) -> bool:
    if " " in keyword:
        return keyword in text
    return re.search(rf"\b{re.escape(keyword)}\b", text) is not None


def classify_theme(text: str) -> list[str]:
    lowered = (text or "").lower()
    return [
        theme
        for theme, rule in THEME_RULES.items()
        if any(_hit(lowered, keyword) for keyword in rule["keywords"])
    ]


def build_theme_table(evidence: pd.DataFrame) -> pd.DataFrame:
    if evidence.empty:
        return pd.DataFrame(
            columns=[
                "theme", "label", "evidence_count", "source_count",
                "platforms", "signal_score", "problem", "opportunity", "evidence_ids",
            ]
        )
    rows: list[dict[str, Any]] = []
    for theme, rule in THEME_RULES.items():
        mask = evidence["text_lower"].apply(
            lambda text: any(_hit(text, keyword) for keyword in rule["keywords"])
        )
        hits = evidence[mask]
        if hits.empty:
            continue
        source_names = (
            hits["source_name"].fillna(hits["platform"]).fillna(hits["source_type"])
        )
        rows.append(
            {
                "theme": theme,
                "label": rule["label"],
                "evidence_count": len(hits),
                "source_count": int(source_names.nunique()),
                "platforms": ", ".join(sorted(source_names.dropna().unique())[:4]),
                "problem": rule["problem"],
                "opportunity": rule["opportunity"],
                "evidence_ids": hits["evidence_id"].tolist(),
            }
        )
    table = pd.DataFrame(rows)
    if table.empty:
        return table
    max_count = max(table["evidence_count"].max(), 1)
    max_sources = max(table["source_count"].max(), 1)
    table["signal_score"] = (
        0.7 * table["evidence_count"] / max_count
        + 0.3 * table["source_count"] / max_sources
    ) * 100
    table["signal_score"] = table["signal_score"].round(1)
    return table.sort_values(
        ["signal_score", "evidence_count"], ascending=False
    ).reset_index(drop=True)


def build_opportunities(
    themes: pd.DataFrame, evidence: pd.DataFrame, minimum_evidence: int = 3
) -> pd.DataFrame:
    if themes.empty:
        return pd.DataFrame(
            columns=[
                "rank", "theme", "opportunity", "problem", "evidence_count",
                "source_count", "signal_score", "evidence_ids", "reasoning",
            ]
        )
    supported = themes[themes["evidence_count"] >= minimum_evidence].copy()
    if supported.empty:
        return pd.DataFrame()
    supported = supported.sort_values(
        ["signal_score", "evidence_count"], ascending=False
    ).reset_index(drop=True)
    supported["rank"] = supported.index + 1
    supported["reasoning"] = supported.apply(
        lambda row: (
            f"{int(row['evidence_count'])} matching records across "
            f"{int(row['source_count'])} source(s); "
            f"exploratory signal score {row['signal_score']:.1f}/100."
        ),
        axis=1,
    )
    return supported[
        [
            "rank", "theme", "opportunity", "problem", "evidence_count",
            "source_count", "signal_score", "evidence_ids", "reasoning",
        ]
    ]


def rank_opportunities(
    opportunities: pd.DataFrame,
    *,
    volume_weight: float = 0.7,
    diversity_weight: float = 0.3,
) -> pd.DataFrame:
    """Re-rank opportunity hypotheses with transparent, editable weights.

    Only evidence volume and source diversity are available before M5/M6 results;
    the other original M7 dimensions are not fabricated.
    """
    if opportunities.empty:
        return opportunities
    ranked = opportunities.copy()
    max_count = max(float(ranked["evidence_count"].max()), 1.0)
    max_sources = max(float(ranked["source_count"].max()), 1.0)
    total_weight = max(float(volume_weight) + float(diversity_weight), 1e-9)
    ranked["priority_score"] = (
        (
            float(volume_weight) * ranked["evidence_count"] / max_count
            + float(diversity_weight) * ranked["source_count"] / max_sources
        )
        / total_weight
        * 100
    ).round(1)
    ranked = ranked.sort_values(
        ["priority_score", "evidence_count", "source_count"], ascending=False
    ).reset_index(drop=True)
    ranked["rank"] = ranked.index + 1
    ranked["reasoning"] = ranked.apply(
        lambda row: (
            f"{int(row['evidence_count'])} matching records across "
            f"{int(row['source_count'])} source(s); transparent priority score "
            f"{row['priority_score']:.1f}/100 using volume={volume_weight:.2f} "
            f"and diversity={diversity_weight:.2f}."
        ),
        axis=1,
    )
    return ranked


def _custom_terms(question: str) -> list[str]:
    tokens = re.findall(r"[a-z][a-z0-9_\-]{2,}", question.lower())
    return [token for token in tokens if token not in STOPWORDS]


def search_evidence(
    evidence: pd.DataFrame,
    terms: list[str],
    *,
    limit: int = 6,
    min_matches: int = 2,
) -> tuple[pd.DataFrame, list[str]]:
    terms = [term.lower() for term in terms if term.strip()]
    if evidence.empty or not terms:
        return evidence.iloc[0:0], []
    scored: list[tuple[int, int, str]] = []
    for idx, row in evidence.iterrows():
        text = row["text_lower"]
        matched = [term for term in terms if _hit(text, term)]
        if not matched:
            continue
        if len(matched) < min_matches and not any(" " in term for term in matched):
            continue
        phrase_bonus = sum(1 for term in matched if " " in term)
        scored.append((len(matched) + phrase_bonus, -idx, row["evidence_id"]))
    if not scored:
        return evidence.iloc[0:0], []
    scored.sort(reverse=True)
    ids = [item[2] for item in scored[:limit]]
    return evidence[evidence["evidence_id"].isin(ids)].copy(), sorted(
        {
            term
            for term in terms
            if any(_hit(text, term) for text in evidence.loc[evidence["evidence_id"].isin(ids), "text_lower"])
        }
    )


def answer_question(
    evidence: pd.DataFrame,
    question: str,
    terms: list[str],
    *,
    min_matches: int = 2,
) -> dict[str, Any]:
    hits, matched = search_evidence(evidence, terms, min_matches=min_matches)
    if hits.empty:
        return {
            "insufficient": True,
            "answer": (
                "Insufficient evidence in the current corpus for a reliable answer. "
                "No records matched the question terms; no conclusion is drawn."
            ),
            "hits": hits,
            "matched_terms": matched,
            "citations": [],
            "locators": [],
            "volume": 0,
            "diversity": 0,
            "confidence": "Low — no matching records.",
            "limitations": [
                "Extractive keyword retrieval only.",
                "No M5 four-field screening results are available.",
            ],
            "gaps": ["No matching records; no contradiction assessment is possible."],
            "contradictions": "Not assessed automatically.",
        }
    source_names = hits["source_name"].fillna("").astype(str)
    return {
        "insufficient": False,
        "answer": (
            f"{len(hits)} closest evidence record(s) matched "
            f"{', '.join(matched) or 'the question'}. "
            "The excerpts below are observed source text, not a causal or validated conclusion."
        ),
        "hits": hits,
        "matched_terms": matched,
        "citations": hits["evidence_id"].tolist(),
        "locators": hits["source_locator"].astype(str).tolist(),
        "volume": int(len(hits)),
        "diversity": int(source_names[source_names != ""].nunique()),
        "confidence": "Low — exploratory keyword retrieval over unvalidated evidence.",
        "limitations": [
            "Extractive keyword retrieval only; no generative synthesis.",
            "No M5 four-field screening results are available.",
            "Results are not a prevalence, causal or validated finding.",
        ],
        "gaps": [
            "Contradictions are not automatically assessed; review the source excerpts.",
            "M5/M6 four-field results are required for a complete structured answer.",
        ],
        "contradictions": "Not assessed automatically.",
    }


# ---------------------------------------------------------------------------
# Upload sandbox
# ---------------------------------------------------------------------------


def ensure_sandbox(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        apply_migrations(conn)
    finally:
        conn.close()


def _safe_metadata(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def ingest_file(
    db_path: str | Path,
    path: str | Path,
    mapping: Mapping[str, str],
    *,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Process one file through the existing ingestion pipeline into a sandbox DB."""
    ensure_sandbox(db_path)
    conn = connect(db_path)
    run_id: int | None = None
    try:
        plan = prepare_upload(conn, path)
        if plan.already_ingested:
            raise ReIngestBlocked(plan.previous_runs[0])
        summary = validate_upload(plan, mapping)
        if summary.get("required_mapping_missing"):
            raise ValueError(
                "required mapping missing: "
                + ", ".join(summary["required_mapping_missing"])
            )
        frame = read_table(plan.path, sheet_name=plan.profile.sheet_name)
        run_id = open_run(conn, plan)
        conn.commit()
        rows_read = len(frame)
        created = duplicate = invalid = 0
        for index, raw in enumerate(frame.to_dict(orient="records")):
            record = normalize_record(
                raw,
                mapping,
                default_source_name=plan.path.name,
            )
            result = validate_record(record)
            if not result.ok:
                invalid += 1
            else:
                item = EvidenceInput(
                    original_text=record["original_text"],
                    source_name=record["source_name"],
                    source_type=record["source_type"],
                    platform=record.get("platform"),
                    author=record.get("author"),
                    source_url=record.get("source_url"),
                    source_date=record.get("source_date"),
                    collection_date=record.get("collection_date"),
                    source_locator=record.get("source_locator"),
                    source_metadata={
                        str(key): _safe_metadata(value)
                        for key, value in (record.get("source_metadata") or {}).items()
                    },
                )
                inserted = insert_evidence(conn, item, run_id=run_id)
                if inserted.created:
                    created += 1
                if inserted.duplicate_status in ("duplicate", "possible_duplicate"):
                    duplicate += 1
            if progress is not None and rows_read:
                progress(min(1.0, (index + 1) / rows_read))
        conn.commit()
        close_run(
            conn,
            run_id,
            status="completed",
            rows_read=rows_read,
            records_created=created,
            records_flagged_duplicate=duplicate,
            records_invalid=invalid,
            message=f"processed via Streamlit sandbox; mapping={dict(mapping)}",
        )
        conn.commit()
        return {
            "run_id": run_id,
            "rows_read": rows_read,
            "created": created,
            "duplicate": duplicate,
            "invalid": invalid,
            "file_hash": plan.file_hash,
            "summary": summary,
        }
    except Exception:
        conn.rollback()
        if run_id is not None:
            try:
                close_run(
                    conn,
                    run_id,
                    status="failed",
                    rows_read=0,
                    records_created=0,
                    records_flagged_duplicate=0,
                    records_invalid=0,
                    message="Streamlit sandbox processing failed",
                )
                conn.commit()
            except Exception:
                conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def card(kicker: str, title: str, body: str, *, accent: str = "#2dd4bf") -> None:
    st.markdown(
        f"""
        <div class="ade-card" style="border-left-color:{accent};">
          <div class="ade-kicker">{_esc(kicker)}</div>
          <div class="ade-card-title">{_esc(title)}</div>
          <div class="ade-card-body">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def metric_row(items: list[tuple[str, str, str]]) -> None:
    cols = st.columns(len(items))
    for column, (label, value, note) in zip(cols, items):
        with column:
            st.markdown(
                f"""
                <div class="ade-metric">
                  <div class="ade-metric-label">{_esc(label)}</div>
                  <div class="ade-metric-value">{_esc(value)}</div>
                  <div class="ade-metric-note">{_esc(note)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def theme_download(df: pd.DataFrame) -> bytes:
    cols = [
        column
        for column in (
            "theme", "label", "evidence_count", "source_count",
            "signal_score", "problem", "opportunity",
        )
        if column in df.columns
    ]
    return df[cols].to_csv(index=False).encode("utf-8")


def evidence_download(df: pd.DataFrame) -> bytes:
    cols = [
        column
        for column in (
            "evidence_id", "source_type", "platform", "source_name", "author",
            "source_date", "source_url", "source_locator", "duplicate_status",
            "validation_status", "original_text", "content_hash",
        )
        if column in df.columns
    ]
    return df[cols].to_csv(index=False).encode("utf-8")


def mentor_summary(
    meta: dict[str, Any],
    themes: pd.DataFrame,
    opportunities: pd.DataFrame,
    source_label: str,
) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# AI Discovery Engine — Mentor Summary",
        "",
        f"Generated: {generated}",
        f"Evidence source: {source_label}",
        "",
        "## Dataset snapshot",
        f"- Records stored: {meta['total']}",
        f"- Non-duplicate records: {meta['genuine']}",
        f"- Sources: {meta['sources']}",
        f"- Duplicate records: {meta['duplicate']}",
        f"- Possible duplicates: {meta['possible_duplicate']}",
        "",
        "## Top exploratory themes",
    ]
    if themes.empty:
        lines.append("- No keyword-signal themes were detected.")
    else:
        for _, row in themes.head(8).iterrows():
            lines.append(
                f"- **{row['label']}** — {int(row['evidence_count'])} records, "
                f"{int(row['source_count'])} source(s), signal score {row['signal_score']:.1f}/100."
            )
    lines += ["", "## Candidate opportunities (interpretation)"]
    if opportunities.empty:
        lines.append("- No opportunity met the minimum supporting-evidence threshold.")
    else:
        for _, row in opportunities.head(6).iterrows():
            lines.append(
                f"- **#{int(row['rank'])} {row['opportunity']}** — {row['reasoning']}"
            )
    lines += [
        "",
        "## Limitations",
        "- The corpus is a non-probability convenience sample; no prevalence or market-size claim is supported.",
        "- Theme and opportunity signals are deterministic keyword-based exploration, not validated findings.",
        "- Research Q&A is extractive retrieval over stored text; it is not a generative or causal analysis.",
        "- All metrics are computed from stored records; no synthetic demo metrics are shown.",
        "",
        "## Interpretation boundary",
        "Observed evidence is reported separately from interpretation. Candidate opportunities are hypotheses for validation, not proven user needs.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def section_overview(meta: dict[str, Any], evidence: pd.DataFrame, source_label: str) -> None:
    st.markdown('<div class="ade-kicker">COMMAND CENTRE</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="ade-hero-title">From raw investor voices to testable opportunities.</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="ade-hero-sub">A traceable discovery workspace over a real SIP-investor evidence corpus. '
        "Every number below is computed from stored records; nothing is simulated.</div>",
        unsafe_allow_html=True,
    )
    metric_row(
        [
            ("Records stored", _fmt(meta["total"]), "All evidence rows"),
            ("Non-duplicate", _fmt(meta["genuine"]), "Distinct analysis base"),
            ("Sources", _fmt(meta["sources"]), "Distinct source records"),
            ("Duplicate flags", _fmt(meta["duplicate"]), "Retained, not deleted"),
        ]
    )
    st.write("")
    st.markdown('<div class="ade-section-title">Evidence composition</div>', unsafe_allow_html=True)
    if meta["by_source"]:
        chart = pd.DataFrame(meta["by_source"]).set_index("source_type")
        st.bar_chart(chart, height=260, color="#2dd4bf")
    else:
        st.info("No source distribution is available.")
    st.write("")
    st.markdown('<div class="ade-section-title">What this application does</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        card("EXPLORE", "Evidence explorer", "Search every record and open source, date, platform and verbatim text.")
    with c2:
        card("INTERPRET", "Themes & opportunities", "Transparent keyword signals rank observed themes; opportunities remain hypotheses.")
    with c3:
        card("ASK", "Evidence Q&A", "Every answer is backed by the exact records and citations used.")


def section_upload() -> None:
    st.markdown('<div class="ade-kicker">PIPELINE</div>', unsafe_allow_html=True)
    st.markdown('<div class="ade-page-title">Upload & evidence processing</div>', unsafe_allow_html=True)
    st.caption(
        "Files are processed into an isolated sandbox database. Uploads do not change the stored evidence."
    )
    uploaded = st.file_uploader(
        "Upload a research file", type=["csv", "xlsx", "xls"], accept_multiple_files=False
    )
    if uploaded is None:
        st.info("Supported formats: CSV, XLSX, XLS. Upload a file to validate its structure before processing.")
        _upload_history()
        return

    raw = uploaded.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", uploaded.name) or "upload.csv"
    path = UPLOAD_DIR / f"{digest[:12]}_{safe_name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)

    ensure_sandbox(SANDBOX_DB)
    conn = connect(SANDBOX_DB)
    try:
        plan = prepare_upload(conn, path)
        history = ingestion_history(conn, limit=5)
    except Exception as exc:
        conn.close()
        st.error(f"Could not read this file: {exc}")
        return
    conn.close()

    if plan.already_ingested:
        st.warning(
            f"This exact file was already processed as ingestion run "
            f"{plan.previous_runs[0].run_id}. Re-processing is blocked to avoid duplicates."
        )
    st.markdown(
        f'<div class="ade-meta">File: <b>{_esc(path.name)}</b> · {_fmt(plan.file_size_bytes)} bytes · '
        f'{_fmt(plan.profile.row_count)} rows · {_fmt(len(plan.profile.columns))} columns · '
        f'SHA-256 <code>{digest[:16]}…</code></div>',
        unsafe_allow_html=True,
    )
    with st.expander("Column mapping and file profile", expanded=True):
        mapping: dict[str, str] = {}
        columns = list(plan.profile.columns)
        suggested = suggest_column_mapping(columns)
        canonical = [
            "original_text", "source_name", "source_type", "platform",
            "source_url", "source_date", "collection_date", "source_locator",
        ]
        for field in canonical:
            options = ["— not mapped —"] + columns
            default = suggested.get(field)
            index = options.index(default) if default in options else 0
            chosen = st.selectbox(
                field.replace("_", " ").title(),
                options,
                index=index,
                key=f"map_{digest[:12]}_{field}",
            )
            if chosen != "— not mapped —":
                mapping[field] = chosen
        st.caption(f"Suggested mapping: {suggested}")

    if st.button("Validate file", type="secondary", width="stretch"):
        try:
            summary = validate_upload(plan, mapping)
            preview = preview_records(plan, mapping, limit=12)
            st.session_state["upload_validation"] = {
                "summary": summary,
                "preview": preview,
                "mapping": mapping,
                "digest": digest,
            }
        except Exception as exc:
            st.error(f"Validation failed: {exc}")

    state = st.session_state.get("upload_validation")
    if state and state.get("digest") == digest:
        summary = state["summary"]
        st.markdown('<div class="ade-section-title">Validation result</div>', unsafe_allow_html=True)
        cols = st.columns(4)
        cols[0].metric("Rows", _fmt(summary.get("rows")))
        cols[1].metric("Valid", _fmt(summary.get("valid")))
        cols[2].metric("Invalid", _fmt(summary.get("invalid")))
        cols[3].metric("Warnings", _fmt(summary.get("with_warnings")))
        if summary.get("required_mapping_missing"):
            st.error("Missing required mapping: " + ", ".join(summary["required_mapping_missing"]))
        if summary.get("issues"):
            st.warning("Validation issues: " + json.dumps(summary["issues"], sort_keys=True))
        preview = pd.DataFrame(state["preview"])
        if not preview.empty:
            show = [
                column
                for column in ("original_text", "source_name", "source_type", "platform", "_validation_ok")
                if column in preview.columns
            ]
            st.dataframe(preview[show], width="stretch", hide_index=True)

        can_ingest = (
            not plan.already_ingested
            and not summary.get("required_mapping_missing")
            and summary.get("valid", 0) > 0
        )
        if st.button(
            "Process valid records into sandbox",
            type="primary",
            disabled=not can_ingest,
            width="stretch",
        ):
            progress = st.progress(0, text="Processing evidence…")
            try:
                result = ingest_file(
                    SANDBOX_DB,
                    path,
                    state["mapping"],
                    progress=lambda value: progress.progress(
                        int(value * 100), text=f"Processing evidence… {int(value * 100)}%"
                    ),
                )
                st.cache_data.clear()
                st.session_state["_upload_flash"] = (
                    f"Processed run {result['run_id']}: {result['created']} created, "
                    f"{result['duplicate']} duplicate/possible, {result['invalid']} invalid."
                )
                st.session_state["_next_source"] = "Upload sandbox"
                st.rerun()
            except ReIngestBlocked as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Processing failed: {exc}")
    _upload_history()


def _upload_history() -> None:
    if not SANDBOX_DB.exists():
        return
    try:
        conn = _ro_connect(SANDBOX_DB)
        try:
            history = ingestion_history(conn, limit=8)
        finally:
            conn.close()
    except sqlite3.Error:
        return
    if history:
        st.markdown('<div class="ade-section-title">Recent sandbox runs</div>', unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(history), width="stretch", hide_index=True)


def section_explorer(evidence: pd.DataFrame, source_label: str) -> None:
    st.markdown('<div class="ade-kicker">TRACEABILITY</div>', unsafe_allow_html=True)
    st.markdown('<div class="ade-page-title">Evidence explorer</div>', unsafe_allow_html=True)
    st.caption(f"Source: {source_label}. Search and filter stored records; open any record for its exact citation.")
    if evidence.empty:
        st.warning("No evidence is available in this source.")
        return
    with st.expander("Filters", expanded=True):
        sources = sorted(evidence["source_type"].dropna().unique())
        platforms = sorted(evidence["platform"].fillna("Unknown").astype(str).unique())
        duplicates = sorted(evidence["duplicate_status"].dropna().unique())
        validations = sorted(evidence["validation_status"].dropna().unique())
        defaults = {
            "explorer_search": "",
            "explorer_source": sources,
            "explorer_platform": platforms,
            "explorer_duplicate": duplicates,
            "explorer_validation": validations,
            "explorer_detail_search": "",
        }
        for key, value in defaults.items():
            st.session_state.setdefault(key, value)

        def _clear_explorer_filters() -> None:
            for key, value in defaults.items():
                st.session_state[key] = value

        search = st.text_input(
            "Search text, evidence ID, author, platform or source",
            key="explorer_search",
            placeholder="Try “SIP”, “EVD-”, an author or a platform",
        )
        c1, c2, c3 = st.columns(3)
        selected_sources = c1.multiselect("Source type", sources, key="explorer_source")
        selected_platforms = c2.multiselect("Platform", platforms, key="explorer_platform")
        selected_duplicates = c3.multiselect(
            "Duplicate status", duplicates, key="explorer_duplicate"
        )
        selected_validation = st.multiselect(
            "Validation status", validations, key="explorer_validation"
        )
        st.button(
            "Clear filters",
            on_click=_clear_explorer_filters,
            type="secondary",
            width="content",
        )

    platform_view = evidence["platform"].fillna("Unknown").astype(str)
    view = evidence[
        evidence["source_type"].isin(selected_sources)
        & platform_view.isin(selected_platforms)
        & evidence["duplicate_status"].isin(selected_duplicates)
        & evidence["validation_status"].isin(selected_validation)
    ]
    if search.strip():
        needle = search.strip().lower()
        mask = (
            view["text_lower"].str.contains(needle, regex=False, na=False)
            | view["evidence_id"].str.lower().str.contains(needle, regex=False, na=False)
            | view["author"].fillna("").str.lower().str.contains(needle, regex=False, na=False)
            | view["platform"].fillna("").str.lower().str.contains(needle, regex=False, na=False)
            | view["source_name"].fillna("").str.lower().str.contains(needle, regex=False, na=False)
        )
        view = view[mask]

    filters_active = (
        bool(search.strip())
        or set(selected_sources) != set(sources)
        or set(selected_platforms) != set(platforms)
        or set(selected_duplicates) != set(duplicates)
        or set(selected_validation) != set(validations)
    )
    if filters_active:
        st.markdown(
            f'<div class="ade-meta">{_fmt(len(view))} of {_fmt(len(evidence))} records shown</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="ade-meta">All {_fmt(len(evidence))} records shown</div>',
            unsafe_allow_html=True,
        )
    if view.empty:
        st.info("No records match the current search and filters. Clear the search or widen the filters.")
    table = view[
        ["evidence_id", "source_name", "platform", "source_date", "duplicate_status", "validation_status"]
    ].copy()
    st.dataframe(table, width="stretch", hide_index=True, height=320)
    st.download_button(
        "Download filtered evidence CSV",
        evidence_download(view),
        file_name="evidence_export.csv",
        mime="text/csv",
        width="stretch",
    )

    st.markdown('<div class="ade-section-title">Open evidence detail</div>', unsafe_allow_html=True)
    detail_search = st.text_input(
        "Find a record to inspect",
        key="explorer_detail_search",
        placeholder="Type an evidence ID, author, platform, or a phrase from the text",
        help="Search first; the detail picker only lists matching records.",
    )
    detail_matches = view
    if detail_search.strip():
        detail_needle = detail_search.strip().lower()
        detail_matches = view[
            view["text_lower"].str.contains(detail_needle, regex=False, na=False)
            | view["evidence_id"].str.lower().str.contains(detail_needle, regex=False, na=False)
            | view["author"].fillna("").str.lower().str.contains(detail_needle, regex=False, na=False)
            | view["platform"].fillna("").str.lower().str.contains(detail_needle, regex=False, na=False)
            | view["source_name"].fillna("").str.lower().str.contains(detail_needle, regex=False, na=False)
        ]
    if not detail_search.strip():
        st.caption(
            f"Search above to open a record. {_fmt(len(view))} record(s) are in the current filter."
        )
    elif detail_matches.empty:
        st.info("No record matches that detail search. Try an evidence ID or a shorter phrase.")
    else:
        capped = detail_matches.head(50)
        if len(detail_matches) > len(capped):
            st.caption(f"Showing the first {len(capped)} of {_fmt(len(detail_matches))} matches. Refine the search to narrow further.")
        selected = st.selectbox(
            "Matching evidence",
            capped["evidence_id"].tolist(),
            format_func=lambda value: f"{value} · {capped.loc[capped['evidence_id'] == value, 'source_name'].iloc[0]}",
        )
        row = capped[capped["evidence_id"] == selected].iloc[0]
        with st.expander(f"Evidence {selected}", expanded=True):
            st.markdown(
                f"""
                <div class="ade-evidence">
                  <div class="ade-evidence-text">{_esc(row['original_text'])}</div>
                  <div class="ade-citation">
                    <b>{_esc(row['source_name'])}</b> · {_esc(row['platform'])} ·
                    {_esc(row['source_date'])} · {_esc(row['source_type'])}<br>
                    Evidence ID <code>{_esc(row['evidence_id'])}</code> ·
                    Locator {_esc(row['source_locator'])}<br>
                    Content SHA-256 <code>{_esc(row['content_hash'])}</code>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if row.get("source_url"):
                st.link_button("Open source URL", row["source_url"])
            matched = classify_theme(row["original_text"])
            if matched:
                st.markdown(
                    "**Exploratory theme signals:** "
                    + ", ".join(THEME_RULES[theme]["label"] for theme in matched)
                )
            else:
                st.caption("No exploratory keyword signal matched this record.")


def section_themes(evidence: pd.DataFrame, themes: pd.DataFrame, source_label: str) -> None:
    st.markdown('<div class="ade-kicker">EXPLORATION</div>', unsafe_allow_html=True)
    st.markdown('<div class="ade-page-title">Theme discovery</div>', unsafe_allow_html=True)
    st.caption(
        f"Source: {source_label}. Deterministic keyword signals over verbatim evidence. "
        "This is exploratory, not a validated theme analysis."
    )
    if themes.empty:
        st.warning("No exploratory theme signals were detected in this source.")
        return
    top = themes.head(10).copy()
    c1, c2 = st.columns([1.5, 1])
    with c1:
        chart = top.set_index("label")[["evidence_count"]]
        st.bar_chart(chart, height=330, color="#38bdf8")
    with c2:
        st.metric("Themes detected", _fmt(len(themes)), "with ≥1 matching record")
        st.metric(
            "Strongest signal",
            themes.iloc[0]["label"],
            f"Score {themes.iloc[0]['signal_score']:.1f}/100",
        )
        st.metric(
            "Top theme records",
            _fmt(themes.iloc[0]["evidence_count"]),
            "observed matches",
        )
    table = themes[
        ["label", "evidence_count", "source_count", "signal_score", "problem", "opportunity"]
    ].rename(
        columns={
            "label": "Theme",
            "evidence_count": "Records",
            "source_count": "Sources",
            "signal_score": "Signal score",
            "problem": "Observed problem",
            "opportunity": "Opportunity hypothesis",
        }
    )
    st.dataframe(table, width="stretch", hide_index=True, height=300)
    st.download_button(
        "Download theme table CSV",
        theme_download(themes),
        file_name="exploratory_themes.csv",
        mime="text/csv",
        width="stretch",
    )
    selected = st.selectbox(
        "Inspect supporting evidence",
        themes["theme"].tolist(),
        format_func=lambda theme: THEME_RULES[theme]["label"],
    )
    row = themes[themes["theme"] == selected].iloc[0]
    st.markdown(
        f"**{_esc(row['label'])}** · {int(row['evidence_count'])} records · "
        f"{int(row['source_count'])} sources · signal {row['signal_score']:.1f}/100"
    )
    st.caption(THEME_RULES[selected]["problem"])
    supporting = evidence[evidence["evidence_id"].isin(row["evidence_ids"])]
    hits = supporting.head(8)
    st.caption(
        f"{_fmt(len(supporting))} supporting record(s) for this theme; "
        f"showing the first {_fmt(len(hits))}."
    )
    for _, record in hits.iterrows():
        with st.expander(f"{record['evidence_id']} · {record['source_name']}"):
            st.write(record["original_text"])
            st.caption(
                f"{record['platform']} · {record['source_date']} · {record['source_url'] or 'no URL'}"
            )


def section_opportunities(
    evidence: pd.DataFrame, themes: pd.DataFrame, opportunities: pd.DataFrame, source_label: str
) -> None:
    st.markdown('<div class="ade-kicker">INTERPRETATION</div>', unsafe_allow_html=True)
    st.markdown('<div class="ade-page-title">Opportunity discovery</div>', unsafe_allow_html=True)
    st.caption(
        f"Source: {source_label}. Opportunities are hypotheses derived from observed theme signals, "
        "not validated product requirements. Minimum support: 3 matching records."
    )
    if opportunities.empty:
        st.warning(
            "No opportunity currently meets the minimum supporting-evidence threshold. "
            "No opportunity is shown rather than inventing one."
        )
        return
    st.markdown('<div class="ade-section-title">Ranking weights</div>', unsafe_allow_html=True)
    w1, w2 = st.columns(2)
    volume_weight = w1.slider(
        "Evidence volume weight", 0.0, 1.0, 0.7, 0.05,
        help="Weight for the number of distinct supporting records.",
    )
    diversity_weight = w2.slider(
        "Source diversity weight", 0.0, 1.0, 0.3, 0.05,
        help="Weight for the number of distinct sources supporting the opportunity.",
    )
    st.caption(
        "Only evidence volume and source diversity are available before M5/M6 four-field results. "
        "The other original M7 scoring dimensions are not fabricated."
    )
    ranked = rank_opportunities(
        opportunities,
        volume_weight=volume_weight,
        diversity_weight=diversity_weight,
    )
    strongest = ranked.iloc[0]
    card(
        "STRONGEST OBSERVED OPPORTUNITY",
        str(strongest["opportunity"]),
        f"<b>{int(strongest['evidence_count'])}</b> matching records across "
        f"<b>{int(strongest['source_count'])}</b> source(s). "
        f"{_esc(strongest['problem'])}<br><br>"
        f"<span class='ade-meta'>Reasoning: {_esc(strongest['reasoning'])}</span>",
        accent="#fbbf24",
    )
    table = ranked[
        ["rank", "opportunity", "evidence_count", "source_count", "priority_score", "reasoning"]
    ].rename(
        columns={
            "rank": "#",
            "opportunity": "Opportunity hypothesis",
            "evidence_count": "Records",
            "source_count": "Sources",
            "priority_score": "Priority score",
            "reasoning": "Why it ranks here",
        }
    )
    st.dataframe(table, width="stretch", hide_index=True)
    st.download_button(
        "Download opportunity table CSV",
        ranked.to_csv(index=False).encode("utf-8"),
        file_name="opportunity_hypotheses.csv",
        mime="text/csv",
        width="stretch",
    )
    selected = st.selectbox(
        "Inspect evidence for an opportunity",
        ranked["theme"].tolist(),
        format_func=lambda theme: THEME_RULES[theme]["label"],
    )
    row = ranked[ranked["theme"] == selected].iloc[0]
    st.markdown(f"**Why this ranks:** {_esc(row['reasoning'])}")
    st.caption(THEME_RULES[selected]["problem"])
    supporting = evidence[evidence["evidence_id"].isin(row["evidence_ids"])]
    hits = supporting.head(8)
    st.caption(
        f"{_fmt(len(supporting))} supporting record(s) for this opportunity; "
        f"showing the first {_fmt(len(hits))}."
    )
    for _, record in hits.iterrows():
        st.markdown(
            f"<div class='ade-quote'>“{_esc(record['original_text'][:420])}”"
            f"<div class='ade-citation'>{_esc(record['evidence_id'])} · "
            f"{_esc(record['source_name'])} · {_esc(record['platform'])}</div></div>",
            unsafe_allow_html=True,
        )


def section_qa(evidence: pd.DataFrame, source_label: str) -> None:
    st.markdown('<div class="ade-kicker">RETRIEVAL</div>', unsafe_allow_html=True)
    st.markdown('<div class="ade-page-title">Research Q&A</div>', unsafe_allow_html=True)
    st.caption(
        f"Source: {source_label}. Answers are extractive retrieval over stored evidence, "
        "not generative claims. Supporting records are always shown."
    )
    mode = st.radio(
        "Question source",
        ["Predefined research questions", "Custom question"],
        horizontal=True,
    )
    if mode == "Predefined research questions":
        choice = st.selectbox(
            "Choose a question",
            [item["title"] for item in PREDEFINED_QUESTIONS],
        )
        item = next(item for item in PREDEFINED_QUESTIONS if item["title"] == choice)
        question = item["title"]
        terms = item["terms"]
    else:
        question = st.text_area(
            "Ask a question about the evidence",
            placeholder="What do users say about payment failures and SIP continuation?",
        )
        terms = _custom_terms(question)
    if st.button("Search evidence", type="primary", width="stretch"):
        if not question.strip():
            st.warning("Enter a question.")
        else:
            answer = answer_question(
                evidence,
                question,
                terms,
                min_matches=2 if mode == "Predefined research questions" else 1,
            )
            st.session_state["qa_result"] = {
                "question": question,
                "answer": answer,
                "source_label": source_label,
            }
    state = st.session_state.get("qa_result")
    if not state or state.get("source_label") != source_label:
        st.info("Ask a question to retrieve the closest supporting evidence.")
        return
    answer = state["answer"]
    st.markdown(f"**Question:** {_esc(state['question'])}")
    if answer["insufficient"]:
        st.warning(answer["answer"])
        return
    st.markdown(
        f"<div class='ade-answer'>{_esc(answer['answer'])}</div>",
        unsafe_allow_html=True,
    )
    st.caption("Matched terms: " + ", ".join(answer["matched_terms"]))
    m1, m2, m3 = st.columns(3)
    m1.metric("Supporting records", _fmt(answer.get("volume", len(answer["hits"]))))
    m2.metric("Distinct sources", _fmt(answer.get("diversity", 0)))
    m3.metric("Confidence", "Low")
    with st.expander("Structured answer details", expanded=False):
        st.markdown(
            f"**Confidence:** {_esc(answer.get('confidence', 'Low'))}  \n"
            f"**Citations:** {', '.join(answer.get('citations', [])) or 'None'}  \n"
            f"**Locators:** {', '.join(answer.get('locators', [])) or 'None'}  \n"
            f"**Contradictions:** {_esc(answer.get('contradictions', 'Not assessed automatically.'))}"
        )
        st.markdown("**Limitations**")
        for item in answer.get("limitations", []):
            st.markdown(f"- {item}")
        st.markdown("**Gaps**")
        for item in answer.get("gaps", []):
            st.markdown(f"- {item}")
    for _, record in answer["hits"].iterrows():
        with st.expander(f"{record['evidence_id']} · {record['source_name']} · {record['source_date']}"):
            st.write(record["original_text"])
            st.caption(
                f"{record['platform']} · {record['source_url'] or 'no URL'} · "
                f"locator {record['source_locator']} · SHA-256 {record['content_hash']}"
            )
            if record.get("source_url"):
                st.link_button("Open source", record["source_url"], key=f"qa_{record['evidence_id']}")


def section_limitations(meta: dict[str, Any], source_label: str) -> None:
    st.markdown('<div class="ade-kicker">HONESTY LAYER</div>', unsafe_allow_html=True)
    st.markdown('<div class="ade-page-title">Research limitations</div>', unsafe_allow_html=True)
    st.caption(f"Source: {source_label}. Observed evidence is kept separate from interpretation.")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="ade-section-title">Observed data facts</div>', unsafe_allow_html=True)
        facts = [
            f"{_fmt(meta['total'])} stored records; {_fmt(meta['genuine'])} non-duplicate.",
            f"{_fmt(meta['sources'])} distinct sources; {_fmt(meta['duplicate'])} duplicate and {_fmt(meta['possible_duplicate'])} possible-duplicate records retained.",
            f"Last evidence write: {meta['last_created'] or 'unknown'}.",
            f"Validated theme assignments stored: {_fmt(meta['validated_themes'])}.",
        ]
        for item in facts:
            st.markdown(f"- {item}")
    with c2:
        st.markdown('<div class="ade-section-title">Interpretation limits</div>', unsafe_allow_html=True)
        limits = [
            "The corpus is a non-probability convenience sample; it cannot support prevalence, market-size or causal claims.",
            "Theme discovery uses transparent keyword rules, not a validated qualitative coding process.",
            "Opportunity rankings are hypotheses based on observed evidence volume and source diversity; they are not validated user needs.",
            "Research Q&A is extractive retrieval. It shows source text and does not generate synthetic findings.",
            "No independent validation, inter-rater reliability or kappa is claimed.",
        ]
        for item in limits:
            st.markdown(f"- {item}")
    st.info(
        "Observed evidence is the stored source text and its citations. "
        "Interpretation is the labelled theme/opportunity layer. Neither is a population estimate."
    )


def section_export(
    meta: dict[str, Any],
    evidence: pd.DataFrame,
    themes: pd.DataFrame,
    opportunities: pd.DataFrame,
    source_label: str,
) -> None:
    st.markdown('<div class="ade-kicker">DELIVERABLES</div>', unsafe_allow_html=True)
    st.markdown('<div class="ade-page-title">Export findings</div>', unsafe_allow_html=True)
    st.caption("Downloads contain only stored evidence and computed exploratory tables. No fabricated content.")
    summary = mentor_summary(meta, themes, opportunities, source_label)
    summary_bytes = summary.encode("utf-8")
    evidence_bytes = evidence_download(evidence)
    theme_bytes = theme_download(themes)
    opportunity_bytes = opportunities.to_csv(index=False).encode("utf-8")

    def export_note(filename: str, payload: bytes) -> None:
        st.caption(
            f"{filename} · {len(payload) / 1024:.1f} KB · payload generated; "
            "browser download not verified."
        )

    st.download_button(
        "Download mentor summary (Markdown)",
        summary_bytes,
        file_name="ai_discovery_mentor_summary.md",
        mime="text/markdown",
        width="stretch",
    )
    export_note("ai_discovery_mentor_summary.md", summary_bytes)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button(
            "Evidence with citations (CSV)",
            evidence_bytes,
            file_name="evidence_with_citations.csv",
            mime="text/csv",
            width="stretch",
        )
        export_note("evidence_with_citations.csv", evidence_bytes)
    with c2:
        st.download_button(
            "Ranked themes (CSV)",
            theme_bytes,
            file_name="ranked_themes.csv",
            mime="text/csv",
            width="stretch",
        )
        export_note("ranked_themes.csv", theme_bytes)
    with c3:
        st.download_button(
            "Ranked opportunities (CSV)",
            opportunity_bytes,
            file_name="ranked_opportunities.csv",
            mime="text/csv",
            width="stretch",
        )
        export_note("ranked_opportunities.csv", opportunity_bytes)
    with st.expander("Preview mentor summary", expanded=False):
        st.markdown(summary)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="🔎",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
        html, body, [class*="css"], .stApp { font-family: 'Inter', sans-serif; }
        .stApp { background: #f5f7fb; color: #0f172a; }
        [data-testid="stSidebar"] { background: #0b1220; border-right: 1px solid #1e293b; }
        [data-testid="stSidebar"] * { color: #dbe7f5 !important; }
        [data-testid="stSidebar"] .stRadio label { padding: 2px 0; }
        [data-testid="stSidebar"] hr { border-color: #243247; }
        .ade-brand { font-weight: 800; letter-spacing: -0.03em; font-size: 1.35rem; color: #f8fafc !important; }
        .ade-brand-sub { color: #8fa3bd !important; font-size: 0.78rem; line-height: 1.5; margin-top: 4px; }
        .ade-kicker { color: #0f766e; font-weight: 700; font-size: 0.72rem; letter-spacing: 0.16em; text-transform: uppercase; }
        .ade-hero-title { font-size: clamp(2rem, 4vw, 3.6rem); line-height: 1.05; letter-spacing: -0.05em; font-weight: 800; max-width: 900px; margin: 0.15rem 0 0.7rem; color: #0b1220; }
        .ade-hero-sub { color: #475569; font-size: 1.02rem; max-width: 820px; line-height: 1.6; margin-bottom: 1.2rem; }
        .ade-page-title { font-size: 2.15rem; line-height: 1.15; letter-spacing: -0.04em; font-weight: 800; color: #0b1220; margin-bottom: 0.2rem; }
        .ade-section-title { font-size: 1.02rem; font-weight: 700; color: #0f172a; margin: 0.8rem 0 0.45rem; }
        .ade-metric { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 1rem 1.1rem; box-shadow: 0 8px 22px rgba(15,23,42,0.04); }
        .ade-metric-label { color: #64748b; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; }
        .ade-metric-value { color: #0f172a; font-size: 1.8rem; font-weight: 800; letter-spacing: -0.04em; margin-top: 0.2rem; }
        .ade-metric-note { color: #94a3b8; font-size: 0.72rem; margin-top: 0.2rem; }
        .ade-card { background: #ffffff; border: 1px solid #e2e8f0; border-left: 4px solid #2dd4bf; border-radius: 14px; padding: 1rem 1.1rem; box-shadow: 0 8px 22px rgba(15,23,42,0.04); height: 100%; }
        .ade-card-title { font-size: 1.02rem; font-weight: 700; margin: 0.2rem 0 0.35rem; color: #0f172a; }
        .ade-card-body { color: #475569; font-size: 0.88rem; line-height: 1.55; }
        .ade-meta { color: #64748b; font-size: 0.8rem; line-height: 1.6; }
        .ade-evidence { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 1rem; }
        .ade-evidence-text { font-size: 0.95rem; line-height: 1.65; color: #1e293b; }
        .ade-citation { color: #64748b; font-size: 0.76rem; line-height: 1.55; margin-top: 0.7rem; }
        .ade-quote { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 0.8rem 0.9rem; margin: 0.5rem 0; font-size: 0.88rem; line-height: 1.55; color: #334155; }
        .ade-answer { background: #ecfeff; border: 1px solid #a5f3fc; border-radius: 12px; padding: 0.9rem 1rem; color: #164e63; font-size: 0.92rem; line-height: 1.55; }
        .stButton > button { border-radius: 10px; font-weight: 600; }
        [data-testid="stMetricValue"] { font-weight: 800; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown('<div class="ade-brand">AI Discovery Engine</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="ade-brand-sub">Evidence → themes → opportunities.<br>'
            "Traceable outputs.</div>",
            unsafe_allow_html=True,
        )
        st.divider()
        options = ["Research corpus"]
        if sandbox_evidence_count() > 0:
            options.append("Upload sandbox")
        pending = st.session_state.pop("_next_source", None)
        if pending in options:
            st.session_state["source_choice"] = pending
        if st.session_state.get("source_choice") not in options:
            st.session_state["source_choice"] = options[0]
        source_choice = st.selectbox("Evidence source", options, key="source_choice")
        st.divider()
        page = st.radio(
            "Navigate",
            [
                "Overview",
                "Upload & Processing",
                "Evidence Explorer",
                "Theme Discovery",
                "Opportunity Discovery",
                "Research Q&A",
                "Research Limitations",
                "Export Findings",
            ],
            label_visibility="collapsed",
        )

    active_db = get_settings().db_path if source_choice == "Research corpus" else SANDBOX_DB
    if not active_db.exists():
        st.error(f"Database not found: {active_db}")
        return
    flash = st.session_state.pop("_upload_flash", None)
    if flash:
        st.success(flash)
    try:
        evidence = load_evidence(str(active_db), active_db.stat().st_mtime_ns)
        meta = load_meta(str(active_db), active_db.stat().st_mtime_ns)
    except Exception as exc:
        st.error(f"Could not read the selected database: {exc}")
        return
    themes = build_theme_table(evidence)
    opportunities = build_opportunities(themes, evidence, minimum_evidence=3)

    if page == "Overview":
        section_overview(meta, evidence, source_choice)
    elif page == "Upload & Processing":
        section_upload()
    elif page == "Evidence Explorer":
        section_explorer(evidence, source_choice)
    elif page == "Theme Discovery":
        section_themes(evidence, themes, source_choice)
    elif page == "Opportunity Discovery":
        section_opportunities(evidence, themes, opportunities, source_choice)
    elif page == "Research Q&A":
        section_qa(evidence, source_choice)
    elif page == "Research Limitations":
        section_limitations(meta, source_choice)
    elif page == "Export Findings":
        section_export(meta, evidence, themes, opportunities, source_choice)


if __name__ == "__main__":
    main()
