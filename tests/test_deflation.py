"""Phase 7a, Task 2: deflation, real values, constant prices and PPPs.

The first test reproduces a deflation by arithmetic written out in the test
itself, so a reviewer can check every number with a pencil.
"""

from __future__ import annotations

import pandas as pd
import pytest
import streamlit
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab.core import audit, db
from pricelab.core.config import get_settings
from pricelab.engine import deflation as dfl

MONTHS = pd.date_range("2024-01-01", periods=3, freq="MS")


def test_a_deflation_reproduced_by_hand():
    """Average weekly earnings and a consumer price index, three months:

        month     earnings   CPI      CPI / CPI(Jan)    real = earnings / ratio
        Jan 2024  2000.00    110.0    110.0/110.0 = 1       2000 / 1    = 2000.000000
        Feb 2024  2050.00    112.0    112.0/110.0           2050 * 110 / 112
                                                             = 225500 / 112 = 2013.392857...
        Mar 2024  2100.00    115.5    115.5/110.0 = 1.05    2100 / 1.05 = 2000.000000

    Prices rose 5% from January to March and earnings rose 5%, so real
    earnings in March are back at January's level, in January 2024 prices.
    """
    earnings = pd.Series([2000.0, 2050.0, 2100.0], index=MONTHS)
    cpi = pd.Series([110.0, 112.0, 115.5], index=MONTHS)
    result = dfl.real_wage(earnings, cpi, reference=MONTHS[0],
                           wage_name="average weekly earnings", index_name="CPI")

    assert result.real.iloc[0] == pytest.approx(2000.0)
    assert result.real.iloc[1] == pytest.approx(225500 / 112)          # 2013.392857...
    assert result.real.iloc[1] == pytest.approx(2013.392857142857)
    assert result.real.iloc[2] == pytest.approx(2000.0)
    assert result.deflator.tolist() == pytest.approx([100.0, 112 / 110 * 100, 105.0])


def test_the_result_names_its_deflator_and_reference_period():
    earnings = pd.Series([2000.0, 2050.0, 2100.0], index=MONTHS)
    cpi = pd.Series([110.0, 112.0, 115.5], index=MONTHS)
    result = dfl.real_wage(earnings, cpi, reference=MONTHS[0],
                           wage_name="average weekly earnings", index_name="CPI")
    assert result.label == ("average weekly earnings, real wage in constant Jan 2024 prices: "
                            "deflated by CPI (Jan 2024 = 100), aligned monthly")
    assert "deflator (Jan 2024 = 100)" in result.frame.columns
    assert "real (constant Jan 2024 prices)" in result.frame.columns


def test_a_year_as_the_reference_is_the_deflator_s_average_over_it():
    """Reference 2023: the 2023 average of the CPI (12 months averaging 100
    by construction) is 100, so each real value is nominal / (CPI / 100)."""
    periods = pd.date_range("2023-01-01", periods=15, freq="MS")
    cpi = pd.Series([94.5 + i for i in range(15)], index=periods)   # 2023 mean = 100.0
    nominal = pd.Series(1000.0, index=periods)
    result = dfl.deflate(nominal, cpi, reference=2023, deflator_name="CPI")
    assert result.reference == "2023"
    assert len(result.reference_periods) == 12
    assert result.real.loc["2024-03-01"] == pytest.approx(1000.0 / (108.5 / 100.0))
    with pytest.raises(dfl.DeflationError, match="needs all 12 periods"):
        dfl.deflate(nominal, cpi, reference=2024)


def test_a_monthly_series_and_a_quarterly_deflator_are_not_silently_resampled():
    monthly = pd.Series(range(1, 7), index=pd.date_range("2024-01-01", periods=6, freq="MS"),
                        dtype=float)
    quarterly = pd.Series([100.0, 102.0], index=pd.date_range("2024-01-01", periods=2,
                                                               freq="QS"))
    with pytest.raises(dfl.AlignmentError, match="monthly and the deflator is quarterly"):
        dfl.deflate(monthly, quarterly, reference="2024-01-01", nominal_name="sales",
                    deflator_name="the deflator")
    # the explicit route: convert, say so, deflate
    converted = dfl.to_frequency(monthly, "Q", how="sum")
    assert converted.tolist() == [6.0, 15.0]
    result = dfl.deflate(converted, quarterly, reference="2024-01-01", nominal_name="sales",
                         conversion_note="sales summed from monthly to quarterly")
    assert result.frequency == "Q"
    assert result.real.tolist() == pytest.approx([6.0, 15.0 / 1.02])
    assert "summed from monthly to quarterly" in result.notes[0]


def test_a_quarter_with_two_months_is_not_a_quarter():
    months = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0],
                       index=pd.date_range("2024-01-01", periods=5, freq="MS"))
    assert dfl.to_frequency(months, "Q").tolist() == [2.0]
    with pytest.raises(dfl.AlignmentError, match="invent the periods"):
        dfl.to_frequency(QUARTERLY_CPI, "M")


