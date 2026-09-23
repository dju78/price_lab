"""Written report generation.

The deck is for presenting; this is for the record. It carries the full
narrative, the method note and the tables, so a reader who was not in the room
can follow what was done and check it.
"""

import io
from datetime import date
from typing import Any

import pandas as pd
from docx import Document
from docx.shared import Inches, Pt, RGBColor

from ..core.config import IndexConfig
from ..core.provenance import STAMP_KEY, ProvenanceStamp, build_stamp
from ..core.security import sanitize_cell
from ..engine.custom import non_standard_notice
from ..engine.index import resolve_index_reference_period
from ..engine.insights import Narrative
from .deck import _pretty

INK = RGBColor(0x12, 0x26, 0x3A)
PRIMARY = RGBColor(0x1E, 0x4D, 0x6B)
MUTED = RGBColor(0x6B, 0x7C, 0x8C)
ACCENT = RGBColor(0xD9, 0x82, 0x2B)

KIND_ORDER = ["trend", "quality", "structure", "seasonal", "method"]
KIND_TITLES = {
    "trend": "Price movement",
    "quality": "Data quality",
    "structure": "Sample structure",
    "seasonal": "Seasonality",
    "method": "Method and sensitivity",
}


# ----------------------------------------------------------------------
def _formula_label(index_cfg: IndexConfig) -> str:
    """How the formula is named in prose. A custom formula is named as
    analyst-defined and quoted in full, because the word "Custom" on its
    own tells a reader nothing they can check."""
    if index_cfg.formula == "custom":
        return f"analyst-defined ({index_cfg.custom_formula})"
    return str(index_cfg.formula).replace("_", " ").title().replace("Tornqvist", "Törnqvist")


def quantity_note(res: dict[str, Any]) -> str:
    """How the quantities behind a quantity-weighted formula were obtained,
    or what Laspeyres meant on a price-only run; empty otherwise."""
    from ..engine.index import QUANTITY_FORMULAE, quantity_series

    cfg = res["config"].index
    quantities = quantity_series(res["imputed"])
    if cfg.formula not in QUANTITY_FORMULAE or quantities is None:
        if cfg.formula == "laspeyres":
            return (" Laspeyres here is the share-weighted (Young) form over the supplied weights, "
                    "falling back to Jevons where none were supplied; the collection carries no "
                    "quantities.")
        return ""
    source = ("derived as expenditure / price, no quantity column having been mapped, and every "
              "result is flagged as built on derived quantities"
              if quantities.name == "quantity_derived" else "taken from the mapped quantity column")
    note = f" Quantities were {source}."
    if cfg.formula == "unit_value":
        note += (" Unit value: the compiler asserted homogeneity of each category's items on "
                 f"these grounds: {cfg.homogeneity_justification!r}.")
    return note


def multilateral_note(res: dict[str, Any]) -> str:
    """One paragraph on the multilateral series, naming the method.

    Empty when the run computed none, so it can be concatenated
    unconditionally -- and never a sentence about a multilateral level that
    omits the method, the window and the rule, because those are what the
    level is a fact about.
    """
    aggregate = res.get("multilateral")
    if aggregate is None:
        return ""
    from .exports import multilateral_basis

    headline = aggregate.headline.dropna()
    where = (f" At {headline.index[-1]:%B %Y} it reads {float(headline.iloc[-1]):.2f}."
             if len(headline) else "")
    skipped = ""
    if aggregate.skipped:
        named = ", ".join(sorted(aggregate.skipped)[:4])
        skipped = (f" {len(aggregate.skipped)} category(ies) produced no multilateral series "
                   f"({named}) and are absent from it; the reason is recorded against each.")
    return (
        f"A multilateral index was compiled per category and rolled up to a headline: "
        f"{multilateral_basis(res)}.{where} A multilateral method estimates every period of a "
        "window at once, so it is transitive within the window and has no path along which "
        "chain drift can accumulate; what it costs is a window that moves, which is what the "
        "extension rule negotiates. The weighted mean of several transitive category series is "
        "not itself transitive, which is the ordinary compromise of publishing a multilateral "
        f"elementary index inside a conventional aggregation structure.{skipped}")


