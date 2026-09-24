"""Phase 6, Task 2: outlier detection and the review queue.

The claim under test is negative and absolute: no code path removes a quote
from an index without an analyst's decision and stated reason attached to
it. So alongside the tests that each screen finds what it should, there are
tests that detection changes nothing, that an unreviewed flag excludes
nothing, that a reason is required at four separate layers, and that a
rejected quote is marked rather than dropped -- the row survives, carrying
who took it out and why.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab import infer_schema, run_pipeline, standardise
from pricelab.core import audit, db, ledger
from pricelab.core.config import OutlierConfig, OutlierDecision, RunConfig, get_settings
from pricelab.engine import outliers as ol

REPO_ROOT = Path(__file__).resolve().parents[1]
PRICES = REPO_ROOT / "supermarket_price_collection.xlsx"

# The bundled collection is the only one with a real season and real faults in
# it, so most of this module is about that file. Skipped rather than failed
# where it is absent, matching tests/test_phase3_hard_gate.py -- though the
# Dockerfile copies it in precisely so these do run in the image.
pytestmark = pytest.mark.skipif(not PRICES.exists(),
                                reason="fixture workbook not present")


def _panel(n_periods: int = 14, n_items: int = 12, *, drift: float = 0.004,
           spikes: dict[tuple[int, int], float] | None = None,
           seed: int = 3) -> pd.DataFrame:
    """A clean panel with optional injected price spikes.

    `spikes` maps (period index, item index) to a multiplier applied to that
    one quote, which is how a test says "this quote, and only this quote,
    is the error".
    """
    rng = np.random.default_rng(seed)
    periods = pd.date_range("2022-01-01", periods=n_periods, freq="MS")
    base = rng.uniform(4, 40, n_items)
    rows: list[dict[str, object]] = []
    for t, period in enumerate(periods):
        for i in range(n_items):
            price = float(base[i] * (1 + drift) ** t * rng.uniform(0.995, 1.005))
            price *= (spikes or {}).get((t, i), 1.0)
            rows.append({"period": period, "category": "Staples", "item_id": f"I{i}",
                         "price_clean": price, "price_imputed": price, "imputation": ""})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------
# Price relatives
# ---------------------------------------------------------------------
def test_relatives_are_built_per_item_and_a_first_observation_has_none():
    relatives = ol.price_relatives(_panel(n_periods=3, n_items=2))
    assert len(relatives) == 2 * 2          # two items, two of three periods
    assert relatives["period"].min() == pd.Timestamp("2022-02-01")
    assert (relatives["ratio"] > 0).all()
    assert relatives["log_ratio"].to_numpy() == pytest.approx(
        np.log(relatives["ratio"].to_numpy()))


def test_a_frame_with_no_usable_price_column_is_refused():
    with pytest.raises(ol.OutlierError, match="price_clean"):
        ol.price_relatives(pd.DataFrame({"period": [], "item_id": []}))
    with pytest.raises(ol.OutlierError, match="item_id"):
        ol.price_relatives(pd.DataFrame({"period": [1], "price_clean": [1.0]}))


# ---------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------
@pytest.mark.parametrize("screen", ol.METHODS)
def test_every_screen_finds_an_injected_spike(screen):
    """One quote multiplied by four, in a panel that is otherwise flat. A
    screen that misses it is not screening."""
    frame = _panel(spikes={(6, 3): 4.0})
    cfg = OutlierConfig(methods=(screen,))
    flags = ol.SCREENS[screen](ol.price_relatives(frame), cfg)
    assert not flags.empty
    caught = flags[(flags["item_id"] == "I3")
                   & (flags["period"] == pd.Timestamp("2022-07-01"))]
    assert len(caught) == 1
    assert caught.iloc[0]["ratio"] == pytest.approx(4.0, rel=0.05)
    assert set(flags.columns) == set(ol.FLAG_COLUMNS)


@pytest.mark.parametrize("screen", ol.METHODS)
def test_no_screen_flags_a_panel_with_nothing_wrong_with_it(screen):
    flags = ol.SCREENS[screen](ol.price_relatives(_panel()), OutlierConfig(methods=(screen,)))
    assert flags.empty


def test_the_deadband_stops_a_tight_cell_flagging_every_rounding():
    """Without it, a category whose relatives are tightly clustered has
    fences a fraction of a percent wide and the queue becomes the data."""
    frame = _panel(n_items=20)
    wide = ol.detect(frame, OutlierConfig(min_change_pct=0.0))
    narrow = ol.detect(frame, OutlierConfig(min_change_pct=5.0))
    assert wide.flag_rate > narrow.flag_rate
    assert narrow.flag_rate == 0.0


def test_the_ratio_screen_covers_the_thin_cells_the_others_skip():
    """Four quotes in a cell have no usable quartiles, which is exactly
    where a doubled price would otherwise go through unnoticed."""
    frame = _panel(n_periods=4, n_items=3, spikes={(2, 1): 3.0})
    cfg = OutlierConfig(min_cell_size=5)
    relatives = ol.price_relatives(frame)
    assert ol.tukey_fences(relatives, cfg).empty
    assert ol.quartile_method(relatives, cfg).empty
    assert ol.hidiroglou_berthelot(relatives, cfg).empty
    assert not ol.ratio_screen(relatives, cfg).empty


def test_hidiroglou_berthelot_weights_by_how_much_the_quote_matters():
    """The exponent is what distinguishes this screen: at u = 1 a cheap item
    must move much further than a dear one before anyone is asked to look."""
    frame = _panel(n_items=14, spikes={(6, 0): 1.6, (6, 1): 1.6})
    # Make item 0 the cheapest by a long way and item 1 the dearest.
    frame.loc[frame["item_id"] == "I0", ["price_clean", "price_imputed"]] *= 0.02
    frame.loc[frame["item_id"] == "I1", ["price_clean", "price_imputed"]] *= 8.0
    relatives = ol.price_relatives(frame)

    flat = ol.hidiroglou_berthelot(relatives, OutlierConfig(hb_u=0.0, hb_c=4.0))
    weighted = ol.hidiroglou_berthelot(relatives, OutlierConfig(hb_u=1.0, hb_c=4.0))
    at_period = pd.Timestamp("2022-07-01")
    flat_items = set(flat[flat["period"] == at_period]["item_id"])
    weighted_items = set(weighted[weighted["period"] == at_period]["item_id"])
    # Both moved by the same ratio; weighting by magnitude keeps the large
    # one and drops the small one.
    assert {"I0", "I1"} <= flat_items
    assert "I1" in weighted_items and "I0" not in weighted_items


def test_the_quartile_method_and_tukey_differ_on_a_skewed_cell():
    """Tukey measures from the quartiles outwards symmetrically; the
    quartile method scales each side by its own half of the distribution, so
    a cell skewed by a promotion is not cut off merely for being long on one
    side."""
    rng = np.random.default_rng(11)
    periods = pd.date_range("2022-01-01", periods=2, freq="MS")
    # Twenty items; most flat, a long tail of discounts, one big rise.
    prices = np.r_[np.full(14, 1.0), rng.uniform(0.60, 0.85, 5), [1.45]]
    rows = []
    for i, factor in enumerate(prices):
        rows.append({"period": periods[0], "category": "c", "item_id": f"I{i}",
                     "price_clean": 10.0, "price_imputed": 10.0})
        rows.append({"period": periods[1], "category": "c", "item_id": f"I{i}",
                     "price_clean": 10.0 * factor, "price_imputed": 10.0 * factor})
    relatives = ol.price_relatives(pd.DataFrame(rows))
    cfg = OutlierConfig(tukey_k=1.5, quartile_ratio=2.5)
    tukey = set(ol.tukey_fences(relatives, cfg)["item_id"])
    quartile = set(ol.quartile_method(relatives, cfg)["item_id"])
    assert tukey != quartile


def test_the_config_rejects_screens_and_bounds_it_does_not_have():
    for kwargs, match in (
            ({"methods": ("tukey", "zscore")}, "unknown outlier screen"),
            ({"hb_u": 2.0}, "exponent between 0 and 1"),
            ({"ratio_low": 2.0, "ratio_high": 1.0}, "0 < low < high")):
        with pytest.raises(ValueError, match=match):
            OutlierConfig(**kwargs)


# ---------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------
def test_the_queue_collapses_the_screens_onto_one_row_per_quote_and_counts_them():
    scan = ol.detect(_panel(spikes={(6, 3): 4.0, (9, 7): 0.25}))
    # Two flags per spike, not one: a single bad quote makes two anomalous
    # movements, the jump onto it and the fall back off it, and both are
    # genuinely surprising. A screen that reported only the first would be
    # asserting it knew which of the two periods was wrong.
    assert len(scan.queue) == 4
    assert set(scan.queue["item_id"]) == {"I3", "I7"}
    assert set(scan.queue["period"]) == {
        pd.Timestamp("2022-07-01"), pd.Timestamp("2022-08-01"),
        pd.Timestamp("2022-10-01"), pd.Timestamp("2022-11-01")}
    assert (scan.queue["n_methods"] >= 1).all()
    assert scan.queue["n_methods"].is_monotonic_decreasing
    # the long form still says which screen caught which quote
    assert len(scan.flags) >= len(scan.queue)
    assert set(scan.flags["method"]) <= set(ol.METHODS)


def test_detection_changes_nothing_about_the_data():
    """It produces a list of questions. Nothing else."""
    frame = _panel(spikes={(6, 3): 4.0})
    before = frame.copy()
    ol.detect(frame)
    pd.testing.assert_frame_equal(frame, before)


def test_an_unreviewed_flag_excludes_nothing():
    frame = _panel(spikes={(6, 3): 4.0})
    scan = ol.detect(frame)
    assert len(scan.queue) == 2                  # the jump and the fall back
    out, report = ol.apply_decisions(frame, [])
    assert report.excluded == 0
    assert report.reviewed == 0
    assert not out["outlier_excluded"].any()
    assert out["price_clean"].notna().all()


def test_pending_hides_what_has_been_decided_and_decided_returns_it():
    scan = ol.detect(_panel(spikes={(6, 3): 4.0, (9, 7): 0.25}))
    first = scan.queue.iloc[0]
    entry = OutlierDecision(period=str(pd.Timestamp(first["period"]).date()),
                            item_id=str(first["item_id"]), decision="accept",
                            reason="checked with the shop; a genuine promotion",
                            analyst="tester1")
    assert len(ol.pending(scan, [entry])) == len(scan.queue) - 1
    taken = ol.decided(scan, [entry])
    assert len(taken) == 1
    assert taken.iloc[0]["reason"].startswith("checked with the shop")
    assert taken.iloc[0]["methods"]


# ---------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------
def test_a_decision_without_a_reason_is_refused_by_the_model():
    with pytest.raises(ValueError, match="must state a reason"):
        OutlierDecision(period="2022-07-01", item_id="I3", decision="reject", reason="   ")
    with pytest.raises(ValueError, match="must be"):
        OutlierDecision(period="2022-07-01", item_id="I3", decision="delete", reason="x")


def test_a_rejected_quote_is_marked_rather_than_dropped():
    """The whole difference between an exclusion and a deletion. After this
    runs the collection still contains every quote that was ever collected,
    and the one taken out of the index says who took it out and why."""
    frame = _panel(spikes={(6, 3): 4.0})
    entry = OutlierDecision(period="2022-07-01", item_id="I3", category="Staples",
                            decision="reject", reason="keyed in pence, not pounds",
                            analyst="tester1")
    out, report = ol.apply_decisions(frame, [entry])

    assert len(out) == len(frame)                    # nothing dropped
    row = out[(out["item_id"] == "I3") & (out["period"] == pd.Timestamp("2022-07-01"))]
    assert len(row) == 1
    assert bool(row["outlier_excluded"].iloc[0])
    assert row["outlier_reason"].iloc[0] == "keyed in pence, not pounds"
    assert row["outlier_analyst"].iloc[0] == "tester1"
    assert pd.isna(row["price_clean"].iloc[0])       # the index cannot use it
    assert report.excluded == 1


def test_an_accepted_or_annotated_quote_keeps_its_price_and_its_record():
    frame = _panel(spikes={(6, 3): 4.0})
    entries = [
        OutlierDecision(period="2022-07-01", item_id="I3", decision="accept",
                        reason="genuine: a supply shock, confirmed", analyst="tester1"),
        OutlierDecision(period="2022-08-01", item_id="I4", decision="annotate",
                        reason="unusual but plausible; watching it", analyst="tester2")]
    out, report = ol.apply_decisions(frame, entries)
    kept = out[(out["item_id"] == "I3") & (out["period"] == pd.Timestamp("2022-07-01"))]
    assert not bool(kept["outlier_excluded"].iloc[0])
    assert kept["price_clean"].notna().iloc[0]
    assert kept["outlier_decision"].iloc[0] == "accept"
    assert report.excluded == 0
    assert report.decisions == {"accept": 1, "reject": 0, "annotate": 1}


def test_exclusions_are_reported_as_a_share_of_the_quotes_they_would_have_fed():
    """In the units imputation is already reported in, and not as a share of
    the decisions taken -- a reviewer who rejected one of one flag has
    excluded one quote, not the whole index."""
    frame = _panel(n_periods=10, n_items=10, spikes={(5, 2): 4.0})
    entry = OutlierDecision(period="2022-06-01", item_id="I2", category="Staples",
                            decision="reject", reason="confirmed keying error",
                            analyst="tester1")
    _, report = ol.apply_decisions(frame, [entry])
    assert report.quotes == 100
    assert report.excluded == 1
    assert report.share == pytest.approx(0.01)
    assert report.by_category.loc["Staples", "share"] == pytest.approx(0.01)
    assert report.by_period.loc[pd.Timestamp("2022-06-01"), "excluded"] == 1

    note = ol.exclusion_note(report)
    assert "1.00% of the 100 quotes" in note
    assert "no quote is removed without one" in note
    assert ol.exclusion_note(None) == ""


def test_two_decisions_on_the_same_quote_are_refused_by_the_config():
    entries = [
        OutlierDecision(period="2022-07-01", item_id="I3", decision="accept", reason="a"),
        OutlierDecision(period="2022-07-01", item_id="I3", decision="reject", reason="b")]
    with pytest.raises(ValueError, match="one quote has one decision"):
        OutlierConfig(entries=entries)


# ---------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------
@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "outliers.db"))
    monkeypatch.setenv("PRICELAB_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import registry, security  # noqa: F401  register tables
    from pricelab.data import classification, mapping, validation  # noqa: F401
    db.init_db()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def test_the_ledger_keeps_one_live_decision_per_quote_and_withdraws_the_rest(database):
    entry = OutlierDecision(period="2022-07-01", item_id="I3", decision="accept",
                            reason="looked genuine", analyst="alice")
    changed = entry.model_copy(update={"decision": "reject",
                                       "reason": "the shop confirmed a keying error"})
    with db.session_scope() as session:
        ledger.record_outlier_decision(session, "alice", "hash1", entry)
        ledger.record_outlier_decision(session, "bob", "hash1", changed)

        live = ledger.load_outlier_decisions(session, "hash1")
        assert len(live) == 1
        assert live[0].decision == "reject"
        # The first decision is not deleted: the trail shows that somebody
        # decided one thing and then decided another.
        everything = ledger.load_outlier_decisions(session, "hash1", include_withdrawn=True)
        assert len(everything) == 2
        withdrawn = [r for r in everything if r.withdrawn_at]
        assert withdrawn[0].decision == "accept"
        assert "superseded" in (withdrawn[0].withdrawal_reason or "")

        entries = ledger.active_outlier_entries(session, "hash1")
        assert [e.decision for e in entries] == ["reject"]


def test_the_ledger_refuses_a_decision_with_no_reason_and_a_withdrawal_with_none(database):
    with db.session_scope() as session:
        bare = OutlierDecision.model_construct(
            period="2022-07-01", item_id="I3", category="", method="", statistic=0.0,
            decision="reject", reason="  ", analyst="alice", decided_at="")
        with pytest.raises(ValueError, match="must state a reason"):
            ledger.record_outlier_decision(session, "alice", "hash1", bare)

        good = OutlierDecision(period="2022-07-01", item_id="I3", decision="reject",
                               reason="keying error", analyst="alice")
        record = ledger.record_outlier_decision(session, "alice", "hash1", good)
        with pytest.raises(ValueError, match="needs a reason"):
            ledger.withdraw_outlier_decision(session, "alice", record.id, "")


def test_decisions_are_keyed_by_the_data_not_the_run(database):
    """So the judgements come back when the same collection is uploaded
    again, exactly as the quality adjustment ledger's do."""
    entry = OutlierDecision(period="2022-07-01", item_id="I3", decision="reject",
                            reason="keying error", analyst="alice")
    with db.session_scope() as session:
        ledger.record_outlier_decision(session, "alice", "hash-a", entry)
        assert len(ledger.active_outlier_entries(session, "hash-a")) == 1
        assert ledger.active_outlier_entries(session, "hash-b") == []


