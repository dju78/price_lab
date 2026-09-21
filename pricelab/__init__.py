"""pricelab: a small, testable pipeline for consumer price collections.

Built around one idea: every number it produces can be traced back to a
recorded configuration and an inspectable set of flagged observations.
"""

from .core.config import ImputationConfig, IndexConfig, QualityConfig, RunConfig, Schema
from .data.upload import ValidationReport, read_price_data, standardise, validate
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

    report = validate(df)
    if not report.passed:
        return {"validation": report, "config": config}

    clean, quality = run_quality(df, config.quality)
    # Approved replacements are linked onto the old item's series here,
    # after fault repair and before imputation, so the imputation and the
    # matched-model index see one continuing item where the collection
    # saw two. `link_log` lists every row that was moved or dropped to do
    # it, and the linked rows are flagged in `clean` and everything
    # downstream of it.
    clean, link_log = apply_adjustments(clean, config.quality_adjustment.entries)
    imputed = run_imputation(clean, config.imputation)
    indices, matched = build_all(imputed, config.index)

    result = {
        "validation": report,
        "clean": clean,
        "quality": quality,
        "link_log": link_log,
        "imputed": imputed,
        "indices": indices,
        "matched_counts": matched,
        "inflation": year_on_year(indices),
        "config": config,
    }
    if compute_impact and config.quality_adjustment.entries:
        result["quality_adjustment_impact"] = impact_report(
            df, config, run=lambda d, c: run_pipeline(d, c, compute_impact=False))
    return result

from .engine.auto import analyse, auto_configure, infer_schema
from .engine.insights import Finding, Narrative, build_narrative
from .reporting.charts import build_all_charts, to_png
from .reporting.deck import build_deck
from .reporting.report import build_docx, build_markdown, method_note
