"""Release wiring audit (the Phase 10.5 audit repeated after Phases 5-9b):
each library path the audit found unreachable from a page, now wired, with
a page-level test.

- Audit page: rebuild a registered forecast or scenario and check its
  digests (`registry.reproduce_projection`, `scenarios.scenario_from_spec`).
- Audit page: replay the stored characteristics layers
  (`store.replay_characteristics`).
- Spatial page: Eurostat's published PPPs by category through the connector
  (`eurostat.ppp_comparison_inputs`), and price level indices
  (`spatial.price_level_indices`).
- Deflation page: the price level index beside a PPP conversion
  (`deflation.price_level_index`).
- Ingest page: the organisation's own classification tree
  (`classification.load_user_defined_tree`), which the pipeline then rolls
  a collection up through.

Outlier withdrawal and the correction audit event are tested beside their
siblings in test_outliers.py and test_wiring.py.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import responses
import streamlit
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab import infer_schema, run_pipeline, standardise
from pricelab.core import audit, db
from pricelab.core.config import RunConfig, get_settings

FIXTURES = Path(__file__).parent / "fixtures" / "connectors"


class FakeUpload:
    def __init__(self, name: str, data: bytes) -> None:
        self.name, self._data, self.size = name, data, len(data)

    def getvalue(self) -> bytes:
        return self._data


def _uploads(monkeypatch, files: dict[str, tuple[str, bytes]]) -> None:
    """Each uploader, by its key, sees its own file or nothing."""
    def fake(*_a, **k):
        entry = files.get(k.get("key", ""))
        return FakeUpload(*entry) if entry else None
    monkeypatch.setattr(streamlit, "file_uploader", fake)


@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "release.db"))
    monkeypatch.setenv("PRICELAB_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import ledger, registry, security  # noqa: F401  register tables
    from pricelab.data import classification, mapping, validation  # noqa: F401
    db.init_db()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def _page(page: str, state: dict | None = None, role: str = "COMPILER") -> AppTest:
    script = f"""
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.{page} as page
set_current_role(Role.{role})
st.session_state.setdefault("pricelab_username", "auditor1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=300)
    for key, value in (state or {}).items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, at.exception
    return at


def _button(at: AppTest, label: str):
    return next(b for b in at.button if b.label == label)


def _collection(months: int = 60, categories=("Bread", "Milk")) -> pd.DataFrame:
    rng = np.random.default_rng(5)
    rows = []
    for c, cat in enumerate(categories):
        for i in range(3):
            level = 5.0 + i
            for period in pd.date_range("2019-01-01", periods=months, freq="MS"):
                level *= 1 + 0.002 * (c + 1) + rng.normal(0, 0.004)
                rows.append({"Date": period, "Category": cat, "Item_ID": f"{cat}{i}",
                             "Item_Name": f"{cat} {i}", "Reported_Price": round(level, 4)})
    raw = pd.DataFrame(rows)
    return standardise(raw, infer_schema(raw))


# ---------------------------------------------------------------------
# Audit page
# ---------------------------------------------------------------------
def test_the_audit_page_rebuilds_a_registered_projection_and_checks_it(deployment):
    from pricelab.core import registry
    from pricelab.engine import forecasting as fc
    from pricelab.engine import scenarios as sc

    df = _collection()
    cfg = RunConfig()
    res = run_pipeline(df, cfg)
    series = res["indices"]["All items"]
    forecast = fc.forecast(series, fc.ForecastSpec(method="arima", horizon=3, origins=6))
    scenario = sc.build_scenario(series, [sc.Shock("wages", 4.0, 2, 0.2, "a source")],
                                 horizon=6, origins=12)
    with db.session_scope() as s:
        run_id = registry.register_run(s, df, cfg, "audited", result=res).run_id
        f_id = registry.register_projection(s, run_id, forecast, "analyst1").projection_id
        s_id = registry.register_projection(s, run_id, scenario, "analyst1").projection_id

    at = _page("audit_log", role="VIEWER")
    options = at.selectbox(key="au_projection").options
    assert any(f_id in o for o in options) and any(s_id in o for o in options)
    at.selectbox(key="au_projection").select(next(o for o in options if s_id in o)).run()
    at.button(key="au_projection_go").click().run()
    assert not at.exception, at.exception
    assert any("match the registered digests exactly" in m.value for m in at.success)
    assert any(c.value == scenario.label for c in at.caption)

    with db.session_scope() as s:
        row = s.query(registry.ProjectionRunORM).filter_by(projection_id=f_id).one()
        row.path_digest = "0" * 64
    at.selectbox(key="au_projection").select(next(o for o in options if f_id in o)).run()
    at.button(key="au_projection_go").click().run()
    assert any("does not reproduce" in e.value for e in at.error)
    with db.session_scope() as s:
        events = s.query(audit.AuditEventORM).filter_by(
            action=audit.PROJECTION_REPRODUCED).all()
        assert len(events) == 2


def test_the_audit_page_replays_the_characteristics_layers(deployment):
    from pricelab.data import store

    raw = pd.DataFrame({" item_id ": ["A1", "A2", "A3"], "size": ["1", "2", "3"],
                        "brand": ["x", "y", None]})
    store_dir = Path(get_settings().store_dir)
    _raw_path, _cleaned, log = store.write_characteristics_layers(raw, store_dir)

    at = _page("audit_log", role="VIEWER")
    at.button(key="au_characteristics_go").click().run()
    assert not at.exception, at.exception
    assert any("replays identically (3 items" in m.value for m in at.success)

    log_path = store_dir / "cleaned" / f"characteristics-{log.raw_content_hash}.log.json"
    tampered = store.CharacteristicsTransformationLog(
        raw_content_hash=log.raw_content_hash, cleaned_content_hash="0" * 64,
        steps=log.steps, created_at=log.created_at)
    log_path.write_text(tampered.to_json(), encoding="utf-8")
    at.button(key="au_characteristics_go").click().run()
    assert any("did not reproduce the logged cleaned layer" in e.value for e in at.error)


# ---------------------------------------------------------------------
# Spatial and deflation: Eurostat PPPs, price level indices
# ---------------------------------------------------------------------
def test_the_spatial_page_compares_eurostat_s_published_ppps_and_gives_price_levels(
        deployment, monkeypatch):
    from pages.spatial import EUROSTAT, PPP_KEY
    from pricelab.data.connectors.eurostat import BASE_URL

    rates = b"region,value\nDE,1.0\nFR,1.0\nPL,4.5\n"
    _uploads(monkeypatch, {"sp_rates": ("rates.csv", rates)})
    at = _page("spatial")
    at.radio(key="sp_source").set_value(EUROSTAT).run()
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, f"{BASE_URL}/{PPP_KEY}",
                 body=(FIXTURES / "eurostat_ppp_2023.json").read_text(encoding="utf-8"),
                 status=200)
        at.button(key="sp_fetch").click().run()
    assert not at.exception, at.exception
    assert any("categories from Eurostat prc_ppp_ind, 2023" in c.value for c in at.caption)
    assert at.selectbox(key="sp_base").value == "EU27_2020"
    at.button(key="sp_go").click().run()
    assert not at.exception, at.exception
    result = at.session_state["sp_result"]
    assert result.ppp.notna().sum() == 37
    assert any("left out of the weighted comparison" in c.value for c in at.caption)
    beside = next(d.value for d in at.dataframe if "gap, %" in getattr(d.value, "columns", []))
    assert beside["gap, %"].abs().median() < 10
    pli = at.session_state["sp_pli"]
    assert set(pli.index) == {"DE", "FR", "PL"}
    assert pli["PL"] == pytest.approx(result.ppp["PL"] / 4.5 * 100)
    with db.session_scope() as s:
        assert s.query(audit.AuditEventORM).filter_by(
            action=audit.EXTERNAL_FETCH_SUCCESS).count() == 1


