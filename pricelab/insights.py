"""The insight engine.

This is the part that does the interpreting. Everything else in the library
computes; this module reads what was computed and states what it means, in
plain English, with the supporting number attached.

Design rules:

- A finding must be falsifiable. Every one carries the evidence that produced
  it, so a reader can check it rather than trust it.
- A finding must clear a materiality threshold. A tool that reports every
  difference reports nothing, because the reader stops looking.
- Findings are ranked, not listed. The ranking is what turns a pile of results
  into a presentation.
- No finding claims a cause. The data shows that coffee replacements enter
  dearer; it does not show why. The wording keeps that line.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import numpy as np
import pandas as pd

from . import diagnostics as dg


MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


@dataclass
class Finding:
    headline: str                       # the claim, one line
    detail: str                         # the explanation, two or three sentences
    evidence: str = ""                  # the number that supports it
    kind: str = "general"               # quality | structure | trend | seasonal | method
    importance: float = 0.0             # ranking score, higher first
    action: str = ""                    # what a user should do about it
    table: Optional[pd.DataFrame] = None
    chart: Optional[str] = None         # key into the chart registry


@dataclass
class Narrative:
    findings: List[Finding] = field(default_factory=list)
    headline: str = ""
    subtitle: str = ""

    def by_kind(self, kind: str) -> List[Finding]:
        return [f for f in self.findings if f.kind == kind]

    def top(self, n: int = 5) -> List[Finding]:
        return sorted(self.findings, key=lambda f: -f.importance)[:n]


def _pct(x: float) -> str:
    return f"{x:,.1f}%"


# ----------------------------------------------------------------------
# Quality findings
# ----------------------------------------------------------------------
def quality_findings(clean: pd.DataFrame, quality: dict, cfg) -> List[Finding]:
    out = []
    n = len(clean)
    counts = quality["flag_summary"]["count"]
    scale_up = int(counts.get("scale_error_x100", 0))
    scale_dn = int(counts.get("scale_error_div100", 0))
    scale = scale_up + scale_dn
    missing = int(counts.get("missing_code", 0))

    if scale:
        share = scale / n
        out.append(Finding(
            headline=f"{scale:,} observations ({share:.1%}) are unit errors, and they are recoverable",
            detail=(
                f"{scale_up:,} values sit around one hundred times the level of their own item "
                f"and {scale_dn:,} sit around one hundredth of it. The multipliers cluster tightly "
                "rather than forming a continuous tail, which is the signature of a unit of "
                "measurement fault rather than genuine price volatility. Because the mechanism is "
                "known, the true value can be restored by rescaling, so the observations were "
                "repaired rather than deleted. Deleting them would have broken the item continuity "
                "that a matched index depends on."),
            evidence=f"{scale:,} of {n:,} records ({share:.1%}); "
                     f"{len(quality['residual_outliers'])} residual outliers after repair",
            kind="quality",
            importance=90 + min(share * 200, 8),
            action="Review the flagged list before publication. If the collection system can be "
                   "changed, a field-level unit validation at entry would remove this class of "
                   "error at source.",
            chart="quality_bands",
        ))

    mech = quality["missing_mechanisms"]
    if len(mech):
        for r in mech.itertuples():
            months = ", ".join(MONTHS[m] for m in r.calendar_months)
            residual_note = (
                f" A further {r.residual_sporadic_gaps:,} gaps in {r.category} sit outside that "
                "pattern and are scattered rather than systemic."
                if getattr(r, "residual_sporadic_gaps", 0) >= 3 else "")
            if r.mechanism == "seasonal":
                out.append(Finding(
                    headline=f"{r.category} is seasonally unavailable, not seasonally priced",
                    detail=(
                        f"{r.gaps:,} observations are absent, recurring in the same calendar months "
                        f"({months}) across the collection period and affecting every item at once. "
                        "This is a product that leaves the shelf, not a price that falls. That "
                        "distinction matters because a category whose seasonality lives in "
                        "availability needs a seasonal index treatment, whereas one whose "
                        "seasonality lives in price does not." + residual_note),
                    evidence=f"{r.gaps:,} gaps across {r.periods_affected} periods, {months}",
                    kind="quality", importance=80,
                    action=f"Apply an explicit seasonal method to {r.category} and state it. "
                           "Holding the level across the out-of-season gap is defensible, but it "
                           "must be a stated choice rather than a side effect.",
                ))
            elif r.mechanism == "collection":
                out.append(Finding(
                    headline=(f"{r.category} has a collection failure, "
                              f"{pd.Timestamp(r.first):%B %Y} to {pd.Timestamp(r.last):%B %Y}"),
                    detail=(
                        f"Every item in the category is unpriced for {r.periods_affected} "
                        "consecutive periods, then resumes. The pattern does not recur, so this is "
                        "non-response rather than seasonality. Left untreated the index simply "
                        "skips the gap, which understates or overstates the movement across it "
                        "depending on what happened to prices meanwhile." + residual_note),
                    evidence=(f"{r.gaps:,} gaps, {pd.Timestamp(r.first):%b %Y} to "
                                  f"{pd.Timestamp(r.last):%b %Y}, all items affected"),
                    kind="quality", importance=78,
                    action=f"Impute {r.category} across the gap, class mean by default, and report "
                           "the sensitivity of the headline rate to that choice.",
                ))
            else:
                out.append(Finding(
                    headline=f"{r.category} has {r.gaps:,} isolated price gaps",
                    detail="Gaps are scattered across items and periods rather than systemic, "
                           "consistent with ordinary non-response.",
                    evidence=f"{r.gaps:,} gaps across {r.periods_affected} periods",
                    kind="quality", importance=45,
                    action="Class mean imputation is usually adequate for scattered gaps."))

    if missing:
        out.append(Finding(
            headline=f"{missing:,} missing values are coded as a sentinel, not left blank",
            detail=("Treating a sentinel value as a price of nil would drag every affected "
                    "average towards zero and, on a geometric index, make the comparison "
                    "undefined. They have been recoded to unavailable before any calculation."),
            evidence=f"{missing:,} of {n:,} records ({missing / n:.1%})",
            kind="quality", importance=70,
            action="Confirm the sentinel list matches the collection system's conventions."))

    resid = quality["residual_outliers"]
    if len(resid):
        out.append(Finding(
            headline=f"{len(resid)} observations remain unexplained after cleaning",
            detail="These sit outside tolerance of their own item's local level but do not match "
                   "a known fault pattern, so they were left untouched. They may be genuine price "
                   "movements or a fault the current rules do not describe.",
            evidence=f"{len(resid)} records beyond {cfg.quality.residual_tolerance} in log10 units",
            kind="quality", importance=75,
            action="Inspect these manually. An unexplained residual is a candidate for a new "
                   "detection rule.", table=resid.head(25)))
    return out


# ----------------------------------------------------------------------
# Structure findings
# ----------------------------------------------------------------------
def structure_findings(imputed: pd.DataFrame, I: pd.DataFrame, matched: pd.DataFrame) -> List[Finding]:
    out = []
    summary, life = dg.churn(imputed)

    total_items = int(summary["items"].sum())
    per_period = imputed.groupby(["category", "period"])["item_id"].nunique()
    out.append(Finding(
        headline=f"The sample rotates: {total_items} items across the period, "
                 f"{per_period.mean():.1f} priced in a typical category period",
        detail=("Items enter and leave throughout, so the collection is a rotating panel rather "
                "than a fixed basket. This is the structural fact that forces a matched-model "
                "index: comparing whatever happens to be in the sample from one period to the "
                "next would count replacement as price change."),
        evidence=f"{total_items} distinct items, median lifespan "
                 f"{summary['median_lifespan'].median():.0f} periods",
        kind="structure", importance=72, table=summary.reset_index(),
        action="Keep the matched-model comparison. Where replacement is frequent, consider "
               "whether the specification changed alongside the item."))

    # Replacement price level: the substance behind churn.
    # An entrant is compared against the level of its own category in the month
    # it arrives, not against the base period, because otherwise every entrant
    # looks dearer simply by arriving later into a rising series.
    first_period = imputed["period"].min()
    price_col = "price_imputed" if "price_imputed" in imputed.columns else "price_clean"
    level = imputed.groupby(["category", "period"])[price_col].median()
    rows = []
    for cat, d in life.groupby("category"):
        entrants = d[d["first"] > first_period]
        if not len(entrants):
            continue
        ratios = []
        for e in entrants.itertuples():
            ref = level.get((cat, e.first))
            if ref and np.isfinite(ref) and ref > 0:
                ratios.append(e.entry_price / ref)
        if ratios:
            rows.append({"category": cat, "replacements": len(ratios),
                         "median_entry_vs_category": float(np.median(ratios)),
                         "uplift_pct": 100 * (float(np.median(ratios)) - 1)})
    if rows:
        rep = pd.DataFrame(rows).set_index("category").sort_values("uplift_pct", ascending=False)
        top = rep.iloc[0]
        if abs(top["uplift_pct"]) > 8:
            out.append(Finding(
                headline=f"In {rep.index[0]}, replacement items arrive "
                         f"{abs(top['uplift_pct']):.0f}% "
                         f"{'above' if top['uplift_pct'] > 0 else 'below'} the category level "
                         "of the month they enter",
                detail=("The composition of the sample is shifting in price level, not only the "
                        "prices within it. Each entrant is compared against its own category in "
                        "the month it arrives, so this is not simply the effect of joining a "
                        "rising series. A matched index removes this from the measured movement; "
                        "an unmatched average would report it as inflation. The data shows the "
                        "shift, it does not show why it is happening."),
                evidence=" · ".join(f"{c} {r.uplift_pct:+.0f}%" for c, r in rep.head(4).iterrows()),
                kind="structure", importance=76, table=rep.round(2).reset_index(),
                action="Check whether replacements are like-for-like in specification. If they "
                       "are not, the index needs a quality adjustment this tool does not apply."))

    # Thin coverage. Periods where the category is legitimately out of season
    # are excluded: an absent seasonal product is not a coverage failure, and
    # counting it as one buries the real cases.
    price_col = "price_imputed" if "price_imputed" in imputed.columns else "price_clean"
    in_season = (imputed.dropna(subset=[price_col])
                 .groupby(["category", "period"]).size().unstack(0).notna())
    thin = matched.drop(columns=["All items"], errors="ignore")
    thin = thin.where(in_season.reindex_like(thin).fillna(False))
    flagged = (thin < 3)
    low = int(flagged.sum().sum())
    if low > 0:
        worst = flagged.sum().sort_values(ascending=False)
        out.append(Finding(
            headline=f"{low} category periods rest on fewer than three matched items",
            detail=("An index built on two items is a far weaker statistic than one built on six, "
                    "and the difference is invisible in the published series. Out-of-season "
                    "periods are excluded from this count, so these are genuine coverage gaps. "
                    "Coverage should be reported alongside the index rather than buried."),
            evidence=f"worst affected: {worst.index[0]} ({int(worst.iloc[0])} periods)",
            kind="structure", importance=62, table=worst[worst > 0].rename("periods").reset_index(),
            action="Publish the matched count alongside the index, and consider suppressing "
                   "periods below a stated minimum."))
    return out


# ----------------------------------------------------------------------
# Trend findings
# ----------------------------------------------------------------------
def trend_findings(I: pd.DataFrame, yoy: pd.DataFrame) -> List[Finding]:
    out = []
    cats = [c for c in I.columns if c != "All items"]
    years = (I.index[-1] - I.index[0]).days / 365.25
    ann = ((I.iloc[-1] / I.iloc[0]) ** (1 / years) - 1) * 100

    if "All items" in I.columns:
        agg = I["All items"]
        out.append(Finding(
            headline=f"Prices rose {agg.iloc[-1] - 100:.0f}% overall, "
                     f"{ann['All items']:.1f}% a year",
            detail=(f"The all-items index reaches {agg.iloc[-1]:.1f} at "
                    f"{I.index[-1]:%B %Y} against a base of 100 at {I.index[0]:%B %Y}. "
                    "With no expenditure weights supplied this aggregate is an equally weighted "
                    "geometric mean of the category indices, so it is indicative of direction and "
                    "broad magnitude rather than an authoritative headline rate."),
            evidence=f"index {agg.iloc[-1]:.1f} at {I.index[-1]:%b %Y}, "
                     f"{ann['All items']:.2f}% annualised",
            kind="trend", importance=95, chart="index",
            action="Supply expenditure weights to turn this into a publishable headline rate."))

        if "All items" in yoy.columns and yoy["All items"].notna().any():
            s = yoy["All items"].dropna()
            peak, peak_at = s.max(), s.idxmax()
            latest = s.iloc[-1]
            out.append(Finding(
                headline=f"Inflation peaked at {_pct(peak)} in {peak_at:%B %Y} and has since "
                         f"eased to {_pct(latest)}",
                detail=("The twelve period rate traces a clear cycle rather than a steady trend. "
                        f"The peak sits in {peak_at:%B %Y} and the rate at the end of the series "
                        f"is {_pct(latest)}. A single average over the whole period would hide "
                        "this shape entirely."),
                evidence=f"peak {_pct(peak)} ({peak_at:%b %Y}), latest {_pct(latest)}, "
                         f"trough {_pct(s.min())} ({s.idxmin():%b %Y})",
                kind="trend", importance=93, chart="inflation"))

    ranked = ann[cats].sort_values(ascending=False)
    fastest, slowest = ranked.index[0], ranked.index[-1]
    spread = ranked.iloc[0] - ranked.iloc[-1]
    out.append(Finding(
        headline=f"Category rates diverge by {spread:.1f} percentage points a year, "
                 f"from {fastest} to {slowest}",
        detail=(f"{fastest} runs at {ranked.iloc[0]:.1f}% a year while {slowest} runs at "
                f"{ranked.iloc[-1]:.1f}%. Divergence of this size means the aggregate conceals "
                "more than it reveals, and any weighting decision will move the headline "
                "materially."),
        evidence=" · ".join(f"{c} {v:.1f}%" for c, v in ranked.head(3).items())
                 + f" … {slowest} {ranked.iloc[-1]:.1f}%",
        kind="trend", importance=85,
        table=pd.DataFrame({"Final level": I.iloc[-1][cats].round(1),
                            "Annualised %": ranked.round(2)}).reset_index(names="Category"),
        chart="index"))

    deflating = ranked[ranked < 0]
    if len(deflating):
        names = ", ".join(deflating.index)
        out.append(Finding(
            headline=f"{names} {'is' if len(deflating) == 1 else 'are'} in sustained deflation",
            detail=(f"Prices fall at {abs(deflating.iloc[0]):.1f}% a year over the full period, "
                    "against a rising aggregate. A category moving persistently against the "
                    "general trend usually reflects something structural in the product rather "
                    "than the price environment, though this data cannot establish what."),
            evidence=" · ".join(f"{c} {v:.1f}% a year" for c, v in deflating.items()),
            kind="trend", importance=82, chart="index"))
    return out


# ----------------------------------------------------------------------
# Seasonal findings
# ----------------------------------------------------------------------
def seasonal_findings(I: pd.DataFrame, quality: dict, threshold: float = 10.0) -> List[Finding]:
    out = []
    seas = dg.seasonality(I)
    if not len(seas):
        return out
    strong = seas[seas["amplitude_pct"] >= threshold]
    strong = strong[strong.index != "All items"]
    if len(strong):
        lead = strong.iloc[0]
        others = ", ".join(strong.index[1:4])
        out.append(Finding(
            headline=f"{strong.index[0]} swings {lead['amplitude_pct']:.0f}% within the year, "
                     f"peaking in {MONTHS[int(lead['peak_month'])]}",
            detail=(f"{len(strong)} categories show a within-year cycle of at least "
                    f"{threshold:.0f}%" + (f", including {others}. " if others else ". ") +
                    "The amplitude is measured on the index rather than on raw prices, because "
                    "raw prices confound the seasonal cycle with changes in which items happen "
                    "to be in the sample. A cycle of this size means any month-on-month "
                    "comparison is close to meaningless without adjustment."),
            evidence=" · ".join(f"{c} {r.amplitude_pct:.0f}% (peak "
                                f"{MONTHS[int(r.peak_month)][:3]})"
                                for c, r in strong.head(4).iterrows()),
            kind="seasonal", importance=80, table=seas.reset_index(), chart="seasonality",
            action="Compare twelve period rates rather than consecutive periods, or apply "
                   "seasonal adjustment before publishing a short-term movement."))

    # A category seasonal in availability but flat in price is a different problem
    mech = quality["missing_mechanisms"]
    if len(mech):
        for r in mech[mech["mechanism"] == "seasonal"].itertuples():
            if r.category in seas.index and seas.loc[r.category, "amplitude_pct"] < threshold:
                out.append(Finding(
                    headline=f"{r.category} is seasonal in availability but flat in price",
                    detail=("The category disappears for part of every year, yet while on sale "
                            "its price barely moves within the season. These are two different "
                            "methodological problems wearing the same word, and applying a "
                            "price-cycle adjustment here would correct for a cycle that does "
                            "not exist."),
                    evidence=f"in-season amplitude {seas.loc[r.category, 'amplitude_pct']:.1f}%, "
                             f"absent {r.gaps:,} observations",
                    kind="seasonal", importance=74,
                    action="Treat as a seasonal item by availability. Consider whether the "
                           "category should contribute to the aggregate out of season at all."))
    return out


# ----------------------------------------------------------------------
# Method findings
# ----------------------------------------------------------------------
def method_findings(imputed: pd.DataFrame, I: pd.DataFrame, cfg) -> List[Finding]:
    out = []

    comp = dg.unmatched_comparison(imputed, I).dropna()
    if len(comp):
        big = comp.reindex(comp["difference_pp"].abs().sort_values(ascending=False).index)
        top = big.iloc[0]
        if abs(top["difference_pp"]) >= 2:
            over = big[big["difference_pp"] < -2]
            under = big[big["difference_pp"] > 2]
            parts = []
            if len(over):
                parts.append(f"overstates {', '.join(over.index[:3])}")
            if len(under):
                parts.append(f"understates {', '.join(under.index[:3])}")
            out.append(Finding(
                headline=f"Ignoring item replacement would misstate {big.index[0]} by "
                         f"{abs(top['difference_pp']):.0f} percentage points",
                detail=("Comparing a simple average of prices from one period to the next "
                        f"{' and '.join(parts)} against the matched comparison. Where "
                        "replacements enter dearer than the items they replace, the naive "
                        "measure counts the change in the sample as inflation; where they enter "
                        "cheaper it does the reverse. Over a long collection this choice moves "
                        "the answer further than any outlier rule."),
                evidence=" · ".join(f"{c} {r.difference_pp:+.1f}pp" for c, r in big.head(4).iterrows()),
                kind="method", importance=88, table=big.reset_index(), chart="method",
                action="Keep the matched comparison. If a naive average is required for "
                       "continuity with an older series, publish both."))

    sens = dg.method_sensitivity(imputed, cfg.index)
    if "max_spread" in sens.columns and len(sens):
        spread = sens.loc["All items", "max_spread"] if "All items" in sens.index \
            else sens["max_spread"].max()
        cur = cfg.index.formula
        out.append(Finding(
            headline=f"The choice of elementary formula moves the result by up to "
                     f"{spread:.1f} index points",
            detail=(f"The same cleaned data aggregated with Jevons, Dutot and Carli gives a "
                    f"spread of {spread:.1f} points at the final period. Carli sits highest, "
                    "which is its known upward bias: it fails the time reversal test, so "
                    "chaining it forward and back does not return to the starting point. "
                    f"This run uses {cur.title()}, which satisfies time reversal and is "
                    "invariant to the units in which each item is quantified."),
            evidence=f"spread {spread:.1f} points at {I.index[-1]:%b %Y}",
            kind="method", importance=70, table=sens.reset_index(names="Category"),
            action="Retain a geometric formula unless the items in an aggregate are genuinely "
                   "homogeneous and comparably quantified."))

    drift = dg.chain_drift(imputed, cfg.index)
    impossible = drift[drift["fixed_base"].isna()]
    if len(impossible):
        names = ", ".join(impossible.index[:4])
        out.append(Finding(
            headline=f"A fixed base index is impossible for {len(impossible)} categories",
            detail=(f"In {names}, no item survives from the base period to the last, so there is "
                    "no direct comparison to make. Chaining is not a stylistic preference here, "
                    "it is the only way to link the two ends of the series. That also means the "
                    "series carries whatever chain drift the rotation introduces."),
            evidence=f"{len(impossible)} of {len(drift)} categories have no surviving item",
            kind="method", importance=77, table=drift.reset_index(names="Category"),
            action="Accept chaining and monitor drift where the category is strongly seasonal."))
    else:
        worst = drift["drift_pp"].abs().idxmax()
        if abs(drift.loc[worst, "drift_pp"]) > 3:
            out.append(Finding(
                headline=f"Chaining introduces {drift.loc[worst, 'drift_pp']:+.1f} points of "
                         f"drift in {worst}",
                detail="The chained level differs from a direct fixed base comparison by more "
                       "than three points, which in a seasonal series usually means the chain is "
                       "accumulating drift rather than measuring price change.",
                evidence=f"chained {drift.loc[worst, 'chained']:.1f} against fixed base "
                         f"{drift.loc[worst, 'fixed_base']:.1f}",
                kind="method", importance=68, table=drift.reset_index(names="Category")))
    return out


# ----------------------------------------------------------------------
# Assembly
# ----------------------------------------------------------------------
def build_narrative(res: dict) -> Narrative:
    """Turn a pipeline result into a ranked, written narrative."""
    cfg = res["config"]
    clean, quality = res["clean"], res["quality"]
    imputed, I, yoy = res["imputed"], res["indices"], res["inflation"]

    findings = []
    findings += quality_findings(clean, quality, cfg)
    findings += structure_findings(imputed, I, res["matched_counts"])
    findings += trend_findings(I, yoy)
    findings += seasonal_findings(I, quality)
    findings += method_findings(imputed, I, cfg)
    findings.sort(key=lambda f: -f.importance)

    if "All items" in I.columns:
        agg = I["All items"]
        years = (I.index[-1] - I.index[0]).days / 365.25
        rate = ((agg.iloc[-1] / agg.iloc[0]) ** (1 / years) - 1) * 100
        headline = (f"Prices rose {agg.iloc[-1] - 100:.0f}% over "
                    f"{years:.0f} years, {rate:.1f}% a year")
    else:
        headline = "Price analysis"

    n_scale = int(quality["flag_summary"]["count"].get("scale_error_x100", 0) +
                  quality["flag_summary"]["count"].get("scale_error_div100", 0))
    subtitle = (f"{len(clean):,} observations · {clean.item_id.nunique()} items · "
                f"{clean.category.nunique()} categories · "
                f"{clean.period.min():%b %Y} to {clean.period.max():%b %Y} · "
                f"{n_scale} data faults repaired")

    return Narrative(findings=findings, headline=headline, subtitle=subtitle)
