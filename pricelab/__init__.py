"""pricelab: a small, testable pipeline for consumer price collections.

Built around one idea: every number it produces can be traced back to a
recorded configuration and an inspectable set of flagged observations.
"""

import time

from .core.config import ImputationConfig, IndexConfig, QualityConfig, RunConfig, Schema
from .core.logging import get_logger, run_context
from .core.models import (
    IMPUTATION_COLUMNS,
    QUALITY_FLAG_COLUMNS,
    ImputationRecord,
    QualityFlag,
    validate_frame,
)
from .data.classification import parent_map_for
from .data.upload import ValidationReport, read_price_data, standardise, validate
from .data.validation import expenditure_inconsistencies
from .engine import diagnostics
from .engine.imputation import run_imputation
from .engine.index import (
    annualised_rate,
    build_all,
    build_index,
    carli,
    dutot,
    jevons,
    laspeyres,
    resolve_index_reference_period,
    year_on_year,
    years_span,
)
from .engine.quality import run_quality
from .engine.quality_adjustment import apply_adjustments, impact_report

__version__ = "0.1.0"
log = get_logger("pricelab.pipeline")


def _assert_contract(frame, columns, model, producer: str) -> None:
    """The stage's output against its declared column contract
    (core.models): an engine stage that stopped producing a column, or
    produced it with the wrong type, fails here with the stage named,
    rather than as a KeyError three stages later."""
    problems = validate_frame(frame, columns, model)
    if problems:
        raise RuntimeError(f"{producer} broke its output contract: " + "; ".join(problems))


def run_pipeline(df, config: RunConfig = None, *, compute_impact: bool = True):
    """Ingest to index in one call, returning every intermediate artefact.

    Nothing is discarded along the way: the panel that asks "how many did you
    change and which ones" gets an answer from the returned object, not from a
    re-run.

    When the configuration carries approved quality adjustments, the result
    also carries their impact report (`quality_adjustment_impact`): the
    pipeline re-run without the adjustments' sizes and without the links,
    and the headline difference attributed. `compute_impact=False` is how
    those inner re-runs avoid computing an impact report of their own.
    """
    config = config or RunConfig()
    with run_context() as correlation_id:
        started = time.monotonic()
        log.info("pipeline.start", extra={"rows": int(len(df)), "label": config.label,
                                          "formula": config.index.formula,
                                          "quality_adjustments": len(config.quality_adjustment.entries)})
        report = validate(df)
        if not report.passed:
            log.warning("pipeline.validation_failed",
                        extra={"errors": len(getattr(report, "errors", []) or [])})
            return {"validation": report, "config": config, "correlation_id": correlation_id}

        clean, quality = run_quality(df, config.quality)
        _assert_contract(clean, QUALITY_FLAG_COLUMNS, QualityFlag, "engine.quality.run_quality")
        log.info("pipeline.quality", extra={"scale_errors_detected": quality["scale_errors_detected"],
                                            "scale_errors_repaired": quality["scale_errors_repaired"]})
        # Approved replacements are linked onto the old item's series here,
        # after fault repair and before imputation, so the imputation and the
        # matched-model index see one continuing item where the collection
        # saw two. `link_log` lists every row that was moved or dropped to do
        # it, and the linked rows are flagged in `clean` and everything
        # downstream of it.
        clean, link_log = apply_adjustments(clean, config.quality_adjustment.entries)
        imputed = run_imputation(clean, config.imputation)
        _assert_contract(imputed, IMPUTATION_COLUMNS, ImputationRecord, "engine.imputation.run_imputation")
        log.info("pipeline.imputation", extra={"imputed_values": int((imputed["imputation"] != "").sum())})
        # A weighted collection whose categories are classification codes
        # rolls up through the tree; the parent map comes from the
        # classification table, or is None (flat weighted aggregate) when the
        # categories are labels or no database is reachable.
        parent_of = (parent_map_for(imputed["category"].astype(str).unique())
                     if "weight" in imputed.columns and imputed["weight"].notna().any() else None)
        indices, matched = build_all(imputed, config.index, parent_of=parent_of)
        log.info("pipeline.index", extra={"periods": int(len(indices)), "series": int(len(indices.columns)),
                                          "elapsed_ms": round((time.monotonic() - started) * 1000)})

        result = {
            "validation": report,
            # Rows where expenditure and price x quantity disagree: reported
            # with the run, never resolved by preferring one of them.
            "expenditure_check": expenditure_inconsistencies(
                df, config.quality.expenditure_tolerance),
            "clean": clean,
            "quality": quality,
            "link_log": link_log,
            "imputed": imputed,
            "indices": indices,
            "matched_counts": matched,
            "inflation": year_on_year(indices),
            "config": config,
            "correlation_id": correlation_id,
        }
        if compute_impact and config.quality_adjustment.entries:
            result["quality_adjustment_impact"] = impact_report(
                df, config, run=lambda d, c: run_pipeline(d, c, compute_impact=False))
            log.info("pipeline.impact_report", extra={
                "adjustment_effect_points": result["quality_adjustment_impact"].adjustment_effect_points})
        log.info("pipeline.done", extra={"elapsed_ms": round((time.monotonic() - started) * 1000)})
        return result

from .engine.auto import analyse, auto_configure, infer_schema
from .engine.insights import Finding, Narrative, build_narrative
from .reporting.charts import build_all_charts, to_png
from .reporting.deck import build_deck
from .reporting.report import build_docx, build_markdown, method_note
