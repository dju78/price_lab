"""Phase 8, Task 2: rents and the four owner-occupied housing approaches."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import streamlit
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab.core import db
from pricelab.core.config import get_settings
from pricelab.engine import housing as hs

Q = pd.date_range("2024-01-01", periods=3, freq="QS")


def _rents() -> pd.DataFrame:
    """North: two dwellings, rents up 10% and 0% (Jevons: sqrt(1.1) =
    1.0488), then both flat. South: one dwelling, up 5%, then up 2%.
    Base-period rent: North 1,000 + 1,000, South 1,000; weights 2:1."""
    rows = [("n1", "North", (1000.0, 1100.0, 1100.0)), ("n2", "North", (1000.0, 1000.0, 1000.0)),
            ("s1", "South", (1000.0, 1050.0, 1071.0))]
    return pd.DataFrame([{"dwelling_id": d, "stratum": s, "period": q, "rent": r}
                         for d, s, rents in rows for q, r in zip(Q, rents, strict=True)])


def test_the_rent_index_by_hand():
    result = hs.rent_index(_rents())
    north = 100 * np.sqrt(1.1)
    assert result.by_stratum.loc[Q[1], "North"] == pytest.approx(north)
    assert result.by_stratum.loc[Q[2], "South"] == pytest.approx(107.1)
    assert result.index[Q[1]] == pytest.approx((2 * north + 105.0) / 3)
    assert result.matched[Q[1]] == 3
    assert "sitting tenancies" in result.label


def test_the_four_approaches_answer_four_different_questions():
    questions = [spec["question"] for spec in hs.OOH_QUESTIONS.values()]
    assert len(set(questions)) == 4
    assert set(hs.OOH_QUESTIONS) == {"rental_equivalence", "net_acquisitions", "user_cost",
                                     "payments"}
    for spec in hs.OOH_QUESTIONS.values():
        assert spec["question"].endswith("?") and spec["counts"] and spec["leaves_out"]


def test_rental_equivalence_is_the_rent_index_under_its_own_question():
    result = hs.rental_equivalence(hs.rent_index(_rents()))
    assert "What would owner-occupiers pay to rent the homes they live in?" in result.label
    assert result.index.iloc[0] == pytest.approx(100.0)


def test_net_acquisitions_takes_out_the_land_by_hand():
    """New dwellings up 10%, land up 30%, land 40% of the base price:
    structure = (110 - 0.4 x 130) / 0.6 = (110 - 52) / 0.6 = 96.667 -- the
    land boom does not reach the consumer price index."""
    prices = pd.Series([100.0, 110.0], index=Q[:2])
    land = pd.Series([50.0, 65.0], index=Q[:2])
    result = hs.net_acquisitions(prices, land_share=0.4, land_prices=land)
    assert result.index.iloc[1] == pytest.approx((110 - 0.4 * 130) / 0.6)
    assert "land excluded" in result.notes[0]
    assert "new to the household sector" in result.label


def test_user_cost_by_hand_and_it_can_go_negative():
    """V = 100, then 102; interest 4%; depreciation 1.5 + maintenance 1.0
    + tax 0.5 = 3%. With a one-year expectation and four quarters a year the
    expected gain is unknown at first and falls back to the realised mean
    (none yet), so UC(base) = 100 x (4 + 3 - 0)/100 = 7. Then a price surge
    makes expected gains exceed 7% and the user cost negative -- which is
    reported, not floored."""
    periods = pd.date_range("2020-01-01", periods=12, freq="QS")
    prices = pd.Series(100 * 1.05 ** np.arange(12), index=periods)     # +21.6% a year
    rate = pd.Series(4.0, index=periods)
    result = hs.user_cost(prices, rate, expectation_years=1)
    assert result.components["user cost"].iloc[0] == pytest.approx(7.0)
    assert (result.components["user cost"].iloc[4:] < 0).all()
    assert any("zero or negative" in n for n in result.notes)
    assert "What does it cost, each period, to own a home?" in result.label


def test_payments_sums_outlays_and_refuses_capital_repayment():
    outlays = pd.DataFrame({"mortgage interest": [300.0, 330.0], "repairs": [100.0, 100.0],
                            "insurance": [50.0, 50.0]}, index=Q[:2])
    result = hs.payments(outlays)
    assert result.index.iloc[1] == pytest.approx(480 / 450 * 100)
    with pytest.raises(hs.HousingError, match="saving, not a consumption payment"):
        hs.payments(outlays.assign(**{"capital repayment": [200.0, 200.0]}))


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
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "housing.db"))
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def test_the_page_presents_four_questions_not_a_choice_of_method(deployment, monkeypatch):
    periods = pd.date_range("2020-01-01", periods=8, freq="QS")
    series = lambda values: pd.DataFrame({"period": periods, "value": values})  # noqa: E731
    uploads = {
        "Rents": FakeUpload("rents.csv", _rents()),
        "New-dwelling price index": FakeUpload("new.csv", series(np.linspace(100, 110, 8))),
        "House price index": FakeUpload("hpi.csv", series(np.linspace(100, 112, 8))),
        "Interest rate": FakeUpload("rate.csv", series(np.full(8, 4.0))),
        "Outlays": FakeUpload("outlays.csv", pd.DataFrame(
            {"period": periods, "mortgage interest": np.linspace(300, 340, 8),
             "repairs": np.full(8, 100.0)})),
    }
    monkeypatch.setattr(streamlit, "file_uploader", lambda label, *a, **k: next(
        (f for key, f in uploads.items() if label.startswith(key)), None))
    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.housing as page
set_current_role(Role.ANALYST)
st.session_state.setdefault("pricelab_username", "analyst1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    headings = [m.value for m in at.markdown if m.value.startswith("#####")]
    for spec in hs.OOH_QUESTIONS.values():
        assert f"##### {spec['name']}: {spec['question']}" in headings
    assert "four questions, not four methods" in "\n".join(m.value for m in at.markdown)
    # no widget offers a choice between the approaches
    for widget in [*at.selectbox, *at.radio]:
        assert not {spec["name"] for spec in hs.OOH_QUESTIONS.values()} & set(
            map(str, widget.options))
    for approach in hs.OOH_QUESTIONS:
        result = at.session_state[f"hs_ooh_{approach}"]
        assert result.question == hs.OOH_QUESTIONS[approach]["question"]
    successes = [s.value for s in at.success]
    assert sum("Answers:" in s for s in successes) == 4
