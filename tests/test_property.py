"""Phase 8: residential property price indices.

The engine tests run on `asset.synthetic_market`, whose quality-constant
index is known and in which larger homes sell increasingly often, so the
methods' differences have a known cause. The real-data test rebuilds
published house price indices from their published parts: Eurostat's
prc_hpi_q (new, existing and total dwellings, 2015 = 100) and prc_hpi_inw
(the weights combining them), recorded from live calls on 2026-09-23:

    GET .../sdmx/2.1/data/prc_hpi_q/Q.TOTAL+DW_NEW+DW_EXST.I15_Q.
        ?format=JSON&lang=en&startPeriod=2019-Q4&endPeriod=2025-Q2
    GET .../sdmx/2.1/data/prc_hpi_inw/A.TOTAL+DW_NEW+DW_EXST.
        ?format=JSON&lang=en&startPeriod=2019&endPeriod=2025

stored as tests/fixtures/connectors/eurostat_hpi_q.json and
eurostat_hpi_weights.json. Eurostat publishes indices, not transactions, so
the five transaction methods cannot be run on agency data through an
existing connector; what can be checked against agency data is the
aggregation, and that is what the real-data test does.
"""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import responses
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab.core import db
from pricelab.core.config import get_settings
from pricelab.data.connectors._jsonstat import parse_jsonstat
from pricelab.data.connectors.eurostat import BASE_URL, hpi_components
from pricelab.engine import asset as ast
from pricelab.engine import decomposition as dc

FIXTURES = Path(__file__).parent / "fixtures" / "connectors"


@pytest.fixture(scope="module")
def market() -> pd.DataFrame:
    return ast.synthetic_market()


@pytest.fixture(scope="module")
def comparison(market) -> ast.MethodComparison:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ast.compare_methods(market)


# ---------------------------------------------------------------------
# Repeat sales by hand
# ---------------------------------------------------------------------
def _three_pairs() -> pd.DataFrame:
    q = pd.to_datetime(["2024-01-01", "2024-04-01", "2024-07-01"])
    return pd.DataFrame({
        "property_id": ["A", "A", "B", "B", "C", "C"],
        "period": [q[0], q[1], q[0], q[2], q[1], q[2]],
        "price": [100.0, 110.0, 200.0, 240.0, 300.0, 315.0]})


def test_repeat_sales_by_hand():
    """Three properties, three quarters, each pair a different span.

    A: Q1 -> Q2, ratio 1.10, a = ln 1.10
    B: Q1 -> Q3, ratio 1.20, b = ln 1.20
    C: Q2 -> Q3, ratio 1.05, c = ln 1.05

    With Q1 the base, least squares minimises
        (b2 - a)^2 + (b3 - b)^2 + (b3 - b2 - c)^2
    whose normal equations 2 b2 - b3 = a - c and 2 b3 - b2 = b + c give
        b2 = (2a + b - c) / 3,   b3 = (a + 2b + c) / 3.
    With a = 0.095310, b = 0.182322, c = 0.048790:
        b2 = (0.190620 + 0.182322 - 0.048790) / 3 = 0.108051
        b3 = (0.095310 + 0.364643 + 0.048790) / 3 = 0.169581
    Index: 100 exp(b2) = 111.4104, 100 exp(b3) = 118.4808
    (The three pairs disagree -- 1.10 x 1.05 = 1.155, not 1.20 -- so least
    squares splits the difference, which is the point of the example.)
    """
    a, b, c = np.log(1.10), np.log(1.20), np.log(1.05)
    result = ast.repeat_sales(_three_pairs())
    assert result.index.iloc[0] == pytest.approx(100.0)
    assert result.index.iloc[1] == pytest.approx(100 * np.exp((2 * a + b - c) / 3))
    assert result.index.iloc[2] == pytest.approx(100 * np.exp((a + 2 * b + c) / 3))
    assert result.index.iloc[1] == pytest.approx(111.4104, abs=1e-4)
    assert result.index.iloc[2] == pytest.approx(118.4808, abs=1e-4)


