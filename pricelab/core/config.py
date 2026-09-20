"""Configuration. Every result the library produces is reproducible from one
of these objects, which is the point: a chart you cannot regenerate from a
recorded configuration is not an auditable statistic."""

from dataclasses import dataclass, field, asdict
from typing import Dict, Optional
import json


@dataclass
class Schema:
    """Column mapping. Lets the library accept any collection with the same
    logical shape, not just this one workbook."""
    date: str = "Date"
    item_id: str = "Item_ID"
    item_name: str = "Item_Name"
    category: str = "Category"
    price: str = "Reported_Price"
    weight: Optional[str] = None          # expenditure weight, if supplied


@dataclass
class QualityConfig:
    missing_codes: tuple = (0,)           # values that mean "no price", not "price of nil"
    reference_window: int = 13            # months, centred, within item
    min_reference_periods: int = 3
    scale_log10_low: float = 1.5          # flag if |log10(price / local median)| in
    scale_log10_high: float = 2.5         # [low, high]; catches order-of-100 unit faults
    repair_scale_errors: bool = True      # repair rather than delete
    residual_tolerance: float = 0.5       # post-repair check, log10 units


@dataclass
class IndexConfig:
    formula: str = "jevons"               # jevons | dutot | carli | laspeyres
    chained: bool = True
    base_period: Optional[str] = None     # ISO date; defaults to first period
    base_value: float = 100.0
    min_matched_items: int = 2            # below this, hold the index and warn


@dataclass
class ImputationConfig:
    # per category: none | carry_forward | class_mean | seasonal_hold
    default_method: str = "none"
    by_category: Dict[str, str] = field(default_factory=dict)

    def method_for(self, category: str) -> str:
        return self.by_category.get(category, self.default_method)


@dataclass
class RunConfig:
    schema: Schema = field(default_factory=Schema)
    quality: QualityConfig = field(default_factory=QualityConfig)
    imputation: ImputationConfig = field(default_factory=ImputationConfig)
    index: IndexConfig = field(default_factory=IndexConfig)
    label: str = "unnamed run"

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent, default=str)

    @classmethod
    def from_dict(cls, d: dict) -> "RunConfig":
        return cls(
            schema=Schema(**d.get("schema", {})),
            quality=QualityConfig(**d.get("quality", {})),
            imputation=ImputationConfig(**d.get("imputation", {})),
            index=IndexConfig(**d.get("index", {})),
            label=d.get("label", "unnamed run"),
        )
