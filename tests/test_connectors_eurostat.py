"""Eurostat connector tests. Happy path replays a real recorded response
(HICP annual rate of change, euro area, extracted from a genuine live
call -- see tests/fixtures/connectors/eurostat_response.json for
provenance); timeout and rate-limit reuse BaseConnector's already-tested
mechanics, so they only confirm the wiring holds for this connector's real
URL, not re-litigate the retry logic itself."""

from pathlib import Path

import pytest
import requests
import responses

from pricelab.data.connectors.base import (
    ConnectorRateLimitError,
    ConnectorSchemaError,
    ConnectorTimeoutError,
)
from pricelab.data.connectors.eurostat import BASE_URL, EurostatConnector

FIXTURE = Path(__file__).parent / "fixtures" / "connectors" / "eurostat_response.json"


def _connector(**kwargs):
    return EurostatConnector(sleep_fn=lambda s: None, max_retries=1,
                             backoff_base_seconds=0.01, **kwargs)


@responses.activate
def test_happy_path_against_recorded_fixture():
    responses.add(responses.GET, f"{BASE_URL}/prc_hicp_manr", body=FIXTURE.read_text(), status=200)
    conn = _connector()

    result = conn.fetch(dataset="prc_hicp_manr", geo="EA", coicop="CP00", use_cache=False)

    assert len(result.data) == 12
    assert str(result.data["period"].min().date()) == "2025-01-01"
    assert result.vintage.source == "eurostat"
    assert result.vintage.api_version == "2.0"


@responses.activate
def test_timeout():
    responses.add(responses.GET, f"{BASE_URL}/prc_hicp_manr", body=requests.exceptions.Timeout())
    conn = _connector()
    with pytest.raises(ConnectorTimeoutError):
        conn.fetch(dataset="prc_hicp_manr", use_cache=False)


@responses.activate
def test_rate_limit():
    responses.add(responses.GET, f"{BASE_URL}/prc_hicp_manr", status=429,
                  headers={"Retry-After": "0.01"})
    conn = _connector()
    with pytest.raises(ConnectorRateLimitError):
        conn.fetch(dataset="prc_hicp_manr", use_cache=False)


@responses.activate
def test_malformed_payload_is_not_valid_json():
    responses.add(responses.GET, f"{BASE_URL}/prc_hicp_manr", body="{not json",
                  status=200, content_type="application/json")
    conn = _connector()
    with pytest.raises(ConnectorSchemaError):
        conn.fetch(dataset="prc_hicp_manr", use_cache=False)


@responses.activate
def test_schema_change_missing_time_dimension():
    """Simulates Eurostat restructuring the response so the 'time'
    dimension moves or is renamed: the error must name what is missing."""
    broken = {"version": "2.0", "id": ["freq", "geo"], "size": [1, 1],
             "dimension": {"freq": {"category": {"index": {"M": 0}}},
                           "geo": {"category": {"index": {"EA": 0}}}},
             "value": {"0": 1.0}}
    responses.add(responses.GET, f"{BASE_URL}/prc_hicp_manr", json=broken, status=200)
    conn = _connector()
    with pytest.raises(ConnectorSchemaError, match="time"):
        conn.fetch(dataset="prc_hicp_manr", use_cache=False)


@responses.activate
def test_schema_change_missing_value_key():
    broken = {"version": "2.0", "id": ["time"], "size": [1],
             "dimension": {"time": {"category": {"index": {"2020-01": 0}}}}}
    responses.add(responses.GET, f"{BASE_URL}/prc_hicp_manr", json=broken, status=200)
    conn = _connector()
    with pytest.raises(ConnectorSchemaError, match="value"):
        conn.fetch(dataset="prc_hicp_manr", use_cache=False)
