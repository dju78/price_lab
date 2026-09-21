"""World Bank connector tests. Happy path replays a real recorded response
(see tests/fixtures/connectors/worldbank_response.json)."""

from pathlib import Path

import pytest
import requests
import responses

from pricelab.data.connectors.base import (
    ConnectorRateLimitError,
    ConnectorSchemaError,
    ConnectorTimeoutError,
)
from pricelab.data.connectors.worldbank import BASE_URL, WorldBankConnector

FIXTURE = Path(__file__).parent / "fixtures" / "connectors" / "worldbank_response.json"
URL = f"{BASE_URL}/US/indicator/FP.CPI.TOTL"


def _connector(**kwargs):
    return WorldBankConnector(sleep_fn=lambda s: None, max_retries=1, backoff_base_seconds=0.01,
                              **kwargs)


@responses.activate
def test_happy_path_against_recorded_fixture():
    responses.add(responses.GET, URL, body=FIXTURE.read_text(), status=200)
    conn = _connector()

    result = conn.fetch(country="US", indicator="FP.CPI.TOTL", use_cache=False)

    assert len(result.data) == 9  # the null 2025 observation is dropped
    assert result.vintage.source == "worldbank"


@responses.activate
def test_timeout():
    responses.add(responses.GET, URL, body=requests.exceptions.Timeout())
    conn = _connector()
    with pytest.raises(ConnectorTimeoutError):
        conn.fetch(country="US", indicator="FP.CPI.TOTL", use_cache=False)


@responses.activate
def test_rate_limit():
    responses.add(responses.GET, URL, status=429, headers={"Retry-After": "0.01"})
    conn = _connector()
    with pytest.raises(ConnectorRateLimitError):
        conn.fetch(country="US", indicator="FP.CPI.TOTL", use_cache=False)


@responses.activate
def test_malformed_payload():
    responses.add(responses.GET, URL, body="[not json", status=200, content_type="application/json")
    conn = _connector()
    with pytest.raises(ConnectorSchemaError):
        conn.fetch(country="US", indicator="FP.CPI.TOTL", use_cache=False)


@responses.activate
def test_schema_change_not_a_two_element_list():
    responses.add(responses.GET, URL, json={"message": "unexpected shape now"}, status=200)
    conn = _connector()
    with pytest.raises(ConnectorSchemaError, match="2-element"):
        conn.fetch(country="US", indicator="FP.CPI.TOTL", use_cache=False)


@responses.activate
def test_schema_change_null_observations():
    """The real World Bank API's own shape for "no data at all"."""
    responses.add(responses.GET, URL, json=[{"page": 1}, None], status=200)
    conn = _connector()
    with pytest.raises(ConnectorSchemaError, match="null"):
        conn.fetch(country="US", indicator="FP.CPI.TOTL", use_cache=False)
