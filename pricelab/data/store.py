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
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    imputed = _transform(raw_df, config)

    path = cleaned_dir / f"{raw_hash}.parquet"
    buf = io.BytesIO()
    imputed.to_parquet(buf, index=False)
    path.write_bytes(buf.getvalue())

    log = TransformationLog(raw_content_hash=raw_hash, config=config, created_at=datetime.now(UTC))
    (cleaned_dir / f"{raw_hash}.log.json").write_text(log.to_json(), encoding="utf-8")
    return path, log


def _transform(raw_df: pd.DataFrame, config: RunConfig) -> pd.DataFrame:
    """The raw-to-cleaned transformation, in exactly the pipeline's order:
    fault repair, then the approved replacement links, then imputation.
    One function, called by both `write_cleaned_layer` and `replay`, so
    the log replays through the same code path production runs."""
    from ..engine.quality_adjustment import apply_adjustments

    clean, _quality_summary = run_quality(raw_df, config.quality)
    clean, _link_log = apply_adjustments(clean, config.quality_adjustment.entries)
    return run_imputation(clean, config.imputation)


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
    return _transform(raw_df, log.config)


# ---------------------------------------------------------------------
# Upload provenance: the vintage sidecar and the characteristics layer
# ---------------------------------------------------------------------
@dataclass
class UploadVintage:
    """Where an uploaded dataset came from and when it was received: the
    upload's counterpart of a connector's `Vintage`. Written beside the raw
    layer as a JSON sidecar, one receipt per upload event, so the raw file
    (named by its content) also carries who supplied that content, from
    which file, and when."""

    kind: str
    """"prices" or "characteristics"."""
    source: str
    """"upload:<file name>"."""
    file_name: str
    file_sha256: str
    """Hash of the bytes as received, before parsing -- distinct from the
    content hash, which is of the parsed table and row-order independent."""
    content_hash: str
    received_at: datetime
    received_by: str
    raw_path: str

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "received_at": self.received_at.isoformat()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UploadVintage:
        return cls(**{**d, "received_at": datetime.fromisoformat(d["received_at"])})


def vintage_sidecar_path(directory: Path | str, content_hash: str) -> Path:
    return Path(directory) / "raw" / f"{content_hash}.vintage.json"


def record_upload(
    df: pd.DataFrame, *, kind: str, file_name: str, file_bytes: bytes, actor: str,
    directory: Path | str,
) -> UploadVintage:
    """Write the raw layer for an upload and append its vintage receipt.

    Idempotent on content: the same table lands at the same raw path
    (`write_raw_layer`), and the sidecar accumulates one receipt per upload
    event, so re-uploading the same file tomorrow adds a receipt rather
    than losing today's.
    """
    raw_path = write_raw_layer(df, directory)
    vintage = UploadVintage(
        kind=kind, source=f"upload:{file_name}", file_name=file_name,
        file_sha256=hashlib.sha256(file_bytes).hexdigest(),
        content_hash=raw_path.stem, received_at=datetime.now(UTC), received_by=actor,
        raw_path=str(raw_path))
    sidecar = vintage_sidecar_path(directory, raw_path.stem)
    receipts: list[dict[str, Any]] = []
    if sidecar.exists():
        receipts = json.loads(sidecar.read_text(encoding="utf-8"))
    receipts.append(vintage.to_dict())
    sidecar.write_text(json.dumps(receipts, indent=2), encoding="utf-8")
    return vintage


def load_vintages(directory: Path | str, content_hash: str) -> list[UploadVintage]:
    sidecar = vintage_sidecar_path(directory, content_hash)
    if not sidecar.exists():
        return []
    return [UploadVintage.from_dict(d) for d in json.loads(sidecar.read_text(encoding="utf-8"))]


