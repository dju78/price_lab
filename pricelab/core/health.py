"""Liveness and readiness probes.

Liveness answers "is the process up" and never touches a dependency: a
container orchestrator restarts a process that fails liveness, and a
database outage is not a reason to restart the application. Readiness
answers "can this instance do work": the database answers a query and
its schema is at the migration head, the Parquet store is writable, and
the settings parsed. An instance that is alive but not ready is kept out
of rotation, not restarted.

Both are exposed three ways: as functions (for tests), as a command
(`python -m pricelab.core.health --live` / `--ready`, exit status 0 or 1,
which is what the Docker HEALTHCHECK runs), and as a tiny HTTP server
(`python -m pricelab.core.health --serve PORT`, `/health/live` and
`/health/ready`) for orchestrators that probe over HTTP without going
through Streamlit's own `/_stcore/health`, which reports only that
Streamlit is running.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from sqlalchemy import text

from .. import __version__
from .config import get_settings


def liveness() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "pid": os.getpid(), "time": time.time()}


def _migration_head_matches(engine: Any) -> tuple[bool, str]:
    """Compare the database's alembic_version with the migration scripts'
    head. A database at an older revision is not ready: the code will ask
    for columns it does not have."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        repo_root = Path(__file__).resolve().parents[2]
        cfg = Config(str(repo_root / "alembic.ini"))
        cfg.set_main_option("script_location", str(repo_root / "migrations"))
        head = ScriptDirectory.from_config(cfg).get_current_head() or "unknown"
        with engine.connect() as conn:
            tables = conn.execute(text(
                "select name from sqlite_master where type='table' and name='alembic_version'"
                if str(engine.url).startswith("sqlite") else
                "select table_name as name from information_schema.tables "
                "where table_name='alembic_version'")).fetchall()
            if not tables:
                # Created by `init_db()` (create_all) rather than Alembic:
                # the schema is whatever the ORM says, which is head by
                # construction.
                return True, f"schema from create_all (head {head})"
            current = conn.execute(text("select version_num from alembic_version")).scalar()
        return current == head, f"database at {current}, scripts at {head}"
    except Exception as exc:  # noqa: BLE001 -- a probe reports, it does not raise
        return False, f"could not determine migration state: {exc}"


def readiness() -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    settings = get_settings()

    started = time.monotonic()
    try:
        from .db import get_engine

        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("select 1")).scalar()
        ok, detail = _migration_head_matches(engine)
        checks["database"] = {"ok": ok, "detail": detail,
                              "latency_ms": round((time.monotonic() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        checks["database"] = {"ok": False, "detail": str(exc)}

    try:
        store = Path(settings.store_dir)
        store.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=store, prefix=".probe-", delete=True):
            pass
        checks["store"] = {"ok": True, "detail": str(store)}
    except Exception as exc:  # noqa: BLE001
        checks["store"] = {"ok": False, "detail": str(exc)}

    checks["settings"] = {"ok": True, "detail": f"environment={settings.environment}"}
    return {"status": "ready" if all(c["ok"] for c in checks.values()) else "not ready",
            "version": __version__, "checks": checks}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 -- http.server's API
        if self.path == "/health/live":
            body, code = liveness(), 200
        elif self.path == "/health/ready":
            body = readiness()
            code = 200 if body["status"] == "ready" else 503
        else:
            body, code = {"error": "unknown probe"}, 404
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def serve(port: int, *, once: bool = False) -> HTTPServer:
    server = HTTPServer(("0.0.0.0", port), _Handler)
    if once:
        server.handle_request()
    else:
        server.serve_forever()
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PriceLab health probes")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--live", action="store_true")
    group.add_argument("--ready", action="store_true")
    group.add_argument("--serve", type=int, metavar="PORT")
    args = parser.parse_args(argv)
    if args.serve:
        serve(args.serve)
        return 0
    report = liveness() if args.live else readiness()
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in ("ok", "ready") else 1


if __name__ == "__main__":
    sys.exit(main())