def test_nominal_periods_the_deflator_does_not_cover_are_refused_or_recorded():
    periods = pd.date_range("2024-01-01", periods=4, freq="MS")
    nominal = pd.Series([10.0, 11.0, 12.0, 13.0], index=periods)
    deflator = pd.Series([100.0, 101.0, 102.0], index=periods[:3])
    with pytest.raises(dfl.AlignmentError, match="not extrapolated"):
        dfl.deflate(nominal, deflator, reference=periods[0])
    result = dfl.deflate(nominal, deflator, reference=periods[0], allow_partial=True)
    assert len(result.real) == 3
    assert "left out (2024-04)" in result.notes[0]


def test_a_reference_period_the_deflator_does_not_observe_is_refused():
    with pytest.raises(dfl.DeflationError, match="not in the deflator"):
        dfl.deflate(pd.Series([1.0, 2.0], index=MONTHS[:2]),
                    pd.Series([100.0, 101.0], index=MONTHS[:2]), reference="2020-01-01")


def test_an_irregular_series_has_no_frequency():
    irregular = pd.Series([1.0, 2.0, 3.0],
                          index=pd.DatetimeIndex(["2024-01-01", "2024-02-01", "2024-05-01"]))
    with pytest.raises(dfl.AlignmentError, match="not regularly spaced"):
        dfl.frequency_of(irregular)


def test_real_growth_is_exact_and_the_approximation_is_shown_with_its_error():
    """Nominal +10%, prices +8%: real growth is 1.10 / 1.08 - 1 = 1.8519%,
    not the 2% that 'nominal minus inflation' gives; the error is 0.148 pp."""
    periods = MONTHS[:2]
    result = dfl.deflate(pd.Series([100.0, 110.0], index=periods),
                         pd.Series([100.0, 108.0], index=periods), reference=periods[0])
    row = dfl.real_growth(result).iloc[1]
    assert row["real_growth_pct"] == pytest.approx((1.10 / 1.08 - 1) * 100)
    assert row["approximation_pct"] == pytest.approx(2.0)
    assert row["approximation_error_pp"] == pytest.approx(2.0 - (1.10 / 1.08 - 1) * 100)


def test_constant_prices_and_a_volume_index():
    """Current-price sales 500, 540, 594 while prices rise 0%, 8%, 10%: at
    January prices the volumes are 500, 500, 540, and the volume index is
    100, 100, 108."""
    periods = MONTHS
    result = dfl.constant_prices(pd.Series([500.0, 540.0, 594.0], index=periods),
                                 pd.Series([100.0, 108.0, 110.0], index=periods),
                                 reference=periods[0], name="retail sales")
    assert result.real.tolist() == pytest.approx([500.0, 500.0, 540.0])
    volume = dfl.volume_index(result)
    assert volume.tolist() == pytest.approx([100.0, 100.0, 108.0])
    assert volume.name == "volume index (Jan 2024 = 100)"
    assert "constant price value" in result.label


def test_ppp_conversion_holds_an_annual_ppp_through_the_year_only_when_asked():
    """Monthly spending of 1,300 and 1,430 local currency units; the 2024 PPP
    is 1.3 local units per international dollar: 1,000 and 1,100 dollars."""
    months = MONTHS[:2]
    spending = pd.Series([1300.0, 1430.0], index=months)
    ppp = pd.Series([1.25, 1.3], index=pd.DatetimeIndex(["2023-01-01", "2024-01-01"]))
    with pytest.raises(dfl.AlignmentError, match="broadcast_annual=True"):
        dfl.ppp_convert(spending, ppp)
    result = dfl.ppp_convert(spending, ppp, broadcast_annual=True, ppp_name="the 2024 PPP")
    assert result.converted.tolist() == pytest.approx([1000.0, 1100.0])
    assert "held constant through the year" in result.label


def test_the_price_level_index_compares_ppp_with_the_exchange_rate():
    years = pd.DatetimeIndex(["2023-01-01", "2024-01-01"])
    level = dfl.price_level_index(pd.Series([1.2, 1.3], index=years),
                                  pd.Series([1.5, 1.3], index=years))
    assert level.tolist() == pytest.approx([80.0, 100.0])


# ---------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------
class FakeUpload:
    def __init__(self, name: str, text: str) -> None:
        self.name, self._data = name, text.encode("utf-8")
        self.size = len(self._data)

    def getvalue(self) -> bytes:
        return self._data


def _csv(values: pd.Series) -> str:
    return "period,value\n" + "\n".join(f"{p:%Y-%m-%d},{v}" for p, v in values.items())


MONTHLY = pd.Series([2000.0 + 10 * i for i in range(12)],
                    index=pd.date_range("2024-01-01", periods=12, freq="MS"))
MONTHLY_CPI = pd.Series([110.0 + 0.5 * i for i in range(12)], index=MONTHLY.index)
QUARTERLY_CPI = pd.Series([110.0, 111.5, 113.0, 114.5],
                          index=pd.date_range("2024-01-01", periods=4, freq="QS"))


