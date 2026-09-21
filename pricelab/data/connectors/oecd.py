"""OECD's current SDMX-JSON API (`sdmx.oecd.org/public/rest/data/...`),
verified live -- OECD retired the older `stats.oecd.org/sdmx-json` domain,
another instance of an agency migrating its API surface mid-project. The
response is SDMX-JSON (the older sibling format to JSON-stat, structured
around `dataSets[].series{}.observations{}` plus a `structure` block
mapping each dimension's positions to codes), not JSON-stat, so it needs
its own decoder rather than reusing `_jsonstat`.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .base import BaseConnector, ConnectorSchemaError

BASE_URL = "https://sdmx.oecd.org/public/rest/data"


class OECDConnector(BaseConnector):
    source_name = "oecd"

    def _request(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        dataset = kwargs["dataset"]
        filter_expr = kwargs.get("filter_expr", "all")
        return f"{BASE_URL}/{dataset}/{filter_expr}", {"format": "jsondata"}

    def _validate(self, raw: Any) -> None:
        if not isinstance(raw, dict) or "data" not in raw:
            raise ConnectorSchemaError(f"{self.source_name}: response is missing 'data'")
        data = raw["data"]
        if "dataSets" not in data or not data["dataSets"]:
            raise ConnectorSchemaError(f"{self.source_name}: 'data.dataSets' is missing or empty")
        if "structure" not in data:
            raise ConnectorSchemaError(f"{self.source_name}: 'data.structure' is missing")
        obs_dims = data["structure"].get("dimensions", {}).get("observation", [])
        if not any(d.get("id") == "TIME_PERIOD" for d in obs_dims):
            raise ConnectorSchemaError(
                f"{self.source_name}: no 'TIME_PERIOD' dimension under "
                "'data.structure.dimensions.observation'")
        series = data["dataSets"][0].get("series", {})
        if not series:
            raise ConnectorSchemaError(f"{self.source_name}: 'dataSets[0].series' is empty")

    def _parse(self, raw: Any, **kwargs: Any) -> pd.DataFrame:
        data = raw["data"]
        series_dict = data["dataSets"][0]["series"]
        series_key = kwargs.get("series_key") or next(iter(series_dict))
        observations = series_dict[series_key]["observations"]

        obs_dims = data["structure"]["dimensions"]["observation"]
        time_dim = next(d for d in obs_dims if d["id"] == "TIME_PERIOD")
        time_values = time_dim["values"]

        rows = []
        for idx_str, obs in observations.items():
            idx = int(idx_str)
            if idx >= len(time_values):
                continue
            period_code = time_values[idx]["id"]
            value = obs[0] if isinstance(obs, list) and obs else None
            rows.append({"period": period_code, "value": value})

        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=["period", "value"])
        df["period"] = pd.to_datetime(df["period"], format="%Y-%m", errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.dropna(subset=["period", "value"]).sort_values("period").reset_index(drop=True)
