"""Append-only, hash-chained audit log.

Persisted to the database, not to session state: a fact the platform's own
governance rules depend on ("every number traces back through the audit
trail") must survive a browser refresh and be visible to more than the one
browser tab that made the change. Each record's hash commits to the previous
record's hash and its own content, so altering or deleting a past record
breaks every hash computed after it. `verify_chain` walks the table in order
and reports exactly where a break first appears.

The timestamp is stored as an ISO-8601 string rather than a native SQL
datetime column deliberately: SQLite has no real datetime type, and letting
SQLAlchemy round-trip a `datetime` through it risks losing timezone
information on read-back, which would make `verify_chain` recompute a
different hash than the one that was actually stored and misreport an intact
chain as tampered. A plain string column round-trips exactly.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base

GENESIS_HASH = "0" * 64

# Well-known action strings, kept as constants rather than a database enum
# so a new action never needs a migration. The Phase 1 spec's minimum set:
# login and logout, data load, configuration change, calculation run,
# quality or imputation override, and export.
LOGIN = "login"
LOGOUT = "logout"
DATA_LOAD = "data_load"
CONFIGURATION_CHANGE = "configuration_change"
CALCULATION_RUN = "calculation_run"
QUALITY_OVERRIDE = "quality_override"
IMPUTATION_OVERRIDE = "imputation_override"
EXPORT = "export"
LEGACY_CONFIG_UPCONVERTED = "legacy_config_upconverted"
EXTERNAL_FETCH_SUCCESS = "external_fetch_success"
EXTERNAL_FETCH_FAILURE = "external_fetch_failure"
VALIDATION_OVERRIDE = "validation_override"
MAPPING_CONFIRMED = "mapping_confirmed"
QUALITY_ADJUSTMENT_APPROVED = "quality_adjustment_approved"
QUALITY_ADJUSTMENT_WITHDRAWN = "quality_adjustment_withdrawn"
OUTLIER_DECISION = "outlier_decision"
OUTLIER_DECISION_WITHDRAWN = "outlier_decision_withdrawn"
SEASONAL_ADJUSTMENT = "seasonal_adjustment"
REVISION_ANALYSIS = "revision_analysis"
RUN_CORRECTED = "run_corrected"
DECOMPOSITION = "decomposition"
DEFLATION = "deflation"
SPATIAL_COMPARISON = "spatial_comparison"
TRADE_INDEX = "trade_index"
CONSTRUCTION_INDEX = "construction_index"
ESCALATION = "escalation"
PROPERTY_INDEX = "property_index"
OWNER_OCCUPIED_HOUSING = "owner_occupied_housing"


class AuditEventORM(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    params_json: Mapped[str] = mapped_column(String, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)


def _hash_record(
    prev_hash: str, actor: str, action: str, target: str, params_json: str, created_at: str
) -> str:
    payload = "|".join([prev_hash, actor, action, target, params_json, created_at])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _last_hash(session: Session) -> str:
    last = session.query(AuditEventORM).order_by(AuditEventORM.id.desc()).first()
    return last.hash if last is not None else GENESIS_HASH


def record_event(
    session: Session, actor: str, action: str, target: str, params: dict[str, Any] | None = None
) -> AuditEventORM:
    """Append one event to the chain.

    Does not commit: callers already run inside `core.db.session_scope`
    (or an equivalent unit of work) and may want to record several events,
    or none, depending on what else happens in that unit of work.
    """
    prev_hash = _last_hash(session)
    created_at = datetime.now(UTC).isoformat()
    params_json = json.dumps(params or {}, default=str, sort_keys=True)
    event = AuditEventORM(
        prev_hash=prev_hash,
        hash=_hash_record(prev_hash, actor, action, target, params_json, created_at),
        actor=actor,
        action=action,
        target=target,
        params_json=params_json,
        created_at=created_at,
    )
    session.add(event)
    session.flush()
    return event


def verify_chain(session: Session) -> tuple[bool, int | None]:
    """Walk the log in order and confirm each record's hash follows
    correctly from the content and hash of the one before it.

    Returns `(True, None)` if the chain is intact, or `(False, event_id)`
    naming the first record where it no longer matches -- which means either
    that record's own content was altered after the fact, or a record was
    deleted from ahead of it in the sequence.
    """
    events = session.query(AuditEventORM).order_by(AuditEventORM.id.asc()).all()
    prev_hash = GENESIS_HASH
    for event in events:
        if event.prev_hash != prev_hash:
            return False, event.id
        expected = _hash_record(
            event.prev_hash, event.actor, event.action, event.target,
            event.params_json, event.created_at)
        if expected != event.hash:
            return False, event.id
        prev_hash = event.hash
    return True, None


def extract_for_run(session: Session, *, label: str, content_hash: str | None = None,
                    run_id: str | None = None, correlation_id: str | None = None) -> pd.DataFrame:
    """The audit events that concern one run, as a table: every event whose
    target is the run's label or registry id, or whose parameters name the
    input data's content hash, the run id or the pipeline correlation id.
    This is the "audit extract" sheet of the evidence pack and the chain a
    bulletin's headline is traced through in tests/test_end_to_end.py.
    """
    needles = [n for n in (content_hash, run_id, correlation_id) if n]
    rows = []
    for e in session.query(AuditEventORM).order_by(AuditEventORM.id).all():
        hit = e.target in {label, f"run {run_id}"} or (run_id and run_id in e.target) \
            or any(n in e.params_json for n in needles)
        if hit:
            rows.append({"id": e.id, "created_at": e.created_at, "actor": e.actor,
                         "action": e.action, "target": e.target, "params": e.params_json,
                         "hash": e.hash, "prev_hash": e.prev_hash})
    return pd.DataFrame(rows, columns=["id", "created_at", "actor", "action", "target", "params",
                                       "hash", "prev_hash"])
