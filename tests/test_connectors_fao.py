"""FAO connector tests. Happy path replays a real recorded CSV response
(see tests/fixtures/connectors/fao_response.csv) -- the one connector in
this set whose source is CSV rather than JSON."""

from pathlib import Path

import pytest
import requests
import responses

from pricelab.data.connectors.base import (
    ConnectorRateLimitError,
    ConnectorSchemaError,
    ConnectorTimeoutError,
)
from pricelab.data.connectors.fao import URL, FAOConnector

FIXTURE = Path(__file__).parent / "fixtures" / "connectors" / "fao_response.csv"


def _connector(**kwargs):
    return FAOConnector(sleep_fn=lambda s: None, max_retries=1, backoff_base_seconds=0.01, **kwargs)


@responses.activate
def test_happy_path_against_recorded_fixture():
    responses.add(responses.GET, URL, body=FIXTURE.read_text(encoding="utf-8"), status=200,
                  content_type="text/csv")
    conn = _connector()

    result = conn.fetch(use_cache=False)

    assert len(result.data) == 30
    assert result.vintage.source == "fao"


@responses.activate
def test_a_different_sub_index_column_can_be_requested():
    responses.add(responses.GET, URL, body=FIXTURE.read_text(encoding="utf-8"), status=200,
                  content_type="text/csv")
    conn = _connector()
    result = conn.fetch(index="Meat Price Index", use_cache=False)
    assert len(result.data) == 30


@responses.activate
def test_timeout():
    responses.add(responses.GET, URL, body=requests.exceptions.Timeout())
    conn = _connector()
    with pytest.raises(ConnectorTimeoutError):
        conn.fetch(use_cache=False)


@responses.activate
def test_rate_limit():
    responses.add(responses.GET, URL, status=429, headers={"Retry-After": "0.01"})
    conn = _connector()
    with pytest.raises(ConnectorRateLimitError):
        conn.fetch(use_cache=False)


@responses.activate
def test_malformed_payload_not_a_csv_at_all():
    responses.add(responses.GET, URL, body="<html>Page not found</html>", status=200,
                  content_type="text/html")
    conn = _connector()
    with pytest.raises(ConnectorSchemaError):
        conn.fetch(use_cache=False)


@responses.activate
def test_schema_change_missing_requested_column():
    csv_text = "MONTHLY FOOD PRICE INDICES\n,\nDate,Some Other Column\nJan-90,1.0\n"
    responses.add(responses.GET, URL, body=csv_text, status=200, content_type="text/csv")
    conn = _connector()
    with pytest.raises(ConnectorSchemaError, match="Food Price Index"):
        conn.fetch(use_cache=False)
