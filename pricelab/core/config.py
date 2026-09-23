"""Configuration. Every result the library produces is reproducible from one
of these objects, which is the point: a chart you cannot regenerate from a
recorded configuration is not an auditable statistic.

Migrated from dataclasses to pydantic v2 in Phase 1 so a saved configuration
is validated on load, not just on construction: a corrupted or hand-edited
config JSON now fails loudly with a field-level error instead of silently
producing a RunConfig with the wrong type in one slot. `to_json()` and
`from_dict()` keep their exact signatures and JSON shape (including the field
name `schema`, which the code and every saved config file already use) so
every existing caller and every JSON file this tool has ever written keeps
working unchanged.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: The current config schema version. Bump this, and add a branch to
#: `_migrate`, whenever a field is renamed or its meaning changes in a way
#: that an old saved config JSON would otherwise misinterpret. A JSON file
#: with no "schema_version" key at all predates this field entirely and is
#: treated as version "1".
#:
#: Version "2" (this one) splits the single `base_period` into three
#: distinct concepts a price index actually has: `price_reference_period`
#: (the denominator of every price relative), `weight_reference_period`
#: (where the weights or quantities come from -- deliberately earlier than
#: the price reference for a Lowe or Young index), and
#: `index_reference_period` (the presentational choice of which period the
#: published series reads 100 at). `base_period` remains as a deprecated
#: alias; see `_migrate`.
CURRENT_SCHEMA_VERSION = "2"


class Schema(BaseModel):
    """Column mapping. Lets the library accept any collection with the same
    logical shape, not just this one workbook."""

    date: str = "Date"
    """Name of the column holding the observation period."""
    item_id: str = "Item_ID"
    """Name of the column holding the stable item identifier."""
    item_name: str = "Item_Name"
    """Name of the column holding the human-readable item label."""
    category: str = "Category"
    """Name of the column holding the classification group."""
    price: str = "Reported_Price"
    """Name of the column holding the observed price, in the collection's currency."""
    weight: str | None = None
    """Name of the column holding an expenditure weight, if one was supplied."""
    quantity: str | None = None
    """Name of the column holding the quantity transacted (scanner and
    transaction data), if one was supplied. What the quantity-basket and
    superlative formulae need."""
    expenditure: str | None = None
    """Name of the column holding expenditure (price x quantity), if one was
    supplied. With both present the two are checked against each other;
    with expenditure alone, quantity is derived as expenditure / price and
    flagged as derived."""
    unit: str | None = None
    """Name of the column holding the unit of measurement the quantity is in
    ("kg", "litre", "pack"), if one was supplied; carried for the unit value
    index's homogeneity judgement."""


class QualityConfig(BaseModel):
    missing_codes: tuple[float, ...] = (0,)
    """Values that mean "no price was collected", not "the price is nil"."""
    reference_window: int = 13
    """Width, in periods, of the centred rolling median used as an item's local price level."""
    min_reference_periods: int = 3
    """Minimum periods required inside the window before a local reference is trusted."""
    scale_log10_low: float = 1.5
    """Lower bound, in log10 units, of the band that flags a unit-of-measurement fault."""
    scale_log10_high: float = 2.5
    """Upper bound, in log10 units, of the band that flags a unit-of-measurement fault."""
    repair_scale_errors: bool = True
    """Rescale a flagged fault back onto its item's level rather than deleting it."""
    residual_tolerance: float = 0.5
    """Post-repair check, in log10 units: evidence the repair rule was correctly specified."""
    expenditure_tolerance: float = 0.01
    """Relative tolerance for `expenditure == price x quantity` when a
    collection carries both; a row outside it is reported, never resolved
    by silently preferring one of the two."""


