"""Written report generation.

The deck is for presenting; this is for the record. It carries the full
narrative, the method note and the tables, so a reader who was not in the room
can follow what was done and check it.
"""

import io
from datetime import date

import pandas as pd
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

from .insights import Narrative
from .deck import _pretty

INK = RGBColor(0x12, 0x26, 0x3A)
PRIMARY = RGBColor(0x1E, 0x4D, 0x6B)
MUTED = RGBColor(0x6B, 0x7C, 0x8C)

KIND_ORDER = ["trend", "quality", "structure", "seasonal", "method"]
KIND_TITLES = {
    "trend": "Price movement",
    "quality": "Data quality",
    "structure": "Sample structure",
    "seasonal": "Seasonality",
    "method": "Method and sensitivity",
}


# ----------------------------------------------------------------------
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

    imp_txt = f"The default imputation method was {_pretty(cfg.imputation.default_method)}."
    if cfg.imputation.by_category:
        imp_txt += (" Category overrides: "
                    + "; ".join(f"{k} treated by {_pretty(v)}"
                                for k, v in cfg.imputation.by_category.items()) + ".")

    return f"""**Source.** {len(clean):,} observations covering {clean.item_id.nunique()} items \
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

**Aggregation.** A {cfg.index.formula.title()} elementary index was used, matched model, \
{'chained period on period' if cfg.index.chained else 'fixed base'}, set to \
{cfg.index.base_value:.0f} at {I.index[0]:%B %Y}. Only items priced in both of the two periods \
being compared enter that comparison, so the entry or exit of an item does not register as price \
change. Periods with fewer than {cfg.index.min_matched_items} matched items hold the previous \
level. {'The all-items aggregate is an equally weighted geometric mean of the category indices. No expenditure weights were supplied, so it is indicative rather than authoritative.' if 'All items' in I.columns else ''}

**Limitations.** No quality adjustment is applied between a departing item and its replacement, \
so any change in specification is implicitly treated as price change. Seasonal treatment is \
limited to holding the level across an out-of-season gap. The tool reports what the collection \
shows; it does not establish why any movement occurred."""


# ----------------------------------------------------------------------
def build_markdown(res: dict, nar: Narrative, label: str = "") -> str:
    I = res["indices"]
    lines = [f"# {nar.headline}", "", f"*{label or res['config'].label}*", "",
             nar.subtitle, "",
             f"Produced {date.today():%d %B %Y} by PriceLab.", "",
             "## Summary", ""]
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
              .to_frame().to_markdown(), "",
              "## Method note", "", method_note(res).replace("**", "**")]
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
