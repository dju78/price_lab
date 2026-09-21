"""Structured JSON logging with a correlation identifier per run.

Every log line is one JSON object -- timestamp, level, logger, message,
and whatever fields the caller attached -- so a log aggregator can index
it without a parser written for this application. The correlation
identifier is a context variable set by `run_context` for the duration of
one pipeline run (and copied into that run's result and audit event), so
every line emitted while that run executes carries the same
`correlation_id`, whichever module emitted it, and the lines for one run
can be pulled out of a shared log by that one field.

Standard-library logging rather than structlog: the same JSON shape,
no additional dependency, and every library this application uses
already logs through `logging`, so their lines get the correlation id
too.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from .config import get_settings

correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pricelab_correlation_id", default=None)

_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message", "asctime", "correlation_id"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=False)


class CorrelationFilter(logging.Filter):
    """Stamps the current correlation id onto every record that passes
    through a handler it is attached to."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id.get()
        return True


_configured = False


def configure_logging(*, force: bool = False, stream: Any = None) -> logging.Handler:
    """Install the JSON handler on the root logger, once. Returns the
    handler (tests capture its stream)."""
    global _configured
    root = logging.getLogger()
    if _configured and not force:
        for h in root.handlers:
            if isinstance(h.formatter, JsonFormatter):
                return h
    settings = get_settings()
    for h in list(root.handlers):
        if isinstance(h.formatter, JsonFormatter):
            root.removeHandler(h)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter() if settings.log_json else logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(correlation_id)s] %(message)s"))
    handler.addFilter(CorrelationFilter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    _configured = True
    return handler


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@contextmanager
def run_context(run_id: str | None = None) -> Iterator[str]:
    """Bind a correlation id for the duration of the block. Nested blocks
    keep the outer id, so a pipeline re-run inside an impact report logs
    under the run that asked for it."""
    existing = correlation_id.get()
    cid = existing or run_id or uuid.uuid4().hex
    token = correlation_id.set(cid)
    try:
        yield cid
    finally:
        correlation_id.reset(token)
