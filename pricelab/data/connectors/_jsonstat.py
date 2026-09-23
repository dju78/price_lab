"""JSON-stat 2.0 decoding, shared by the Eurostat connector and the generic
SDMX 2.1 connector: Eurostat's dissemination API *is* a real SDMX 2.1
endpoint, so the same decode logic serves both rather than one reimplementing
the other.

Reference: https://json-stat.org/format/ -- a JSON-stat 2.0 dataset stores
observations in a single flat `value` object keyed by a row-major flattened
index over every dimension's category position, with `size` giving each
dimension's cardinality in the same order as `id`.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .base import ConnectorSchemaError


def validate_jsonstat(raw: Any, source_name: str) -> None:
    if not isinstance(raw, dict):
        raise ConnectorSchemaError(f"{source_name}: expected a JSON object at the top level")
    for field in ("dimension", "value", "size", "id"):
        if field not in raw:
            raise ConnectorSchemaError(f"{source_name}: response is missing '{field}'")
    if "time" not in raw["dimension"]:
        raise ConnectorSchemaError(
            f"{source_name}: response has no 'time' dimension under 'dimension'")
    time_dim = raw["dimension"]["time"]
    if "category" not in time_dim or "index" not in time_dim["category"]:
        raise ConnectorSchemaError(
            f"{source_name}: 'dimension.time.category.index' is missing")


def parse_jsonstat(raw: dict[str, Any]) -> pd.DataFrame:
    """Decode every observation's time period and value.

    A dimension the query narrowed to a single category (the common case
    for a filtered single-series request) is not returned: it is the same
    on every row. A dimension that varies -- a request for several COICOP
    divisions at once, say -- comes back as a column of its own, named
    after the dimension and holding each row's category code. Without it
    the rows of different series are indistinguishable, and a decoder that
    returned twelve divisions' observations as one unlabelled column would
    be handing the caller a series that does not exist.
    """
    dim_ids: list[str] = raw["id"]
    sizes: list[int] = raw["size"]
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]

    time_pos = dim_ids.index("time")
    time_index: dict[str, int] = raw["dimension"]["time"]["category"]["index"]
    position_to_period = {v: k for k, v in time_index.items()}
    varying = {
        pos: {v: k for k, v in raw["dimension"][dim]["category"]["index"].items()}
        for pos, dim in enumerate(dim_ids)
        if dim != "time" and sizes[pos] > 1 and "category" in raw["dimension"].get(dim, {})
        and "index" in raw["dimension"][dim]["category"]}

    rows: list[dict[str, Any]] = []
    for flat_str, value in raw["value"].items():
        flat = int(flat_str)
        remaining = flat
        idx_per_dim = []
        for stride in strides:
            idx_per_dim.append(remaining // stride)
            remaining %= stride
        period_code = position_to_period[idx_per_dim[time_pos]]
        row: dict[str, Any] = {"period": period_code, "value": value, "_flat_index": flat}
        for pos, codes in varying.items():
            row[dim_ids[pos]] = codes[idx_per_dim[pos]]
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["period", "value"])
    df["period"] = df["period"].apply(_parse_period_code)
    df = df.dropna(subset=["period"]).drop(columns="_flat_index")
    return df.sort_values("period").reset_index(drop=True)


def _parse_period_code(code: str) -> pd.Timestamp | None:
    """Eurostat/SDMX time codes seen in practice: "2020" (annual), "2020-01"
    (monthly) and "2020-Q1" (quarterly). Returns the period's first day, or
    None for a code in none of these shapes -- dropped by the caller rather
    than raised on, since one unparseable period among many valid ones is a
    data-shape question for the caller, not grounds to fail the whole fetch.
    """
    if len(code) == 4 and code.isdigit():
        return pd.Timestamp(int(code), 1, 1)
    if "-Q" in code:
        year_str, q_str = code.split("-Q")
        return pd.Timestamp(int(year_str), (int(q_str) - 1) * 3 + 1, 1)
    try:
        return pd.to_datetime(code, format="%Y-%m")
    except ValueError:
        return None
