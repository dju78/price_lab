"""The provenance stamp: one record of where an export came from, built
by one function and carried by every export format.

An export that leaves this application is read by people who cannot see
the run that produced it. The stamp is what lets them ask the questions
an auditor asks -- which data, which version of the code, which
parameters, was anything suppressed, was the formula a recognised one --
and get the same answer from a CSV, a slide deck, a Word report, an
Excel evidence pack, a PDF bulletin or an SDMX message, because all six
carry the same object serialised the same way.

`build_stamp` is the one function. Each exporter embeds `stamp.to_json()`
somewhere its format can carry a string (a comment line, document
properties, a dedicated sheet, an annotation) and additionally renders
`stamp.rows()` where a reader would look for it. `reporting.readback`
reads the stamp back out of each format, which is how the tests prove
every export actually carries it rather than being told it does.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from .config import RunConfig, get_settings

STAMP_KEY = "pricelab_provenance"
"""The name every format files the JSON under: a CSV comment prefix, a
docx/pptx core-properties field, an Excel sheet name, an SDMX annotation
title, a PDF info key."""


@dataclass(frozen=True)
class ProvenanceStamp:
    run_id: str
    """The registry's run identifier, or "unregistered" for an export of a
    run nobody has registered -- stated rather than fabricated."""
    registered: bool
    label: str
    data_vintage: str
    """Content hash of the input data (row-order independent), which is
    also the raw layer's file name in the Parquet store."""
    data_source: str | None
    """The uploaded file's name, or a connector's source name."""
    data_received_at: str | None
    code_version: str
    """The git commit the exporting process runs, or "unknown"."""
    pricelab_version: str
    environment_fingerprint: str
    parameters: dict[str, Any]
    """The complete `RunConfig`, as JSON-compatible data: the ledger,
    reference periods, formula, imputation and quality settings."""
    suppression_rules: dict[str, Any]
    non_standard_formula: bool
    non_standard_expression: str | None
    quality_adjustments: int
    headline: dict[str, Any] | None
    generated_at: str
    vintage: int = 1
    """Registry vintage: 1 for a first release, higher for a correction."""
    supersedes_run_id: str | None = None
    correction_reason: str | None = None
    approved: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    """Format- or caller-specific additions (a bulletin's release date, an
    evidence pack's sheet list)."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> ProvenanceStamp:
        return cls(**json.loads(text))

    def rows(self) -> list[tuple[str, str]]:
        """The stamp as a flat list of (field, value) strings, in the order a
        reader wants them, for the human-visible rendering every format
        also carries."""
        head = self.headline or {}
        rows = [
            ("Run identifier", self.run_id + ("" if self.registered else " (not registered)")),
            ("Run label", self.label),
            ("Vintage", str(self.vintage) + (f", supersedes {self.supersedes_run_id}"
                                            if self.supersedes_run_id else "")),
            ("Approved", "yes" if self.approved else "no"),
            ("Data vintage (content hash)", self.data_vintage),
            ("Data source", self.data_source or "not recorded"),
            ("Data received", self.data_received_at or "not recorded"),
            ("Code version (git commit)", self._code_description()),
            ("PriceLab version", self.pricelab_version),
            ("Environment", self.environment_fingerprint),
            ("Headline", (f"{head.get('series')} {head.get('value'):.4f} at {head.get('period')}, "
                          f"{head.get('reference_period')} = 100") if head else "none"),
            ("Formula", str(self.parameters.get("index", {}).get("formula"))),
            ("Non-standard formula", "YES: " + str(self.non_standard_expression)
             if self.non_standard_formula else "no"),
            ("Quality adjustments applied", str(self.quality_adjustments)),
            ("Suppression", f"Cells built from fewer than {self.suppression_rules.get('min_count')} "
                            f"quotes are suppressed ({self.suppression_rules.get('method')})"),
            ("Generated", self.generated_at),
            ("Parameters (JSON)", json.dumps(self.parameters, sort_keys=True)),
        ]
        if self.correction_reason:
            rows.insert(3, ("Correction reason", self.correction_reason))
        return rows

    def _code_description(self) -> str:
        from .registry import describe_code_version

        return describe_code_version(self.code_version)

    def as_text(self) -> str:
        return "\n".join(f"{k}: {v}" for k, v in self.rows())


def _code_version() -> str:
    from .registry import _code_version as registry_code_version

    return registry_code_version()


def suppression_rules() -> dict[str, Any]:
    """The disclosure-control rules this deployment applies to every
    published cell, stated on every export whether or not any cell in it
    was actually suppressed."""
    return {
        "min_count": get_settings().suppression_min_count,
        "method": "primary suppression of cells below min_count, plus secondary suppression "
                  "of the smallest remaining cell where a group would otherwise publish exactly "
                  "one suppressed cell against its total",
        "count_basis": "matched item quotes behind each period's comparison",
    }


def build_stamp(
    res: Mapping[str, Any],
    label: str,
    *,
    run: Any | None = None,
    data_vintage: str | None = None,
    data_source: str | None = None,
    data_received_at: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> ProvenanceStamp:
    """Build the stamp for an export of `res` (a pipeline result).

    `run` is the registry row (`core.registry.IndexRunORM`) when the run is
    registered, which supplies the run id, vintage, approval and the
    input's content hash; without it the export is stamped "unregistered"
    and the data vintage is `data_vintage` (the content hash the caller
    knows) or computed from `res["clean"]` as a last resort, labelled as
    such.
    """
    from .. import __version__
    from ..engine.custom import is_non_standard, non_standard_expression
    from ..engine.index import resolve_index_reference_period
    from .registry import _environment_fingerprint

    config: RunConfig = res["config"]
    head: dict[str, Any] | None = None
    if "indices" in res:
        indices = res["indices"]
        series = "All items" if "All items" in indices.columns else str(indices.columns[0])
        head = {"series": series, "period": str(indices.index[-1].date()),
                "value": float(indices[series].iloc[-1]),
                "reference_period": str(
                    resolve_index_reference_period(config.index, indices.index).date())}

    if run is not None:
        vintage_hash = str(run.input_hash)
        source = data_source or run.data_source
        received = data_received_at or run.data_received_at
        code = str(run.code_version)
        fingerprint = str(run.environment_fingerprint)
    else:
        if data_vintage is None:
            from ..data.store import content_hash_of
            data_vintage = "computed-from-cleaned:" + content_hash_of(res["clean"])
        vintage_hash = data_vintage
        source, received = data_source, data_received_at
        code = _code_version()
        fingerprint = _environment_fingerprint()

    return ProvenanceStamp(
        run_id=str(run.run_id) if run is not None else "unregistered",
        registered=run is not None,
        label=label or config.label,
        data_vintage=vintage_hash,
        data_source=source,
        data_received_at=received,
        code_version=code,
        pricelab_version=__version__,
        environment_fingerprint=fingerprint,
        parameters=json.loads(config.to_json()),
        suppression_rules=suppression_rules(),
        non_standard_formula=is_non_standard(config.index),
        non_standard_expression=(non_standard_expression(config.index)
                                 if is_non_standard(config.index) else None),
        quality_adjustments=len(config.quality_adjustment.entries),
        headline=head,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        vintage=int(run.vintage) if run is not None else 1,
        supersedes_run_id=run.supersedes_run_id if run is not None else None,
        correction_reason=run.correction_reason if run is not None else None,
        approved=bool(run.approved) if run is not None else False,
        extra=dict(extra or {}),
    )