@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "deflation.db"))
    monkeypatch.setenv("PRICELAB_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import ledger, registry, security  # noqa: F401  register tables
    from pricelab.data import classification, mapping, validation  # noqa: F401
    db.init_db()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def _uploads(monkeypatch, files: dict[str, FakeUpload]) -> None:
    monkeypatch.setattr(streamlit, "file_uploader",
                        lambda label, *a, **k: next((f for key, f in files.items()
                                                     if label.startswith(key)), None))


def _page(state: dict | None = None) -> AppTest:
    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.deflation as page
set_current_role(Role.ANALYST)
st.session_state.setdefault("pricelab_username", "analyst1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=120)
    for key, value in (state or {}).items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, at.exception
    return at


def test_the_page_deflates_and_names_the_deflator_and_reference(deployment, monkeypatch):
    _uploads(monkeypatch, {"Nominal series": FakeUpload("earnings.csv", _csv(MONTHLY)),
                           "Deflator": FakeUpload("cpi.csv", _csv(MONTHLY_CPI))})
    at = _page({"df_reference": "2024-01", "df_kind": "Real wage"})
    next(b for b in at.button if b.label == "Deflate").click().run()
    assert not at.exception, at.exception

    result = at.session_state["df_result"]
    assert result.reference == "Jan 2024" and result.deflator_name == "cpi.csv"
    assert result.real.iloc[-1] == pytest.approx(2110.0 / (115.5 / 110.0))
    message = at.success[0].value
    assert "deflated by cpi.csv (Jan 2024 = 100)" in message
    assert "constant Jan 2024 prices" in message
    with db.session_scope() as s:
        events = s.query(audit.AuditEventORM).filter_by(action=audit.DEFLATION).all()
    assert len(events) == 1 and '"reference": "Jan 2024"' in events[0].params_json


def test_the_page_shows_a_frequency_mismatch_instead_of_resampling(deployment, monkeypatch):
    _uploads(monkeypatch, {"Nominal series": FakeUpload("earnings.csv", _csv(MONTHLY)),
                           "Deflator": FakeUpload("cpi_q.csv", _csv(QUARTERLY_CPI))})
    at = _page({"df_reference": "2024-01"})
    next(b for b in at.button if b.label == "Deflate").click().run()
    assert not at.exception, at.exception
    assert any("monthly" in e.value and "quarterly" in e.value and "not resampled silently"
               in e.value for e in at.error)
    assert "df_result" not in at.session_state

    # choosing a conversion is what makes it happen, and it is recorded
    at.selectbox(key="df_conversion").select(
        "Nominal series to the deflator's frequency, by its sum (a flow)").run()
    next(b for b in at.button if b.label == "Deflate").click().run()
    assert not at.exception, at.exception
    result = at.session_state["df_result"]
    assert result.frequency == "Q"
    assert any("converted from monthly to quarterly by its sum" in n for n in result.notes)


def test_the_page_uses_a_series_of_the_compiled_index_as_the_deflator(deployment, monkeypatch):
    indices = pd.DataFrame({"All items": MONTHLY_CPI / 1.1})
    _uploads(monkeypatch, {"Nominal series": FakeUpload("earnings.csv", _csv(MONTHLY))})
    at = _page({"analysis": {"result": {"indices": indices}, "label": "my run",
                             "narrative": None, "decisions": []},
                "df_reference": "2024"})
    next(b for b in at.button if b.label == "Deflate").click().run()
    assert not at.exception, at.exception
    result = at.session_state["df_result"]
    assert result.deflator_name == "the compiled All items index (my run)"
    assert result.reference == "2024"


def test_the_page_converts_at_ppp_only_when_the_broadcast_is_chosen(deployment, monkeypatch):
    ppp = pd.Series([1.3], index=pd.DatetimeIndex(["2024-01-01"]))
    ppp = pd.concat([pd.Series([1.25], index=pd.DatetimeIndex(["2023-01-01"])), ppp])
    _uploads(monkeypatch, {"Values in local currency": FakeUpload("spend.csv", _csv(MONTHLY)),
                           "PPPs": FakeUpload("ppp.csv", _csv(ppp))})
    at = _page()
    next(b for b in at.button if b.label == "Convert").click().run()
    assert any("broadcast_annual=True" in e.value for e in at.error)
    at.checkbox(key="df_ppp_broadcast").check().run()
    next(b for b in at.button if b.label == "Convert").click().run()
    assert not at.exception, at.exception
    assert at.session_state["df_ppp_result"].converted.iloc[0] == pytest.approx(2000.0 / 1.3)


def test_a_viewer_is_refused_the_deflation_page(deployment):
    import pages.deflation as page
    from pricelab.core.models import Role
    from pricelab.core.security import AccessDenied, set_current_role

    set_current_role(Role.VIEWER)
    try:
        with pytest.raises(AccessDenied):
            page.render()
    finally:
        set_current_role(Role.COMPILER)
