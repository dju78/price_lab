"""The column mapping workspace: a confidence score alongside
`engine.auto.infer_schema`'s guess for each canonical field, and storage
for the mapping an analyst actually confirmed, keyed by the file's content
hash so the same file maps identically the next time it is uploaded.

Reuses `infer_schema` for the guess itself rather than reimplementing
column matching a second time with scores attached; this module only adds
the confidence layer and the persistence `infer_schema` never needed
before an analyst's confirmation became something to remember.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import String
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..core.config import Schema
from ..core.db import Base
from ..engine.auto import infer_schema

CANONICAL_FIELDS = ("date", "item_id", "item_name", "category", "price", "weight")


@dataclass
class MappingSuggestion:
    field: str
    """The canonical field this suggestion is for (date, item_id, ...)."""
    column: str | None
    """The source column suggested for it, or None if nothing matched."""
    confidence: float
    """0.0-1.0. 1.0: the column name matches the canonical field's own
    default name. 0.8: a keyword associated with the field appears in the
    column name. 0.4: resolved by elimination (cardinality ranking, or the
    only column left), the same fallback `infer_schema` itself uses when
    naming gives no signal. 0.0: no source column at all -- `infer_schema`
    always returns some column even then (it falls back to the first or
    last column present), so a caller must not treat a low-but-nonzero
    confidence as "no mapping"."""


#: The exact default column name `core.config.Schema` uses for each field,
#: matched case-insensitively for a confidence-1.0 suggestion.
_DEFAULT_NAMES = {
    "date": "date", "item_id": "item_id", "item_name": "item_name",
    "category": "category", "price": "reported_price", "weight": "weight",
}

#: The same keyword lists `infer_schema` matches on, kept here only to
#: score *how* a suggestion was found, not to find it a second time.
_KEYWORDS = {
    "date": ("date", "period", "month", "time"),
    "item_id": ("item_id", "itemid", "product_id", "sku", "id"),
    "item_name": ("item_name", "name", "description", "product"),
    "category": ("category", "group", "class", "division"),
    "price": ("price", "value", "cost", "amount"),
    "weight": ("weight", "expenditure"),
}


def suggest_column_mapping(df: pd.DataFrame) -> dict[str, MappingSuggestion]:
    """A `MappingSuggestion` per canonical field, using `infer_schema`'s
    guess with a confidence score attached."""
    guess = infer_schema(df)
    guessed = {
        "date": guess.date, "item_id": guess.item_id, "item_name": guess.item_name,
        "category": guess.category, "price": guess.price, "weight": guess.weight,
    }
    suggestions = {}
    for field in CANONICAL_FIELDS:
        column = guessed[field]
        suggestions[field] = MappingSuggestion(
            field=field, column=column, confidence=_confidence(field, column))
    return suggestions


def _confidence(field: str, column: str | None) -> float:
    if column is None:
        return 0.0
    normalized = column.strip().lower().replace(" ", "_")
    if normalized == _DEFAULT_NAMES[field]:
        return 1.0
    if any(keyword in normalized for keyword in _KEYWORDS[field]):
        return 0.8
    return 0.4


def file_hash_of(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


class ColumnMappingORM(Base):
    __tablename__ = "column_mappings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    schema_json: Mapped[str] = mapped_column(String, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    confirmed_at: Mapped[str] = mapped_column(String(32), nullable=False)


def store_confirmed_mapping(
    session: Session, file_hash: str, schema: Schema, actor: str
) -> ColumnMappingORM:
    """Record the mapping an analyst confirmed for this exact file
    content, so re-uploading it maps identically without asking again.
    Overwrites a previous confirmation for the same `file_hash` rather
    than accumulating stale ones: a file's content hash is exactly what it
    is, and the newest confirmation for it is the one that should apply.
    """
    existing = session.query(ColumnMappingORM).filter_by(file_hash=file_hash).one_or_none()
    schema_json = schema.model_dump_json()
    now = datetime.now(UTC).isoformat()
    if existing is not None:
        existing.schema_json = schema_json
        existing.confirmed_by = actor
        existing.confirmed_at = now
        session.add(existing)
        session.flush()
        return existing
    record = ColumnMappingORM(
        file_hash=file_hash, schema_json=schema_json, confirmed_by=actor, confirmed_at=now)
    session.add(record)
    session.flush()
    return record


def get_confirmed_mapping(session: Session, file_hash: str) -> Schema | None:
    record = session.query(ColumnMappingORM).filter_by(file_hash=file_hash).one_or_none()
    if record is None:
        return None
    return Schema.model_validate_json(record.schema_json)
