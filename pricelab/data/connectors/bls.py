"""US Bureau of Labor Statistics, public API v2.

Verified live *without* a registration key, for a single series -- v2
permits a small number of unregistered requests per day, each limited to
10 years of history and fewer series per call than a registered key
allows. `api_key` is read from `PRICELAB_BLS_API_KEY` if a caller wants the
registered tier's higher limits; its absence degrades to the unregistered
tier rather than failing, which is what "expect friction, report it, and
degrade gracefully when a key is absent" means for this specific agency.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd

from .base import BaseConnector, ConnectorError, ConnectorSchemaError

BASE_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data"

_MONTH_PERIODS = {f"M{m:02d}": m for m in range(1, 13)}


class BLSConnector(BaseConnector):
    source_name = "bls"

    def _request(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        series_id = kwargs["series_id"]
        params: dict[str, Any] = {}
        api_key = kwargs.get("api_key") or os.environ.get("PRICELAB_BLS_API_KEY")
        if api_key:
            params["registrationkey"] = api_key
        return f"{BASE_URL}/{series_id}", params

    def _validate(self, raw: Any) -> None:
        if not isinstance(raw, dict) or "Results" not in raw:
            raise ConnectorSchemaError(f"{self.source_name}: response is missing 'Results'")
        series = raw["Results"].get("series")
        if not series or "data" not in series[0]:
            raise ConnectorSchemaError(
                f"{self.source_name}: 'Results.series[0].data' is missing")
        # A request BLS could not process (bad series ID, quota exceeded)
        # still returns HTTP 200 with an explanatory status/message: a
        # structural problem, not a schema one, so it is a plain
        # ConnectorError rather than ConnectorSchemaError.
        if raw.get("status") != "REQUEST_SUCCEEDED":
            raise ConnectorError(
                f"{self.source_name}: {raw.get('status')}: {'; '.join(raw.get('message', []))}")

    def _parse(self, raw: Any, **kwargs: Any) -> pd.DataFrame:
        series_id = kwargs["series_id"]
        series = next(
            (s for s in raw["Results"]["series"] if s.get("seriesID") == series_id),
            raw["Results"]["series"][0])
        rows = []
        for point in series["data"]:
            month = _MONTH_PERIODS.get(point["period"])
            if month is None:
                continue  # annual averages (M13) and similar non-month codes
            value = pd.to_numeric(point["value"], errors="coerce")
            rows.append({"period": pd.Timestamp(int(point["year"]), month, 1), "value": value})
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=["period", "value"])
        return df.dropna(subset=["value"]).sort_values("period").reset_index(drop=True)
