"""Phase 7b, Task 2: trade price indices and the unit value bias."""

from __future__ import annotations

import pandas as pd
import pytest
import streamlit
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab.core import db
from pricelab.core.config import get_settings
from pricelab.engine import trade as tr


def _mix_shift() -> pd.DataFrame:
    """Two export products whose prices never change, 10 and 100 a tonne.
    In January the country ships 90 t of the cheap one and 10 t of the dear
    one; in February, 10 t and 90 t."""
    rows = []
    for period, (cheap, dear) in (("2024-01-01", (90, 10)), ("2024-02-01", (10, 90))):
        rows += [{"period": period, "flow": "export", "product": "cheap",
                  "value": 10.0 * cheap, "quantity": float(cheap)},
                 {"period": period, "flow": "export", "product": "dear",
                  "value": 100.0 * dear, "quantity": float(dear)}]
    return pd.DataFrame(rows)


def test_the_unit_value_bias_by_hand():
    """The module's justification, with pencil arithmetic.

    January: value 90 x 10 + 10 x 100 = 1,900 for 100 t: unit value 19.
    February: value 10 x 10 + 90 x 100 = 9,100 for 100 t: unit value 91.
    Unit value index, February: 91 / 19 x 100 = 478.947...
    Price index: both prices are unchanged, so every formula gives 100.
    Gap: 378.947 index points, all of it the shift in composition; the
    composition effect is 91 / 19 = 4.789, the factor by which the unit
    value moved with no price moving at all.
    """
    bias = tr.unit_value_bias(_mix_shift(), "export")
    february = pd.Timestamp("2024-02-01")
    assert bias.unit_value.index[february] == pytest.approx(91 / 19 * 100)
    assert bias.price.index[february] == pytest.approx(100.0)
    assert bias.table.loc[february, "gap_points"] == pytest.approx(91 / 19 * 100 - 100)
    assert bias.max_gap_points == pytest.approx(378.94736842105266)
    assert bias.table.loc[february, "composition_effect"] == pytest.approx(91 / 19)


def test_every_formula_sees_no_price_change_where_there_is_none():
    for formula in tr.FORMULAS:
        assert tr.price_index(_mix_shift(), "export", formula=formula).index.iloc[-1] == \
            pytest.approx(100.0)


def test_a_unit_value_index_always_carries_the_bias_and_the_conditions():
    result = tr.unit_value_index(_mix_shift(), "export")
    assert result.warning.startswith(tr.UNIT_VALUE_WARNING)
    assert "homogeneous" in result.warning and "stable" in result.warning
    assert tr.UNIT_VALUE_WARNING in result.label


def _flows() -> pd.DataFrame:
    rows = []
    for period, px, pm in (("2024-01-01", (10.0, 20.0), (5.0, 8.0)),
                           ("2024-02-01", (11.0, 21.0), (5.5, 8.0)),
                           ("2024-03-01", (12.0, 20.0), (6.0, 9.0))):
        for product, price, qty in (("x1", px[0], 50.0), ("x2", px[1], 30.0)):
            rows.append({"period": period, "flow": "export", "product": product,
                         "value": price * qty, "quantity": qty})
        for product, price, qty in (("m1", pm[0], 40.0), ("m2", pm[1], 60.0)):
            rows.append({"period": period, "flow": "import", "product": product,
                         "value": price * qty, "quantity": qty})
    return pd.DataFrame(rows)


def test_terms_of_trade_reconcile_to_the_ratio_of_the_indices_exactly():
    exports = tr.price_index(_flows(), "export")
    imports = tr.price_index(_flows(), "import")
    terms = tr.terms_of_trade(exports, imports)
    assert terms.to_numpy() == pytest.approx(
        (exports.index / imports.index * 100).to_numpy(), rel=0, abs=0)
    # February by hand, fixed quantities so every formula is the Laspeyres:
    # exports (50 x 11 + 30 x 21) / (50 x 10 + 30 x 20) = 1180 / 1100
    # imports (40 x 5.5 + 60 x 8) / (40 x 5 + 60 x 8) = 700 / 680
    assert terms[pd.Timestamp("2024-02-01")] == pytest.approx((1180 / 1100) / (700 / 680) * 100)


def test_terms_of_trade_refuse_mismatched_indices():
    exports = tr.price_index(_flows(), "export")
    uv_imports = tr.unit_value_index(_flows(), "import")
    with pytest.raises(tr.TradeError, match="unit value index by a price index"):
        tr.terms_of_trade(exports, uv_imports)
    with pytest.raises(tr.TradeError, match="rebase"):
        tr.terms_of_trade(exports, tr.price_index(_flows(), "import",
                                                  base=pd.Timestamp("2024-02-01")))


def test_bad_inputs_are_refused():
    with pytest.raises(tr.TradeError, match="flow must be"):
        tr.price_index(_flows(), "transit")
    with pytest.raises(tr.TradeError, match="no \\['quantity'\\]"):
        tr.price_index(_flows().drop(columns="quantity"), "export")


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
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "trade.db"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import audit, ledger, registry, security  # noqa: F401  register tables
    db.init_db()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def test_the_page_states_the_bias_and_shows_both_indices(deployment, monkeypatch):
    frame = pd.concat([_mix_shift(), _flows()[_flows()["flow"] == "import"]
                       .loc[lambda d: d["period"] != "2024-03-01"]])
    monkeypatch.setattr(streamlit, "file_uploader",
                        lambda *a, **k: FakeUpload("trade.csv", frame))
    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.trade as page
set_current_role(Role.ANALYST)
st.session_state.setdefault("pricelab_username", "analyst1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=120)
    at.run()
    next(b for b in at.button if b.label == "Compile").click().run()
    assert not at.exception, at.exception
    assert any(tr.UNIT_VALUE_WARNING in w.value and "homogeneous" in w.value for w in at.warning)
    results = at.session_state["tr_results"]
    assert results["export"].max_gap_points == pytest.approx(378.94736842105266)
    gap = next(m for m in at.metric if m.label == "Largest unit value gap")
    assert gap.value.startswith("378.95")
    terms = at.session_state["tr_terms"]
    assert terms.iloc[0] == pytest.approx(100.0)
