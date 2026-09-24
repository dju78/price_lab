"""Automatic slide deck generation.

The deck is built from the narrative, not from a fixed template, so it adapts
to whatever the data turned out to contain: a collection with no seasonal
categories gets no seasonality slide, and one with a collection failure gets a
slide about it.

python-pptx is used rather than a JavaScript generator so the whole application
is a single Python deployment with no Node runtime.
"""

import io
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

# The class, for annotations: `pptx.Presentation` is the factory function
# that returns one, and mypy will not accept a function as a type.
from pptx.presentation import Presentation as PresentationType
from pptx.util import Inches, Pt

from ..core.provenance import STAMP_KEY, ProvenanceStamp, build_stamp
from ..core.security import sanitize_cell
from ..engine.custom import non_standard_notice
from ..engine.index import annualised_rate, resolve_index_reference_period, years_span
from ..engine.insights import Finding, Narrative

# Palette, matching charts.py
INK = RGBColor(0x12, 0x26, 0x3A)
PRIMARY = RGBColor(0x1E, 0x4D, 0x6B)
ACCENT = RGBColor(0xD9, 0x82, 0x2B)
MUTED = RGBColor(0x8A, 0x9B, 0xA8)
LIGHT = RGBColor(0xE9, 0xEE, 0xF2)
PAPER = RGBColor(0xFF, 0xFF, 0xFF)
ON_DARK = RGBColor(0xF4, 0xF7, 0xF9)

HEAD_FONT = "Cambria"
BODY_FONT = "Calibri"

W, H = 13.333, 7.5

METHOD_NAMES = {"none": "no imputation", "class_mean": "class mean",
                "carry_forward": "carry forward", "seasonal_hold": "seasonal hold"}


def _pretty(name: str) -> str:
    return METHOD_NAMES.get(name, name.replace("_", " "))

M = 0.7                                  # margin


def _blank(prs: PresentationType) -> Any:
    return prs.slides.add_slide(prs.slide_layouts[6])


def _bg(slide: Any, prs: PresentationType, colour: RGBColor) -> Any:
    shp = slide.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
    shp.fill.solid()
    shp.fill.fore_color.rgb = colour
    shp.line.fill.background()
    shp.shadow.inherit = False
    slide.shapes._spTree.remove(shp._element)
    slide.shapes._spTree.insert(2, shp._element)
    return shp


