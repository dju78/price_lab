"""Outlier screens, and the review queue that is the only way a quote leaves
an index.

Four screens, deliberately, because they disagree. A quote caught by one and
not the others is a different kind of evidence from one caught by all four,
and the reviewer who has to decide about it needs to see which:

Tukey fences     the quartiles of the cell's log price relatives, plus or
                 minus `k` interquartile ranges. Symmetric in logs, which is
                 the right symmetry for a ratio -- a halving and a doubling
                 are the same size of surprise.
Quartile method  the same quartiles, but the distance is measured from the
                 median outwards in each direction separately, so a skewed
                 distribution is not cut off on its long side. This is the
                 screen most European offices describe under that name.
Hidiroglou-      scores the ratio and then multiplies by the magnitude of
Berthelot        the price, so a large item must move further than a small
                 one before it is flagged. The screen used where the
                 aggregate is dominated by a few large contributors, which
                 a purely distributional screen will not protect.
Ratio screen     the price relative outside a fixed band. Crude, and there
                 precisely because the other three need a distribution: on a
                 cell of four quotes there are no usable quartiles, and this
                 is what is left.

What this module will not do is delete anything. Detection produces flags;
flags produce queue entries; an analyst accepts, rejects or annotates each
one with a stated reason and their name against it; and only then does
`apply_decisions` exclude anything, by marking it excluded rather than by
dropping the row. There is no argument to any function here that silently
removes a quote, because an excluded quote that nobody signed for is a
deletion nobody can review, and a published index with one in it cannot be
defended.

Exclusions are reported the way imputations already are: as a count and as a
share of the cell they would otherwise have fed. A reader who can see that
three percent of a category's quotes were imputed, but not that another two
percent were excluded, has been shown half the treatment.

Sources: Hidiroglou and Berthelot (1986), "Statistical editing and imputation
for periodic business surveys", Survey Methodology 12; Tukey (1977),
*Exploratory Data Analysis*; Eurostat, *Handbook on Price and Volume Measures*
and the HICP methodological manual on price relative screening.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np
import pandas as pd

from ..core.config import OutlierConfig, OutlierDecision

#: The screens, in the order the queue lists them.
METHODS: tuple[str, ...] = ("tukey", "quartile", "hidiroglou_berthelot", "ratio")

METHOD_LABELS: dict[str, str] = {
    "tukey": "Tukey fences",
    "quartile": "Quartile method",
    "hidiroglou_berthelot": "Hidiroglou-Berthelot",
    "ratio": "Period-on-period ratio",
}

#: What an analyst may conclude about a flagged quote.
DECISIONS: tuple[str, ...] = ("accept", "reject", "annotate")

DECISION_LABELS: dict[str, str] = {
    "accept": "Accept (a genuine price)",
    "reject": "Reject (an error; exclude from the index)",
    "annotate": "Annotate (keep it, on the record)",
}

#: Columns every screen returns, so the queue can hold them all in one table.
FLAG_COLUMNS: tuple[str, ...] = (
    "period", "item_id", "category", "price", "previous_price", "ratio",
    "method", "statistic", "lower", "upper")


class OutlierError(ValueError):
    """Raised when the data cannot support the screen asked for."""


# ---------------------------------------------------------------------
# Price relatives
# ---------------------------------------------------------------------
def price_relatives(df: pd.DataFrame, price_col: str = "price_clean") -> pd.DataFrame:
    """Each item's period-on-period price relative, with what it was built from.

    Every screen here works on relatives rather than on levels, because a
    price level is not comparable between items and a movement is. A cell of
    bread and champagne has no usable distribution of prices and a perfectly
    usable one of price changes.

    An item's first observation has no predecessor and no relative, and is
    absent from the result rather than carrying a null: it is not a quote a
    screen declined to flag, it is a quote no screen could see.
    """
    required = {"period", "item_id"}
    if not required <= set(df.columns):
        raise OutlierError(
            f"outlier screening needs {sorted(required)} columns; this frame has "
            f"{list(df.columns)[:8]}")
    price = price_col if price_col in df.columns else "price_reported"
    if price not in df.columns:
        raise OutlierError(f"neither {price_col!r} nor 'price_reported' is a column here")

    frame = pd.DataFrame({
        "period": pd.to_datetime(df["period"]),
        "item_id": df["item_id"].astype(str),
        "category": (df["category"].astype(str) if "category" in df.columns
                     else pd.Series("all", index=df.index)),
        "price": pd.to_numeric(df[price], errors="coerce"),
    }).sort_values(["item_id", "period"])
    frame = frame[np.isfinite(frame["price"]) & (frame["price"] > 0)]
    frame["previous_price"] = frame.groupby("item_id")["price"].shift(1)
    frame = frame[np.isfinite(frame["previous_price"]) & (frame["previous_price"] > 0)]
    frame["ratio"] = frame["price"] / frame["previous_price"]
    frame["log_ratio"] = np.log(frame["ratio"].to_numpy(dtype=float))
    out: pd.DataFrame = frame.reset_index(drop=True)
    return out


def _outside_deadband(values: np.ndarray, centre: float, cfg: OutlierConfig) -> np.ndarray:
    """Which log relatives are far enough from the cell's centre to be worth
    flagging at all.

    Applied by every distributional screen before its own fences, so a cell
    whose relatives are tightly clustered does not turn a rounding of a
    price into a queue entry. See `OutlierConfig.min_change_pct` for why
    this is a parameter rather than a constant.
    """
    band = abs(float(np.log1p(cfg.min_change_pct / 100.0)))
    outside: np.ndarray = np.abs(values - centre) > band
    return outside


def _flag_frame(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=list(FLAG_COLUMNS))
    frame: pd.DataFrame = pd.DataFrame(list(rows))
    return frame.loc[:, list(FLAG_COLUMNS)]


# ---------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------
def tukey_fences(relatives: pd.DataFrame, cfg: OutlierConfig | None = None) -> pd.DataFrame:
    """Quartiles of the cell's log relatives, plus or minus k interquartile
    ranges.

    In logs rather than in ratios: a fence drawn on raw ratios sits at
    different multiples above and below one, so an item that halved would be
    flagged while an item that doubled would not, and the screen would have
    a direction nobody chose. Tukey (1977); k = 1.5 is his, 3.0 the
    conventional "far out" fence.
    """
    cfg = cfg or OutlierConfig()
    rows: list[dict[str, Any]] = []
    for (period, category), cell in relatives.groupby(["period", "category"]):
        if len(cell) < cfg.min_cell_size:
            continue
        values = cell["log_ratio"].to_numpy(dtype=float)
        q1, q3 = np.percentile(values, [25, 75])
        spread = q3 - q1
        if spread <= 0:
            continue
        low, high = q1 - cfg.tukey_k * spread, q3 + cfg.tukey_k * spread
        far = _outside_deadband(values, float(np.median(values)), cfg)
        for row, value, beyond in zip(cell.itertuples(), values, far, strict=True):
            if beyond and (value < low or value > high):
                rows.append({
                    "period": period, "item_id": row.item_id, "category": category,
                    "price": row.price, "previous_price": row.previous_price,
                    "ratio": row.ratio, "method": "tukey", "statistic": float(value),
                    "lower": float(np.exp(low)), "upper": float(np.exp(high))})
    return _flag_frame(rows)


def quartile_method(relatives: pd.DataFrame, cfg: OutlierConfig | None = None) -> pd.DataFrame:
    """Distance from the median, measured outwards in each direction.

        lower = median - ratio x (median - Q1)
        upper = median + ratio x (Q3 - median)

    The difference from Tukey is that the two sides are scaled by their own
    halves of the distribution, so a cell whose relatives are skewed -- the
    usual shape when a promotion runs in one direction -- is not cut off on
    its long side merely for being long. The screen most European statistical
    offices describe under this name.
    """
    cfg = cfg or OutlierConfig()
    rows: list[dict[str, Any]] = []
    for (period, category), cell in relatives.groupby(["period", "category"]):
        if len(cell) < cfg.min_cell_size:
            continue
        values = cell["log_ratio"].to_numpy(dtype=float)
        q1, median, q3 = np.percentile(values, [25, 50, 75])
        below, above = median - q1, q3 - median
        if below <= 0 and above <= 0:
            continue
        low = median - cfg.quartile_ratio * below if below > 0 else -np.inf
        high = median + cfg.quartile_ratio * above if above > 0 else np.inf
        far = _outside_deadband(values, float(median), cfg)
        for row, value, beyond in zip(cell.itertuples(), values, far, strict=True):
            if beyond and (value < low or value > high):
                rows.append({
                    "period": period, "item_id": row.item_id, "category": category,
                    "price": row.price, "previous_price": row.previous_price,
                    "ratio": row.ratio, "method": "quartile", "statistic": float(value),
                    "lower": float(np.exp(low)), "upper": float(np.exp(high))})
    return _flag_frame(rows)


def hidiroglou_berthelot(relatives: pd.DataFrame, cfg: OutlierConfig | None = None
                         ) -> pd.DataFrame:
    """The ratio scored, then scaled by how much the quote matters.

    Two steps. First the ratio is centred on the cell's median in a way that
    is symmetric in both directions:

        s_i = 1 - median(r)/r_i   if r_i < median(r),  else  r_i/median(r) - 1

    Then it is weighted by the size of the quote:

        E_i = |s_i| x max(p_it, p_i,t-1) ^ u

    and flagged against the median of E with separate distances either side,
    each floored at five percent of the median so a cell whose E values are
    nearly identical does not flag everything. The importance exponent `u`
    is what distinguishes this screen from the other three: at u = 0 it is a
    screen on the ratio alone, and at u = 1 a small item must move a great
    deal further than a large one before anyone is asked to look at it.
    That is the right trade where a handful of quotes carry most of an
    aggregate, and the wrong one where every quote matters equally.

    Hidiroglou and Berthelot (1986); c between 4 and 8 in Statistics
    Canada's usual practice.
    """
    cfg = cfg or OutlierConfig()
    rows: list[dict[str, Any]] = []
    for (period, category), cell in relatives.groupby(["period", "category"]):
        if len(cell) < cfg.min_cell_size:
            continue
        ratios = cell["ratio"].to_numpy(dtype=float)
        median_ratio = float(np.median(ratios))
        if not np.isfinite(median_ratio) or median_ratio <= 0:
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            s = np.where(ratios < median_ratio, 1.0 - median_ratio / ratios,
                         ratios / median_ratio - 1.0)
        magnitude = np.maximum(cell["price"].to_numpy(dtype=float),
                               cell["previous_price"].to_numpy(dtype=float))
        effect = np.abs(s) * np.power(magnitude, cfg.hb_u)
        effect = np.where(np.isfinite(effect), effect, np.nan)
        if not np.isfinite(effect).any():
            continue
        e_q1, e_med, e_q3 = np.nanpercentile(effect, [25, 50, 75])
        floor = abs(0.05 * e_med) if np.isfinite(e_med) else 0.0
        d_low = max(e_med - e_q1, floor)
        d_high = max(e_q3 - e_med, floor)
        if d_low <= 0 and d_high <= 0:
            continue
        low, high = e_med - cfg.hb_c * d_low, e_med + cfg.hb_c * d_high
        logs = cell["log_ratio"].to_numpy(dtype=float)
        far = _outside_deadband(logs, float(np.log(median_ratio)), cfg)
        for row, value, beyond in zip(cell.itertuples(), effect, far, strict=True):
            if not beyond or not np.isfinite(value) or low <= value <= high:
                continue
            rows.append({
                "period": period, "item_id": row.item_id, "category": category,
                "price": row.price, "previous_price": row.previous_price,
                "ratio": row.ratio, "method": "hidiroglou_berthelot",
                "statistic": float(value), "lower": float(low), "upper": float(high)})
    return _flag_frame(rows)


def ratio_screen(relatives: pd.DataFrame, cfg: OutlierConfig | None = None) -> pd.DataFrame:
    """A price relative outside a fixed band.

    No distribution, no cell size, no quartiles: just a band. That is the
    point of it. The other three screens need enough quotes in a cell to
    have a shape, and a thin cell -- four items in a category, a month with
    a collection failure -- has none, which is exactly where a doubled price
    is most likely to go through unnoticed. This screen is what covers that,
    and its band is deliberately wide because it is the crude one.
    """
    cfg = cfg or OutlierConfig()
    rows: list[dict[str, Any]] = []
    outside = (relatives["ratio"] < cfg.ratio_low) | (relatives["ratio"] > cfg.ratio_high)
    for row in relatives[outside].itertuples():
        rows.append({
            "period": row.period, "item_id": row.item_id, "category": row.category,
            "price": row.price, "previous_price": row.previous_price, "ratio": row.ratio,
            "method": "ratio", "statistic": float(cast(float, row.ratio)),
            "lower": cfg.ratio_low, "upper": cfg.ratio_high})
    return _flag_frame(rows)


SCREENS = {
    "tukey": tukey_fences,
    "quartile": quartile_method,
    "hidiroglou_berthelot": hidiroglou_berthelot,
    "ratio": ratio_screen,
}


# ---------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class OutlierScan:
    """Everything the screens found, as one reviewable queue."""

    flags: pd.DataFrame
    """One row per screen per flagged quote: the long form, which is what
    says *which* screen caught a quote."""
    queue: pd.DataFrame
    """One row per flagged quote, with the screens that caught it collapsed
    into `methods` and `n_methods`. This is the review queue; a quote caught
    by all four is a different proposition from one caught by the crude
    band alone, and the count is there so a reviewer can sort by it."""
    relatives: int
    """Price relatives examined, which is the denominator for the flag
    rate -- not the row count of the collection, which includes every
    item's first period, where no screen could look."""
    methods_run: tuple[str, ...]
    notes: tuple[str, ...] = ()

    @property
    def flag_rate(self) -> float:
        return len(self.queue) / self.relatives if self.relatives else float("nan")


