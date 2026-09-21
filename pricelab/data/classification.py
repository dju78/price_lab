"""Classification trees: COICOP, and the generic machinery CPA, NACE, HS
and user-defined trees will use once a real source file exists for them.

COICOP_2018_DIVISIONS/seed_coicop_divisions (the 13 top-level divisions,
hand-typed in Phase 1) are kept for continuity -- nothing in this codebase
called them beyond the ORM table registration, so nothing breaks by their
staying -- but `seed_coicop_2018` below supersedes them: it loads the
*complete* COICOP 2018 tree (divisions through sub-classes, 871 codes) from
`data/reference/coicop_2018.csv`, a file derived from the authoritative
source rather than typed from memory.

Source: United Nations Statistics Division, Classification of Individual
Consumption According to Purpose (COICOP) 2018, English structure file.
Downloaded 2026-09-20 from
https://unstats.un.org/unsd/classifications/Econ/Download/COICOP_2018_English_structure.xlsx
(linked from the UNSD classifications registry at
https://unstats.un.org/unsd/classifications/Econ/Registry). The vendored
CSV keeps exactly the `code` and `title` columns from that file (renamed
`code`/`label`), with `level` and `parent_code` derived mechanically from
the code's dot-separated depth -- not retyped by hand, so there is no
transcription step where a digit could be mistyped.

CPA, NACE and HS are not seeded: no authoritative, machine-readable source
for any of them was found and verified in the time available for this
phase (see docs/backlog.md). `load_classification_from_csv` below places no
COICOP-specific assumption anywhere in its path, so pointing it at a real
CPA/NACE/HS file, whenever one is obtained, is the entire remaining work --
not a rewrite. A user-defined tree needs no authoritative source by
definition; the same loader accepts any `scheme` name a caller chooses.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..core.db import Base

REFERENCE_DIR = Path(__file__).resolve().parent / "reference"
COICOP_2018_CSV = REFERENCE_DIR / "coicop_2018.csv"

#: The thirteen COICOP 2018 *divisions* (level 0) covering household
#: individual consumption -- the subset relevant to a CPI. Kept for
#: continuity with Phase 1's `seed_coicop_divisions`; `seed_coicop_2018`
#: below loads all 871 codes across all 15 divisions (the full standard
#: also covers NPISH and government consumption) and all four sub-levels.
COICOP_2018_DIVISIONS: tuple[tuple[str, str], ...] = (
    ("01", "Food and non-alcoholic beverages"),
    ("02", "Alcoholic beverages, tobacco and narcotics"),
    ("03", "Clothing and footwear"),
    ("04", "Housing, water, electricity, gas and other fuels"),
    ("05", "Furnishings, household equipment and routine household maintenance"),
    ("06", "Health"),
    ("07", "Transport"),
    ("08", "Information and communication"),
    ("09", "Recreation, sport and culture"),
    ("10", "Education services"),
    ("11", "Restaurants and accommodation services"),
    ("12", "Insurance and financial services"),
    ("13", "Personal care, social protection and miscellaneous goods and services"),
)


class ClassificationNodeORM(Base):
    __tablename__ = "classification_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scheme: Mapped[str] = mapped_column(String(32), nullable=False, default="COICOP2018")
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_code: Mapped[str | None] = mapped_column(String(32), nullable=True)


def seed_coicop_divisions(session: Session) -> None:
    """Insert the thirteen COICOP 2018 divisions if they are not already
    present. Safe to call more than once. Superseded by `seed_coicop_2018`,
    which loads the complete tree these divisions are the top of."""
    existing = {
        row.code
        for row in session.query(ClassificationNodeORM).filter_by(scheme="COICOP2018", level=0)
    }
    for code, label in COICOP_2018_DIVISIONS:
        if code in existing:
            continue
        session.add(ClassificationNodeORM(
            scheme="COICOP2018", code=code, label=label, level=0, parent_code=None))
    session.flush()


def load_classification_from_csv(session: Session, path: Path | str, scheme: str) -> int:
    """Load a classification tree from a CSV of columns
    `code,label,level,parent_code` (an optional `scheme` column is ignored
    in favour of the `scheme` argument, so the same file can be loaded
    under a different name for a sandbox/test copy). Idempotent: a
    `(scheme, code)` pair already in the table is left untouched, so
    calling this again after a partial load, or after `seed_coicop_2018`
    already ran, only inserts what is missing.

    Generic on purpose: nothing here is COICOP-specific. Pointing this at
    a real CPA, NACE or HS structure file, or at a user's own tree, is the
    entire integration -- there is no COICOP-only code path to generalise
    first.

    Returns the number of rows actually inserted.
    """
    existing = {
        row.code for row in session.query(ClassificationNodeORM).filter_by(scheme=scheme)
    }
    inserted = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row["code"].strip()
            if code in existing:
                continue
            parent_code = (row.get("parent_code") or "").strip() or None
            session.add(ClassificationNodeORM(
                scheme=scheme, code=code, label=row["label"],
                level=int(row["level"]), parent_code=parent_code))
            existing.add(code)
            inserted += 1
    session.flush()
    return inserted


def seed_coicop_2018(session: Session) -> int:
    """Load the complete COICOP 2018 tree (871 codes, divisions through
    sub-classes) from the vendored, source-derived CSV. See this module's
    docstring for provenance. Idempotent, like `load_classification_from_csv`."""
    return load_classification_from_csv(session, COICOP_2018_CSV, scheme="COICOP2018")


def parent_map(nodes: Iterable[ClassificationNodeORM]) -> dict[str, str | None]:
    """A plain `{code: parent_code}` mapping from a query result, for
    handing to `validate_weight_hierarchy` without that function needing to
    know about the ORM."""
    return {n.code: n.parent_code for n in nodes}


def validate_weight_hierarchy(
    weights: Mapping[str, float],
    parent_of: Mapping[str, str | None],
    tolerance: float = 1e-6,
) -> list[str]:
    """Check that every node's weight equals the sum of its children's
    weights, wherever both are present in `weights`.

    Takes plain mappings rather than ORM objects or a DataFrame so it has
    no dependency on where the weights or the tree structure actually come
    from -- a classification tree loaded from the database, and a weight
    per code from an uploaded national-accounts weights file, in whatever
    shape that upload was parsed into.

    A node whose children are only partially present in `weights` is
    skipped rather than flagged: an incomplete weight set is a different
    problem (missing data) from an inconsistent one, and this function
    reports inconsistency, not completeness -- `data.validation` reports
    the former.

    Returns a list of human-readable problem descriptions, empty if every
    checkable node is consistent.
    """
    children_by_parent: dict[str, list[str]] = defaultdict(list)
    for code, parent in parent_of.items():
        if parent:
            children_by_parent[parent].append(code)

    problems: list[str] = []
    for parent, children in sorted(children_by_parent.items()):
        if parent not in weights:
            continue
        if not all(c in weights for c in children):
            continue
        total = sum(weights[c] for c in children)
        parent_weight = weights[parent]
        if abs(total - parent_weight) > tolerance:
            problems.append(
                f"node {parent!r}: {len(children)} children sum to {total:.6g}, "
                f"but the node's own weight is {parent_weight:.6g} "
                f"(difference {total - parent_weight:+.6g})")
    return problems


def load_user_defined_tree(
    session: Session, scheme: str, nodes: Sequence[tuple[str, str, int, str | None]]
) -> int:
    """Register a user-defined classification tree: `nodes` is a sequence
    of `(code, label, level, parent_code)` tuples, supplied directly rather
    than read from a file, for a tree small enough to define inline (a
    company's own product hierarchy, say). Idempotent like the CSV loader.
    A tree large enough to warrant a file should use
    `load_classification_from_csv` with its own `scheme` name instead.
    """
    existing = {
        row.code for row in session.query(ClassificationNodeORM).filter_by(scheme=scheme)
    }
    inserted = 0
    for code, label, level, parent_code in nodes:
        if code in existing:
            continue
        session.add(ClassificationNodeORM(
            scheme=scheme, code=code, label=label, level=level, parent_code=parent_code))
        existing.add(code)
        inserted += 1
    session.flush()
    return inserted
