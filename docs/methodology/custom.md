# Analyst-defined formulae (`engine/custom.py`, `core/security.py`)

## What it does

A compiler can compile a run on an arithmetic expression over the standard
elementary indices computed for the same matched comparison — `jevons`,
`dutot`, `carli`, `harmonic_mean`, `cswd` — and the matched sample size
`n_items`, for example `(carli * harmonic_mean) ** 0.5` (the CSWD index) or
`0.5 * jevons + 0.5 * dutot`. Aggregate formulae may reference category
index levels by sanitised name, with `all_items` reserved for the aggregate.

## How it is evaluated

Through `core.security.evaluate_formula`: a whitelist walker over the
expression's syntax tree that accepts numeric literals, the arithmetic
operators, whitelisted names and four whitelisted functions, and rejects
everything else — attribute access, subscripts, calls outside the
whitelist, comprehensions, `import`. It never calls `eval` or `exec`. An
expression is rejected when it is written (on Ingest, and again by the
configuration model's validator), not part-way through a compile.

## Recording and marking

The expression lives in `IndexConfig.custom_formula`, so it is covered by
the registry's content hash, the cache key and every saved configuration
like any other parameter. A run using one is **non-standard** and is marked
as such on the deck's title slide, in the written report's banner, at the
head of every CSV export, in the SDMX message's dataset attributes
(`NON_STANDARD_FORMULA`), in the bulletin, and in every export's
provenance stamp (`non_standard_formula`, `non_standard_expression`).

## Citation and caveat

No standard defines these; that is the point of the mark. The method
sensitivity diagnostic (`engine/diagnostics.py`) adds a column for the
custom formula so its distance from every standard formula is a number,
not an impression.