def detect(df: pd.DataFrame, cfg: OutlierConfig | None = None, *,
           price_col: str = "price_clean") -> OutlierScan:
    """Run the configured screens and assemble the review queue.

    Nothing here changes the data. The result is a list of quotes somebody
    should look at, ordered by how many screens agreed, and that is the
    whole of it: the next thing that happens to a flagged quote is a person
    deciding about it.
    """
    cfg = cfg or OutlierConfig()
    relatives = price_relatives(df, price_col)
    notes: list[str] = []
    if relatives.empty:
        return OutlierScan(flags=_flag_frame([]), queue=_queue_frame(_flag_frame([])),
                           relatives=0, methods_run=tuple(cfg.methods),
                           notes=("no item has two consecutive priced periods, so there is no "
                                  "price relative for any screen to examine",))

    pieces = [SCREENS[method](relatives, cfg) for method in cfg.methods if method in SCREENS]
    flags = (pd.concat([p for p in pieces if not p.empty], ignore_index=True)
             if any(not p.empty for p in pieces) else _flag_frame([]))

    thin = relatives.groupby(["period", "category"]).size()
    too_thin = int((thin < cfg.min_cell_size).sum())
    if too_thin:
        notes.append(
            f"{too_thin} of {len(thin)} category-periods hold fewer than {cfg.min_cell_size} "
            "price relatives, so the three distributional screens were not run on them; the "
            "ratio screen was, which is why it is in the set")

    return OutlierScan(flags=flags, queue=_queue_frame(flags), relatives=int(len(relatives)),
                       methods_run=tuple(cfg.methods), notes=tuple(notes))


