"""Phase 7b, Task 1: spatial price comparison."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import streamlit
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab.core import audit, db
from pricelab.core.config import get_settings
from pricelab.engine import spatial as sp

TRUE_PPP = {"A": 1.0, "B": 1.7, "C": 0.8, "D": 120.0}


def _known(seed: int = 0, thin: bool = True) -> pd.DataFrame:
    """Every price is PPP_r x pi_n exactly, with about 15% of prices
    missing outside the base and quantities that vary freely: any correct method recovers
    TRUE_PPP whatever the quantities or the gaps. Region E prices only
    three of the products, at a parity of 2.5."""
    rng = np.random.default_rng(seed)
    products = [f"p{i}" for i in range(20)]
    pi = {p: float(rng.uniform(1, 50)) for p in products}
    rows = [{"region": r, "product": p, "price": ppp * pi[p],
             "quantity": float(rng.uniform(1, 100)), "expenditure": float(rng.uniform(1, 10))}
            for r, ppp in TRUE_PPP.items() for p in products
            if r == "A" or rng.random() < 0.85]         # the base prices everything
    if thin:
        rows += [{"region": "E", "product": p, "price": 2.5 * pi[p], "quantity": 3.0,
                  "expenditure": 1.0} for p in products[:3]]
    return pd.DataFrame(rows)


@pytest.mark.parametrize("method", ["cpd", "geary_khamis", "weighted_cpd"])
def test_a_case_with_a_known_solution_is_recovered_exactly(method):
    prices = _known()
    if method == "cpd":
        result = sp.cpd(prices, base="A")
    elif method == "weighted_cpd":
        result = sp.cpd(prices, base="A", weighted=True)
    else:
        result = sp.geary_khamis(prices, base="A")
    for region, ppp in TRUE_PPP.items():
        assert result.ppp[region] == pytest.approx(ppp, rel=1e-9)


def test_two_regions_two_products_by_hand():
    """A prices both products at 1, B at 2 and 4, equal quantities.

    Geary-Khamis: with PPP_B = x, the international prices are
    pi_1 = (1 + 2/x)/2 and pi_2 = (1 + 4/x)/2, and
    x = (2 + 4) / (pi_1 + pi_2) = 12 / (2 + 6/x), so 2x + 6 = 12: x = 3.
    CPD: the log parity is the mean log price ratio,
    (ln 2 + ln 4)/2 = ln sqrt(8): PPP_B = 2.8284. The two differ because
    Geary-Khamis weights by quantity and CPD by count, which is the point
    of having both.
    """
    prices = pd.DataFrame({"region": ["A", "A", "B", "B"], "product": ["x", "y", "x", "y"],
                           "price": [1.0, 1.0, 2.0, 4.0], "quantity": [1.0, 1.0, 1.0, 1.0]})
    assert sp.geary_khamis(prices, base="A", min_overlap=1).ppp["B"] == pytest.approx(3.0)
    assert sp.cpd(prices, base="A", min_overlap=1).ppp["B"] == pytest.approx(np.sqrt(8.0))


def test_a_thin_overlap_is_reported_and_not_published():
    result = sp.cpd(_known(), base="A", min_overlap=5)
    assert result.ppp_estimated["E"] == pytest.approx(2.5)        # estimated...
    assert np.isnan(result.ppp["E"])                                # ...not published
    assert "at most 3 of its products" in result.withheld["E"]
    assert set(result.thin_pairs["region_b"]) == {"E"}
    assert result.overlap.loc["A", "E"] == 3
    assert "1 withheld" in result.label
    converted = sp.convert(pd.Series({"A": 100.0, "B": 170.0, "E": 250.0}), result)
    assert converted.loc["B", "in A prices"] == pytest.approx(100.0)
    assert np.isnan(converted.loc["E", "in A prices"])
    assert "not published" in converted.loc["E", "not_converted_because"]


def test_every_pair_s_matched_products_are_reported():
    prices = _known(thin=False)
    overlap = sp.overlap_matrix(prices)
    for a in TRUE_PPP:
        for b in TRUE_PPP:
            shared = len(set(prices.loc[prices["region"] == a, "product"])
                         & set(prices.loc[prices["region"] == b, "product"]))
            assert overlap.loc[a, b] == shared


def test_a_region_sharing_nothing_with_the_base_is_withheld_not_fatal():
    """Real comparison data has regions that publish only aggregates (see
    tests/test_spatial_eurostat.py); one of them must not sink the rest."""
    prices = pd.concat([_known(thin=False), pd.DataFrame(
        {"region": ["Z"], "product": ["only_in_z"], "price": [3.0], "quantity": [1.0],
         "expenditure": [1.0]})])
    for result in (sp.cpd(prices, base="A"), sp.geary_khamis(prices, base="A")):
        assert np.isnan(result.ppp_estimated["Z"]) and np.isnan(result.ppp["Z"])
        assert "no chain of products" in result.withheld["Z"]
        assert result.ppp["B"] == pytest.approx(1.7)
    alone = pd.DataFrame({"region": ["A", "Z"], "product": ["x", "q"], "price": [1.0, 3.0]})
    with pytest.raises(sp.SpatialError, match="no region is connected"):
        sp.cpd(alone, base="A")


def test_price_level_indices_compare_parities_with_exchange_rates():
    result = sp.cpd(_known(thin=False), base="A")
    levels = sp.price_level_indices(result, pd.Series({"B": 2.0, "C": 0.8, "D": 100.0}))
    assert levels.to_dict() == pytest.approx({"B": 85.0, "C": 100.0, "D": 120.0})


def test_cpd_reports_standard_errors_when_the_fit_is_not_exact():
    noisy = _known(thin=False)
    noisy["price"] *= np.exp(np.random.default_rng(3).normal(0, 0.05, len(noisy)))
    result = sp.cpd(noisy, base="A")
    assert result.standard_errors is not None and result.standard_errors["B"] > 0
    assert result.ppp["B"] == pytest.approx(1.7, rel=0.05)


# ---------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------
class FakeUpload:
    def __init__(self, name: str, frame: pd.DataFrame) -> None:
        self.name, self._data = name, frame.to_csv(index=False).encode("utf-8")
        self.size = len(self._data)

    def getvalue(self) -> bytes:
        return self._data


@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "spatial.db"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import audit, ledger, registry, security  # noqa: F401  register tables
    db.init_db()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def test_the_page_publishes_parities_and_withholds_the_thin_region(deployment, monkeypatch):
    uploads = {"Regional prices": FakeUpload("prices.csv", _known()),
               "Values in local currency": FakeUpload(
                   "values.csv", pd.DataFrame({"region": ["B", "E"], "value": [170.0, 250.0]}))}
    monkeypatch.setattr(streamlit, "file_uploader", lambda label, *a, **k: next(
        (f for key, f in uploads.items() if label.startswith(key)), None))
    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.spatial as page
set_current_role(Role.ANALYST)
st.session_state.setdefault("pricelab_username", "analyst1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=120)
    at.session_state["sp_method"] = "geary_khamis"
    at.run()
    next(b for b in at.button if b.label == "Compare").click().run()
    assert not at.exception, at.exception
    result = at.session_state["sp_result"]
    assert result.ppp["B"] == pytest.approx(1.7)
    assert any(w.value.startswith("E: at most 3") for w in at.warning)
    converted = at.session_state["sp_converted"]
    assert converted.loc["B", "in A prices"] == pytest.approx(100.0)
    assert np.isnan(converted.loc["E", "in A prices"])
    with db.session_scope() as s:
        assert s.query(audit.AuditEventORM).filter_by(
            action=audit.SPATIAL_COMPARISON).count() == 1
