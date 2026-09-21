"""Tests for the classification tree loader, the COICOP 2018 seed, and
weight-hierarchy validation.

The generic loader is tested against an obviously-synthetic tree (codes
like "TEST.1") precisely so it is never mistaken for a real classification:
this codebase has no authoritative CPA/NACE/HS source to load for real
(see data/classification.py's module docstring), so the machinery is
proven with fake data rather than a fabricated "real" one.
"""

from pathlib import Path

import pytest
from dbtarget import database_url

from pricelab.core import db
from pricelab.core.config import get_settings
from pricelab.data.classification import (
    ClassificationNodeORM,
    load_classification_from_csv,
    load_user_defined_tree,
    parent_map,
    seed_coicop_2018,
    validate_weight_hierarchy,
)


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "classification_test.db"))
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield
    db.reset_db_state()
    get_settings.cache_clear()


# ---------------------------------------------------------------------
# COICOP 2018
# ---------------------------------------------------------------------
def test_seed_coicop_2018_loads_the_full_tree(fresh_db):
    with db.session_scope() as s:
        inserted = seed_coicop_2018(s)
        assert inserted == 871

    with db.session_scope() as s:
        total = s.query(ClassificationNodeORM).filter_by(scheme="COICOP2018").count()
        assert total == 871
        divisions = {
            n.code for n in s.query(ClassificationNodeORM).filter_by(scheme="COICOP2018", level=0)
        }
        assert divisions == {f"{i:02d}" for i in range(1, 16)}


def test_seed_coicop_2018_is_idempotent(fresh_db):
    with db.session_scope() as s:
        first = seed_coicop_2018(s)
    with db.session_scope() as s:
        second = seed_coicop_2018(s)
        assert second == 0
        assert s.query(ClassificationNodeORM).filter_by(scheme="COICOP2018").count() == first


def test_every_coicop_node_except_divisions_has_a_present_parent(fresh_db):
    with db.session_scope() as s:
        seed_coicop_2018(s)
    with db.session_scope() as s:
        nodes = s.query(ClassificationNodeORM).filter_by(scheme="COICOP2018").all()
        codes = {n.code for n in nodes}
        orphans = [n.code for n in nodes if n.parent_code and n.parent_code not in codes]
        assert orphans == []


# ---------------------------------------------------------------------
# Generic loader, proven with an obviously-synthetic tree
# ---------------------------------------------------------------------
@pytest.fixture()
def synthetic_tree_csv(tmp_path) -> Path:
    path = tmp_path / "synthetic_tree.csv"
    path.write_text(
        "code,label,level,parent_code\n"
        "TEST.1,Root one,0,\n"
        "TEST.1.1,Child of root one,1,TEST.1\n"
        "TEST.1.2,Another child of root one,1,TEST.1\n"
        "TEST.2,Root two,0,\n",
        encoding="utf-8",
    )
    return path


def test_generic_loader_loads_a_synthetic_tree_under_its_own_scheme(fresh_db, synthetic_tree_csv):
    with db.session_scope() as s:
        inserted = load_classification_from_csv(s, synthetic_tree_csv, scheme="TEST_SCHEME")
        assert inserted == 4

    with db.session_scope() as s:
        nodes = s.query(ClassificationNodeORM).filter_by(scheme="TEST_SCHEME").all()
        assert {n.code for n in nodes} == {"TEST.1", "TEST.1.1", "TEST.1.2", "TEST.2"}


def test_generic_loader_is_idempotent_and_scheme_isolated(fresh_db, synthetic_tree_csv):
    with db.session_scope() as s:
        load_classification_from_csv(s, synthetic_tree_csv, scheme="TEST_SCHEME")
        seed_coicop_2018(s)  # a second, unrelated scheme in the same table

    with db.session_scope() as s:
        second_pass = load_classification_from_csv(s, synthetic_tree_csv, scheme="TEST_SCHEME")
        assert second_pass == 0
        assert s.query(ClassificationNodeORM).filter_by(scheme="TEST_SCHEME").count() == 4
        assert s.query(ClassificationNodeORM).filter_by(scheme="COICOP2018").count() == 871