def seasonal_note(res: dict[str, Any]) -> str:
    """One paragraph on the seasonal stage, naming the adjustment engine.

    Delegates to `engine.seasonal.adjustment_note`, which is where the rule
    that the engine is always named lives. Nothing in this module composes
    a sentence about seasonal adjustment itself, so there is no second place
    for that rule to be forgotten.
    """
    from ..engine.seasonal import adjustment_note

    return adjustment_note(res.get("seasonal"))


def outlier_note(res: dict[str, Any]) -> str:
    """One paragraph on what the screens found and what an analyst did."""
    from ..engine.outliers import exclusion_note

    scan = res.get("outlier_scan")
    report = res.get("outlier_exclusions")
    if scan is None:
        return ""
    screens = ", ".join(scan.methods_run)
    found = (f"{len(scan.queue):,} quote(s) of {scan.relatives:,} price relatives were flagged "
             f"by at least one of the {screens} screens ({scan.flag_rate:.2%}).")
    decided = exclusion_note(report)
    return f"{found} {decided}".strip() if decided else (
        f"{found} None has been reviewed yet, and an unreviewed flag excludes nothing: no "
        "quote leaves this index without an analyst's name and stated reason against it.")


def method_note(res: dict[str, Any]) -> str:
    """The audit trail. Written as prose because a reader checking the work
    needs to follow the reasoning, not decode a settings dump."""
    cfg = res["config"]
    clean, q = res["clean"], res["quality"]
    counts = q["flag_summary"]["count"]
    I = res["indices"]
    up = int(counts.get("scale_error_x100", 0))
    dn = int(counts.get("scale_error_div100", 0))
    mech = q["missing_mechanisms"]

    mech_txt = "No gaps were detected."
    if len(mech):
        parts = []
        for r in mech.itertuples():
            parts.append(f"{r.category} was classified as {r.mechanism} "
                         f"({r.gaps:,} observations)")
        mech_txt = ("Gaps were classified by mechanism before treatment: "
                    + "; ".join(parts) + ".")

    # A non-standard run says so before any of the numbers it produced: a
    # reader who stops after the first line must still come away knowing
    # the formula was not a recognised one.
    notice = non_standard_notice(cfg.index)
    banner = f"**{notice}**\n\n" if notice else ""

    imp_txt = f"The default imputation method was {_pretty(cfg.imputation.default_method)}."
    if cfg.imputation.by_category:
        imp_txt += (" Category overrides: "
                    + "; ".join(f"{k} treated by {_pretty(v)}"
                                for k, v in cfg.imputation.by_category.items()) + ".")

    return banner + f"""**Source.** {len(clean):,} observations covering {clean.item_id.nunique()} items \
across {clean.category.nunique()} categories, {clean.period.min():%B %Y} to \
{clean.period.max():%B %Y}.

**Missing values.** {int(counts.get('missing_code', 0)):,} observations carried a missing \
code and were recoded to unavailable before any calculation, rather than being treated as a \
price of nil. {mech_txt}

**Outlier detection.** Each observation was compared against a {cfg.quality.reference_window} \
period centred rolling median of its own item's series. A centred local median was used in \
preference to a whole period median because prices trend across a long collection, and a fixed \
reference would flag genuine later prices as faults. Observations whose deviation from that \
local level fell between {cfg.quality.scale_log10_low} and {cfg.quality.scale_log10_high} in \
log10 units were treated as unit of measurement errors. A band was used rather than a simple \
threshold because a unit fault has a known multiplier, whereas genuine volatility does not \
cluster at a fixed ratio.

**Treatment.** {up:,} observations were rescaled down and {dn:,} rescaled up. They were \
{'repaired rather than deleted' if cfg.quality.repair_scale_errors else 'excluded'}, because \
deletion would break the item continuity that a matched comparison depends on. After treatment, \
{len(q['residual_outliers'])} observations remained beyond {cfg.quality.residual_tolerance} in \
log10 units of their local level.

**Imputation.** {imp_txt}

**Aggregation.** A {_formula_label(cfg.index)} elementary index was used, matched model, \
{'chained period on period' if cfg.index.chained else 'fixed base'}, set to \
{cfg.index.base_value:.0f} at {resolve_index_reference_period(cfg.index, I.index):%B %Y}. \
Only items priced in both of the two periods \
being compared enter that comparison, so the entry or exit of an item does not register as price \
change.{quantity_note(res)} Periods with fewer than {cfg.index.min_matched_items} matched items hold the previous \
level. {'The all-items aggregate is an equally weighted geometric mean of the category indices. No expenditure weights were supplied, so it is indicative rather than authoritative.' if 'All items' in I.columns else ''}

**Quality adjustment.** {quality_adjustment_note(res)}
{_optional_section("Outlier review", outlier_note(res))}{_optional_section("Multilateral index", multilateral_note(res))}{_optional_section("Seasonality", seasonal_note(res))}
**Limitations.** {_limitations_note(res)} Seasonal treatment is \
limited to holding the level across an out-of-season gap. The tool reports what the collection \
shows; it does not establish why any movement occurred."""


