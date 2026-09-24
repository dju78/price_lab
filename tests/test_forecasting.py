"""Phase 9b: forecasts and scenarios, and the discipline that keeps a
projected figure from leaving the system as a bare number."""

from __future__ import annotations

import ast
import io
import re
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from dbtarget import database_url
from matplotlib.figure import Figure
from streamlit.testing.v1 import AppTest

from pricelab import build_narrative, infer_schema, run_pipeline, standardise
from pricelab.core import db
from pricelab.core.config import RunConfig, get_settings
from pricelab.core.provenance import build_stamp
from pricelab.engine import forecasting as fc
from pricelab.engine import scenarios as sc
from pricelab.engine.projection import ProjectionIncomplete, missing_parts
from pricelab.reporting import charts, projections

REPO_ROOT = Path(__file__).resolve().parents[1]
PRICES = REPO_ROOT / "supermarket_price_collection.xlsx"
needs_prices = pytest.mark.skipif(not PRICES.exists(), reason="fixture workbook not present")


def _months(n: int, start: str = "2000-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="MS")


def _ar_series(phi: float = 0.6, sigma: float = 0.01, mean: float = 0.002, n: int = 300,
               seed: int = 7) -> pd.Series:
    """An index whose log is ARIMA(1,1,0): monthly log changes follow an
    AR(1) with coefficient `phi` around `mean`, innovations sd `sigma`."""
    rng = np.random.default_rng(seed)
    d = np.zeros(n)
    for t in range(1, n):
        d[t] = mean * (1 - phi) + phi * d[t - 1] + rng.normal(0, sigma)
    return pd.Series(100 * np.exp(np.cumsum(d)), index=_months(n), name="simulated")


def _random_walk(n: int = 200, seed: int = 104) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n))), index=_months(n),
                     name="random walk")


def _pass_through(n: int = 240, seed: int = 3) -> tuple[pd.Series, pd.Series]:
    """pi_t = 0.1 + 0.3 pi_{t-1} + 0.2 dx_{t-1} + 0.1 dx_{t-2} + e: the
    headline's monthly rate on its own lag and two lags of the driver's
    monthly change, all in per cent."""
    rng = np.random.default_rng(seed)
    dx = rng.normal(0, 2, n)
    driver = pd.Series(50 * np.exp(np.cumsum(dx) / 100), index=_months(n), name="energy")
    pi = np.zeros(n)
    for t in range(2, n):
        pi[t] = 0.1 + 0.3 * pi[t - 1] + 0.2 * dx[t - 1] + 0.1 * dx[t - 2] + rng.normal(0, 0.2)
    level = pd.Series(100 * np.exp(np.cumsum(pi) / 100), index=_months(n), name="headline")
    return level, driver


@pytest.fixture(scope="module")
def fixture_headline() -> pd.Series:
    if not PRICES.exists():
        pytest.skip("fixture workbook not present")
    raw = pd.read_excel(PRICES)
    res = run_pipeline(standardise(raw, infer_schema(raw)), RunConfig())
    series: pd.Series = res["indices"]["All items"]
    return series


@pytest.fixture(scope="module")
def ar_forecast() -> fc.ForecastResult:
    return fc.forecast(_ar_series(), fc.ForecastSpec(method="arima", horizon=6, origins=24))


@pytest.fixture(scope="module")
def pass_through() -> fc.ForecastResult:
    level, driver = _pass_through()
    return fc.forecast(level, fc.ForecastSpec(method="pass_through", horizon=6, origins=24,
                                              lags=2, simulations=400,
                                              driver_name="energy prices",
                                              driver_source="simulated for the test"),
                       driver=driver)


@pytest.fixture(scope="module")
def scenario(fixture_headline) -> sc.ScenarioResult:
    return sc.build_scenario(fixture_headline, [
        sc.Shock("energy prices", 20.0, 3, 0.08, "a stated test source", 6),
        sc.Shock("wages", 5.0, 1, 0.2, "another stated test source")], name="Energy and wages")


@pytest.fixture(scope="module")
def fixture_forecast(fixture_headline) -> fc.ForecastResult:
    return fc.forecast(fixture_headline, fc.ForecastSpec(method="ets", horizon=12, origins=24))


def _stamp() -> object:
    df = _collection(48)
    return build_stamp(run_pipeline(df, RunConfig()), "test")


