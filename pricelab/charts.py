"""Charts.

One factory, two consumers: the app renders the figures, the deck embeds them
as PNG. Building them once means a chart in the exported deck is the same chart
the user approved on screen, which is the whole point of an export.
"""

import io
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from . import diagnostics as dg

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


def _fig(figsize):
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


def to_png(fig, dpi=200) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    return buf.getvalue()


# ----------------------------------------------------------------------
def quality_bands(clean: pd.DataFrame, figsize=(10, 5)):
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


def index_chart(I: pd.DataFrame, figsize=(10, 5)):
    fig, ax = _fig(figsize)
    cats = [c for c in I.columns if c != "All items"]
    for i, c in enumerate(cats):
        ax.plot(I.index, I[c], lw=1.1, color=SERIES[i % len(SERIES)], alpha=0.75, label=c)
    if "All items" in I.columns:
        ax.plot(I.index, I["All items"], lw=3, color=INK, label="All items", zorder=5)
    ax.axhline(100, color=MUTED, lw=0.8, ls="--")
    ax.set_ylabel(f"Index, {I.index[0]:%b %Y} = 100")
    ax.legend(frameon=False, fontsize=8, ncol=3, loc="upper left")
    ax.set_title("Category price indices", loc="left", fontsize=12, color=INK, pad=12)
    return fig


def inflation_chart(yoy: pd.DataFrame, col="All items", figsize=(10, 4.5)):
    fig, ax = _fig(figsize)
    s = yoy[col].dropna()
    ax.plot(s.index, s.values, lw=2.4, color=PRIMARY)
    ax.fill_between(s.index, 0, s.values, color=PRIMARY, alpha=0.10)
    ax.axhline(0, color=MUTED, lw=0.9)
    peak_at = s.idxmax()
    ax.scatter([peak_at], [s.max()], s=70, color=ACCENT, zorder=5)
    ax.annotate(f"{s.max():.1f}%  {peak_at:%b %Y}", (peak_at, s.max()),
                textcoords="offset points", xytext=(10, 6), color=ACCENT,
                fontsize=10, fontweight="bold")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_title("Twelve period rate of change", loc="left", fontsize=12, color=INK, pad=12)
    return fig


def method_chart(comp: pd.DataFrame, figsize=(10, 5)):
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


def seasonality_chart(seas: pd.DataFrame, figsize=(10, 4.5)):
    fig, ax = _fig(figsize)
    d = seas[seas.index != "All items"].sort_values("amplitude_pct")
    colors = [ACCENT if v >= 10 else MUTED for v in d["amplitude_pct"]]
    ax.barh(d.index, d["amplitude_pct"], color=colors, height=0.65)
    ax.set_xlabel("Peak to trough within the year, %")
    ax.grid(axis="y", visible=False)
    ax.set_title("Seasonal amplitude, measured on the index",
                 loc="left", fontsize=12, color=INK, pad=12)
    return fig


def coverage_chart(matched: pd.DataFrame, figsize=(10, 4)):
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


def build_all_charts(res: dict) -> dict:
    """Every chart the narrative might reference, rendered once."""
    I, yoy = res["indices"], res["inflation"]
    charts = {
        "quality_bands": quality_bands(res["clean"]),
        "index": index_chart(I),
        "coverage": coverage_chart(res["matched_counts"]),
    }
    if "All items" in yoy.columns and yoy["All items"].notna().any():
        charts["inflation"] = inflation_chart(yoy)
    comp = dg.unmatched_comparison(res["imputed"], I)
    if comp["difference_pp"].notna().any():
        charts["method"] = method_chart(comp)
    seas = dg.seasonality(I)
    if len(seas):
        charts["seasonality"] = seasonality_chart(seas)
    return charts
