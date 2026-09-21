"""World Bank Indicators API. Verified live against
`api.worldbank.org/v2/country/{country}/indicator/{indicator}`.

The response is a two-element list: a metadata object (page/total/etc.)
followed by the list of observations. A country-indicator combination with
no data at all comes back as `[metadata, None]` rather than `[metadata,
[]]`, which `_validate` treats as a schema condition worth naming rather
than letting `_parse` crash on `None` being unsubscriptable.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .base import BaseConnector, ConnectorSchemaError

BASE_URL = "https://api.worldbank.org/v2/country"


class WorldBankConnector(BaseConnector):
    source_name = "worldbank"

    def _request(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        country = kwargs["country"]
        indicator = kwargs["indicator"]
        return (f"{BASE_URL}/{country}/indicator/{indicator}",
                {"format": "json", "per_page": kwargs.get("per_page", 1000)})

    def _validate(self, raw: Any) -> None:
        if not isinstance(raw, list) or len(raw) != 2:
            raise ConnectorSchemaError(
                f"{self.source_name}: expected a 2-element [metadata, observations] list")
        if raw[1] is None:
            raise ConnectorSchemaError(
                f"{self.source_name}: no data for this country/indicator combination "
                f"(observations element is null)")
        if not isinstance(raw[1], list):
            raise ConnectorSchemaError(
                f"{self.source_name}: 'observations' element is not a list")
        if raw[1] and not {"date", "value"} <= raw[1][0].keys():
            missing = {"date", "value"} - raw[1][0].keys()
            raise ConnectorSchemaError(
                f"{self.source_name}: observation rows are missing {sorted(missing)}")

    def _parse(self, raw: Any, **kwargs: Any) -> pd.DataFrame:
        observations = raw[1]
        df = pd.DataFrame({
            "period": [pd.Timestamp(int(o["date"]), 1, 1) for o in observations],
            "value": [o["value"] for o in observations],
        })
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.dropna(subset=["value"]).sort_values("period").reset_index(drop=True)
