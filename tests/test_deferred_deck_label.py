"""A forcing function, not a backlog note.

reporting/charts.py's index_chart and reporting/deck.py's headline stat both
hardcode their "X = 100" label as `I.index[0]`, the series' first period,
rather than reading the run's actual `index_reference_period`. pages/
findings.py had the same bug and was fixed in the reference-period patch;
these two were deliberately left, because no reachable code path can set a
non-default index_reference_period today and so nothing currently
publishes a wrong label.

That "nothing reaches it yet" is exactly the condition Phase 3 changes:
Lowe and Young need a real, independently-set index_reference_period, and
the deck is the client-facing artefact -- the thing that actually leaves
this application and gets read by someone who cannot see the source data
behind it. This test is `strict=True` xfail: it fails today (documenting
the bug), and if reporting/charts.py or reporting/deck.py is ever changed
in a way that happens to make it pass without the label logic actually
being fixed, `strict=True` turns that accidental pass into a test suite
failure rather than a silent, uncelebrated fix. Treat making this test
pass *because the label was actually fixed* as a Phase 3 entry condition,
and delete the xfail marker in the same commit that fixes it.
"""

from io import BytesIO

import pandas as pd
import pytest
from pptx import Presentation

from pricelab import run_pipeline
from pricelab.core.config import IndexConfig, RunConfig
from pricelab.engine.insights import build_narrative
from pricelab.reporting.charts import build_all_charts
from pricelab.reporting.deck import build_deck


def _panel_with_a_late_index_reference_period():
    periods = pd.date_range("2020-01-01", periods=4, freq="MS")  # Jan-Apr 2020
    df = pd.DataFrame({
        "period": list(periods) * 2,
        "category": ["Bread"] * 4 + ["Milk"] * 4,
        "item_id": ["1"] * 4 + ["2"] * 4,
        "item_name": ["White"] * 4 + ["Semi"] * 4,
        "price_reported": [1.0, 1.05, 1.10, 1.15, 2.0, 1.9, 2.2, 2.6],
    })
    # Rebased to March 2020 (periods[2]), not the series' first period.
    index_ref = periods[2]
    cfg = RunConfig(index=IndexConfig(chained=True, index_reference_period=str(index_ref.date())))
    return df, cfg, index_ref, periods[0]


@pytest.mark.xfail(
    strict=True,
    reason="reporting/charts.py and reporting/deck.py hardcode their '= 100' label to "
           "I.index[0] rather than reading index_reference_period; fixing this is a "
           "Phase 3 entry condition (docs/backlog.md), and this test must keep failing "
           "until that fix actually lands -- see this file's module docstring.",
)
def test_chart_ylabel_and_deck_text_report_the_actual_index_reference_period():
    df, cfg, index_ref, first_period = _panel_with_a_late_index_reference_period()
    res = run_pipeline(df, cfg)
    assert "indices" in res, "fixture must produce a valid index for this test to mean anything"

    nar = build_narrative(res)
    charts = build_all_charts(res)

    correct_label_fragment = f"{index_ref:%b %Y} = 100"
    wrong_label_fragment = f"{first_period:%b %Y} = 100"
    assert correct_label_fragment != wrong_label_fragment, "test data must make the two differ"

    # reporting/charts.py's index_chart
    chart_ylabel = charts["index"].axes[0].get_ylabel()
    assert correct_label_fragment in chart_ylabel, (
        f"chart y-axis label {chart_ylabel!r} does not name the configured "
        f"index_reference_period ({index_ref:%b %Y})")

    # reporting/deck.py's headline stat callout
    deck_bytes = build_deck(res, nar, charts, label="deferred label test")
    prs = Presentation(BytesIO(deck_bytes))
    all_text = "\n".join(
        shape.text_frame.text
        for slide in prs.slides
        for shape in slide.shapes
        if shape.has_text_frame
    )
    assert correct_label_fragment in all_text, (
        "no slide names the configured index_reference_period "
        f"({index_ref:%b %Y}) in a '= 100' label")