class IndexConfig(BaseModel):
    formula: str = "jevons"
    """Elementary or aggregate formula: jevons | dutot | carli | laspeyres,
    or "custom" to evaluate `custom_formula` instead."""

    custom_formula: str | None = None
    """A restricted arithmetic expression combining the standard formulae,
    for a compiler who needs one this tool does not ship. Evaluated by
    `core.security.evaluate_formula` -- a whitelist AST walker, never
    `eval` -- over the named building blocks listed in
    `engine.custom.ELEMENTARY_VARIABLES`.

    Recorded here rather than anywhere else precisely so it is covered by
    everything that already covers a parameter: the registry's content
    hash, the cache key, the audit trail and the config JSON of every
    saved run. A run compiled with one is marked non-standard in every
    export (`engine.custom.is_non_standard`), because a reader cannot
    otherwise tell that the number in front of them came from a formula
    the analyst wrote rather than one the profession recognises."""
    custom_aggregate_formula: str | None = None
    """An analyst-defined expression for the all-items aggregate over the
    category index levels of the same period (names are the sanitised
    category labels, plus `all_items` for the standard aggregate), e.g.
    `(bread + milk) / 2` or `all_items - 0.1 * energy`. Evaluated by the
    same whitelist walker as `custom_formula`; a run using one is marked
    non-standard everywhere, exactly as an elementary custom formula is.
    Independent of `formula`: a Jevons run may still define its aggregate."""
    chained: bool = True
    """Chain period-on-period links rather than compare every period to one fixed base."""
    chain_drift_threshold_pp: float = 1.0
    """Index points of chained-versus-direct gap above which the chain
    drift diagnostic flags a series (`engine.splicing.chain_drift`)."""

    base_period: str | None = None
    """Deprecated alias carried for backward compatibility only: a config
    saved before schema_version 2 used this one field for what are, in a
    real index, three distinct periods. Read `price_reference_period` and
    `index_reference_period` instead; a legacy config loaded through
    `RunConfig.from_dict` has both populated from this field automatically
    (see `_migrate`). New code should not set this field."""

    price_reference_period: str | None = None
    """ISO date of the period whose prices are the denominator of every
    price relative. Used directly by `engine.index.build_index` for a
    fixed-base (non-chained) comparison; defaults to the first period
    present when unset. Has no effect on a chained index, where each link
    compares only to the immediately preceding period."""

    weight_reference_period: str | None = None
    """ISO date of the period the expenditure weights or quantities are
    drawn from. Deliberately earlier than `price_reference_period` for a
    Lowe or Young index. Carried and displayed only in this phase: no
    formula implemented yet reads it (Lowe and Young are Phase 3)."""

    index_reference_period: str | None = None
    """ISO date of the period the published series is rebased to read
    `base_value`. A presentational choice, changeable by rebasing without
    recomputing anything: applied by `engine.index.build_index` as a final
    rescaling step, identically whether the series was built chained or
    fixed-base, so it need not equal `price_reference_period`; defaults to
    the first period present when unset."""

    base_value: float = 100.0
    """Index level assigned to the index reference period."""
    min_matched_items: int = 2
    """Below this many matched items, the index holds its level and flags insufficiency."""
    homogeneity_justification: str | None = None
    """Required by `formula = "unit_value"`: the compiler's stated grounds
    for treating each category's items as one homogeneous product whose
    quantities are additive (CPI Manual 2020, para. 8.87). Recorded so a
    reviewer can judge the assertion; a unit value index is never computed
    without it."""

    @model_validator(mode="after")
    def _custom_formula_is_coherent_and_parses(self) -> IndexConfig:
        """A custom formula must be declared as one, and must parse.

        Both halves are rejected at config time rather than at run time.
        A `custom_formula` set while `formula` still names a standard one
        is ambiguous about which the caller meant, and a typo in the
        expression should surface when it is written -- not part-way
        through a compile, after the quality and imputation stages have
        already run.
        """
        if self.formula == "unit_value" and not (self.homogeneity_justification or "").strip():
            raise ValueError(
                'formula is "unit_value" but homogeneity_justification is empty: a unit value '
                "index is only defined over strictly homogeneous items, and the assertion that "
                "they are is recorded with the run (engine.elementary.unit_value)")
        if self.formula == "custom" and not (self.custom_formula or "").strip():
            raise ValueError(
                'formula is "custom" but custom_formula is empty: there is no expression '
                "to evaluate.")
        if self.custom_formula and self.formula != "custom":
            raise ValueError(
                f'custom_formula is set but formula is "{self.formula}", so the custom '
                'expression would be silently ignored. Set formula="custom" to use it, or '
                "clear custom_formula.")
        if self.custom_aggregate_formula is not None and not self.custom_aggregate_formula.strip():
            raise ValueError("custom_aggregate_formula is set but empty; clear it or write an expression")
        if self.custom_aggregate_formula:
            from .security import FormulaError, validate_formula_syntax

            try:
                # Names are the run's own category labels, unknown until the
                # data is loaded; the interface checks them against the
                # actual columns. Syntax and construct whitelist are checked
                # here so a saved config with a rejected construct never loads.
                validate_formula_syntax(self.custom_aggregate_formula)
            except FormulaError as exc:
                raise ValueError(
                    f"custom_aggregate_formula is not a permitted expression: {exc}") from exc
        if self.custom_formula:
            # Imported inside the validator, not at module scope: the
            # variable names a custom formula may use are derived from the
            # elementary formulae the engine actually implements, and
            # engine imports this module. By validation time both modules
            # are fully loaded, so the cycle never materialises -- and
            # deriving the names rather than restating them here means a
            # formula added to the engine cannot become a name this
            # validator still rejects.
            from ..engine.custom import ELEMENTARY_VARIABLES
            from .security import FormulaError, validate_formula_syntax
            try:
                validate_formula_syntax(self.custom_formula, ELEMENTARY_VARIABLES)
            except FormulaError as exc:
                raise ValueError(f"custom_formula is not a permitted expression: {exc}") from exc
        return self

    @model_validator(mode="after")
    def _weight_reference_not_after_price_reference(self) -> IndexConfig:
        """A Lowe or Young index (Phase 3) draws its weights or quantities
        from `weight_reference_period` and compares prices at
        `price_reference_period`; a weight reference after the price
        reference would mean weighting the comparison by a basket that, at
        the price reference period, did not exist yet to observe. Compared
        as plain ISO date strings (no datetime parsing dependency here):
        valid because zero-padded YYYY-MM-DD sorts identically whether
        compared lexicographically or chronologically. `base_period` is
        read as the fallback price reference, matching how
        `engine.index.build_index` itself resolves it.
        """
        price_ref = self.price_reference_period or self.base_period
        if self.weight_reference_period and price_ref and self.weight_reference_period > price_ref:
            raise ValueError(
                f"weight_reference_period ({self.weight_reference_period!r}) is after "
                f"price_reference_period ({price_ref!r}). The weights or quantities a "
                "fixed-basket index draws on must come from no later than the period "
                "prices are compared against.")
        return self