# ---------------------------------------------------------------------
# A known process is recovered
# ---------------------------------------------------------------------
def test_an_arima_forecast_recovers_the_process_it_was_generated_from(ar_forecast):
    """ARIMA(1,1,0), phi 0.6, innovation sd 0.01, 300 months. Stated
    tolerance: phi within 0.10 (about 2.5 standard errors), the innovation
    standard deviation within 15%, and the order itself selected by AICc."""
    assert ar_forecast.specification.startswith("ARIMA(1,1,0)")
    assert ar_forecast.parameters["ar.L1"] == pytest.approx(0.6, abs=0.10)
    assert np.sqrt(ar_forecast.parameters["sigma2"]) == pytest.approx(0.01, rel=0.15)


def test_a_pass_through_regression_recovers_its_coefficients_and_calls_them_correlational(
        pass_through):
    """Stated tolerance: each coefficient within 0.03 and the long-run sum,
    0.3 / (1 - 0.3) = 0.4286, within 0.05."""
    table = pass_through.regression.coefficients.set_index("term")["estimate"]
    assert table["driver change, lag 1"] == pytest.approx(0.2, abs=0.03)
    assert table["driver change, lag 2"] == pytest.approx(0.1, abs=0.03)
    assert table["headline rate, previous period"] == pytest.approx(0.3, abs=0.10)
    assert pass_through.regression.long_run == pytest.approx(0.4286, abs=0.05)
    reading = pass_through.regression.coefficients["reading"]
    assert (reading == "correlational association, not a causal estimate").all()
    assert fc.CORRELATIONAL in pass_through.label


# ---------------------------------------------------------------------
# The benchmark comparison is live
# ---------------------------------------------------------------------
def test_on_a_random_walk_the_model_does_not_beat_the_benchmark_and_says_so():
    result = fc.forecast(_random_walk(), fc.ForecastSpec(method="arima", horizon=6, origins=24))
    assert result.benchmark.benchmark == "random walk"
    assert not result.beats_benchmark
    assert "does not beat the random walk benchmark" in result.label
    # in the same statement as the number, not in a separate panel
    assert result.label.startswith(result.headline)
    assert result.label.index("does not beat") < result.label.index("Backtest of")
    assert result.verdict == "does not beat the random walk benchmark"


def test_random_walks_rarely_beat_the_benchmark():
    """Ten random walks: a model beats a random walk only by luck, and the
    Diebold-Mariano rule allows that about one time in twenty."""
    beaten = [fc.forecast(_random_walk(120, seed), fc.ForecastSpec(
        method="arima", horizon=3, origins=24, max_order=1)).beats_benchmark
        for seed in range(200, 210)]
    assert sum(beaten) <= 2


@needs_prices
def test_on_the_fixture_a_seasonal_model_beats_the_seasonal_naive(fixture_forecast):
    """The other half of a live comparison: where a model is better, the
    verdict says so. The bundled series is strongly seasonal, so its
    benchmark is the seasonal naive."""
    assert fixture_forecast.benchmark.benchmark == "seasonal naive"
    assert fixture_forecast.beats_benchmark
    assert "beats the seasonal naive benchmark" in fixture_forecast.label
    assert fixture_forecast.benchmark.ratio < 0.2


def test_where_the_backtest_error_exceeds_the_implied_interval_the_label_says_so(
        pass_through):
    """The regression's interval holds the driver at its last value, so it
    omits the driver's own uncertainty; its backtest shows it."""
    assert pass_through.backtest.understates
    assert pass_through.backtest.coverage < 0.9
    assert "understating its own uncertainty" in pass_through.label
    table = pass_through.backtest.by_horizon
    assert (table.loc[pass_through.backtest.understated_horizons, "measured_over_implied"]
            > 1).all()


def test_every_forecast_carries_interval_backtest_benchmark_and_assumptions(ar_forecast):
    assert missing_parts(ar_forecast) == []
    path = ar_forecast.path
    assert {"point", "lower", "upper", "measured_lower", "measured_upper"} <= set(path.columns)
    assert (path["lower"] < path["point"]).all() and (path["point"] < path["upper"]).all()
    assert ar_forecast.backtest.origins == 24 and ar_forecast.backtest.horizon == 6
    names = {a.name for a in ar_forecast.assumptions}
    assert {"Method", "Estimation sample", "Benchmark", "Interval", "Backtest"} <= names
    assert all(a.stated for a in ar_forecast.assumptions)
    assert set(ar_forecast.diagnostics["test"].str.split(",").str[0]) == {
        "Ljung-Box", "Jarque-Bera", "ARCH LM"}


