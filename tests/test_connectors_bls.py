"""BLS connector tests. Happy path replays a real recorded response,
fetched live *without* a registration key (see
tests/fixtures/connectors/bls_response.json)."""

from pathlib import Path

import pytest
import requests
import responses

from pricelab.data.connectors.base import (
    ConnectorError,
    ConnectorRateLimitError,
    ConnectorSchemaError,
    ConnectorTimeoutError,
)
from pricelab.data.connectors.bls import BASE_URL, BLSConnector

FIXTURE = Path(__file__).parent / "fixtures" / "connectors" / "bls_response.json"
SERIES_ID = "CUUR0000SA0"
URL = f"{BASE_URL}/{SERIES_ID}"


def _connector(**kwargs):
    return BLSConnector(sleep_fn=lambda s: None, max_retries=1, backoff_base_seconds=0.01, **kwargs)


@responses.activate
def test_happy_path_against_recorded_fixture_fetched_without_a_key():
    responses.add(responses.GET, URL, body=FIXTURE.read_text(), status=200)
    conn = _connector()

    result = conn.fetch(series_id=SERIES_ID, use_cache=False)

    assert len(result.data) == 31  # the "-" (lapse-in-appropriations) row is dropped
    assert result.vintage.source == "bls"


@responses.activate
def test_api_key_is_included_when_supplied_and_redacted_in_the_vintage():
    responses.add(responses.GET, URL, body=FIXTURE.read_text(), status=200)
    conn = _connector()

    result = conn.fetch(series_id=SERIES_ID, api_key="REALSECRETKEY", use_cache=False)

    assert "registrationkey=REALSECRETKEY" in responses.calls[0].request.url
    assert "REALSECRETKEY" not in result.vintage.query


@responses.activate
def test_timeout():
    responses.add(responses.GET, URL, body=requests.exceptions.Timeout())
    conn = _connector()
    with pytest.raises(ConnectorTimeoutError):
        conn.fetch(series_id=SERIES_ID, use_cache=False)


@responses.activate
def test_rate_limit():
    responses.add(responses.GET, URL, status=429, headers={"Retry-After": "0.01"})
    conn = _connector()
    with pytest.raises(ConnectorRateLimitError):
        conn.fetch(series_id=SERIES_ID, use_cache=False)


@responses.activate
def test_malformed_payload():
    responses.add(responses.GET, URL, body="not json", status=200, content_type="application/json")
    conn = _connector()
    with pytest.raises(ConnectorSchemaError):
        conn.fetch(series_id=SERIES_ID, use_cache=False)


@responses.activate
def test_schema_change_missing_results():
    responses.add(responses.GET, URL, json={"status": "REQUEST_SUCCEEDED"}, status=200)
    conn = _connector()
    with pytest.raises(ConnectorSchemaError, match="Results"):
        conn.fetch(series_id=SERIES_ID, use_cache=False)


@responses.activate
def test_request_not_processed_is_a_connector_error_not_a_schema_error():
    """A structurally valid response BLS could not fulfil (bad series ID,
    quota exceeded) is a request failure, not a shape change."""
    responses.add(responses.GET, URL, json={
        "status": "REQUEST_NOT_PROCESSED",
        "message": ["Series does not exist"],
        "Results": {"series": [{"seriesID": SERIES_ID, "data": []}]},
    }, status=200)
    conn = _connector()
    with pytest.raises(ConnectorError, match="Series does not exist") as exc_info:
        conn.fetch(series_id=SERIES_ID, use_cache=False)
    assert not isinstance(exc_info.value, ConnectorSchemaError)
