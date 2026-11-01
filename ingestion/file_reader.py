"""Read research files without modifying them.

M1 scope: *inspect* and *read* only. Nothing here writes to the database, and
nothing rewrites the source file. The original columns are always available to
the caller so provenance can be preserved at import time.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

SUPPORTED_SUFFIXES: tuple[str, ...] = (".csv", ".xlsx", ".xls")

_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


class UnsupportedFileError(ValueError):
    """Raised for file types we do not claim to support."""


@dataclass(slots=True)
class TableProfile:
    """Structural description of a research file, used before any import."""

    path: Path
    kind: str
    columns: list[str]
    row_count: int
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    encoding: str | None = None
    sheet_name: str | None = None
    delimiter: str | None = None
    empty_text_rows: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.path.name,
            "kind": self.kind,
            "encoding": self.encoding,
            "sheet_name": self.sheet_name,
            "rows": self.row_count,
            "columns": self.columns,
            "empty_text_rows": self.empty_text_rows,
        }


def _detect_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def read_table(
    path: str | Path,
    *,
    sheet_name: str | int | None = None,
    nrows: int | None = None,
) -> pd.DataFrame:
    """Read a CSV/XLSX file into a DataFrame, preserving every original column."""
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedFileError(
            f"{suffix or '<no extension>'} is not supported; use CSV or XLSX"
        )
    if not file_path.is_file():
        raise FileNotFoundError(file_path)

    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(file_path, sheet_name=sheet_name or 0, nrows=nrows)

    last_error: Exception | None = None
    for encoding in _ENCODINGS:
        try:
            with file_path.open("r", encoding=encoding, newline="") as handle:
                sample = handle.read(8192)
                handle.seek(0)
                delimiter = _detect_delimiter(sample)
                return pd.read_csv(
                    handle,
                    sep=delimiter,
                    nrows=nrows,
                    dtype=str,
                    keep_default_na=False,
                    skip_blank_lines=True,
                )
        except UnicodeDecodeError as exc:  # try the next encoding
            last_error = exc
            continue
    raise UnsupportedFileError(f"could not decode {file_path.name}: {last_error}")


def inspect_table(
    path: str | Path,
    *,
    sheet_name: str | int | None = None,
    sample_size: int = 5,
) -> TableProfile:
    """Profile a file: columns, row count, sample rows, encoding.

    Read-only. Safe to run on the consolidated dataset without importing it.
    """
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    frame = read_table(file_path, sheet_name=sheet_name)

    encoding: str | None = None
    delimiter: str | None = None
    if suffix == ".csv":
        with file_path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            sample = handle.read(8192)
        delimiter = _detect_delimiter(sample)
        encoding = "utf-8-sig"

    columns = [str(column) for column in frame.columns]
    text_column = _guess_text_column(columns)
    empty_text_rows = 0
    if text_column is not None:
        series = frame[text_column].astype(str).str.strip()
        empty_text_rows = int((series == "").sum())

    return TableProfile(
        path=file_path,
        kind="csv" if suffix == ".csv" else "excel",
        columns=columns,
        row_count=int(len(frame)),
        sample_rows=frame.head(sample_size).to_dict(orient="records"),
        encoding=encoding,
        sheet_name=str(sheet_name) if sheet_name is not None else None,
        delimiter=delimiter,
        empty_text_rows=empty_text_rows,
    )


_TEXT_HINTS: tuple[str, ...] = (
    "text",
    "review",
    "comment",
    "content",
    "body",
    "message",
    "post",
    "quote",
    "snippet",
    "description",
)


def _guess_text_column(columns: list[str]) -> str | None:
    """Best-effort hint only; the UI lets the user confirm the mapping."""
    lowered = {column: column.lower() for column in columns}
    for hint in _TEXT_HINTS:
        for column, name in lowered.items():
            if hint in name:
                return column
    return None


def guess_text_column(columns: list[str]) -> str | None:
    """Public wrapper used by the upload screen to preselect a text column."""
    return _guess_text_column(columns)
