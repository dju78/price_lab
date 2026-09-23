"""Phase 7b, Task 4: contract escalation.

The worked example is a contract a manager could hold beside the output:

    Base amount       GBP 100,000.00 per monthly payment
    Index             the construction output price index, vintage "run r42,
                      vintage 2", base reading January 2024 = 100.0
    Lag               two months: the April payment uses February's index
    Indexed share     80%; 20% of the amount is fixed
    Cap               +5% of the base amount
    Collar            -3% of the base amount

    Index:  Jan 100.0  Feb 102.0  Mar 104.0  Apr 110.0  May 95.0

    Payment  index month  movement   x 80%     limit          payment
    April    February      +2.00%    +1.60%    -              101,600.00
    May      March         +4.00%    +3.20%    -              103,200.00
    June     April        +10.00%    +8.00%    cap +5%        105,000.00  (would be 108,000.00)
    July     May           -5.00%    -4.00%    collar -3%      97,000.00  (would be  96,000.00)

    Total 406,800.00 against 400,000.00 unadjusted and 408,800.00 unlimited.
"""

from __future__ import annotations

import pandas as pd
import pytest
import streamlit
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab.core import audit, db
from pricelab.core.config import get_settings
from pricelab.engine import escalation as es

INDEX = pd.Series([100.0, 102.0, 104.0, 110.0, 95.0],
                  index=pd.date_range("2024-01-01", periods=5, freq="MS"))
PAYMENTS = pd.date_range("2024-04-01", periods=4, freq="MS")


def _clause(**overrides) -> es.EscalationClause:
    settings = dict(base_amount=100_000.0, index_name="the construction output price index",
                    index_vintage="run r42, vintage 2", base_period=pd.Timestamp("2024-01-01"),
                    lag=2, indexation_factor=0.8, cap_pct=5.0, collar_pct=3.0, currency="GBP")
    settings.update(overrides)
    return es.EscalationClause(**settings)


def test_the_worked_contract_example():
    result = es.apply_clause(_clause(), INDEX, PAYMENTS)
    s = result.schedule
    assert list(s["index_period"]) == ["Feb 2024", "Mar 2024", "Apr 2024", "May 2024"]
    assert s["movement_pct"].tolist() == pytest.approx([2.0, 4.0, 10.0, -5.0])
    assert s["adjustment_pct"].tolist() == pytest.approx([1.6, 3.2, 8.0, -4.0])
    assert list(s["limit_bound"]) == ["", "", "cap", "collar"]
    assert s["payment"].tolist() == pytest.approx([101_600.0, 103_200.0, 105_000.0, 97_000.0])
    assert s["payment_without_cap_or_collar"].tolist() == pytest.approx(
        [101_600.0, 103_200.0, 108_000.0, 96_000.0])
    assert s["payment"].sum() == pytest.approx(406_800.0)


def test_the_summary_names_the_index_vintage_lag_and_every_binding_limit():
    summary = es.apply_clause(_clause(), INDEX, PAYMENTS).summary
    assert "the construction output price index, vintage: run r42, vintage 2" in summary
    assert "The base reading is the index at January 2024: 100.0000" in summary
    assert "2 periods before the payment period" in summary
    assert "80% of the amount is indexed and 20% is fixed" in summary
    assert ("June 2024: the index reading (Apr 2024, 110.0000) is +10.00% against the base; "
            "with 80% indexation that is +8.00%. The cap of +5% bound, so the payment is "
            "GBP 105,000.00 instead of GBP 108,000.00") in summary
    assert ("July 2024: the index reading (May 2024, 95.0000) is -5.00% against the base; "
            "with 80% indexation that is -4.00%. The collar of -3% bound, so the payment is "
            "GBP 97,000.00 instead of GBP 96,000.00") in summary
    assert "GBP 406,800.00, against GBP 400,000.00 unadjusted and GBP 408,800.00" in summary
    # the periods where nothing bound are not reported as binding
    assert "April 2024:" not in summary and "May 2024:" not in summary