def _optional_section(title: str, body: str) -> str:
    """A named paragraph, or nothing at all when the stage did not run.

    Nothing at all, rather than "not applicable": a method note listing
    every stage a run could have had, most of them empty, buries the ones
    it did have.
    """
    if not body:
        return ""
    return f"\n**{title}.** {body}\n"


def quality_adjustment_note(res: dict[str, Any]) -> str:
    """One paragraph on what the ledger did, or that there was none."""
    entries = res["config"].quality_adjustment.entries
    impact = res.get("quality_adjustment_impact")
    if not entries or impact is None:
        return ("No item replacement was valued: every item that left the sample simply "
                "stopped being compared, and every item that arrived started a series of "
                "its own, so no price gap between an old item and a new one entered the "
                "index. That is itself an implicit quality adjustment -- it assumes the "
                "gap was entirely quality -- and it is stated here so it can be questioned.")
    methods = pd.Series([e.method for e in entries]).value_counts()
    method_txt = ", ".join(f"{int(n)} by {_pretty(str(m))}" for m, n in methods.items())
    direction = "raised" if impact.adjustment_effect_points > 0 else "lowered"
    plural = "s were" if len(entries) != 1 else " was"
    return (f"{len(entries)} item replacement{plural} valued "
            f"and linked onto the departing item's series ({method_txt}); each valuation, its "
            f"justification and who approved it are in the ledger below. Valuing the "
            f"replacements rather than treating their price gaps as pure price change "
            f"{direction} the {impact.headline} index at {impact.final_period:%B %Y} by "
            f"{abs(impact.adjustment_effect_points):.2f} points "
            f"({impact.adjustment_effect_annual_pp:+.2f} percentage points of the annual rate, "
            f"measured {impact.annual_measure.replace('_', ' ')}). Linking them at all, against "
            f"the matched-model default of comparing nothing across a replacement, moved it by "
            f"{impact.linking_effect_points:+.2f} points.")


def _limitations_note(res: dict[str, Any]) -> str:
    if res["config"].quality_adjustment.entries:
        return ("Quality adjustment is applied only to the replacements listed in the ledger; "
                "any other item that left and was replaced is treated as unrelated to its "
                "successor, which implicitly attributes their whole price gap to quality.")
    return ("No quality adjustment is applied between a departing item and its replacement, "
            "so any change in specification is implicitly treated as quality, not price.")