def _queue_frame(flags: pd.DataFrame) -> pd.DataFrame:
    columns = ["period", "item_id", "category", "price", "previous_price", "ratio",
               "methods", "n_methods"]
    if flags.empty:
        return pd.DataFrame(columns=columns)
    grouped = flags.groupby(["period", "item_id"], as_index=False).agg(
        category=("category", "first"), price=("price", "first"),
        previous_price=("previous_price", "first"), ratio=("ratio", "first"),
        methods=("method", lambda s: ", ".join(sorted(set(s)))),
        n_methods=("method", lambda s: int(len(set(s)))))
    return grouped[columns].sort_values(
        ["n_methods", "period", "item_id"], ascending=[False, True, True]).reset_index(drop=True)


def pending(scan: OutlierScan, entries: Sequence[OutlierDecision]) -> pd.DataFrame:
    """The queue minus what has already been decided.

    What a reviewer opens the page to see. A decided quote does not vanish
    from the record -- `decided` returns it, and the audit log has it with a
    name and a reason -- it simply stops being asked about.
    """
    if scan.queue.empty:
        return scan.queue
    seen = {(str(pd.Timestamp(e.period).date()), e.item_id) for e in entries}
    keys = [(str(pd.Timestamp(p).date()), str(i))
            for p, i in zip(scan.queue["period"], scan.queue["item_id"], strict=True)]
    return scan.queue[[k not in seen for k in keys]].reset_index(drop=True)


