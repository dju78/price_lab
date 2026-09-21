"""The hard gate, as a test rather than a promise.

`tests/fixtures/phase3_baseline_index.parquet` is the committed fixture's
index series, every category and the all-items aggregate, computed under
the settings `auto_configure` chooses for it, and frozen at the start of
Phase 3 -- after being proved identical to what the pre-Phase-3 engine
produced by re-running that engine's own code against it.

Parquet rather than CSV because the comparison below is exact: a CSV
baseline compares a float against its own decimal rendering, which is a
test of the formatting rather than of the arithmetic.

Phase 3 adds nine aggregate formulae, three new elementary ones, a
weighted roll-up, splicing and two new imputation methods. Every one of
those is a chance to change a number nobody asked to be changed. This test
is what makes that chance visible on the commit that takes it, rather than
on whatever later run someone happens to compare by eye.

A deliberate change to the default method would legitimately fail this
test. The response is to say so and regenerate the baseline in the same
commit, not to loosen the comparison.
"""

from pathlib import Path

import pandas as pd
import pytest

BASELINE = Path(__file__).resolve().parent / "fixtures" / "phase3_baseline_index.parquet"
FIXTURE = Path(__file__).resolve().parents[1] / "supermarket_price_collection.xlsx"


def test_the_committed_fixture_reproduces_its_phase3_baseline_series_exactly():
    if not FIXTURE.exists():
        pytest.skip("fixture workbook not present")

    from pricelab import auto_configure, infer_schema, standardise
    from pricelab.engine.imputation import run_imputation
    from pricelab.engine.index import build_all
    from pricelab.engine.quality import run_quality

    raw = pd.read_excel(FIXTURE, sheet_name="Price_Data")
    df = standardise(raw, infer_schema(raw))
    cfg, _decisions = auto_configure(df, "hard gate")

    clean, _quality = run_quality(df, cfg.quality)
    imputed = run_imputation(clean, cfg.imputation)
    I, _matched = build_all(imputed, cfg.index)

    expected = pd.read_parquet(BASELINE)

    pd.testing.assert_frame_equal(I, expected, check_exact=True)


def test_the_fixtures_settings_are_still_the_ones_the_baseline_was_built_under():
    """The baseline only means anything if the run that produced it is the
    run this fixture still gets. If `auto_configure` starts choosing a
    different formula or chaining, the series comparison above would be
    testing a different method against an old answer and calling the
    mismatch a regression."""
    if not FIXTURE.exists():
        pytest.skip("fixture workbook not present")

    from pricelab import auto_configure, infer_schema, standardise

    raw = pd.read_excel(FIXTURE, sheet_name="Price_Data")
    df = standardise(raw, infer_schema(raw))
    cfg, _decisions = auto_configure(df, "hard gate")

    assert cfg.index.formula == "jevons"
    assert cfg.index.chained is True
    assert cfg.index.price_reference_period is None
    assert cfg.index.index_reference_period is None
    assert cfg.index.weight_reference_period is None
