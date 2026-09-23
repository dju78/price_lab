"""Machine-readable exports: the publication table with disclosure
control applied, a stamped CSV, and an SDMX-ML 2.1 generic data message.

One publication table
---------------------
`publication_table` is the long-form series every machine-readable export
and the Excel evidence pack write from: one row per period and series,
with the index level, the matched quote count behind it, and disclosure
control applied through `core.security.suppress_with_secondary`. A
suppressed cell is written as the word "suppressed" plus the rule that
suppressed it, never as a blank: a blank is indistinguishable from a
missing observation, and the reader has to be able to tell "we did not
have this number" from "we have it and are not allowed to publish it".

Primary suppression removes any cell built from fewer than the
deployment's `suppression_min_count` matched quotes. Secondary
suppression then protects each period: the all-items aggregate is an
equally weighted geometric mean of the category series, so a period that
published its aggregate and every category but one would let a reader
back out the suppressed one, and the smallest remaining published cell in
that period is suppressed too. That is the single-total case
`suppress_with_secondary` certifies.

SDMX-ML
-------
`to_sdmx_ml` writes a GenericData message in SDMX-ML 2.1, validated in
`tests/test_exports.py` against the standard's own XSDs (the SDMX
Technical Working Group's published schema set, vendored under
tests/fixtures/sdmx_2_1), not merely shaped like one. The data structure
is PriceLab's own, declared in the header (`PRICELAB:PRICELAB_CPI(1.0)`):
one series dimension, `SERIES`, keyed on the category label; time at the
observation level; `OBS_STATUS` "C" on a suppressed observation, whose
value is omitted; and the provenance stamp as a dataset-level annotation.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from typing import Any, cast

import numpy as np
import pandas as pd
from lxml import etree

from ..core.provenance import STAMP_KEY, ProvenanceStamp
from ..core.security import safe_csv, sanitize_cell, suppress_with_secondary

SUPPRESSED = "suppressed"

NS = {
    "message": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "generic": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic",
    "common": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
}
SDMX_AGENCY = "PRICELAB"
SDMX_STRUCTURE_ID = "PRICELAB_CPI"
SDMX_STRUCTURE_VERSION = "1.0"


# ---------------------------------------------------------------------
# What produced a series
# ---------------------------------------------------------------------
#: Series names carrying a basis other than the run's own elementary
#: formula are prefixed so they can never be mistaken for it in a flat file.
MULTILATERAL_PREFIX = "multilateral: "
SEASONAL_ADJUSTED_PREFIX = "seasonally adjusted: "


def multilateral_basis(res: Mapping[str, Any]) -> str:
    """Method, window, splice and the spread across methods, in one phrase.

    Every place a multilateral level appears prints this beside it. A
    multilateral number is a fact about prices *and* about a method, and
    the reader cannot tell which method from the number; the phrase is the
    only thing that carries it, so it is built once here and used
    everywhere rather than reassembled per format.
    """
    aggregate = res.get("multilateral")
    if aggregate is None:
        return ""
    cfg = res["config"].multilateral
    from ..engine.multilateral import METHOD_LABELS, SPLICE_LABELS

    phrase = (f"{METHOD_LABELS.get(cfg.method, cfg.method)}, {cfg.window}-period window, "
              f"{SPLICE_LABELS.get(cfg.splice, cfg.splice).lower()}")
    spreads = [float(r.splice_spread_pp.max()) for r in aggregate.results.values()
               if len(r.splice_spread_pp)]
    if spreads:
        phrase += (f"; the extension rule could have moved a level by up to "
                   f"{max(spreads):.2f} index points")
    if aggregate.skipped:
        phrase += (f"; {len(aggregate.skipped)} category(ies) produced no multilateral series "
                   "and carry no weight in this headline")
    if not aggregate.weighted:
        phrase += "; aggregated by equally weighted geometric mean, no expenditure weights"
    return phrase


def seasonal_basis(res: Mapping[str, Any]) -> str:
    """The engine that produced the adjusted series, named unconditionally."""
    seasonal = res.get("seasonal")
    if seasonal is None or seasonal.adjustment is None:
        return ""
    return str(seasonal.adjustment.label)


#: Every place a seasonally adjusted series reaches a reader, and what that
#: place carries with it. Each must show the engine that ran (with the
#: fallback said as such), the unadjusted series beside the adjusted one,
#: and the direct-adjustment statement with its additivity warning.
#: `tests/test_seasonal_surfaces.py` renders each and checks all three, and
#: fails if a module in reporting/ or pages/ touches the adjusted series
#: without being listed here.
SEASONAL_SURFACES: dict[str, str] = {
    "page": "pages/seasonal.py: the status line before the chart, the chart itself, its "
            "caption, and the CSV download's first line",
    "chart": "reporting/charts.seasonal_adjustment_chart: the title and legend name the "
             "engine; the note under the axes carries the full label; both series drawn",
    "method_note": "engine/seasonal.adjustment_note, via reporting/report.seasonal_note: the "
                   "paragraph every report, deck and bulletin quotes",
    "markdown": "reporting/report.build_markdown: the Seasonality method section and the "
                "Seasonal adjustment table (both series in adjacent columns)",
    "word": "reporting/report.build_docx: the same section and table, and the chart",
    "deck": "reporting/deck.build_deck: the 'What produced these series' slide and the "
            "seasonal adjustment chart slide",
    "excel": "reporting/excel.build_evidence_pack: the Seasonal adjustment sheet and the "
             "Series basis sheet",
    "bulletin": "reporting/bulletin.build_bulletin: the chart, the table, 'How each series "
                "was produced' and the methodology note",
    "csv": "reporting/exports.index_csv: the basis column on the adjusted rows, and the "
           "unadjusted rows",
    "sdmx": "reporting/exports.to_sdmx_ml: the BASIS attribute on the adjusted series, and "
            "the unadjusted series",
}


def series_basis(res: Mapping[str, Any]) -> dict[str, str]:
    """Every series the run can publish, mapped to what produced it.

    Empty string for the ordinary bilateral series, whose basis is the run's
    configuration and is already in the method note and the provenance
    stamp. Non-empty for anything a reader could otherwise mistake for it.
    """
    basis: dict[str, str] = {}
    multilateral = multilateral_basis(res)
    aggregate = res.get("multilateral")
    if multilateral and aggregate is not None:
        for column in aggregate.indices.columns:
            basis[f"{MULTILATERAL_PREFIX}{column}"] = multilateral
    seasonal = seasonal_basis(res)
    stage = res.get("seasonal")
    adjustment = stage.adjustment if stage is not None else None
    if seasonal and adjustment is not None:
        basis[f"{SEASONAL_ADJUSTED_PREFIX}{adjustment.series_name}"] = seasonal
    return basis


def additional_series(res: Mapping[str, Any]) -> pd.DataFrame:
    """The multilateral and seasonally adjusted series, as publication rows.

    Returned in exactly the shape `publication_table` builds, so they can be
    concatenated onto it and every downstream format -- the wide table, the
    stamped CSV, the SDMX message, the Excel pack, the bulletin -- carries
    them without knowing they exist. That is the point: there is no format
    that can accidentally be left out, because no format opts in.

    The seasonally adjusted series brings its unadjusted counterpart with
    it, always, under its own name. A reader who finds one in an export
    finds the other beside it.
    """
    rows: list[dict[str, Any]] = []
    basis = series_basis(res)

    aggregate = res.get("multilateral")
    if aggregate is not None:
        for column in aggregate.indices.columns:
            name = f"{MULTILATERAL_PREFIX}{column}"
            for period, value in aggregate.indices[column].items():
                rows.append({
                    "period": pd.Timestamp(cast(Any, period)), "series": name,
                    "index": float(value), "matched_items": float("nan"),
                    "basis": basis.get(name, "")})

    seasonal = res.get("seasonal")
    adjustment = seasonal.adjustment if seasonal is not None else None
    if adjustment is not None:
        adjusted_name = f"{SEASONAL_ADJUSTED_PREFIX}{adjustment.series_name}"
        for period, value in adjustment.adjusted.items():
            rows.append({
                "period": pd.Timestamp(cast(Any, period)), "series": adjusted_name,
                "index": float(value), "matched_items": float("nan"),
                "basis": basis.get(adjusted_name, "")})
        # The unadjusted series, unconditionally, under a name that says so.
        # This is the mechanism behind "the unadjusted series is published
        # alongside the adjusted series everywhere the adjusted series
        # appears": they are produced by the same loop, from the same
        # object, and there is no argument that omits the second.
        for period, value in adjustment.unadjusted.items():
            rows.append({
                "period": pd.Timestamp(cast(Any, period)),
                "series": f"unadjusted: {adjustment.series_name}",
                "index": float(value), "matched_items": float("nan"),
                "basis": "as compiled, before seasonal adjustment"})

    if not rows:
        return pd.DataFrame(columns=["period", "series", "index", "matched_items", "basis"])
    frame = pd.DataFrame(rows)
    usable: pd.DataFrame = frame[np.isfinite(frame["index"])].reset_index(drop=True)
    return usable


def publication_table(res: Mapping[str, Any], min_count: int | None = None) -> pd.DataFrame:
    """Long-form publication table with disclosure control applied.

    Columns: period, series, index (float, NaN where suppressed),
    matched_items, suppressed (bool), suppression_rule (str, empty where
    published), published (the value to print: the number, or the word
    "suppressed").
    """
    indices: pd.DataFrame = res["indices"]
    matched: pd.DataFrame = res["matched_counts"]
    rows = []
    for series in indices.columns:
        counts = matched[series] if series in matched.columns else None
        for period in indices.index:
            rows.append({
                "period": pd.Timestamp(period), "series": str(series),
                "index": float(indices.loc[period, series]),
                "matched_items": (float(counts.loc[period]) if counts is not None
                                  and period in counts.index else float("nan")),
            })
    table = pd.DataFrame(rows)
    if table.empty:
        return table.assign(basis="", suppressed=False, suppression_rule="", published="")
    table["basis"] = ""

    categories = table[table["series"] != "All items"].copy()
    aggregate = table[table["series"] == "All items"].copy()
    # The aggregate is not a cell built from quotes; it is derived from
    # the categories and is protected through them (secondary suppression
    # below), so it is never itself primary-suppressed on a count.
    if len(categories):
        protected = suppress_with_secondary(
            categories, group_col="period", value_col="index", count_col="matched_items",
            min_count=min_count)
    else:
        protected = categories.assign(suppressed=False, secondary_suppressed=False)
    from ..core.config import get_settings
    threshold = min_count if min_count is not None else get_settings().suppression_min_count
    protected["suppression_rule"] = ""
    primary = protected["suppressed"] & ~protected["secondary_suppressed"]
    protected.loc[primary, "suppression_rule"] = (
        f"primary: built from fewer than {threshold} matched quotes")
    protected.loc[protected["secondary_suppressed"], "suppression_rule"] = (
        "secondary: smallest remaining cell in a period with one primary-suppressed cell, "
        "so the suppressed value cannot be recovered from the all-items aggregate")
    aggregate = aggregate.assign(suppressed=False, secondary_suppressed=False, suppression_rule="")
    # The multilateral and seasonally adjusted series, where the run
    # produced them. Appended here, in the one function every format reads
    # its rows from, so no export can be shipped having quietly left them
    # out -- and each row carries `basis`, so no level can appear anywhere
    # without the method that produced it beside it.
    extra = additional_series(res)
    if not extra.empty:
        extra = extra.assign(suppressed=False, secondary_suppressed=False,
                             suppression_rule="")
    frames = [protected, aggregate] + ([extra] if not extra.empty else [])
    out = pd.concat(frames, ignore_index=True)
    out["basis"] = out["basis"].fillna("")
    out = out.sort_values(["series", "period"]).reset_index(drop=True)
    out["published"] = [
        SUPPRESSED if s else (f"{v:.6f}" if pd.notna(v) else "")
        for s, v in zip(out["suppressed"], out["index"], strict=True)]
    return out.drop(columns=["secondary_suppressed"])


def wide_publication(table: pd.DataFrame) -> pd.DataFrame:
    """The publication table pivoted to one row per period, one column per
    series, with suppressed cells carrying the word rather than a blank."""
    if table.empty:
        return pd.DataFrame()
    wide = table.pivot(index="period", columns="series", values="published")
    wide.index = pd.DatetimeIndex(wide.index).strftime("%Y-%m-%d")
    wide.index.name = "period"
    ordered = [c for c in wide.columns if c != "All items"] + (
        ["All items"] if "All items" in wide.columns else [])
    return wide[ordered]


# ---------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------
def stamped_csv(df: pd.DataFrame, stamp: ProvenanceStamp, notice: str = "", **kwargs: Any) -> str:
    """`safe_csv` with the provenance stamp as the first comment line and an
    optional notice as the second. `#` comment lines are what every
    mainstream CSV reader skips and every text editor shows."""
    lines = [f"# {STAMP_KEY} {stamp.to_json()}"]
    if notice:
        lines.append(f"# {sanitize_cell(notice)}")
    kwargs.setdefault("index", False)
    return "\n".join(lines) + "\n" + safe_csv(df, **kwargs)


def index_csv(res: Mapping[str, Any], stamp: ProvenanceStamp, notice: str = "") -> str:
    """The publication table as a stamped CSV."""
    table = publication_table(res).copy()
    table["period"] = table["period"].dt.strftime("%Y-%m-%d")
    return stamped_csv(table, stamp, notice)


# ---------------------------------------------------------------------
# SDMX-ML 2.1
# ---------------------------------------------------------------------
def _sub(parent: etree._Element, ns: str, tag: str, text: str | None = None,
         **attrs: str) -> etree._Element:
    el = etree.SubElement(parent, f"{{{NS[ns]}}}{tag}", **attrs)
    if text is not None:
        el.text = text
    return el


def to_sdmx_ml(res: Mapping[str, Any], stamp: ProvenanceStamp, *, message_id: str | None = None,
               sender_name: str = "PriceLab") -> bytes:
    """An SDMX-ML 2.1 GenericData message of the publication table.

    Every string that came from user data (series names, the label) goes
    through the same sanitiser as every other export: an XML attribute is
    not a spreadsheet cell, but the file will be opened by tools that turn
    it into one.
    """
    table = publication_table(res)
    root = etree.Element(f"{{{NS['message']}}}GenericData", nsmap=NS)
    header = _sub(root, "message", "Header")
    _sub(header, "message", "ID", message_id or f"PRICELAB_{stamp.run_id}"[:64])
    _sub(header, "message", "Test", "false")
    _sub(header, "message", "Prepared", stamp.generated_at.replace("+00:00", ""))
    sender = _sub(header, "message", "Sender", id=SDMX_AGENCY)
    name = _sub(sender, "common", "Name", sender_name)
    name.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
    structure = _sub(header, "message", "Structure", structureID=SDMX_STRUCTURE_ID,
                     dimensionAtObservation="TIME_PERIOD")
    ref_holder = _sub(structure, "common", "Structure")
    etree.SubElement(ref_holder, "Ref", agencyID=SDMX_AGENCY, id=SDMX_STRUCTURE_ID,
                     version=SDMX_STRUCTURE_VERSION)

    dataset = _sub(root, "message", "DataSet", structureRef=SDMX_STRUCTURE_ID, action="Information")
    annotations = _sub(dataset, "common", "Annotations")
    annotation = _sub(annotations, "common", "Annotation")
    _sub(annotation, "common", "AnnotationTitle", STAMP_KEY)
    _sub(annotation, "common", "AnnotationType", "provenance")
    text = _sub(annotation, "common", "AnnotationText", stamp.to_json())
    text.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
    ds_attrs = _sub(dataset, "generic", "Attributes")
    _sub(ds_attrs, "generic", "Value", id="RUN_ID", value=str(sanitize_cell(stamp.run_id)))
    _sub(ds_attrs, "generic", "Value", id="DATA_VINTAGE", value=stamp.data_vintage)
    _sub(ds_attrs, "generic", "Value", id="CODE_VERSION", value=stamp.code_version)
    _sub(ds_attrs, "generic", "Value", id="NON_STANDARD_FORMULA",
         value="true" if stamp.non_standard_formula else "false")

    for series_name, group in table.groupby("series", sort=False):
        series = _sub(dataset, "generic", "Series")
        key = _sub(series, "generic", "SeriesKey")
        _sub(key, "generic", "Value", id="SERIES", value=str(sanitize_cell(series_name)))
        attrs = _sub(series, "generic", "Attributes")
        _sub(attrs, "generic", "Value", id="UNIT_MEASURE", value="INDEX")
        # What produced a supplementary series, on the series itself: the
        # seasonal engine and the direct-adjustment statement, the
        # multilateral method and window. A series key alone ("seasonally
        # adjusted: All items") says a series was adjusted but not by what,
        # and an SDMX consumer never sees the CSV's basis column.
        basis = str(group["basis"].iloc[0]) if "basis" in group.columns else ""
        if basis:
            _sub(attrs, "generic", "Value", id="BASIS", value=str(sanitize_cell(basis)))
        head = stamp.headline or {}
        _sub(attrs, "generic", "Value", id="BASE_PER",
             value=str(head.get("reference_period", "")))
        # Plain dicts rather than `itertuples`: this frame has a column
        # literally called "index", which a namedtuple field silently
        # shadows `tuple.index` with. It happens to work, and it is exactly
        # the kind of coincidence that stops working.
        for row in group.sort_values("period").to_dict("records"):
            obs = _sub(series, "generic", "Obs")
            _sub(obs, "generic", "ObsDimension",
                 value=pd.Timestamp(cast(Any, row["period"])).strftime("%Y-%m"))
            value = row["index"]
            if row["suppressed"]:
                obs_attrs = _sub(obs, "generic", "Attributes")
                _sub(obs_attrs, "generic", "Value", id="OBS_STATUS", value="C")
                _sub(obs_attrs, "generic", "Value", id="OBS_COMMENT",
                     value=str(sanitize_cell(row["suppression_rule"])))
            elif pd.notna(value):
                _sub(obs, "generic", "ObsValue", value=f"{float(cast(Any, value)):.6f}")
            else:
                obs_attrs = _sub(obs, "generic", "Attributes")
                _sub(obs_attrs, "generic", "Value", id="OBS_STATUS", value="M")
    document: bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                                     pretty_print=True)
    return document


def validate_sdmx_ml(document: bytes, schema_dir: str) -> tuple[bool, list[str]]:
    """Validate an SDMX-ML message against the standard's schema set
    (`SDMXMessage.xsd` and the files it imports, in `schema_dir`)."""
    from pathlib import Path

    schema = etree.XMLSchema(etree.parse(str(Path(schema_dir) / "SDMXMessage.xsd")))
    doc = etree.parse(io.BytesIO(document))
    ok = bool(schema.validate(doc))
    return ok, [str(e.message) for e in schema.error_log]
