"""Phase 7b, Task 0b: contributions to the annual rate across a chain link.

The treatment is the published one (`decomposition.RIBE_SOURCE`): the OECD
note "OECD calculation of contributions to overall annual inflation" (2018,
updated 2022), section 3, following Walschots (2016), which Balk and
Mehrhoff call the Ribe contribution in chapter 8 of Eurostat's HICP
Methodological Manual, and which Eurostat uses for its published HICP
contributions.

Two checks. A constructed case whose answer is derived by hand in the
docstring. And the euro-area HICP: contributions computed from the
recorded index and weight responses against Eurostat's own published
contributions (dataset prc_hicp_ctrb), recorded from a live call on
2026-09-23:

    GET .../sdmx/2.1/data/prc_hicp_ctrb/M.PC_PNT.CP01+...+CP12.EA
        ?format=JSON&lang=en&startPeriod=2025-01&endPeriod=2025-12

stored as tests/fixtures/connectors/eurostat_hicp_ea_contributions.json.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest
import responses
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab.core import db
from pricelab.core.config import get_settings
from pricelab.data.connectors._jsonstat import parse_jsonstat
from pricelab.data.connectors.eurostat import BASE_URL, hicp_chain_inputs
from pricelab.engine import decomposition as dc

FIXTURES = Path(__file__).parent / "fixtures" / "connectors"
DIVISIONS = [f"CP{i:02d}" for i in range(1, 13)]


def _hand_case() -> tuple[pd.DataFrame, dict[int, dict[str, float]]]:
    periods = pd.to_datetime(["2023-12-01", "2024-03-01", "2024-12-01", "2025-03-01"])
    components = pd.DataFrame({"A": [100.0, 104.0, 110.0, 121.0],
                               "B": [100.0, 101.0, 102.0, 102.0]}, index=periods)
    # Weights used for the link from December of the year before
    weights = {2024: {"A": 0.5, "B": 0.5}, 2025: {"A": 0.2, "B": 0.8}}
    return components, weights


def test_the_chain_link_contributions_by_hand():
    """Two components, re-weighted in December 2024 from 50/50 to 20/80.

    The chain-linked aggregate (December 2023 = 100):
        Mar 2024 = 100 x (0.5 x 104/100 + 0.5 x 101/100)        = 102.5
        Dec 2024 = 100 x (0.5 x 110/100 + 0.5 x 102/100)        = 106
        Mar 2025 = 106 x (0.2 x 121/110 + 0.8 x 102/102)
                 = 106 x (0.22 + 0.8) = 106 x 1.02              = 108.12
    Annual rate, March 2025: 108.12 / 102.5 - 1 = 5.62 / 102.5  = 5.4829268 %

    Contributions (OECD note, section 3):
    A, since December (2025 weights):
        (106 / 102.5) x 0.2 x (121 - 110) / 110 = 2.12 / 102.5
    A, up to December (2024 weights):
        (100 / 102.5) x 0.5 x (110 - 104) / 100 = 3.00 / 102.5
    A total = 5.12 / 102.5 = 4.9951220 pp
    B, since December: (106 / 102.5) x 0.8 x (102 - 102) / 102 = 0
    B, up to December: (100 / 102.5) x 0.5 x (102 - 101) / 100 = 0.50 / 102.5
    B total = 0.50 / 102.5 = 0.4878049 pp
    A + B = 5.62 / 102.5, the annual rate, exactly.
    """
    components, weights = _hand_case()
    result = dc.ribe_contributions(components, weights)
    march = pd.Timestamp("2025-03-01")
    assert result.aggregate.tolist() == pytest.approx([100.0, 102.5, 106.0, 108.12])
    assert result.annual_rate_pct[march] == pytest.approx(5.62 / 102.5 * 100)
    assert result.since_link.loc[march, "A"] == pytest.approx(2.12 / 102.5 * 100)
    assert result.before_link.loc[march, "A"] == pytest.approx(3.00 / 102.5 * 100)
    assert result.contributions.loc[march, "A"] == pytest.approx(5.12 / 102.5 * 100)
    assert result.contributions.loc[march, "B"] == pytest.approx(0.50 / 102.5 * 100)
    assert result.residual_pp < 1e-12


def test_one_set_of_weights_across_the_link_gets_the_hand_case_wrong():
    """Why the approximation is not good enough: on this year's weights
    alone, A's year-on-year contribution is w x (P_now - P_year_ago) /
    P_TOT(year ago) scaled as for a fixed basket, and it misses the hand
    answer by more than a percentage point."""
    components, weights = _hand_case()
    march, year_ago = pd.Timestamp("2025-03-01"), pd.Timestamp("2024-03-01")
    shares = weights[2025]
    fixed = {c: shares[c] * (components.loc[march, c] - components.loc[year_ago, c])
             / sum(shares[k] * components.loc[year_ago, k] for k in shares) * 100
             for c in shares}
    ribe = dc.ribe_contributions(components, weights).contributions.loc[march]
    assert abs(fixed["A"] - ribe["A"]) > 1.0


def test_december_is_one_link_long_and_has_no_second_part():
    components, weights = _hand_case()
    extended = pd.concat([components, pd.DataFrame(
        {"A": [125.0], "B": [103.0]}, index=pd.to_datetime(["2025-12-01"]))])
    result = dc.ribe_contributions(extended, weights)
    december = pd.Timestamp("2025-12-01")
    assert result.before_link.loc[december].abs().max() == 0.0
    # and it is the fixed-basket contribution on this year's weights
    assert result.contributions.loc[december, "B"] == pytest.approx(0.8 * (103 - 102) / 102 * 100)


def test_missing_weights_or_links_are_refused_with_what_is_needed():
    components, weights = _hand_case()
    with pytest.raises(dc.DecompositionError, match="two years of weights"):
        dc.ribe_contributions(components, {2025: weights[2025]})


@pytest.fixture(scope="module")
def hicp():
    indices = parse_jsonstat(json.loads(
        (FIXTURES / "eurostat_hicp_ea_indices.json").read_text(encoding="utf-8")))
    weights = parse_jsonstat(json.loads(
        (FIXTURES / "eurostat_hicp_ea_weights.json").read_text(encoding="utf-8")))
    published = parse_jsonstat(json.loads(
        (FIXTURES / "eurostat_hicp_ea_contributions.json").read_text(encoding="utf-8")))
    return indices, weights, published.pivot(index="period", columns="coicop", values="value")


def test_the_ribe_contributions_reproduce_eurostat_s_published_ones(hicp):
    """Every division, every month of 2025, against Eurostat's published
    contributions to euro-area annual inflation. Eurostat publishes them to
    two decimals, so rounding alone accounts for up to 0.005 pp; the
    indices behind this computation are themselves published to one or two
    decimals. Measured: at most 0.0056 pp."""
    indices, weights, published = hicp
    chained, by_year, aggregate = hicp_chain_inputs(indices, weights, DIVISIONS)
    for total in (None, aggregate):
        result = dc.ribe_contributions(chained, by_year, aggregate=total)
        ours = result.contributions.loc["2025"]
        gap = (ours[DIVISIONS] - published[DIVISIONS]).abs()
        assert gap.shape == (12, 12)
        assert float(gap.max().max()) < 0.006


def test_on_real_data_the_contributions_sum_to_the_rate_they_decompose(hicp):
    indices, weights, _ = hicp
    chained, by_year, aggregate = hicp_chain_inputs(indices, weights, DIVISIONS)
    own = dc.ribe_contributions(chained, by_year)
    assert own.residual_pp < 1e-10
    # With the published all-items index, compiled by Eurostat from unrounded
    # data, the sum misses its annual rate by the rounding in the published
    # division indices -- reported, not forced to zero.
    published = dc.ribe_contributions(chained, by_year, aggregate=aggregate)
    assert 0 < published.residual_pp < 0.02


def test_the_source_is_cited_on_the_result():
    components, weights = _hand_case()
    result = dc.ribe_contributions(components, weights)
    assert "OECD calculation of contributions to overall annual inflation" in result.source
    assert "Walschots (2016)" in result.source and "prc_hicp_ctrb" in result.source


# ---------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------
@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "ribe.db"))
    monkeypatch.setenv("PRICELAB_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import ledger, registry, security  # noqa: F401  register tables
    db.init_db()
    import pages.sources as sources

    sources._cache = None
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def test_the_decomposition_page_shows_contributions_across_the_link(deployment):
    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.decomposition as page
set_current_role(Role.ANALYST)
st.session_state.setdefault("pricelab_username", "analyst1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=120)
    at.session_state["dc_source"] = "Eurostat HICP (official, with item weights)"
    at.run()
    with responses.RequestsMock() as rsps:
        for dataset, fixture in (("prc_hicp_midx", "eurostat_hicp_ea_indices.json"),
                                 ("prc_hicp_inw", "eurostat_hicp_ea_weights.json")):
            rsps.add(responses.GET, re.compile(rf"{re.escape(BASE_URL)}/{dataset}/.*"),
                     body=(FIXTURES / fixture).read_text(encoding="utf-8"), status=200)
        next(b for b in at.button if b.label == "Fetch from Eurostat").click().run()
    assert not at.exception, at.exception
    result = at.session_state["dc_ribe"]
    assert result.residual_pp < 1e-10
    text = "\n".join(m.value for m in at.markdown) + "\n".join(c.value for c in at.caption)
    assert "across the re-weighting" in text
    assert "OECD calculation of contributions" in text
    assert "Largest gap, contributions against the rate" in [m.label for m in at.metric]
