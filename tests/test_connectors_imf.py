"""IMF (DataMapper) connector tests. Happy path replays a real recorded
response (see tests/fixtures/connectors/imf_response.json), trimmed to the
USA series from a genuine live call."""

from pathlib import Path

import pytest
import requests
import responses

from pricelab.data.connectors.base import (
    ConnectorRateLimitError,
    ConnectorSchemaError,
    ConnectorTimeoutError,
)
from pricelab.data.connectors.imf import BASE_URL, IMFConnector

FIXTURE = Path(__file__).parent / "fixtures" / "connectors" / "imf_response.json"
URL = f"{BASE_URL}/PCPIPCH/USA"


def _connector(**kwargs):
    return IMFConnector(sleep_fn=lambda s: None, max_retries=1, backoff_base_seconds=0.01, **kwargs)


@responses.activate
def test_happy_path_against_recorded_fixture():
    responses.add(responses.GET, URL, body=FIXTURE.read_text(), status=200)
    conn = _connector()

    result = conn.fetch(indicator="PCPIPCH", country="USA", use_cache=False)

    assert len(result.data) > 0
    assert result.vintage.source == "imf"


@responses.activate
def test_timeout():
    responses.add(responses.GET, URL, body=requests.exceptions.Timeout())
    conn = _connector()
    with pytest.raises(ConnectorTimeoutError):
        conn.fetch(indicator="PCPIPCH", country="USA", use_cache=False)


@responses.activate
def test_rate_limit():
    responses.add(responses.GET, URL, status=429, headers={"Retry-After": "0.01"})
    conn = _connector()
    with pytest.raises(ConnectorRateLimitError):
        conn.fetch(indicator="PCPIPCH", country="USA", use_cache=False)


@responses.activate
def test_malformed_payload():
    responses.add(responses.GET, URL, body="not json", status=200, content_type="application/json")
    conn = _connector()
    with pytest.raises(ConnectorSchemaError):
        conn.fetch(indicator="PCPIPCH", country="USA", use_cache=False)


@responses.activate
def test_schema_change_missing_values_key():
    responses.add(responses.GET, URL, json={"api": {}}, status=200)
    conn = _connector()
    with pytest.raises(ConnectorSchemaError, match="'values'"):
        conn.fetch(indicator="PCPIPCH", country="USA", use_cache=False)


@responses.activate
def test_schema_change_missing_country():
    responses.add(responses.GET, URL, json={"values": {"PCPIPCH": {"GBR": {"2020": 1.0}}}},
                  status=200)
    conn = _connector()
    with pytest.raises(ConnectorSchemaError, match="USA"):
        conn.fetch(indicator="PCPIPCH", country="USA", use_cache=False)


@responses.activate
def test_classic_ifs_endpoint_is_not_used():
    """Documents the friction this connector was built around: the classic
    dataservices.imf.org endpoint refused the connection outright when
    tested live, matching this phase's warning about IMF's intermittent
    availability."""
    assert "dataservices.imf.org" not in BASE_URL
    assert "datamapper" in BASE_URL
