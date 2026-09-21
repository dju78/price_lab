"""ONS connector tests. Happy path replays a real recorded response from
the beta API (see tests/fixtures/connectors/ons_response.json)."""

from pathlib import Path

import pytest
import requests
import responses

from pricelab.data.connectors.base import (
    ConnectorRateLimitError,
    ConnectorSchemaError,
    ConnectorTimeoutError,
)
from pricelab.data.connectors.ons import BASE_URL, ONSConnector

FIXTURE = Path(__file__).parent / "fixtures" / "connectors" / "ons_response.json"
SERIES_URI = "/economy/inflationandpriceindices/timeseries/d7g7/mm23"


def _connector(**kwargs):
    return ONSConnector(sleep_fn=lambda s: None, max_retries=1, backoff_base_seconds=0.01, **kwargs)


@responses.activate
def test_happy_path_against_recorded_fixture():
    responses.add(responses.GET, BASE_URL, body=FIXTURE.read_text(), status=200)
    conn = _connector()

    result = conn.fetch(series_uri=SERIES_URI, use_cache=False)

    assert len(result.data) == 24
    assert result.vintage.source == "ons"
    assert result.vintage.api_version == "timeseries"


@responses.activate
def test_timeout():
    responses.add(responses.GET, BASE_URL, body=requests.exceptions.Timeout())
    conn = _connector()
    with pytest.raises(ConnectorTimeoutError):
        conn.fetch(series_uri=SERIES_URI, use_cache=False)


@responses.activate
def test_rate_limit():
    responses.add(responses.GET, BASE_URL, status=429, headers={"Retry-After": "0.01"})
    conn = _connector()
    with pytest.raises(ConnectorRateLimitError):
        conn.fetch(series_uri=SERIES_URI, use_cache=False)


@responses.activate
def test_malformed_payload():
    responses.add(responses.GET, BASE_URL, body="<html>not json</html>", status=200,
                  content_type="application/json")
    conn = _connector()
    with pytest.raises(ConnectorSchemaError):
        conn.fetch(series_uri=SERIES_URI, use_cache=False)


@responses.activate
def test_schema_change_no_time_granularity_arrays():
    """Simulates ONS restructuring the response so none of
    years/quarters/months are present any more."""
    responses.add(responses.GET, BASE_URL, json={"type": "timeseries", "description": {}},
                  status=200)
    conn = _connector()
    with pytest.raises(ConnectorSchemaError, match="months"):
        conn.fetch(series_uri=SERIES_URI, use_cache=False)


@responses.activate
def test_schema_change_rows_missing_expected_fields():
    responses.add(responses.GET, BASE_URL,
                  json={"months": [{"date": "2020 JAN"}]},  # no "value", no "year"
                  status=200)
    conn = _connector()
    with pytest.raises(ConnectorSchemaError, match="months"):
        conn.fetch(series_uri=SERIES_URI, use_cache=False)


@responses.activate
def test_legacy_decommissioned_endpoint_is_not_used():
    """Documents the friction this connector was built around: the legacy
    timeseries API this connector deliberately does not call."""
    assert "beta.ons.gov.uk" in BASE_URL
    assert BASE_URL != "https://api.ons.gov.uk/timeseries"
