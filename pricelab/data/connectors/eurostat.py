"""Eurostat SDMX 2.1 dissemination API.

Verified live: `GET .../data/{dataset}?format=JSON&geo=..&coicop=..&lang=en`
returns a JSON-stat 2.0 document. Query-string dimension filters (`geo`,
`coicop`, ...) are accepted by the endpoint but, for at least the
`prc_hicp_manr` dataset tested, do not actually narrow the response: an
unfiltered request for that dataset returns every country and every COICOP
category (54 MB for that one dataset). Callers should expect to filter
client-side (`_parse` returns every combination the response contains when
more than one is present) rather than relying on the query string alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from ._jsonstat import parse_jsonstat, validate_jsonstat
from .base import BaseConnector

BASE_URL = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data"


class EurostatConnector(BaseConnector):
    source_name = "eurostat"

    def _request(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        dataset = kwargs["dataset"]
        filters = {k: v for k, v in kwargs.items() if k != "dataset"}
        params = {"format": "JSON", "lang": "en", **filters}
        return f"{BASE_URL}/{dataset}", params

    def _validate(self, raw: Any) -> None:
        validate_jsonstat(raw, self.source_name)

    def _parse(self, raw: Any, **kwargs: Any) -> pd.DataFrame:
        return parse_jsonstat(raw)

    def _api_version(self, raw: Any) -> str | None:
        version = raw.get("version") if isinstance(raw, dict) else None
        return str(version) if version is not None else None


# ---------------------------------------------------------------------
# The HICP as a classification tree
# ---------------------------------------------------------------------
#: Eurostat datasets holding the HICP's monthly indices and its item weights.
HICP_INDEX_DATASET = "prc_hicp_midx"
HICP_WEIGHT_DATASET = "prc_hicp_inw"
HICP_ROOT = "CP00"


#: The ECOICOP codes of the all-items HICP, its twelve divisions and their
#: groups, as Eurostat publishes them (checked against the dataset's code
#: list on 2026-09-23). CP08 has one published group covering a small part
#: of its weight, which `hicp_tree` handles by keeping CP08 as a leaf.
HICP_CODES: tuple[str, ...] = (
    "CP00", "CP01", "CP011", "CP012", "CP02", "CP021", "CP022", "CP03", "CP031", "CP032",
    "CP04", "CP041", "CP043", "CP044", "CP045", "CP05", "CP051", "CP052", "CP053", "CP054",
    "CP055", "CP056", "CP06", "CP061", "CP062", "CP063", "CP07", "CP071", "CP072", "CP073",
    "CP08", "CP081", "CP09", "CP091", "CP092", "CP093", "CP094", "CP095", "CP096", "CP10",
    "CP101", "CP102", "CP103", "CP104", "CP105", "CP11", "CP111", "CP112", "CP12", "CP121",
    "CP123", "CP124", "CP125", "CP126", "CP127")


def hicp_key(dataset: str, codes: list[str], geo: str, *, unit: str | None = "I15") -> str:
    """The `dataset` argument that asks the SDMX 2.1 endpoint for several
    COICOP codes in one request, by key path rather than query string.

    The query-string filters are accepted and ignored by this endpoint (see
    the module docstring), but a key path is honoured: `M.I15.CP01+CP02.EA`
    returns those two series and nothing else. The weights dataset has no
    unit dimension, hence `unit=None` for it.
    """
    joined = "+".join(codes)
    if dataset == HICP_WEIGHT_DATASET or unit is None:
        return f"{dataset}/A.{joined}.{geo}"
    return f"{dataset}/M.{unit}.{joined}.{geo}"


def hicp_parent(code: str) -> str | None:
    """ECOICOP parent by code: CP011 -> CP01 -> CP00 -> None."""
    if code == HICP_ROOT:
        return None
    if len(code) == 4:          # a division, CPnn
        return HICP_ROOT
    return code[:4]


@dataclass(frozen=True)
class HICPTree:
    """Leaf indices, their weights and the tree above them, ready for
    `engine.aggregation.aggregate_tree`, plus the published headline to
    check the re-aggregation against."""

    leaf_indices: pd.DataFrame
    leaf_weights: dict[str, float]
    parent_of: dict[str, str | None]
    supplied_parent_weights: dict[str, float]
    published: pd.DataFrame
    """Every published series, rebased like the leaves, including the
    divisions and the all-items index the tree re-derives."""
    weight_year: int
    base_period: pd.Timestamp
    notes: tuple[str, ...]


def hicp_tree(indices: pd.DataFrame, weights: pd.DataFrame, year: int, *,
              coverage_tolerance: float = 0.01) -> HICPTree:
    """Build the three-level HICP tree for one year from two decoded fetches.

    `indices` and `weights` are the frames `parse_jsonstat` returns for a
    multi-code request (a `coicop` column beside `period` and `value`).

    The year is the unit of HICP compilation: each year's item weights are
    price-updated to December of the year before, and the indices are
    chain-linked there. So the leaves are rebased to December of `year - 1`
    and weighted with `year`'s weights, which is the arithmetic the
    published index itself uses within that year -- and is why a
    re-aggregation of the published divisions reproduces the published
    all-items index to rounding, which the tests check.

    A division is used as a leaf in its own right when its published groups
    do not cover its weight (within `coverage_tolerance` of it): an
    aggregate built from a fraction of its parts would be a different index
    with the parent's name on it. Each such substitution is named in
    `notes`.
    """
    for frame, name in ((indices, "indices"), (weights, "weights")):
        if "coicop" not in frame.columns:
            raise ValueError(
                f"the {name} frame has no 'coicop' column: it must come from a request for "
                "several COICOP codes, so each row says which series it belongs to")
    wide = indices.pivot_table(index="period", columns="coicop", values="value")
    wide.index = pd.DatetimeIndex(wide.index)
    base = pd.Timestamp(year - 1, 12, 1)
    if base not in wide.index:
        raise ValueError(
            f"the indices do not include December {year - 1}, the period {year}'s HICP weights "
            "are price-updated to; fetch from that month onwards")
    span = wide.loc[base:pd.Timestamp(year, 12, 1)]
    rebased = span / span.loc[base] * 100.0

    w = weights[pd.DatetimeIndex(weights["period"]).year == year]
    if w.empty:
        raise ValueError(f"no item weights for {year} in the weights frame")
    weight_of = {str(c): float(v) for c, v in zip(w["coicop"], w["value"], strict=True)}

    codes = [c for c in rebased.columns if c in weight_of]
    divisions = sorted(c for c in codes if len(c) == 4 and c != HICP_ROOT)
    notes: list[str] = []
    leaves: list[str] = []
    for division in divisions:
        groups = [c for c in codes if len(c) == 5 and c.startswith(division)]
        covered = sum(weight_of[g] for g in groups)
        if groups and abs(covered - weight_of[division]) <= coverage_tolerance * max(
                weight_of[division], 1e-12) + 0.05:
            leaves.extend(groups)
        else:
            leaves.append(division)
            if groups:
                notes.append(
                    f"{division}: its published groups ({', '.join(groups)}) carry "
                    f"{covered:.2f} of its {weight_of[division]:.2f} per mille, so the division "
                    "is used as a leaf itself rather than rebuilt from a fraction of its parts")
    parent_of: dict[str, str | None] = {HICP_ROOT: None}
    for leaf in leaves:
        parent = hicp_parent(leaf)
        parent_of[leaf] = parent
        if parent is not None and parent != HICP_ROOT:
            parent_of[parent] = HICP_ROOT
    supplied = {c: weight_of[c] for c in parent_of if c not in leaves and c in weight_of}
    return HICPTree(
        leaf_indices=rebased[leaves], leaf_weights={c: weight_of[c] for c in leaves},
        parent_of=parent_of, supplied_parent_weights=supplied, published=rebased,
        weight_year=year, base_period=base, notes=tuple(notes))


def hicp_chain_inputs(indices: pd.DataFrame, weights: pd.DataFrame, codes: list[str]
                      ) -> tuple[pd.DataFrame, dict[int, dict[str, float]], pd.Series]:
    """The chain-linked component indices, each year's weights and the
    published all-items index, in the shapes
    `engine.decomposition.ribe_contributions` takes.

    Eurostat labels a weight set with the year it is used in -- the 2025
    weights apply from December 2024 to December 2025 -- which is the
    convention `ribe_contributions` expects.
    """
    wide = indices.pivot_table(index="period", columns="coicop", values="value")
    wide.index = pd.DatetimeIndex(wide.index)
    missing = [c for c in [*codes, HICP_ROOT] if c not in wide.columns]
    if missing:
        raise ValueError(f"the indices have no series for {missing}")
    by_year: dict[int, dict[str, float]] = {}
    for period, code, value in zip(pd.DatetimeIndex(weights["period"]), weights["coicop"],
                                   weights["value"], strict=True):
        if code in codes:
            by_year.setdefault(int(period.year), {})[str(code)] = float(value)
    return wide[codes], by_year, wide[HICP_ROOT].rename(HICP_ROOT)


# ---------------------------------------------------------------------
# Purchasing power parities by expenditure category
# ---------------------------------------------------------------------
PPP_DATASET = "prc_ppp_ind"
#: Eurostat's aggregate geography codes, which are not regions to compare.
_AGGREGATE_GEO = ("EU", "EA", "CPC")


def ppp_comparison_inputs(frame: pd.DataFrame, *, parent: str = "A01",
                          base: str = "EU27_2020", year: int | None = None,
                          ) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """A spatial comparison's inputs from `prc_ppp_ind`: each region's most
    detailed published categories under `parent`, their PPPs as the prices
    and their national-currency expenditure as the weights; the published
    PPP of `parent` itself, to compare the result with; and each region's
    coverage -- the share of its `parent` expenditure the categories used
    account for.

    "Most detailed published" is per region: a country that publishes only
    aggregates contributes the aggregate, which no other region prices at
    that level, so it arrives in the comparison unconnected and is reported
    as such rather than dropped in silence.
    """
    data = frame.copy()
    if year is not None:
        data = data[pd.DatetimeIndex(data["period"]).year == year]
    regions = sorted(g for g in data["geo"].unique()
                     if g == base or not str(g).startswith(_AGGREGATE_GEO))
    data = data[data["geo"].isin(regions) & data["ppp_cat"].astype(str).str.startswith(parent)]
    ppp_item = next(i for i in data["na_item"].unique() if str(i).startswith("PPP_"))
    ppp = data[data["na_item"] == ppp_item]
    spend = data[data["na_item"] == "EXP_NAC"]
    rows = []
    for region, group in ppp.groupby("geo"):
        codes = set(group["ppp_cat"].astype(str)) - {parent} or {parent}
        leaves = [c for c in codes if not any(o != c and o.startswith(c) for o in codes)]
        rows.append(group[group["ppp_cat"].isin(leaves)].assign(region=region))
    long = pd.concat(rows).rename(columns={"ppp_cat": "product", "value": "price"})
    long = long.merge(spend.rename(columns={"geo": "region", "ppp_cat": "product",
                                            "value": "expenditure"})[
        ["region", "product", "expenditure"]], on=["region", "product"], how="left")
    published = ppp[ppp["ppp_cat"] == parent].set_index("geo")["value"].astype(float)
    parent_spend = spend[spend["ppp_cat"] == parent].set_index("geo")["value"].astype(float)
    used = long.groupby("region")["expenditure"].sum(min_count=1)
    coverage = (used / parent_spend.reindex(used.index)).rename("coverage")
    return (long[["region", "product", "price", "expenditure"]].reset_index(drop=True),
            published.rename(f"published {parent} PPP"), coverage)


# ---------------------------------------------------------------------
# House price indices: new and existing dwellings
# ---------------------------------------------------------------------
HPI_DATASET = "prc_hpi_q"
HPI_WEIGHT_DATASET = "prc_hpi_inw"


def hpi_components(indices: pd.DataFrame, weights: pd.DataFrame, geo: str
                   ) -> tuple[pd.DataFrame, dict[int, dict[str, float]], pd.Series]:
    """For one geography: the new- and existing-dwelling sub-indices, each
    year's weights for them (per mille, labelled with the year they apply
    in), and the published total -- the inputs for rebuilding the total with
    `engine.decomposition.chain_linked_aggregate` at a fourth-quarter link.
    """
    mine = indices[indices["geo"] == geo]
    if mine.empty:
        raise ValueError(f"no house price indices for {geo!r}")
    wide = mine.pivot_table(index="period", columns="purchase", values="value")
    wide.index = pd.DatetimeIndex(wide.index)
    parts = ["DW_NEW", "DW_EXST"]
    missing = [c for c in [*parts, "TOTAL"] if c not in wide.columns]
    if missing:
        raise ValueError(f"{geo} has no {missing} series")
    by_year: dict[int, dict[str, float]] = {}
    w = weights[(weights["geo"] == geo) & weights["purchase"].isin(parts)]
    for period, purchase, value in zip(pd.DatetimeIndex(w["period"]), w["purchase"], w["value"],
                                       strict=True):
        by_year.setdefault(int(period.year), {})[str(purchase)] = float(value)
    return wide[parts], by_year, wide["TOTAL"].rename("TOTAL")
