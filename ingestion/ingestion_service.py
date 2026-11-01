"""Ingestion orchestration: the M1 foundation only.

M1 deliberately stops at *preparation*: a file can be profiled, validated and
mapped, and an ingestion run can be opened and closed. No evidence rows are
written here — that is M2, and it must not run until the consolidated dataset
is in place and approved.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from database import repository
from database.models import IngestionRunInfo
from ingestion import deduplication
from ingestion.file_reader import TableProfile, inspect_table, read_table
from ingestion.normalizer import normalize_record, suggest_column_mapping
from ingestion.validator import ValidationResult, validate_batch, validate_record


class ReIngestBlocked(RuntimeError):
    """Raised when identical file bytes were already ingested successfully."""

    def __init__(self, previous_run: IngestionRunInfo) -> None:
        self.previous_run = previous_run
        super().__init__(
            f"file already ingested as run {previous_run.run_id} "
            f"({previous_run.file_name}); cumulative imports never duplicate evidence"
        )


@dataclass(slots=True)
class UploadPlan:
    """Everything known about an upload before any evidence is written."""

    path: Path
    profile: TableProfile
    file_hash: str
    file_size_bytes: int
    suggested_mapping: dict[str, str]
    previous_runs: list[IngestionRunInfo] = field(default_factory=list)

    @property
    def already_ingested(self) -> bool:
        return any(run.status == "completed" for run in self.previous_runs)


def prepare_upload(
    conn: sqlite3.Connection,
    path: str | Path,
    *,
    sheet_name: str | int | None = None,
) -> UploadPlan:
    """Profile a file and check it against ingestion history. Writes nothing."""
    file_path = Path(path)
    profile = inspect_table(file_path, sheet_name=sheet_name)
    digest = deduplication.hash_file_path(file_path)
    previous = repository.find_runs_by_file_hash(conn, digest)

    return UploadPlan(
        path=file_path,
        profile=profile,
        file_hash=digest,
        file_size_bytes=file_path.stat().st_size,
        suggested_mapping=suggest_column_mapping(profile.columns),
        previous_runs=previous,
    )


def preview_records(
    plan: UploadPlan,
    mapping: Mapping[str, str] | None = None,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Dry-run normalisation + validation. No database writes."""
    effective_mapping = dict(mapping or plan.suggested_mapping)
    frame = read_table(plan.path, sheet_name=plan.profile.sheet_name, nrows=limit)
    preview: list[dict[str, Any]] = []
    for raw in frame.to_dict(orient="records"):
        record = normalize_record(raw, effective_mapping)
        result: ValidationResult = validate_record(record)
        record["_validation_ok"] = result.ok
        record["_validation"] = [issue.as_dict() for issue in result.issues]
        preview.append(record)
    return preview


def validate_upload(
    plan: UploadPlan,
    mapping: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate the whole file without storing anything."""
    effective_mapping = dict(mapping or plan.suggested_mapping)
    frame = read_table(plan.path, sheet_name=plan.profile.sheet_name)
    records = [normalize_record(raw, effective_mapping) for raw in frame.to_dict(orient="records")]
    summary = validate_batch(records)
    # Only the evidence text is genuinely required. source_type may legitimately
    # be inferred from the platform/source name, so it is reported, not required.
    summary["required_mapping_missing"] = [
        field for field in ("original_text",) if field not in effective_mapping
    ]
    summary["inferred_fields"] = [
        field
        for field in ("source_type", "source_name")
        if field not in effective_mapping
    ]
    return summary


def open_run(
    conn: sqlite3.Connection,
    plan: UploadPlan,
    *,
    allow_reingest: bool = False,
) -> int:
    """Register an ingestion run, refusing identical completed uploads.

    ``allow_reingest`` exists only for deliberate re-processing of a corrected
    file with identical bytes; it is off by default and must be a conscious act.
    """
    completed = [run for run in plan.previous_runs if run.status == "completed"]
    if completed and not allow_reingest:
        raise ReIngestBlocked(completed[0])

    run_id = repository.start_ingestion_run(
        conn,
        file_name=plan.path.name,
        file_hash_value=plan.file_hash,
        file_size_bytes=plan.file_size_bytes,
        sheet_or_table=plan.profile.sheet_name,
    )
    repository.log_audit(
        conn,
        entity="ingestion_run",
        entity_id=str(run_id),
        action="started",
        detail=f"file={plan.path.name} hash={plan.file_hash[:12]}",
    )
    return run_id


def close_run(
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
    """Close a run with its final counts and an audit entry."""
    repository.finish_ingestion_run(
        conn,
        run_id,
        status=status,
        rows_read=rows_read,
        records_created=records_created,
        records_flagged_duplicate=records_flagged_duplicate,
        records_invalid=records_invalid,
        message=message,
    )
    repository.log_audit(
        conn,
        entity="ingestion_run",
        entity_id=str(run_id),
        action=status,
        detail=message,
    )


def ingestion_history(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    """Recent runs, newest first, for the upload screen."""
    rows = conn.execute(
        "SELECT * FROM ingestion_runs ORDER BY run_id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]
