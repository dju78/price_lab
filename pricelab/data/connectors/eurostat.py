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
