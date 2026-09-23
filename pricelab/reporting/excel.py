"""The Excel evidence pack: one workbook, one sheet per thing an auditor
asks for, every string cell sanitised, every suppressed cell labelled.

Sheets, in order: Provenance, Source data, Weights, Elementary aggregates,
Upper level aggregates, Quality adjustment ledger, Final index,
Methodology log, Audit extract. The sheet list is itself recorded in the
stamp (`extra["sheets"]`) so the reader of the Provenance sheet can tell
whether a sheet is missing.

Every DataFrame is written through `core.security.sanitize_dataframe`,
headers included, before it reaches openpyxl, and openpyxl is told to
treat the result as values: a cell whose text starts with "=" would
otherwise be stored as a formula, which is the injection this closes.
Suppressed cells (see `reporting.exports.publication_table`) carry the
word "suppressed" in the value cell and the rule in the adjacent column,
never a blank.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from ..core.provenance import STAMP_KEY, ProvenanceStamp
from ..core.security import sanitize_cell, sanitize_dataframe
from ..engine.quality_adjustment import ledger_frame
from .exports import publication_table, wide_publication
from .report import method_note

#: Sheets every pack carries. A run with a seasonal or multilateral stage
#: adds one per supplementary section on top of these, named for the
#: section, and the Provenance sheet lists whatever the pack actually has.
SHEETS = ("Provenance", "Source data", "Weights", "Elementary aggregates",
          "Upper level aggregates", "Quality adjustment ledger", "Final index",
          "Series basis", "Methodology log", "Audit extract")

_HEADER_FILL = PatternFill("solid", fgColor="E9EEF2")
_SUPPRESSED_FILL = PatternFill("solid", fgColor="FDEBD0")


def _sheet_name(name: str) -> str:
    cleaned = "".join(c for c in name if c not in '[]:*?/\\')[:31]
    return str(sanitize_cell(cleaned)).lstrip("'") or "Sheet"


def _write_frame(ws: Any, df: pd.DataFrame, *, mark_suppressed_col: str | None = None) -> None:
    """Write a sanitised frame as values. Datetimes are written as ISO
    dates (text), so a period never turns into a spreadsheet serial that
    a downstream reader has to reinterpret."""
    frame = sanitize_dataframe(df.reset_index() if df.index.name else df)
    for col in frame.columns:
        if pd.api.types.is_datetime64_any_dtype(frame[col]):
            frame[col] = frame[col].dt.strftime("%Y-%m-%d")
    headers = [str(c) for c in frame.columns]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = _HEADER_FILL
    for row in frame.itertuples(index=False):
        values = []
        for v in row:
            if isinstance(v, float) and pd.isna(v):
                values.append(None)
            elif hasattr(v, "item") and not isinstance(v, str):
                values.append(v.item())
            else:
                values.append(v)
        ws.append(values)
        # Values only: a string that still starts with "=" after
        # sanitising cannot exist, but openpyxl's formula detection is
        # disabled per cell regardless, so the guarantee does not rest on
        # the sanitiser alone.
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    if mark_suppressed_col and mark_suppressed_col in headers:
        idx = headers.index(mark_suppressed_col) + 1
        for r in range(2, ws.max_row + 1):
            if ws.cell(row=r, column=idx).value == "suppressed":
                ws.cell(row=r, column=idx).fill = _SUPPRESSED_FILL
    for i, header in enumerate(headers, start=1):
        width = max(len(header), *(len(str(c.value)) for c in ws[get_column_letter(i)][:200]
                                    if c.value is not None)) if ws.max_row > 1 else len(header)
        ws.column_dimensions[get_column_letter(i)].width = min(max(10, width + 2), 60)
    ws.freeze_panes = "A2"


def _weights(source: pd.DataFrame, indices: pd.DataFrame) -> pd.DataFrame:
    if "weight" in source.columns and source["weight"].notna().any():
        supplied = pd.DataFrame(
            source.dropna(subset=["weight"])
            .groupby(["category", "item_id"], as_index=False)["weight"].first())
        supplied["note"] = "expenditure weight as supplied"
        return supplied
    categories = [c for c in indices.columns if c != "All items"]
    return pd.DataFrame({
        "category": categories,
        "weight": [1.0 / len(categories)] * len(categories) if categories else [],
        "note": ["no expenditure weights supplied: the all-items aggregate is an equally "
                 "weighted geometric mean of the category series"] * len(categories)})


def _upper_level(res: Mapping[str, Any]) -> pd.DataFrame:
    indices, yoy = res["indices"], res["inflation"]
    if "All items" not in indices.columns:
        return pd.DataFrame({"note": ["single-series collection: no upper-level aggregate"]})
    cats = [c for c in indices.columns if c != "All items"]
    return pd.DataFrame({
        "period": indices.index,
        "All items": indices["All items"].to_numpy(),
        "year_on_year_pct": yoy["All items"].to_numpy(),
        "categories_aggregated": len(cats),
        "aggregation": "equally weighted geometric mean of category series",
    })


def _methodology(res: Mapping[str, Any], decisions: Sequence[str]) -> pd.DataFrame:
    rows = []
    for block in method_note(dict(res)).split("\n\n"):
        block = block.strip().replace("**", "")
        if not block:
            continue
        head, _, body = block.partition(".")
        rows.append({"section": head.strip(), "text": body.strip() or block})
    for d in decisions:
        rows.append({"section": "Automatic decision", "text": str(d)})
    cfg = res["config"]
    rows.append({"section": "Configuration (JSON)", "text": cfg.to_json(indent=0)})
    return pd.DataFrame(rows, columns=["section", "text"])


def build_evidence_pack(
    res: Mapping[str, Any],
    stamp: ProvenanceStamp,
    *,
    source: pd.DataFrame,
    decisions: Sequence[str] = (),
    audit_events: pd.DataFrame | None = None,
) -> bytes:
    """Build the workbook. `source` is the standardised input panel as
    uploaded (before cleaning); `audit_events` is the audit extract for
    this run, as a DataFrame, or None when no audit database is in scope
    (stated on the sheet, not left empty)."""
    wb = Workbook()
    wb.remove(wb.active)
    from .report import supplementary_tables, with_index_column

    sections = supplementary_tables(dict(res))
    stamp_rows = list(stamp.rows()) + [(STAMP_KEY, stamp.to_json())]
    # What the pack actually contains, not what a pack usually contains: a
    # reader checking the index against the list should not find a sheet
    # named that is not there, or miss one that is.
    stamp_rows.insert(0, ("Sheets", ", ".join(
        list(SHEETS) + [title for title, _, _ in sections])))

    ws = wb.create_sheet(_sheet_name("Provenance"))
    _write_frame(ws, pd.DataFrame(stamp_rows, columns=["field", "value"]))

    ws = wb.create_sheet(_sheet_name("Source data"))
    _write_frame(ws, source)

    ws = wb.create_sheet(_sheet_name("Weights"))
    _write_frame(ws, _weights(source, res["indices"]))

    table = publication_table(res)
    # The multilateral and seasonally adjusted series are in the publication
    # table too (that is how every format gets them), but they are not
    # elementary aggregates and must not be filed as if they were: they get
    # their own sheets below, each with the method that produced it.
    supplementary = table["basis"].astype(str).str.len() > 0
    elementary = table[(table["series"] != "All items") & ~supplementary].copy()
    elementary = elementary[["period", "series", "published", "matched_items", "suppressed",
                             "suppression_rule"]].rename(columns={"published": "index"})
    ws = wb.create_sheet(_sheet_name("Elementary aggregates"))
    _write_frame(ws, elementary, mark_suppressed_col="index")

    ws = wb.create_sheet(_sheet_name("Upper level aggregates"))
    _write_frame(ws, _upper_level(res))

    ws = wb.create_sheet(_sheet_name("Quality adjustment ledger"))
    ledger = ledger_frame(res["config"].quality_adjustment.entries)
    if ledger.empty:
        ledger = pd.DataFrame({"note": ["no quality adjustment was applied to this run"]})
    _write_frame(ws, ledger)

    ws = wb.create_sheet(_sheet_name("Final index"))
    _write_frame(ws, wide_publication(table))

    # Which method produced which series, for every series in the pack that
    # was not produced by the run's own elementary formula. A reader holding
    # one sheet of levels can always find out what they are.
    ws = wb.create_sheet(_sheet_name("Series basis"))
    basis = table[supplementary][["series", "basis"]].drop_duplicates()
    if basis.empty:
        basis = pd.DataFrame({"note": [
            "every series in this pack was produced by the run's own elementary formula, "
            "described in the methodology log"]})
    _write_frame(ws, basis)

    # One sheet per supplementary section, from the same list the report and
    # the deck iterate, so the pack cannot be missing a section the report
    # has.
    for title, note, frame in sections:
        ws = wb.create_sheet(_sheet_name(title))
        body = with_index_column(frame)
        if note:
            _write_frame(ws, pd.DataFrame({"note": [note]}))
            ws.append([])
            start = ws.max_row + 1
            _write_frame(ws, body)
            del start
        else:
            _write_frame(ws, body)

    ws = wb.create_sheet(_sheet_name("Methodology log"))
    _write_frame(ws, _methodology(res, decisions))

    ws = wb.create_sheet(_sheet_name("Audit extract"))
    if audit_events is None or audit_events.empty:
        audit_events = pd.DataFrame({"note": [
            "no audit events were in scope for this export (exported outside a signed-in "
            "session, or the run's label matched no event)"]})
    _write_frame(ws, audit_events)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