# ---------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------
@needs_prices
def test_a_scenario_applies_its_shocks_and_states_every_assumption(scenario):
    path = scenario.path
    energy = 100 * np.log(1.2) * 0.08
    assert path["shock: energy prices"].iloc[:2].tolist() == [0.0, 0.0]
    assert path["shock: energy prices"].iloc[2] == pytest.approx(energy / 6)
    assert path["shock: energy prices"].iloc[-1] == pytest.approx(energy)
    gap = 100 * np.log(path["point"] / path["baseline"])
    assert gap.iloc[-1] == pytest.approx(energy + 100 * np.log(1.05) * 0.2)
    assert missing_parts(scenario) == []
    names = [a.name for a in scenario.assumptions]
    for driver in ("energy prices", "wages"):
        for part in ("size of the shock", "timing", "coefficient applied"):
            assert f"{driver}: {part}" in names
    assert (path["lower"] <= path["lower_80"]).all() and (path["upper_80"] <= path["upper"]).all()


@needs_prices
def test_a_scenario_is_never_labelled_a_forecast(scenario):
    assert scenario.kind == "scenario"
    assert scenario.label.startswith("Scenario -- not a forecast.")
    assert "forecast" not in scenario.label.replace("not a forecast", "")


def _without(text: str) -> str:
    return text.lower().replace("not a forecast", "")


@needs_prices
def test_a_scenario_with_an_unstated_assumption_is_refused_at_export(fixture_headline):
    unsourced = sc.build_scenario(fixture_headline, [sc.Shock("exchange rate", 10.0, 1, 0.05)])
    assert unsourced.unstated == ["exchange rate: coefficient applied"]
    assert "cannot be exported" in unsourced.label
    stamp = _stamp()
    for export in projections.PROJECTION_EXPORTS:
        with pytest.raises(ProjectionIncomplete, match="unstated: exchange rate: coefficient"):
            getattr(projections, export)(unsourced, stamp)
    undated = sc.build_scenario(fixture_headline,
                                [sc.Shock("wages", 3.0, None, 0.2, "a source")])
    with pytest.raises(ProjectionIncomplete, match="wages: timing"):
        projections.projection_csv(undated, stamp)


# ---------------------------------------------------------------------
# Export discipline: the surface inventory
# ---------------------------------------------------------------------
EXPORT_NAME = re.compile(r"^(build_|to_)|_(csv|workbook|markdown|xlsx|docx|pptx|pdf|png|sdmx_ml)$")


def test_every_export_function_is_in_the_inventory():
    found = set()
    for path in (REPO_ROOT / "pricelab" / "reporting").glob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        for node in tree.body:
            if (isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
                    and EXPORT_NAME.search(node.name)):
                found.add(node.name if path.stem == "projections"
                          else f"{path.stem}.{node.name}")
    listed = set(projections.PROJECTION_EXPORTS) | set(projections.EXPORTS_WITHOUT_PROJECTIONS)
    assert not found - listed, (f"{sorted(found - listed)} can write a file and are in "
                                "neither reporting/projections.PROJECTION_EXPORTS nor "
                                "EXPORTS_WITHOUT_PROJECTIONS")
    assert not listed - found, f"listed but not found: {sorted(listed - found)}"


def test_no_export_outside_the_projection_list_can_take_a_projection():
    import importlib
    import inspect

    for name in projections.EXPORTS_WITHOUT_PROJECTIONS:
        module, function = name.split(".")
        target = getattr(importlib.import_module(f"pricelab.reporting.{module}"), function)
        parameters = " ".join(inspect.signature(target).parameters)
        assert not re.search(r"forecast|scenario|projection", parameters), name


PROJECTION_IMPORT = re.compile(
    r"engine(\.|\s+import\s+)(forecasting|scenarios|projection)\b|"
    r"reporting(\.|\s+import\s+)projections\b|ForecastResult|ScenarioResult")