def test_consistent_pairs_are_recovered_exactly_by_both_forms():
    """When every pair agrees with one index, BMN and Case-Shiller both
    return it, whatever the weighting."""
    true = {"2024-01-01": 100.0, "2024-04-01": 104.0, "2024-07-01": 110.0, "2024-10-01": 107.0}
    periods = list(true)
    rows = []
    for i, (first, second) in enumerate([(0, 1), (0, 2), (1, 3), (0, 3), (2, 3), (1, 2)]):
        rows += [{"property_id": f"P{i}", "period": periods[first], "price": 50.0 * true[
            periods[first]]}, {"property_id": f"P{i}", "period": periods[second],
                               "price": 50.0 * true[periods[second]]}]
    for weighted in (False, True):
        index = ast.repeat_sales(pd.DataFrame(rows), weighted=weighted).index
        assert index.to_numpy() == pytest.approx(list(true.values()))


def test_a_period_no_pair_reaches_is_refused():
    tx = _three_pairs()
    tx = pd.concat([tx, pd.DataFrame({"property_id": ["D", "D"], "period": pd.to_datetime(
        ["2025-01-01", "2025-04-01"]), "price": [1.0, 2.0]})])
    with pytest.raises(ast.PropertyError, match="not identified"):
        ast.repeat_sales(tx)


# ---------------------------------------------------------------------
# All five families on the same sales, and why they differ
# ---------------------------------------------------------------------
def test_every_method_runs_on_the_same_sales_and_states_what_it_measures(comparison):
    assert set(comparison.results) == set(ast.METHODS)
    for result in comparison.results.values():
        assert result.label.startswith(result.name)
        assert "measures " + result.measures in result.label
        assert "Limitation: " + result.limitation in result.label
    # the limitations the prompt names, in the words the reader sees
    assert "selected sample" in comparison.results["repeat_sales_bmn"].limitation
    assert "specification" in comparison.results["hedonic"].limitation
    assert "strata" in comparison.results["stratified_median"].limitation


def test_the_quality_holding_methods_track_the_known_index(comparison):
    truth = ast.true_index()
    last = comparison.table.index.max()
    for key in ("hedonic", "spar", "repeat_sales_bmn"):
        assert comparison.table.loc[last, key] == pytest.approx(truth[last], rel=0.03), key
    # while the median, which fixes only the strata, is carried up by the mix
    assert comparison.table.loc[last, "stratified_median"] > truth[last] * 1.05


def test_the_differences_are_explained_with_numbers(comparison):
    text = "\n".join(comparison.explanation)
    mix = float(comparison.quality_mix.iloc[-1]) - 100
    assert mix > 3                      # larger homes selling more often, as built
    assert f"{mix:+.2f}% more than those sold" in text
    assert "within its strata the quality of what sold moved" in text
    assert "within its cells (stratum by size band) the quality of what sold moved" in text
    assert "pairs of sales of the same property" in text
    assert "not estimates of one number" in text


def test_the_mix_explanation_accounts_for_the_mix_adjusted_gap(comparison, market):
    """The within-cell quality shift the explanation reports is the size of
    the mix-adjusted mean's actual gap to the hedonic index."""
    line = next(line for line in comparison.explanation if line.startswith("Mix-adjusted mean"))
    gap = float(re.search(r", ([+-]\d+\.\d+) points from the hedonic", line).group(1))
    accounted = float(re.search(r"about ([+-]\d+\.\d+) index points of the gap", line).group(1))
    assert abs(accounted - gap) < 0.6


# ---------------------------------------------------------------------
# Revisions: repeat sales revises by construction
# ---------------------------------------------------------------------
def test_adding_a_period_revises_the_repeat_sales_history(market):
    periods = sorted(market["period"].unique())
    before = ast.repeat_sales(market[market["period"] <= periods[-2]]).index
    after = ast.repeat_sales(market).index
    changes = (after.reindex(before.index) - before).dropna()
    assert (changes.iloc[1:].abs() > 1e-6).all()      # every earlier period moved


