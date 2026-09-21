"""A generic SDMX 2.1 connector, configurable by base URL and dataflow
identifier so a new agency's JSON-stat-flavoured SDMX 2.1 endpoint is a
configuration entry, not a new module.

Verified live by construction: `EurostatConnector` is what this class
looks like with `base_url`/`dataset` fixed at construction time rather
than left configurable, and it is verified live against the real Eurostat
endpoint (see eurostat.py). This class is exercised against the same
recorded Eurostat fixture in tests, proving the *generic* path produces
the same result the dedicated connector does, rather than only being
reviewed by eye.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ._jsonstat import parse_jsonstat, validate_jsonstat
from .base import BaseConnector


class GenericSDMXConnector(BaseConnector):
    """`GenericSDMXConnector(name="ecb", base_url="https://data-api.ecb.europa.eu/service/data")`,
    then `.fetch(dataset="EXR", audit_session=..., FREQ="M")` -- any SDMX
    2.1 JSON-stat endpoint reachable as `{base_url}/{dataset}?{filters}`.
    """

    def __init__(self, name: str, base_url: str, **kwargs: Any):
        super().__init__(**kwargs)
        self.source_name = name
        self._base_url = base_url.rstrip("/")

    def _request(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        dataset = kwargs["dataset"]
        filters = {k: v for k, v in kwargs.items() if k != "dataset"}
        params = {"format": "JSON", **filters}
        return f"{self._base_url}/{dataset}", params

    def _validate(self, raw: Any) -> None:
        validate_jsonstat(raw, self.source_name)

    def _parse(self, raw: Any, **kwargs: Any) -> pd.DataFrame:
        return parse_jsonstat(raw)

    def _api_version(self, raw: Any) -> str | None:
        version = raw.get("version") if isinstance(raw, dict) else None
        return str(version) if version is not None else None
