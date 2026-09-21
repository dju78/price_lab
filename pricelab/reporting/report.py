"""Written report generation.

The deck is for presenting; this is for the record. It carries the full
narrative, the method note and the tables, so a reader who was not in the room
can follow what was done and check it.
"""

import io
from datetime import date

import pandas as pd
from docx import Document
from docx.shared import Inches, Pt, RGBColor

from ..core.config import IndexConfig
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
    return str(index_cfg.formula).title()


def method_note(res: dict) -> str:
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
change. Periods with fewer than {cfg.index.min_matched_items} matched items hold the previous \
level. {'The all-items aggregate is an equally weighted geometric mean of the category indices. No expenditure weights were supplied, so it is indicative rather than authoritative.' if 'All items' in I.columns else ''}

**Quality adjustment.** {quality_adjustment_note(res)}

**Limitations.** {_limitations_note(res)} Seasonal treatment is \
limited to holding the level across an out-of-season gap. The tool reports what the collection \
shows; it does not establish why any movement occurred."""


def quality_adjustment_note(res: dict) -> str:
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
    method_txt = ", ".join(f"{int(n)} by {_pretty(m)}" for m, n in methods.items())
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


def _limitations_note(res: dict) -> str:
    if res["config"].quality_adjustment.entries:
        return ("Quality adjustment is applied only to the replacements listed in the ledger; "
                "any other item that left and was replaced is treated as unrelated to its "
                "successor, which implicitly attributes their whole price gap to quality.")
    return ("No quality adjustment is applied between a departing item and its replacement, "
            "so any change in specification is implicitly treated as quality, not price.")


def quality_adjustment_tables(res: dict) -> tuple[pd.DataFrame, pd.DataFrame] | None:
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
def build_markdown(res: dict, nar: Narrative, label: str = "") -> str:
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

    lines += ["## Index levels", "",
              I.iloc[-1].round(2).rename(f"Index at {I.index[-1]:%b %Y}")
              .to_frame().to_markdown(), ""]
    tables = quality_adjustment_tables(res)
    if tables is not None:
        ledger, scenarios = tables
        lines += ["## Quality adjustment", "", quality_adjustment_note(res), "",
                  "### Ledger", "", ledger.to_markdown(index=False), "",
                  "### Impact on the headline", "", scenarios.to_markdown(), ""]
    lines += ["## Method note", "", method_note(res).replace("**", "**")]
    return "\n".join(lines)


# ----------------------------------------------------------------------
def _style(doc):
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


def _rich(p, text):
    """Render **bold** segments without a markdown dependency."""
    for i, seg in enumerate(text.split("**")):
        if not seg:
            continue
        run = p.add_run(seg)
        run.bold = (i % 2 == 1)


def _table(doc, df: pd.DataFrame, max_rows=15):
    d = df.head(max_rows)
    t = doc.add_table(rows=1, cols=len(d.columns))
    t.style = "Light Grid Accent 1"
    for i, c in enumerate(d.columns):
        cell = t.rows[0].cells[i]
        cell.text = str(c).replace("_", " ").title()
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


def build_docx(res: dict, nar: Narrative, charts: dict, label: str = "") -> bytes:
    from .charts import to_png

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

    doc.add_heading("Method note", level=2)
    for block in method_note(res).split("\n\n"):
        _rich(doc.add_paragraph(), block.strip())

    doc.add_heading("Reproducibility", level=2)
    para = doc.add_paragraph()
    run = para.add_run(res["config"].to_json())
    run.font.name = "Courier New"
    run.font.size = Pt(7.5)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
