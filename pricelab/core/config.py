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
    chained: bool = True
    """Chain period-on-period links rather than compare every period to one fixed base."""

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
        if self.formula == "custom" and not (self.custom_formula or "").strip():
            raise ValueError(
                'formula is "custom" but custom_formula is empty: there is no expression '
                "to evaluate.")
        if self.custom_formula and self.formula != "custom":
            raise ValueError(
                f'custom_formula is set but formula is "{self.formula}", so the custom '
                'expression would be silently ignored. Set formula="custom" to use it, or '
                "clear custom_formula.")
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
