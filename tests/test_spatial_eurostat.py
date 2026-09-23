"""Phase 8, Task 0b: the spatial module against real international
comparison data.

Eurostat's purchasing power parities by analytical category, 2023,
recorded from a live call through the Eurostat connector on 2026-09-23:

    GET .../sdmx/2.1/data/prc_ppp_ind/A.PPP_EU27_2020+EXP_NAC+PLI_EU27_2020..
        ?format=JSON&lang=en&startPeriod=2023&endPeriod=2023

stored as tests/fixtures/connectors/eurostat_ppp_2023.json: 50 geographies
and 61 categories. The categories' PPPs stand in for prices and their
national-currency expenditure for weights, so a weighted CPD across each
country's most detailed published categories gives an aggregate parity to
set beside Eurostat's own PPP for actual individual consumption (A01).

What the real data did that constructed data had not: it is not thinly
overlapping but *disconnected* in places -- Japan, the United States and the
United Kingdom publish only aggregates, so they share no detailed category
with anyone. The spatial engine used to refuse the whole comparison when a
region had no chain to the base; it now withholds such a region with the
reason and compares the rest.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import responses

from pricelab.data.connectors.eurostat import BASE_URL, EurostatConnector, ppp_comparison_inputs
from pricelab.engine import spatial as sp

FIXTURE = Path(__file__).parent / "fixtures" / "connectors" / "eurostat_ppp_2023.json"


@pytest.fixture(scope="module")
def fetched():
    """The PPPs as the connector delivers them."""
    connector = EurostatConnector(sleep_fn=lambda s: None, max_retries=1)
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, f"{BASE_URL}/prc_ppp_ind/A.PPP_EU27_2020+EXP_NAC+PLI_EU27_2020..",
                 body=FIXTURE.read_text(encoding="utf-8"), status=200)
        return connector.fetch(dataset="prc_ppp_ind/A.PPP_EU27_2020+EXP_NAC+PLI_EU27_2020..",
                               startPeriod="2023", endPeriod="2023", use_cache=False).data


@pytest.fixture(scope="module")
def comparison(fetched):
    prices, published, coverage = ppp_comparison_inputs(fetched)
    result = sp.cpd(prices.dropna(subset=["expenditure"]), base="EU27_2020", weighted=True)
    return prices, published, coverage, result


def test_the_real_overlap_is_ragged_and_meets_the_engine(comparison):
    prices, _, _, result = comparison
    per_region = prices.groupby("region")["product"].nunique()
    assert per_region[["JP", "UK", "US"]].tolist() == [1, 1, 1]       # aggregates only
    assert per_region.drop(["JP", "UK", "US"]).min() == 23
    for region in ("JP", "UK", "US"):
        assert pd.isna(result.ppp_estimated[region])
        assert "shares no chain of products with EU27_2020" in result.withheld[region]
    assert result.overlap.loc["JP", "DE"] == 0
    # 23 detailed categories are priced, but Eurostat publishes national-
    # currency expenditure for only 15 of them (none for clothing, footwear,
    # energy, furnishings, appliances, hospital services, transport services
    # or audio-visual equipment), so the weighted comparison rests on 15
    assert result.overlap.loc["DE", "FR"] == 15
    assert result.ppp.notna().sum() == 37           # 36 countries and the base


def test_the_parities_sit_near_eurostat_s_and_the_gap_is_explained(comparison, fetched):
    """Against Eurostat's published A01 PPPs: a median gap of 5.5%. Not
    rounding -- the 15 detailed categories with expenditure weights cover
    about half of each country's consumption (median 49%), leaving out rents, most health and
    education, and government-provided services: the non-traded services
    that are cheapest in low-price countries. So the gap is systematic: the
    cheaper the country, the more a goods-heavy parity overstates its price
    level. Measured: Spearman correlation of -0.77 between the gap and
    Eurostat's price level index."""
    _, published, coverage, result = comparison
    gap = ((result.ppp / published) - 1.0) * 100.0
    pli = fetched[(fetched["na_item"] == "PLI_EU27_2020") & (fetched["ppp_cat"] == "A01")] \
        .set_index("geo")["value"]
    frame = pd.DataFrame({"gap": gap, "pli": pli, "coverage": coverage}).dropna() \
        .drop(index="EU27_2020")
    assert len(frame) == 36
    assert frame["gap"].abs().median() < 6.0
    assert frame["coverage"].median() < 0.6
    assert frame["gap"].corr(frame["pli"], method="spearman") < -0.6
    # the cheapest countries are where the gap is largest
    assert frame.nsmallest(5, "pli")["gap"].mean() > 10.0
