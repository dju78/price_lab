"""FAO Food Price Index.

FAOSTAT's own REST API (`fenixservices.fao.org/faostat/api`) did not
respond from this environment (connection failure, not a timeout). The
Food Price Index itself is published as a plain CSV at a stable URL linked
from FAO's World Food Situation page, verified live; that CSV, not the
FAOSTAT API, is what this connector reads. It is the one connector in this
set that returns CSV rather than JSON, so it overrides `_decode` to hand
`_parse` the raw response text.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import pandas as pd
import requests

from .base import BaseConnector, ConnectorSchemaError

URL = ("https://www.fao.org/fileadmin/templates/worldfood/"
       "Reports_and_docs/Food_price_indices_data.csv")

_EXPECTED_HEADER = "Date"


class FAOConnector(BaseConnector):
    source_name = "fao"

    def _request(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        return URL, {}

    def _decode(self, response: requests.Response) -> Any:
        return response.text

    def _validate(self, raw: Any) -> None:
        if not isinstance(raw, str) or _EXPECTED_HEADER not in raw:
            raise ConnectorSchemaError(
                f"{self.source_name}: expected a CSV containing a '{_EXPECTED_HEADER}' "
                "header row; the file this connector reads may have changed shape")

    def _parse(self, raw: Any, **kwargs: Any) -> pd.DataFrame:
        rows = list(csv.reader(io.StringIO(raw)))
        header_idx = next(i for i, r in enumerate(rows) if r and r[0] == _EXPECTED_HEADER)
        header = rows[header_idx]
        data_rows = [r for r in rows[header_idx + 1:] if r and r[0].strip()]
        df = pd.DataFrame(data_rows, columns=header)

        value_col = kwargs.get("index", "Food Price Index")
        if value_col not in df.columns:
            raise ConnectorSchemaError(
                f"{self.source_name}: column '{value_col}' not found "
                f"(available: {list(df.columns)})")

        out = pd.DataFrame({
            "period": pd.to_datetime(df["Date"], format="%b-%y", errors="coerce"),
            "value": pd.to_numeric(df[value_col], errors="coerce"),
        })
        return out.dropna(subset=["period", "value"]).sort_values("period").reset_index(drop=True)
