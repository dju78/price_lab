"""The public demonstration: a viewer seeded from the environment only into
an empty database, and a banner that says what ephemeral storage means for
the audit log and the run registry."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab.core import audit, db, demo
from pricelab.core.config import get_settings
from pricelab.core.models import Role
from pricelab.core.registry import IndexRunORM
from pricelab.core.security import UserORM, create_session, create_user

APP_PATH = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture()
def empty_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "demo.db"))
    monkeypatch.delenv(demo.DEMO_USERNAME_ENV, raising=False)
    monkeypatch.delenv(demo.DEMO_PASSWORD_ENV, raising=False)
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import registry, security  # noqa: F401  register tables
    db.init_db()
    yield monkeypatch
    db.reset_db_state()
    get_settings.cache_clear()


def _configure(monkeypatch, username: str = "demo", password: str = "demo-pass") -> None:
    monkeypatch.setenv(demo.DEMO_USERNAME_ENV, username)
    monkeypatch.setenv(demo.DEMO_PASSWORD_ENV, password)


def test_nothing_is_seeded_unless_the_environment_asks(empty_db):
    with db.session_scope() as s:
        outcome = demo.seed_demo_viewer(s, seed_run=False)
        assert not outcome.created and "no demonstration account" in outcome.reason
        assert s.query(UserORM).count() == 0
    _configure(empty_db, password="")
    with db.session_scope() as s:
        assert not demo.seed_demo_viewer(s, seed_run=False).created


def test_an_empty_database_gets_one_viewer_and_one_approved_demonstration_run(empty_db):
    _configure(empty_db)
    with db.session_scope() as s:
        outcome = demo.seed_demo_viewer(s)
        assert outcome.created
        users = s.query(UserORM).all()
        assert [(u.username, u.role) for u in users] == [("demo", Role.VIEWER.value)]
        runs = s.query(IndexRunORM).all()
        assert len(runs) == 1 and runs[0].approved and runs[0].label == demo.DEMO_RUN_LABEL
        actions = {e.action for e in s.query(audit.AuditEventORM).all()}
        assert {audit.DEMO_ACCOUNT_SEEDED, audit.DEMO_RUN_SEEDED} <= actions


def test_it_can_never_create_anything_but_a_viewer(empty_db):
    """No parameter and no setting chooses the role."""
    assert "role" not in inspect.signature(demo.seed_demo_viewer).parameters
    _configure(empty_db, username="admin")
    empty_db.setenv("PRICELAB_DEMO_ROLE", "administrator")
    with db.session_scope() as s:
        demo.seed_demo_viewer(s, seed_run=False)
        assert s.query(UserORM).one().role == Role.VIEWER.value


def test_it_refuses_when_the_database_already_holds_any_user(empty_db):
    """On a real deployment -- which has at least its administrator -- the
    seeding path creates nothing, whatever the environment says."""
    with db.session_scope() as s:
        create_user(s, "admin1", "a-real-password", Role.ADMINISTRATOR)
    _configure(empty_db, username="backdoor")
    with db.session_scope() as s:
        outcome = demo.seed_demo_viewer(s)
        assert not outcome.created and outcome.reason.startswith("refused")
        assert [u.username for u in s.query(UserORM).all()] == ["admin1"]
        assert s.query(IndexRunORM).count() == 0
    # and a second start of a seeded demonstration does not seed again
    with db.session_scope() as s:
        assert not demo.seed_demo_viewer(s).created


def _app(token: str | None) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    if token:
        at.session_state["pricelab_session_token"] = token
    at.run()
    assert not at.exception, at.exception
    return at


def test_the_banner_is_on_every_page_while_the_demonstration_account_is_signed_in(empty_db):
    _configure(empty_db)
    with db.session_scope() as s:
        demo.seed_demo_viewer(s, seed_run=False)
        token = create_session(s, s.query(UserORM).filter_by(username="demo").one())
        other = create_session(s, create_user(s, "viewer2", "password123", Role.VIEWER))

    at = _app(token)
    warnings = [w.value for w in at.warning]
    assert demo.DEMO_BANNER in warnings
    assert "audit log and the run registry are reset whenever this instance restarts" \
        in demo.DEMO_BANNER
    assert "not retained" in demo.DEMO_BANNER

    assert demo.DEMO_BANNER not in [w.value for w in _app(other).warning]

    signed_out = _app(None)
    assert demo.DEMO_BANNER in [i.value for i in signed_out.info], \
        "the sign-in screen of a demonstration says so too"
