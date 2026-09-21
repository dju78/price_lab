"""Tests for the validation engine: every dimension, severity
classification, the override workflow, and persistence of override
decisions.
"""

from pathlib import Path

import pandas as pd
import pytest

from pricelab.core import db
from pricelab.core.config import get_settings
from pricelab.data.validation import (
    Severity,
    ValidationOverrideORM,
    assess,
    record_override,
)


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", f"sqlite:///{tmp_path / 'validation_test.db'}")
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield
    db.reset_db_state()
    get_settings.cache_clear()


def _clean_panel() -> pd.DataFrame:
    periods = pd.to_datetime(["2020-01-01", "2020-02-01", "2020-03-01"])
    rows = []
    for period in periods:
        for category, item_id, item_name, price in [
            ("Bread", "1", "White", 1.0), ("Bread", "2", "Wholemeal", 1.1),
            ("Milk", "3", "Semi", 0.9), ("Milk", "4", "Whole", 0.95),
        ]:
            rows.append((period, category, item_id, item_name, price))
    return pd.DataFrame(rows, columns=["period", "category", "item_id", "item_name",
                                        "price_reported"])


# ---------------------------------------------------------------------
# Clean data
# ---------------------------------------------------------------------
def test_clean_panel_has_no_critical_findings_and_does_not_block():
    assessment = assess(_clean_panel(), reference_date=pd.Timestamp("2020-03-01"))
    assert assessment.by_severity(Severity.CRITICAL) == []
    assert not assessment.blocking


# ---------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------
def test_a_minority_of_nulls_in_a_required_column_is_high_not_critical():
    df = _clean_panel()
    df.loc[df.index[:2], "category"] = None  # 2 of 12 rows -> ~17%
    assessment = assess(df, reference_date=pd.Timestamp("2020-03-01"))
    completeness = assessment.by_severity(Severity.HIGH)
    assert any(f.dimension == "completeness" for f in completeness)
    assert not assessment.blocking


def test_a_majority_of_nulls_in_a_required_column_is_critical_and_blocks():
    df = _clean_panel()
    df.loc[df.index[:8], "category"] = None  # 8 of 12 rows -> majority
    assessment = assess(df, reference_date=pd.Timestamp("2020-03-01"))
    critical = assessment.by_severity(Severity.CRITICAL)
    assert any(f.dimension == "completeness" for f in critical)
    assert assessment.blocking


# ---------------------------------------------------------------------
# Validity (routed from the structural report, not a dead second check)
# ---------------------------------------------------------------------
def test_negative_prices_are_critical_and_categorised_as_validity():
    df = _clean_panel()
    df.loc[df.index[0], "price_reported"] = -1.0
    assessment = assess(df, reference_date=pd.Timestamp("2020-03-01"))
    critical = assessment.by_severity(Severity.CRITICAL)
    assert any(f.dimension == "validity" for f in critical)
    assert assessment.blocking


def test_a_negative_price_does_not_suppress_other_dimensions():
    """A critical structural problem must not blind the analyst to
    everything else wrong with the file in the same pass."""
    df = _clean_panel()
    df.loc[df.index[0], "price_reported"] = -1.0
    df.loc[df.index[1], "category"] = None
    assessment = assess(df, reference_date=pd.Timestamp("2020-03-01"))
    dimensions = {f.dimension for f in assessment.findings}
    assert "validity" in dimensions
    assert "completeness" in dimensions


# ---------------------------------------------------------------------
# Uniqueness (duplicate keys)
# ---------------------------------------------------------------------
def test_duplicate_period_item_pairs_are_critical_uniqueness_findings():
    df = _clean_panel()
    dup_row = df.iloc[[0]].copy()
    df = pd.concat([df, dup_row], ignore_index=True)
    assessment = assess(df, reference_date=pd.Timestamp("2020-03-01"))
    critical = assessment.by_severity(Severity.CRITICAL)
    assert any(f.dimension == "uniqueness" for f in critical)
    assert assessment.blocking