def decided(scan: OutlierScan, entries: Sequence[OutlierDecision]) -> pd.DataFrame:
    """Every decision taken, joined to what the screens had said about it."""
    if not entries:
        return pd.DataFrame(columns=["period", "item_id", "category", "decision", "reason",
                                     "analyst", "decided_at", "methods", "ratio"])
    rows = pd.DataFrame([e.model_dump() for e in entries])
    rows["period"] = pd.to_datetime(rows["period"])
    if scan.queue.empty:
        rows["methods"] = ""
        rows["n_methods"] = 0
        return rows
    merged = rows.merge(scan.queue[["period", "item_id", "methods", "n_methods", "ratio"]],
                        on=["period", "item_id"], how="left", suffixes=("", "_scan"))
    return merged


@dataclass(frozen=True)
class ExclusionReport:
    """What the rejected quotes would have fed, had they not been rejected."""

    excluded: int
    reviewed: int
    quotes: int
    """Quotes in the collection: the denominator that makes `share` mean
    what the reader needs it to -- the share of the aggregate the excluded
    quotes would have fed, in exactly the units imputation is already
    reported in."""
    by_category: pd.DataFrame
    """Per category: quotes excluded, quotes in the category, and the share.
    The same shape `engine.imputation.response_rates` reports imputation in,
    because a reader comparing the two should not have to translate."""
    by_period: pd.DataFrame
    decisions: Mapping[str, int] = field(default_factory=dict)
    """How many quotes were accepted, rejected and annotated."""

    @property
    def share(self) -> float:
        """Excluded quotes as a share of all quotes -- the aggregate they
        would have fed. Not a share of the decisions taken: a reviewer who
        rejected one of one flag has excluded one quote, not the whole
        index, and the two numbers are easy to confuse and far apart."""
        return self.excluded / self.quotes if self.quotes else 0.0


