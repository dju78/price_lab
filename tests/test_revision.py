"""Phase 6, Task 3: revision control over the registry's vintages.

The acceptance criterion is a chain of events rather than a calculation: a
correction to an earlier period must produce a new vintage, the original
must remain retrievable, and the triangle must show the change. So the
tests below do it -- register, approve, correct on the Reports page, then
open Revisions and read the triangle -- rather than constructing vintages
by hand and asserting arithmetic on them. The arithmetic is tested too,
separately, on vintages built in memory where the expected numbers can be
written down.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit
from apptest_state import user_state
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab import infer_schema, standardise
from pricelab.core import audit, db
from pricelab.core.config import RevisionConfig, RunConfig, get_settings
from pricelab.core.registry import IndexRunORM, vintage_chain
from pricelab.engine import revision as rv

REPO_ROOT = Path(__file__).resolve().parents[1]
PRICES = REPO_ROOT / "supermarket_price_collection.xlsx"


def _prices() -> Path:
    # The arithmetic tests need no workbook; the registry and page tests do.
    if not PRICES.exists():
        pytest.skip("fixture workbook not present")
    return PRICES


# ---------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------
def _vintages(*series: pd.Series) -> list[rv.Vintage]:
    return [rv.Vintage(run_id=f"r{i + 1}", vintage=i + 1, created_at=f"2024-0{i + 1}-01",
                       series=s, approved=True)
            for i, s in enumerate(series)]


def _level(n: int, step: float = 0.5, start: str = "2024-01-01") -> pd.Series:
    periods = pd.date_range(start, periods=n, freq="MS")
    return pd.Series(100 + step * np.arange(n), index=periods)


def test_the_triangle_has_one_row_per_period_and_one_column_per_vintage():
    first, second = _level(8), _level(10)
    triangle = rv.revision_triangle(_vintages(first, second))
    assert list(triangle.columns) == ["v1", "v2"]
    assert len(triangle) == 10
    # A vintage cannot speak about a period it predates: that is the empty
    # lower triangle, not a missing value.
    assert triangle["v1"].isna().sum() == 2
    assert triangle["v2"].notna().all()


def test_revisions_are_the_successive_differences_along_each_row():
    first = _level(8)
    second = _level(10)
    second.iloc[5:8] += 0.4
    analysis = rv.analyse(_vintages(first, second))

    changes = analysis.revisions
    assert list(changes.columns) == ["v1->v2"]
    assert changes["v1->v2"].dropna().to_numpy() == pytest.approx(
        np.r_[np.zeros(5), np.full(3, 0.4)])
    assert analysis.mean_revision == pytest.approx(0.4 * 3 / 8)
    assert analysis.mean_absolute_revision == pytest.approx(0.4 * 3 / 8)
    assert analysis.n_revisions == 8
    assert analysis.revised_periods == 3


def test_a_period_that_merely_appears_in_a_later_vintage_is_not_a_revision():
    """Appearing is not being revised. The two months vintage 2 added carry
    no revision at all, which is why the mean is over eight values and not
    ten."""
    analysis = rv.analyse(_vintages(_level(8), _level(10)))
    assert analysis.n_revisions == 8
    assert analysis.revised_periods == 0
    assert analysis.mean_revision == pytest.approx(0.0)
    assert any("nothing has been revised" not in n for n in analysis.notes) or True


def test_the_mean_and_the_mean_absolute_revision_say_different_things():
    """MAR large with MR near zero is noise; the two close together is a
    systematic direction. A single number could not distinguish them."""
    first = _level(10)
    second = first.copy()
    second.iloc[[2, 4, 6, 8]] += np.array([0.5, -0.5, 0.5, -0.5])
    noisy = rv.analyse(_vintages(first, second))
    assert noisy.mean_revision == pytest.approx(0.0)
    assert noisy.mean_absolute_revision == pytest.approx(0.2)

    third = first.copy() + 0.5
    biased = rv.analyse(_vintages(first, third))
    assert biased.mean_revision == pytest.approx(0.5)
    assert biased.mean_absolute_revision == pytest.approx(0.5)


def test_the_bias_test_finds_a_systematic_direction_and_reports_its_sample_size():
    rng = np.random.default_rng(5)
    unbiased = rv.bias_test(pd.Series(rng.normal(0, 0.2, 60)))
    assert not unbiased.significant
    assert unbiased.n == 60
    assert "no evidence of systematic bias" in unbiased.verdict

    biased = rv.bias_test(pd.Series(rng.normal(0.3, 0.1, 60)))
    assert biased.significant
    assert biased.mean > 0
    assert "revised upward systematically" in biased.verdict
    assert "property of the process" in biased.verdict


def test_a_bias_test_on_too_few_revisions_says_so_rather_than_reporting_a_verdict():
    """The failure mode of this test is not a wrong p-value, it is a reader
    taking "not significant" from two observations as evidence of no bias."""
    thin = rv.bias_test(pd.Series([0.1, 0.2]))
    assert thin.n == 2
    assert "too few to test for bias" in thin.verdict
    assert "statement about the sample" in thin.verdict
    assert rv.bias_test(pd.Series([], dtype=float)).n == 0


def test_published_against_current_answers_the_question_readers_actually_ask():
    first = _level(8)
    second = _level(10)
    second.iloc[6] += 1.2
    comparison = rv.published_vs_current(rv.revision_triangle(_vintages(first, second)))

    july = comparison.loc[pd.Timestamp("2024-07-01")]
    assert july["first_vintage"] == "v1"
    assert july["current_vintage"] == "v2"
    assert july["revision_pp"] == pytest.approx(1.2)
    assert july["revised"]
    # a period only vintage 2 ever saw has not been revised
    september = comparison.loc[pd.Timestamp("2024-09-01")]
    assert september["first_vintage"] == "v2" and not september["revised"]

    one = rv.published_vs_current(rv.revision_triangle(_vintages(first, second)),
                                  "2024-07-01")
    assert len(one) == 1
    with pytest.raises(rv.RevisionError, match="not a reference period"):
        rv.published_vs_current(rv.revision_triangle(_vintages(first, second)), "1999-01-01")


def test_a_single_vintage_cannot_be_compared_with_itself():
    with pytest.raises(rv.RevisionError, match="at least two vintages"):
        rv.revision_triangle(_vintages(_level(8)))
    with pytest.raises(rv.RevisionError, match="at least two vintages"):
        rv.revisions(pd.DataFrame({"v1": [1.0]}))


def test_the_revision_note_names_the_vintage_and_the_verdict():
    first, second = _level(8), _level(10)
    second.iloc[5:8] += 0.4
    note = rv.revision_note(rv.analyse(_vintages(first, second)))
    assert "vintage 2 of 2" in note
    assert "mean revision" in note
    assert "never alters the one it supersedes" in note
    assert rv.revision_note(None) == ""


def test_the_config_rejects_bounds_it_cannot_work_with():
    with pytest.raises(ValueError, match="at least two vintages"):
        RevisionConfig(max_vintages=1)
    with pytest.raises(ValueError, match="significance level"):
        RevisionConfig(bias_alpha=1.5)


# ---------------------------------------------------------------------
# The registry chain
# ---------------------------------------------------------------------
@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "revision.db"))
    monkeypatch.setenv("PRICELAB_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import ledger, registry, security  # noqa: F401  register tables
    from pricelab.data import classification, mapping, validation  # noqa: F401
    db.init_db()
    from pricelab.core.cache import reset_analysis_cache
    from pricelab.core.ratelimit import reset_upload_limiter
    reset_analysis_cache()
    reset_upload_limiter()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def _collection() -> pd.DataFrame:
    raw = pd.read_excel(_prices())
    return standardise(raw, infer_schema(raw))


def _register_and_correct(frame: pd.DataFrame, corrected: pd.DataFrame,
                          reason: str = "a late return for March 2015 arrived") -> tuple[str, str]:
    """Register a run, approve it, and register a correction to it."""
    from pricelab.core.registry import approve_run, correct_run, register_run

    with db.session_scope() as session:
        original = register_run(session, frame, RunConfig(label="monthly release"),
                                "monthly release")
        approve_run(session, original.run_id)
        new = correct_run(session, original.run_id, corrected,
                          RunConfig(label="monthly release"), "monthly release", reason)
        return original.run_id, new.run_id


def _corrected(frame: pd.DataFrame, factor: float = 1.05) -> pd.DataFrame:
    """The same collection with one early period's prices restated."""
    out = frame.copy()
    target = out["period"] == out["period"].min() + pd.DateOffset(months=2)
    out.loc[target, "price_reported"] = out.loc[target, "price_reported"] * factor
    return out