def test_the_revisions_appear_in_the_revision_triangle(market):
    from pricelab.engine import revision as rv

    analysis = ast.repeat_sales_revisions(market)
    assert isinstance(analysis, rv.RevisionAnalysis)
    assert analysis.triangle.shape[1] == len(analysis.vintages) >= 8
    # the triangle is the same machinery: revisions of published periods
    assert analysis.n_revisions > 0 and analysis.revised_periods > 0
    assert 0.05 < analysis.mean_absolute_revision < 5.0
    first_period = analysis.triangle.index[1]
    column_values = analysis.triangle.loc[first_period].dropna()
    assert column_values.max() - column_values.min() > 0.05


# ---------------------------------------------------------------------
# Diagnostics and suppression
# ---------------------------------------------------------------------
def test_counts_and_coverage(market):
    counts = ast.transaction_counts(market)
    assert (counts["All strata"] == counts.drop(columns="All strata").sum(axis=1)).all()
    coverage = ast.market_coverage(market, stock={"North": 3000, "South": 3000})
    assert coverage.loc[("method", "stratified median / mix-adjusted mean"),
                        "share_of_sales_pct"] == pytest.approx(100.0)
    pairs = len(ast.repeat_sales_pairs(market))
    assert coverage.loc[("method", "repeat sales (second sales of pairs)"),
                        "sales_used"] == pairs
    assert coverage.loc[("stratum", "North"), "share_of_stock_sold_per_period_pct"] > 0


def _thin_market() -> pd.DataFrame:
    """Three strata: two with plenty of sales, and 'Island' with two sales
    in one quarter."""
    rows = []
    rng = np.random.default_rng(0)
    for q, period in enumerate(pd.date_range("2024-01-01", periods=2, freq="QS")):
        for stratum, n in (("North", 20), ("South", 30), ("Island", 20 if q == 0 else 2)):
            for i in range(n):
                rows.append({"property_id": f"{stratum}{q}{i}", "period": period,
                             "stratum": stratum, "price": float(rng.uniform(100, 200))})
    return pd.DataFrame(rows)


def test_a_thin_stratum_is_suppressed_and_secondary_suppression_holds():
    result = ast.stratified_median(_thin_market())
    protected = ast.suppress_strata(result, min_count=5)
    second = protected[protected["period"] == pd.Timestamp("2024-04-01")].set_index("stratum")
    assert second.loc["Island", "published"] == "suppressed"
    assert second.loc["Island", "rule"].startswith("primary")
    # one primary suppression in a period whose all-strata index is published:
    # the smallest remaining stratum (North, 20 sales) goes too
    assert second.loc["North", "published"] == "suppressed"
    assert second.loc["North", "rule"].startswith("secondary")
    assert second.loc["South", "published"] != "suppressed"
    first = protected[protected["period"] == pd.Timestamp("2024-01-01")]
    assert not first["suppressed"].any()


# ---------------------------------------------------------------------
# Small hand checks for the other methods
# ---------------------------------------------------------------------
def test_stratified_median_and_spar_by_hand():
    """Two strata. Base: North medians 100, South 200; value of sales
    North 300 (three sales at 100), South 600. Next quarter: North median
    110 (+10%), South 210 (+5%): (300 x 1.10 + 600 x 1.05) / 900 = 106.667.

    SPAR, same sales with appraisals equal to the base prices: base ratio
    900 / 900 = 1; next, (330 + 630) / 900 = 1.0667."""
    q = pd.to_datetime(["2024-01-01", "2024-04-01"])
    rows = []
    for period, north, south in ((q[0], 100.0, 200.0), (q[1], 110.0, 210.0)):
        for i in range(3):
            rows.append({"property_id": f"N{i}", "period": period, "stratum": "North",
                         "price": north, "appraisal": 100.0})
        for i in range(3):
            rows.append({"property_id": f"S{i}", "period": period, "stratum": "South",
                         "price": south, "appraisal": 200.0})
    tx = pd.DataFrame(rows)
    assert ast.stratified_median(tx).index.iloc[1] == pytest.approx((300 * 1.1 + 600 * 1.05)
                                                                     / 900 * 100)
    assert ast.sale_price_appraisal_ratio(tx).index.iloc[1] == pytest.approx(960 / 900 * 100)


