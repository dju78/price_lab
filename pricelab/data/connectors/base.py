"""Base machinery every official-source connector shares: retry with
exponential backoff and jitter, response caching through `core.cache` with
a configurable time-to-live, rate-limit handling that respects
`Retry-After`, schema validation before a response reaches the engine, a
vintage stamp, and an audit event for every fetch attempt, successful or
not.

A concrete connector implements three methods (`_request`, `_validate`,
`_parse`) and gets retry, caching, rate-limiting and auditing for free from
`BaseConnector.fetch`. Nothing here is specific to any one agency's API
shape; that is exactly what `_request`/`_validate`/`_parse` exist to
localise.
"""

from __future__ import annotations

import hashlib
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import requests
from sqlalchemy.orm import Session

from ...core import audit
from ...core.cache import BoundedCache, content_key

DEFAULT_CACHE_TTL_SECONDS = 3600.0


class ConnectorError(Exception):
    """Base class for every error this module raises. A caller that wants
    to catch "anything went wrong fetching from an official source,
    whatever the reason" catches this."""


class ConnectorTimeoutError(ConnectorError):
    """The request did not complete within the configured timeout, even
    after retrying."""


class ConnectorRateLimitError(ConnectorError):
    """The source returned HTTP 429 (or an agency-specific rate-limit
    signal) and continued to after every retry permitted."""