@dataclass
class CharacteristicsTransformationLog:
    """What `standardise_characteristics` did to a raw characteristics
    table, sufficient to regenerate the cleaned table from the raw one.
    The steps are the log; `replay_characteristics` re-applies the same
    function and checks the result's hash against the one recorded."""

    raw_content_hash: str
    cleaned_content_hash: str
    steps: list[str]
    created_at: datetime

    def to_json(self) -> str:
        return json.dumps({
            "raw_content_hash": self.raw_content_hash,
            "cleaned_content_hash": self.cleaned_content_hash,
            "steps": self.steps, "created_at": self.created_at.isoformat()}, indent=2)

    @classmethod
    def from_json(cls, text: str) -> CharacteristicsTransformationLog:
        d = json.loads(text)
        return cls(raw_content_hash=d["raw_content_hash"],
                   cleaned_content_hash=d["cleaned_content_hash"], steps=list(d["steps"]),
                   created_at=datetime.fromisoformat(d["created_at"]))


def standardise_characteristics(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """The deterministic raw-to-cleaned transformation for a
    characteristics file: column names stripped, `item_id` as text,
    columns that parse entirely as numbers made numeric, empty rows
    dropped. Returns the cleaned table and the list of steps applied,
    which is what the transformation log records."""
    steps: list[str] = []
    out = raw.copy()
    stripped = {c: str(c).strip() for c in out.columns}
    if any(k != v for k, v in stripped.items()):
        out = out.rename(columns=stripped)
        steps.append("stripped whitespace from column names")
    if "item_id" not in out.columns:
        raise ValueError("a characteristics file needs an item_id column")
    before = len(out)
    out = out.dropna(how="all")
    if len(out) != before:
        steps.append(f"dropped {before - len(out)} entirely empty rows")
    out["item_id"] = out["item_id"].astype(str).str.strip()
    steps.append("item_id cast to text and stripped")
    for col in [c for c in out.columns if c != "item_id"]:
        if pd.api.types.is_numeric_dtype(out[col]):
            continue
        coerced = pd.to_numeric(out[col], errors="coerce")
        non_null = out[col].notna()
        if non_null.any() and coerced[non_null].notna().all():
            out[col] = coerced
            steps.append(f"{col}: parsed as numeric")
    return out.reset_index(drop=True), steps


def write_characteristics_layers(
    raw: pd.DataFrame, directory: Path | str
) -> tuple[Path, Path, CharacteristicsTransformationLog]:
    """Raw layer, cleaned layer and transformation log for a
    characteristics table, mirroring `write_raw_layer` /
    `write_cleaned_layer` for prices."""
    directory = Path(directory)
    raw_path = write_raw_layer(raw, directory)
    cleaned, steps = standardise_characteristics(raw)
    cleaned_dir = directory / "cleaned"
    cleaned_dir.mkdir(parents=True, exist_ok=True)
    cleaned_path = cleaned_dir / f"characteristics-{raw_path.stem}.parquet"
    buf = io.BytesIO()
    cleaned.to_parquet(buf, index=False)
    cleaned_path.write_bytes(buf.getvalue())
    log = CharacteristicsTransformationLog(
        raw_content_hash=raw_path.stem, cleaned_content_hash=content_hash_of(cleaned),
        steps=steps, created_at=datetime.now(UTC))
    (cleaned_dir / f"characteristics-{raw_path.stem}.log.json").write_text(
        log.to_json(), encoding="utf-8")
    return raw_path, cleaned_path, log


def replay_characteristics(raw: pd.DataFrame, log: CharacteristicsTransformationLog) -> pd.DataFrame:
    """Regenerate the cleaned characteristics table from the raw one and
    check it against the log's recorded hash, refusing the wrong raw
    layer the way `replay` does."""
    actual = content_hash_of(raw)
    if actual != log.raw_content_hash:
        raise ImmutableLayerError(
            f"raw characteristics hash {actual} does not match the log's {log.raw_content_hash}")
    cleaned, steps = standardise_characteristics(raw)
    if steps != log.steps or content_hash_of(cleaned) != log.cleaned_content_hash:
        raise ImmutableLayerError(
            "replaying the characteristics transformation did not reproduce the logged "
            "cleaned layer; the transformation code has changed since the log was written")
    return cleaned