# ---------------------------------------------------------------------
# Real data: a published index rebuilt from its published parts
# ---------------------------------------------------------------------
@pytest.fixture(scope="module")
def hpi():
    indices = parse_jsonstat(json.loads((FIXTURES / "eurostat_hpi_q.json").read_text()))
    weights = parse_jsonstat(json.loads((FIXTURES / "eurostat_hpi_weights.json").read_text()))
    return indices, weights


@pytest.mark.parametrize("geo", ["IE", "NL", "FR", "ES", "DK", "PL", "DE", "EU27_2020"])
def test_published_house_price_totals_are_rebuilt_from_their_parts(hpi, geo):
    """The total rebuilt from the new- and existing-dwelling indices and
    their weights, chained at the fourth quarter, against the published
    total. The published parts are rounded to two decimals, so rounding
    alone moves a rebuilt level by up to a few hundredths; measured at most
    0.061 index points (Germany), and under 0.02 for the others here."""
    indices, weights = hpi
    parts, by_year, total = hpi_components(indices, weights, geo)
    rebuilt = dc.chain_linked_aggregate(parts, by_year, link_month=10)
    start = rebuilt.first_valid_index()
    gap = (rebuilt / 100.0 * float(total[start]) - total).dropna()
    assert len(gap) >= 20
    assert float(gap.abs().max()) < 0.07


# ---------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------
@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "property.db"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import ledger, registry, security  # noqa: F401  register tables
    db.init_db()
    import pages.sources as sources

    sources._cache = None
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def _app(page: str, state: dict | None = None) -> AppTest:
    script = f"""
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.{page} as page
set_current_role(Role.ANALYST)
st.session_state.setdefault("pricelab_username", "analyst1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=180)
    for key, value in (state or {}).items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, at.exception
    return at


def test_the_property_page_explains_suppresses_and_revises(deployment):
    at = _app("property")
    next(b for b in at.button if b.label == "Use the demonstration market").click().run()
    next(b for b in at.button if b.label == "Compile all methods").click().run()
    assert not at.exception, at.exception
    comparison = at.session_state["pr_comparison"]
    assert set(comparison.results) == set(ast.METHODS)
    captions = [c.value for c in at.caption]
    for result in comparison.results.values():
        assert f"Measures {result.measures}. Limitation: {result.limitation}." in captions
    markdown = "\n".join(m.value for m in at.markdown)
    assert "Why they differ" in markdown and "not estimates of one number" in markdown

    next(b for b in at.button if b.label == "Estimate the revision profile").click().run()
    assert not at.exception, at.exception
    analysis = at.session_state["pr_revision"]
    assert analysis.mean_absolute_revision > 0
    assert "Mean absolute revision" in [m.label for m in at.metric]

    # the same revisions, in the same frame, on the Revisions page
    revisions = _app("revisions", {"pr_revision": analysis})
    text = "\n".join(m.value for m in revisions.markdown)
    assert "Repeat sales house price index" in text and "Revision triangle" in text


def test_the_property_page_rebuilds_a_published_index(deployment):
    at = _app("property", {"pr_geo": "IE"})
    with responses.RequestsMock() as rsps:
        for dataset, fixture in (("prc_hpi_q", "eurostat_hpi_q.json"),
                                 ("prc_hpi_inw", "eurostat_hpi_weights.json")):
            rsps.add(responses.GET, re.compile(rf"{re.escape(BASE_URL)}/{dataset}/.*"),
                     body=(FIXTURES / fixture).read_text(), status=200)
        next(b for b in at.button if b.label == "Fetch and rebuild").click().run()
    assert not at.exception, at.exception
    rebuilt = at.session_state["pr_rebuilt"]
    assert rebuilt["gap, index points"].abs().max() < 0.02
    assert "Largest gap to the published total" in [m.label for m in at.metric]
