"""Tests for core/security.py: RBAC, authentication, export sanitisation,
the restricted formula evaluator, and disclosure control."""

import pandas as pd
import pytest

from pricelab.core import db
from pricelab.core.config import get_settings
from pricelab.core.models import Role
from pricelab.core.security import (
    AccessDenied,
    FormulaError,
    PasswordAuthProvider,
    UserORM,
    create_session,
    create_user,
    evaluate_formula,
    require_role,
    safe_csv,
    sanitize_cell,
    set_current_role,
    suppress_small_cells,
    suppress_with_secondary,
    validate_session,
)


# ---------------------------------------------------------------------
# a. RBAC
# ---------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clear_role():
    set_current_role(None)
    yield
    set_current_role(None)


def a_compiler_only_page():
    @require_role(Role.ADMINISTRATOR, Role.COMPILER)
    def render():
        return "rendered"
    return render


def test_permitted_role_can_render_the_page():
    set_current_role(Role.COMPILER)
    assert a_compiler_only_page()() == "rendered"


def test_viewer_role_is_refused_a_compiler_page_by_direct_call():
    """This is "direct navigation" in the sense that matters: calling the
    page's own render function, bypassing any sidebar or nav menu that
    would normally lead to it. Real access control refuses it from inside
    the function body regardless of how it was reached."""
    set_current_role(Role.VIEWER)
    with pytest.raises(AccessDenied):
        a_compiler_only_page()()


def test_unauthenticated_session_is_refused():
    set_current_role(None)
    with pytest.raises(AccessDenied):
        a_compiler_only_page()()


def test_require_role_needs_at_least_one_role():
    with pytest.raises(ValueError):
        require_role()


# ---------------------------------------------------------------------
# b. Authentication
# ---------------------------------------------------------------------
@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", f"sqlite:///{tmp_path / 'security_test.db'}")
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield
    db.reset_db_state()
    get_settings.cache_clear()


def test_password_is_hashed_not_stored_plain(fresh_db):
    with db.session_scope() as s:
        user = create_user(s, "alice", "correct horse battery staple", Role.COMPILER)
        assert user.password_hash != "correct horse battery staple"
        assert not user.password_hash.startswith("correct horse")


def test_password_hash_is_not_a_bare_sha_digest(fresh_db):
    import hashlib
    with db.session_scope() as s:
        user = create_user(s, "alice", "hunter2", Role.COMPILER)
    sha256 = hashlib.sha256(b"hunter2").hexdigest()
    sha1 = hashlib.sha1(b"hunter2").hexdigest()
    md5 = hashlib.md5(b"hunter2").hexdigest()
    assert user.password_hash not in (sha256, sha1, md5)
    assert user.password_hash.startswith("$argon2")


def test_auth_provider_accepts_correct_password_and_rejects_wrong_one(fresh_db):
    with db.session_scope() as s:
        create_user(s, "alice", "correct horse battery staple", Role.ANALYST)

    provider = PasswordAuthProvider()
    with db.session_scope() as s:
        ok = provider.authenticate(s, "alice", "correct horse battery staple")
        assert ok is not None
        assert ok.role == Role.ANALYST.value

        bad = provider.authenticate(s, "alice", "wrong password")
        assert bad is None

        missing = provider.authenticate(s, "not-a-user", "anything")
        assert missing is None


def test_duplicate_username_is_refused(fresh_db):
    with db.session_scope() as s:
        create_user(s, "alice", "pw1", Role.VIEWER)
    with db.session_scope() as s:
        with pytest.raises(ValueError, match="already exists"):
            create_user(s, "alice", "pw2", Role.VIEWER)


def test_session_token_round_trips_and_resolves_to_the_user(fresh_db):
    with db.session_scope() as s:
        user = create_user(s, "alice", "pw", Role.ANALYST)
        token = create_session(s, user)

    with db.session_scope() as s:
        resolved = validate_session(s, token, timeout_minutes=60)
        assert resolved is not None
        assert resolved.username == "alice"


