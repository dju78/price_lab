"""The public demonstration: one viewer account seeded at startup, and the
banner that says what a demonstration on ephemeral storage is.

Seeding is deliberately narrow, because a startup path that creates
accounts is a back door on any deployment it runs against by mistake:

- it creates a **viewer** and nothing else -- the role is fixed here, not
  read from the environment, so no setting can make it mint a compiler or
  an administrator;
- it runs only when both `PRICELAB_DEMO_USERNAME` and
  `PRICELAB_DEMO_PASSWORD` are set;
- it **refuses** when the database already holds any user at all, so on a
  real deployment (which has an administrator) it creates nothing, whatever
  the environment says.

With the account it registers and approves one demonstration run of the
bundled collection, so a viewer -- who can load approved runs but not
compile -- has something to open on Reports.

The banner matters as much as the seeding. The platform presents its audit
log and run registry as governance guarantees; on storage that does not
persist, both are emptied at every restart, and an empty audit log cannot
say whether nothing happened or everything was lost. The banner says so on
every page while the demonstration account is signed in.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from . import audit
from .models import Role

__all__ = [
    "DEMO_BANNER",
    "DEMO_PASSWORD_ENV",
    "DEMO_USERNAME_ENV",
    "SeedOutcome",
    "demo_username",
    "is_demo_session",
    "seed_demo_viewer",
]

DEMO_USERNAME_ENV = "PRICELAB_DEMO_USERNAME"
DEMO_PASSWORD_ENV = "PRICELAB_DEMO_PASSWORD"
DEMO_ROLE = Role.VIEWER
DEMO_RUN_LABEL = "Demonstration: bundled supermarket collection"
BUNDLED = Path(__file__).resolve().parents[2] / "supermarket_price_collection.xlsx"

DEMO_BANNER = (
    "**This is a demonstration.** Data uploaded here is not retained. The audit log and "
    "the run registry are reset whenever this instance restarts, so an empty or short "
    "audit log here means the instance has restarted recently -- it is not evidence that "
    "nothing happened, and it is not evidence that nothing was lost. A real deployment "
    "keeps both in PostgreSQL on persistent storage.")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeedOutcome:
    created: bool
    reason: str


def demo_username() -> str | None:
    name = os.environ.get(DEMO_USERNAME_ENV, "").strip()
    return name or None


def is_demo_session(username: str | None) -> bool:
    """Whether the signed-in user is the seeded demonstration account."""
    configured = demo_username()
    return bool(configured and username == configured)


def seed_demo_viewer(session: Session, *, seed_run: bool = True) -> SeedOutcome:
    """Create the demonstration viewer if, and only if, the environment asks
    for one and the database holds no user yet."""
    from .security import UserORM, create_user

    username = demo_username()
    password = os.environ.get(DEMO_PASSWORD_ENV, "")
    if not username or not password:
        return SeedOutcome(False, "no demonstration account is configured")
    existing = session.query(UserORM).count()
    if existing:
        return SeedOutcome(False, f"refused: the database already holds {existing} user(s), "
                                  "so this is not an empty demonstration instance")
    create_user(session, username, password, DEMO_ROLE)
    audit.record_event(session, "system", audit.DEMO_ACCOUNT_SEEDED, username,
                       {"role": DEMO_ROLE.value})
    if seed_run and BUNDLED.exists():
        _seed_demo_run(session)
    log.info("demonstration viewer seeded", extra={"username": username})
    return SeedOutcome(True, f"created viewer {username!r}")


def _seed_demo_run(session: Session) -> None:
    """Register and approve one run of the bundled collection, so a viewer
    has an approved run to load on Reports."""
    import pandas as pd

    from .. import run_pipeline
    from ..data.upload import standardise
    from ..engine.auto import infer_schema
    from .config import RunConfig
    from .registry import approve_run, register_run

    raw = pd.read_excel(BUNDLED)
    df = standardise(raw, infer_schema(raw))
    config = RunConfig(label=DEMO_RUN_LABEL)
    run = register_run(session, df, config, DEMO_RUN_LABEL, result=run_pipeline(df, config),
                       data_source=BUNDLED.name)
    approve_run(session, run.run_id)
    audit.record_event(session, "system", audit.DEMO_RUN_SEEDED, f"run {run.run_id}",
                       {"label": DEMO_RUN_LABEL})