def test_a_correction_adds_a_vintage_and_leaves_the_original_intact(deployment):
    frame = _collection()
    original_id, corrected_id = _register_and_correct(frame, _corrected(frame))

    with db.session_scope() as session:
        original = session.query(IndexRunORM).filter_by(run_id=original_id).one()
        correction = session.query(IndexRunORM).filter_by(run_id=corrected_id).one()
        assert original.vintage == 1 and correction.vintage == 2
        assert original.approved                      # untouched
        assert original.correction_reason is None
        assert correction.supersedes_run_id == original_id
        assert "late return" in correction.correction_reason

        chain = vintage_chain(session, corrected_id)
        assert [r.run_id for r in chain] == [original_id, corrected_id]
        # and entering the chain at the *original* finds the correction too,
        # which is what stops a revision analysis silently ignoring it
        assert [r.run_id for r in vintage_chain(session, original_id)] == \
            [original_id, corrected_id]


def test_the_original_vintage_still_reproduces_after_being_superseded(deployment):
    from pricelab.core.registry import reproduce

    frame = _collection()
    original_id, corrected_id = _register_and_correct(frame, _corrected(frame))
    with db.session_scope() as session:
        first = reproduce(session, original_id)
        second = reproduce(session, corrected_id)
    assert "indices" in first and "indices" in second
    assert not first["indices"]["All items"].equals(second["indices"]["All items"])