def test_only_the_listed_modules_handle_projections():
    handling = set()
    for folder in ("pricelab", "pages"):
        for path in (REPO_ROOT / folder).rglob("*.py"):
            if PROJECTION_IMPORT.search(path.read_text("utf-8")):
                handling.add(path.relative_to(REPO_ROOT).as_posix())
    assert handling <= set(projections.PROJECTION_MODULES), sorted(
        handling - set(projections.PROJECTION_MODULES))


def test_the_projection_pages_export_only_through_the_projection_exports():
    for page in ("forecasting", "scenarios"):
        source = (REPO_ROOT / "pages" / f"{page}.py").read_text("utf-8")
        assert not re.search(r"safe_csv|stamped_csv|to_csv|to_excel|index_csv", source), page
    exports = (REPO_ROOT / "pages" / "forecasting.py").read_text("utf-8")
    assert exports.count("st.download_button") + exports.count(".download_button(") == 1
    for function in projections.PROJECTION_EXPORTS:
        assert f"projections.{function}(" in exports


def _drop(projection, part: str):
    if part == "interval":
        return replace(projection, path=projection.path.drop(columns=["lower", "upper"]))
    if part == "backtest performance":
        return replace(projection, backtest=None)
    if part == "benchmark comparison":
        return replace(projection, benchmark=None)
    return replace(projection, assumptions=())


def _text(export: str, output: object) -> str:
    if isinstance(output, bytes):
        from openpyxl import load_workbook

        book = load_workbook(io.BytesIO(output))
        return "\n".join(str(cell) for ws in book.worksheets
                         for row in ws.iter_rows(values_only=True) for cell in row
                         if cell is not None)
    return str(output)


@needs_prices
@pytest.mark.parametrize("export", sorted(projections.PROJECTION_EXPORTS))
def test_no_export_path_emits_a_projection_without_all_four_parts(export, ar_forecast,
                                                                   scenario):
    """The single most important test of the phase: every export path,
    given a forecast or a scenario lacking any one of its four parts,
    refuses; given all four, writes every one of them."""
    stamp = _stamp()
    write = getattr(projections, export)
    for projection in (ar_forecast, scenario):
        for part in ("interval", "backtest performance", "benchmark comparison",
                     "assumptions"):
            with pytest.raises(ProjectionIncomplete, match=part):
                write(_drop(projection, part), stamp)
        text = _text(export, write(projection, stamp))
        assert projection.benchmark.statement in text
        assert projection.backtest.statement in text
        for assumption in projection.assumptions:
            assert assumption.name in text and assumption.source in text
        if projection.kind == "forecast":
            assert "forecast: lower, model-implied" in text
            assert "forecast: upper, implied by backtest errors" in text
        else:
            assert "scenario: fan lower, 95%" in text
            assert "forecast" not in _without(text), "a scenario export mentions a forecast"


# ---------------------------------------------------------------------
# A forecast and a scenario never share an axis or a column unlabelled
# ---------------------------------------------------------------------
def _axis(forecast_label: str, scenario_label: str) -> Figure:
    fig = Figure()
    ax = fig.add_subplot(111)
    charts.mark(ax.plot([1, 2], [100, 101], label=forecast_label), "index_level",
                projection="forecast")
    charts.mark(ax.plot([1, 2], [100, 102], label=scenario_label), "index_level",
                projection="scenario")
    return fig


def test_a_forecast_and_a_scenario_share_an_axis_only_when_each_is_labelled():
    charts.check_figure(_axis("forecast: ARIMA", "scenario: energy shock"))
    for forecast_label, scenario_label in (("ARIMA", "scenario: energy shock"),
                                           ("forecast: ARIMA", "energy shock"),
                                           ("forecast: ARIMA", "scenario forecast"),
                                           ("forecast and scenario", "scenario: energy"),
                                           ("_nolegend_", "scenario: energy")):
        with pytest.raises(charts.ChartUnitError, match="a forecast and a scenario share"):
            charts.check_figure(_axis(forecast_label, scenario_label))


