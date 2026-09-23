"""Charts.

One factory, two consumers: the app renders the figures, the deck embeds them
as PNG. Building them once means a chart in the exported deck is the same chart
the user approved on screen, which is the whole point of an export.
"""

import io
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
    ax.scatter(ok["period"], ok["price_reported"], s=5, color=PRIMARY, alpha=0.35,
               linewidths=0, label="As reported")
    if len(bad):
        ax.scatter(bad["period"], bad["price_reported"], s=22, color=ACCENT,
                   alpha=0.9, linewidths=0, label="Flagged as unit error")
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
        ax.plot(I.index, I[c], lw=1.2, color=SERIES[i % len(SERIES)], alpha=0.85, label=c,
                linestyle=LINE_STYLES[i % len(LINE_STYLES)])
    if "All items" in I.columns:
        ax.plot(I.index, I["All items"], lw=3, color=INK, label="All items", zorder=5)
    ax.axhline(100, color=MUTED, lw=0.8, ls="--")
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
    ax.plot(periods, values, lw=2.4, color=PRIMARY)
    ax.fill_between(periods, 0, values, color=PRIMARY, alpha=0.10)
    ax.axhline(0, color=MUTED, lw=0.9)
    peak_at = pd.Timestamp(cast(Any, s.idxmax()))
    ax.scatter(np.array([peak_at], dtype="datetime64[ns]"), [s.max()], s=70, color=ACCENT,
               zorder=5)
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
    ax.barh(d.index, d["difference_pp"], color=colors, height=0.65)
    ax.axvline(0, color=INK, lw=1)
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
    ax.barh(d.index, d["amplitude_pct"], color=colors, height=0.65)
    ax.set_xlabel("Peak to trough within the year, %")
    ax.grid(axis="y", visible=False)
    ax.set_title("Seasonal amplitude, measured on the index",
                 loc="left", fontsize=12, color=INK, pad=12)
    return fig


def coverage_chart(matched: pd.DataFrame, figsize: tuple[float, float] = (10, 4)) -> Figure:
    fig, ax = _fig(figsize)
    d = matched.drop(columns=["All items"], errors="ignore").T
    im = ax.imshow(d.values, aspect="auto", cmap="Blues", vmin=0)
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
    ax.axhline(0, color=MUTED, linewidth=1)
    ax.scatter(result.fitted, result.residuals, s=14, color=PRIMARY, alpha=0.7)
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
    ax.scatter(result.leverage, result.residuals / sd, s=14, color=PRIMARY, alpha=0.7)
    threshold = 2.0 * result.n_params / max(result.n_obs, 1)
    ax.axvline(threshold, color=ACCENT, linewidth=1, linestyle="--")
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
    bars = ax.bar([labels[k] for k in levels.index], levels.values, color=colours, width=0.55,
                  hatch=["", "//", ".."], edgecolor="white")   # tellable apart in one ink
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


def build_all_charts(res: dict[str, Any]) -> dict[str, Figure]:
    """Every chart the narrative might reference, rendered once."""
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
    return charts
