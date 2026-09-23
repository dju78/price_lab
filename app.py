"""PriceLab.

Upload a price collection. The tool diagnoses it, cleans it, builds the index,
writes the findings and produces a deck and a report you can send on.

The interface holds no analytical logic. Everything it shows comes from the
library, which is tested independently, so what appears on screen is exactly
what a batch run would produce.

Phase 1 converted this from one script running every section unconditionally
into role-gated, multi-page navigation: this file now only holds the
authentication gate, shared chrome (styling, sidebar identity, workflow
progress) and the page registry. Every section that used to run inline here
moved to pages/, unchanged in what it computes.
"""

from __future__ import annotations

import streamlit as st

import pages.audit_log
import pages.construction
import pages.decomposition
import pages.deflation
import pages.diagnostics
import pages.escalation
import pages.findings
import pages.housing
import pages.imputation
import pages.index_build
import pages.ingest
import pages.multilateral
import pages.outliers
import pages.property
import pages.quality
import pages.quality_adjustment
import pages.reports
import pages.revisions
import pages.seasonal
import pages.sources
import pages.spatial
import pages.trade
from pages import common
from pricelab.core import audit, db
from pricelab.core.logging import configure_logging
from pricelab.core.models import Role
from pricelab.core.security import (
    AccessDenied,
    PasswordAuthProvider,
    create_session,
    current_role,
    invalidate_session,
    set_current_role,
    validate_session,
)

st.set_page_config(page_title="PriceLab", page_icon="📈", layout="wide",
                   initial_sidebar_state="expanded")

st.markdown("""
<style>
  .block-container {padding-top: 2.2rem; max-width: 1180px;}
  h1, h2, h3 {color: #12263A;}
  [data-testid="stMetricValue"] {color: #1E4D6B; font-size: 2rem;}
  .pl-headline {font-size: 2.3rem; line-height: 1.18; font-weight: 700;
                color: #12263A; margin: 0.2rem 0 0.4rem 0;}
  .pl-sub {color: #6B7C8C; font-size: 0.95rem; margin-bottom: 1.3rem;}
  .pl-eyebrow {color: #D9822B; font-weight: 700; font-size: 0.75rem;
               letter-spacing: 0.09em; text-transform: uppercase;}
  .pl-ev {color: #1E4D6B; font-size: 0.88rem; font-style: italic;
          margin-bottom: 0.6rem;}
  .pl-card {background: #F2F5F7; border-radius: 10px; padding: 0.9rem 1.1rem;
            margin-bottom: 0.6rem; color: #12263A;}
</style>
""", unsafe_allow_html=True)

# Fast path for local development: creates any table that doesn't already
# exist and is a no-op otherwise. A deployed environment runs the Alembic
# migration in migrations/ instead, which is what actually owns schema
# evolution; this call never drops or alters a column.
configure_logging()
db.init_db()


def _login_form() -> None:
    st.markdown('<div class="pl-eyebrow">PriceLab</div>', unsafe_allow_html=True)
    st.markdown('<div class="pl-headline">Sign in</div>', unsafe_allow_html=True)
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")

    if not submitted:
        return

    provider = PasswordAuthProvider()
    with db.session_scope() as s:
        user = provider.authenticate(s, username, password)
        if user is None:
            st.error("Incorrect username or password.")
            return
        token = create_session(s, user)
        role = Role(user.role)
        audit.record_event(s, username, audit.LOGIN, "session")

    st.session_state["pricelab_session_token"] = token
    st.session_state["pricelab_username"] = username
    set_current_role(role)
    st.rerun()


def _resolve_session() -> str | None:
    """Validate the browser session's token against the database, enforcing
    the idle timeout on every rerun rather than trusting a role cached from
    login. Returns the username if the session is still valid, else clears
    local state and returns None."""
    token = st.session_state.get("pricelab_session_token")
    if not token:
        return None
    with db.session_scope() as s:
        user = validate_session(s, token)
        if user is None:
            for key in ("pricelab_session_token", "pricelab_username"):
                st.session_state.pop(key, None)
            set_current_role(None)
            return None
        set_current_role(Role(user.role))
        return str(user.username)