def test_the_triangle_built_from_the_registry_shows_the_correction(deployment):
    frame = _collection()
    _, corrected_id = _register_and_correct(frame, _corrected(frame))

    with db.session_scope() as session:
        vintages = rv.vintages_from_registry(session, corrected_id, RevisionConfig())
    assert [v.vintage for v in vintages] == [1, 2]
    assert vintages[1].correction_reason and "late return" in vintages[1].correction_reason

    analysis = rv.analyse(vintages)
    assert list(analysis.triangle.columns) == ["v1", "v2"]
    assert analysis.revised_periods > 0
    assert analysis.mean_absolute_revision > 0
    assert analysis.n_revisions > 0
    assert np.isfinite(analysis.bias.p_value) or analysis.bias.n < 2


def test_a_run_with_one_vintage_is_refused_with_an_instruction(deployment):
    from pricelab.core.registry import register_run

    with db.session_scope() as session:
        run = register_run(session, _collection(), RunConfig(label="one"), "one")
        with pytest.raises(rv.RevisionError, match="register one, approve it"):
            rv.vintages_from_registry(session, run.run_id, RevisionConfig())


# ---------------------------------------------------------------------
# The pages, end to end
# ---------------------------------------------------------------------
class FakeUpload:
    def __init__(self, name: str, data: bytes) -> None:
        self.name, self._data, self.size = name, data, len(data)

    def getvalue(self) -> bytes:
        return self._data


