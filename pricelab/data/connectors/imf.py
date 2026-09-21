"""IMF.

Targets the DataMapper API (`www.imf.org/external/datamapper/api/v1`), the
source behind the World Economic Outlook database, not the classic
`dataservices.imf.org/REST/SDMX_JSON.svc` (IFS/CompactData) endpoint.
Verified live that the classic endpoint refuses the connection outright
(no response at all, not even a slow one) from this environment, which
matches this phase's brief warning that "the IMF endpoint has a history of
intermittent availability" -- DataMapper, tested at the same time, answers
normally. DataMapper covers annual data (inflation, PCPIPCH, among many
other indicators) rather than IFS's monthly CPI levels; a connector for
the classic endpoint, if it becomes reachable, is a second module, not a
change to this one.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .base import BaseConnector, ConnectorSchemaError

BASE_URL = "https://www.imf.org/external/datamapper/api/v1"


class IMFConnector(BaseConnector):
    source_name = "imf"

    def _request(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        indicator = kwargs["indicator"]
        country = kwargs["country"]
        return f"{BASE_URL}/{indicator}/{country}", {}

    def _validate(self, raw: Any) -> None:
        if not isinstance(raw, dict) or "values" not in raw:
            raise ConnectorSchemaError(f"{self.source_name}: response is missing 'values'")

    def _parse(self, raw: Any, **kwargs: Any) -> pd.DataFrame:
        indicator = kwargs["indicator"]
        country = kwargs["country"]
        values = raw["values"]
        if indicator not in values:
            raise ConnectorSchemaError(
                f"{self.source_name}: indicator '{indicator}' is missing from 'values' "
                f"(available: {sorted(values.keys())[:10]}...)")
        by_country = values[indicator]
        if country not in by_country:
            raise ConnectorSchemaError(
                f"{self.source_name}: country '{country}' is missing from "
                f"values.{indicator} (available: {sorted(by_country.keys())[:10]}...)")
        series = by_country[country]
        df = pd.DataFrame({
            "period": [pd.Timestamp(int(year), 1, 1) for year in series],
            "value": [float(v) for v in series.values()],
        })
        return df.sort_values("period").reset_index(drop=True)
