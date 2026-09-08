"""Value-level quality.

Two principles, both defensible to a panel:

1. Diagnose the mechanism before choosing the treatment. A zero that means
   "out of season" and a zero that means "collector could not visit" are
   different problems and must not share a rule.

2. Never modify silently. Every altered observation carries a flag naming what
   was done and why, so any decision can be inspected, disputed and reversed.
"""

import numpy as np
import pandas as pd

from .config import QualityConfig


FLAG_NONE = "none"
FLAG_MISSING = "missing_code"
FLAG_SCALE_UP = "scale_error_x100"      # value was 100x too large
FLAG_SCALE_DOWN = "scale_error_div100"  # value was 100x too small


def local_reference(s: pd.Series, window: int, min_periods: int) -> pd.Series:
    """Centred rolling median within an item.

    Centred, so it tracks trend rather than lagging it. Median, so a single
    faulty observation cannot drag the reference towards itself. A whole-period
    median would be wrong here: over a long panel, genuine late prices would be
    flagged as outliers simply because the series trends.
    """
    return s.rolling(window, center=True, min_periods=min_periods).median()


def recode_missing(df: pd.DataFrame, cfg: QualityConfig) -> pd.DataFrame:
    """Sentinel values become NaN. They are not prices."""
    out = df.copy()
    out["price"] = out["price_reported"]
    hit = out["price"].isin(cfg.missing_codes)
    out.loc[hit, "price"] = np.nan
    out["flag"] = FLAG_NONE
    out.loc[hit | out["price_reported"].isna(), "flag"] = FLAG_MISSING
    return out


def classify_missing(df: pd.DataFrame, min_share: float = 0.8) -> pd.DataFrame:
    """Label the mechanism behind each gap, per category.

    seasonal   the gap recurs in the same calendar months across years and
               affects essentially every item at once
    collection the gap hits every item in a category simultaneously but does
               not recur seasonally, i.e. the collection failed
    sporadic   isolated item-level gaps

    This is a diagnostic that proposes a classification. A human confirms it.
    """
    rows = []
    for cat, d in df.groupby("category"):
        gaps = d[d["flag"] == FLAG_MISSING]
        if gaps.empty:
            continue
        items = d["item_id"].nunique()

        # share of a category-period's items that are missing
        per_period = (gaps.groupby("period").size()
                      / d.groupby("period")["item_id"].nunique())
        systemic = per_period[per_period >= min_share]

        # Classify on the systemic periods alone: a category can carry both a
        # systemic block (seasonal or collection) and unrelated scattered
        # item-level gaps at the same time, and the two must not be blended
        # when describing what the systemic block actually looks like.
        systemic_gaps = gaps[gaps["period"].isin(systemic.index)]
        by_month = systemic_gaps.groupby(systemic_gaps["period"].dt.month).size()
        months_hit = set(by_month.index)
        years_hit = systemic_gaps["period"].dt.year.nunique()
        total_years = d["period"].dt.year.nunique()

        # Recurrence across years can only be demonstrated with at least two
        # years on hand. Below that, a systemic gap confined to a handful of
        # calendar months is the best evidence available, and the safer
        # reading: calling it "collection failure" instead would trigger
        # class-mean imputation, fabricating prices for a product that may
        # simply not be on sale yet.
        years_needed = max(2, 0.6 * total_years) if total_years >= 2 else 1
        recurs = years_hit >= years_needed and len(months_hit) <= 8

        if len(systemic) and recurs:
            mechanism = "seasonal"
        elif len(systemic):
            mechanism = "collection"
        else:
            mechanism = "sporadic"

        # For a seasonal or collection label, report the systemic block only:
        # that is the pattern the label describes. Any remaining scattered
        # gaps in the same category are sporadic noise and are not evidence
        # for this finding, even though they share a category.
        evidence_gaps = gaps if mechanism == "sporadic" else systemic_gaps

        rows.append({
            "category": cat,
            "mechanism": mechanism,
            "gaps": len(evidence_gaps),
            "items": items,
            "periods_affected": evidence_gaps["period"].nunique(),
            "calendar_months": sorted(months_hit) if mechanism != "sporadic" else sorted(
                set(gaps["period"].dt.month)),
            "first": evidence_gaps["period"].min(),
            "last": evidence_gaps["period"].max(),
            "residual_sporadic_gaps": len(gaps) - len(evidence_gaps),
        })
    return pd.DataFrame(rows)


