"""Tests for BaseConnector's shared machinery: retry with backoff and
jitter, Retry-After-aware rate-limit handling, caching, and audit logging.
Every concrete connector inherits this untested-by-itself, so it is tested
thoroughly once here rather than duplicated per connector."""

from __future__ import annotations

import pandas as pd
import pytest
import requests
import responses

from pricelab.core import audit, db
from pricelab.core.cache import BoundedCache
from pricelab.core.config import get_settings
from pricelab.core.models import Role
from pricelab.core.security import create_user
from pricelab.data.connectors.base import (
    BaseConnector,
    ConnectorError,
    ConnectorHTTPError,
    ConnectorRateLimitError,
    ConnectorSchemaError,
    ConnectorTimeoutError,
)

URL = "https://example.test/api"


class DummyConnector(BaseConnector):
    source_name = "dummy"

    def _request(self, **kwargs):
        return URL, {"series": kwargs.get("series", "X")}

    def _validate(self, raw):
        if not isinstance(raw, dict) or "values" not in raw:
            raise ConnectorSchemaError("dummy: expected key 'values' in response")

    def _parse(self, raw, **kwargs):
        return pd.DataFrame({
            "period": pd.to_datetime(list(raw["values"].keys())),
            "value": list(raw["values"].values()),
        })


@pytest.fixture()
def no_sleep():
    calls = []
    return calls, (lambda seconds: calls.append(seconds))


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", f"sqlite:///{tmp_path / 'connectors_test.db'}")
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield
    db.reset_db_state()
    get_settings.cache_clear()


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------
@responses.activate
def test_happy_path_returns_parsed_data_and_a_vintage(no_sleep):
    _, sleep_fn = no_sleep
    responses.add(responses.GET, URL, json={"values": {"2020-01-01": 1.0, "2020-02-01": 2.0}},
                  status=200)
    conn = DummyConnector(sleep_fn=sleep_fn)

    result = conn.fetch(series="X", use_cache=False)

    assert list(result.data["value"]) == [1.0, 2.0]
    assert result.vintage.source == "dummy"
    assert result.vintage.response_hash
    assert result.from_cache is False


# ---------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------
@responses.activate
def test_timeout_retries_then_raises_a_typed_error(no_sleep):
    calls, sleep_fn = no_sleep
    responses.add(responses.GET, URL, body=requests.exceptions.Timeout())
    conn = DummyConnector(sleep_fn=sleep_fn, max_retries=2, backoff_base_seconds=0.1)

    with pytest.raises(ConnectorTimeoutError, match="timed out after 3 attempts"):
        conn.fetch(series="T", use_cache=False)

    assert len(responses.calls) == 3  # 1 initial + 2 retries
    assert len(calls) == 2            # slept between attempts, not after the last


@responses.activate
def test_timeout_followed_by_success_recovers(no_sleep):
    _, sleep_fn = no_sleep
    responses.add(responses.GET, URL, body=requests.exceptions.Timeout())
    responses.add(responses.GET, URL, json={"values": {"2020-01-01": 9.0}}, status=200)
    conn = DummyConnector(sleep_fn=sleep_fn, max_retries=2, backoff_base_seconds=0.01)

    result = conn.fetch(series="T2", use_cache=False)
    assert list(result.data["value"]) == [9.0]


# ---------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------
@responses.activate
def test_rate_limit_honours_retry_after_then_succeeds(no_sleep):
    calls, sleep_fn = no_sleep
    responses.add(responses.GET, URL, status=429, headers={"Retry-After": "7"})
    responses.add(responses.GET, URL, json={"values": {"2020-01-01": 3.0}}, status=200)
    conn = DummyConnector(sleep_fn=sleep_fn, max_retries=2)

    result = conn.fetch(series="R", use_cache=False)

    assert list(result.data["value"]) == [3.0]
    assert calls == [7.0]  # slept exactly the server-specified duration, not a guessed backoff


@responses.activate
def test_rate_limit_exhausted_raises_a_typed_error(no_sleep):
    _, sleep_fn = no_sleep
    for _ in range(5):
        responses.add(responses.GET, URL, status=429, headers={"Retry-After": "0.01"})
    conn = DummyConnector(sleep_fn=sleep_fn, max_retries=2)

    with pytest.raises(ConnectorRateLimitError, match="rate limited after 3 attempts"):
        conn.fetch(series="R2", use_cache=False)


@responses.activate
def test_rate_limit_falls_back_to_backoff_without_retry_after_header(no_sleep):
    calls, sleep_fn = no_sleep
    responses.add(responses.GET, URL, status=429)  # no Retry-After header at all
    responses.add(responses.GET, URL, json={"values": {"2020-01-01": 1.0}}, status=200)
    conn = DummyConnector(sleep_fn=sleep_fn, max_retries=1, backoff_base_seconds=0.5,
                          jitter_fn=lambda lo, hi: 0.0)

    conn.fetch(series="R3", use_cache=False)
    assert calls == [0.5]  # backoff_base * 2**0 + zero jitter


# ---------------------------------------------------------------------
# Malformed payload / schema change
# ---------------------------------------------------------------------
@responses.activate
def test_malformed_payload_raises_schema_error_naming_the_field(no_sleep):
    _, sleep_fn = no_sleep
    responses.add(responses.GET, URL, json={"unexpected": "shape"}, status=200)
    conn = DummyConnector(sleep_fn=sleep_fn)

    with pytest.raises(ConnectorSchemaError, match="'values'"):
        conn.fetch(series="M", use_cache=False)