# ---------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------
def test_the_pipeline_screens_and_applies_decisions_between_the_ledger_and_imputation():
    raw = pd.read_excel(PRICES)
    collection = standardise(raw, infer_schema(raw))
    res = run_pipeline(collection, RunConfig(outlier=OutlierConfig(enabled=True)))
    scan = res["outlier_scan"]
    assert scan is not None and len(scan.queue) > 0
    assert 0 < scan.flag_rate < 0.10          # a reviewable queue, not the data
    assert res["outlier_exclusions"].excluded == 0

    top = scan.queue.iloc[0]
    entry = OutlierDecision(period=str(pd.Timestamp(top["period"]).date()),
                            item_id=str(top["item_id"]), category=str(top["category"]),
                            decision="reject", reason="confirmed error", analyst="tester1")
    reviewed = run_pipeline(collection, RunConfig(
        outlier=OutlierConfig(enabled=True, entries=[entry])))
    assert reviewed["outlier_exclusions"].excluded == 1
    # The hole the exclusion left was filled by the run's own imputation,
    # which is why the screening sits before that stage.
    imputed = reviewed["imputed"]
    row = imputed[(imputed["item_id"] == entry.item_id)
                  & (imputed["period"] == pd.Timestamp(entry.period))]
    assert len(row) == 1
    assert bool(row["outlier_excluded"].iloc[0])


