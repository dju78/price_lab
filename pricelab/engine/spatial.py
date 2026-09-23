"""Spatial price comparison: purchasing power parities between regions by
the country product dummy (CPD) method and the Geary-Khamis method, and
conversion at the parities they produce.

A spatial comparison is an index across places instead of across time, and
it fails the same way a thin index stratum does: a parity resting on two or
three products the regions happen to share is a number, not a measurement.
So every result carries the matched-product count for every pair of
regions, and a region whose products are too thinly tied to the rest of
the comparison is *reported* -- its estimate is visible, with the count and
the reason -- but *withheld* from the published parities and from any
conversion built on them. A region with no chain of shared products to the
base at all has no estimate; it is reported with the reason and the rest of
the comparison goes ahead without it.

CPD (Summers 1973; ICP Book, World Bank 2013, chapter 4):

    ln p_rn = alpha_r + beta_n + e_rn,     PPP_r = exp(alpha_r - alpha_base)

a regression of log prices on region and product dummies, weighted by
expenditure shares when they are supplied. It works with any pattern of
missing prices as long as the regions are connected through shared
products, and gives standard errors.

Geary-Khamis (Geary 1958, Khamis 1972): international prices and parities
defined jointly,

    pi_n  = sum_r (p_rn q_rn / PPP_r) / sum_r q_rn
    PPP_r = sum_n p_rn q_rn / sum_n pi_n q_rn

solved by iteration from PPP = 1. Quantity-weighted, so large regions
dominate the international prices -- the Gerschenkron effect -- and
additive in real expenditures, which is why it is used for aggregate
volume comparisons. Needs quantities.

Both are normalised so that the base region's parity is 1: a PPP is local
currency units per unit of the base region's currency.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "METHODS",
    "SpatialError",
    "SpatialResult",
    "convert",
    "cpd",
    "geary_khamis",
    "overlap_matrix",
    "price_level_indices",
]

METHODS: dict[str, str] = {"cpd": "Country product dummy (CPD)", "geary_khamis": "Geary-Khamis"}

#: Products a region must share with the rest of the comparison before its
#: parity is published. Below this it is estimated and reported, not published.
DEFAULT_MIN_OVERLAP = 5


class SpatialError(ValueError):
    """The prices cannot support the comparison asked of them."""


@dataclass(frozen=True)
class SpatialResult:
    method: str
    base: str
    ppp_estimated: pd.Series
    """Every region's parity as estimated, withheld or not."""
    ppp: pd.Series
    """The published parities: NaN for a withheld region."""
    overlap: pd.DataFrame
    """Products priced in both regions, for every pair."""
    withheld: Mapping[str, str]
    """Region -> why its parity is not published."""
    thin_pairs: pd.DataFrame
    """Every pair of regions sharing fewer products than the threshold."""
    min_overlap: int
    international_prices: pd.Series | None = None
    standard_errors: pd.Series | None = None
    iterations: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        published = int(self.ppp.notna().sum())
        return (f"{METHODS[self.method]} parities, {self.base} = 1, local currency per unit of "
                f"{self.base}'s; {published} of {len(self.ppp_estimated)} regions published, "
                f"{len(self.withheld)} withheld for sharing fewer than {self.min_overlap} "
                "products with the rest of the comparison")

    @property
    def table(self) -> pd.DataFrame:
        shared = {r: _shared_products(self.overlap, r) for r in self.ppp_estimated.index}
        out = pd.DataFrame({
            "ppp_estimated": self.ppp_estimated,
            "ppp_published": self.ppp,
            "products_shared_with_others": pd.Series(shared),
            "withheld_because": pd.Series({r: self.withheld.get(r, "")
                                           for r in self.ppp_estimated.index}),
        })
        if self.standard_errors is not None:
            out.insert(2, "standard_error_log", self.standard_errors)
        return out


def _prepare(prices: pd.DataFrame, value_cols: tuple[str, ...]) -> pd.DataFrame:
    needed = {"region", "product", "price", *value_cols}
    missing = sorted(needed - set(prices.columns))
    if missing:
        raise SpatialError(f"the price table has no {missing} column(s)")
    frame = prices[list(needed)].dropna().reset_index(drop=True)
    frame["region"] = frame["region"].astype(str)
    frame["product"] = frame["product"].astype(str)
    if (frame["price"] <= 0).any():
        raise SpatialError("prices must be positive; a zero or negative price has no parity")
    if frame.duplicated(["region", "product"]).any():
        raise SpatialError("each region must price each product at most once; average repeated "
                           "quotes to one price per region and product first")
    return frame


def overlap_matrix(prices: pd.DataFrame) -> pd.DataFrame:
    """Products priced in both regions, for every pair (the diagonal is each
    region's own product count)."""
    frame = _prepare(prices, ())
    presence = pd.crosstab(frame["product"], frame["region"]).clip(upper=1)
    matrix = presence.T @ presence
    matrix.index.name, matrix.columns.name = "region", "region"
    return matrix.astype(int)


