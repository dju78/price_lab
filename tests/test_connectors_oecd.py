"""OECD connector tests. Happy path replays a real recorded response (see
tests/fixtures/connectors/oecd_response.json), trimmed from a genuine live
call against OECD's current sdmx.oecd.org endpoint."""

from pathlib import Path

import pytest
import requests
import responses

from pricelab.data.connectors.base import (
    ConnectorRateLimitError,
    ConnectorSchemaError,
    ConnectorTimeoutError,
)
from pricelab.data.connectors.oecd import BASE_URL, OECDConnector

FIXTURE = Path(__file__).parent / "fixtures" / "connectors" / "oecd_response.json"
DATASET = "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0"
FILTER = "USA.M.N.CPI.PA._T.N.GY"
URL = f"{BASE_URL}/{DATASET}/{FILTER}"


def _connector(**kwargs):
    return OECDConnector(sleep_fn=lambda s: None, max_retries=1, backoff_base_seconds=0.01,
                         **kwargs)


@responses.activate
def test_happy_path_against_recorded_fixture():
    responses.add(responses.GET, URL, body=FIXTURE.read_text(), status=200)
    conn = _connector()

    result = conn.fetch(dataset=DATASET, filter_expr=FILTER, use_cache=False)

    assert len(result.data) == 24
    assert result.vintage.source == "oecd"


@responses.activate
def test_timeout():
    responses.add(responses.GET, URL, body=requests.exceptions.Timeout())
    conn = _connector()
    with pytest.raises(ConnectorTimeoutError):
        conn.fetch(dataset=DATASET, filter_expr=FILTER, use_cache=False)


@responses.activate
def test_rate_limit():
    responses.add(responses.GET, URL, status=429, headers={"Retry-After": "0.01"})
    conn = _connector()
    with pytest.raises(ConnectorRateLimitError):
        conn.fetch(dataset=DATASET, filter_expr=FILTER, use_cache=False)


@responses.activate
def test_malformed_payload():
    responses.add(responses.GET, URL, body="{{broken", status=200, content_type="application/json")
    conn = _connector()
    with pytest.raises(ConnectorSchemaError):
        conn.fetch(dataset=DATASET, filter_expr=FILTER, use_cache=False)


@responses.activate
def test_schema_change_missing_structure():
    responses.add(responses.GET, URL, json={"data": {"dataSets": [{"series": {"0": {}}}]}},
                  status=200)
    conn = _connector()
    with pytest.raises(ConnectorSchemaError, match="structure"):
        conn.fetch(dataset=DATASET, filter_expr=FILTER, use_cache=False)


@responses.activate
def test_schema_change_missing_time_period_dimension():
    broken = {"data": {
        "dataSets": [{"series": {"0:0": {"observations": {"0": [1.0, 0]}}}}],
        "structure": {"dimensions": {"observation": [{"id": "SOMETHING_ELSE", "values": []}]}},
    }}
    responses.add(responses.GET, URL, json=broken, status=200)
    conn = _connector()
    with pytest.raises(ConnectorSchemaError, match="TIME_PERIOD"):
        conn.fetch(dataset=DATASET, filter_expr=FILTER, use_cache=False)