def test_session_beyond_the_idle_timeout_is_rejected(fresh_db):
    with db.session_scope() as s:
        user = create_user(s, "alice", "pw", Role.ANALYST)
        token = create_session(s, user)

    with db.session_scope() as s:
        # A timeout of 0 minutes means "expired the instant it was created".
        assert validate_session(s, token, timeout_minutes=0) is None

    with db.session_scope() as s:
        # The expired session was deleted, not merely ignored.
        assert s.query(UserORM).filter_by(username="alice").one() is not None
        from pricelab.core.security import SessionORM
        assert s.query(SessionORM).filter_by(token=token).one_or_none() is None


def test_unknown_token_resolves_to_no_user(fresh_db):
    with db.session_scope() as s:
        assert validate_session(s, "not-a-real-token") is None


# ---------------------------------------------------------------------
# c. Export sanitisation
# ---------------------------------------------------------------------
INJECTION_VECTORS = [
    "=cmd|'/c calc'!A1",
    "+1+1",
    "-2+3",
    "@SUM(A1:A2)",
    "\t=1+1",
]


@pytest.mark.parametrize("vector", INJECTION_VECTORS)
def test_every_known_injection_vector_is_neutralised(vector):
    sanitized = sanitize_cell(vector)
    assert sanitized.startswith("'")
    assert sanitized[1:] == vector


def test_ordinary_strings_are_untouched():
    assert sanitize_cell("White 550g loaf") == "White 550g loaf"
    assert sanitize_cell("Bread") == "Bread"


def test_non_string_values_are_untouched():
    assert sanitize_cell(1.5) == 1.5
    assert sanitize_cell(None) is None


@pytest.mark.parametrize("vector", INJECTION_VECTORS)
def test_safe_csv_neutralises_injection_in_every_string_column(vector):
    df = pd.DataFrame({"item_name": [vector], "category": [vector], "price": [1.0]})
    csv_text = safe_csv(df, index=False)
    # The raw dangerous prefix must not appear un-escaped as the start of
    # any field in the produced CSV text.
    for line in csv_text.splitlines()[1:]:
        for field in line.split(","):
            assert not field.startswith(("=", "+", "-", "@", "\t"))


def test_safe_csv_matches_both_app_download_paths():
    """Mirrors app.py's two CSV downloads: cleaned data and flagged
    observations, both of which include user-controlled item_name/category
    text."""
    cleaned = pd.DataFrame({
        "item_name": ["=HYPERLINK(\"http://evil\")", "Ordinary item"],
        "category": ["Bread", "@evil"],
        "price_clean": [1.0, 2.0],
    })
    flagged = pd.DataFrame({
        "item_name": ["+1+1"], "category": ["-1-1"], "flag": ["scale_error_x100"],
    })
    for frame in (cleaned, flagged):
        csv_text = safe_csv(frame, index=False)
        for line in csv_text.splitlines()[1:]:
            for field in line.split(","):
                assert field[:1] not in ("=", "+", "-", "@", "\t")


def test_safe_csv_leaves_numeric_columns_alone():
    df = pd.DataFrame({"price": [-1.5, 2.0]})
    csv_text = safe_csv(df, index=False)
    assert "-1.5" in csv_text  # a genuine negative number is not text injection


def test_safe_csv_sanitises_a_dangerous_column_header():
    """A pivoted export (one column per category, as the index-series
    download produces) turns a user-supplied category name into a header
    cell, which is just as much a spreadsheet-injection vector as a row."""
    df = pd.DataFrame({"=EVILCAT": [100.0, 101.0]})
    csv_text = safe_csv(df, index=False)
    header = csv_text.splitlines()[0]
    assert header.startswith("'=EVILCAT")


def test_safe_csv_preserves_datetime_index_formatting():
    """A DatetimeIndex must keep pandas' date formatting in the exported
    CSV rather than being rebuilt as a plain Index of str(Timestamp), which
    numeric/datetime values can never need sanitising against anyway."""
    df = pd.DataFrame(
        {"All items": [100.0, 101.0]},
        index=pd.to_datetime(["2020-01-01", "2020-02-01"]))
    df.index.name = "period"
    csv_text = safe_csv(df)
    assert "2020-01-01,100.0" in csv_text
    assert "00:00:00" not in csv_text