@needs_prices
def test_a_forecast_and_a_scenario_share_a_table_only_when_each_column_is_labelled(
        fixture_forecast, scenario):
    table = projections.side_by_side(fixture_forecast, scenario)
    assert all(c.startswith(("forecast: ", "scenario: ")) for c in table.columns
               if c != "period")
    bad = table.rename(columns={"scenario: path": "path"})
    kinds = {c: "forecast" for c in table.columns if c.startswith("forecast")}
    kinds.update({c: "scenario" for c in table.columns if c.startswith("scenario")})
    kinds["path"] = "scenario"
    with pytest.raises(ProjectionIncomplete, match="does not say it is a scenario"):
        projections.check_projection_columns(bad, kinds)


@needs_prices
def test_the_projection_charts_are_labelled_as_what_they_are(fixture_forecast, scenario):
    forecast_fig = charts.forecast_chart(fixture_forecast)
    scenario_fig = charts.scenario_chart(scenario)
    assert fixture_forecast.verdict in forecast_fig.axes[0].get_title(loc="left")
    assert scenario_fig.axes[0].get_title(loc="left").startswith("Scenario (not a forecast)")
    legend = [t.get_text() for t in scenario_fig.axes[0].get_legend().get_texts()]
    assert all("forecast" not in _without(t) for t in legend)


# ---------------------------------------------------------------------
# Correlational language
# ---------------------------------------------------------------------
CAUSAL = re.compile(r"\b(causes?|caused by|causal effect|impact of|effect of|drives|driven by|"
                    r"leads to|results in|due to)\b", re.IGNORECASE)


