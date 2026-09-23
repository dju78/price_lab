"""Phase 9a, Task 0: the property methods on real transactions.

HM Land Registry price paid data for 2024, downloaded on 2026-09-23 from
https://price-paid-data.publicdata.landregistry.gov.uk/pp-2024.csv (162 MB,
930,559 records). tests/fixtures/price_paid_2024_oldham.csv holds every
record for the district of Oldham, copied verbatim -- headerless and fully
quoted, exactly as published -- 3,108 records.

Contains HM Land Registry data (c) Crown copyright and database right 2024.
This data is licensed under the Open Government Licence v3.0.

What the real data did that constructed data had not is recorded in
`data/price_paid.py` and docs/methodology/asset.md; these tests hold each of
the fixes to it.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm
import streamlit
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab.core import db
from pricelab.core.config import get_settings
from pricelab.data import loaders
from pricelab.data.price_paid import PPD_COLUMNS, parse_price_paid
from pricelab.engine import asset as ast
from pricelab.engine.hedonic import _vif

FIXTURE = Path(__file__).parent / "fixtures" / "price_paid_2024_oldham.csv"
RULES = ast.PairRules(min_months=6, exclude_new_build_first=True, max_ratio=2.0)


@pytest.fixture(scope="module")
def loaded() -> loaders.LoadResult:
    return loaders.read_upload(FIXTURE.read_bytes(), FIXTURE.name)


@pytest.fixture(scope="module")
def parsed(loaded):
    return parse_price_paid(loaded.df)


# ---------------------------------------------------------------------
# The loader: two things the real file broke
# ---------------------------------------------------------------------
def test_a_headerless_file_keeps_its_first_record(loaded):
    """The file has no header row. Before, the header inference took the
    first transaction as the column names and it vanished from the data."""
    assert not loaded.has_header and loaded.header_row == -1
    assert len(loaded.df) == 3108 == len(FIXTURE.read_bytes().splitlines())
    assert loaded.df.shape[1] == len(PPD_COLUMNS)


def test_a_sample_ending_inside_a_quoted_field_still_parses():
    """The memory estimate read a fixed-size byte sample, which in a fully
    quoted file can end mid-field: "EOF inside string", and the whole
    upload failed. The sample is now cut back to its last complete line."""
    line = b'"{ID}","100","2024-01-01 00:00","AB1 2CD","T","N","F","1","","A STREET","","TOWN","D","C","A","A"\n'
    body = line * (loaders.SAMPLE_BYTES // len(line) + 50)
    cut = body[:loaders.SAMPLE_BYTES]
    assert cut.count(b'"') % 2 == 1                  # the raw sample ends inside a quote
    result = loaders.read_upload(body, "quoted.csv")
    assert len(result.df) == body.count(b"\n")


def test_a_wide_table_titled_by_years_is_not_mistaken_for_headerless():
    wide = b"item,2023,2024\nbread,1.10,1.20\nmilk,0.90,0.95\n"
    assert loaders.read_upload(wide, "wide.csv").has_header
    dated = b"item,2024-01-01,2024-02-01\nbread,1.10,1.20\nmilk,0.90,0.95\n"
    assert loaders.read_upload(dated, "dated.csv").has_header


# ---------------------------------------------------------------------
# What the real data held
# ---------------------------------------------------------------------
def test_every_rule_is_counted_on_the_real_extract(parsed):
    counts = dict(zip(parsed.findings["rule"], parsed.findings["records"], strict=True))
    assert parsed.records == 3108
    assert counts["category B: repossessions, buy-to-let, transfers to companies"] == 783
    assert counts["same property, date and price recorded twice"] == 21
    assert counts["no postcode"] == 1
    assert counts["leasehold"] == 1453              # a majority of the market sales, here
    assert counts["new build"] == 104
    assert parsed.used == 3108 - 783 - 21
    assert set(parsed.transactions["stratum"]) <= {"Detached", "Semi-detached", "Terraced",
                                                   "Flat"}
    assert parsed.transactions["property_id"].isna().sum() == 1


def test_short_interval_re_sales_are_counted_out_of_repeat_sales(parsed):
    tx = parsed.transactions
    everything = ast.repeat_sales_pairs(tx)
    kept = ast.repeat_sales_pairs(tx, RULES)
    assert len(everything) == 3 and len(kept) == 2
    assert kept.attrs["excluded"]["less than 6 months apart"] == 1


def test_a_method_the_data_cannot_support_is_reported_and_the_rest_still_run(parsed):
    """One district-year holds two usable repeat sales pairs; before, that
    made the whole comparison fail. Now repeat sales and SPAR are named as
    not computed, with the reason, and the other methods answer."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        comparison = ast.compare_methods(
            parsed.transactions, size_col=None, cell_cols=("leasehold", "new_build"),
            characteristics=(), categorical=("stratum", "leasehold", "new_build"), rules=RULES)
    assert set(comparison.results) == {"stratified_median", "mix_adjusted_mean", "hedonic"}
    assert "not identified" in comparison.unavailable["repeat_sales_bmn"]
    assert "no appraisal" in comparison.unavailable["spar"]
    text = "\n".join(comparison.explanation)
    assert "Repeat sales (Bailey-Muth-Nourse): not computed" in text
    last = comparison.table.index.max()
    assert last == pd.Timestamp("2024-12-01")
    assert 95 < comparison.table.loc[last, "hedonic"] < 115


# ---------------------------------------------------------------------
# The hedonic diagnostics had to scale
# ---------------------------------------------------------------------
def test_the_fast_vif_is_the_auxiliary_regression_vif_exactly():
    """A year of transactions with a dummy per county made the old VIF --
    one auxiliary regression per regressor -- run for over ten minutes
    without finishing. The inverse
    correlation matrix gives the same numbers in one step."""
    rng = np.random.default_rng(0)
    n = 400
    a = rng.normal(size=n)
    X = pd.DataFrame({"const": 1.0, "a": a, "b": 0.7 * a + rng.normal(size=n) * 0.5,
                      "c": rng.normal(size=n), "d": (rng.random(n) < 0.3).astype(float)})
    arr = X.to_numpy()
    slow = {col: 1.0 / (1.0 - sm.OLS(arr[:, j], np.delete(arr, j, axis=1)).fit().rsquared)
            for j, col in enumerate(X.columns) if col != "const"}
    assert _vif(X).to_dict() == pytest.approx(slow, rel=1e-9)
    X["e"] = X["a"] + X["c"]                      # exact collinearity
    assert np.isinf(_vif(X)[["a", "c", "e"]]).all()


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
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "ppd.db"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import audit, ledger, registry, security  # noqa: F401  register tables
    db.init_db()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def test_the_property_page_reads_price_paid_data_as_published(deployment, monkeypatch):
    upload = FakeUpload("pp-2024-oldham.csv", FIXTURE.read_bytes())
    monkeypatch.setattr(streamlit, "file_uploader", lambda label, *a, **k: upload
                        if label.startswith("Or: HM Land Registry") else None)
    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.property as page
set_current_role(Role.ANALYST)
st.session_state.setdefault("pricelab_username", "analyst1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    findings = at.session_state["pr_findings"]
    assert findings["records"].iloc[2] == 783
    assert any("Open Government Licence" in c.value for c in at.caption)
    next(b for b in at.button if b.label == "Compile all methods").click().run()
    assert not at.exception, at.exception
    comparison = at.session_state["pr_comparison"]
    assert "repeat_sales_bmn" in comparison.unavailable
    assert any("sampling uncertainty has not been quantified" in c.value for c in at.caption)
