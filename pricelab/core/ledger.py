"""The quality adjustment ledger's persistent record: who approved which
replacement valuation, when, on what grounds.

The *applied* ledger is `RunConfig.quality_adjustment` -- that is what the
registry hashes and `reproduce()` re-runs. This table is the approval
record behind it, keyed by the input data's content hash the way
validation overrides are, so that when the same collection is uploaded
again its approved replacements come back with it rather than having to
be valued and approved a second time, and so an auditor can list every
approval ever made against a dataset whether or not the run that used it
was registered.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import Float, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .config import QualityAdjustmentEntry
from .db import Base


class QualityAdjustmentORM(Base):
    __tablename__ = "quality_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    old_item: Mapped[str] = mapped_column(String(255), nullable=False)
    new_item: Mapped[str] = mapped_column(String(255), nullable=False)
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    parameters_json: Mapped[str] = mapped_column(String, nullable=False)
    justification: Mapped[str] = mapped_column(String, nullable=False)
    approved_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_at: Mapped[str] = mapped_column(String(32), nullable=False)
    withdrawn_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    withdrawn_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    withdrawal_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    def to_entry(self) -> QualityAdjustmentEntry:
        return QualityAdjustmentEntry(
            old_item=self.old_item, new_item=self.new_item, category=self.category,
            period=self.period, method=self.method, quality_ratio=self.quality_ratio,
            parameters=json.loads(self.parameters_json), justification=self.justification,
            approved_by=self.approved_by, approved_at=self.approved_at)


def record_adjustment(
    session: Session, actor: str, content_hash: str, entry: QualityAdjustmentEntry,
) -> QualityAdjustmentORM:
    """Persist one approval. The justification is mandatory: an
    adjustment with no stated grounds is not reviewable, and the ledger
    exists to be reviewed. Callers with an audit session should also log
    `core.audit.QUALITY_ADJUSTMENT_APPROVED`."""
    if not entry.justification or not entry.justification.strip():
        raise ValueError("a quality adjustment must state its justification")
    now = datetime.now(UTC).isoformat()
    record = QualityAdjustmentORM(
        content_hash=content_hash, category=entry.category, old_item=entry.old_item,
        new_item=entry.new_item, period=entry.period, method=entry.method,
        quality_ratio=float(entry.quality_ratio), parameters_json=json.dumps(entry.parameters),
        justification=entry.justification, approved_by=actor or entry.approved_by,
        approved_at=entry.approved_at or now)
    session.add(record)
    session.flush()
    return record


def withdraw_adjustment(session: Session, actor: str, record_id: int, reason: str) -> None:
    """An approval is never deleted; it is marked withdrawn, with a reason,
    and stops being applied. The row stays so the audit trail can show
    that a valuation was made and then reversed."""
    if not reason or not reason.strip():
        raise ValueError("withdrawing a quality adjustment needs a reason")
    record = session.get(QualityAdjustmentORM, record_id)
    if record is None:
        raise ValueError(f"no quality adjustment with id {record_id}")
    record.withdrawn_at = datetime.now(UTC).isoformat()
    record.withdrawn_by = actor
    record.withdrawal_reason = reason
    session.flush()


def load_ledger(session: Session, content_hash: str, *, include_withdrawn: bool = False
                ) -> list[QualityAdjustmentORM]:
    """Every approval recorded against this input data, oldest first."""
    stmt = select(QualityAdjustmentORM).where(QualityAdjustmentORM.content_hash == content_hash)
    if not include_withdrawn:
        stmt = stmt.where(QualityAdjustmentORM.withdrawn_at.is_(None))
    return list(session.scalars(stmt.order_by(QualityAdjustmentORM.id)).all())


def active_entries(session: Session, content_hash: str) -> list[QualityAdjustmentEntry]:
    """The ledger as `RunConfig.quality_adjustment.entries` should carry it."""
    return [r.to_entry() for r in load_ledger(session, content_hash)]
