"""The PDF statistical bulletin, following official release conventions:
headline figure, key points, charts, tables, methodology note, revision
statement, and a contact and release-date block.

The headline is read from the run registry, not recomputed. A bulletin is
a release of a *registered* number: the figure printed at the top is
`IndexRunORM.headline_value` for the run being released, which
`core.registry.register_run` recorded when the run was registered. If the
live result handed in disagrees with the registry -- the code changed, or
somebody is trying to release a different run's outputs under a
registered run's identifier -- the bulletin refuses rather than printing
either number.

Every string that reaches a Paragraph is XML-escaped (reportlab's
paragraphs parse a mini-markup) and sanitised like every other export.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from xml.sax.saxutils import escape

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..core.config import get_settings
from ..core.provenance import STAMP_KEY, ProvenanceStamp
from ..core.security import sanitize_cell
from ..engine.insights import Narrative
from .exports import publication_table, wide_publication
from .report import method_note

INK, PRIMARY, ACCENT, MUTED = "#12263A", "#1E4D6B", "#D9822B", "#8A9BA8"
HEADLINE_TOLERANCE = 1e-6


class BulletinError(ValueError):
    """The bulletin cannot be produced for this run as asked."""


def _t(text: Any) -> str:
    """Escape and sanitise text for a Paragraph."""
    return escape(str(sanitize_cell("" if text is None else text)))


def _bold_markup(block: str) -> str:
    """A method-note block, escaped, with its leading `**Label.**` as a bold
    run. Only that leading marker is honoured; any other asterisks are
    text."""
    if block.startswith("**") and "**" in block[2:]:
        end = block.index("**", 2)
        return f"<b>{_t(block[2:end])}</b>{_t(block[end + 2:])}"
    return _t(block)


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "org": ParagraphStyle("org", parent=base["Normal"], fontSize=9, textColor=MUTED),
        "title": ParagraphStyle("title", parent=base["Title"], fontSize=20, alignment=TA_LEFT,
                                textColor=INK, spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"], fontSize=11,
                                   textColor=PRIMARY, spaceAfter=10),
        "headline": ParagraphStyle("headline", parent=base["Normal"], fontSize=30,
                                   leading=34, textColor=ACCENT),
        "headline_label": ParagraphStyle("hl", parent=base["Normal"], fontSize=10,
                                         textColor=INK, leading=13),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=13, textColor=PRIMARY,
                             spaceBefore=12, spaceAfter=6, keepWithNext=1),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9.5, leading=13,
                               textColor=INK),
        "small": ParagraphStyle("small", parent=base["Normal"], fontSize=7.5, leading=9.5,
                                textColor=MUTED),
        "mono": ParagraphStyle("mono", parent=base["Code"], fontSize=5.5, leading=6.5,
                               textColor=MUTED),
    }


FRAME_WIDTH = 174 * mm     # A4 less the 18 mm margins either side


def _table(rows: Sequence[Sequence[Any]], col_widths: Sequence[float] | None = None,
           header: bool = True) -> Table:
    """A table that stays inside the frame: columns share the frame width
    when none are given, cells wrap, and the font shrinks for wide tables
    (thirteen columns of index levels overran the margin when rendered)."""
    n_cols = max(len(r) for r in rows)
    if col_widths is None:
        col_widths = [FRAME_WIDTH / n_cols] * n_cols
    font_size = 8 if n_cols <= 8 else 6.5
    cell_style = ParagraphStyle("cell", fontName="Helvetica", fontSize=font_size,
                                leading=font_size + 2, textColor=INK)
    head_style = ParagraphStyle("head", parent=cell_style, fontName="Helvetica-Bold",
                                fontSize=font_size if n_cols <= 10 else font_size - 0.7)
    data = [[Paragraph(_t(c), head_style if (header and i == 0) else cell_style) for c in r]
            for i, r in enumerate(rows)]
    t = Table(data, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(INK)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor(PRIMARY)),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#E9EEF2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 6 if n_cols <= 8 else 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6 if n_cols <= 8 else 2),
    ]
    if header:
        style += [("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                  ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EEF2"))]
    t.setStyle(TableStyle(style))
    return t


def registry_headline(run: Any) -> dict[str, Any]:
    """The headline as the registry holds it for this run."""
    if run.headline_value is None:
        raise BulletinError(
            f"run {run.run_id} has no headline recorded in the registry (it was registered "
            "before headline recording existed); re-register it before releasing a bulletin")
    return {"series": run.headline_series, "period": run.headline_period,
            "value": float(run.headline_value), "reference_period": run.headline_reference_period}


def revision_statement(run: Any) -> str:
    if run.vintage == 1 and not run.supersedes_run_id:
        return (f"First release of run {run.run_id}. No revision has been made to these "
                "figures. A correction, if one is ever needed, will be published as a new "
                "vintage with a stated reason; this vintage will remain on record unchanged.")
    return (f"Vintage {run.vintage} of this release, superseding run {run.supersedes_run_id}. "
            f"Reason for revision: {run.correction_reason or 'not stated'}. The superseded "
            "vintage remains on record and reproducible.")


def build_bulletin(
    res: Mapping[str, Any],
    nar: Narrative,
    charts: Mapping[str, Any],
    stamp: ProvenanceStamp,
    *,
    run: Any,
    release_date: date | None = None,
) -> bytes:
    """Render the bulletin for registered `run`. `stamp` should have been
    built with the same `run`, so the identifiers agree."""
    from .charts import to_png

    settings = get_settings()
    head = registry_headline(run)
    live = float(res["indices"][head["series"]].iloc[-1]) if head["series"] in res["indices"].columns \
        else float("nan")
    if abs(live - head["value"]) > HEADLINE_TOLERANCE:
        raise BulletinError(
            f"the registry holds {head['value']:.6f} for run {run.run_id}'s headline but the "
            f"result supplied computes {live:.6f}; a bulletin releases the registered number "
            "and will not print either while they disagree. Reproduce the run from the "
            "registry, or register a corrected vintage.")
    if stamp.run_id != run.run_id:
        raise BulletinError("the provenance stamp was built for a different run")

    st = _styles()
    release = release_date or date.today()
    story: list[Any] = []

    story.append(Paragraph(_t(settings.release_organisation) + " &nbsp;|&nbsp; Statistical bulletin",
                           st["org"]))
    story.append(Paragraph(_t(stamp.label), st["title"]))
    story.append(Paragraph(f"Released {release:%d %B %Y} &nbsp;·&nbsp; run {_t(run.run_id)}, "
                           f"vintage {run.vintage}" + (" (approved)" if run.approved else
                                                      " (not yet approved)"), st["subtitle"]))

    period = pd.Timestamp(head["period"])
    ref = pd.Timestamp(head["reference_period"])
    yoy = res["inflation"][head["series"]].iloc[-1] if head["series"] in res["inflation"].columns \
        else float("nan")
    story.append(KeepTogether([
        Paragraph(f"{head['value']:.1f}", st["headline"]),
        Paragraph(f"{_t(head['series'])} index, {period:%B %Y} ({ref:%B %Y} = 100)"
                  + (f". {yoy:+.1f}% on a year earlier." if pd.notna(yoy) else "")
                  + " Figure as registered; not recomputed for this bulletin.",
                  st["headline_label"]),
    ]))
    if stamp.non_standard_formula:
        story.append(Paragraph("<b>NON-STANDARD INDEX:</b> compiled on an analyst-defined formula "
                               f"({_t(stamp.non_standard_expression)}), not comparable with an "
                               "index on a standard formula.", st["body"]))

    story.append(Paragraph("Key points", st["h2"]))
    for f in nar.top(5):
        story.append(Paragraph(f"• <b>{_t(f.headline)}</b> {_t(f.evidence)}", st["body"]))

    story.append(Paragraph("Charts", st["h2"]))
    for key in ("index", "inflation", "quality_adjustment"):
        if key in charts:
            story.append(Image(io.BytesIO(to_png(charts[key], dpi=150)), width=170 * mm,
                               height=85 * mm, kind="proportional"))
            story.append(Spacer(1, 4))

    table = publication_table(res)
    wide = wide_publication(table)
    tail = wide.tail(13)
    headers = ["Period", *[str(c) for c in tail.columns]]
    # Monthly periods print as year-month; a full date wraps in a narrow
    # column and says nothing more.
    body = [[pd.Timestamp(idx).strftime("%Y-%m"),
             *[("suppressed" if v == "suppressed" else f"{float(v):.1f}" if v else "")
               for v in row]] for idx, row in zip(tail.index, tail.to_numpy(), strict=True)]
    n_sup = int(table["suppressed"].sum())
    shown_sup = int((tail == "suppressed").to_numpy().sum())
    story.append(KeepTogether([
        Paragraph("Tables", st["h2"]),
        _table([headers, *body]),
        Paragraph(
            f"{shown_sup} cell{'s' if shown_sup != 1 else ''} in this table "
            f"({n_sup} in the full series) read \"suppressed\" under this release's "
            f"disclosure control: {_t(stamp.suppression_rules.get('method'))} (minimum "
            f"{stamp.suppression_rules.get('min_count')} matched quotes).", st["small"]),
    ]))

    # Any series in that table not produced by the run's own elementary
    # formula is named here, beside the table, with what produced it. The
    # methodology note says the same thing at greater length further down,
    # and this is the short form a reader meets at the number itself: a
    # multilateral or seasonally adjusted level that a reader takes for the
    # headline, because nothing next to it said otherwise, is the failure
    # this paragraph exists to prevent.
    from .exports import series_basis

    basis = {name: phrase for name, phrase in series_basis(res).items()
             if name in set(wide.columns)}
    # The unadjusted counterpart is published in the same table by
    # construction (`exports.additional_series`); saying so here is what
    # tells a reader of the PDF to look for it.
    if any(name.startswith("seasonally adjusted: ") for name in basis):
        basis["unadjusted series"] = (
            "published in the same table, under its own name, so what the adjustment removed "
            "can be seen rather than taken on trust")
    if basis:
        story.append(KeepTogether([
            Paragraph("How each series was produced", st["h2"]),
            *[Paragraph(f"<b>{_t(name)}</b> — {_t(phrase)}", st["small"])
              for name, phrase in basis.items()]]))

    story.append(Paragraph("Methodology note", st["h2"]))
    # The method note already carries the quality-adjustment paragraph;
    # its **bold** markers become bold runs rather than being stripped.
    for block in method_note(dict(res)).split("\n\n"):
        block = block.strip()
        if block:
            story.append(Paragraph(_bold_markup(block), st["body"]))

    story.append(Paragraph("Revision statement", st["h2"]))
    story.append(Paragraph(_t(revision_statement(run)), st["body"]))

    story.append(KeepTogether([
        Paragraph("Contact and release", st["h2"]),
        _table([
            ["Organisation", settings.release_organisation],
            ["Contact", settings.release_contact_name],
            ["Email", settings.release_contact_email],
            ["Telephone", settings.release_contact_phone or "not given"],
            ["Release date", f"{release:%d %B %Y}"],
            ["Next release", "as announced by the organisation"],
        ], col_widths=[40 * mm, 134 * mm], header=False),
    ]))

    story.append(PageBreak())
    story.append(Paragraph("Provenance", st["h2"]))
    story.append(_table([["Field", "Value"], *[[k, str(v)] for k, v in stamp.rows()]],
                        col_widths=[45 * mm, 129 * mm]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"{STAMP_KEY} {_t(stamp.to_json())}", st["mono"]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
        bottomMargin=16 * mm, title=str(sanitize_cell(stamp.label)),
        author=settings.release_organisation, subject=f"{STAMP_KEY}:{stamp.run_id}",
        keywords=stamp.to_json(), creator="PriceLab")
    doc.build(story)
    return buf.getvalue()
