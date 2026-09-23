"""Index run registry.

Every run is registered with a hash of its input data, its complete
parameter set, the code version it ran under and a fingerprint of the
Python and library versions in play, so a published figure can be
reproduced byte for byte rather than merely re-derived by eyeballing a
downloaded config file. `reproduce` re-executes a registered run from
exactly that stored record.

This supersedes the config-JSON-only reproducibility `RunConfig.to_json()`
gave before Phase 1; that path keeps working (a config JSON still loads via
`RunConfig.from_dict`), it just no longer needs to be the *only* record of
how a number was produced, because the input data and the run's outcome are
now registered alongside it.

An approved run is immutable: `approve_run` marks it so, and any further
change to that analysis must go through `correct_run`, which registers a new
vintage with a required reason rather than editing the original in place.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import Any

import pandas as pd
from sqlalchemy import Boolean, Float, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import audit
from .config import RunConfig
from .db import Base

_FINGERPRINT_LIBRARIES = ("pandas", "numpy", "pydantic", "streamlit", "sqlalchemy")


class IndexRunORM(Base):
    __tablename__ = "index_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[str] = mapped_column(String, nullable=False)
    code_version: Mapped[str] = mapped_column(String(64), nullable=False)
    environment_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    vintage: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    correction_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    supersedes_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    input_parquet: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    price_reference_period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    weight_reference_period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    index_reference_period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The published number, recorded at registration (Phase 10) so a
    # bulletin reads it from here rather than recomputing it, and so a
    # later reproduction can be checked against what was actually
    # registered. NULL for runs registered before these columns existed.
    headline_series: Mapped[str | None] = mapped_column(String(255), nullable=True)
    headline_period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    headline_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    headline_reference_period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Where the input data came from and when it was received: the data
    # vintage, alongside `input_hash` which is its content.
    data_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data_received_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


def _code_version() -> str:
    """The short git commit this process is running, or "unknown" outside a
    git checkout (a source distribution, a container built without the
    .git directory). Reproducibility degrades gracefully rather than
    failing the whole registration when this can't be determined."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True)
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _environment_fingerprint() -> str:
    parts = [f"python-{sys.version.split()[0]}"]
    for lib in _FINGERPRINT_LIBRARIES:
        try:
            parts.append(f"{lib}-{pkg_version(lib)}")
        except PackageNotFoundError:
            continue
    return ";".join(parts)


def _hash_dataframe(df: pd.DataFrame) -> str:
    """A content hash of the actual values, independent of row order, so two
    uploads of the same collection register as the same input even if pandas
    happened to read the rows back in a different order."""
    canonical = df.sort_values(list(df.columns)).reset_index(drop=True)
    row_hashes = pd.util.hash_pandas_object(canonical, index=False).to_numpy()
    return hashlib.sha256(row_hashes.tobytes()).hexdigest()


def headline_of(indices: pd.DataFrame, config: RunConfig) -> dict[str, Any]:
    """The one number a release leads with: the aggregate series' level at
    the final period, with the period it is for and the period it reads
    `base_value` at. The "All items" column when there is one, else the
    single series the collection has."""
    from ..engine.index import resolve_index_reference_period

    series = "All items" if "All items" in indices.columns else str(indices.columns[0])
    final = pd.Timestamp(indices.index[-1])
    return {
        "series": series,
        "period": str(final.date()),
        "value": float(indices[series].iloc[-1]),
        "reference_period": str(
            resolve_index_reference_period(config.index, indices.index).date()),
    }


def register_run(
    session: Session, df: pd.DataFrame, config: RunConfig, label: str,
    *, result: dict[str, Any] | None = None,
    data_source: str | None = None, data_received_at: str | None = None,
) -> IndexRunORM:
    """Register a run's input and parameters. Idempotent: registering the
    same data under the same configuration again returns the existing
    record rather than creating a duplicate, because the whole point of a
    content hash is that identical inputs are recognised as identical.

    The headline figure is recorded with the run. `result` is the pipeline
    output already computed for this data and configuration, when the
    caller has it; otherwise the pipeline is run here, because a registry
    entry without the number it published is a record of an input, not of
    a release.
    """
    input_hash = _hash_dataframe(df)
    config_json = config.to_json()
    content_hash = hashlib.sha256((input_hash + config_json).encode("utf-8")).hexdigest()
    run_id = content_hash[:16]

    existing = session.query(IndexRunORM).filter_by(run_id=run_id).one_or_none()
    if existing is not None:
        return existing

    if result is None:
        from .. import run_pipeline
        result = run_pipeline(df, config)
    headline = headline_of(result["indices"], config) if "indices" in result else None

    buf = io.BytesIO()
    df.to_parquet(buf, index=False)

    run = IndexRunORM(
        run_id=run_id,
        input_hash=input_hash,
        content_hash=content_hash,
        config_json=config_json,
        code_version=_code_version(),
        environment_fingerprint=_environment_fingerprint(),
        label=label,
        created_at=datetime.now(UTC).isoformat(),
        approved=False,
        vintage=1,
        input_parquet=buf.getvalue(),
        # Recorded exactly as configured, including None when the run never
        # set one: that is itself the honest record (it means the engine's
        # own default applied), not something to synthesise a value for.
        price_reference_period=config.index.price_reference_period,
        weight_reference_period=config.index.weight_reference_period,
        index_reference_period=config.index.index_reference_period,
        headline_series=headline["series"] if headline else None,
        headline_period=headline["period"] if headline else None,
        headline_value=headline["value"] if headline else None,
        headline_reference_period=headline["reference_period"] if headline else None,
        data_source=data_source,
        data_received_at=data_received_at,
    )
    session.add(run)
    session.flush()
    return run


