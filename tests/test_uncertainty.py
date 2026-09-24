"""Phase 9a: sampling uncertainty, methodological sensitivity, and where
each appears."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from dbtarget import database_url
from matplotlib.figure import Figure
from streamlit.testing.v1 import AppTest

from pricelab import build_narrative, infer_schema, run_pipeline, standardise
from pricelab.core import db
from pricelab.core.config import RunConfig, get_settings
from pricelab.engine import sensitivity as se
from pricelab.engine import uncertainty as un
from pricelab.reporting import charts

REPO_ROOT = Path(__file__).resolve().parents[1]
PRICES = REPO_ROOT / "supermarket_price_collection.xlsx"
needs_prices = pytest.mark.skipif(not PRICES.exists(), reason="fixture workbook not present")
DESIGN = un.SamplingDesign(cluster="outlet", strata="stratum", declared_by="the test")


# ---------------------------------------------------------------------
# A population whose true movement is known
# ---------------------------------------------------------------------
def _population(seed: int = 1) -> pd.DataFrame:
    """Three strata (one category each), 200 outlets per stratum, 5 items
    per outlet. An item's log price relative is its stratum's mean movement
    plus an outlet effect shared by every item in the outlet (sd 0.04) plus
    its own noise (sd 0.02): the clustering that makes quote-level
    resampling wrong."""
    rng = np.random.default_rng(seed)
    rows = []
    for h, mu in enumerate((0.02, 0.05, -0.01)):
        outlet_effect = rng.normal(0.0, 0.04, 200)
        for k in range(200):
            for i in range(5):
                rows.append((f"S{h}", f"S{h}O{k}", f"S{h}O{k}I{i}", f"C{h}",
                             mu + outlet_effect[k] + rng.normal(0.0, 0.02)))
    return pd.DataFrame(rows, columns=["stratum", "outlet", "item_id", "category",
                                       "log_relative"])


def _sample(population: pd.DataFrame, rng: np.random.Generator, per_stratum: int = 30
            ) -> pd.DataFrame:
    chosen = np.concatenate([
        rng.choice(population.loc[population["stratum"] == h, "outlet"].unique(), per_stratum,
                   replace=False) for h in ("S0", "S1", "S2")])
    return population[population["outlet"].isin(chosen)]


def test_the_design_based_interval_covers_the_truth_at_its_nominal_rate():
    """400 independent stratified cluster samples (30 of 200 outlets per
    stratum), each with a 399-replicate bootstrap: the 95% interval should
    contain the population's true movement in about 95% of them.

    The tolerance: with 400 samples a coverage estimate has a standard error
    of sqrt(0.95 x 0.05 / 400) = 1.1 points, so 92% to 98% is roughly
    +-2.75 standard errors -- tight enough to catch an interval that is
    systematically too narrow or too wide, loose enough not to fail on
    sampling noise in the check itself. Measured: 95.5%. The naive interval,
    resampling quotes, covers the truth only about two times in three, which
    is the understatement the design-based bootstrap exists to prevent."""
    population = _population()
    truth = (np.exp(population.groupby("category")["log_relative"].mean().mean()) - 1) * 100
    rng = np.random.default_rng(5)
    covered = naive_covered = 0
    ratios = []
    for r in range(400):
        comparison = un.compare_with_naive(_sample(population, rng), DESIGN, replicates=399,
                                           seed=r)
        covered += comparison.design_based.lower_pct <= truth <= comparison.design_based.upper_pct
        naive_covered += comparison.naive.lower_pct <= truth <= comparison.naive.upper_pct
        ratios.append(comparison.width_ratio)
    assert 0.92 <= covered / 400 <= 0.98
    assert naive_covered / 400 < 0.80
    assert np.mean(ratios) > 1.5


def test_the_design_based_interval_is_wider_than_the_naive_one_and_says_by_how_much():
    comparison = un.compare_with_naive(_sample(_population(), np.random.default_rng(0)), DESIGN,
                                       replicates=999)
    assert comparison.design_based.width_pp > comparison.naive.width_pp
    assert comparison.width_ratio > 1.5
    assert f"{comparison.width_ratio:.2f} times narrower" in comparison.statement
    assert "not published" in comparison.statement
    assert comparison.naive.label.count("NAIVE") == 1


def test_no_design_means_no_interval():
    with pytest.raises(un.DesignUnknown, match="no sampling design has been declared"):
        un.bootstrap_interval(_population().head(50), None)


def test_a_stratum_with_one_cluster_is_refused_rather_than_given_zero_variance():
    sample = _sample(_population(), np.random.default_rng(0))
    lonely = sample[(sample["stratum"] != "S2") | (sample["outlet"] == sample.loc[
        sample["stratum"] == "S2", "outlet"].iloc[0])]
    with pytest.raises(un.UncertaintyError, match="single cluster"):
        un.bootstrap_interval(lonely, DESIGN)


def test_the_interval_label_says_what_it_is_and_what_it_is_not():
    result = un.bootstrap_interval(_sample(_population(), np.random.default_rng(0)), DESIGN,
                                   replicates=199)
    assert "sampling uncertainty only" in result.label
    assert "design assumed" in result.label and "declared by the test" in result.label
    assert "not how much it would move under a different method" in result.label


# ---------------------------------------------------------------------
# Sensitivity on the bundled collection
# ---------------------------------------------------------------------
@pytest.fixture(scope="module")
def collection() -> pd.DataFrame:
    if not PRICES.exists():
        pytest.skip("fixture workbook not present")
    raw = pd.read_excel(PRICES)
    return standardise(raw, infer_schema(raw))


@pytest.fixture(scope="module")
def spread(collection) -> se.SensitivityResult:
    return se.sensitivity(collection, RunConfig())


@needs_prices
def test_the_sensitivity_spread_on_the_fixture_names_its_extremes(spread):
    """On the bundled collection, All items in Dec 2025 (Jan 2015 = 100) is
    135.60 as published. Across 12 alternatives it runs from 118.12, with
    overall-mean imputation, to 138.57, with the Carli formula: 20.45
    points. Imputation dominates: carrying prices forward or filling from
    the overall mean moves the headline by 17 points, the formula by 3, the
    aggregation by 2, the multilateral window and the seasonal treatment by
    under one."""
    assert spread.baseline == pytest.approx(135.598, abs=0.01)
    assert spread.low[:2] == ("imputation method", "overall_mean")
    assert spread.high[:2] == ("elementary formula", "carli")
    assert spread.range_points == pytest.approx(20.45, abs=0.02)
    assert len(spread.computed) == 12
    assert "not a confidence interval" in spread.label
    assert "(imputation method: overall_mean)" in spread.label
    assert "(elementary formula: carli)" in spread.label
    reasons = spread.table.loc[spread.table["status"] == "not applicable", "reason"]
    assert reasons.str.contains("quantities").any()
    assert reasons.str.contains("no replacement was valued").any()


@needs_prices
def test_the_spread_is_labelled_a_lower_bound_wherever_it_appears(spread):
    """One choice is varied at a time, so interactions are excluded: the
    label, the chart title and the page say the range is a lower bound."""
    assert se.LOWER_BOUND in spread.label
    assert "interactions between choices are excluded" in se.LOWER_BOUND
    assert "true methodological range is wider" in se.LOWER_BOUND
    title = charts.sensitivity_chart(spread).axes[0].get_title(loc="left")
    assert "a lower bound, one choice varied at a time" in title
    source = (REPO_ROOT / "pages" / "uncertainty.py").read_text("utf-8")
    assert "se.LOWER_BOUND" in source


@needs_prices
def test_the_imputed_share_of_the_aggregate_is_reported_beside_the_table(spread):
    """The published run imputes nothing; a tenth of the December 2025
    aggregate (Strawberries, out of season) is simply missing. The two
    imputation settings that move the headline by about seventeen points
    fill exactly that tenth, 6.1% of the aggregate over the whole run."""
    assert spread.imputed.final == 0.0 and spread.imputed.run == 0.0
    assert spread.imputed.missing_final == pytest.approx(0.1)
    rows = spread.table.set_index("setting")
    for method in ("carry_forward", "overall_mean"):
        assert rows.loc[method, "imputed_share_final"] == pytest.approx(0.1)
        assert rows.loc[method, "imputed_share_run"] == pytest.approx(0.0609, abs=5e-4)
        assert rows.loc[method, "difference_points"] < -16.5
    assert rows.loc["dutot", "imputed_share_final"] == 0.0
    statement = spread.imputation_statement
    assert statement.startswith("Imputed share of the aggregate in the published run: 0.0%")
    assert "the most any of them fills is 10.0% of the aggregate" in statement


@needs_prices
def test_every_dimension_is_tried_or_says_why_not(spread):
    assert set(spread.table["dimension"]) == set(se.DIMENSIONS)


# ---------------------------------------------------------------------
# Never on one axis
# ---------------------------------------------------------------------
def test_a_sensitivity_range_and_a_confidence_interval_cannot_share_an_axis():
    fig = Figure()
    ax = fig.add_subplot(111)
    charts.mark(ax.plot([1.0, 2.0], [0, 0], label="95% interval"), "percent_change",
                band="sampling uncertainty")
    charts.mark(ax.plot([0.5, 3.0], [0, 0], label="method range"), "percent_change",
                band="methodological sensitivity")
    with pytest.raises(charts.ChartUnitError, match="must never share an axis"):
        charts.check_figure(fig)


@needs_prices
def test_the_two_charts_are_separate_figures_each_labelled(spread):
    result = un.bootstrap_interval(_sample(_population(), np.random.default_rng(0)), DESIGN,
                                   replicates=199)
    interval_fig = charts.interval_chart(result)
    sensitivity_fig = charts.sensitivity_chart(spread)
    assert interval_fig is not sensitivity_fig
    assert "Sampling uncertainty" in interval_fig.axes[0].get_title(loc="left")
    assert "not a confidence interval" in sensitivity_fig.axes[0].get_title(loc="left")
    for fig in (interval_fig, sensitivity_fig):
        charts.check_figure(fig)


# ---------------------------------------------------------------------
# Every headline carries its uncertainty or says it has none
# ---------------------------------------------------------------------
HEADLINE_PATTERN = re.compile(
    r"\.metric\(\s*f?[\"'][^\"']*(All items|index|rate|headline|change)|\[\"indices\"\]",
    re.IGNORECASE)


def test_every_page_showing_a_headline_is_in_the_inventory_and_calls_the_helper():
    from pages import common

    pages_dir = REPO_ROOT / "pages"
    showing = {path.stem for path in pages_dir.glob("*.py")
               if path.stem != "common" and HEADLINE_PATTERN.search(path.read_text("utf-8"))}
    unlisted = showing - set(common.HEADLINE_SURFACES) - set(common.HEADLINE_EXEMPT)
    assert not unlisted, (f"{sorted(unlisted)} show a headline figure without being in "
                          "pages/common.HEADLINE_SURFACES or HEADLINE_EXEMPT")
    for page in common.HEADLINE_SURFACES:
        source = (pages_dir / f"{page}.py").read_text("utf-8")
        assert "common.show_uncertainty(" in source, page
    assert not set(common.HEADLINE_SURFACES) & set(common.HEADLINE_EXEMPT)


# ---------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------
@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "uncertainty.db"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import audit, ledger, registry, security  # noqa: F401  register tables
    db.init_db()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def _outlet_collection() -> pd.DataFrame:
    """A small collection with an outlet column, so a design can be declared."""
    rng = np.random.default_rng(3)
    rows = []
    for c, drift in (("Bread", 0.004), ("Milk", 0.002)):
        for o in range(6):
            shift = rng.normal(0, 0.003)
            for i in range(3):
                for t, period in enumerate(pd.date_range("2023-01-01", periods=14, freq="MS")):
                    rows.append({"Date": period, "Category": c, "Item_ID": f"{c}{o}{i}",
                                 "Item_Name": f"{c} {o}-{i}", "Outlet": f"{c}-O{o}",
                                 "Reported_Price": round((5 + i) * (1 + drift + shift) ** t, 4)})
    raw = pd.DataFrame(rows)
    schema = infer_schema(raw)
    frame = standardise(raw, schema)
    frame["outlet"] = raw["Outlet"].to_numpy()
    return frame


def _page(page: str, state: dict) -> AppTest:
    script = f"""
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.{page} as page
set_current_role(Role.ANALYST)
st.session_state.setdefault("pricelab_username", "analyst1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=300)
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, at.exception
    return at


def test_the_uncertainty_page_refuses_without_a_design_then_estimates_with_one(deployment):
    df = _outlet_collection()
    res = run_pipeline(df, RunConfig())
    state = {"analysis": {"result": res, "narrative": build_narrative(res), "decisions": [],
                          "label": "outlets"},
             "input_df": df, "run_config": res["config"]}
    at = _page("uncertainty", state)
    assert any("no sampling design has been declared" in w.value for w in at.warning)
    assert not [b for b in at.button if b.label == "Estimate the interval"]

    at.selectbox(key="un_cluster").select("outlet").run()
    at.selectbox(key="un_strata").select("category").run()
    next(b for b in at.button if b.label == "Estimate the interval").click().run()
    assert not at.exception, at.exception
    held = at.session_state["un_interval"]
    assert held["label"] == "outlets" and not held["result"].naive
    assert any("sampling uncertainty only" in s.value for s in at.success)
    assert any("times narrower" in i.value for i in at.info)

    next(b for b in at.button if b.label == "Recompute under the alternatives").click().run()
    assert not at.exception, at.exception
    sensitivity = at.session_state["un_sensitivity"]["result"]
    assert any(s.value == sensitivity.label for s in at.success)
    assert any(se.LOWER_BOUND in c.value for c in at.caption)
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Imputed share of the aggregate, whole run"] == (
        f"{sensitivity.imputed.run:.1%}")
    assert any(i.value == sensitivity.imputation_statement for i in at.info)
    table = at.dataframe[-1].value
    assert "imputed share of the aggregate (final period)" in table.columns
    headings = [m.value for m in at.markdown if m.value.startswith("####")]
    assert headings == ["#### Sampling uncertainty (a confidence interval)",
                        "#### Methodological sensitivity (not a confidence interval)"]

    # and the headline on Findings now carries both, as two separate statements
    findings = _page("findings", {**state, "un_interval": held,
                                  "un_sensitivity": at.session_state["un_sensitivity"]})
    captions = [c.value for c in findings.caption]
    assert any(c.startswith("All items -- sampling uncertainty:") for c in captions)
    assert any(c.startswith("All items -- Methodological sensitivity range") for c in captions)


def test_a_headline_with_no_interval_says_so(deployment):
    df = _outlet_collection()
    res = run_pipeline(df, RunConfig())
    at = _page("findings", {"analysis": {"result": res, "narrative": build_narrative(res),
                                         "decisions": [], "label": "no design"}})
    assert any("Sampling uncertainty has not been quantified" in c.value for c in at.caption)