@responses.activate
def test_non_json_body_is_reported_not_crashed_on(no_sleep):
    _, sleep_fn = no_sleep
    responses.add(responses.GET, URL, body="not json at all {{{", status=200,
                  content_type="application/json")
    conn = DummyConnector(sleep_fn=sleep_fn)

    with pytest.raises(ConnectorError):
        conn.fetch(series="BADJSON", use_cache=False)


# ---------------------------------------------------------------------
# Ordinary HTTP errors (not rate limiting)
# ---------------------------------------------------------------------
@responses.activate
def test_http_error_raises_with_status_code(no_sleep):
    _, sleep_fn = no_sleep
    responses.add(responses.GET, URL, status=500)
    conn = DummyConnector(sleep_fn=sleep_fn, max_retries=0)

    with pytest.raises(ConnectorHTTPError) as exc_info:
        conn.fetch(series="E", use_cache=False)
    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------
@responses.activate
def test_cache_hit_makes_no_second_request(no_sleep):
    _, sleep_fn = no_sleep
    responses.add(responses.GET, URL, json={"values": {"2020-01-01": 1.0}}, status=200)
    cache: BoundedCache = BoundedCache(max_entries=4)
    conn = DummyConnector(sleep_fn=sleep_fn, cache=cache)

    first = conn.fetch(series="CACHED")
    second = conn.fetch(series="CACHED")

    assert len(responses.calls) == 1
    assert first.from_cache is False
    assert second.from_cache is True


@responses.activate
def test_different_query_parameters_are_different_cache_entries(no_sleep):
    _, sleep_fn = no_sleep
    responses.add(responses.GET, URL, json={"values": {"2020-01-01": 1.0}}, status=200)
    responses.add(responses.GET, URL, json={"values": {"2020-01-01": 2.0}}, status=200)
    cache: BoundedCache = BoundedCache(max_entries=4)
    conn = DummyConnector(sleep_fn=sleep_fn, cache=cache)

    a = conn.fetch(series="A")
    b = conn.fetch(series="B")

    assert len(responses.calls) == 2
    assert list(a.data["value"]) == [1.0]
    assert list(b.data["value"]) == [2.0]


@responses.activate
def test_use_cache_false_always_refetches(no_sleep):
    _, sleep_fn = no_sleep
    responses.add(responses.GET, URL, json={"values": {"2020-01-01": 1.0}}, status=200)
    responses.add(responses.GET, URL, json={"values": {"2020-01-01": 1.0}}, status=200)
    cache: BoundedCache = BoundedCache(max_entries=4)
    conn = DummyConnector(sleep_fn=sleep_fn, cache=cache)

    conn.fetch(series="F", use_cache=False)
    conn.fetch(series="F", use_cache=False)
    assert len(responses.calls) == 2


# ---------------------------------------------------------------------
# API keys and credentials are never leaked into the vintage/audit query
# ---------------------------------------------------------------------
def test_redact_query_hides_anything_key_or_token_shaped():
    conn = DummyConnector()
    redacted = conn._redact_query({
        "series": "X", "api_key": "SECRET123", "registrationKey": "ALSOSECRET",
        "access_token": "TOKEN", "format": "json",
    })
    assert redacted["series"] == "X"
    assert redacted["format"] == "json"
    assert redacted["api_key"] == "<redacted>"
    assert redacted["registrationKey"] == "<redacted>"
    assert redacted["access_token"] == "<redacted>"


# ---------------------------------------------------------------------
# Audit logging: every fetch, success or failure, is recorded
# ---------------------------------------------------------------------
@responses.activate
def test_successful_fetch_is_audited(no_sleep, fresh_db):
    _, sleep_fn = no_sleep
    responses.add(responses.GET, URL, json={"values": {"2020-01-01": 1.0}}, status=200)
    conn = DummyConnector(sleep_fn=sleep_fn)

    with db.session_scope() as s:
        create_user(s, "fetcher", "pw", Role.COMPILER)

    with db.session_scope() as s:
        conn.fetch(series="AUDITED", audit_session=s, actor="fetcher", use_cache=False)

    with db.session_scope() as s:
        events = s.query(audit.AuditEventORM).filter_by(
            action=audit.EXTERNAL_FETCH_SUCCESS).all()
        assert len(events) == 1
        assert events[0].actor == "fetcher"
        assert events[0].target == "dummy"


@responses.activate
def test_failed_fetch_is_also_audited(no_sleep, fresh_db):
    _, sleep_fn = no_sleep
    responses.add(responses.GET, URL, json={"broken": True}, status=200)
    conn = DummyConnector(sleep_fn=sleep_fn)

    with db.session_scope() as s:
        with pytest.raises(ConnectorSchemaError):
            conn.fetch(series="WILLFAIL", audit_session=s, actor="fetcher", use_cache=False)

    with db.session_scope() as s:
        events = s.query(audit.AuditEventORM).filter_by(
            action=audit.EXTERNAL_FETCH_FAILURE).all()
        assert len(events) == 1
        assert "values" in events[0].params_json


@responses.activate
def test_no_audit_session_means_no_audit_call_but_still_works(no_sleep):
    _, sleep_fn = no_sleep
    responses.add(responses.GET, URL, json={"values": {"2020-01-01": 1.0}}, status=200)
    conn = DummyConnector(sleep_fn=sleep_fn)
    result = conn.fetch(series="NOAUDIT", use_cache=False)
    assert list(result.data["value"]) == [1.0]
