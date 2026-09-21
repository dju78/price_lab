"""Storage: an immutable raw layer, a cleaned layer derived from it, and a
transformation log sufficient to regenerate the cleaned layer from the raw
layer deterministically.

The transformation log is deliberately not a bespoke list of operations:
it is exactly the `RunConfig` (quality + imputation settings) that
`engine.quality.run_quality` and `engine.imputation.run_imputation` already
take, because those two calls *are* the raw-to-cleaned transformation this
pipeline performs. Logging the config and replaying it by calling the same
two functions again means replay uses the exact code path production does,
not a second, parallel implementation that could silently drift from it.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from ..core.config import RunConfig
from ..engine.imputation import run_imputation
from ..engine.quality import run_quality


class ImmutableLayerError(Exception):
    """Raised by `write_raw_layer` when asked to overwrite a raw file that
    already exists. The raw layer is written once; a second, different
    upload of "the same" file gets its own path (named by content hash),
    never an overwrite of an existing one."""


@dataclass
class TransformationLog:
    """What was done to turn a raw layer into a cleaned one: the
    configuration `run_quality`/`run_imputation` were called with, plus
    when. Sufficient, together with the raw layer, to regenerate the
    cleaned layer exactly -- see `replay`."""

    raw_content_hash: str
    config: RunConfig
    created_at: datetime

    def to_json(self) -> str:
        return json.dumps({
            "raw_content_hash": self.raw_content_hash,
            "config": self.config.model_dump(by_alias=True, mode="json"),
            "created_at": self.created_at.isoformat(),
        }, indent=2)

    @classmethod
    def from_json(cls, text: str) -> TransformationLog:
        data = json.loads(text)
        return cls(
            raw_content_hash=data["raw_content_hash"],
            config=RunConfig.from_dict(data["config"]),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


def content_hash_of(df: pd.DataFrame) -> str:
    """The same content-hashing approach `core.registry` uses: independent
    of row order, so two loads of the same data always hash identically."""
    canonical = df.sort_values(list(df.columns)).reset_index(drop=True)
    row_hashes = pd.util.hash_pandas_object(canonical, index=False).to_numpy()
    return hashlib.sha256(row_hashes.tobytes()).hexdigest()


def write_raw_layer(df: pd.DataFrame, directory: Path | str) -> Path:
    """Write `df` to `{directory}/raw/{content_hash}.parquet`, refusing to
    overwrite a file that already exists there. The same input always
    lands at the same path (its content hash), so calling this again with
    identical data is a safe no-op check, not a second write; calling it
    with a path that exists but was written by something else entirely
    (which should be impossible, since the path *is* the content's hash)
    is refused loudly rather than silently trusting the existing file.
    """
    directory = Path(directory)
    raw_dir = directory / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    digest = content_hash_of(df)
    path = raw_dir / f"{digest}.parquet"
    if path.exists():
        existing = pd.read_parquet(path)
        if content_hash_of(existing) != digest:
            raise ImmutableLayerError(
                f"{path} already exists and its content hash does not match what was just "
                "computed for it; the raw layer must never be overwritten")
        return path
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    path.write_bytes(buf.getvalue())
    return path


def write_cleaned_layer(
    raw_df: pd.DataFrame, config: RunConfig, directory: Path | str
) -> tuple[Path, TransformationLog]:
    """Derive the cleaned layer from `raw_df` via the exact quality and
    imputation calls the analysis pipeline itself makes, write it to
    `{directory}/cleaned/{raw_content_hash}.parquet`, and return it
    alongside the `TransformationLog` that can regenerate it."""
    directory = Path(directory)
    cleaned_dir = directory / "cleaned"
    cleaned_dir.mkdir(parents=True, exist_ok=True)

    raw_hash = content_hash_of(raw_df)
    clean, _quality_summary = run_quality(raw_df, config.quality)
    imputed = run_imputation(clean, config.imputation)

    path = cleaned_dir / f"{raw_hash}.parquet"
    buf = io.BytesIO()
    imputed.to_parquet(buf, index=False)
    path.write_bytes(buf.getvalue())

    log = TransformationLog(raw_content_hash=raw_hash, config=config, created_at=datetime.now(UTC))
    return path, log


def replay(raw_df: pd.DataFrame, log: TransformationLog) -> pd.DataFrame:
    """Regenerate the cleaned layer from the raw layer and a transformation
    log, deterministically: the same `run_quality`/`run_imputation` calls
    `write_cleaned_layer` made, against the same raw data and the same
    logged configuration.

    Raises `ImmutableLayerError` if `raw_df` does not hash to what the log
    says it should -- replaying a transformation log against the wrong raw
    layer would silently produce a plausible-looking but wrong cleaned
    layer, which is worse than refusing.
    """
    actual_hash = content_hash_of(raw_df)
    if actual_hash != log.raw_content_hash:
        raise ImmutableLayerError(
            f"raw data content hash {actual_hash} does not match the transformation log's "
            f"{log.raw_content_hash}; refusing to replay against the wrong raw layer")
    clean, _quality_summary = run_quality(raw_df, log.config.quality)
    imputed = run_imputation(clean, log.config.imputation)
    return imputed