username = _resolve_session()
if username is None:
    # Streamlit falls back to auto-discovering every file under pages/ as a
    # navigable page (including non-page modules like pages/common.py)
    # whenever a run does not call st.navigation() at all -- which an
    # early st.stop() here would do on every unauthenticated run. Routing
    # the login screen through st.navigation() too, with position="hidden"
    # so it shows no sidebar chrome, keeps that fallback from ever
    # triggering and is the only way to keep the real pages from being
    # listed (even as dead links) before sign-in.
    st.navigation([st.Page(_login_form, title="Sign in")], position="hidden").run()
    st.stop()

# ---------------------------------------------------------------------
# Authenticated from here on.
# ---------------------------------------------------------------------
st.sidebar.markdown("### PriceLab")
st.sidebar.caption(f"Signed in as **{username}** ({current_role().value})")
if st.sidebar.button("Sign out", use_container_width=True):
    token = st.session_state.get("pricelab_session_token")
    with db.session_scope() as s:
        if token:
            invalidate_session(s, token)
        audit.record_event(s, username, audit.LOGOUT, "session")
    for key in ("pricelab_session_token", "pricelab_username"):
        st.session_state.pop(key, None)
    set_current_role(None)
    st.rerun()

common.workflow_progress()
st.sidebar.divider()

# Each entry's allowed roles are read from the page function's own
# `require_role` decorator (`__pricelab_required_roles__`), not repeated
# here: a page with no decorator (Reports) is open to any authenticated
# role. This is navigation-menu filtering for a sensible sidebar only --
# the actual access control is each page's own decorator, enforced again
# from inside `pg.run()` regardless of what this list shows.
#
# url_path is given explicitly rather than left to be inferred from the
# callable: every page module names its render function `render`, so
# `st.Page` would infer the same "render" pathname for every one of them
# and StreamlitAPIException as soon as more than one page is visible at
# once (which happens for every role but viewer).
_PAGE_SPECS = [
    (pages.ingest.render, "Ingest", "📥", "ingest"),
    (pages.quality.render, "Quality", "🧪", "quality"),
    (pages.outliers.render, "Outliers", "🚩", "outliers"),
    (pages.imputation.render, "Imputation", "🧩", "imputation"),
    (pages.quality_adjustment.render, "Quality adjustment", "🔁", "quality-adjustment"),
    (pages.index_build.render, "Index build", "📈", "index-build"),
    (pages.multilateral.render, "Multilateral", "🧮", "multilateral"),
    (pages.seasonal.render, "Seasonality", "🍓", "seasonality"),
    (pages.decomposition.render, "Decomposition", "🧱", "decomposition"),
    (pages.deflation.render, "Deflation", "💶", "deflation"),
    (pages.spatial.render, "Spatial comparison", "🗺️", "spatial"),
    (pages.trade.render, "Trade prices", "🚢", "trade"),
    (pages.construction.render, "Construction", "🏗️", "construction"),
    (pages.escalation.render, "Contract escalation", "📑", "escalation"),
    (pages.property.render, "Property prices", "🏠", "property"),
    (pages.housing.render, "Rents and owner-occupied housing", "🔑", "housing"),
    (pages.findings.render, "Findings", "🗒️", "findings"),
    (pages.diagnostics.render, "Diagnostics", "🔎", "diagnostics"),
    (pages.reports.render, "Reports", "📤", "reports"),
    (pages.sources.render, "Sources", "🌐", "sources"),
    (pages.revisions.render, "Revisions", "📜", "revisions"),
    (pages.audit_log.render, "Audit", "🔏", "audit"),
]


def _visible_to_current_role(page_fn: object) -> bool:
    allowed = getattr(page_fn, "__pricelab_required_roles__", None)
    return allowed is None or current_role() in allowed


nav_pages = [
    st.Page(fn, title=title, icon=icon, url_path=url_path)
    for fn, title, icon, url_path in _PAGE_SPECS
    if _visible_to_current_role(fn)
]

pg = st.navigation(nav_pages)
try:
    pg.run()
except AccessDenied as exc:
    st.error(f"You don't have access to this page: {exc}")