class ImputationConfig(BaseModel):
    default_method: str = "none"
    """Imputation method applied to a category with no explicit override:
    none | carry_forward | class_mean | seasonal_hold."""
    by_category: dict[str, str] = Field(default_factory=dict)
    """Per-category override of the imputation method."""

    def method_for(self, category: str) -> str:
        return self.by_category.get(category, self.default_method)


class QualityAdjustmentEntry(BaseModel):
    """One approved replacement link: old item, new item, the method that
    valued the quality difference between them, and the value it arrived
    at. The unit of the quality adjustment ledger.

    Lives in the configuration rather than beside it for the same reason
    `custom_formula` does: everything that already covers a parameter --
    the registry's content hash, the cache key, the config JSON of every
    saved run, `reproduce()` -- then covers this without a second
    mechanism. A published index that depended on a ledger stored somewhere
    the hash could not see would be reproducible in every respect except
    the one the manual says is the most scrutinised.
    """

    old_item: str
    new_item: str
    category: str
    period: str
    """ISO date of the first period the replacement's price is used for the
    old item's series."""
    method: str
    """The reason code of the method that produced `quality_ratio`; see
    `engine.quality_adjustment.ReasonCode`."""
    quality_ratio: float = Field(gt=0)
    """Value of the new item relative to the old, in price terms: 1.0 means
    directly comparable, 1.2 means the new item is worth 20 percent more.
    The new item's prices are divided by this to express them in old-item
    quality terms."""
    parameters: dict[str, Any] = Field(default_factory=dict)
    """Whatever the method needed to reach the ratio: the overlap period and
    prices, the quantities, the option value, the peer items."""
    justification: str = ""
    approved_by: str = ""
    approved_at: str = ""


