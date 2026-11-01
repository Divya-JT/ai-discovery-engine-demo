"""Validation rules for incoming evidence rows.

Validation never repairs or rewrites text. It classifies a row as storable or
not, and produces human-readable reasons for the ingestion report and the
manual-review queue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping
from urllib.parse import urlparse

from config import SOURCE_TYPE_VALUES

#: Below this length a record is still stored but flagged as thin evidence.
MIN_USABLE_TEXT_LENGTH = 15


@dataclass(slots=True, frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: str  # "error" | "warning"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "severity": self.severity}


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    def summary(self) -> str:
        if not self.issues:
            return "ok"
        return "; ".join(f"{issue.code}: {issue.message}" for issue in self.issues)


def has_usable_text(value: Any) -> bool:
    """True when the value contains real, non-whitespace content."""
    if value is None:
        return False
    return bool(str(value).strip())


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _looks_like_date(value: str) -> bool:
    """Accept any parseable ISO-8601 date prefix.

    Non-ISO formats are a warning (recorded but stored unchanged), not a
    rejection, because source files legitimately vary.
    """
    try:
        date.fromisoformat(value.strip()[:10])
        return True
    except ValueError:
        return False


def validate_record(record: Mapping[str, Any]) -> ValidationResult:
    """Validate one normalised record.

    Errors block storage (empty text, unknown source type).
    Warnings allow storage but are surfaced for review.
    """
    issues: list[ValidationIssue] = []

    text = record.get("original_text")
    if not has_usable_text(text):
        issues.append(
            ValidationIssue(
                code="EMPTY_TEXT",
                message="record has no usable evidence text",
                severity="error",
            )
        )
    elif len(str(text).strip()) < MIN_USABLE_TEXT_LENGTH:
        issues.append(
            ValidationIssue(
                code="THIN_TEXT",
                message=f"text shorter than {MIN_USABLE_TEXT_LENGTH} characters",
                severity="warning",
            )
        )

    source_type = record.get("source_type")
    if source_type not in SOURCE_TYPE_VALUES:
        issues.append(
            ValidationIssue(
                code="INVALID_SOURCE_TYPE",
                message=f"unknown source_type {source_type!r}",
                severity="error",
            )
        )

    if not has_usable_text(record.get("source_name")):
        issues.append(
            ValidationIssue(
                code="MISSING_SOURCE_NAME",
                message="source name missing; a default will be applied",
                severity="warning",
            )
        )

    url = record.get("source_url")
    if url and not _looks_like_url(str(url)):
        issues.append(
            ValidationIssue(
                code="SUSPECT_URL",
                message="source URL is not a valid http(s) URL; stored unchanged",
                severity="warning",
            )
        )

    source_date = record.get("source_date")
    if source_date and not _looks_like_date(str(source_date)):
        issues.append(
            ValidationIssue(
                code="UNPARSED_DATE",
                message="source date is not ISO-8601; stored unchanged",
                severity="warning",
            )
        )

    return ValidationResult(
        ok=not any(issue.severity == "error" for issue in issues), issues=issues
    )


def validate_batch(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate validation over a batch, for the pre-import preview."""
    valid = 0
    invalid = 0
    warned = 0
    issue_counts: dict[str, int] = {}

    for record in records:
        result = validate_record(record)
        if result.ok:
            valid += 1
        else:
            invalid += 1
        if result.warnings:
            warned += 1
        for issue in result.issues:
            issue_counts[issue.code] = issue_counts.get(issue.code, 0) + 1

    return {
        "rows": len(records),
        "valid": valid,
        "invalid": invalid,
        "with_warnings": warned,
        "issues": issue_counts,
    }
