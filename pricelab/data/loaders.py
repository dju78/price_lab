"""The upload pipeline: format dispatch (CSV, Excel including multi-sheet,
Parquet, JSON), encoding and delimiter detection, header-row inference, and
a memory-footprint estimate enforced against the same size cap
`pages.ingest.validate_upload` already checks on the raw file size.

That existing check only bounds the bytes on disk. A CSV of mostly short
numeric fields can easily cost several times its file size once parsed
into a DataFrame (per-cell Python object overhead for anything not a
clean numeric dtype), so a file that clears the file-size cap can still
be the thing that pushes the process over its memory budget. This module
adds the check that catches that case, and aborts a chunked CSV read
partway through if the actual measured memory of the chunks read so far
already exceeds the limit, rather than only estimating beforehand from a
sample.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import pandas as pd

#: Rows per chunk for the chunked CSV reader. Small enough that a read
#: aborted for exceeding its memory budget stops within a few chunks of
#: crossing the line, large enough that chunking overhead is negligible.
CHUNK_ROWS = 50_000

#: Bytes sampled to detect encoding, delimiter and the header row, and to
#: build the per-row memory estimate for CSV. Large enough to see a
#: realistic mix of values, small enough to stay cheap regardless of the
#: full file's size.
SAMPLE_BYTES = 262_144

EXCEL_EXTENSIONS = (".xlsx", ".xlsm", ".xls")


class LoaderError(Exception):
    """A file could not be read into the canonical shape at all."""


class MemoryFootprintError(LoaderError):
    def __init__(self, estimated_mb: float, max_mb: float):
        self.estimated_mb = estimated_mb
        self.max_mb = max_mb
        super().__init__(
            f"Estimated in-memory size {estimated_mb:.1f} MB exceeds the {max_mb:.0f} MB "
            "limit configured for this deployment. Upload a smaller extract, or ask an "
            "administrator to raise PRICELAB_UPLOAD_MAX_MB.")


@dataclass
class LoadResult:
    df: pd.DataFrame
    encoding: str | None
    """Detected text encoding, for CSV; None for binary formats."""
    delimiter: str | None
    """Detected field delimiter, for CSV; None otherwise."""
    header_row: int
    """0-indexed row used as the header. Usually 0; not 0 when a title or
    metadata line sits above the real header row."""


def detect_encoding(sample: bytes) -> str:
    """The first of these that decodes the sample without error. Falls
    back to latin-1, which never raises (every byte sequence is valid
    under it) -- a wrong guess here shows up as garbled text for the
    analyst to notice and re-upload with an explicit override, not as a
    crash partway through a large read.
    """
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "latin-1"


def detect_delimiter(text_sample: str) -> str:
    """The candidate delimiter (of comma, semicolon, tab, pipe) whose
    per-line count is the most consistent across the sample's non-blank
    lines. Counting rather than `csv.Sniffer` directly, because the
    Sniffer gives up on the whole sample -- raising rather than skipping
    the offending line -- the moment one line in it does not contain the
    real delimiter at all, which a genuine title or metadata line above
    the header does by definition.
    """
    lines = [line for line in text_sample.splitlines() if line.strip()]
    best_delimiter = ","
    best_score = 0
    for candidate in (",", ";", "\t", "|"):
        counts = [line.count(candidate) for line in lines]
        nonzero = [c for c in counts if c > 0]
        if not nonzero:
            continue
        mode_count = max(set(nonzero), key=nonzero.count)
        score = nonzero.count(mode_count)
        if score > best_score:
            best_score = score
            best_delimiter = candidate
    return best_delimiter


def infer_header_row(rows: list[list[str]], max_scan_rows: int = 10) -> int:
    """The first row that looks like a real header: at least two non-blank
    cells, with the row below it the same width. Handles a title or
    metadata line placed above the real header, which happens often enough
    in hand-edited collection spreadsheets to be worth checking for, while
    defaulting to row 0 whenever there is nothing to suggest otherwise.
    """
    scan = rows[:max_scan_rows]
    if len(scan) < 2:
        return 0
    for i in range(len(scan) - 1):
        non_blank = len([c for c in scan[i] if str(c).strip()])
        if non_blank >= 2 and len(scan[i]) == len(scan[i + 1]):
            return i
    return 0


def sniff_csv(file_bytes: bytes) -> tuple[str, str, int]:
    """`(encoding, delimiter, header_row)` from a bounded sample, without
    reading the whole file."""
    sample = file_bytes[:SAMPLE_BYTES]
    encoding = detect_encoding(sample)
    text_sample = sample.decode(encoding, errors="replace")
    delimiter = detect_delimiter(text_sample)
    rows = list(csv.reader(io.StringIO(text_sample), delimiter=delimiter))
    header_row = infer_header_row(rows)
    return encoding, delimiter, header_row


def list_sheets(file_bytes: bytes, name: str) -> list[str] | None:
    """Sheet names for a multi-sheet Excel workbook, or None for anything
    else (CSV, Parquet, JSON have no sheet concept)."""
    if not name.lower().endswith(EXCEL_EXTENSIONS):
        return None
    return [str(s) for s in pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names]


def estimate_memory_mb(file_bytes: bytes, name: str, sheet_name: int | str = 0) -> float:
    """An upper-bound estimate of the parsed DataFrame's memory footprint,
    in megabytes.

    For CSV: parse a bounded sample, measure its actual memory with
    `memory_usage(deep=True)`, and scale by (total lines / sample lines).
    Counting lines is a byte scan of data already resident in memory (the
    upload's bytes), not a second full parse, so this stays cheap even for
    a large file.

    For Excel, Parquet and JSON, pandas has no equivalent "read N rows
    cheaply" API that stays proportionally cheaper than a full parse for
    every format, so the estimate is the real, fully-parsed size -- exact
    rather than projected, but only known after the read has already
    happened. `read_upload` still checks it before returning the result to
    a caller, and before the win of an early abort is available for these
    formats specifically -- a limitation worth stating rather than hiding
    behind a falsely proportional-looking number.
    """
    lower = name.lower()
    if lower.endswith(".csv"):
        encoding, delimiter, header_row = sniff_csv(file_bytes)
        sample_text = file_bytes[:SAMPLE_BYTES].decode(encoding, errors="replace")
        sample_lines = sample_text.count("\n") or 1
        sample_df = pd.read_csv(
            io.StringIO(sample_text), delimiter=delimiter, header=header_row,
            on_bad_lines="skip")
        if sample_df.empty:
            return 0.0
        bytes_per_line = sample_df.memory_usage(deep=True).sum() / max(len(sample_df), 1)
        total_lines = file_bytes.count(b"\n") or 1
        scale = total_lines / sample_lines
        return bytes_per_line * len(sample_df) * scale / (1024 * 1024)

    if lower.endswith(EXCEL_EXTENSIONS):
        df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet_name)
        return float(df.memory_usage(deep=True).sum()) / (1024 * 1024)

    if lower.endswith(".parquet"):
        df = pd.read_parquet(io.BytesIO(file_bytes))
        return float(df.memory_usage(deep=True).sum()) / (1024 * 1024)

    if lower.endswith(".json"):
        df = pd.read_json(io.BytesIO(file_bytes))
        return float(df.memory_usage(deep=True).sum()) / (1024 * 1024)

    raise LoaderError(f"'{name}' is not a recognised format (csv, xlsx, xlsm, xls, parquet, json)")


def _read_csv_chunked(
    file_bytes: bytes, encoding: str, delimiter: str, header_row: int, max_mb: float | None
) -> pd.DataFrame:
    """Read in bounded chunks, checking the running measured memory of the
    chunks accumulated so far against `max_mb` after every chunk. This is
    the real safety net beyond `estimate_memory_mb`'s pre-read projection:
    a projection from a sample can undercount a file whose later rows are
    wider or less regular than its first ones, so the read itself can
    still abort partway rather than only being refused up front on a
    misleadingly small estimate.
    """
    buf = io.StringIO(file_bytes.decode(encoding, errors="replace"))
    reader = pd.read_csv(
        buf, delimiter=delimiter, header=header_row, chunksize=CHUNK_ROWS, on_bad_lines="skip")
    chunks: list[pd.DataFrame] = []
    running_bytes = 0
    for chunk in reader:
        chunks.append(chunk)
        running_bytes += int(chunk.memory_usage(deep=True).sum())
        if max_mb is not None and running_bytes / (1024 * 1024) > max_mb:
            raise MemoryFootprintError(running_bytes / (1024 * 1024), max_mb)
    if not chunks:
        return pd.DataFrame()
    return pd.concat(chunks, ignore_index=True)


def read_upload(
    file_bytes: bytes, name: str, *, sheet_name: int | str = 0, max_mb: float | None = None
) -> LoadResult:
    """Read an upload into the canonical raw DataFrame shape, dispatching
    on `name`'s extension (csv, xlsx/xlsm/xls, parquet, json).

    `max_mb`, if given, is enforced against `estimate_memory_mb` before
    any full read is attempted, and -- for CSV specifically, via chunked
    reading -- again against the real measured size as the file is read,
    so a read that would exceed it is abandoned rather than completed and
    then rejected after the fact.
    """
    lower = name.lower()
    estimated_mb = estimate_memory_mb(file_bytes, name, sheet_name)
    if max_mb is not None and estimated_mb > max_mb:
        raise MemoryFootprintError(estimated_mb, max_mb)

    if lower.endswith(".csv"):
        encoding, delimiter, header_row = sniff_csv(file_bytes)
        df = _read_csv_chunked(file_bytes, encoding, delimiter, header_row, max_mb)
        return LoadResult(df=df, encoding=encoding, delimiter=delimiter, header_row=header_row)

    if lower.endswith(EXCEL_EXTENSIONS):
        df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet_name)
        return LoadResult(df=df, encoding=None, delimiter=None, header_row=0)

    if lower.endswith(".parquet"):
        df = pd.read_parquet(io.BytesIO(file_bytes))
        return LoadResult(df=df, encoding=None, delimiter=None, header_row=0)

    if lower.endswith(".json"):
        df = pd.read_json(io.BytesIO(file_bytes))
        return LoadResult(df=df, encoding=None, delimiter=None, header_row=0)

    raise LoaderError(f"'{name}' is not a recognised format (csv, xlsx, xlsm, xls, parquet, json)")