def approve_run(session: Session, run_id: str) -> IndexRunORM:
    run = session.query(IndexRunORM).filter_by(run_id=run_id).one()
    run.approved = True
    session.add(run)
    session.flush()
    return run


def correct_run(
    session: Session, run_id: str, df: pd.DataFrame, config: RunConfig, label: str, reason: str
) -> IndexRunORM:
    """Register a corrected vintage of an approved run.

    The original record is never modified: this only ever adds a new one,
    linked back to the run it supersedes, with a mandatory reason. Correcting
    a run that was never approved is refused, because an unapproved run can
    simply be edited or re-registered directly; "correction" is a concept
    that only applies once a figure has been signed off.
    """
    if not reason or not reason.strip():
        raise ValueError("a correction must state a reason")
    original = session.query(IndexRunORM).filter_by(run_id=run_id).one()
    if not original.approved:
        raise ValueError(f"run {run_id!r} is not approved; edit or re-register it directly")

    new_run = register_run(session, df, config, label, data_source=original.data_source,
                           data_received_at=original.data_received_at)
    if new_run.run_id == original.run_id:
        raise ValueError("the correction is byte-identical to the run it corrects")
    new_run.vintage = original.vintage + 1
    new_run.correction_reason = reason
    new_run.supersedes_run_id = run_id
    session.add(new_run)
    session.flush()
    return new_run


def vintage_chain(session: Session, run_id: str) -> list[IndexRunORM]:
    """Every vintage of this run, oldest first.

    A correction registers a new row pointing back at the one it supersedes
    (`correct_run`), so the vintages are a linked list and this walks it in
    both directions from wherever the caller happened to enter: back through
    `supersedes_run_id` to the original, then forward through whatever
    superseded each one. Entering the chain at the latest vintage and
    getting only that vintage back would be the natural way to write a
    revision analysis that silently ignored every revision.

    The list is the registry's, not a copy of it. Nothing here writes, and
    no second store of past vintages exists: a vintage *is* a registered
    run, retrievable and reproducible exactly like any other.
    """
    current = session.query(IndexRunORM).filter_by(run_id=run_id).one()
    chain = [current]
    seen = {current.run_id}
    # Backwards to the original.
    node = current
    while node.supersedes_run_id and node.supersedes_run_id not in seen:
        previous = session.query(IndexRunORM).filter_by(
            run_id=node.supersedes_run_id).one_or_none()
        if previous is None:
            break
        chain.insert(0, previous)
        seen.add(previous.run_id)
        node = previous
    # Forwards to the latest.
    node = current
    while True:
        later = session.query(IndexRunORM).filter_by(
            supersedes_run_id=node.run_id).order_by(IndexRunORM.vintage).first()
        if later is None or later.run_id in seen:
            break
        chain.append(later)
        seen.add(later.run_id)
        node = later
    return chain


def reproduce(session: Session, run_id: str, actor: str = "system") -> dict[str, Any]:
    """Re-execute a registered run from exactly its stored input and
    configuration, returning what `pricelab.run_pipeline` returns live.

    Imports `pricelab.run_pipeline` at call time rather than at module load:
    `pricelab/__init__.py` imports from `core.config` while it is being
    built, so importing the top-level package back from inside `core` at
    module scope would be circular. By the time anything actually calls
    `reproduce`, `import pricelab` has already completed.

    A run registered before the price/weight/index reference period split
    (schema_version 1) has its stored config upconverted on load by
    `RunConfig.from_dict`; when that happens here, it is logged to the
    audit trail rather than left as a silent reinterpretation of what an
    old run's parameters meant. `actor` names who triggered the
    reproduction, for that log entry; callers with a signed-in user should
    pass its username rather than accepting the "system" default.
    """
    from .. import run_pipeline

    run = session.query(IndexRunORM).filter_by(run_id=run_id).one()
    df = pd.read_parquet(io.BytesIO(run.input_parquet))
    config = RunConfig.from_dict(json.loads(run.config_json))
    if config.legacy_upconverted:
        audit.record_event(
            session, actor, audit.LEGACY_CONFIG_UPCONVERTED, f"run {run_id}",
            {"upgraded_to_schema_version": config.schema_version})
    result: dict[str, Any] = run_pipeline(df, config)
    return result