def test_the_deflation_page_gives_a_price_level_index_beside_a_ppp_conversion(
        deployment, monkeypatch):
    years = ["2021-01-01", "2022-01-01", "2023-01-01"]
    values = "period,value\n" + "".join(f"{y},{v}\n" for y, v in zip(years, (100, 110, 120),
                                                                        strict=True))
    ppp = "period,value\n" + "".join(f"{y},{v}\n" for y, v in zip(years, (2.0, 2.1, 2.2),
                                                                     strict=True))
    rate = "period,value\n" + "".join(f"{y},{v}\n" for y, v in zip(years, (4.0, 4.2, 4.0),
                                                                      strict=True))
    _uploads(monkeypatch, {"df_ppp_values": ("values.csv", values.encode()),
                           "df_ppp_file": ("ppp.csv", ppp.encode()),
                           "df_ppp_rate": ("rate.csv", rate.encode())})
    at = _page("deflation")
    at.button(key="df_ppp_go").click().run()
    assert not at.exception, at.exception
    pli = at.session_state["df_pli"]
    assert pli.round(4).tolist() == [50.0, 50.0, 55.0]
    assert any(c.value.startswith("Price level index: the PPP over the exchange rate")
               for c in at.caption)


# ---------------------------------------------------------------------
# Ingest: the organisation's own classification tree
# ---------------------------------------------------------------------
def test_ingest_loads_a_user_defined_tree_that_the_pipeline_rolls_up_through(
        deployment, monkeypatch):
    from pricelab.data import classification

    tree = (b"code,label,level,parent_code\nFOOD,Food,1,\nBAKERY,Bakery,2,FOOD\n"
            b"DAIRY,Dairy,2,FOOD\n")
    _uploads(monkeypatch, {"ing_tree_file": ("tree.csv", tree)})
    at = _page("ingest")
    at.button(key="ing_tree_go").click().run()
    assert any("Name the scheme" in e.value for e in at.error)
    at.text_input(key="ing_tree_scheme").input("Company products").run()
    at.button(key="ing_tree_go").click().run()
    assert not at.exception, at.exception
    assert any("Loaded 3 of 3 node(s)" in s.value for s in at.success)
    with db.session_scope() as s:
        nodes = s.query(classification.ClassificationNodeORM).filter_by(
            scheme="Company products").all()
        assert {n.code for n in nodes} == {"FOOD", "BAKERY", "DAIRY"}
        assert s.query(audit.AuditEventORM).filter_by(
            action=audit.CONFIGURATION_CHANGE).count() == 1
    assert classification.parent_map_for(["BAKERY", "DAIRY"]) == {
        "BAKERY": "FOOD", "DAIRY": "FOOD", "FOOD": None}

    orphan = b"code,label,level,parent_code\nFISH,Fish,2,SEAFOOD\n"
    _uploads(monkeypatch, {"ing_tree_file": ("orphan.csv", orphan)})
    at.button(key="ing_tree_go").click().run()
    assert any("parent code(s) not in the tree: SEAFOOD" in e.value for e in at.error)
