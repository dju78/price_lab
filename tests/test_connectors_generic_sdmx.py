"""Generic SDMX 2.1 connector tests.

Reuses the same recorded Eurostat fixture the dedicated EurostatConnector
test uses, and asserts the generic connector -- configured with Eurostat's
base URL rather than having it built in -- produces an identical result.
That is the actual claim "adding an agency is configuration, not code"
makes, proven rather than only asserted.
"""

from pathlib import Path

import pandas as pd
import pytest
import requests
import responses

from pricelab.data.connectors.base import ConnectorRateLimitError, ConnectorTimeoutError
from pricelab.data.connectors.eurostat import BASE_URL, EurostatConnector
from pricelab.data.connectors.generic_sdmx import GenericSDMXConnector

FIXTURE = Path(__file__).parent / "fixtures" / "connectors" / "eurostat_response.json"


def _generic(**kwargs):
    return GenericSDMXConnector(
        name="eurostat_via_generic", base_url=BASE_URL,
        sleep_fn=lambda s: None, max_retries=1, backoff_base_seconds=0.01, **kwargs)


@responses.activate
def test_generic_connector_matches_the_dedicated_eurostat_connector():
    responses.add(responses.GET, f"{BASE_URL}/prc_hicp_manr", body=FIXTURE.read_text(), status=200)
    dedicated = EurostatConnector(sleep_fn=lambda s: None)
    dedicated_result = dedicated.fetch(dataset="prc_hicp_manr", use_cache=False)

    responses.add(responses.GET, f"{BASE_URL}/prc_hicp_manr", body=FIXTURE.read_text(), status=200)
    generic = _generic()
    generic_result = generic.fetch(dataset="prc_hicp_manr", use_cache=False)

    pd.testing.assert_frame_equal(dedicated_result.data, generic_result.data)
    assert generic_result.vintage.source == "eurostat_via_generic"


@responses.activate
def test_timeout():
    responses.add(responses.GET, f"{BASE_URL}/prc_hicp_manr", body=requests.exceptions.Timeout())
    conn = _generic()
    with pytest.raises(ConnectorTimeoutError):
        conn.fetch(dataset="prc_hicp_manr", use_cache=False)


@responses.activate
def test_rate_limit():
    responses.add(responses.GET, f"{BASE_URL}/prc_hicp_manr", status=429,
                  headers={"Retry-After": "0.01"})
    conn = _generic()
    with pytest.raises(ConnectorRateLimitError):
        conn.fetch(dataset="prc_hicp_manr", use_cache=False)


@responses.activate
def test_a_second_agency_is_pure_configuration():
    """Pointing the same class at a different base URL is the entire
    integration for a second agency -- no new module, no new class."""
    other_base = "https://example-agency.test/sdmx/data"
    responses.add(responses.GET, f"{other_base}/SOME_DATASET", body=FIXTURE.read_text(),
                  status=200)
    conn = GenericSDMXConnector(name="example_agency", base_url=other_base,
                                sleep_fn=lambda s: None)
    result = conn.fetch(dataset="SOME_DATASET", use_cache=False)
    assert result.vintage.source == "example_agency"
    assert len(result.data) == 12