def test_a_disabled_outlier_section_runs_no_screen_at_all():
    raw = pd.read_excel(PRICES)
    res = run_pipeline(standardise(raw, infer_schema(raw)), RunConfig())
    assert res["outlier_scan"] is None
    assert res["outlier_exclusions"] is None


def test_the_method_note_reports_the_screens_and_the_exclusions():
    from pricelab.reporting.report import outlier_note

    raw = pd.read_excel(PRICES)
    collection = standardise(raw, infer_schema(raw))
    res = run_pipeline(collection, RunConfig(outlier=OutlierConfig(enabled=True)))
    note = outlier_note(res)
    assert "price relatives were flagged" in note
    assert "None has been reviewed yet" in note
    assert "without an analyst's name and stated reason" in note
    assert outlier_note({"outlier_scan": None}) == ""


# ---------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------
class FakeUpload:
    def __init__(self, name: str, data: bytes) -> None:
        self.name, self._data, self.size = name, data, len(data)

    def getvalue(self) -> bytes:
        return self._data


@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "outlier_page.db"))
    monkeypatch.setenv("PRICELAB_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import registry, security  # noqa: F401  register tables
    from pricelab.data import classification, mapping, validation  # noqa: F401
    db.init_db()
    from pricelab.core.cache import reset_analysis_cache
    from pricelab.core.ratelimit import reset_upload_limiter
    reset_analysis_cache()
    reset_upload_limiter()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def _ingest(monkeypatch) -> dict:
    monkeypatch.setattr(streamlit, "file_uploader",
                        lambda *a, **k: FakeUpload("prices.xlsx", PRICES.read_bytes()))
    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.ingest as page
set_current_role(Role.COMPILER)
st.session_state.setdefault("pricelab_username", "tester1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    next(b for b in at.button if b.label == "Confirm column mapping").click().run()
    assert not at.exception, at.exception
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: None)
    return {k: v for k, v in at.session_state.filtered_state.items()
            if not str(k).startswith("$$")}


def _page(state: dict, role: str = "COMPILER") -> AppTest:
    script = f"""
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.outliers as page
set_current_role(Role.{role})
st.session_state.setdefault("pricelab_username", "tester1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=300)
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, at.exception
    return at


def test_the_page_shows_a_reviewable_queue_with_the_screens_that_caught_each_quote(
        deployment, monkeypatch):
    at = _page(_ingest(monkeypatch))
    labels = [m.label for m in at.metric]
    assert "Price relatives screened" in labels
    assert "Flagged" in labels
    assert "Awaiting review" in labels
    flagged = next(m for m in at.metric if m.label == "Flagged")
    assert int(str(flagged.value).replace(",", "")) > 0

    frames = [d.value for d in at.dataframe]
    queue = next(f for f in frames if "n_methods" in getattr(f, "columns", []))
    assert "methods" in queue.columns
    assert queue["n_methods"].max() >= 1


def test_the_page_records_a_decision_with_its_analyst_and_reason(deployment, monkeypatch):
    """The acceptance criterion: every decision reaches the audit log with a
    name and a reason against it, and no path removes a quote without one."""
    state = _ingest(monkeypatch)
    at = _page(state)
    before = len(next(m for m in at.metric if m.label == "Awaiting review").value)
    del before

    at.session_state["ol_decision"] = "reject"
    at.session_state["ol_reason"] = "the shop confirmed the price was keyed in pence"
    next(f for f in at.button if f.label == "Record decision").click().run()
    assert not at.exception, at.exception

    with db.session_scope() as session:
        saved = ledger.load_outlier_decisions(session, state["content_hash"])
        assert len(saved) == 1
        assert saved[0].decision == "reject"
        assert saved[0].reason == "the shop confirmed the price was keyed in pence"
        assert saved[0].analyst == "tester1"

        events = session.query(audit.AuditEventORM).filter_by(
            action=audit.OUTLIER_DECISION).all()
        assert len(events) == 1
        assert "tester1" in events[0].actor
        assert "keyed in pence" in events[0].params_json


def test_the_page_refuses_a_decision_with_no_reason(deployment, monkeypatch):
    state = _ingest(monkeypatch)
    at = _page(state)
    at.session_state["ol_decision"] = "reject"
    at.session_state["ol_reason"] = "   "
    next(f for f in at.button if f.label == "Record decision").click().run()
    assert not at.exception, at.exception
    assert any("needs a reason" in e.value for e in at.error)
    with db.session_scope() as session:
        assert ledger.load_outlier_decisions(session, state["content_hash"]) == []


def test_the_page_reports_the_exclusion_share_after_a_rejection(deployment, monkeypatch):
    state = _ingest(monkeypatch)
    at = _page(state)
    at.session_state["ol_decision"] = "reject"
    at.session_state["ol_reason"] = "confirmed keying error"
    next(f for f in at.button if f.label == "Record decision").click().run()

    at = _page(state)
    labels = [m.label for m in at.metric]
    assert "Quotes excluded" in labels
    assert "Share of the collection" in labels
    assert any("no quote is removed without one" in i.value for i in at.info)


def test_a_viewer_is_refused_the_outliers_page(deployment):
    import pages.outliers as page
    from pricelab.core.models import Role
    from pricelab.core.security import AccessDenied, set_current_role

    set_current_role(Role.VIEWER)
    try:
        with pytest.raises(AccessDenied, match="administrator"):
            page.render()
    finally:
        set_current_role(Role.COMPILER)


def test_the_page_withdraws_a_decision_with_a_reason_and_logs_it(deployment, monkeypatch):
    """Release wiring audit: `ledger.withdraw_outlier_decision` had no caller
    in the product. A decision is withdrawn, never deleted, with a reason,
    and the withdrawal reaches the audit log."""
    state = _ingest(monkeypatch)
    at = _page(state)
    at.session_state["ol_decision"] = "reject"
    at.session_state["ol_reason"] = "keyed in pence"
    next(f for f in at.button if f.label == "Record decision").click().run()
    assert not at.exception, at.exception

    next(b for b in at.button if b.label == "Withdraw decision").click().run()
    assert any("needs a reason" in e.value for e in at.error)
    at.text_input(key="ol_withdraw_reason").input("the shop's second receipt shows pounds").run()
    next(b for b in at.button if b.label == "Withdraw decision").click().run()
    assert not at.exception, at.exception
    with db.session_scope() as session:
        assert ledger.load_outlier_decisions(session, state["content_hash"]) == []
        kept = ledger.load_outlier_decisions(session, state["content_hash"],
                                             include_withdrawn=True)
        assert len(kept) == 1 and kept[0].withdrawal_reason.startswith("the shop's second")
        assert kept[0].withdrawn_by == "tester1"
        events = session.query(audit.AuditEventORM).filter_by(
            action=audit.OUTLIER_DECISION_WITHDRAWN).all()
        assert len(events) == 1 and "second receipt" in events[0].params_json