def test_nothing_describes_a_coefficient_in_causal_language():
    for path in ("pricelab/engine/forecasting.py", "pricelab/engine/scenarios.py",
                 "pricelab/reporting/projections.py", "pages/forecasting.py",
                 "pages/scenarios.py"):
        tree = ast.parse((REPO_ROOT / path).read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for hit in CAUSAL.finditer(node.value):
                    before = node.value[max(0, hit.start() - 12):hit.start()].lower()
                    assert "not" in before, f"{path}: {node.value[:120]!r}"


# ---------------------------------------------------------------------
# Reproducible from the run registry
# ---------------------------------------------------------------------
def _collection(months: int = 60, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for c, drift in (("Bread", 0.004), ("Milk", 0.002)):
        for i in range(3):
            level = 5.0 + i
            for period in pd.date_range("2019-01-01", periods=months, freq="MS"):
                level *= 1 + drift + rng.normal(0, 0.004)
                rows.append({"Date": period, "Category": c, "Item_ID": f"{c}{i}",
                             "Item_Name": f"{c} {i}", "Reported_Price": round(level, 4)})
    raw = pd.DataFrame(rows)
    return standardise(raw, infer_schema(raw))


@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "forecasting.db"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import audit, ledger, registry, security  # noqa: F401  register tables
    db.init_db()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def test_backtests_are_reproducible_from_the_run_registry(deployment):
    from pricelab.core import registry

    df = _collection()
    cfg = RunConfig()
    res = run_pipeline(df, cfg)
    series = res["indices"]["All items"]
    level, driver = _pass_through(60, seed=11)
    driver.index = series.index
    made = [
        fc.forecast(series, fc.ForecastSpec(method="arima", horizon=3, origins=6)),
        fc.forecast(series, fc.ForecastSpec(method="pass_through", horizon=3, origins=6,
                                            lags=1, simulations=200, driver_name="energy",
                                            driver_source="test"), driver=driver),
        sc.build_scenario(series, [sc.Shock("wages", 4.0, 2, 0.2, "a source")], horizon=6,
                          origins=12),
    ]
    with db.session_scope() as s:
        run_id = registry.register_run(s, df, cfg, "registry test", result=res).run_id
        ids = [registry.register_projection(s, run_id, p, "analyst1",
                                            driver=driver if p.method == "pass_through"
                                            else None).projection_id
               if p.kind == "forecast" else
               registry.register_projection(s, run_id, p, "analyst1").projection_id
               for p in made]
        assert len(set(ids)) == 3
        again = registry.register_projection(s, run_id, made[0], "analyst1")
        assert again.projection_id == ids[0]
    with db.session_scope() as s:
        for original, projection_id in zip(made, ids, strict=True):
            rebuilt, matches = registry.reproduce_projection(s, projection_id, "auditor")
            assert matches, projection_id
            pd.testing.assert_frame_equal(rebuilt.backtest.errors, original.backtest.errors)
            pd.testing.assert_frame_equal(rebuilt.path, original.path)
        row = s.query(registry.ProjectionRunORM).filter_by(projection_id=ids[0]).one()
        row.backtest_digest = "0" * 64
    with db.session_scope() as s:
        assert registry.reproduce_projection(s, ids[0])[1] is False
        from pricelab.core.audit import AuditEventORM

        actions = {e.action for e in s.query(AuditEventORM).all()}
        assert {"projection_registered", "projection_reproduced"} <= actions


# ---------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------
def _page(page: str, state: dict) -> AppTest:
    script = f"""
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.{page} as page
set_current_role(Role.ANALYST)
st.session_state.setdefault("pricelab_username", "analyst1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=300)
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, at.exception
    return at


def _state(df: pd.DataFrame, label: str = "sixty months") -> dict:
    res = run_pipeline(df, RunConfig())
    return {"analysis": {"result": res, "narrative": build_narrative(res), "decisions": [],
                         "label": label},
            "input_df": df, "run_config": res["config"]}


def test_the_forecasts_page_puts_the_verdict_beside_the_number_and_registers_it(deployment):
    state = _state(_collection())
    at = _page("forecasting", state)
    at.selectbox(key="fc_method").select("arima").run()
    at.number_input(key="fc_horizon").set_value(3).run()
    at.number_input(key="fc_origins").set_value(6).run()
    at.button(key="fc_go").click().run()
    assert not at.exception, at.exception
    result = at.session_state["fc_result"]["result"]
    verdicts = [e.value for e in (*at.success, *at.warning)]
    assert result.benchmark.statement in verdicts
    assert any(m.value == result.headline for m in at.markdown)
    assert len(at.get("download_button")) == 3
    at.button(key="fc_register").click().run()
    assert not at.exception, at.exception
    assert any("Registered as forecast" in s.value for s in at.success)


def test_the_forecasts_page_shows_coefficients_only_as_correlational(deployment, pass_through):
    state = _state(_collection())
    state["fc_result"] = {"label": "sixty months", "result": pass_through, "driver": None}
    at = _page("forecasting", state)
    captions = [c.value for c in at.caption]
    assert fc.CORRELATIONAL in captions
    assert any(c.startswith("Long-run association") and fc.CORRELATIONAL in c
               for c in captions)
    assert any("understating its own uncertainty" in w.value for w in at.warning)


def test_the_scenarios_page_refuses_to_export_an_unsourced_scenario(deployment):
    state = _state(_collection())
    at = _page("scenarios", state)
    at.number_input(key="sc_horizon").set_value(6).run()
    at.number_input(key="sc_origins").set_value(12).run()
    at.checkbox(key="sc_energy_prices_on").check().run()
    at.number_input(key="sc_energy_prices_coef").set_value(0.08).run()
    assert any("Say where this coefficient comes from" in w.value for w in at.warning)
    at.button(key="sc_go").click().run()
    assert not at.exception, at.exception
    result = at.session_state["sc_result"]["result"]
    assert result.unstated == ["energy prices: coefficient applied"]
    assert any("cannot be exported" in e.value for e in at.error)
    assert not at.get("download_button")
    assert any(i.value.startswith("Scenario -- not a forecast.") for i in at.info)

    at.text_input(key="sc_energy_prices_source").input("a stated source").run()
    at.button(key="sc_go").click().run()
    assert not at.exception, at.exception
    assert at.session_state["sc_result"]["result"].unstated == []
    assert len(at.get("download_button")) == 3


def test_the_scenarios_page_takes_a_coefficient_from_the_run_weights(deployment):
    state = _state(_collection())
    at = _page("scenarios", state)
    at.number_input(key="sc_horizon").set_value(6).run()
    at.number_input(key="sc_origins").set_value(12).run()
    at.checkbox(key="sc_administered_prices_on").check().run()
    at.radio(key="sc_administered_prices_basis").set_value(
        "The weight of chosen categories (direct effect only)").run()
    at.multiselect(key="sc_administered_prices_cats").select("Milk").run()
    at.button(key="sc_go").click().run()
    assert not at.exception, at.exception
    shock = at.session_state["sc_result"]["result"].shocks[0]
    assert shock.coefficient == pytest.approx(0.5)
    assert shock.coefficient_source.startswith("direct effect only: the share of Milk")