def _shared_products(overlap: pd.DataFrame, region: str) -> int:
    """The most products the region shares with any single other region:
    its strongest direct tie into the comparison. A region whose best
    pairing rests on a handful of products has a parity resting on them."""
    if len(overlap) <= 1:
        return 0
    row = pd.Series(overlap.loc[region], dtype=float).drop(region)
    return int(row.max())


def _connected_to(frame: pd.DataFrame, base: str) -> set[str]:
    """Regions reachable from the base through chains of shared products."""
    by_product = frame.groupby("product")["region"].apply(set)
    by_region = frame.groupby("region")["product"].apply(set)
    reached, frontier = {base}, [base]
    while frontier:
        region = frontier.pop()
        for product in by_region[region]:
            for other in by_product[product] - reached:
                reached.add(other)
                frontier.append(other)
    return reached


def _withholding(frame: pd.DataFrame, overlap: pd.DataFrame, base: str,
                 min_overlap: int) -> tuple[dict[str, str], pd.DataFrame]:
    regions = list(overlap.index)
    withheld: dict[str, str] = {}
    for region in regions:
        shared = _shared_products(overlap, region)
        if region != base and shared < min_overlap:
            withheld[region] = (
                f"at most {shared} of its products are priced in any other region, below the "
                f"{min_overlap} a published parity needs; the estimate is shown, not published")
    thin = [(a, b, int(overlap.loc[a, b])) for i, a in enumerate(regions)
            for b in regions[i + 1:] if overlap.loc[a, b] < min_overlap]
    return withheld, pd.DataFrame(thin, columns=["region_a", "region_b", "matched_products"])


def _connected_part(frame: pd.DataFrame, base: str) -> tuple[pd.DataFrame, dict[str, str]]:
    """The prices of the regions connected to the base, and a reason for
    every region that is not.

    A region sharing no chain of products with the base has no parity by
    any method: nothing it prices can be compared with anything the base
    prices. Real comparison data has such regions -- countries that publish
    only aggregates, say -- and they are reported and left out of the
    estimation, rather than failing the comparison for everyone else.
    """
    if base not in set(frame["region"]):
        raise SpatialError(f"the base region {base!r} has no prices")
    reached = _connected_to(frame, base)
    unreachable = {
        r: (f"shares no chain of products with {base}: nothing it prices can be compared with "
            "anything the base prices, so it has no parity by any method")
        for r in sorted(set(frame["region"]) - reached)}
    connected = frame[frame["region"].isin(reached)]
    if connected["region"].nunique() < 2:
        raise SpatialError(f"no region is connected to {base!r} through shared products")
    return connected, unreachable


def _merge_withheld(thin: dict[str, str], unreachable: dict[str, str]) -> dict[str, str]:
    merged = dict(thin)
    merged.update(unreachable)
    return merged


def cpd(prices: pd.DataFrame, *, base: str, weighted: bool = False,
        min_overlap: int = DEFAULT_MIN_OVERLAP) -> SpatialResult:
    """Country product dummy parities.

    `prices` is a long table with `region`, `product` and `price`; with
    `weighted=True` it also needs `expenditure`, and each observation is
    weighted by its product's share of the region's expenditure (the
    weighted CPD of the ICP), so a product a region hardly buys moves its
    parity hardly at all.
    """
    full = _prepare(prices, ("expenditure",) if weighted else ())
    frame, unreachable = _connected_part(full, base)
    all_regions = sorted(set(full["region"]))
    regions = sorted(set(frame["region"]))
    products = sorted(set(frame["product"]))
    others = [r for r in regions if r != base]
    region_pos = {r: i for i, r in enumerate(others)}
    product_pos = {p: len(others) + i for i, p in enumerate(products)}
    design = np.zeros((len(frame), len(others) + len(products)))
    for row, (region, product) in enumerate(zip(frame["region"], frame["product"], strict=True)):
        if region != base:
            design[row, region_pos[region]] = 1.0
        design[row, product_pos[product]] = 1.0
    y = np.log(frame["price"].to_numpy(dtype=float))
    if weighted:
        shares = frame["expenditure"] / frame.groupby("region")["expenditure"].transform("sum")
        root = np.sqrt(shares.to_numpy(dtype=float))
    else:
        root = np.ones(len(frame))
    coef, _, rank, _ = np.linalg.lstsq(design * root[:, None], y * root, rcond=None)
    if rank < design.shape[1]:
        raise SpatialError("the region and product dummies are not all identified")
    residuals = (y - design @ coef) * root
    dof = len(frame) - design.shape[1]
    alpha = pd.Series({base: 0.0, **{r: float(coef[region_pos[r]]) for r in others}})
    standard_errors: pd.Series | None = None
    notes: list[str] = []
    if dof > 0:
        sigma2 = float(residuals @ residuals) / dof
        cov = sigma2 * np.linalg.pinv((design * root[:, None]).T @ (design * root[:, None]))
        standard_errors = pd.Series({base: 0.0, **{r: float(np.sqrt(cov[region_pos[r],
                                                                         region_pos[r]]))
                                                    for r in others}})
    else:
        notes.append("as many parameters as prices: the fit is exact and has no standard errors")
    overlap = overlap_matrix(full)
    thin_withheld, thin = _withholding(full, overlap, base, min_overlap)
    withheld = _merge_withheld(thin_withheld, unreachable)
    estimated = pd.Series(np.exp(alpha.to_numpy(dtype=float)),
                          index=alpha.index).reindex(all_regions)
    return SpatialResult(
        method="cpd", base=base, ppp_estimated=estimated,
        ppp=estimated.where(~estimated.index.isin(list(withheld))), overlap=overlap,
        withheld=withheld, thin_pairs=thin, min_overlap=min_overlap,
        international_prices=pd.Series({p: float(np.exp(coef[product_pos[p]]))
                                        for p in products}),
        standard_errors=(standard_errors.reindex(all_regions) if standard_errors is not None
                         else None),
        notes=tuple(notes))


