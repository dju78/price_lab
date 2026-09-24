"""The only way a forecast or a scenario leaves the system.

A projected figure without its interval, its backtest performance, its
benchmark comparison and its assumptions is a bare number about the future,
and a reader will take it as a fact. So every export of a projection goes
through `release`, which refuses one missing any of the four
(`engine.projection.missing_parts`), and every export function is named in
`PROJECTION_EXPORTS`. `EXPORTS_WITHOUT_PROJECTIONS` names every other export
function in `reporting/` with the reason it cannot carry a projected figure.
`tests/test_forecasting.py` scans the package and fails on any export
function in neither list, on any module outside `PROJECTION_MODULES` that
touches a projection's path, and on any export in `PROJECTION_EXPORTS` that
emits a projection without all four parts -- the surface inventory pattern.

Forecasts and scenarios are written with every column prefixed by their kind
("forecast: ", "scenario: "), and `side_by_side` is the one place the two
share a table: its columns say which is which, and `check_projection_columns`
refuses a table that does not.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from typing import Any

import pandas as pd

from ..core.provenance import STAMP_KEY, ProvenanceStamp
from ..engine.projection import ProjectionIncomplete, missing_parts
from .exports import stamped_csv

__all__ = [
    "EXPORTS_WITHOUT_PROJECTIONS",
    "PROJECTION_EXPORTS",
    "PROJECTION_MODULES",
    "check_projection_columns",
    "projection_csv",
    "projection_markdown",
    "projection_workbook",
    "release",
    "side_by_side",
]

#: Every function that writes a projected figure out of the system.
PROJECTION_EXPORTS: dict[str, str] = {
    "projection_csv": "the projected path as a stamped CSV, the four parts in its header",
    "projection_workbook": "an Excel workbook: read-me-first, path, assumptions, backtest, "
                           "benchmark, provenance",
    "projection_markdown": "a Markdown note of the projection and its four parts",
}

#: Every other export function in `reporting/`, and why no projected figure
#: can reach it: each takes a compiled run (`res`) or a narrative, and a
#: projection is never stored in either.
EXPORTS_WITHOUT_PROJECTIONS: dict[str, str] = {
    "exports.stamped_csv": "writes the frame it is given; projections reach it only through "
                           "projection_csv, which releases them first",
    "exports.index_csv": "the compiled run's publication table",
    "exports.to_sdmx_ml": "the compiled run's publication table",
    "excel.build_evidence_pack": "the compiled run's evidence",
    "report.build_markdown": "the compiled run's report",
    "report.build_docx": "the compiled run's report",
    "deck.build_deck": "the compiled run's findings",
    "bulletin.build_bulletin": "a registered run's release",
    "charts.to_png": "renders a figure; projection charts carry their label as a footnote",
    "charts.build_all_charts": "the compiled run's charts, returned as figures, not written",
    "exports.validate_sdmx_ml": "validates a document; writes nothing",
    "report.provenance_markdown": "a provenance stamp as Markdown",
    "readback.from_csv": "reads a provenance stamp back; writes nothing",
    "readback.from_markdown": "reads a provenance stamp back; writes nothing",
    "readback.from_docx": "reads a provenance stamp back; writes nothing",
    "readback.from_pptx": "reads a provenance stamp back; writes nothing",
    "readback.from_xlsx": "reads a provenance stamp back; writes nothing",
    "readback.from_pdf": "reads a provenance stamp back; writes nothing",
}

#: The modules allowed to handle a projection: the engines that build it,
#: this module, the charts that draw it with its label, the registry that
#: records and rebuilds it, and the two pages that show it.
PROJECTION_MODULES: tuple[str, ...] = (
    "pricelab/engine/projection.py", "pricelab/engine/forecasting.py",
    "pricelab/engine/scenarios.py", "pricelab/reporting/projections.py",
    "pricelab/reporting/charts.py", "pricelab/core/registry.py", "pages/forecasting.py",
    "pages/scenarios.py",
)


def release(projection: Any) -> Any:
    """Return the projection if it carries all four parts; otherwise raise
    `ProjectionIncomplete` naming what is missing."""
    missing = missing_parts(projection)
    if missing:
        kind = getattr(projection, "kind", "projection")
        raise ProjectionIncomplete(
            f"this {kind} cannot be exported: it lacks its {'; its '.join(missing)}. No "
            "projected figure leaves the system without its interval, its backtest "
            "performance, its benchmark comparison and its stated assumptions.")
    return projection


# ---------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------
_FORECAST_COLUMNS = {
    "point": "forecast: point (median)",
    "lower": "forecast: lower, model-implied",
    "upper": "forecast: upper, model-implied",
    "measured_lower": "forecast: lower, implied by backtest errors",
    "measured_upper": "forecast: upper, implied by backtest errors",
}
_SCENARIO_COLUMNS = {
    "point": "scenario: path",
    "lower": "scenario: fan lower, 95%",
    "upper": "scenario: fan upper, 95%",
    "lower_80": "scenario: fan lower, 80%",
    "upper_80": "scenario: fan upper, 80%",
    "lower_50": "scenario: fan lower, 50%",
    "upper_50": "scenario: fan upper, 50%",
    "baseline": "scenario: baseline rule, no shocks",
}


def path_table(projection: Any) -> pd.DataFrame:
    """The projected path with every column named for its kind."""
    path = projection.path.copy()
    if projection.kind == "forecast":
        names = _FORECAST_COLUMNS
    else:
        names = {**_SCENARIO_COLUMNS, **{c: f"scenario: {c} (contribution, % of level)"
                                         for c in path.columns if c.startswith("shock: ")}}
    table = path.rename(columns=names)
    table.index = pd.DatetimeIndex(table.index).strftime("%Y-%m-%d")
    table.index.name = "period"
    out: pd.DataFrame = table.reset_index()
    return out


def check_projection_columns(table: pd.DataFrame, kinds: Mapping[str, str]) -> pd.DataFrame:
    """Refuse a table holding forecast and scenario columns unless each is
    named for its kind. `kinds` maps column to "forecast" or "scenario"."""
    from .charts import labelled_as

    present = {kinds[c] for c in table.columns if c in kinds}
    if len(present) > 1:
        for column in table.columns:
            kind = kinds.get(column)
            if kind is not None and not labelled_as(str(column), kind):
                raise ProjectionIncomplete(
                    f"a table holds forecasts and scenarios, and the {kind} column {column!r} "
                    f"does not say it is a {kind}. A scenario is not a forecast; name each "
                    "column for what it is")
    return table


def side_by_side(forecast: Any, scenario: Any) -> pd.DataFrame:
    """A forecast and a scenario in one table, each column named for its
    kind, both released first."""
    release(forecast)
    release(scenario)
    left, right = path_table(forecast), path_table(scenario)
    table = left.merge(right, on="period", how="outer")
    kinds = {**{c: "forecast" for c in left.columns if c != "period"},
             **{c: "scenario" for c in right.columns if c != "period"}}
    return check_projection_columns(table, kinds)


def _statements(projection: Any) -> list[str]:
    """The label, which states the projection with its interval, its
    benchmark comparison and its backtest in one statement."""
    return [projection.label]


def _assumption_table(projection: Any) -> pd.DataFrame:
    return pd.DataFrame([a.as_row() for a in projection.assumptions])


def _backtest_table(projection: Any) -> pd.DataFrame:
    table: pd.DataFrame = projection.backtest.by_horizon.reset_index()
    if projection.kind == "scenario":
        table = table.drop(columns=["coverage", "implied_halfwidth_pct",
                                    "measured_halfwidth_pct", "measured_over_implied"])
    return table


def _benchmark_table(projection: Any) -> pd.DataFrame:
    b = projection.benchmark
    return pd.DataFrame([
        {"measure": "benchmark", "value": b.benchmark},
        {"measure": f"{projection.kind} method", "value": b.method},
        {"measure": "method RMSE, %", "value": f"{b.method_rmse_pct:.4f}"},
        {"measure": "benchmark RMSE, %", "value": f"{b.benchmark_rmse_pct:.4f}"},
        {"measure": "RMSE ratio (below 1 is better)", "value": f"{b.ratio:.4f}"},
        {"measure": "Diebold-Mariano statistic", "value": f"{b.dm_statistic:.4f}"},
        {"measure": "Diebold-Mariano p-value (one-sided)", "value": f"{b.p_value:.4f}"},
        {"measure": "beats the benchmark", "value": "yes" if b.beats else "no"},
        {"measure": "origins", "value": str(b.origins)},
        {"measure": "horizons", "value": f"1-{b.horizon}"},
        {"measure": "statement", "value": b.statement},
    ])


def _stamp(stamp: ProvenanceStamp, projection: Any) -> ProvenanceStamp:
    from dataclasses import replace

    return replace(stamp, extra={**stamp.extra, "projection": projection.kind,
                                 "backtest_digest": projection.backtest.digest})


# ---------------------------------------------------------------------
# The exports
# ---------------------------------------------------------------------
def projection_csv(projection: Any, stamp: ProvenanceStamp) -> str:
    """The path as a stamped CSV; its header comment lines carry the label,
    the benchmark comparison, the backtest and every assumption."""
    release(projection)
    lines = [f"{projection.kind.upper()}: " + text for text in _statements(projection)]
    lines += [f"assumption: {a.name} = {a.value} (source: {a.source})"
              for a in projection.assumptions]
    table = _backtest_table(projection)
    lines += ["backtest by horizon: " + "; ".join(
        f"h{h} RMSE {m:.3f}% vs benchmark {b:.3f}%" for h, m, b in
        zip(table["h"], table["rmse_pct"], table["benchmark_rmse_pct"], strict=True))]
    notice = "\n# ".join(lines)
    return stamped_csv(path_table(projection), _stamp(stamp, projection), notice)


def projection_workbook(projection: Any, stamp: ProvenanceStamp) -> bytes:
    """An Excel workbook whose first sheet is the four parts in words."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    from ..core.security import sanitize_cell

    release(projection)
    stamped = _stamp(stamp, projection)
    wb = Workbook()
    first = wb.active
    assert first is not None
    first.title = "Read me first"
    first["A1"] = sanitize_cell(f"{projection.kind.capitalize()}"
                                + (" -- not a forecast" if projection.kind == "scenario" else ""))
    first["A1"].font = Font(bold=True, size=13)
    for row, text in enumerate(_statements(projection), start=3):
        cell = first.cell(row=row, column=1, value=sanitize_cell(text))
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    first.column_dimensions["A"].width = 140

    def sheet(name: str, frame: pd.DataFrame) -> None:
        ws = wb.create_sheet(name)
        ws.append([sanitize_cell(str(c)) for c in frame.columns])
        for values in frame.itertuples(index=False):
            ws.append([sanitize_cell(v) if isinstance(v, str) else v for v in values])

    sheet("Path", path_table(projection))
    sheet("Assumptions", _assumption_table(projection))
    sheet("Backtest by horizon", _backtest_table(projection))
    errors = projection.backtest.errors.copy()
    for column in ("origin", "target"):
        errors[column] = pd.DatetimeIndex(errors[column]).strftime("%Y-%m-%d")
    sheet("Backtest errors", errors)
    sheet("Benchmark", _benchmark_table(projection))
    sheet(STAMP_KEY, pd.DataFrame(stamped.rows(), columns=["field", "value"]))
    ws = wb[STAMP_KEY]
    ws.append([STAMP_KEY, stamped.to_json()])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def projection_markdown(projection: Any, stamp: ProvenanceStamp) -> str:
    """A Markdown note: the label first, then each part under its heading."""
    from .report import provenance_markdown

    release(projection)
    title = ("Forecast" if projection.kind == "forecast"
             else "Scenario (not a forecast)")
    parts = [f"# {title}", "", projection.label, "",
             "## Benchmark comparison", "", projection.benchmark.statement, "",
             "## Backtest performance", "", projection.backtest.statement, "",
             _backtest_table(projection).round(3).to_markdown(index=False), "",
             "## Assumptions", "", _assumption_table(projection).to_markdown(index=False), "",
             "## Path and interval", "", path_table(projection).round(3).to_markdown(index=False),
             "", provenance_markdown(_stamp(stamp, projection))]
    return "\n".join(parts)