# ---------------------------------------------------------------------
# Timeliness
# ---------------------------------------------------------------------
def test_stale_data_is_flagged_medium_timeliness():
    assessment = assess(_clean_panel(), reference_date=pd.Timestamp("2021-01-01"))
    medium = assessment.by_severity(Severity.MEDIUM)
    assert any(f.dimension == "timeliness" for f in medium)
    assert not assessment.blocking  # medium does not block


def test_current_data_has_no_timeliness_finding():
    assessment = assess(_clean_panel(), reference_date=pd.Timestamp("2020-03-01"))
    assert not any(f.dimension == "timeliness" for f in assessment.findings)


# ---------------------------------------------------------------------
# Conformity
# ---------------------------------------------------------------------
def test_a_category_not_in_the_classification_is_a_high_conformity_finding():
    assessment = assess(
        _clean_panel(), classification_codes=["Bread"], reference_date=pd.Timestamp("2020-03-01"))
    high = assessment.by_severity(Severity.HIGH)
    assert any(f.dimension == "conformity" for f in high)


def test_every_category_recognised_has_no_conformity_finding():
    assessment = assess(
        _clean_panel(), classification_codes=["Bread", "Milk"],
        reference_date=pd.Timestamp("2020-03-01"))
    assert not any(f.dimension == "conformity" for f in assessment.findings)


# ---------------------------------------------------------------------
# Plausibility (reuses engine.quality's own missingness diagnosis)
# ---------------------------------------------------------------------
def test_a_price_far_from_its_items_own_median_is_a_high_plausibility_finding():
    df = _clean_panel()
    df.loc[df.index[0], "price_reported"] = 10_000.0  # item "1" otherwise ~1.0
    assessment = assess(df, reference_date=pd.Timestamp("2020-03-01"))
    high = assessment.by_severity(Severity.HIGH)
    assert any(f.dimension == "plausibility" for f in high)


def test_the_real_fixture_matches_its_known_documented_plausibility_findings():
    """Smoke test against the real, multi-year fixture: it is known to
    contain a Pasta collection failure and a Strawberries seasonal gap by
    design, and nothing else critical."""
    from pricelab import infer_schema, standardise

    fixture = Path(__file__).resolve().parents[1] / "supermarket_price_collection.xlsx"
    if not fixture.exists():
        pytest.skip("fixture workbook not present")
    raw = pd.read_excel(fixture, sheet_name="Price_Data")
    df = standardise(raw, infer_schema(raw))

    assessment = assess(df, reference_date=pd.Timestamp("2026-01-01"))
    assert not assessment.blocking
    plausibility_messages = " ".join(
        f.message for f in assessment.findings if f.dimension == "plausibility")
    assert "Pasta" in plausibility_messages
    assert "Strawberries" in plausibility_messages


# ---------------------------------------------------------------------
# Override workflow
# ---------------------------------------------------------------------
def test_overriding_a_dimension_clears_only_its_own_critical_findings():
    df = _clean_panel()
    df.loc[df.index[0], "price_reported"] = -1.0  # validity: critical
    dup_row = df.iloc[[1]].copy()
    df = pd.concat([df, dup_row], ignore_index=True)  # uniqueness: critical
    assessment = assess(df, reference_date=pd.Timestamp("2020-03-01"))
    assert assessment.blocking

    assessment.override("validity")
    assert assessment.blocking  # uniqueness still unresolved

    assessment.override("uniqueness")
    assert not assessment.blocking


def test_record_override_persists_who_when_and_why(fresh_db):
    with db.session_scope() as s:
        record_override(
            s, actor="analyst@example.com", content_hash="abc123",
            dimension="validity", decision="justify", reason="Sale price, verified with retailer")

    with db.session_scope() as s:
        rows = s.query(ValidationOverrideORM).filter_by(content_hash="abc123").all()
        assert len(rows) == 1
        assert rows[0].actor == "analyst@example.com"
        assert rows[0].decision == "justify"
        assert rows[0].reason == "Sale price, verified with retailer"


def test_record_override_refuses_a_blank_reason(fresh_db):
    with db.session_scope() as s:
        with pytest.raises(ValueError, match="reason"):
            record_override(
                s, actor="analyst@example.com", content_hash="abc123",
                dimension="validity", decision="accept", reason="   ")