def detect_scale_errors(df: pd.DataFrame, cfg: QualityConfig) -> pd.DataFrame:
    """Flag observations that sit a clean order of 100 away from their own
    item's local level.

    The threshold is a band, not a tail cut. A unit-of-measurement fault has a
    known multiplier; a genuinely volatile price does not cluster at exactly
    100x. Requiring the deviation to fall inside [low, high] rather than simply
    exceed a cutoff is what separates the two.
    """
    out = df.copy()
    ref = out.groupby("item_id")["price"].transform(
        lambda s: local_reference(s, cfg.reference_window, cfg.min_reference_periods))
    ref = ref.fillna(out.groupby("item_id")["price"].transform("median"))
    out["reference"] = ref

    with np.errstate(divide="ignore", invalid="ignore"):
        dev = np.log10(out["price"] / out["reference"])
    out["log10_deviation"] = dev

    too_high = dev.between(cfg.scale_log10_low, cfg.scale_log10_high)
    too_low = dev.between(-cfg.scale_log10_high, -cfg.scale_log10_low)
    out.loc[too_high, "flag"] = FLAG_SCALE_UP
    out.loc[too_low, "flag"] = FLAG_SCALE_DOWN
    return out


def repair(df: pd.DataFrame, cfg: QualityConfig) -> pd.DataFrame:
    """Repair rather than delete.

    Once the mechanism is known to be a factor of 100, the true value is
    recoverable. Deleting instead would break item continuity in exactly the
    periods a matched index depends on, so removal costs more than it saves.
    """
    out = df.copy()
    out["price_clean"] = out["price"]
    if not cfg.repair_scale_errors:
        out.loc[out["flag"].isin([FLAG_SCALE_UP, FLAG_SCALE_DOWN]), "price_clean"] = np.nan
        return out
    out.loc[out["flag"] == FLAG_SCALE_UP, "price_clean"] = out["price"] / 100
    out.loc[out["flag"] == FLAG_SCALE_DOWN, "price_clean"] = out["price"] * 100
    return out


def residual_check(df: pd.DataFrame, cfg: QualityConfig) -> pd.DataFrame:
    """Evidence that the repair rule was correctly specified.

    Claiming data was cleaned is weak. Showing that no observation remains
    beyond tolerance of its own item's local level is the proof, and it is also
    the check that catches an over-aggressive threshold.
    """
    ref = df.groupby("item_id")["price_clean"].transform(
        lambda s: local_reference(s, cfg.reference_window, cfg.min_reference_periods))
    ref = ref.fillna(df.groupby("item_id")["price_clean"].transform("median"))
    with np.errstate(divide="ignore", invalid="ignore"):
        resid = np.log10(df["price_clean"] / ref).abs()
    return df.loc[resid > cfg.residual_tolerance].assign(residual=resid[resid > cfg.residual_tolerance])


def run_quality(df: pd.DataFrame, cfg: QualityConfig = None):
    """Full quality pass. Returns the cleaned frame plus everything needed to
    justify it."""
    cfg = cfg or QualityConfig()
    out = recode_missing(df, cfg)
    mechanisms = classify_missing(out)
    out = detect_scale_errors(out, cfg)
    out = repair(out, cfg)
    residuals = residual_check(out, cfg)

    summary = (out["flag"].value_counts().rename("count").to_frame()
               .assign(share=lambda x: (x["count"] / len(out)).round(4)))

    # flag_summary counts observations *detected* as scale errors, which
    # detect_scale_errors sets regardless of cfg.repair_scale_errors. Whether
    # they were actually repaired (rescaled) or dropped (nulled) depends on
    # that config flag, and callers displaying "faults repaired" need the
    # count that reflects what actually happened, not what was found.
    n_scale_detected = int(summary["count"].get(FLAG_SCALE_UP, 0) +
                           summary["count"].get(FLAG_SCALE_DOWN, 0))
    n_scale_repaired = n_scale_detected if cfg.repair_scale_errors else 0

    return out, {"flag_summary": summary,
                 "scale_errors_detected": n_scale_detected,
                 "scale_errors_repaired": n_scale_repaired,
                 "missing_mechanisms": mechanisms,
                 "residual_outliers": residuals,
                 "config": cfg}