def test_a_dead_band_passes_on_only_the_excess_or_the_whole_movement():
    """A 3% dead band, full indexation, no limits. February's +2% is inside
    the band: nothing. April's +10% is outside: 'excess' passes on 7%,
    'full' passes on 10%."""
    excess = es.apply_clause(_clause(indexation_factor=1.0, cap_pct=None, collar_pct=None,
                                     dead_band_pct=3.0, lag=0),
                             INDEX, pd.DatetimeIndex(["2024-02-01", "2024-04-01"]))
    assert excess.schedule["payment"].tolist() == pytest.approx([100_000.0, 107_000.0])
    assert any("dead band absorbed all of it" in e for e in excess.events)
    full = es.apply_clause(_clause(indexation_factor=1.0, cap_pct=None, collar_pct=None,
                                   dead_band_pct=3.0, dead_band_mode="full", lag=0),
                           INDEX, pd.DatetimeIndex(["2024-02-01", "2024-04-01"]))
    assert full.schedule["payment"].tolist() == pytest.approx([100_000.0, 110_000.0])


def test_a_trigger_holds_the_price_until_the_index_has_moved_far_enough():
    """A 3% trigger: February (+2%) does not re-set the price; March (+4%)
    does; April (+10%) re-sets again; May (-5%) re-sets again."""
    result = es.apply_clause(_clause(indexation_factor=1.0, cap_pct=None, collar_pct=None,
                                     trigger_pct=3.0, lag=0),
                             INDEX, pd.date_range("2024-02-01", periods=4, freq="MS"))
    assert result.schedule["price_set_at_pct"].tolist() == pytest.approx([0.0, 4.0, 10.0, -5.0])
    assert any("February 2024" in e and "not re-set" in e for e in result.events)


def test_an_averaged_reading_uses_the_named_months():
    result = es.apply_clause(_clause(averaging=3, lag=0, base_period=pd.Timestamp("2024-03-01"),
                                     cap_pct=None, collar_pct=None, indexation_factor=1.0),
                             INDEX, pd.DatetimeIndex(["2024-05-01"]))
    # base: mean(100, 102, 104) = 102; May: mean(104, 110, 95) = 103
    assert result.base_reading == pytest.approx(102.0)
    assert result.schedule["movement_pct"].iloc[0] == pytest.approx((103 / 102 - 1) * 100)
    assert result.schedule["index_period"].iloc[0] == "the average of Mar 2024 to May 2024"


def test_a_payment_on_an_unpublished_index_is_refused():
    with pytest.raises(es.EscalationError, match="no published value for 2024-06"):
        es.apply_clause(_clause(), INDEX, pd.DatetimeIndex(["2024-08-01"]))


def test_a_clause_that_cannot_be_right_is_refused():
    for bad, match in (({"indexation_factor": 1.5}, "between 0 and 1"),
                       ({"cap_pct": -1.0}, "cannot be negative"),
                       ({"lag": -1}, "lag must be zero or more")):
        with pytest.raises(es.EscalationError, match=match):
            _clause(**bad)


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
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "escalation.db"))
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def _page(monkeypatch, state: dict) -> AppTest:
    upload = FakeUpload("cop_index.csv", INDEX.rename("value").rename_axis("period")
                        .reset_index())
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: upload)
    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.escalation as page
set_current_role(Role.ANALYST)
st.session_state.setdefault("pricelab_username", "analyst1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=120)
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, at.exception
    return at


def test_the_page_refuses_an_uploaded_index_with_no_vintage(deployment, monkeypatch):
    at = _page(monkeypatch, {})
    assert any("State which vintage" in w.value for w in at.warning)
    assert not [b for b in at.button if b.label == "Build the schedule"]


def test_the_page_reproduces_the_worked_example_and_leads_with_the_summary(
        deployment, monkeypatch):
    at = _page(monkeypatch, {
        "es_vintage": "published 2024-06-14", "es_name": "Construction output price index",
        "es_lag": 2, "es_factor": 0.8, "es_use_cap": True, "es_cap": 5.0,
        "es_use_collar": True, "es_collar": 3.0,
        "es_first": pd.Timestamp("2024-04-01"), "es_count": 4})
    next(b for b in at.button if b.label == "Build the schedule").click().run()
    assert not at.exception, at.exception
    result = at.session_state["es_result"]
    assert result.schedule["payment"].tolist() == pytest.approx(
        [101_600.0, 103_200.0, 105_000.0, 97_000.0])
    text = "\n".join(m.value for m in at.markdown)
    assert "vintage: published 2024-06-14" in text
    assert "The cap of +5% bound" in text and "The collar of -3% bound" in text
    headings = [m.value for m in at.markdown if m.value.startswith("####")]
    assert headings == ["#### The clause as applied", "#### Payment schedule"]
    with db.session_scope() as s:
        event = s.query(audit.AuditEventORM).filter_by(action=audit.ESCALATION).one()
    assert '"bound": 2' in event.params_json and "published 2024-06-14" in event.params_json
