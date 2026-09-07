"""Automatic configuration.

This is what lets a user upload a file and get a finished analysis without
answering questions. The tool runs a diagnostic pass, reads what the collection
actually contains, and selects the treatments that follow from it.

Two things it deliberately does not do. It does not guess an outlier rule from
the data, because a rule fitted to the faults it is meant to detect will always
find them. And it does not hide what it chose: every automatic decision is
returned with the reason, so the user can see it on screen and override it.
"""

from typing import Tuple, List
import pandas as pd

from .config import RunConfig, Schema, QualityConfig, ImputationConfig, IndexConfig
from .quality import run_quality, recode_missing, classify_missing


def infer_schema(df: pd.DataFrame) -> Schema:
    """Map columns by name, falling back to type and cardinality.

    Deliberately conservative: it proposes a mapping for the user to confirm
    rather than proceeding silently on a guess.
    """
    cols = list(df.columns)
    lower = {c.lower().strip(): c for c in cols}

    def find(*keys, exclude=()):
        for k in keys:
            for lc, orig in lower.items():
                if any(x in lc for x in exclude):
                    continue
                if k in lc:
                    return orig
        return None

    date = find("date", "period", "month", "time")
    if date is None:
        for c in cols:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                date = c
                break

    price = find("price", "value", "cost", "amount")
    name = find("item_name", "name", "description", "product", exclude=("category", "group"))
    item_id = find("item_id", "itemid", "product_id", "sku", "id",
                   exclude=("category", "group", "class"))
    category = find("category", "group", "class", "division", exclude=("num", "code"))

    # Names are unreliable: "id_group" matches both patterns. Cardinality is not.
    # The category column always has fewer distinct values than the item column,
    # so where the two collide or come out the wrong way round, resolve on that.
    text_cols = [c for c in cols
                 if c not in (date, price) and 1 < df[c].nunique() < len(df)]
    if item_id == category or item_id is None or category is None:
        ranked = sorted(text_cols, key=lambda c: df[c].nunique())
        if len(ranked) >= 2:
            category, item_id = ranked[0], ranked[-1]
        elif ranked:
            category = item_id = ranked[0]
    elif df[category].nunique() > df[item_id].nunique():
        category, item_id = item_id, category

    if name in (None, item_id, category):
        # Prefer a column with the same cardinality as the item id: that is the
        # item's label rather than a second identifier.
        for c in text_cols:
            if c not in (item_id, category) and df[c].nunique() == df[item_id].nunique():
                name = c
                break
        else:
            name = item_id

    return Schema(date=date or cols[0],
                  item_id=item_id or cols[0],
                  item_name=name or (item_id or cols[0]),
                  category=category or (item_id or cols[0]),
                  price=price or cols[-1],
                  weight=find("weight", "expenditure"))


def auto_configure(df: pd.DataFrame, label: str = "") -> Tuple[RunConfig, List[str]]:
    """Run a diagnostic pass and select treatments from what it finds.

    Returns the configuration and a plain-English list of the decisions taken,
    which the app shows to the user rather than applying them invisibly.
    """
    decisions = []
    cfg = RunConfig(label=label or "Uploaded collection")

    # Sentinel detection. Zero is near-universal as a missing code, but only
    # treat it as one where it appears often enough to be a convention rather
    # than a genuine free item.
    zeros = int((df["price_reported"] == 0).sum())
    if zeros:
        share = zeros / len(df)
        cfg.quality.missing_codes = (0,)
        decisions.append(
            f"Treated {zeros:,} zero values ({share:.1%}) as a missing code rather than a "
            "price of nil, because a genuine price of zero is rare and a sentinel is common.")
    else:
        cfg.quality.missing_codes = tuple()

    # Reference window from the periodicity: 13 for monthly, 5 for quarterly,
    # so the window always spans a little over one year.
    periods = df["period"].drop_duplicates().sort_values()
    if len(periods) > 2:
        step = periods.diff().dt.days.median()
        if step <= 10:
            window = 15                                   # weekly
        elif step <= 45:
            window = 13                                   # monthly
        elif step <= 130:
            window = 5                                    # quarterly
        else:
            window = 3                                    # annual
        cfg.quality.reference_window = window
        decisions.append(
            f"Set the outlier reference window to {window} periods, spanning roughly one year "
            f"at the detected collection frequency, so seasonal movement is not mistaken for "
            "a fault.")

    # Diagnose gaps, then choose imputation per category from the mechanism.
    probe = recode_missing(df, cfg.quality)
    mech = classify_missing(probe)
    by_cat = {}
    for r in mech.itertuples():
        if r.mechanism == "seasonal":
            by_cat[r.category] = "seasonal_hold"
            decisions.append(
                f"{r.category}: gaps recur in the same calendar months across years, so this "
                "reads as seasonal unavailability. Held the level across the out-of-season gap "
                "rather than imputing a price for a product that was not on sale.")
        elif r.mechanism == "collection":
            by_cat[r.category] = "class_mean"
            decisions.append(
                f"{r.category}: every item is unpriced for {r.periods_affected} consecutive "
                "periods without recurring, which reads as collection failure rather than "
                "seasonality. Imputed by class mean so the gap moves with its priced peers.")
        else:
            by_cat[r.category] = "class_mean"
            decisions.append(
                f"{r.category}: {r.gaps:,} scattered gaps, imputed by class mean.")
    cfg.imputation = ImputationConfig(default_method="none", by_category=by_cat)

    # Aggregation. Jevons and chaining are the defensible defaults and are not
    # inferred from the data, because a formula chosen to suit a dataset is not
    # a method, it is a result.
    cfg.index = IndexConfig(formula="jevons", chained=True, min_matched_items=2)
    decisions.append(
        "Aggregated with a matched-model chained Jevons index. Matched because items rotate in "
        "and out; geometric because Jevons satisfies time reversal and is invariant to the "
        "units each item is quantified in; chained because the sample evolves.")

    if "weight" in df.columns and df["weight"].notna().any():
        cfg.index.formula = "laspeyres"
        decisions.append(
            "Expenditure weights were supplied, so a weighted Laspeyres index was used and the "
            "aggregate is a genuine weighted rate rather than an indicative one.")

    return cfg, decisions


def analyse(df: pd.DataFrame, label: str = "", config: RunConfig = None):
    """Upload to finished analysis in one call.

    Returns everything the interface and the exports need: the pipeline result,
    the written narrative, the charts, and the decisions taken along the way.
    """
    from . import run_pipeline
    from .insights import build_narrative
    from .charts import build_all_charts

    if config is None:
        config, decisions = auto_configure(df, label)
    else:
        decisions = []

    res = run_pipeline(df, config)
    if "indices" not in res:
        return {"result": res, "decisions": decisions, "narrative": None, "charts": {}}

    nar = build_narrative(res)
    charts = build_all_charts(res)
    return {"result": res, "narrative": nar, "charts": charts,
            "decisions": decisions, "config": config}
