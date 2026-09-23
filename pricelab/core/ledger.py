"""The approval ledgers: who decided what, when, and on what grounds.

Two of them now -- quality adjustment valuations, and outlier review
decisions -- sharing one shape because they are the same kind of object. A
person made a judgement about a specific observation, with a reason, and the
index that follows depends on it. Both are keyed by the input data's content
hash rather than by a run, so the judgements come back when the same
collection is uploaded again and an auditor can list every one ever made
against a dataset whether or not the run that used it was registered.

The quality adjustment ledger's record: who approved which replacement
valuation, when, on what grounds.

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

from .config import OutlierDecision, QualityAdjustmentEntry
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


# ---------------------------------------------------------------------
# The outlier review queue's decisions
# ---------------------------------------------------------------------
class OutlierDecisionORM(Base):
    """One analyst's decision on one flagged quote.

    There is no "delete this quote" row and no way to write one. The
    decision is `accept`, `reject` or `annotate`, the reason is mandatory at
    the column level as well as in `core.config.OutlierDecision`, and
    `reject` means the engine excludes the quote and reports the exclusion
    -- it never means the row leaves the data. Withdrawal works the way the
    quality adjustment ledger's does: the record stays and is marked, so the
    audit trail shows that a judgement was made and then reversed rather
    than showing nothing at all.
    """

    __tablename__ = "outlier_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    item_id: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    method: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    statistic: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    analyst: Mapped[str] = mapped_column(String(255), nullable=False)
    decided_at: Mapped[str] = mapped_column(String(32), nullable=False)
    withdrawn_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    withdrawn_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    withdrawal_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    def to_entry(self) -> OutlierDecision:
        return OutlierDecision(
            period=self.period, item_id=self.item_id, category=self.category,
            method=self.method, statistic=self.statistic, decision=self.decision,
            reason=self.reason, analyst=self.analyst, decided_at=self.decided_at)


def record_outlier_decision(session: Session, actor: str, content_hash: str,
                            entry: OutlierDecision) -> OutlierDecisionORM:
    """Persist one decision, replacing any live decision on the same quote.

    Replacing rather than adding, because one quote has one decision: two
    live rows for the same quote would leave the index depending on which
    one a query happened to return first. The superseded row is withdrawn,
    not deleted, so the record still shows that somebody decided one thing
    and then decided another.

    The reason is mandatory here as well as on the model. Callers with an
    audit session must also log `core.audit.OUTLIER_DECISION`; the page does.
    """
    if not (entry.reason or "").strip():
        raise ValueError(
            "an outlier decision must state a reason: a quote excluded from a published index "
            "without one is a deletion nobody can review")
    now = datetime.now(UTC).isoformat()
    live = session.scalars(
        select(OutlierDecisionORM)
        .where(OutlierDecisionORM.content_hash == content_hash)
        .where(OutlierDecisionORM.period == entry.period)
        .where(OutlierDecisionORM.item_id == entry.item_id)
        .where(OutlierDecisionORM.withdrawn_at.is_(None))).all()
    for previous in live:
        previous.withdrawn_at = now
        previous.withdrawn_by = actor or entry.analyst
        previous.withdrawal_reason = "superseded by a later decision on the same quote"

    record = OutlierDecisionORM(
        content_hash=content_hash, period=entry.period, item_id=entry.item_id,
        category=entry.category, method=entry.method, statistic=float(entry.statistic),
        decision=entry.decision, reason=entry.reason, analyst=actor or entry.analyst,
        decided_at=entry.decided_at or now)
    session.add(record)
    session.flush()
    return record


def withdraw_outlier_decision(session: Session, actor: str, record_id: int,
                              reason: str) -> None:
    """A decision is never deleted; it is marked withdrawn, with a reason,
    and stops being applied."""
    if not reason or not reason.strip():
        raise ValueError("withdrawing an outlier decision needs a reason")
    record = session.get(OutlierDecisionORM, record_id)
    if record is None:
        raise ValueError(f"no outlier decision with id {record_id}")
    record.withdrawn_at = datetime.now(UTC).isoformat()
    record.withdrawn_by = actor
    record.withdrawal_reason = reason
    session.flush()


def load_outlier_decisions(session: Session, content_hash: str, *,
                           include_withdrawn: bool = False) -> list[OutlierDecisionORM]:
    """Every decision recorded against this input data, oldest first."""
    stmt = select(OutlierDecisionORM).where(OutlierDecisionORM.content_hash == content_hash)
    if not include_withdrawn:
        stmt = stmt.where(OutlierDecisionORM.withdrawn_at.is_(None))
    return list(session.scalars(stmt.order_by(OutlierDecisionORM.id)).all())


def active_outlier_entries(session: Session, content_hash: str) -> list[OutlierDecision]:
    """The queue's decisions as `RunConfig.outlier.entries` should carry them."""
    return [r.to_entry() for r in load_outlier_decisions(session, content_hash)]