class ConnectorHTTPError(ConnectorError):
    """The source returned an HTTP error status other than a rate limit."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


class ConnectorSchemaError(ConnectorError):
    """The response's shape does not match what this connector expects.

    Always names the specific field or structure that moved: a schema
    change silently producing a wrong series is exactly the failure mode
    this exists to prevent, so the message must be specific enough to
    debug from, not just "malformed response".
    """


@dataclass
class Vintage:
    """A record of exactly where one fetched observation came from and
    when, so a chart built from it can be traced back to a specific
    retrieval rather than just "the internet, at some point"."""

    source: str
    query: str
    retrieved_at: datetime
    response_hash: str
    api_version: str | None = None


@dataclass
class ConnectorResult:
    """What `BaseConnector.fetch` returns: the parsed observations plus the
    vintage that produced them, so a caller never has one without the
    other."""

    data: pd.DataFrame
    vintage: Vintage
    from_cache: bool = False


class BaseConnector(ABC):
    """Shared fetch/retry/cache/audit machinery for an official-source
    connector.

    A subclass implements:
      `_request(**kwargs) -> tuple[str, dict[str, Any]]`
          The URL and query parameters for this fetch. Never includes an
          API key directly in a cache key or a log message; see
          `_redact_query`.
      `_validate(raw) -> None`
          Raise `ConnectorSchemaError` naming the specific field or
          structure that is missing or has changed shape. Called before
          `_parse`, so a schema change never reaches parsing logic that
          would otherwise produce a plausible-looking wrong series.
      `_parse(raw, **kwargs) -> pd.DataFrame`
          Turn a validated raw response into a DataFrame of at least
          `period` (datetime64) and `value` (float) columns.
      `_api_version(raw) -> str | None`
          The API or dataset version the response itself reports, if it
          reports one. Returns None when the source does not expose one;
          not every agency's API does.
    """

    #: Overridden by each subclass; used as the audit `target` and the
    #: vintage's `source`.
    source_name: str = "unknown"

    def __init__(
        self,
        session: requests.Session | None = None,
        cache: BoundedCache[ConnectorResult] | None = None,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.5,
        timeout_seconds: float = 10.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        jitter_fn: Callable[[float, float], float] = random.uniform,
    ):
        self._session = session or requests.Session()
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_retries = max_retries
        self._backoff_base_seconds = backoff_base_seconds
        self._timeout_seconds = timeout_seconds
        self._sleep = sleep_fn
        self._jitter = jitter_fn

    @abstractmethod
    def _request(self, **kwargs: Any) -> tuple[str, dict[str, Any]]: ...

    @abstractmethod
    def _validate(self, raw: Any) -> None: ...

    @abstractmethod
    def _parse(self, raw: Any, **kwargs: Any) -> pd.DataFrame: ...

    def _api_version(self, raw: Any) -> str | None:
        return None

    def _redact_query(self, params: dict[str, Any]) -> dict[str, Any]:
        """The query as recorded in the vintage stamp and the audit log:
        every parameter except anything that looks like a credential."""
        return {k: ("<redacted>" if "key" in k.lower() or "token" in k.lower() else v)
                for k, v in params.items()}

    def fetch(
        self,
        *,
        audit_session: Session | None = None,
        actor: str = "system",
        use_cache: bool = True,
        **kwargs: Any,
    ) -> ConnectorResult:
        """Fetch, validate, parse and stamp one response.

        `audit_session`, if given, gets an `EXTERNAL_FETCH_SUCCESS` or
        `EXTERNAL_FETCH_FAILURE` event for every call, cache hits included:
        a cache hit is still a fact about what this run used, and belongs
        in the trail a published figure can be traced through.
        """
        url, params = self._request(**kwargs)
        safe_query = self._redact_query(params)
        key = content_key(self.source_name, url, str(sorted(safe_query.items())))

        if use_cache and self._cache is not None:
            cached = self._cache.get(key)
            if cached is not None:
                hit = ConnectorResult(data=cached.data, vintage=cached.vintage, from_cache=True)
                self._audit(audit_session, actor, True, safe_query, cache_hit=True)
                return hit

        try:
            raw, response_text = self._get_with_retry(url, params)
        except ConnectorError as exc:
            self._audit(audit_session, actor, False, safe_query, error=str(exc))
            raise

        try:
            self._validate(raw)
            df = self._parse(raw, **kwargs)
        except ConnectorSchemaError as exc:
            self._audit(audit_session, actor, False, safe_query, error=str(exc))
            raise

        vintage = Vintage(
            source=self.source_name,
            query=str(safe_query),
            retrieved_at=datetime.now(UTC),
            response_hash=hashlib.sha256(response_text.encode("utf-8")).hexdigest(),
            api_version=self._api_version(raw),
        )
        result = ConnectorResult(data=df, vintage=vintage, from_cache=False)

        if use_cache and self._cache is not None:
            self._cache.set(key, result, ttl_seconds=self._cache_ttl_seconds)

        self._audit(audit_session, actor, True, safe_query, cache_hit=False)
        return result

    def _audit(
        self,
        session: Session | None,
        actor: str,
        success: bool,
        query: dict[str, Any],
        *,
        cache_hit: bool = False,
        error: str | None = None,
    ) -> None:
        if session is None:
            return
        action = audit.EXTERNAL_FETCH_SUCCESS if success else audit.EXTERNAL_FETCH_FAILURE
        params: dict[str, Any] = {"query": query, "cache_hit": cache_hit}
        if error is not None:
            params["error"] = error
        audit.record_event(session, actor, action, self.source_name, params)

    def _get_with_retry(self, url: str, params: dict[str, Any]) -> tuple[Any, str]:
        """GET with exponential backoff and jitter, honouring
        `Retry-After` on a 429. Returns the parsed JSON (or raw text, for a
        connector whose `_parse` wants that instead) alongside the raw
        response text the vintage hash is computed from."""
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._session.get(url, params=params, timeout=self._timeout_seconds)
            except requests.exceptions.Timeout as exc:
                last_error = exc
                if attempt < self._max_retries:
                    self._sleep(self._backoff_delay(attempt))
                    continue
                raise ConnectorTimeoutError(
                    f"{self.source_name}: timed out after {self._max_retries + 1} attempts"
                ) from exc
            except requests.exceptions.RequestException as exc:
                raise ConnectorError(f"{self.source_name}: request failed: {exc}") from exc

            if response.status_code == 429:
                if attempt < self._max_retries:
                    self._sleep(self._retry_after_delay(response, attempt))
                    continue
                raise ConnectorRateLimitError(
                    f"{self.source_name}: rate limited after {self._max_retries + 1} attempts")

            if response.status_code >= 400:
                raise ConnectorHTTPError(
                    response.status_code,
                    f"{self.source_name}: HTTP {response.status_code} for {url}")

            try:
                decoded = self._decode(response)
            except ValueError as exc:
                # `_decode`'s default calls response.json(), which raises a
                # ValueError subclass (requests.exceptions.JSONDecodeError)
                # on a non-JSON body. That is a schema problem -- the
                # response is not shaped as this connector expects -- not
                # a transport failure, so it is wrapped rather than left to
                # leak past this module's own exception hierarchy.
                raise ConnectorSchemaError(
                    f"{self.source_name}: response body could not be decoded: {exc}") from exc
            return decoded, response.text

        # Unreachable in practice (every branch above returns or raises),
        # but keeps mypy honest about the loop's fallthrough.
        raise ConnectorError(f"{self.source_name}: exhausted retries") from last_error

    def _decode(self, response: requests.Response) -> Any:
        """JSON by default; a connector whose source returns CSV (FAO)
        overrides this to return `response.text` instead."""
        result: Any = response.json()
        return result

    def _backoff_delay(self, attempt: int) -> float:
        base: float = self._backoff_base_seconds * (2**attempt)
        jitter: float = self._jitter(0, self._backoff_base_seconds)
        result: float = base + jitter
        return result

    def _retry_after_delay(self, response: requests.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return float(retry_after)
            except ValueError:
                pass  # Retry-After can also be an HTTP-date; fall back to backoff
        return self._backoff_delay(attempt)
