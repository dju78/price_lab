"""Tests for app.py's real navigation outcome under different roles.

tests/test_security.py already proves `require_role` raises `AccessDenied`
when a decorated function is called directly under the wrong role -- that
tests the decorator. These tests run the actual app (via Streamlit's
AppTest) as a signed-in viewer and confirm what a viewer sees is genuinely
never a compiler-only page: app.py's nav-list filtering means a
compiler-only page is never registered as a route in a viewer's session at
all, so "navigating directly to it" has no URL to land on, and Streamlit's
own navigation falls back to the session's one reachable page (Reports)
rather than crashing or leaking compiler-only content.
"""

from pathlib import Path

import pytest
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab.core import db
from pricelab.core.config import get_settings
from pricelab.core.models import Role
from pricelab.core.security import create_session, create_user

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = str(REPO_ROOT / "app.py")


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "nav_test.db"))
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield
    db.reset_db_state()
    get_settings.cache_clear()


def _signed_in_session_token(role: Role, username: str) -> str:
    with db.session_scope() as s:
        user = create_user(s, username, "password123", role)
        return create_session(s, user)


def _rendered_text(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_viewer_never_reaches_a_compiler_page_and_lands_on_reports_instead(fresh_db):
    """The outcome, not the decorator: a viewer's session has no route to
    Ingest (or any other compiler-only page) at all, so the app falls back
    to the one page a viewer can reach."""
    token = _signed_in_session_token(Role.VIEWER, "viewer1")

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["pricelab_session_token"] = token
    at.run()

    assert not at.exception
    text = _rendered_text(at)
    assert "### Reports" in text
    # None of the compiler-only pages' own headings ever render.
    assert "Ingest and compile" not in text
    assert "### Quality" not in text
    assert "### Imputation" not in text
    assert "### Index build" not in text


def test_compiler_reaches_ingest_and_the_session_has_no_stray_pages(fresh_db):
    """The positive control: a role that *is* allowed lands on its own
    default page, proving the previous test's absence of Ingest content for
    a viewer is because of role filtering, not a broken app."""
    token = _signed_in_session_token(Role.COMPILER, "compiler1")

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["pricelab_session_token"] = token
    at.run()

    assert not at.exception
    assert "Ingest and compile" in _rendered_text(at)


def test_unauthenticated_session_sees_only_the_sign_in_form(fresh_db):
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()

    assert not at.exception
    text = _rendered_text(at)
    assert "Sign in" in text
    assert "Reports" not in text
    assert "Ingest and compile" not in text
