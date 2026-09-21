"""Tests for the column mapping workspace: confidence-scored suggestions
built on top of `engine.auto.infer_schema`, and storage of the mapping an
analyst confirmed, keyed by the file's content hash so the same file maps
identically the next time it is uploaded.
"""

from pathlib import Path

import pandas as pd
import pytest
from dbtarget import database_url

from pricelab.core import db
from pricelab.core.config import Schema, get_settings
from pricelab.data.mapping import (
    file_hash_of,
    get_confirmed_mapping,
    store_confirmed_mapping,
    suggest_column_mapping,
)


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "mapping_test.db"))
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield
    db.reset_db_state()
    get_settings.cache_clear()


def _well_named_df() -> pd.DataFrame:
    return pd.DataFrame({
        "Date": pd.to_datetime(["2020-01-01", "2020-02-01"]),
        "Item_ID": ["1", "2"],
        "Item_Name": ["White bread", "Semi-skimmed milk"],
        "Category": ["Bread", "Milk"],
        "Reported_Price": [1.0, 0.9],
    })


def _renamed_but_keyword_matched_df() -> pd.DataFrame:
    return pd.DataFrame({
        "collection_month": pd.to_datetime(["2020-01-01", "2020-02-01"]),
        "product_id": ["1", "2"],
        "product_description": ["White bread", "Semi-skimmed milk"],
        "group": ["Bread", "Milk"],
        "cost": [1.0, 0.9],
    })


# ---------------------------------------------------------------------
# Confidence-scored suggestions
# ---------------------------------------------------------------------
def test_exact_default_names_score_full_confidence():
    suggestions = suggest_column_mapping(_well_named_df())
    assert suggestions["date"].column == "Date"
    assert suggestions["date"].confidence == 1.0
    assert suggestions["item_id"].column == "Item_ID"
    assert suggestions["item_id"].confidence == 1.0
    assert suggestions["price"].column == "Reported_Price"
    assert suggestions["price"].confidence == 1.0


def test_keyword_matched_columns_score_high_but_not_full_confidence():
    suggestions = suggest_column_mapping(_renamed_but_keyword_matched_df())
    assert suggestions["date"].column == "collection_month"
    assert suggestions["date"].confidence == 0.8
    assert suggestions["item_id"].column == "product_id"
    assert suggestions["item_id"].confidence == 0.8
    assert suggestions["price"].column == "cost"
    assert suggestions["price"].confidence == 0.8


def test_weight_absent_scores_zero_confidence_and_no_column():
    suggestions = suggest_column_mapping(_well_named_df())
    assert suggestions["weight"].column is None
    assert suggestions["weight"].confidence == 0.0


def test_a_column_resolved_only_by_cardinality_scores_partial_confidence():
    # Neither "category"-like nor "item_id"-like keywords appear anywhere;
    # infer_schema falls back to cardinality ranking, which the suggestion
    # layer must report as a lower-confidence guess, not a keyword match.
    df = pd.DataFrame({
        "Date": pd.to_datetime(["2020-01-01", "2020-02-01", "2020-01-01", "2020-02-01"]),
        "Reported_Price": [1.0, 1.1, 0.9, 0.95],
        "col_a": ["Bread", "Bread", "Milk", "Milk"],
        "col_b": ["1", "1", "2", "2"],
    })
    suggestions = suggest_column_mapping(df)
    assert suggestions["category"].confidence == 0.4
    assert suggestions["item_id"].confidence == 0.4


# ---------------------------------------------------------------------
# Confirmed-mapping persistence
# ---------------------------------------------------------------------
def test_confirmed_mapping_round_trips_by_file_hash(fresh_db):
    file_hash = file_hash_of(b"pretend file contents")
    schema = Schema(date="collection_month", item_id="product_id", price="cost")

    with db.session_scope() as s:
        assert get_confirmed_mapping(s, file_hash) is None
        store_confirmed_mapping(s, file_hash, schema, actor="analyst@example.com")

    with db.session_scope() as s:
        reloaded = get_confirmed_mapping(s, file_hash)
        assert reloaded == schema


def test_reconfirming_the_same_file_hash_overwrites_rather_than_duplicates(fresh_db):
    file_hash = file_hash_of(b"same file, re-uploaded")
    first = Schema(date="collection_month")
    second = Schema(date="Date")

    with db.session_scope() as s:
        store_confirmed_mapping(s, file_hash, first, actor="alice")
        store_confirmed_mapping(s, file_hash, second, actor="bob")

    with db.session_scope() as s:
        from pricelab.data.mapping import ColumnMappingORM
        rows = s.query(ColumnMappingORM).filter_by(file_hash=file_hash).all()
        assert len(rows) == 1
        assert rows[0].confirmed_by == "bob"
        reloaded = get_confirmed_mapping(s, file_hash)
        assert reloaded == second


def test_different_files_get_independent_mappings(fresh_db):
    hash_a = file_hash_of(b"file a")
    hash_b = file_hash_of(b"file b")
    schema_a = Schema(date="collection_month")
    schema_b = Schema(date="Date")

    with db.session_scope() as s:
        store_confirmed_mapping(s, hash_a, schema_a, actor="alice")
        store_confirmed_mapping(s, hash_b, schema_b, actor="bob")

    with db.session_scope() as s:
        assert get_confirmed_mapping(s, hash_a) == schema_a
        assert get_confirmed_mapping(s, hash_b) == schema_b


def test_file_hash_is_stable_and_content_derived():
    assert file_hash_of(b"abc") == file_hash_of(b"abc")
    assert file_hash_of(b"abc") != file_hash_of(b"abd")


def test_the_real_fixture_gets_full_confidence_suggestions_for_every_field():
    fixture = Path(__file__).resolve().parents[1] / "supermarket_price_collection.xlsx"
    if not fixture.exists():
        pytest.skip("fixture workbook not present")
    raw = pd.read_excel(fixture, sheet_name="Price_Data")
    suggestions = suggest_column_mapping(raw)
    for field in ("date", "item_id", "item_name", "category", "price"):
        assert suggestions[field].column is not None
        assert suggestions[field].confidence >= 0.8
