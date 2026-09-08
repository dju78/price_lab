"""pricelab: a small, testable pipeline for consumer price collections.

Built around one idea: every number it produces can be traced back to a
recorded configuration and an inspectable set of flagged observations.
"""

from .config import RunConfig, Schema, QualityConfig, ImputationConfig, IndexConfig
from .ingest import read_price_data, standardise, validate, ValidationReport
from .quality import run_quality
from .impute import run_imputation
from .index import (build_index, build_all, year_on_year, jevons, dutot, carli, laspeyres,
                    years_span, annualised_rate)
from . import diagnostics

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

from .insights import build_narrative, Narrative, Finding
from .charts import build_all_charts, to_png
from .deck import build_deck
from .report import build_docx, build_markdown, method_note
from .auto import auto_configure, infer_schema, analyse
