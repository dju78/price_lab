"""Charts.

One factory, two consumers: the app renders the figures, the deck embeds them
as PNG. Building them once means a chart in the exported deck is the same chart
the user approved on screen, which is the whole point of an export.

Units on an axis
----------------
Every plotted artist is marked with the unit it is drawn in (`mark`), and
`check_figure` refuses a figure that puts two units on one axis -- an index
level beside a percentage change, a percentage change beside a
percentage-point contribution -- or that puts a nominal and a real series on
one axis without each line saying which it is. `build_all_charts` checks
every figure it returns, and `tests/test_decomposition.py` checks every chart
this module can draw, so a chart that mixes them fails a test rather than
reaching a reader. An unmarked artist is itself a failure: a rule that only
applies to the artists somebody remembered to label is not a rule.
"""

import io
import textwrap
from collections.abc import Mapping
from typing import Any, cast

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from ..core.config import IndexConfig
from ..engine import diagnostics as dg
from ..engine.index import resolve_index_reference_period

# Palette. Deep navy dominates, amber is the single sharp accent reserved for
# whatever the chart is actually about.
INK = "#12263A"
PRIMARY = "#1E4D6B"
ACCENT = "#D9822B"
MUTED = "#8A9BA8"
LIGHT = "#E9EEF2"
SERIES = ["#1E4D6B", "#D9822B", "#4E8098", "#A34A28", "#6B9080",
          "#8C6A9B", "#3C6E71", "#C08552", "#5C80BC", "#7D8491"]

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.edgecolor": MUTED, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": LIGHT, "grid.linewidth": 0.8,
    "figure.facecolor": "white", "axes.facecolor": "white",
})


# ----------------------------------------------------------------------
# Units
# ----------------------------------------------------------------------
UNITS: dict[str, str] = {
    "index_level": "index level",
    "percent_change": "percentage change",
    "percentage_points": "percentage-point contribution",
    "currency": "currency value",
    "price": "price",
    "count": "count",
    "other": "other",
}
BASES = ("nominal", "real")
#: Two kinds of spread that answer different questions and must never share
#: an axis: how much a number would move under a different sample, and how
#: much under a different defensible choice of method.
BANDS = ("sampling uncertainty", "methodological sensitivity")
_UNIT_ATTR, _BASIS_ATTR, _BAND_ATTR = "_pricelab_unit", "_pricelab_basis", "_pricelab_band"


class ChartUnitError(ValueError):
    """A figure draws incompatible quantities on one axis."""


def mark(artist: Any, unit: str, basis: str | None = None, band: str | None = None) -> Any:
    """Record the unit (and, for money, nominal or real) an artist is drawn
    in, and -- for a spread -- which kind of spread it is. Returns the
    artist, so a call can wrap the plotting call. A list of lines, or a bar
    container, marks each member."""
    if unit not in UNITS:
        raise ChartUnitError(f"unknown unit {unit!r}; one of {sorted(UNITS)}")
    if basis is not None and basis not in BASES:
        raise ChartUnitError(f"basis must be one of {BASES}, not {basis!r}")
    if band is not None and band not in BANDS:
        raise ChartUnitError(f"band must be one of {BANDS}, not {band!r}")
    if isinstance(artist, (list, tuple)):
        targets = list(artist)
    elif hasattr(artist, "patches"):
        targets = list(artist.patches)
    else:
        targets = [artist]
    for target in targets:
        setattr(target, _UNIT_ATTR, unit)
        setattr(target, _BASIS_ATTR, basis)
        setattr(target, _BAND_ATTR, band)
    return artist


def _data_artists(ax: Axes) -> list[Any]:
    return [*ax.lines, *ax.collections, *ax.patches, *ax.images]


