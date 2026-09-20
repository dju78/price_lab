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
    year_on_year,
    years_span,
)
from .engine.quality import run_quality

__version__ = "0.1.0"


def run_pipeline(df, config: RunConfig = None):
    """Ingest to index in one call, returning every intermediate artefact.

    Nothing is discarded along the way: the panel that asks "how many did you
    change and which ones" gets an answer from the returned object, not from a
    re-run.
    """
    config = config or RunConfig()

    report = validate(df)
    if not report.passed:
        return {"validation": report, "config": config}

    clean, quality = run_quality(df, config.quality)
    imputed = run_imputation(clean, config.imputation)
    indices, matched = build_all(imputed, config.index)

    return {
        "validation": report,
        "clean": clean,
        "quality": quality,
        "imputed": imputed,
        "indices": indices,
        "matched_counts": matched,
        "inflation": year_on_year(indices),
        "config": config,
    }

from .engine.auto import analyse, auto_configure, infer_schema
from .engine.insights import Finding, Narrative, build_narrative
from .reporting.charts import build_all_charts, to_png
from .reporting.deck import build_deck
from .reporting.report import build_docx, build_markdown, method_note
