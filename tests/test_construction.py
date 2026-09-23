"""Phase 7b, Task 3: construction input cost and output price indices."""

from __future__ import annotations

import pandas as pd
import pytest
import streamlit
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab.core import db
from pricelab.core.config import get_settings
from pricelab.engine import construction as cn

PERIODS = pd.date_range("2024-01-01", periods=3, freq="QS")


def test_the_input_cost_index_by_hand():
    """Materials 50% of cost, labour 40%, plant 10%. Next quarter materials
    are up 10%, labour 5%, plant flat:
        0.5 x 1.10 + 0.4 x 1.05 + 0.1 x 1.00 = 0.55 + 0.42 + 0.10 = 1.07."""
    inputs = pd.DataFrame({"materials": [200.0, 220.0, 230.0], "labour": [100.0, 105.0, 110.0],
                           "plant": [80.0, 80.0, 84.0]}, index=PERIODS)
    result = cn.input_cost_index(inputs, {"materials": 50, "labour": 40, "plant": 10})
    assert result.index.iloc[1] == pytest.approx(107.0)
    assert result.weights.sum() == pytest.approx(1.0)
    assert "does not reflect productivity" in result.label


def test_the_output_price_index_prices_a_fixed_bill_by_hand():
    """A bill of 100 m2 of walling and 20 m3 of concrete.
    Base: 100 x 50 + 20 x 120 = 7,400. Next: 100 x 52 + 20 x 126 = 7,720.
    Index: 7,720 / 7,400 x 100 = 104.324..."""
    rates = pd.DataFrame({"period": list(PERIODS[:2]) * 2,
                          "item": ["walling", "walling", "concrete", "concrete"],
                          "rate": [50.0, 52.0, 120.0, 126.0]})
    result = cn.output_price_index(rates, {"walling": 100, "concrete": 20})
    assert result.index.iloc[1] == pytest.approx(7720 / 7400 * 100)
    assert "margins and overheads" in result.label


def test_the_two_indices_are_different_questions_and_the_gap_is_reported():
    inputs = cn.input_cost_index(pd.DataFrame({"materials": [100.0, 110.0]},
                                              index=PERIODS[:2]), {"materials": 1})
    rates = pd.DataFrame({"period": PERIODS[:2], "item": ["unit", "unit"],
                          "rate": [1000.0, 1050.0]})
    outputs = cn.output_price_index(rates, {"unit": 1})
    gap = cn.input_output_gap(inputs, outputs)
    # Inputs up 10%, output prices up 5%: clients paid 105/110 of what
    # input costs alone would suggest -- margins squeezed or productivity up.
    assert gap["output_over_input"].iloc[1] == pytest.approx(105 / 110 * 100)
    assert cn.CONCEPTS["input_cost"] != cn.CONCEPTS["output_price"]


def test_mismatched_inputs_and_unpriced_bill_items_are_refused():
    with pytest.raises(cn.ConstructionError, match="shares with no index"):
        cn.input_cost_index(pd.DataFrame({"a": [1.0, 2.0]}, index=PERIODS[:2]), {"b": 1})
    rates = pd.DataFrame({"period": PERIODS[:2], "item": ["walling"] * 2, "rate": [1.0, 2.0]})
    with pytest.raises(cn.ConstructionError, match="no tender rate"):
        cn.output_price_index(rates, {"walling": 1, "roofing": 2})


def test_a_period_missing_a_bill_item_is_not_priced():
    rates = pd.DataFrame({"period": [PERIODS[0], PERIODS[0], PERIODS[1]],
                          "item": ["walling", "roofing", "walling"], "rate": [1.0, 2.0, 1.1]})
    result = cn.output_price_index(rates, {"walling": 1, "roofing": 1})
    assert pd.isna(result.index.iloc[1])
    assert "not priced" in result.notes[0]


class FakeUpload:
    def __init__(self, name: str, frame: pd.DataFrame) -> None:
        self.name, self._data = name, frame.to_csv(index=False).encode("utf-8")
        self.size = len(self._data)

    def getvalue(self) -> bytes:
        return self._data


@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "construction.db"))
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def test_the_page_states_both_concepts_and_compiles_both_indices(deployment, monkeypatch):
    inputs = pd.DataFrame({"period": list(PERIODS[:2]) * 2, "input": ["materials"] * 2
                           + ["labour"] * 2, "index": [100.0, 110.0, 100.0, 100.0],
                           "share": [0.5, 0.5, 0.5, 0.5]})
    outputs = pd.DataFrame({"period": PERIODS[:2], "item": ["unit"] * 2,
                            "rate": [1000.0, 1030.0], "quantity": [1.0, 1.0]})
    uploads = {"Input price": FakeUpload("inputs.csv", inputs),
               "Tender rates": FakeUpload("outputs.csv", outputs)}
    monkeypatch.setattr(streamlit, "file_uploader", lambda label, *a, **k: next(
        (f for key, f in uploads.items() if label.startswith(key)), None))
    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.construction as page
set_current_role(Role.ANALYST)
st.session_state.setdefault("pricelab_username", "analyst1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=120)
    at.run()
    infos = [i.value for i in at.info]
    assert infos[:2] == [cn.CONCEPTS["input_cost"], cn.CONCEPTS["output_price"]]
    next(b for b in at.button if b.label == "Compile").click().run()
    assert not at.exception, at.exception
    built = at.session_state["cn_built"]
    assert built["input_cost"].index.iloc[1] == pytest.approx(105.0)
    assert built["output_price"].index.iloc[1] == pytest.approx(103.0)
    assert at.session_state["cn_gap"]["output_over_input"].iloc[1] == pytest.approx(
        103 / 105 * 100)