def quality_adjustment_tables(res: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """The ledger and the impact scenarios as tables, or None when the
    run carries no adjustment."""
    from ..engine.quality_adjustment import ledger_frame

    impact = res.get("quality_adjustment_impact")
    entries = res["config"].quality_adjustment.entries
    if not entries or impact is None:
        return None
    ledger = ledger_frame(entries)[["period", "category", "old_item", "new_item", "method",
                                    "quality_ratio", "justification", "approved_by"]]
    scenarios = impact.scenarios.rename(index={
        "as_configured": "As compiled", "linked_unadjusted": "Linked, valued at ratio 1",
        "no_link": "Not linked (matched model)"}).round(3)
    return ledger, scenarios


# ----------------------------------------------------------------------
def with_index_column(frame: pd.DataFrame) -> pd.DataFrame:
    """The frame with its index promoted to a column, safely.

    `reset_index` raises when the index's name already exists as a column,
    which the outlier queue -- indexed by position, with its own `period`
    column -- hits. A positional index carries no information and is simply
    dropped; a named one becomes a column under a name nothing else is
    using.
    """
    if isinstance(frame.index, pd.RangeIndex):
        return frame.reset_index(drop=True)
    name = str(frame.index.name or "period")
    while name in frame.columns:
        name = f"{name}_index"
    return frame.rename_axis(name).reset_index()


CONTRIBUTIONS_TITLE = "Contributions to the change"


def contributions_summary(res: dict[str, Any], periods_per_year: int = 12
                          ) -> tuple[str, pd.DataFrame]:
    """What drove the headline: each category's contribution to its change,
    as (note, table), for the bulletin, the Word and Markdown reports and
    the deck.

    The comparison is the latest period on the same period a year earlier
    where the run is long enough, and first to last period otherwise. The
    level of the tree is stated -- the run's categories, directly beneath
    All items -- and the table ends with the sum of the contributions, the
    headline's own published change, and the gap between them. The gap is
    shown, never absorbed: it is zero to floating-point precision when the
    headline is the weighted arithmetic mean of the categories, and it is
    exactly what a reader needs to see when it is not (an analyst-defined
    aggregate formula, say).

    A run without expenditure weights gets a note and an empty table: its
    headline is an equally weighted geometric mean, which has no additive
    decomposition, and a release that silently omitted the section would
    look like one that had forgotten it.
    """
    from ..engine import decomposition as dc

    components = dc.components_from_run(res)
    columns = ["Weight", "Change, %", "Contribution, pp"]
    if components.weights is None:
        return (" ".join(components.notes) or "Contributions were not computed.",
                pd.DataFrame(columns=columns))
    levels = components.indices.dropna(how="all")
    if len(levels) < 2:
        return ("The run has a single period, so there is no change to decompose.",
                pd.DataFrame(columns=columns))
    end = pd.Timestamp(levels.index[-1])
    year_ago = end - pd.DateOffset(months=12 // periods_per_year * periods_per_year)
    start = year_ago if year_ago in levels.index else pd.Timestamp(levels.index[0])
    span = ("on the same period a year earlier" if start == year_ago else
            f"since {start:%B %Y}, the first period (the run is shorter than a year)")
    result = dc.tree_contributions(components.indices, components.weights,
                                   components.parent_of, start, end)
    top = result.children(result.root).sort_values("contribution_to_headline_pp",
                                                   ascending=False)
    headline = res["indices"]["All items"]
    published = (float(headline[end]) / float(headline[start]) - 1.0) * 100.0
    total = float(top["contribution_to_headline_pp"].sum())
    residual = total - published
    table = pd.DataFrame({
        "Weight": top["weight"].round(4),
        "Change, %": top["change_pct"].round(4),
        "Contribution, pp": top["contribution_to_headline_pp"].round(6),
    })
    table.index.name = "Category"
    footer = pd.DataFrame({
        "Weight": [float("nan")] * 3, "Change, %": [float("nan")] * 3,
        "Contribution, pp": [round(total, 6), round(published, 6), residual]},
        index=pd.Index(["Sum of contributions", "All items change, % (as published)",
                        "Residual, pp (sum minus published change)"], name="Category"))
    note = (f"Contributions to the change in All items in {end:%B %Y} {span}, in percentage "
            f"points, at level 1 of the classification tree: the run's "
            f"{len(top)} categories, directly beneath All items. They sum to {total:+.6f}; "
            f"the headline's published change is {published:+.6f}%, and the residual of "
            f"{residual:.1e} pp is shown in the table rather than absorbed into any category. "
            "One set of weights spans the comparison, so the decomposition is exact rather "
            "than an approximation across a re-weighting.")
    return note, pd.concat([table, footer])


def supplementary_tables(res: dict[str, Any]) -> list[tuple[str, str, pd.DataFrame]]:
    """The Phase 6 sections, as (title, note, table), in one list.

    Markdown, Word, the deck, the Excel pack and the bulletin all iterate
    this, so a stage that runs appears in every output and a stage that does
    not appears in none -- without any format deciding for itself. The
    alternative, each format assembling its own sections, is how a series
    ends up in the CSV and missing from the report that explains it.

    The seasonal entry is a single table holding the adjusted series and the
    unadjusted series in adjacent columns. Not two entries: two entries can
    be separated, and one of the two rules this phase enforces is that they
    never are.
    """
    out: list[tuple[str, str, pd.DataFrame]] = []

    aggregate = res.get("multilateral")
    if aggregate is not None:
        levels = aggregate.indices.round(2)
        out.append(("Multilateral index", multilateral_note(res), levels))
        if aggregate.skipped:
            out.append((
                "Categories without a multilateral series",
                "Named rather than dropped: a category missing from a weighted headline moves "
                "the headline.",
                pd.DataFrame({"reason": pd.Series(dict(aggregate.skipped))})))

    seasonal = res.get("seasonal")
    if seasonal is not None:
        if seasonal.adjustment is not None:
            out.append(("Seasonal adjustment", seasonal_note(res),
                        seasonal.adjustment.frame.round(4)))
        if seasonal.comparison is not None and len(seasonal.comparison.results) > 1:
            out.append((
                "Strictly seasonal item treatment",
                "Class confinement keeps each class's full weight in every period; weight "
                "update removes an absent item's weight and renormalises. The gap is the size "
                "of a judgement that would otherwise have been made silently.",
                seasonal.comparison.table.round(4)))
        if seasonal.rothwell is not None:
            out.append((
                "Rothwell index",
                f"Each period's available items priced against their average prices in base "
                f"year {seasonal.rothwell.base_year}. A movement here mixes price change with "
                "the changing composition of a seasonal basket, which is inherent to the form.",
                pd.DataFrame({"rothwell": seasonal.rothwell.index.round(3),
                              "items": seasonal.rothwell.items_by_period})))

    scan = res.get("outlier_scan")
    if scan is not None and len(scan.queue):
        # `round` on a frame holding a period column warns and does nothing
        # for it, so the numeric columns are rounded by name.
        queue = scan.queue.head(25).copy()
        for column in ("price", "previous_price", "ratio"):
            if column in queue.columns:
                queue[column] = queue[column].astype(float).round(4)
        out.append(("Outlier review queue", outlier_note(res), queue))
    report = res.get("outlier_exclusions")
    if report is not None and report.excluded:
        out.append((
            "Excluded quotes by category",
            "Reported as a share of the quotes they would have fed, in the units imputation "
            "is already reported in.",
            report.by_category.round(4)))
    return out


def provenance_markdown(stamp: ProvenanceStamp) -> str:
    """The stamp as a Markdown section: a readable table, then the JSON
    in a fenced block tagged with the stamp key so it can be read back
    mechanically (`reporting.readback.from_markdown`)."""
    lines = ["## Provenance", "", "| Field | Value |", "|:--|:--|"]
    for k, v in stamp.rows():
        cell = str(sanitize_cell(v)).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {k} | {cell} |")
    lines += ["", f"```{STAMP_KEY}", stamp.to_json(), "```", ""]
    return "\n".join(lines)


def build_markdown(res: dict[str, Any], nar: Narrative, label: str = "",
                   stamp: ProvenanceStamp | None = None) -> str:
    stamp = stamp or build_stamp(res, label)
    I = res["indices"]
    lines = [f"# {nar.headline}", "", f"*{label or res['config'].label}*", "",
             nar.subtitle, "",
             f"Produced {date.today():%d %B %Y} by PriceLab.", ""]
    notice = non_standard_notice(res["config"].index)
    if notice:
        lines += [f"> **{notice}**", ""]
    lines += ["## Summary", ""]
    for i, f in enumerate(nar.top(5), 1):
        lines.append(f"{i}. **{f.headline}** ({f.evidence})")
    lines.append("")

    for kind in KIND_ORDER:
        group = [f for f in nar.findings if f.kind == kind]
        if not group:
            continue
        lines += [f"## {KIND_TITLES[kind]}", ""]
        for f in group:
            lines += [f"### {f.headline}", "", f.detail, ""]
            if f.evidence:
                lines += [f"*Evidence: {f.evidence}*", ""]
            if f.action:
                lines += [f"**Action.** {f.action}", ""]

    from ..core.security import sanitize_dataframe

    lines += ["## Index levels", "",
              sanitize_dataframe(I.iloc[-1].round(2).rename(f"Index at {I.index[-1]:%b %Y}")
                                 .to_frame()).to_markdown(), ""]
    if "All items" in I.columns:
        note, contributions = contributions_summary(res)
        lines += [f"## {CONTRIBUTIONS_TITLE}", "", note, ""]
        if len(contributions):
            lines += [sanitize_dataframe(contributions).to_markdown(), ""]
    tables = quality_adjustment_tables(res)
    if tables is not None:
        ledger, scenarios = tables
        lines += ["## Quality adjustment", "", quality_adjustment_note(res), "",
                  "### Ledger", "", sanitize_dataframe(ledger).to_markdown(index=False), "",
                  "### Impact on the headline", "", sanitize_dataframe(scenarios).to_markdown(), ""]
    for title, note, table in supplementary_tables(res):
        lines += [f"## {title}", ""]
        if note:
            lines += [note, ""]
        lines += [sanitize_dataframe(table).to_markdown(), ""]
    lines += ["## Method note", "", method_note(res).replace("**", "**"), "",
              provenance_markdown(stamp)]
    return "\n".join(lines)


# ----------------------------------------------------------------------
def _style(doc: Any) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(8)
    normal.paragraph_format.line_spacing = 1.15
    for name, size, colour, bold in (("Heading 1", 18, INK, True),
                                     ("Heading 2", 13, PRIMARY, True),
                                     ("Heading 3", 11, INK, True)):
        st = doc.styles[name]
        st.font.name = "Cambria" if name == "Heading 1" else "Calibri"
        st.font.size = Pt(size)
        st.font.color.rgb = colour
        st.font.bold = bold


def _rich(p: Any, text: str) -> None:
    """Render **bold** segments without a markdown dependency."""
    for i, seg in enumerate(text.split("**")):
        if not seg:
            continue
        run = p.add_run(seg)
        run.bold = (i % 2 == 1)


def _table(doc: Any, df: pd.DataFrame, max_rows: int = 15) -> None:
    # A Word table is one paste away from a spreadsheet, so its cells and
    # headers go through the same sanitiser as every other tabular export.
    from ..core.security import sanitize_dataframe

    d = sanitize_dataframe(df.head(max_rows))
    t = doc.add_table(rows=1, cols=len(d.columns))
    t.style = "Light Grid Accent 1"
    for i, c in enumerate(d.columns):
        cell = t.rows[0].cells[i]
        header = str(c)
        cell.text = header if header.startswith("'") else header.replace("_", " ").title()
        for para in cell.paragraphs:
            for run in para.runs:
                run.bold = True
                run.font.size = Pt(9)
    for _, row in d.iterrows():
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = f"{v:,.2f}" if isinstance(v, float) else str(v)
            for para in cells[i].paragraphs:
                for run in para.runs:
                    run.font.size = Pt(9)
    doc.add_paragraph()


def build_docx(res: dict[str, Any], nar: Narrative, charts: dict[str, Any], label: str = "",
               stamp: ProvenanceStamp | None = None) -> bytes:
    from .charts import to_png

    stamp = stamp or build_stamp(res, label)

    doc = Document()
    _style(doc)

    doc.add_heading(nar.headline, level=1)
    p = doc.add_paragraph()
    r = p.add_run(label or res["config"].label)
    r.font.size = Pt(11)
    r.font.color.rgb = PRIMARY
    p = doc.add_paragraph()
    r = p.add_run(nar.subtitle + f"  ·  Produced {date.today():%d %B %Y} by PriceLab.")
    r.font.size = Pt(9)
    r.font.color.rgb = MUTED

    notice = non_standard_notice(res["config"].index)
    if notice:
        p = doc.add_paragraph()
        r = p.add_run(notice)
        r.bold = True
        r.font.size = Pt(10)
        r.font.color.rgb = ACCENT

    doc.add_heading("Summary", level=2)
    for f in nar.top(5):
        para = doc.add_paragraph(style="List Number")
        _rich(para, f"**{f.headline}** — {f.evidence}")

    for kind in KIND_ORDER:
        group = [f for f in nar.findings if f.kind == kind]
        if not group:
            continue
        doc.add_heading(KIND_TITLES[kind], level=2)
        for f in group:
            doc.add_heading(f.headline, level=3)
            doc.add_paragraph(f.detail)
            if f.evidence:
                para = doc.add_paragraph()
                run = para.add_run("Evidence: " + f.evidence)
                run.italic = True
                run.font.size = Pt(9.5)
                run.font.color.rgb = PRIMARY
            if f.chart and f.chart in charts:
                doc.add_picture(io.BytesIO(to_png(charts[f.chart])), width=Inches(6.0))
                charts = {k: v for k, v in charts.items() if k != f.chart}
            if f.table is not None and len(f.table):
                _table(doc, f.table.reset_index(drop=True))
            if f.action:
                para = doc.add_paragraph()
                _rich(para, f"**Action.** {f.action}")

    doc.add_heading("Index levels", level=2)
    I = res["indices"]
    _table(doc, I.iloc[-1].round(2).rename("Index").to_frame()
           .reset_index(names="Category"), max_rows=30)

    if "All items" in I.columns:
        note, contributions = contributions_summary(res)
        doc.add_heading(CONTRIBUTIONS_TITLE, level=2)
        doc.add_paragraph(note)
        if len(contributions):
            _table(doc, contributions.reset_index(), max_rows=40)

    tables = quality_adjustment_tables(res)
    if tables is not None:
        ledger, scenarios = tables
        doc.add_heading("Quality adjustment", level=2)
        doc.add_paragraph(quality_adjustment_note(res))
        doc.add_heading("Ledger", level=3)
        _table(doc, ledger, max_rows=30)
        doc.add_heading("Impact on the headline", level=3)
        _table(doc, scenarios.reset_index(names="Scenario"))
        if "quality_adjustment" in charts:
            doc.add_picture(io.BytesIO(to_png(charts["quality_adjustment"])), width=Inches(6.0))

    for title, note, table in supplementary_tables(res):
        doc.add_heading(title, level=2)
        if note:
            _rich(doc.add_paragraph(), note)
        if title == "Seasonal adjustment" and "seasonal_adjustment" in charts:
            doc.add_picture(io.BytesIO(to_png(charts["seasonal_adjustment"])), width=Inches(6.0))
        _table(doc, with_index_column(table), max_rows=30)

    doc.add_heading("Method note", level=2)
    for block in method_note(res).split("\n\n"):
        _rich(doc.add_paragraph(), block.strip())

    doc.add_heading("Provenance", level=2)
    _table(doc, pd.DataFrame(stamp.rows(), columns=["Field", "Value"]), max_rows=40)
    # Machine-readable copy as its own paragraph, tagged with the stamp
    # key so `reporting.readback.from_docx` finds it without parsing the
    # table; the document properties cap a field at 255 characters, so
    # they carry only the run identifier.
    para = doc.add_paragraph()
    run = para.add_run(f"{STAMP_KEY} {stamp.to_json()}")
    run.font.name = "Courier New"
    run.font.size = Pt(6)
    run.font.color.rgb = MUTED
    doc.core_properties.subject = f"{STAMP_KEY}:{stamp.run_id}"[:255]

    # Same rule as the deck: a paragraph whose first run begins with a
    # formula leader is user data at the start of a line, one paste away
    # from a spreadsheet cell.
    for para in doc.paragraphs:
        if para.runs:
            para.runs[0].text = str(sanitize_cell(para.runs[0].text))

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