def check_axes(ax: Axes) -> None:
    """Raise `ChartUnitError` if this axis mixes units, unlabelled bases, or
    a confidence interval with a sensitivity range."""
    bands = {getattr(a, _BAND_ATTR, None) for a in _data_artists(ax)} - {None}
    if len(bands) > 1:
        raise ChartUnitError(
            f"the axis {ax.get_title()!r} draws a confidence interval (sampling uncertainty) "
            "and a sensitivity range (methodological choice) together. They answer different "
            "questions and must never share an axis or be read as one band")
    units: dict[str, list[str]] = {}
    for artist in _data_artists(ax):
        unit = getattr(artist, _UNIT_ATTR, None)
        if unit is None:
            raise ChartUnitError(
                f"an unmarked {type(artist).__name__} on the axis titled {ax.get_title()!r}: "
                "every plotted artist must declare its unit with charts.mark")
        units.setdefault(unit, []).append(str(artist.get_label()))
    if len(units) > 1:
        described = "; ".join(f"{UNITS[u]} ({', '.join(sorted(set(v))[:3])})"
                              for u, v in units.items())
        raise ChartUnitError(
            f"one axis ({ax.get_title()!r}) mixes {len(units)} units: {described}. Draw each on "
            "an axis of its own")
    bases = {getattr(a, _BASIS_ATTR, None) for a in _data_artists(ax)} - {None}
    if len(bases) > 1:
        for artist in _data_artists(ax):
            basis = getattr(artist, _BASIS_ATTR, None)
            label = str(artist.get_label())
            if basis is not None and not label.startswith("_") and basis not in label.lower():
                raise ChartUnitError(
                    f"nominal and real series share the axis {ax.get_title()!r}, and the "
                    f"{basis} series is labelled {label!r}, which does not say it is {basis}")
            if basis is not None and label.startswith("_"):
                raise ChartUnitError(
                    f"nominal and real series share the axis {ax.get_title()!r}, and a {basis} "
                    "series there has no label at all")
        if not ax.get_ylabel():
            raise ChartUnitError(
                f"nominal and real series share the axis {ax.get_title()!r} with no axis label "
                "saying what they are measured in")


def check_figure(fig: Figure) -> Figure:
    """`check_axes` on every data axis of the figure; returns it unchanged."""
    for ax in fig.axes:
        if getattr(ax, "_colorbar", None) is not None:
            continue   # a colour bar's scale, not data
        check_axes(ax)
    return fig