def geary_khamis(prices: pd.DataFrame, *, base: str, min_overlap: int = DEFAULT_MIN_OVERLAP,
                 tolerance: float = 1e-12, max_iterations: int = 10_000) -> SpatialResult:
    """Geary-Khamis parities, by iteration from PPP = 1.

    `prices` needs `region`, `product`, `price` and `quantity`. A product
    missing in a region simply does not enter that region's sums; the
    comparison must still be connected through shared products.
    """
    full = _prepare(prices, ("quantity",))
    if (full["quantity"] < 0).any():
        raise SpatialError("quantities must not be negative")
    frame, unreachable = _connected_part(full, base)
    all_regions = sorted(set(full["region"]))
    regions = sorted(set(frame["region"]))
    value = (frame["price"] * frame["quantity"]).to_numpy(dtype=float)
    region_of = frame["region"].to_numpy()
    product_of = frame["product"].to_numpy()
    quantity = frame["quantity"].to_numpy(dtype=float)
    q_by_product = pd.Series(quantity).groupby(product_of).sum()
    v_by_region = pd.Series(value).groupby(region_of).sum()
    ppp = pd.Series(1.0, index=regions)
    international = pd.Series(dtype=float)
    iterations = 0
    for iterations in range(1, max_iterations + 1):   # noqa: B007 - reported below
        deflated = value / ppp.reindex(region_of).to_numpy()
        international = pd.Series(deflated).groupby(product_of).sum() / q_by_product
        at_international = pd.Series(international.reindex(product_of).to_numpy() * quantity)
        updated = v_by_region / at_international.groupby(region_of).sum()
        updated = updated / updated[base]
        change = float((updated - ppp).abs().max())
        ppp = updated
        if change < tolerance:
            break
    else:
        raise SpatialError(f"Geary-Khamis did not converge in {max_iterations} iterations")
    overlap = overlap_matrix(full)
    thin_withheld, thin = _withholding(full, overlap, base, min_overlap)
    withheld = _merge_withheld(thin_withheld, unreachable)
    return SpatialResult(
        method="geary_khamis", base=base, ppp_estimated=ppp.reindex(all_regions),
        ppp=ppp.reindex(all_regions).where(~pd.Index(all_regions).isin(list(withheld))),
        overlap=overlap, withheld=withheld, thin_pairs=thin, min_overlap=min_overlap,
        international_prices=international, iterations=iterations)


def convert(values: pd.Series, result: SpatialResult) -> pd.DataFrame:
    """Local-currency values (indexed by region) divided by the published
    parities: values in the base region's currency at its price level.

    A withheld region is carried with no converted value and the reason,
    rather than being converted at a parity that was not published.
    """
    local = pd.Series(values, dtype=float)
    out = pd.DataFrame({"local_currency": local,
                        "ppp": result.ppp.reindex(local.index)})
    out[f"in {result.base} prices"] = out["local_currency"] / out["ppp"]
    out["not_converted_because"] = [
        result.withheld.get(str(r), "" if str(r) in result.ppp.index else "no parity for region")
        for r in out.index]
    return out


def price_level_indices(result: SpatialResult, exchange_rates: pd.Series) -> pd.Series:
    """PPP over the market exchange rate (local currency per unit of the
    base region's currency), times 100: above 100 a region is dearer than
    the base, below it cheaper. Published parities only."""
    rates = pd.Series(exchange_rates, dtype=float)
    common = result.ppp.dropna().index.intersection(rates.index)
    if common.empty:
        raise SpatialError("no region has both a published parity and an exchange rate")
    return (result.ppp[common] / rates[common] * 100.0).rename("price level index")
