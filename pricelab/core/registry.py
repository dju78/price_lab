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
from sqlalchemy import Boolean, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, Session, mapped_column

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


def register_run(session: Session, df: pd.DataFrame, config: RunConfig, label: str) -> IndexRunORM:
    """Register a run's input and parameters. Idempotent: registering the
    same data under the same configuration again returns the existing
    record rather than creating a duplicate, because the whole point of a
    content hash is that identical inputs are recognised as identical."""
    input_hash = _hash_dataframe(df)
    config_json = config.to_json()
    content_hash = hashlib.sha256((input_hash + config_json).encode("utf-8")).hexdigest()
    run_id = content_hash[:16]

    existing = session.query(IndexRunORM).filter_by(run_id=run_id).one_or_none()
    if existing is not None:
        return existing

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

    new_run = register_run(session, df, config, label)
    if new_run.run_id == original.run_id:
        raise ValueError("the correction is byte-identical to the run it corrects")
    new_run.vintage = original.vintage + 1
    new_run.correction_reason = reason
    new_run.supersedes_run_id = run_id
    session.add(new_run)
    session.flush()
    return new_run


def reproduce(session: Session, run_id: str) -> dict[str, Any]:
    """Re-execute a registered run from exactly its stored input and
    configuration, returning what `pricelab.run_pipeline` returns live.

    Imports `pricelab.run_pipeline` at call time rather than at module load:
    `pricelab/__init__.py` imports from `core.config` while it is being
    built, so importing the top-level package back from inside `core` at
    module scope would be circular. By the time anything actually calls
    `reproduce`, `import pricelab` has already completed.
    """
    from .. import run_pipeline

    run = session.query(IndexRunORM).filter_by(run_id=run_id).one()
    df = pd.read_parquet(io.BytesIO(run.input_parquet))
    config = RunConfig.from_dict(json.loads(run.config_json))
    result: dict[str, Any] = run_pipeline(df, config)
    return result
