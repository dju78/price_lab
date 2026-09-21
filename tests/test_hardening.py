"""Production hardening: upload rate limiting, the session idle timeout
enforced end to end, structured logging with one correlation id per run,
the database statement timeout, graceful degradation on an external API
outage, and the health probes.
"""

from __future__ import annotations

import io
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
import requests
import responses
from dbtarget import OVERRIDE, database_url, postgres_only
from sqlalchemy import text
from streamlit.testing.v1 import AppTest

from pricelab import run_pipeline
from pricelab.core import audit, db, health, ratelimit
from pricelab.core.cache import BoundedCache
from pricelab.core.config import IndexConfig, RunConfig, get_settings
from pricelab.core.logging import JsonFormatter, configure_logging, correlation_id, run_context
from pricelab.core.models import Role
from pricelab.core.security import SessionORM, create_session, create_user
from pricelab.data.connectors.base import BaseConnector, ConnectorSchemaError, ConnectorTimeoutError

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = str(REPO_ROOT / "app.py")


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "hardening.db"))
    monkeypatch.setenv("PRICELAB_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


# ---------------------------------------------------------------------
# Rate limiting on upload
# ---------------------------------------------------------------------
def test_the_upload_limiter_refuses_the_next_upload_and_says_how_long_to_wait():
    clock = {"t": 1000.0}
    limiter = ratelimit.SlidingWindowLimiter(limit=3, window_seconds=60, clock=lambda: clock["t"])
    for _ in range(3):
        limiter.acquire("alice")
    with pytest.raises(ratelimit.RateLimited) as excinfo:
        limiter.acquire("alice")
    assert 59 <= excinfo.value.wait_seconds <= 60
    assert "3 uploads" in str(excinfo.value)
    limiter.acquire("bob")                       # another user is unaffected
    clock["t"] += 61
    limiter.acquire("alice")                     # the window has slid
    assert limiter.wait_time("alice") == 0.0


def test_the_ingest_page_refuses_an_upload_over_the_limit_before_reading_it(fresh_db, monkeypatch):
    """`validate_upload` and the limiter both run before `getvalue()`; the
    page path is exercised through the limiter the page actually uses."""
    monkeypatch.setenv("PRICELAB_UPLOAD_RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()
    ratelimit.reset_upload_limiter()
    limiter = ratelimit.upload_limiter()
    assert limiter.limit == 1
    limiter.acquire("compiler1")
    with pytest.raises(ratelimit.RateLimited):
        limiter.acquire("compiler1")
    ratelimit.reset_upload_limiter()


# ---------------------------------------------------------------------
# Session timeout, enforced by the application not just the function
# ---------------------------------------------------------------------
def test_an_idle_session_past_the_timeout_lands_on_the_sign_in_form(fresh_db):
    with db.session_scope() as s:
        user = create_user(s, "sleepy", "password123", Role.COMPILER)
        token = create_session(s, user)
        record = s.query(SessionORM).filter_by(token=token).one()
        record.last_seen_at = (datetime.now(UTC) - timedelta(
            minutes=get_settings().session_timeout_minutes + 5)).isoformat()
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["pricelab_session_token"] = token
    at.run()
    assert not at.exception
    text_ = "\n".join(m.value for m in at.markdown)
    assert "Ingest and compile" not in text_
    assert any("Username" in (w.label or "") for w in at.text_input), "sign-in form expected"
    with db.session_scope() as s:
        assert s.query(SessionORM).filter_by(token=token).one_or_none() is None, "expired session deleted"


# ---------------------------------------------------------------------
# Structured logging with a correlation id per run
# ---------------------------------------------------------------------
def test_every_log_line_of_a_run_carries_its_correlation_id_as_json():
    stream = io.StringIO()
    handler = configure_logging(force=True, stream=stream)
    assert isinstance(handler.formatter, JsonFormatter)
    periods = pd.date_range("2020-01-01", periods=4, freq="MS")
    df = pd.DataFrame({"period": list(periods) * 2, "category": ["x"] * 8,
                       "item_id": ["a"] * 4 + ["b"] * 4, "item_name": ["a"] * 4 + ["b"] * 4,
                       "price_reported": [1, 1.1, 1.2, 1.3, 2, 2.1, 2.2, 2.3]})
    res = run_pipeline(df, RunConfig(index=IndexConfig(min_matched_items=1)))
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    assert len(lines) >= 4
    ids = {line["correlation_id"] for line in lines}
    assert ids == {res["correlation_id"]}
    assert {line["message"] for line in lines} >= {"pipeline.start", "pipeline.index", "pipeline.done"}
    assert all({"ts", "level", "logger", "message"} <= set(line) for line in lines)
    assert correlation_id.get() is None            # reset after the run


def test_a_nested_run_keeps_the_outer_correlation_id():
    with run_context("outer") as outer:
        with run_context() as inner:
            assert inner == outer
        logging.getLogger("t").info("x")


# ---------------------------------------------------------------------
# Database: statement timeout and pooling settings
# ---------------------------------------------------------------------
def test_a_runaway_sqlite_statement_is_aborted_at_the_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", f"sqlite:///{tmp_path / 'timeout.db'}")
    monkeypatch.setenv("PRICELAB_DB_STATEMENT_TIMEOUT_MS", "150")
    get_settings.cache_clear()
    db.reset_db_state()
    try:
        started = time.monotonic()
        with pytest.raises(Exception, match="interrupted"), db.get_engine().connect() as conn:
            conn.execute(text(
                "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 100000000) "
                "SELECT count(*) FROM r")).scalar()
        assert time.monotonic() - started < 5.0
        with db.get_engine().connect() as conn:
            assert conn.execute(text("select 1")).scalar() == 1   # the connection survives
    finally:
        db.reset_db_state()
        get_settings.cache_clear()


def test_a_runaway_postgresql_statement_is_aborted_by_statement_timeout(monkeypatch):
    """The production path: `statement_timeout` set per connection at
    connect time, through the pooled engine."""
    postgres_only()
    monkeypatch.setenv("PRICELAB_DATABASE_URL", OVERRIDE)
    monkeypatch.setenv("PRICELAB_DB_STATEMENT_TIMEOUT_MS", "200")
    monkeypatch.setenv("PRICELAB_DB_POOL_SIZE", "2")
    get_settings.cache_clear()
    db.reset_db_state()
    try:
        engine = db.get_engine()
        assert engine.pool.size() == 2
        started = time.monotonic()
        with pytest.raises(Exception, match="statement timeout"), engine.connect() as conn:
            conn.execute(text("select pg_sleep(5)")).scalar()
        assert time.monotonic() - started < 5.0
        with engine.connect() as conn:
            assert conn.execute(text("select 1")).scalar() == 1
            assert conn.execute(text("show statement_timeout")).scalar() == "200ms"
    finally:
        db.reset_db_state()
        get_settings.cache_clear()


def test_pool_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("PRICELAB_DB_POOL_SIZE", "7")
    monkeypatch.setenv("PRICELAB_DB_POOL_TIMEOUT_SECONDS", "2.5")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.db_pool_size == 7 and s.db_pool_timeout_seconds == 2.5
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------
# Graceful degradation when an external API is unavailable
# ---------------------------------------------------------------------
URL = "https://example.test/api"


class Dummy(BaseConnector):
    source_name = "dummy"

    def _request(self, **kwargs):
        return URL, {"series": "X"}

    def _validate(self, raw):
        if "values" not in raw:
            raise ConnectorSchemaError("dummy: expected 'values'")

    def _parse(self, raw, **kwargs):
        return pd.DataFrame({"period": pd.to_datetime(list(raw["values"])),
                             "value": list(raw["values"].values())})


@responses.activate
def test_an_outage_serves_the_last_good_response_with_its_age_stated(fresh_db):
    clock = {"t": 0.0}
    cache: BoundedCache = BoundedCache(max_entries=8, clock=lambda: clock["t"])
    connector = Dummy(cache=cache, cache_ttl_seconds=60, max_retries=0, sleep_fn=lambda s: None)
    responses.add(responses.GET, URL, json={"values": {"2020-01-01": 1.0}})
    with db.session_scope() as s:
        live = connector.fetch(audit_session=s, actor="analyst1")
    assert not live.stale and not live.from_cache

    clock["t"] += 3600                      # the fresh cache entry has expired...
    responses.reset()
    responses.add(responses.GET, URL, body=requests.exceptions.Timeout())   # ...and the API is down
    with db.session_scope() as s:
        degraded = connector.fetch(audit_session=s, actor="analyst1")
    assert degraded.stale and degraded.from_cache
    assert degraded.age is not None and degraded.age >= timedelta(0)
    assert "live fetch failed" in degraded.message and "timed out" in degraded.message
    assert "may be out of date" in degraded.message
    pd.testing.assert_frame_equal(degraded.data, live.data)

    with db.session_scope() as s:
        failure = [e for e in s.query(audit.AuditEventORM).all()
                   if e.action == audit.EXTERNAL_FETCH_FAILURE]
    assert failure and json.loads(failure[-1].params_json)["served_stale"] is True

    # Without a last good response the outage is an error, as before.
    with pytest.raises(ConnectorTimeoutError):
        Dummy(cache=BoundedCache(max_entries=2), max_retries=0, sleep_fn=lambda s: None).fetch()
    # And a caller that insists on live data can opt out of stale service.
    with pytest.raises(ConnectorTimeoutError):
        connector.fetch(allow_stale=False)


# ---------------------------------------------------------------------
# Health and readiness probes
# ---------------------------------------------------------------------
def test_liveness_never_touches_the_database(monkeypatch):
    monkeypatch.setattr(db, "get_engine", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    report = health.liveness()
    assert report["status"] == "ok" and "version" in report


def test_readiness_reports_each_check_and_fails_when_the_database_is_unreachable(fresh_db, monkeypatch):
    ready = health.readiness()
    assert ready["status"] == "ready", ready
    assert set(ready["checks"]) == {"database", "store", "settings"}
    assert ready["checks"]["database"]["ok"] and "latency_ms" in ready["checks"]["database"]

    monkeypatch.setattr(db, "get_engine", lambda: (_ for _ in ()).throw(RuntimeError("db is down")))
    not_ready = health.readiness()
    assert not_ready["status"] == "not ready"
    assert "db is down" in not_ready["checks"]["database"]["detail"]


def test_the_probe_command_exits_nonzero_when_not_ready(fresh_db, monkeypatch, capsys):
    assert health.main(["--live"]) == 0
    assert health.main(["--ready"]) == 0
    monkeypatch.setattr(db, "get_engine", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert health.main(["--ready"]) == 1
    assert '"status": "not ready"' in capsys.readouterr().out


def test_the_http_probe_serves_json(fresh_db):
    import threading
    import urllib.request

    server = health.HTTPServer(("127.0.0.1", 0), health._Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/live", timeout=5) as r:
            assert r.status == 200 and json.loads(r.read())["status"] == "ok"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/ready", timeout=5) as r:
            assert r.status == 200 and json.loads(r.read())["status"] == "ready"
    finally:
        server.shutdown()
        server.server_close()
