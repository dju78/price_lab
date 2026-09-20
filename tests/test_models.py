"""Tests for the Phase 1 domain schemas and the DataFrame column contract."""

import pandas as pd

from pricelab.core.config import Schema
from pricelab.core.models import PRICE_QUOTE_COLUMNS, PriceQuote, validate_frame
from pricelab.data.upload import standardise


def _standard_frame():
    raw = pd.DataFrame({
        "Date": ["2020-01-01", "2020-02-01"],
        "Category": ["Bread", "Bread"],
        "Item_ID": ["1", "1"],
        "Item_Name": ["White loaf", "White loaf"],
        "Reported_Price": [1.1, 1.2],
    })
    return standardise(raw, Schema())


def test_a_real_standardised_frame_conforms():
    assert validate_frame(_standard_frame(), PRICE_QUOTE_COLUMNS, PriceQuote) == []


def test_missing_required_column_is_reported():
    broken = _standard_frame().drop(columns=["category"])
    problems = validate_frame(broken, PRICE_QUOTE_COLUMNS, PriceQuote)
    assert any("category" in p for p in problems)


def test_wrong_dtype_is_reported_without_row_sampling():
    broken = _standard_frame().copy()
    broken["period"] = broken["period"].astype(str)  # no longer datetime64
    problems = validate_frame(broken, PRICE_QUOTE_COLUMNS, PriceQuote)
    assert any("period" in p and "dtype" in p for p in problems)


def test_missing_optional_column_is_fine():
    broken = _standard_frame().drop(columns=["item_name"])
    assert validate_frame(broken, PRICE_QUOTE_COLUMNS, PriceQuote) == []