class QualityAdjustmentConfig(BaseModel):
    """The quality adjustment ledger as applied to this run."""

    entries: list[QualityAdjustmentEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_replacement_per_item(self) -> QualityAdjustmentConfig:
        """An old item can be replaced once, by one new item, and a new item
        can continue one old item. Two entries claiming the same new item
        would write two different histories for the same prices."""
        seen_new: dict[str, str] = {}
        seen_old: dict[str, str] = {}
        for e in self.entries:
            if e.new_item in seen_new:
                raise ValueError(
                    f"new item {e.new_item!r} is entered as the replacement for both "
                    f"{seen_new[e.new_item]!r} and {e.old_item!r}; one replacement continues "
                    "one series")
            if e.old_item in seen_old:
                raise ValueError(
                    f"old item {e.old_item!r} is replaced by both {seen_old[e.old_item]!r} and "
                    f"{e.new_item!r}; an item can be replaced once")
            seen_new[e.new_item] = e.old_item
            seen_old[e.old_item] = e.new_item
        return self


class SeasonalConfig(BaseModel):
    """Strictly seasonal item treatment and seasonal adjustment.

    Off by default, and deliberately so. Seasonal adjustment is the easiest
    place in this platform to manufacture a smooth line that has removed a
    real movement, and it is not something a run should acquire because
    nobody turned it off.
    """

    enabled: bool = False
    """Compute the seasonal treatments and the adjusted series with the run."""

    treatment: str = "both"
    """Strictly seasonal item treatment: "class_confinement", "weight_update",
    or "both". "both" is the default because the two give different answers
    on the same data and the difference is the point -- a run that quietly
    picked one has made a judgement the reader cannot see."""

    periods_per_year: int = 12
    """Periods in a seasonal cycle: 12 for monthly, 4 for quarterly."""

    min_years: int = 2
    """Years of data an item needs before its absences can be called a
    season rather than a gap. Below two years there is no repetition to
    observe, and every interrupted item would be classed as seasonal."""

    max_in_season_share: float = 0.9
    """An item in season in more than this share of the calendar periods is
    not strictly seasonal, it is an item with gaps. The two need different
    treatment and conflating them applies a seasonal rule to a collection
    failure."""

    adjust: bool = True
    """Compute a seasonally adjusted series alongside the treatments."""

    adjustment_engine: str = "auto"
    """"x13" demands X-13ARIMA-SEATS and fails if the binary is absent;
    "stl" always uses STL; "auto" prefers X-13 and falls back to STL,
    recording which actually ran. Whatever runs is named in every output --
    an STL result presented as a seasonal adjustment without qualification
    is a misrepresentation, so the engine that ran travels with the series
    rather than being looked up later."""

    adjustment_series: str = "All items"
    """Which column of the compiled index to adjust. The headline by
    default; a category is adjusted by naming it."""

    stl_seasonal: int = 7
    """STL's seasonal smoother length, in periods. Must be an odd number of
    at least 7 (statsmodels' own constraint)."""

    stability_sub_samples: int = 3
    """Sub-spans the stability test re-estimates the seasonal factors on.
    A factor that moves a great deal between sub-samples is not a seasonal
    pattern, it is a curve fitted to noise."""

    stability_threshold_pct: float = 2.0
    """Percentage points of factor range, across sub-samples, above which
    the adjustment is reported as unstable."""

    rothwell: bool = False
    """Also compute the Rothwell index for the strictly seasonal items."""

    counter_seasonal: bool = False
    """Also estimate off-season prices counter-seasonally, and report the
    index that results, so the cost of the estimate is visible beside the
    index that made none."""

    @model_validator(mode="after")
    def _choices_are_known(self) -> SeasonalConfig:
        if self.treatment not in ("class_confinement", "weight_update", "both"):
            raise ValueError(
                'treatment must be "class_confinement", "weight_update" or "both", not '
                f"{self.treatment!r}")
        if self.adjustment_engine not in ("auto", "x13", "stl"):
            raise ValueError(
                f'adjustment_engine must be "auto", "x13" or "stl", not '
                f"{self.adjustment_engine!r}")
        if self.periods_per_year < 2:
            raise ValueError(
                f"a seasonal cycle of {self.periods_per_year} periods has no structure to "
                "estimate; monthly data has 12 and quarterly 4")
        if self.min_years < 2:
            raise ValueError(
                "at least two years are needed before an absence can be called a season "
                "rather than a gap")
        if self.stl_seasonal < 7 or self.stl_seasonal % 2 == 0:
            raise ValueError(
                f"stl_seasonal must be an odd number of at least 7, not {self.stl_seasonal}")
        if self.stability_sub_samples < 2:
            raise ValueError("a stability test needs at least two sub-samples to compare")
        return self


class OutlierDecision(BaseModel):
    """One analyst's decision on one flagged quote.

    A decision, never a deletion: `decision` says what the analyst concluded
    and `reason` says why, and both travel with the run. The engine applies
    "reject" by excluding the quote from the index and reporting the
    exclusion; it never removes a row from the data.
    """

    period: str
    item_id: str
    category: str = ""
    method: str = ""
    """Which detector flagged it, carried so a reviewer can see whether the
    quote was caught by one screen or by all four."""
    statistic: float = 0.0
    """The detector's own figure for this quote -- the ratio, the score, the
    distance beyond the fence -- in the detector's own units."""
    decision: str = "accept"
    """"accept" (a genuine price, keep it), "reject" (an error, exclude it
    from the index) or "annotate" (keep it, but on the record that it was
    looked at and why)."""
    reason: str
    analyst: str = ""
    decided_at: str = ""

    @model_validator(mode="after")
    def _decision_is_known_and_reasoned(self) -> OutlierDecision:
        if self.decision not in ("accept", "reject", "annotate"):
            raise ValueError(
                f'decision must be "accept", "reject" or "annotate", not {self.decision!r}')
        if not (self.reason or "").strip():
            raise ValueError(
                "every outlier decision must state a reason: a quote excluded from a published "
                "index without one is a deletion nobody can review, which is the single thing "
                "this queue exists to prevent")
        return self


class OutlierConfig(BaseModel):
    """Outlier screening, and the analyst decisions taken on what it found."""

    enabled: bool = False
    """Run the screens with the pipeline. Off by default: the scale-error
    repair in `engine.quality` already runs unconditionally, and these
    screens exist to be reviewed, not to run unattended."""

    methods: tuple[str, ...] = ("tukey", "quartile", "hidiroglou_berthelot", "ratio")
    """Which screens to run. All four by default, because they disagree --
    a quote caught by one and not the others is a different kind of
    evidence from one caught by all four, and the queue shows which."""

    tukey_k: float = 1.5
    """Multiples of the interquartile range beyond the quartiles. 1.5 is
    Tukey's own; 3.0 is the conventional "far out" fence."""

    quartile_ratio: float = 2.5
    """Quartile method: multiples of the median-to-quartile distance,
    applied to log price relatives."""

    hb_c: float = 4.0
    """Hidiroglou-Berthelot: multiples of the interquartile spread of the
    effect statistic. Statistics Canada's usual range is 4 to 8."""

    hb_u: float = 0.5
    """Hidiroglou-Berthelot's importance exponent, between 0 and 1. At 0 the
    method reduces to a screen on the ratio alone; at 1 it scales fully with
    the magnitude of the price, so a large price must move further to be
    flagged."""

    ratio_low: float = 0.5
    ratio_high: float = 2.0
    """Period-on-period ratio screen: a price relative outside this band is
    flagged. Deliberately wide, because this screen is the crude one and is
    there to catch what the distributional screens miss on a thin cell."""

    min_change_pct: float = 5.0
    """A deadband: a quote whose price relative sits within this percentage
    of its cell's median relative is never flagged, whatever the fences say.

    Without one, a cell whose relatives are tightly clustered has fences a
    fraction of a percent wide and every ordinary rounding of a price
    becomes a queue entry -- on the bundled collection, screening with no
    deadband flags 23 percent of all price relatives, which is not a review
    queue, it is the data. Every statistical office applies a tolerance of
    this kind for the same reason. It is stated here as a parameter rather
    than buried in the screens because it decides how much work the queue
    creates, and that is an operational judgement, not a statistical one."""

    min_cell_size: int = 5
    """Below this many price relatives, a cell's quartiles are not a
    distribution and the distributional screens are not run on it. The ratio
    screen still is, which is why it is there."""

    entries: list[OutlierDecision] = Field(default_factory=list)
    """The review queue's decisions, as applied to this run. Empty means
    nothing has been reviewed, and an unreviewed flag excludes nothing."""

    @model_validator(mode="after")
    def _methods_are_known_and_bounds_ordered(self) -> OutlierConfig:
        known = ("tukey", "quartile", "hidiroglou_berthelot", "ratio")
        unknown = [m for m in self.methods if m not in known]
        if unknown:
            raise ValueError(
                f"unknown outlier screen(s) {unknown}; available: {', '.join(known)}")
        if not 0.0 <= self.hb_u <= 1.0:
            raise ValueError(f"hb_u is an exponent between 0 and 1, not {self.hb_u}")
        if not 0 < self.ratio_low < self.ratio_high:
            raise ValueError(
                f"the ratio screen's band must satisfy 0 < low < high, not "
                f"{self.ratio_low} to {self.ratio_high}")
        seen: set[tuple[str, str]] = set()
        for entry in self.entries:
            key = (entry.period, entry.item_id)
            if key in seen:
                raise ValueError(
                    f"two decisions recorded for {entry.item_id!r} at {entry.period}; one quote "
                    "has one decision, or the run applies whichever happened to be last")
            seen.add(key)
        return self


class RevisionConfig(BaseModel):
    """How a revision analysis over the registry's vintages is computed."""

    enabled: bool = False
    max_vintages: int = 12
    """Vintages back from the current one to reproduce. Each reproduction
    re-executes a whole pipeline, so this is a cost, not a preference."""
    bias_alpha: float = 0.05
    """Significance level for the bias test on the revision series."""

    @model_validator(mode="after")
    def _bounds(self) -> RevisionConfig:
        if self.max_vintages < 2:
            raise ValueError("a revision analysis needs at least two vintages to compare")
        if not 0 < self.bias_alpha < 1:
            raise ValueError(f"bias_alpha is a significance level in (0, 1), not {self.bias_alpha}")
        return self


class MultilateralConfig(BaseModel):
    """Method, window and extension rule for a multilateral index.

    Separate from `IndexConfig` rather than folded into it because a
    multilateral index is not a choice of formula, it is a different object:
    it has no price reference period, it is computed over a window rather
    than period by period, and its published series depends on an extension
    rule that a bilateral index has no equivalent of. Conflating the two
    settings would make `IndexConfig.formula` mean two incompatible things
    depending on a flag elsewhere, which is exactly the conflation the
    reference-period split was made to undo.

    Carried on `RunConfig` so a multilateral compilation is covered by
    everything that already covers a run: the registry's content hash, the
    cache key, the audit trail and the config JSON of every saved run. Two
    runs that differ only in their splice are different runs, and the hash
    must say so.
    """

    enabled: bool = False
    """Whether this run computes a multilateral index alongside the
    bilateral one. Off by default: most collections are price quotes with
    no quantities, where every method here is unavailable, and a setting
    that silently does nothing is worse than one that must be asked for."""

    method: str = "geks_fisher"
    """One of `engine.multilateral.METHODS`."""

    window: int = 25
    """Periods in the estimation window. `engine.multilateral.DEFAULT_WINDOW`
    is the same number and the reason for it; repeated as a literal here
    because a pydantic default must not import the engine."""

    splice: str = "mean"
    """One of `engine.multilateral.SPLICES`. Mean splice rather than the
    movement splice most often described first, because it is the rule least
    exposed to a single unrepresentative month and this default is applied
    to data nobody has looked at yet."""

    anchor_month: int = 12
    """Calendar month that restarts the expanding window under FBEW and
    FBMW. Ignored by the other four rules."""

    min_matched_items: int = 2
    """Below this many products in common, a bilateral comparison inside
    GEKS is discarded rather than computed on one item."""

    @model_validator(mode="after")
    def _method_and_splice_are_known(self) -> MultilateralConfig:
        """Checked here rather than at compile time, so a typo in a saved
        config surfaces when the config is loaded rather than part-way
        through a run. The names are duplicated from
        `engine.multilateral` deliberately: core must not import engine.
        `tests/test_multilateral.py` asserts the two lists stay equal."""
        methods = ("geks_fisher", "geks_tornqvist", "tpd", "wtpd", "tdh", "geary_khamis")
        splices = ("movement", "window", "half", "mean", "fbew", "fbmw")
        if self.method not in methods:
            raise ValueError(
                f"unknown multilateral method {self.method!r}; available: {', '.join(methods)}")
        if self.splice not in splices:
            raise ValueError(
                f"unknown splice {self.splice!r}; available: {', '.join(splices)}")
        if self.window < 2:
            raise ValueError(
                f"a multilateral window of {self.window} periods has nothing to compare; it "
                "needs at least two")
        if not 1 <= self.anchor_month <= 12:
            raise ValueError(f"anchor_month must be a calendar month 1-12, not {self.anchor_month}")
        return self


class RunConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_version: str = CURRENT_SCHEMA_VERSION
    """Version of this configuration's shape, not of the data it describes.
    Present so a saved run can be re-interpreted correctly even after this
    schema changes; see `_migrate`."""
    schema_: Schema = Field(default_factory=Schema, alias="schema")
    """Column mapping. The Python attribute is `schema_`: `schema` alone
    would shadow pydantic's own (deprecated) `BaseModel.schema()`. Aliased to
    the bare name "schema" in JSON, matching every config file this tool has
    written before Phase 1, and constructible either way (`RunConfig(schema=...)`
    or `RunConfig(schema_=...)`) since no caller in this codebase reads the
    attribute back by name today."""
    quality: QualityConfig = Field(default_factory=QualityConfig)
    imputation: ImputationConfig = Field(default_factory=ImputationConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    quality_adjustment: QualityAdjustmentConfig = Field(default_factory=QualityAdjustmentConfig)
    """Approved replacement links (Phase 4). Empty for every run compiled
    before this field existed, and an empty ledger applies nothing, so a
    schema_version 2 config without the key loads unchanged: no version
    bump, because no old field was renamed or reinterpreted."""
    multilateral: MultilateralConfig = Field(default_factory=MultilateralConfig)
    """Multilateral method, window and extension rule (Phase 5). Disabled by
    default, and a disabled section changes nothing, so a schema_version 2
    config saved without the key loads and compiles exactly as before: no
    version bump, because no old field was renamed or reinterpreted -- the
    same reasoning as `quality_adjustment` above."""

    seasonal: SeasonalConfig = Field(default_factory=SeasonalConfig)
    """Strictly seasonal item treatment and seasonal adjustment (Phase 6).
    Disabled by default; a disabled section changes nothing, so a config
    saved before it existed loads and compiles exactly as before."""

    outlier: OutlierConfig = Field(default_factory=OutlierConfig)
    """Outlier screens and the review queue's decisions (Phase 6). Disabled
    by default, and an empty queue excludes nothing even when enabled."""

    revision: RevisionConfig = Field(default_factory=RevisionConfig)
    """Revision analysis over the registry's vintages (Phase 6). Reads the
    registry; changes nothing about how this run is compiled."""

    label: str = "unnamed run"

    legacy_upconverted: bool = Field(default=False, exclude=True)
    """True if this instance was built by `from_dict` from a config saved
    before schema_version 2, whose single `base_period` was just split into
    three fields (see `_migrate`). A fact about *how this instance was
    loaded*, not a durable setting, so it is excluded from `to_json()`: a
    freshly re-saved copy of an upconverted config is not itself legacy.
    Callers with access to an audit session (`core.registry.reproduce` is
    the one that matters today) log this rather than upconverting silently.
    """

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.model_dump(by_alias=True, mode="json"), indent=indent)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunConfig:
        data, upconverted = _migrate(dict(d))
        cfg = cls.model_validate(data)
        cfg.legacy_upconverted = upconverted
        return cfg


def _migrate(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Upgrade a config dict of any past schema_version to the current shape.

    Returns the upgraded dict and whether an actual upconversion happened
    (as opposed to the dict already being current), so `from_dict` can flag
    the resulting `RunConfig` for its caller to log if it has somewhere to
    log it to.

    A dict with no "schema_version" key predates the field and is version
    "1". Versions "1" upgrade to "2" by populating the three reference
    period fields from the deprecated `base_period`, which is what every
    saved config meant before those fields existed: a fixed-base index used
    it as the price reference, a chained one used it to rebase, and nothing
    distinguished a weight reference because nothing needed one yet. When a
    future version is introduced, add a further branch here rather than
    changing what old JSON means: a run registered under an old config must
    go on meaning exactly what it meant when it was registered.
    """
    version = data.get("schema_version", "1")
    upconverted = False

    if version == "1":
        index_data = dict(data.get("index") or {})
        base = index_data.get("base_period")
        if base is not None:
            for field in ("price_reference_period", "weight_reference_period",
                          "index_reference_period"):
                index_data.setdefault(field, base)
        data["index"] = index_data
        version = "2"
        upconverted = True

    if version != CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"unknown config schema_version {version!r}; no migration path is registered for it")
    data["schema_version"] = CURRENT_SCHEMA_VERSION
    return data, upconverted


def _default_database_url() -> str:
    """An absolute path to `pricelab.db` at the repository root, rather than
    the bare relative `sqlite:///./pricelab.db` a first pass at this used.

    A relative path resolves against the process's current working
    directory, which is not the same thing as "where this application
    lives": `streamlit run app.py` from the repository root, from its
    parent directory, and from inside a Docker container's WORKDIR all give
    a different current working directory, and each would silently open or
    create a *different* SQLite file with the same relative name. That is
    exactly the kind of ambiguity a database path must not have. Anchoring
    to this file's own location makes the default consistent regardless of
    where the process was launched from; a deployment that wants the
    database somewhere else still overrides it with `PRICELAB_DATABASE_URL`.
    """
    repo_root = Path(__file__).resolve().parents[2]
    return f"sqlite:///{(repo_root / 'pricelab.db').as_posix()}"


class Settings(BaseSettings):
    """Environment-aware application settings, distinct from `RunConfig`.

    `RunConfig` describes how one analysis run should be computed and is part
    of that run's reproducibility record. `Settings` describes how this
    deployment of the application is operated, and has no business being
    stored alongside a run: two runs computed with an identical `RunConfig`
    must be identical regardless of the upload cap or session timeout the
    server happened to be configured with at the time.

    Sourced from environment variables prefixed `PRICELAB_` (for example
    `PRICELAB_DATABASE_URL`), with development-safe defaults so the
    application runs locally with no configuration at all. No secret is
    given a default here or committed to the repository.
    """

    model_config = SettingsConfigDict(env_prefix="PRICELAB_", env_file=".env", extra="ignore")

    environment: str = "development"
    """One of "development" or "production"; not security-enforcing on its
    own, but read by callers that should behave more cautiously in production
    (e.g. hiding stack traces)."""

    database_url: str = Field(default_factory=_default_database_url)
    """SQLAlchemy connection URL. A file-based SQLite URL by default so the
    application runs with no external service; changing this to a
    PostgreSQL URL is the entire migration to a production database, because
    every query in core/ is written through SQLAlchemy Core/ORM rather than
    against SQLite-specific SQL."""

    upload_max_mb: float = 50.0
    """Maximum accepted upload size, in megabytes, enforced before the
    file's bytes are read into memory."""

    cache_max_entries: int = 8
    """Maximum number of distinct analyses held in the in-process result
    cache at once; the oldest is evicted first once this is exceeded."""

    suppression_min_count: int = 3
    """Minimum number of price quotes (or outlets) a published cell may be
    built from before statistical disclosure control suppresses it."""

    session_timeout_minutes: int = 60
    """Idle minutes after which a login session is no longer accepted."""

    store_dir: str = Field(default_factory=lambda: str(Path(__file__).resolve().parents[2] / "store"))
    """Directory of the Parquet store: the immutable raw layer, the cleaned
    layer and the vintage sidecars (`data.store`). Anchored to the
    repository by default for the same reason the database is."""

    release_organisation: str = "PriceLab"
    """Named on the bulletin's title block and contact block."""
    release_contact_name: str = "Statistical enquiries"
    release_contact_email: str = "not configured"
    release_contact_phone: str = ""

    upload_rate_limit_per_minute: int = 10
    """Uploads one signed-in user may start per minute; the next is
    refused with the wait time stated, before any bytes are read."""

    db_pool_size: int = 5
    """Connections kept open in the pool (server databases only; SQLite
    uses its own single-file locking)."""
    db_max_overflow: int = 5
    db_pool_timeout_seconds: float = 30.0
    """How long a request waits for a pooled connection before failing
    rather than hanging."""
    db_statement_timeout_ms: int = 30_000
    """Per-statement timeout: PostgreSQL's `statement_timeout`; for SQLite,
    the busy timeout on a locked database and a progress-handler abort on
    a statement running past this long."""

    log_json: bool = True
    """Structured JSON log lines (one object per line) rather than text."""
    log_level: str = "INFO"


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings singleton, read once from the environment.

    Cached rather than re-read on every call because environment variables
    do not change within a running process; a test that needs different
    settings constructs `Settings()` directly (or calls
    `get_settings.cache_clear()` after mutating `os.environ`) rather than
    fighting this cache.
    """
    return Settings()
