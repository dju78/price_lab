"""Analyst-defined elementary and aggregate formulae.

A compiler sometimes needs a formula this tool does not ship -- a blend of
two elementary means, a core measure excluding one category, a
house-specific variant with a published definition. The options are to
ship every variant anyone might want, to refuse, or to let the formula be
written down. This module takes the third, through
`core.security.evaluate_formula`: a whitelist AST walker over numeric
literals, arithmetic operators, whitelisted names and four whitelisted
functions. It never calls `eval` or `exec`, and its default is deny -- a
construct with no branch in the walker is rejected rather than passed
through.

What a custom formula may reference is deliberately narrow. Rather than
exposing raw price vectors, which would need a language with loops and
therefore a much larger attack surface, the expression combines
*already-computed* named quantities: the standard elementary indices for
the same comparison, or the category index levels for the same period.
That covers the cases people actually ask for -- `(carli * harmonic_mean)
** 0.5`, `0.5 * jevons + 0.5 * dutot` -- while keeping the evaluator's
inputs to a handful of floats.

A run compiled with one of these is not a standard index and is marked as
such everywhere it is exported. That is not a disclaimer for its own sake:
a reader looking at a chart cannot tell that its underlying formula was
written by the analyst rather than drawn from the CPI Manual, and the
whole argument for this tool is that its numbers can be traced to a stated
method.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

from ..core.config import IndexConfig
from ..core.security import FormulaError, evaluate_formula, validate_formula_syntax
from .elementary import ELEMENTARY_FORMULAE, ElementaryResult

#: Names a custom *elementary* formula may reference: the standard
#: elementary indices computed over the same matched pair of periods, plus
#: the matched sample size.
ELEMENTARY_VARIABLES = (*sorted(ELEMENTARY_FORMULAE), "n_items")

#: Reserved name for the equally weighted aggregate in a custom
#: *aggregate* formula, alongside one sanitised name per category.
ALL_ITEMS = "all_items"


def _sanitise(name: str) -> str:
    """A category label turned into something the parser can treat as a
    name: "Food & drink" becomes "food_drink". Collisions are possible in
    principle and are reported by `aggregate_variables` rather than
    silently resolved, since a formula referencing an ambiguous name would
    otherwise pick whichever category happened to be encountered last."""
    cleaned = "".join(c if c.isalnum() else "_" for c in name.strip().lower())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return f"c_{cleaned}" if not cleaned or cleaned[0].isdigit() else cleaned


def aggregate_variables(columns: pd.Index) -> dict[str, str]:
    """Map each usable variable name to the column it refers to.

    Raises on a collision rather than resolving one: two categories whose
    labels sanitise to the same name make any formula referencing that
    name ambiguous, and an index whose definition is ambiguous is worse
    than one that failed to compile.
    """
    mapping: dict[str, str] = {}
    for column in columns:
        name = ALL_ITEMS if column == "All items" else _sanitise(str(column))
        if name in mapping:
            raise FormulaError(
                f"categories {mapping[name]!r} and {str(column)!r} both reduce to the "
                f"variable name {name!r}; rename one of them, because a formula using "
                f"{name!r} would not say which it meant")
        mapping[name] = str(column)
    return mapping


def validate_elementary_formula(expr: str) -> set[str]:
    """Check a custom elementary formula against the names it may use."""
    return validate_formula_syntax(expr, ELEMENTARY_VARIABLES)


def validate_aggregate_formula(expr: str, columns: pd.Index) -> set[str]:
    """Check a custom aggregate formula against the categories present."""
    return validate_formula_syntax(expr, aggregate_variables(columns))


def evaluate_elementary(
    expr: str, a: pd.Series, b: pd.Series, imputed: pd.Series | None = None
) -> ElementaryResult:
    """Evaluate a custom elementary formula over one matched comparison.

    Every standard elementary index is computed first and offered to the
    expression as a name, so the analyst composes from building blocks
    rather than from raw prices. The result carries `formula="custom"` and
    the expression itself in `parameters`, so the value never travels
    without the definition that produced it.
    """
    results = {name: fn(a, b, imputed) for name, fn in ELEMENTARY_FORMULAE.items()}
    any_result = next(iter(results.values()))
    variables: dict[str, float] = {name: r.value for name, r in results.items()}
    variables["n_items"] = float(any_result.n_items)

    parameters = {"expression": expr, "inputs": dict(variables)}
    if any_result.n_items == 0 or not all(np.isfinite(v) for v in variables.values()):
        return ElementaryResult(float("nan"), any_result.n_items, any_result.n_imputed,
                                "custom", parameters)

    value = evaluate_formula(expr, variables)
    return ElementaryResult(float(value), any_result.n_items, any_result.n_imputed,
                            "custom", parameters)


def evaluate_aggregate(expr: str, I: pd.DataFrame) -> pd.Series:
    """Evaluate a custom aggregate formula period by period over a frame
    of category indices.

    A period where any referenced series is missing yields NaN for that
    period rather than failing the whole run: a category that starts late
    is an ordinary fact about a collection, and refusing to produce any of
    the series because of it would be a worse answer than producing the
    part that is defined.
    """
    names = aggregate_variables(I.columns)
    referenced = validate_formula_syntax(expr, names)

    values = []
    for period in I.index:
        variables = {name: float(I.loc[period, column]) for name, column in names.items()}
        if any(not np.isfinite(variables[name]) for name in referenced):
            values.append(float("nan"))
            continue
        values.append(float(evaluate_formula(expr, variables)))
    return pd.Series(values, index=I.index, name="custom")


def is_non_standard(cfg: IndexConfig) -> bool:
    """True when this run's index came from an analyst-defined formula."""
    return cfg.formula == "custom" and bool((cfg.custom_formula or "").strip())


#: The line that goes on every export of a non-standard run. One sentence,
#: stating what was used, because a mark a reader has to interpret is not
#: much better than no mark.
NON_STANDARD_NOTICE = (
    "NON-STANDARD INDEX: this run used an analyst-defined formula ({expression}), not one "
    "of the standard index formulae. It is not comparable with a published index compiled "
    "on a standard formula.")


def non_standard_notice(cfg: IndexConfig) -> str:
    """The export banner for this config, or an empty string when the run
    used a standard formula."""
    if not is_non_standard(cfg):
        return ""
    return NON_STANDARD_NOTICE.format(expression=cfg.custom_formula)


def custom_formula_parameters(cfg: IndexConfig) -> Mapping[str, str]:
    """What the audit log and run registry should record about a custom
    formula, beyond the config JSON they already serialise wholesale."""
    if not is_non_standard(cfg):
        return {}
    return {"formula": "custom", "expression": cfg.custom_formula or ""}