def apply_decisions(df: pd.DataFrame, entries: Sequence[OutlierDecision], *,
                    price_col: str = "price_clean") -> tuple[pd.DataFrame, ExclusionReport]:
    """Exclude what an analyst rejected, and report what that removed.

    Rejected quotes are **marked**, not dropped. The row stays, with
    `outlier_excluded` True and `outlier_reason` carrying the analyst's
    words, and the price is set to unavailable so the index cannot use it.
    That is the difference between an exclusion and a deletion: after this
    runs, the collection still contains every quote that was ever collected,
    and every one that was taken out of the index says who took it out and
    why.

    Accepted and annotated quotes are marked too, with their reason, and
    keep their price. A quote somebody looked at and kept is evidence about
    the collection just as much as one they removed, and it is the record
    that a screen firing did not automatically become an exclusion.
    """
    out = df.copy()
    for column, default in (("outlier_excluded", False), ("outlier_reason", ""),
                            ("outlier_decision", ""), ("outlier_analyst", "")):
        if column not in out.columns:
            out[column] = default
    if not entries:
        return out, ExclusionReport(
            excluded=0, reviewed=0, quotes=int(len(out)),
            by_category=pd.DataFrame(columns=["excluded", "quotes", "share"]),
            by_period=pd.DataFrame(columns=["excluded", "quotes", "share"]),
            decisions={})

    periods = pd.to_datetime(out["period"])
    items = out["item_id"].astype(str)
    counts = {"accept": 0, "reject": 0, "annotate": 0}
    for entry in entries:
        counts[entry.decision] = counts.get(entry.decision, 0) + 1
        target = (periods == pd.Timestamp(entry.period)) & (items == entry.item_id)
        if not target.any():
            continue
        out.loc[target, "outlier_decision"] = entry.decision
        out.loc[target, "outlier_reason"] = entry.reason
        out.loc[target, "outlier_analyst"] = entry.analyst
        if entry.decision == "reject":
            out.loc[target, "outlier_excluded"] = True
            if price_col in out.columns:
                out.loc[target, price_col] = np.nan

    excluded = out["outlier_excluded"].astype(bool)
    category = (out["category"].astype(str) if "category" in out.columns
                else pd.Series("all", index=out.index))
    by_category = pd.DataFrame({
        "excluded": excluded.groupby(category).sum().astype(int),
        "quotes": category.groupby(category).size().astype(int)})
    by_category["share"] = by_category["excluded"] / by_category["quotes"]
    by_period = pd.DataFrame({
        "excluded": excluded.groupby(periods).sum().astype(int),
        "quotes": periods.groupby(periods).size().astype(int)})
    by_period["share"] = by_period["excluded"] / by_period["quotes"]

    return out, ExclusionReport(
        excluded=int(excluded.sum()), reviewed=len(entries), quotes=int(len(out)),
        by_category=by_category.sort_values("share", ascending=False),
        by_period=by_period.sort_index(), decisions=counts)


def exclusion_note(report: ExclusionReport | None) -> str:
    """One sentence for the method note. Empty when nothing was reviewed, so
    it can be concatenated unconditionally."""
    if report is None or not report.reviewed:
        return ""
    worst = ""
    if len(report.by_category) and report.by_category["excluded"].max() > 0:
        top = report.by_category.index[0]
        worst = (f" The heaviest exclusion was in {top}, at "
                 f"{report.by_category.iloc[0]['share']:.1%} of its quotes.")
    return (
        f"{report.reviewed:,} flagged quote(s) were reviewed by an analyst: "
        f"{report.decisions.get('accept', 0):,} accepted as genuine, "
        f"{report.decisions.get('reject', 0):,} rejected and excluded from the index, "
        f"{report.decisions.get('annotate', 0):,} kept with a note. The exclusions are "
        f"{report.share:.2%} of the {report.quotes:,} quotes in the collection. Every decision "
        f"carries the analyst's name and stated reason into the audit log; no quote is removed "
        f"without one.{worst}")