def test_load_user_defined_tree_from_inline_tuples(fresh_db):
    nodes = [
        ("root", "My company's categories", 0, None),
        ("root.a", "Category A", 1, "root"),
        ("root.b", "Category B", 1, "root"),
    ]
    with db.session_scope() as s:
        inserted = load_user_defined_tree(s, "MYCOMPANY_V1", nodes)
        assert inserted == 3
    with db.session_scope() as s:
        again = load_user_defined_tree(s, "MYCOMPANY_V1", nodes)
        assert again == 0
        assert s.query(ClassificationNodeORM).filter_by(scheme="MYCOMPANY_V1").count() == 3


# ---------------------------------------------------------------------
# Weight-hierarchy validation
# ---------------------------------------------------------------------
def test_validate_weight_hierarchy_reports_nothing_when_consistent():
    parent_of = {"01": None, "01.1": "01", "01.2": "01", "01.3": "01"}
    weights = {"01": 100.0, "01.1": 60.0, "01.2": 30.0, "01.3": 10.0}
    assert validate_weight_hierarchy(weights, parent_of) == []


def test_validate_weight_hierarchy_reports_an_inconsistent_node():
    parent_of = {"01": None, "01.1": "01", "01.2": "01", "01.3": "01"}
    weights = {"01": 100.0, "01.1": 60.0, "01.2": 30.0, "01.3": 5.0}  # sums to 95, not 100
    problems = validate_weight_hierarchy(weights, parent_of)
    assert len(problems) == 1
    assert "'01'" in problems[0]
    assert "95" in problems[0]


def test_validate_weight_hierarchy_within_tolerance_is_not_reported():
    parent_of = {"01": None, "01.1": "01", "01.2": "01"}
    weights = {"01": 100.0, "01.1": 60.0, "01.2": 39.9999995}
    assert validate_weight_hierarchy(weights, parent_of, tolerance=1e-3) == []


def test_validate_weight_hierarchy_skips_a_node_with_incomplete_child_weights():
    """A node whose children are only partially weighted is a missing-data
    problem, not an inconsistency this function reports."""
    parent_of = {"01": None, "01.1": "01", "01.2": "01"}
    weights = {"01": 100.0, "01.1": 60.0}  # 01.2's weight is missing entirely
    assert validate_weight_hierarchy(weights, parent_of) == []


def test_validate_weight_hierarchy_skips_a_node_with_no_weight_of_its_own():
    """Nothing to compare the children's sum against."""
    parent_of = {"01": None, "01.1": "01", "01.2": "01"}
    weights = {"01.1": 60.0, "01.2": 30.0}  # 01's own weight was never supplied
    assert validate_weight_hierarchy(weights, parent_of) == []


def test_parent_map_builds_a_plain_dict_from_orm_rows(fresh_db):
    with db.session_scope() as s:
        seed_coicop_2018(s)
    with db.session_scope() as s:
        nodes = s.query(ClassificationNodeORM).filter_by(scheme="COICOP2018").all()
        pm = parent_map(nodes)
        assert pm["01.1"] == "01"
        assert pm["01"] is None


def test_full_coicop_weight_hierarchy_validates_against_a_synthetic_but_consistent_weight_set(
    fresh_db,
):
    """An end-to-end proof on the real tree shape: build a weight for every
    node bottom-up (leaf weights are arbitrary, every ancestor's weight is
    exactly its children's sum by construction) and confirm the validator
    finds nothing wrong with it."""
    with db.session_scope() as s:
        seed_coicop_2018(s)
    with db.session_scope() as s:
        nodes = s.query(ClassificationNodeORM).filter_by(scheme="COICOP2018").all()
    pm = parent_map(nodes)
    children_of: dict[str, list[str]] = {}
    for code, parent in pm.items():
        if parent:
            children_of.setdefault(parent, []).append(code)

    weights: dict[str, float] = {}

    def weight_of(code: str) -> float:
        if code not in children_of:
            weights[code] = 1.0
            return 1.0
        total = sum(weight_of(c) for c in children_of[code])
        weights[code] = total
        return total

    for code in pm:
        if code not in weights:
            weight_of(code)

    assert validate_weight_hierarchy(weights, pm) == []