def _fig(figsize: tuple[float, float]) -> tuple[Figure, Axes]:
    """Create a figure outside pyplot's global registry.

    plt.subplots() keeps every figure alive in a module-level list. In a
    long-running server that is a slow memory leak, so figures are built
    directly and handed to the caller to own.
    """
    fig = Figure(figsize=figsize, facecolor="white")
    ax = fig.add_subplot(111)
    ax.set_facecolor("white")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(color=LIGHT, linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax


def to_png(fig: Figure, dpi: int = 200) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    return buf.getvalue()


# ----------------------------------------------------------------------
def quality_bands(clean: pd.DataFrame, figsize: tuple[float, float] = (10, 5)) -> Figure:
    """The chart that proves the errors are unit faults, not volatility."""
    fig, ax = _fig(figsize)
    ok = clean[clean["flag"].isin(["none", "missing_code"])]
    bad = clean[clean["flag"].str.startswith("scale_")]
    mark(ax.scatter(ok["period"], ok["price_reported"], s=5, color=PRIMARY, alpha=0.35,
                    linewidths=0, label="As reported"), "price")
    if len(bad):
        mark(ax.scatter(bad["period"], bad["price_reported"], s=22, color=ACCENT,
                        alpha=0.9, linewidths=0, label="Flagged as unit error"), "price")
    ax.set_yscale("log")
    ax.set_ylabel("Reported price, log scale")
    ax.legend(frameon=False, loc="upper left", fontsize=9)
    ax.set_title("Faults separate into bands rather than a continuous tail",
                 loc="left", fontsize=12, color=INK, pad=12)
    return fig


LINE_STYLES = ["-", "--", "-.", ":", (0, (5, 1)), (0, (3, 1, 1, 1)), (0, (1, 1)),
               (0, (5, 2, 1, 2)), (0, (7, 3)), (0, (2, 2))]


def _percent_label(value: float, _pos: object) -> str:
    return f"{value:.1f}%" if abs(value - round(value)) > 1e-9 else f"{value:.0f}%"


def index_chart(I: pd.DataFrame, cfg: IndexConfig | None = None,
                figsize: tuple[float, float] = (10, 5)) -> Figure:
    """`cfg` is the run's IndexConfig, read only to label the y-axis with the
    period the series was actually rebased to. Optional, and defaulting to
    the series' first period when absent, because that is what the rebasing
    step itself defaults to -- so a caller with no config in hand still gets
    a label that matches the arithmetic rather than one that contradicts it.
    """
    fig, ax = _fig(figsize)
    cats = [c for c in I.columns if c != "All items"]
    # Colour alone does not survive a greyscale print, and the bulletin is
    # printed: each series also gets its own dash pattern, so eleven lines
    # stay tellable apart in one ink.
    for i, c in enumerate(cats):
        mark(ax.plot(I.index, I[c], lw=1.2, color=SERIES[i % len(SERIES)], alpha=0.85, label=c,
                     linestyle=LINE_STYLES[i % len(LINE_STYLES)]), "index_level")
    if "All items" in I.columns:
        mark(ax.plot(I.index, I["All items"], lw=3, color=INK, label="All items", zorder=5),
             "index_level")
    mark(ax.axhline(100, color=MUTED, lw=0.8, ls="--"), "index_level")
    index_ref = resolve_index_reference_period(cfg or IndexConfig(), I.index)
    ax.set_ylabel(f"Index, {index_ref:%b %Y} = 100")
    # Below the axes, not over the data: a legend inside the plot area sat
    # on the seasonal peaks in the rendered bulletin.
    ax.legend(frameon=False, fontsize=8, ncol=4, loc="upper center",
              bbox_to_anchor=(0.5, -0.12))
    ax.set_title("Category price indices", loc="left", fontsize=12, color=INK, pad=12)
    return fig


def inflation_chart(yoy: pd.DataFrame, col: str = "All items",
                    figsize: tuple[float, float] = (10, 4.5)) -> Figure | None:
    fig, ax = _fig(figsize)
    s = yoy[col].dropna()
    # numpy arrays rather than the Series' own `.values`/index: pandas types
    # those as a union with ExtensionArray, which matplotlib's stubs do not
    # accept, and the conversion is what matplotlib does internally anyway.
    periods = pd.DatetimeIndex(s.index).to_numpy()
    values = s.to_numpy(dtype=float)
    mark(ax.plot(periods, values, lw=2.4, color=PRIMARY), "percent_change")
    mark(ax.fill_between(periods, 0, values, color=PRIMARY, alpha=0.10), "percent_change")
    mark(ax.axhline(0, color=MUTED, lw=0.9), "percent_change")
    peak_at = pd.Timestamp(cast(Any, s.idxmax()))
    mark(ax.scatter(np.array([peak_at], dtype="datetime64[ns]"), [s.max()], s=70, color=ACCENT,
                    zorder=5), "percent_change")
    ax.annotate(f"{s.max():.1f}%  {peak_at:%b %Y}", (peak_at, s.max()),
                textcoords="offset points", xytext=(10, 6), color=ACCENT,
                fontsize=10, fontweight="bold")
    # A whole-number formatter on half-point ticks printed "2%, 2%, 2%" in
    # the rendered bulletin; one decimal is shown whenever the tick step
    # needs it.
    ax.yaxis.set_major_formatter(FuncFormatter(_percent_label))
    ax.set_ylabel("% change on the same period a year earlier")
    ax.set_title("Twelve period rate of change", loc="left", fontsize=12, color=INK, pad=12)
    return fig


def method_chart(comp: pd.DataFrame, figsize: tuple[float, float] = (10, 5)) -> Figure:
    """Matched index against a naive average. The bar that carries the argument."""
    fig, ax = _fig(figsize)
    d = comp.dropna().sort_values("difference_pp")
    colors = [ACCENT if abs(v) >= 5 else MUTED for v in d["difference_pp"]]
    mark(ax.barh(d.index, d["difference_pp"], color=colors, height=0.65), "percentage_points")
    mark(ax.axvline(0, color=INK, lw=1), "percentage_points")
    ax.set_xlabel("Matched index minus naive average of prices, percentage points")
    ax.grid(axis="y", visible=False)
    ax.set_title("What ignoring item replacement would cost",
                 loc="left", fontsize=12, color=INK, pad=12)
    return fig


def seasonality_chart(seas: pd.DataFrame,
                      figsize: tuple[float, float] = (10, 4.5)) -> Figure:
    fig, ax = _fig(figsize)
    d = seas[seas.index != "All items"].sort_values("amplitude_pct")
    colors = [ACCENT if v >= 10 else MUTED for v in d["amplitude_pct"]]
    mark(ax.barh(d.index, d["amplitude_pct"], color=colors, height=0.65), "percent_change")
    ax.set_xlabel("Peak to trough within the year, %")
    ax.grid(axis="y", visible=False)
    ax.set_title("Seasonal amplitude, measured on the index",
                 loc="left", fontsize=12, color=INK, pad=12)
    return fig


def coverage_chart(matched: pd.DataFrame, figsize: tuple[float, float] = (10, 4)) -> Figure:
    fig, ax = _fig(figsize)
    d = matched.drop(columns=["All items"], errors="ignore").T
    im = mark(ax.imshow(d.values, aspect="auto", cmap="Blues", vmin=0), "count")
    ax.set_yticks(range(len(d.index)), d.index, fontsize=8)
    step = max(1, len(d.columns) // 10)
    ax.set_xticks(range(0, len(d.columns), step),
                  [pd.Timestamp(p).strftime("%Y") for p in d.columns[::step]], fontsize=8)
    ax.grid(False)
    fig.colorbar(im, ax=ax, shrink=0.7, label="matched items")
    ax.set_title("Matched items behind each comparison",
                 loc="left", fontsize=12, color=INK, pad=12)
    return fig


def hedonic_residual_chart(result: Any, figsize: tuple[float, float] = (10, 4.5)) -> Figure:
    """Residuals against fitted values, in the regression's own scale.

    The plot a reviewer looks at before the coefficient table: a fan
    shape means the robust standard errors were needed, a curve means the
    functional form is wrong, and a handful of far points are the
    observations to go and look at.
    """
    fig, ax = _fig(figsize)
    mark(ax.axhline(0, color=MUTED, linewidth=1), "other")
    mark(ax.scatter(result.fitted, result.residuals, s=14, color=PRIMARY, alpha=0.7), "other")
    ax.set_xlabel("fitted (transformed price)")
    ax.set_ylabel("residual")
    ax.set_title(f"Hedonic residuals: {result.spec.functional_form}, adjusted R² "
                 f"{result.adj_r_squared:.3f}", loc="left", fontsize=12, color=INK, pad=12)
    return fig


def hedonic_leverage_chart(result: Any, figsize: tuple[float, float] = (10, 4.5)) -> Figure:
    """Leverage against standardised residual: the observations that pull
    the fit towards themselves, and whether they are also badly fitted.
    The dashed line is the conventional 2k/n leverage threshold."""
    fig, ax = _fig(figsize)
    sd = float(result.residuals.std()) or 1.0
    mark(ax.scatter(result.leverage, result.residuals / sd, s=14, color=PRIMARY, alpha=0.7),
         "other")
    threshold = 2.0 * result.n_params / max(result.n_obs, 1)
    mark(ax.axvline(threshold, color=ACCENT, linewidth=1, linestyle="--"), "other")
    ax.set_xlabel("leverage (hat value)")
    ax.set_ylabel("standardised residual")
    ax.set_title("Hedonic leverage", loc="left", fontsize=12, color=INK, pad=12)
    return fig


def impact_chart(impact: Any, figsize: tuple[float, float] = (10, 4),
                 reference_period: pd.Timestamp | None = None) -> Figure:
    """The three scenarios' final headline levels side by side: the
    quality adjustment's effect as a picture, not a footnote."""
    fig, ax = _fig(figsize)
    levels = impact.scenarios["final_level"]
    labels = {"as_configured": "As compiled", "linked_unadjusted": "Linked, no adjustment",
              "no_link": "Not linked (matched model)"}
    colours = [ACCENT, PRIMARY, MUTED]
    bars = mark(ax.bar([labels[k] for k in levels.index], levels.values, color=colours,
                       width=0.55, hatch=["", "//", ".."], edgecolor="white"),   # one ink
                "index_level")
    for bar, value in zip(bars, levels.values, strict=True):
        ax.annotate(f"{value:.2f}", (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                    ha="center", va="bottom", fontsize=9, color=INK)
    lo = float(levels.min())
    ax.set_ylim(lo - max(1.0, (float(levels.max()) - lo) * 3), float(levels.max()) + 1.0)
    ref = f", {pd.Timestamp(reference_period):%b %Y} = 100" if reference_period is not None else ""
    ax.set_ylabel(f"{impact.headline} index at {impact.final_period:%b %Y}{ref}")
    ax.set_title(f"Quality adjustment moved the headline by "
                 f"{impact.adjustment_effect_points:+.2f} points",
                 loc="left", fontsize=12, color=INK, pad=12)
    return fig


def _footnote(fig: Figure, text: str, width: int = 150) -> None:
    """A wrapped note under the axes: where a qualification that must travel
    with the chart goes, so a PNG lifted out of a report still carries it."""
    fig.text(0.01, -0.01, textwrap.fill(text, width), ha="left", va="top", fontsize=7,
             color=INK)


def seasonal_adjustment_chart(adjustment: Any, figsize: tuple[float, float] = (10, 4.8)
                              ) -> Figure:
    """The unadjusted and adjusted series on the same axes, with the engine
    that produced the adjusted one in the title, the legend and the note.

    Both lines, always: the reader has to be able to see what the adjustment
    removed. And the engine in three places, because each can be cropped
    out of a screenshot on its own.
    """
    fig, ax = _fig(figsize)
    frame = adjustment.frame
    periods = pd.DatetimeIndex(frame.index).to_numpy()
    mark(ax.plot(periods, frame["unadjusted"].to_numpy(dtype=float), lw=1.2, color=MUTED,
                 linestyle="--", label="Unadjusted, as compiled"), "index_level")
    mark(ax.plot(periods, frame["adjusted"].to_numpy(dtype=float), lw=2.4, color=PRIMARY,
                 label=f"Seasonally adjusted ({adjustment.engine_label})"), "index_level")
    ax.set_ylabel(f"{adjustment.series_name}, index level")
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    fallback = " (fallback, not X-13ARIMA-SEATS)" if adjustment.fell_back else ""
    ax.set_title(f"{adjustment.series_name}: seasonally adjusted with "
                 f"{adjustment.engine_label}{fallback}", loc="left", fontsize=12, color=INK, pad=12)
    _footnote(fig, adjustment.label[:1].upper() + adjustment.label[1:] + ".")
    return check_figure(fig)


def contributions_chart(contrib: pd.DataFrame, title: str = "Contributions to the change",
                        figsize: tuple[float, float] = (10, 5)) -> Figure:
    """Stacked contributions per period, in percentage points, with their
    total marked. The total is the sum of the contributions -- the same
    number as the headline's percentage change, drawn and labelled in
    percentage points, because a percentage change and a percentage-point
    contribution do not share an axis here even when they are equal."""
    fig, ax = _fig(figsize)
    periods = pd.DatetimeIndex(contrib.index)
    x = np.arange(len(periods))
    positive, negative = np.zeros(len(x)), np.zeros(len(x))
    for i, column in enumerate(contrib.columns):
        values = contrib[column].fillna(0.0).to_numpy(dtype=float)
        bottom = np.where(values >= 0, positive, negative)
        mark(ax.bar(x, values, bottom=bottom, color=SERIES[i % len(SERIES)], width=0.8,
                    label=str(column)), "percentage_points")
        positive += np.where(values >= 0, values, 0.0)
        negative += np.where(values < 0, values, 0.0)
    total = contrib.sum(axis=1, min_count=1).to_numpy(dtype=float)
    mark(ax.plot(x, total, color=INK, marker="o", lw=0, ms=4,
                 label="Total (sum of contributions)"), "percentage_points")
    mark(ax.axhline(0, color=INK, lw=0.8), "percentage_points")
    step = max(1, len(x) // 12)
    ax.set_xticks(x[::step], [f"{p:%b %y}" for p in periods[::step]], fontsize=8)
    ax.set_ylabel("Contribution, percentage points")
    ax.legend(frameon=False, fontsize=7, ncol=6, loc="upper center",
              bbox_to_anchor=(0.5, -0.1))
    ax.set_title(title, loc="left", fontsize=12, color=INK, pad=12)
    return check_figure(fig)


def rates_chart(series: Mapping[str, pd.Series], title: str, ylabel: str = "% change",
                figsize: tuple[float, float] = (10, 4.5)) -> Figure:
    """Several rates of change on one axis -- all percentage changes."""
    fig, ax = _fig(figsize)
    for i, (name, values) in enumerate(series.items()):
        clean = values.dropna()
        mark(ax.plot(pd.DatetimeIndex(clean.index).to_numpy(), clean.to_numpy(dtype=float),
                     lw=2.2 if i == 0 else 1.4, color=INK if i == 0 else SERIES[i % len(SERIES)],
                     linestyle=LINE_STYLES[i % len(LINE_STYLES)], label=name), "percent_change")
    mark(ax.axhline(0, color=MUTED, lw=0.8), "percent_change")
    ax.yaxis.set_major_formatter(FuncFormatter(_percent_label))
    ax.set_ylabel(ylabel)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.set_title(title, loc="left", fontsize=12, color=INK, pad=12)
    return check_figure(fig)


def deflation_chart(result: Any, figsize: tuple[float, float] = (10, 4.8)) -> Figure:
    """Nominal and real on one axis, which is allowed only because both are
    in the same currency and each line says which it is: "nominal (current
    prices)" and "real (constant Dec 2024 prices)". The axis label and the
    note name the deflator and the reference period."""
    fig, ax = _fig(figsize)
    frame = result.frame
    periods = pd.DatetimeIndex(frame.index).to_numpy()
    mark(ax.plot(periods, frame["nominal"].to_numpy(dtype=float), lw=1.4, color=MUTED,
                 linestyle="--", label=f"{result.nominal_name}, nominal (current prices)"),
         "currency", "nominal")
    mark(ax.plot(periods, result.real.reindex(frame.index).to_numpy(dtype=float), lw=2.4,
                 color=PRIMARY, label=f"{result.nominal_name}, real ({result.unit})"),
         "currency", "real")
    ax.set_ylabel(f"Value: nominal in current prices, real in {result.unit}")
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    ax.set_title(f"{result.nominal_name}, nominal and real", loc="left", fontsize=12,
                 color=INK, pad=12)
    _footnote(fig, result.label + ".")
    return check_figure(fig)


def interval_chart(result: Any, figsize: tuple[float, float] = (10, 2.6)) -> Figure:
    """A confidence interval for a movement, alone on its axis, labelled as
    sampling uncertainty. The sensitivity range has its own chart."""
    fig, ax = _fig(figsize)
    mark(ax.plot([result.lower_pct, result.upper_pct], [0, 0], lw=6, color=PRIMARY,
                 solid_capstyle="butt", label=f"{result.level:.0%} confidence interval"),
         "percent_change", band="sampling uncertainty")
    mark(ax.plot([result.estimate_pct], [0], "o", color=INK, ms=8, label="estimate"),
         "percent_change", band="sampling uncertainty")
    ax.set_yticks([])
    ax.set_xlabel("Change in All items, % (sampling uncertainty only)")
    ax.set_title("Sampling uncertainty: how much the change would move under a different sample",
                 loc="left", fontsize=11, color=INK, pad=10)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    _footnote(fig, result.label)
    return check_figure(fig)


def sensitivity_chart(result: Any, figsize: tuple[float, float] = (10, 5)) -> Figure:
    """Each alternative choice's headline, as bars against the published
    level, alone on its axis and labelled as methodological sensitivity --
    never a confidence interval."""
    fig, ax = _fig(figsize)
    computed = result.computed.sort_values("headline")
    labels = [f"{d}: {s}"[:70] for d, s in zip(computed["dimension"], computed["setting"],
                                              strict=True)]
    mark(ax.barh(labels, computed["headline"].to_numpy(dtype=float), color=MUTED, height=0.6),
         "index_level", band="methodological sensitivity")
    mark(ax.axvline(result.baseline, color=INK, lw=1.5, label="published"),
         "index_level", band="methodological sensitivity")
    ax.set_xlabel(f"All items at {result.period:%b %Y} under each alternative choice "
                  f"({result.reference:%b %Y} = 100)")
    ax.set_xlim(min(computed["headline"].min(), result.baseline) * 0.97,
                max(computed["headline"].max(), result.baseline) * 1.02)
    ax.grid(axis="y", visible=False)
    ax.set_title("Methodological sensitivity (not a confidence interval)", loc="left",
                 fontsize=11, color=INK, pad=10)
    _footnote(fig, result.label)
    return check_figure(fig)


def build_all_charts(res: dict[str, Any]) -> dict[str, Figure]:
    """Every chart the narrative might reference, rendered once, each one
    checked for mixed units on an axis before it is handed to anybody."""
    I, yoy = res["indices"], res["inflation"]
    cfg = res["config"].index if "config" in res else None
    charts = {
        "quality_bands": quality_bands(res["clean"]),
        "index": index_chart(I, cfg),
        "coverage": coverage_chart(res["matched_counts"]),
    }
    if "All items" in yoy.columns and yoy["All items"].notna().any():
        inflation = inflation_chart(yoy)
        if inflation is not None:
            charts["inflation"] = inflation
    comp = dg.unmatched_comparison(res["imputed"], I)
    if comp["difference_pp"].notna().any():
        charts["method"] = method_chart(comp)
    seas = dg.seasonality(I)
    if len(seas):
        charts["seasonality"] = seasonality_chart(seas)
    if res.get("quality_adjustment_impact") is not None:
        charts["quality_adjustment"] = impact_chart(
            res["quality_adjustment_impact"],
            reference_period=resolve_index_reference_period(cfg or IndexConfig(), I.index))
    seasonal = res.get("seasonal")
    if seasonal is not None and seasonal.adjustment is not None:
        charts["seasonal_adjustment"] = seasonal_adjustment_chart(seasonal.adjustment)
    for figure in charts.values():
        check_figure(figure)
    return charts