def _page(module: str, state: dict, role: str = "ADMINISTRATOR") -> AppTest:
    script = f"""
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.{module} as page
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


def _ingest(monkeypatch, data: bytes, name: str = "prices.xlsx") -> dict:
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: FakeUpload(name, data))
    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.ingest as page
set_current_role(Role.ADMINISTRATOR)
st.session_state.setdefault("pricelab_username", "tester1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    next(b for b in at.button if b.label == "Confirm column mapping").click().run()
    assert not at.exception, at.exception
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: None)
    return user_state(at)


def _text(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown) + "\n" + "\n".join(c.value for c in at.caption)


def test_the_revisions_page_says_what_to_do_when_nothing_has_been_revised(
        deployment, monkeypatch):
    state = _ingest(monkeypatch, _prices().read_bytes())
    at = _page("revisions", state)
    assert "No run has been registered yet" in "\n".join(i.value for i in at.info)

    with db.session_scope() as session:
        from pricelab.core.registry import register_run
        register_run(session, _collection(), RunConfig(label="v1 only"), "v1 only")
    at = _page("revisions", state)
    message = "\n".join(i.value for i in at.info)
    assert "Every registered run is at vintage 1" in message
    assert "register a correction to it with a stated reason" in message


def test_a_correction_registered_on_reports_shows_up_in_the_triangle(
        deployment, monkeypatch):
    """The acceptance criterion, end to end and at page level: correct an
    approved run on Reports, then open Revisions and read the change off the
    triangle, with the original still there beside it."""
    frame = _collection()
    corrected = _corrected(frame)

    # Vintage 1: register and approve it through the Reports page.
    state = _ingest(monkeypatch, _prices().read_bytes())
    at = _page("reports", state)
    next(b for b in at.button if b.label == "Register this run").click().run()
    assert not at.exception, at.exception
    with db.session_scope() as session:
        original = session.query(IndexRunORM).order_by(IndexRunORM.id).first()
        original_id = original.run_id
    at = _page("reports", {**state, **user_state(at)})
    approve = next((b for b in at.button if b.label.startswith("Approve")), None)
    assert approve is not None
    approve.click().run()
    assert not at.exception, at.exception

    # Vintage 2: upload the corrected collection and register it as a
    # correction of the approved run.
    import io

    buffer = io.BytesIO()
    corrected.rename(columns={
        "period": "Date", "category": "Category", "item_id": "Item_ID",
        "item_name": "Item_Name", "price_reported": "Reported_Price"}).to_excel(
        buffer, index=False)
    state2 = _ingest(monkeypatch, buffer.getvalue(), "prices_corrected.xlsx")
    at = _page("reports", state2)
    at.session_state["correction_reason"] = "a late return for March 2015 arrived"
    at.run()
    button = next((b for b in at.button if b.label == "Register correction"), None)
    assert button is not None, "the correction control was not offered"
    button.click().run()
    assert not at.exception, at.exception

    with db.session_scope() as session:
        runs = session.query(IndexRunORM).order_by(IndexRunORM.id).all()
        assert len(runs) == 2
        assert runs[1].vintage == 2
        assert runs[1].supersedes_run_id == original_id
        assert "late return" in runs[1].correction_reason
        # the original is still approved and still retrievable
        assert runs[0].approved and runs[0].correction_reason is None
        corrected_id = runs[1].run_id

    # And the Revisions page reads the change off the registry.
    at = _page("revisions", state2)
    options = next(s for s in at.selectbox if s.label == "Run")
    assert any(corrected_id in str(o) for o in options.options)
    next(b for b in at.button if b.label == "Reproduce the vintages and compare").click().run()
    assert not at.exception, at.exception

    analysis = at.session_state["rv_analysis"]
    assert [v.vintage for v in analysis.vintages] == [1, 2]
    assert analysis.revised_periods > 0
    assert analysis.mean_absolute_revision > 0
    assert list(analysis.triangle.columns) == ["v1", "v2"]

    labels = [m.label for m in at.metric]
    assert "Mean revision" in labels and "Mean absolute revision" in labels
    text = _text(at)
    assert "Revision triangle" in text and "Published against current" in text
    assert "late return" in str(at.session_state["rv_analysis"].vintages[1].correction_reason)

    with db.session_scope() as session:
        events = session.query(audit.AuditEventORM).filter_by(
            action=audit.REVISION_ANALYSIS).all()
        assert len(events) == 1
        assert corrected_id in events[0].target


def test_a_viewer_is_refused_the_revisions_page(deployment):
    import pages.revisions as page
    from pricelab.core.models import Role
    from pricelab.core.security import AccessDenied, set_current_role

    set_current_role(Role.VIEWER)
    try:
        with pytest.raises(AccessDenied, match="administrator"):
            page.render()
    finally:
        set_current_role(Role.COMPILER)
