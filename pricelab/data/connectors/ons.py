"""ONS (UK Office for National Statistics).

Targets the beta API (`api.beta.ons.gov.uk/v1/data`), not the legacy
timeseries API (`api.ons.gov.uk/timeseries/.../data`): verified live that
the legacy endpoint returns HTTP 404 with the body "This API has been
decommissioned as part of a suite of work to improve the digital products
and services we offer. It was fully retired on 25/11/2024." ONS's own
migration from the legacy API to the beta one is exactly the kind of
friction this phase's brief asked to be reported rather than hidden.

The beta API takes a series `uri` (e.g.
`/economy/inflationandpriceindices/timeseries/d7g7/mm23`) and returns
`years`, `quarters` and `months` arrays, whichever the series actually
publishes at.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .base import BaseConnector, ConnectorSchemaError

BASE_URL = "https://api.beta.ons.gov.uk/v1/data"

_MONTH_GRANULARITIES = ("months", "quarters", "years")


class ONSConnector(BaseConnector):
    source_name = "ons"

    def _request(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        return BASE_URL, {"uri": kwargs["series_uri"]}

    def _validate(self, raw: Any) -> None:
        if not isinstance(raw, dict):
            raise ConnectorSchemaError(f"{self.source_name}: expected a JSON object")
        if not any(g in raw for g in _MONTH_GRANULARITIES):
            raise ConnectorSchemaError(
                f"{self.source_name}: response has none of {_MONTH_GRANULARITIES}")
        granularity = kwargs_granularity(raw)
        rows = raw[granularity]
        if rows and not {"date", "value", "year"} <= rows[0].keys():
            missing = {"date", "value", "year"} - rows[0].keys()
            raise ConnectorSchemaError(
                f"{self.source_name}: '{granularity}' rows are missing {sorted(missing)}")

    def _parse(self, raw: Any, **kwargs: Any) -> pd.DataFrame:
        granularity = kwargs.get("granularity") or kwargs_granularity(raw)
        rows = raw[granularity]
        out = pd.DataFrame({
            "period": [_row_period(r) for r in rows],
            "value": [_to_float(r["value"]) for r in rows],
        })
        return out.dropna(subset=["period"]).sort_values("period").reset_index(drop=True)

    def _api_version(self, raw: Any) -> str | None:
        version = raw.get("type") if isinstance(raw, dict) else None
        return str(version) if version else None


def kwargs_granularity(raw: dict[str, Any]) -> str:
    """The finest granularity this response actually has, preferring
    months over quarters over years, unless the caller asked for a
    specific one that is present."""
    for g in _MONTH_GRANULARITIES:
        if g in raw and raw[g]:
            return g
    return _MONTH_GRANULARITIES[0]


def _row_period(row: dict[str, Any]) -> pd.Timestamp | None:
    year = row.get("year")
    month = row.get("month") or ""
    if not year:
        return None
    if month:
        try:
            return pd.to_datetime(f"{year}-{month}", format="%Y-%B")
        except ValueError:
            return None
    return pd.Timestamp(int(year), 1, 1)


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")