def _text(slide: Any, x: float, y: float, w: float, h: float,
          runs: Sequence[Mapping[str, Any]], align: Any = PP_ALIGN.LEFT,
          anchor: Any = MSO_ANCHOR.TOP, space_after: float = 0) -> Any:
    """runs: list of dicts with text, size, bold, colour, font, italic."""
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, r in enumerate(runs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(r.get("space_after", space_after))
        if r.get("bullet"):
            p.level = 0
        run = p.add_run()
        run.text = r["text"]
        f = run.font
        f.name = r.get("font", BODY_FONT)
        f.size = Pt(r.get("size", 14))
        f.bold = r.get("bold", False)
        f.italic = r.get("italic", False)
        f.color.rgb = r.get("colour", INK)
        if r.get("line_spacing"):
            p.line_spacing = r["line_spacing"]
    return box


def _sentence(text: str) -> str:
    """Text shown as a line of its own on a slide starts with a capital: a
    finding's evidence ("index 135.6 at Dec 2025") is written to follow a
    label in other formats, and on a slide it stands alone."""
    return text[:1].upper() + text[1:] if text[:1].islower() else text


def _card(slide: Any, x: float, y: float, w: float, h: float,
          fill: RGBColor = LIGHT) -> Any:
    shp = slide.shapes.add_shape(5, Inches(x), Inches(y), Inches(w), Inches(h))  # rounded rect
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    shp.line.fill.background()
    shp.shadow.inherit = False
    try:
        shp.adjustments[0] = 0.06
    except Exception:
        pass
    return shp


def _dot(slide: Any, x: float, y: float, d: float, colour: RGBColor = ACCENT) -> Any:
    shp = slide.shapes.add_shape(9, Inches(x), Inches(y), Inches(d), Inches(d))  # oval
    shp.fill.solid()
    shp.fill.fore_color.rgb = colour
    shp.line.fill.background()
    shp.shadow.inherit = False
    return shp


def _image(slide: Any, png: bytes, x: float, y: float, w: float) -> Any:
    return slide.shapes.add_picture(io.BytesIO(png), Inches(x), Inches(y), width=Inches(w))


# ----------------------------------------------------------------------
# Slides
# ----------------------------------------------------------------------
def slide_title(prs: PresentationType, nar: Narrative, label: str,
                non_standard: str = "") -> Any:
    s = _blank(prs)
    _bg(s, prs, INK)
    _dot(s, M, 1.44, 0.18, ACCENT)
    _text(s, M + 0.34, 1.36, 7, 0.35,
          [{"text": "PRICE COLLECTION ANALYSIS", "size": 12, "bold": True,
            "colour": ACCENT, "font": BODY_FONT}])
    _text(s, M, 2.05, W - 2 * M - 0.8, 2.4,
          [{"text": nar.headline, "size": 40, "bold": True, "colour": ON_DARK,
            "font": HEAD_FONT, "line_spacing": 1.08}])
    _text(s, M, 4.75, W - 2 * M - 0.8, 0.45,
          [{"text": label, "size": 15, "colour": ON_DARK}])
    _text(s, M, 5.25, W - 2 * M - 0.8, 0.5,
          [{"text": nar.subtitle, "size": 12, "colour": MUTED}])
    # A non-standard run is flagged on the title slide, in the accent
    # colour, not on a methodology slide at the back. This is the slide
    # that gets screenshotted into an email, and a caveat only a careful
    # reader reaches is a caveat that does not travel with the number.
    if non_standard:
        _text(s, M, 5.95, W - 2 * M - 0.8, 0.5,
              [{"text": non_standard, "size": 11, "bold": True, "colour": ACCENT}])
    _text(s, M, 6.6, 9, 0.4,
          [{"text": "Generated by PriceLab. Every figure is reproducible from the "
                    "exported configuration.", "size": 10, "colour": MUTED,
            "italic": True}])
    notes = ("Open on the headline number, then say what the collection is and over what "
             "period. Do not read the subtitle aloud, it is there for the reader.")
    if non_standard:
        notes += ("\n\nSay explicitly that this run used an analyst-defined formula and is "
                  "not comparable with a published index.")
    s.notes_slide.notes_text_frame.text = notes
    return s


def slide_contents(prs: PresentationType, nar: Narrative, n: int = 4) -> Any:
    s = _blank(prs)
    _text(s, M, 0.55, 10, 0.7,
          [{"text": "What the data shows", "size": 34, "bold": True,
            "font": HEAD_FONT}])
    top = nar.top(n)
    y, gap, card_h = 1.55, 0.22, 1.24
    for i, f in enumerate(top):
        _card(s, M, y, W - 2 * M, card_h, LIGHT)
        _dot(s, M + 0.38, y + card_h / 2 - 0.16, 0.32, ACCENT)
        _text(s, M + 0.38, y + card_h / 2 - 0.15, 0.32, 0.3,
              [{"text": str(i + 1), "size": 13, "bold": True, "colour": PAPER}],
              align=PP_ALIGN.CENTER)
        _text(s, M + 1.0, y + 0.24, W - 2 * M - 1.6, 0.5,
              [{"text": f.headline, "size": 16, "bold": True, "colour": INK}])
        _text(s, M + 1.0, y + 0.72, W - 2 * M - 1.6, 0.4,
              [{"text": _sentence(f.evidence), "size": 11, "colour": PRIMARY, "italic": True}])
        y += card_h + gap
    s.notes_slide.notes_text_frame.text = (
        "Four findings, ranked by materiality. Say each headline once and move on; the "
        "detail comes on the slides that follow.")
    return s


def slide_finding(prs: PresentationType, f: Finding, chart_png: bytes | None = None,
                  eyebrow: str = "") -> Any:
    """One finding, chart on the right where one exists."""
    s = _blank(prs)
    if eyebrow:
        _dot(s, M, 0.63, 0.16, ACCENT)
        _text(s, M + 0.3, 0.55, 6, 0.35,
              [{"text": eyebrow.upper(), "size": 11, "bold": True, "colour": ACCENT}])
    # Every finding uses the same two columns: the argument on the left, and
    # on the right the chart where the finding has one, or else a panel
    # with its evidence and what to do about it -- so a chartless finding
    # reads as part of the same deck rather than as a wall of text.
    text_w = 4.9
    _text(s, M, 1.05, text_w, 1.5,
          [{"text": f.headline, "size": 27, "bold": True, "font": HEAD_FONT,
            "line_spacing": 1.08}])
    _text(s, M, 2.75, text_w, 3.0,
          [{"text": f.detail, "size": 13, "colour": INK, "line_spacing": 1.28}])
    if f.evidence and chart_png:
        _card(s, M, 5.75, text_w, 0.82, LIGHT)
        _text(s, M + 0.28, 5.95, text_w - 0.56, 0.5,
              [{"text": _sentence(f.evidence), "size": 12, "bold": True, "colour": PRIMARY}])
    if not chart_png:
        _finding_panel(s, f, M + text_w + 0.5)
    if chart_png:
        cx = M + text_w + 0.5
        cw = W - M - cx
        # Centre the chart on the slide's vertical axis. Chart aspect ratios vary
        # by type, so the height is read from the image rather than assumed.
        try:
            from PIL import Image
            iw, ih = Image.open(io.BytesIO(chart_png)).size
            ch = cw * ih / iw
        except Exception:
            ch = cw * 0.5
        _image(s, chart_png, cx, max(1.1, (H - ch) / 2), cw)
    notes = f.detail
    if f.action:
        notes += "\n\nRecommended action: " + f.action
    s.notes_slide.notes_text_frame.text = notes
    return s


def _finding_panel(s: Any, f: Finding, x: float) -> None:
    """The right-hand column of a finding with no chart: its evidence as the
    figure a chart would have shown, and the recommended action."""
    w = W - M - x
    _card(s, x, 1.1, w, 5.45, LIGHT)
    y = 1.45
    if f.evidence:
        _text(s, x + 0.35, y, w - 0.7, 0.3,
              [{"text": "THE EVIDENCE", "size": 10, "bold": True, "colour": ACCENT}])
        _text(s, x + 0.35, y + 0.4, w - 0.7, 1.9,
              [{"text": _sentence(f.evidence), "size": 20, "bold": True, "colour": PRIMARY,
                "font": HEAD_FONT, "line_spacing": 1.12}])
        y += 2.55
    if f.action:
        _text(s, x + 0.35, y, w - 0.7, 0.3,
              [{"text": "WHAT TO DO", "size": 10, "bold": True, "colour": ACCENT}])
        _text(s, x + 0.35, y + 0.4, w - 0.7, 6.3 - y,
              [{"text": f.action, "size": 13, "colour": INK, "line_spacing": 1.25}])


def slide_stat_row(prs: PresentationType, title: str, stats: Sequence[Mapping[str, Any]],
                   caption: str = "") -> Any:
    """Large number callouts. stats: [{value, label}]."""
    s = _blank(prs)
    _text(s, M, 0.55, 11, 0.8,
          [{"text": title, "size": 32, "bold": True, "font": HEAD_FONT}])
    n = len(stats)
    gap = 0.3
    card_w = (W - 2 * M - gap * (n - 1)) / n
    for i, st in enumerate(stats):
        x = M + i * (card_w + gap)
        _card(s, x, 2.0, card_w, 2.5, LIGHT)
        _text(s, x + 0.3, 2.35, card_w - 0.6, 1.1,
              [{"text": st["value"], "size": 46, "bold": True,
                "colour": ACCENT if st.get("accent") else PRIMARY, "font": HEAD_FONT}])
        _text(s, x + 0.3, 3.5, card_w - 0.6, 0.85,
              [{"text": st["label"], "size": 12, "colour": INK, "line_spacing": 1.2}])
    if caption:
        _text(s, M, 5.0, W - 2 * M, 1.4,
              [{"text": caption, "size": 13, "colour": INK, "line_spacing": 1.3}])
    s.notes_slide.notes_text_frame.text = (
        "The headline numbers, gathered in one place before the detail. " +
        (caption if caption else "Each figure is unpacked on the slides that follow."))
    return s


def slide_actions(prs: PresentationType, nar: Narrative) -> Any:
    s = _blank(prs)
    _text(s, M, 0.55, 11, 0.8,
          [{"text": "What to do about it", "size": 34, "bold": True,
            "font": HEAD_FONT}])
    acts = [f for f in nar.top(12) if f.action][:4]
    y = 1.75
    for f in acts:
        _dot(s, M, y + 0.09, 0.16, ACCENT)
        _text(s, M + 0.34, y, W - 2 * M - 0.4, 0.4,
              [{"text": f.headline, "size": 14, "bold": True, "colour": INK}])
        _text(s, M + 0.34, y + 0.38, W - 2 * M - 0.6, 0.8,
              [{"text": f.action, "size": 12.5, "colour": PRIMARY, "line_spacing": 1.25}])
        y += 1.28
    s.notes_slide.notes_text_frame.text = (
        "Close on actions rather than findings. Each one names something the reader "
        "can change.")
    return s


def slide_method(prs: PresentationType, res: dict[str, Any], nar: Narrative) -> Any:
    s = _blank(prs)
    _bg(s, prs, INK)
    cfg = res["config"]
    n_repaired = res["quality"]["scale_errors_repaired"]
    n_detected = res["quality"]["scale_errors_detected"]
    scale_desc = (f"{n_repaired} scale errors repaired by rescaling rather than deleted"
                 if n_repaired == n_detected else
                 f"{n_detected} scale errors found, {n_repaired} repaired by rescaling and "
                 f"{n_detected - n_repaired} dropped rather than repaired, as configured")
    _text(s, M, 0.6, 11, 0.8,
          [{"text": "How this was produced", "size": 32, "bold": True,
            "colour": ON_DARK, "font": HEAD_FONT}])

    left = [
        ("Cleaning",
         f"Missing sentinels recoded before calculation. Outliers judged against a "
         f"{cfg.quality.reference_window} period centred rolling median of each item's own "
         f"series, so a trending price is not mistaken for a fault. "
         f"{scale_desc}; "
         f"{len(res['quality']['residual_outliers'])} unexplained residuals remain."),
        ("Imputation",
         f"Default {_pretty(cfg.imputation.default_method)}. " +
         ("; ".join(f"{k}: {_pretty(v)}" for k, v in cfg.imputation.by_category.items())
          or "No category overrides.")),
    ]
    right = [
        ("Aggregation",
         f"{cfg.index.formula.title()} elementary index, matched model, "
         f"{'chained' if cfg.index.chained else 'fixed base'}. Items entering or leaving "
         "between two periods are excluded from that comparison, so replacement does not "
         "enter as price change."),
        ("Limitations",
         (f"{len(cfg.quality_adjustment.entries)} replacements valued and linked (see the "
          "ledger); every other departing item is treated as unrelated to its successor. "
          if cfg.quality_adjustment.entries else
          "No quality adjustment between a departing item and its replacement, so their "
          "price gap is implicitly treated as quality. ")
         + (contributions_gap(res) or (
             "Expenditure weights supplied: the aggregate is their weighted arithmetic mean, "
             "and its change is decomposed into contributions." if _has_weights(res) else
             "No expenditure weights, so the aggregate is equally weighted and "
             "indicative."))),
    ]
    for col, items in ((M, left), (W / 2 + 0.2, right)):
        y = 1.7
        for head, body in items:
            _text(s, col, y, W / 2 - M - 0.3, 0.35,
                  [{"text": head, "size": 15, "bold": True, "colour": ACCENT}])
            # A long limitations block steps down a size rather than running
            # into the footer.
            size = 12 if len(body) <= 360 else 10.5
            _text(s, col, y + 0.42, W / 2 - M - 0.3, 2.0,
                  [{"text": body, "size": size, "colour": ON_DARK, "line_spacing": 1.25}])
            y += 2.5
    _text(s, M, 6.7, 11, 0.4,
          [{"text": "Configuration exported alongside this deck reproduces every figure.",
            "size": 10, "colour": MUTED, "italic": True}])
    s.notes_slide.notes_text_frame.text = (
        "Closing method slide. Use this to answer 'how do I know this is right': every "
        "setting named here is in the exported configuration file, so the analysis can be "
        "reproduced exactly, and every judgement call (imputation, formula, chaining) is a "
        "stated default rather than something inferred from the data.")
    return s


# ----------------------------------------------------------------------
def slide_supplementary(prs: PresentationType, res: dict[str, Any]) -> Any | None:
    """One slide naming what produced each supplementary series.

    The deck is the artefact that most often leaves this application on its
    own, in front of an audience who will not open the workbook. A
    seasonally adjusted line on a chart, with no statement of what adjusted
    it, is the single easiest way for this platform to mislead somebody, so
    the deck carries the same sentences the report does -- not a shortened
    version of them.

    Returns None, and adds nothing, when the run had no supplementary
    series, so a deck never gains an empty slide.
    """
    from .report import multilateral_note, outlier_note, seasonal_note

    blocks = [(title, body) for title, body in (
        ("Multilateral index", multilateral_note(res)),
        ("Seasonality", seasonal_note(res)),
        ("Outlier review", outlier_note(res))) if body]
    if not blocks:
        return None

    s = _blank(prs)
    _bg(s, prs, INK)
    _text(s, M, 0.6, 11, 0.8,
          [{"text": "What produced these series", "size": 32, "bold": True,
            "colour": ON_DARK, "font": HEAD_FONT}])
    y = 1.7
    height = min(1.6, 4.6 / max(1, len(blocks)))
    for head, body in blocks:
        _text(s, M, y, W - 2 * M, 0.35,
              [{"text": head, "size": 15, "bold": True, "colour": ACCENT}])
        _text(s, M, y + 0.42, W - 2 * M, height,
              [{"text": body, "size": 11, "colour": ON_DARK, "line_spacing": 1.25}])
        y += height + 0.7
    _text(s, M, 6.7, 11, 0.4,
          [{"text": "Every series in the exported workbook names the method that produced "
                    "it, on its own sheet.", "size": 10, "colour": MUTED, "italic": True}])
    s.notes_slide.notes_text_frame.text = (
        "Read this slide out if anyone asks what 'seasonally adjusted' or 'multilateral' "
        "means here. The engine that actually ran is named, including where it is a fallback "
        "rather than the method a reader would assume, and the unadjusted series is in the "
        "workbook beside the adjusted one.")
    return s


# ----------------------------------------------------------------------
def slide_contributions(prs: PresentationType, res: dict[str, Any]) -> Any | None:
    """What drove the headline: the contributions table, its level and its
    residual. The residual is a row of the table, not a footnote, because a
    decomposition whose parts do not quite add up has to say so where the
    parts are.

    Returns None, and adds no slide, when there are no contributions to
    show (no expenditure weights, or a single period): a deck never carries
    a slide whose only content is why it is empty. The reason goes into the
    Limitations block of the method slide instead (`contributions_gap`)."""
    from .report import CONTRIBUTIONS_TITLE, contributions_summary

    if "All items" not in res["indices"].columns:
        return None
    note, table = contributions_summary(res)
    if table.empty:
        return None
    s = _blank(prs)
    _text(s, M, 0.5, 11, 0.8,
          [{"text": CONTRIBUTIONS_TITLE, "size": 30, "bold": True, "font": HEAD_FONT}])
    _text(s, M, 1.35, W - 2 * M, 1.2, [{"text": note, "size": 11, "colour": MUTED}])
    y = 2.7
    rows = list(table.iterrows())[:14]
    if rows:
        _text(s, M, y, 5.5, 0.3, [{"text": "Category", "size": 11, "bold": True}])
        _text(s, M + 6.0, y, 2.5, 0.3, [{"text": "Change, %", "size": 11, "bold": True}])
        _text(s, M + 8.6, y, 3.0, 0.3, [{"text": "Contribution, pp", "size": 11, "bold": True}])
        y += 0.34
    for name, row in rows:
        residual = str(name).startswith("Residual")
        change = "" if pd.isna(row["Change, %"]) else f"{row['Change, %']:+.2f}"
        value = row["Contribution, pp"]
        shown = f"{value:.1e}" if residual else f"{value:+.3f}"
        colour = ACCENT if residual else INK
        _text(s, M, y, 5.8, 0.3, [{"text": str(name), "size": 10, "colour": colour}])
        _text(s, M + 6.0, y, 2.5, 0.3, [{"text": change, "size": 10}])
        _text(s, M + 8.6, y, 3.0, 0.3, [{"text": shown, "size": 10, "colour": colour}])
        y += 0.28
    s.notes_slide.notes_text_frame.text = (
        "The contributions sum to the headline's change; the last row is the gap between "
        "that sum and the published change, which should be zero to rounding. If it is not, "
        "say why before taking questions on the categories.")
    return s


def contributions_gap(res: dict[str, Any]) -> str:
    """Why the deck has no contributions slide, as a complete sentence, or
    the empty string when it has one."""
    from .report import contributions_summary

    if "All items" not in res["indices"].columns:
        return ""
    note, table = contributions_summary(res)
    return note if table.empty else ""


def _has_weights(res: dict[str, Any]) -> bool:
    from ..engine.index import category_weights

    imputed = res.get("imputed")
    return imputed is not None and category_weights(imputed) is not None


def slide_provenance(prs: PresentationType, stamp: ProvenanceStamp) -> Any:
    """The stamp, rendered where a reader of the deck would look for it:
    the last slide, in full, with the JSON copy in the notes and in the
    file's core properties."""
    s = _blank(prs)
    _text(s, M, 0.55, 11, 0.8,
          [{"text": "Provenance", "size": 32, "bold": True, "font": HEAD_FONT}])
    rows = [(k, provenance_display_value(k, v)) for k, v in stamp.rows()
            if k != "Parameters (JSON)"]
    value_w = W - 2 * M - 3.5
    top, bottom = 1.45, H - 0.35
    # Every value in full: each row is as tall as its wrapped text, and the
    # type steps down until the whole stamp fits the slide. Nothing is cut.
    for size in (10.0, 9.0, 8.0, 7.0):
        heights = [_wrapped_lines(v, value_w, size) * size * 1.22 / 72 + 0.05 for _, v in rows]
        if top + sum(heights) <= bottom:
            break
    y = top
    for (k, v), h in zip(rows, heights, strict=True):
        _text(s, M, y, 3.4, h, [{"text": k, "size": size, "bold": True, "colour": PRIMARY}])
        _text(s, M + 3.5, y, value_w, h,
              [{"text": str(sanitize_cell(v)), "size": size, "colour": INK}])
        y += h
    s.notes_slide.notes_text_frame.text = f"{STAMP_KEY} {stamp.to_json()}"
    return s


def provenance_display_value(field: str, value: str) -> str:
    """A stamp value as the slide shows it: in full, with the environment's
    `;`-joined library list given spaces to wrap at. The notes and the file
    properties keep the exact JSON."""
    return value.replace(";", "; ") if field == "Environment" else value


def _wrapped_lines(text: str, width_in: float, size_pt: float) -> int:
    """Lines `text` takes in a box `width_in` wide at `size_pt`, wrapping at
    spaces, with a word longer than a line broken across lines. A
    conservative estimate: Calibri averages about half an em per character."""
    per_line = max(1, int(width_in * 72 / (size_pt * 0.52)))
    lines, used = 1, 0
    for word in str(text).split(" "):
        n = len(word)
        need = n if used == 0 else used + 1 + n
        if need <= per_line:
            used = need
            continue
        lines += 1 if used else 0
        while n > per_line:           # a word longer than a line
            lines += 1
            n -= per_line
        used = n
    return lines


def _neutralise_leading_formulas(prs: PresentationType) -> None:
    """A text run that *begins* with a spreadsheet formula leader is user
    data placed at the start of a line (a label, a category name), and a
    slide's text is one copy-paste from a spreadsheet cell. Prose never
    begins with "=" or "@", so only such runs are touched, through the
    same `sanitize_cell` every other export uses."""
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        run.text = str(sanitize_cell(run.text))


def build_deck(res: dict[str, Any], nar: Narrative, charts: dict[str, Any], label: str = "",
               stamp: ProvenanceStamp | None = None) -> bytes:
    """Assemble the deck from whatever the narrative actually found."""
    from .charts import to_png

    stamp = stamp or build_stamp(res, label)

    png = {k: to_png(v) for k, v in charts.items()}
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(W), Inches(H)

    slide_title(prs, nar, label or res["config"].label,
                non_standard_notice(res["config"].index))
    slide_contents(prs, nar)

    # Headline numbers
    I, yoy = res["indices"], res["inflation"]
    stats: list[dict[str, Any]] = []
    if "All items" in I.columns:
        years = years_span(I.index)
        # The "= 100" period is whichever period the series was rebased to,
        # not whichever came first: they coincide by default and diverge the
        # moment a run sets its own index or price reference period. The deck
        # is the artefact that leaves this application, so a stale label here
        # is a wrong number published to someone who cannot check it.
        index_ref = resolve_index_reference_period(res["config"].index, I.index)
        stats.append({"value": f"{I['All items'].iloc[-1]:.0f}",
                      "label": f"All items index at {I.index[-1]:%b %Y}, "
                               f"{index_ref:%b %Y} = 100"})
        if years > 0:
            rate = annualised_rate(I["All items"].iloc[-1] / 100, years)
            stats.append({"value": f"{rate:.1f}%",
                          "label": "Average annual rate of change"})
        if yoy["All items"].notna().any():
            s_ = yoy["All items"].dropna()
            stats.append({"value": f"{s_.max():.1f}%", "accent": True,
                          "label": f"Peak twelve period rate, {s_.idxmax():%b %Y}"})
    n_repaired = res["quality"]["scale_errors_repaired"]
    n_detected = res["quality"]["scale_errors_detected"]
    if n_detected:
        stats.append({"value": f"{n_repaired}" if n_repaired == n_detected
                                else f"{n_repaired}/{n_detected}",
                      "accent": True,
                      "label": "Data faults detected and repaired" if n_repaired == n_detected
                               else "Data faults repaired / detected"})
    if stats:
        slide_stat_row(prs, "The headline numbers", stats[:4],
                       "Figures are produced from the cleaned collection using a matched "
                       "model index. " + (
                           "The aggregate is weighted by the expenditure weights supplied."
                           if _has_weights(res) else
                           "The aggregate is equally weighted, as no expenditure weights "
                           "were supplied."))

    slide_contributions(prs, res)

    # Quality adjustment is the most scrutinised number in a price index,
    # so when a ledger exists it gets its own slide, ahead of the findings.
    impact = res.get("quality_adjustment_impact")
    if impact is not None:
        n = len(res["config"].quality_adjustment.entries)
        plural = "s" if n != 1 else ""
        adjustment_stats: list[dict[str, Any]] = [
            {"value": f"{impact.adjustment_effect_points:+.2f}", "accent": True,
             "label": f"Index points: effect of valuing the {n} replacement{plural} "
                      "rather than treating the price gap as price"},
            {"value": f"{impact.adjustment_effect_annual_pp:+.2f} pp",
             "label": f"Of the annual rate at {impact.final_period:%b %Y} "
                      f"({impact.annual_measure.replace('_', ' ')})"},
            {"value": f"{impact.linking_effect_points:+.2f}",
             "label": "Index points: effect of linking replacements at all, against the "
                      "matched-model default"},
        ]
        slide_stat_row(
            prs, "Quality adjustment", adjustment_stats,
            "Each replacement's valuation, method, justification and approver are in the "
            "ledger exported with this deck. The matched-model default compares nothing across "
            "a replacement, which attributes the whole price gap to quality.")
        if "quality_adjustment" in png:
            slide_finding(prs, Finding(
                kind="method", headline="Three ways to treat the replacements",
                detail="The headline as compiled, with the same replacements linked but "
                       "valued at ratio one, and with none linked.",
                evidence=f"{impact.headline} at {impact.final_period:%b %Y}",
                importance=80, chart="quality_adjustment"),
                png["quality_adjustment"], "Quality adjustment")

    # One slide per material finding, in ranked order, deduplicating charts
    eyebrow = {"quality": "Data quality", "structure": "Sample structure",
               "trend": "Price trend", "seasonal": "Seasonality",
               "method": "Method"}
    used_charts, made = set(), 0
    for f in nar.findings:
        if made >= 7:
            break
        chart = png.get(f.chart) if f.chart and f.chart not in used_charts else None
        if f.chart and chart:
            used_charts.add(f.chart)
        if f.importance < 70 and not chart:
            continue
        slide_finding(prs, f, chart, eyebrow.get(f.kind, ""))
        made += 1

    slide_actions(prs, nar)
    slide_method(prs, res, nar)
    slide_supplementary(prs, res)
    seasonal = res.get("seasonal")
    if "seasonal_adjustment" in png and seasonal is not None and seasonal.adjustment is not None:
        # The adjusted line, never alone: the chart draws the unadjusted
        # series beside it, and the slide text is the full label.
        adjustment = seasonal.adjustment
        slide_finding(prs, Finding(
            kind="seasonal",
            headline=f"{adjustment.series_name}, seasonally adjusted with "
                     f"{adjustment.engine_label}, beside the unadjusted series",
            detail=adjustment.label[:1].upper() + adjustment.label[1:] + ".",
            evidence="Both series are in the exported workbook and the CSV.",
            importance=70, chart="seasonal_adjustment"),
            png["seasonal_adjustment"], "Seasonal adjustment")
    slide_provenance(prs, stamp)
    # python-pptx caps a core property at 255 characters, so the full JSON
    # lives in the provenance slide's notes; the subject just names the run.
    prs.core_properties.subject = f"{STAMP_KEY}:{stamp.run_id}"[:255]
    _neutralise_leading_formulas(prs)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