# ---------------------------------------------------------------------
# d. Restricted formula evaluation
# ---------------------------------------------------------------------
def test_basic_arithmetic():
    assert evaluate_formula("2 + 3 * 4", {}) == 14
    assert evaluate_formula("(2 + 3) * 4", {}) == 20
    assert evaluate_formula("a + b", {"a": 1.5, "b": 2.5}) == 4.0
    assert evaluate_formula("-a", {"a": 5}) == -5
    assert evaluate_formula("abs(-a)", {"a": 5}) == 5
    assert evaluate_formula("round(a)", {"a": 2.6}) == 3


@pytest.mark.parametrize("expr", [
    "__import__('os')",
    "a.__class__",
    "a.__class__.__bases__",
    "a[0]",
    "[x for x in (1, 2, 3)]",
    "{x for x in (1, 2)}",
    "(x for x in (1, 2))",
    "lambda x: x",
    "os.system('ls')",
    "eval('1')",
    "exec('1')",
    "getattr(a, 'x')",
])
def test_every_disallowed_construct_is_rejected(expr):
    with pytest.raises(FormulaError):
        evaluate_formula(expr, {"a": 1, "os": 1})


def test_import_statement_is_rejected_at_parse_time():
    with pytest.raises(FormulaError):
        evaluate_formula("import os", {})


def test_unknown_name_is_rejected():
    with pytest.raises(FormulaError, match="unknown name"):
        evaluate_formula("a + b", {"a": 1})


def test_keyword_arguments_are_rejected():
    with pytest.raises(FormulaError):
        evaluate_formula("round(a, ndigits=2)", {"a": 1.234})


# ---------------------------------------------------------------------
# e. Disclosure control
# ---------------------------------------------------------------------
def test_cell_below_minimum_count_is_suppressed():
    df = pd.DataFrame({"category": ["A", "B"], "value": [10.0, 20.0], "n": [2, 5]})
    out = suppress_small_cells(df, "n", ["value"], min_count=3)
    assert pd.isna(out.loc[0, "value"])
    assert out.loc[1, "value"] == 20.0
    assert out.loc[0, "suppressed"] and not out.loc[1, "suppressed"]


def test_secondary_suppression_prevents_recovery_by_subtraction():
    """One cell (n=1) is below the threshold in a group whose total (60) is
    otherwise fully known from its two other, published, cells. Primary
    suppression alone would leave it recoverable as 60 - 20 - 30 = 10;
    secondary suppression must additionally hide one more cell so no single
    equation isolates the suppressed value."""
    df = pd.DataFrame({
        "group": ["A", "A", "A"],
        "value": [10.0, 20.0, 30.0],
        "n": [1, 10, 10],
    })
    total = df["value"].sum()
    out = suppress_with_secondary(df, "group", "value", "n", min_count=3)

    suppressed_rows = out[out["suppressed"]]
    assert len(suppressed_rows) == 2  # primary + one secondary
    published_sum = out.loc[~out["suppressed"], "value"].sum()
    # With two cells hidden, the published remainder is one equation short
    # of pinning down either unknown, so the original 10.0 cannot be
    # recovered as `total - published_sum`.
    assert total - published_sum != 10.0


def test_no_secondary_suppression_needed_when_nothing_is_primary_suppressed():
    df = pd.DataFrame({"group": ["A", "A"], "value": [10.0, 20.0], "n": [10, 10]})
    out = suppress_with_secondary(df, "group", "value", "n", min_count=3)
    assert not out["suppressed"].any()


def test_two_cell_group_suppresses_both_when_one_is_below_threshold():
    """The degenerate case: a group of exactly two cells. If one is
    suppressed, the other is exactly `total - suppressed_value` and must
    also be hidden."""
    df = pd.DataFrame({"group": ["A", "A"], "value": [10.0, 20.0], "n": [1, 10]})
    out = suppress_with_secondary(df, "group", "value", "n", min_count=3)
    assert out["suppressed"].all()
