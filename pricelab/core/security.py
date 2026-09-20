"""Security: role-based access control, authentication, export sanitisation,
restricted formula evaluation, and statistical disclosure control.

Five independent concerns live here because they are the platform's five
non-negotiable security rules given code, not because they share
implementation. Each is usable and tested on its own.
"""

from __future__ import annotations

import ast
import functools
import operator
import secrets
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import pandas as pd
import streamlit as st
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from .config import get_settings
from .db import Base
from .models import Role

# ---------------------------------------------------------------------
# a. Role-based access control
# ---------------------------------------------------------------------
_ROLE_SESSION_KEY = "pricelab_auth_role"


class AccessDenied(PermissionError):
    """Raised by `require_role` inside the guarded function's own body.

    A caller that hides a disabled sidebar entry has not enforced anything;
    a caller that raises this from inside the page's render function has,
    because the page produces no output at all without it, regardless of
    how it was reached.
    """


def current_role() -> Role | None:
    """The role of the currently authenticated session, or None if there is
    none. Backed by `st.session_state`, which is per-browser-session and
    survives a rerun (Streamlit re-executes the whole script on every
    interaction), but not a login."""
    value = st.session_state.get(_ROLE_SESSION_KEY)
    return Role(value) if value is not None else None


def set_current_role(role: Role | None) -> None:
    if role is None:
        st.session_state.pop(_ROLE_SESSION_KEY, None)
    else:
        st.session_state[_ROLE_SESSION_KEY] = role.value


def require_role(*allowed: Role) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate a page's render function so it refuses to run for any role
    not listed, checked from inside the function body on every call --
    which is what makes this real access control rather than a hidden
    widget: a caller that invokes the page function directly, bypassing
    whatever navigation menu would normally lead to it, is refused exactly
    the same way.
    """
    if not allowed:
        raise ValueError("require_role needs at least one allowed role")

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            role = current_role()
            if role not in allowed:
                who = role.value if role is not None else "unauthenticated"
                raise AccessDenied(
                    f"'{fn.__name__}' requires one of "
                    f"{[r.value for r in allowed]}; current role is '{who}'")
            return fn(*args, **kwargs)

        # Genuinely unavoidable: mypy types `wrapper` as a plain Callable,
        # which has no attribute to assign to. app.py's navigation reads
        # this attribute back to filter the sidebar to roles that can
        # actually use each page (see app.py's `_visible_to_current_role`);
        # giving `wrapper` a Protocol with this one extra attribute just to
        # satisfy mypy would be more machinery than the thing it types.
        wrapper.__pricelab_required_roles__ = allowed  # type: ignore[attr-defined]
        return wrapper

    return decorator


# ---------------------------------------------------------------------
# b. Authentication
# ---------------------------------------------------------------------
class UserORM(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)


class SessionORM(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    last_seen_at: Mapped[str] = mapped_column(String(32), nullable=False)


_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Argon2id hash, never a bare digest and never reversible."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bool(_hasher.verify(password_hash, password))
    except (VerifyMismatchError, InvalidHash):
        return False


class AuthProvider(Protocol):
    """What a login page depends on. `PasswordAuthProvider` is the only
    implementation today; an OIDC provider is a second class with this same
    method, so replacing one for the other never touches a call site."""

    def authenticate(self, session: Session, username: str, password: str) -> UserORM | None: ...


class PasswordAuthProvider:
    """Username and password checked against the local `users` table."""

    def authenticate(self, session: Session, username: str, password: str) -> UserORM | None:
        user = session.query(UserORM).filter_by(username=username).one_or_none()
        if user is None:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user


def create_user(session: Session, username: str, password: str, role: Role) -> UserORM:
    if session.query(UserORM).filter_by(username=username).one_or_none() is not None:
        raise ValueError(f"user '{username}' already exists")
    user = UserORM(
        username=username,
        password_hash=hash_password(password),
        role=role.value,
        created_at=datetime.now(UTC).isoformat(),
    )
    session.add(user)
    session.flush()
    return user


def create_session(session: Session, user: UserORM) -> str:
    """Issue a new session token for an authenticated user. The token
    itself, not the role, is what the browser session should hold: the role
    is re-derived from the database on each `validate_session` call, so a
    role change or an administrator revoking access takes effect on the
    user's very next request rather than only after their token expires."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC).isoformat()
    session.add(SessionORM(token=token, user_id=user.id, created_at=now, last_seen_at=now))
    session.flush()
    return token


def validate_session(session: Session, token: str, timeout_minutes: float | None = None) -> UserORM | None:
    """Look up a session token, enforcing the idle timeout.

    A session idle for longer than the configured timeout is deleted and
    treated as absent. A session found valid has its `last_seen_at` touched,
    so the timeout is a rolling idle window, not a fixed time-to-live from
    login.
    """
    timeout = timeout_minutes if timeout_minutes is not None else get_settings().session_timeout_minutes
    record = session.query(SessionORM).filter_by(token=token).one_or_none()
    if record is None:
        return None
    last_seen = datetime.fromisoformat(record.last_seen_at)
    if datetime.now(UTC) - last_seen > timedelta(minutes=timeout):
        session.delete(record)
        return None
    record.last_seen_at = datetime.now(UTC).isoformat()
    session.add(record)
    return session.query(UserORM).filter_by(id=record.user_id).one_or_none()


def invalidate_session(session: Session, token: str) -> None:
    session.query(SessionORM).filter_by(token=token).delete()


# ---------------------------------------------------------------------
# c. Export sanitisation
# ---------------------------------------------------------------------
#: Leading characters a spreadsheet application may interpret as the start
#: of a formula rather than literal text.
_DANGEROUS_LEADERS = ("=", "+", "-", "@", "\t")


def sanitize_cell(value: Any) -> Any:
    """Neutralise a single value if it would open a spreadsheet formula.

    A leading apostrophe forces every common spreadsheet application to
    treat the cell as literal text; it is invisible in the rendered cell,
    unlike prefixing with a quote character that would actually display.
    """
    if isinstance(value, str) and value[:1] in _DANGEROUS_LEADERS:
        return "'" + value
    return value


def _sanitize_index(idx: pd.Index) -> pd.Index:
    """Sanitise an index's labels only if they are string-like. A
    DatetimeIndex (a period index, as `engine.index.build_index` produces)
    is left completely untouched: rebuilding it as a plain object Index
    would replace pandas' date formatting in `to_csv` with `str(Timestamp)`,
    a real formatting regression for a risk that dtype cannot carry.
    """
    if pd.api.types.is_object_dtype(idx) or pd.api.types.is_string_dtype(idx):
        sanitized: list[Any] = [sanitize_cell(v) for v in idx]
        rebuilt: pd.Index = pd.Index(sanitized, name=idx.name)
        return rebuilt
    return idx


def sanitize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Sanitise every string-typed column of a DataFrame, plus its column
    and row index labels if they are string-like. Numeric and datetime
    columns and indexes are left untouched -- a spreadsheet formula
    requires a string cell that starts with a trigger character, and
    coercing a numeric or datetime value to string to check would corrupt
    the export for no security benefit.

    Column labels are in scope, not only cell values: a pivoted export
    (e.g. one row per period, one column per category) turns a
    user-supplied category name into a header cell, which a spreadsheet
    reads exactly as it would read any other first-row cell.
    """
    out = df.copy()
    for col in out.select_dtypes(include=["object", "string"]).columns:
        out[col] = out[col].map(sanitize_cell)
    out.columns = _sanitize_index(out.columns)
    out.index = _sanitize_index(out.index)
    return out


def safe_csv(df: pd.DataFrame, **kwargs: Any) -> str:
    """`DataFrame.to_csv` with every string cell sanitised against formula
    injection first. Every export path in this codebase should route
    through this rather than calling `.to_csv` directly, so the gap this
    closes (the cleaned-data and flagged-observations downloads in app.py
    wrote user-controlled `item_name`/`category` strings unsanitised) cannot
    reopen the next time an export is added.
    """
    result: str | None = sanitize_dataframe(df).to_csv(**kwargs)
    if result is None:
        raise ValueError(
            "safe_csv received a path_or_buf and so has nothing to return; "
            "call it with no destination argument and write the returned string yourself")
    return result


# ---------------------------------------------------------------------
# d. Restricted formula evaluation
# ---------------------------------------------------------------------
class FormulaError(ValueError):
    """A formula could not be parsed, or used a construct outside the
    permitted arithmetic subset."""


_ALLOWED_BINOPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

_ALLOWED_FUNCS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
}


def evaluate_formula(expr: str, variables: Mapping[str, float]) -> float:
    """Evaluate a restricted arithmetic expression: numeric literals, the
    operators in `_ALLOWED_BINOPS`, unary +/-, named references resolved
    only against `variables`, and calls to `_ALLOWED_FUNCS`. Nothing else.

    This never calls `eval` or `exec`. `ast.parse(expr, mode="eval")` only
    accepts a single expression, so `import x` is already a `SyntaxError`
    before evaluation begins. Every other construct this must reject --
    attribute access, subscripting, comprehensions, lambdas, and a call to
    anything not whitelisted -- is rejected because `_eval_node` has no
    branch for it and falls through to the final `FormulaError`: the
    default is deny, not a denylist a new syntax form could slip past.

    Nothing in this codebase calls this yet. It is built ahead of Phase 3,
    which will need a safe way to let an analyst define a derived series
    (for example a custom core-inflation exclusion formula) without that
    feature's first implementation being `eval` with a regex sanity check
    in front of it, which is how an unsafe evaluator usually ends up
    shipped by accident.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"could not parse {expr!r}: {exc}") from exc
    return _eval_node(tree.body, variables)


def _eval_node(node: ast.expr, variables: Mapping[str, float]) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise FormulaError(f"literal {node.value!r} is not a permitted number")
        return float(node.value)

    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise FormulaError(f"unknown name '{node.id}'")
        return float(variables[node.id])

    if isinstance(node, ast.BinOp):
        op = _ALLOWED_BINOPS.get(type(node.op))
        if op is None:
            raise FormulaError(f"operator '{type(node.op).__name__}' is not permitted")
        return op(_eval_node(node.left, variables), _eval_node(node.right, variables))

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.UAdd):
            return _eval_node(node.operand, variables)
        if isinstance(node.op, ast.USub):
            return -_eval_node(node.operand, variables)
        raise FormulaError(f"unary operator '{type(node.op).__name__}' is not permitted")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise FormulaError("only a whitelisted function may be called")
        if node.keywords:
            raise FormulaError("keyword arguments are not permitted")
        args = [_eval_node(a, variables) for a in node.args]
        return float(_ALLOWED_FUNCS[node.func.id](*args))

    raise FormulaError(f"expression element '{type(node).__name__}' is not permitted")


# ---------------------------------------------------------------------
# e. Statistical disclosure control
# ---------------------------------------------------------------------
def suppress_small_cells(
    df: pd.DataFrame, count_col: str, value_cols: Sequence[str], min_count: int | None = None
) -> pd.DataFrame:
    """Primary suppression: null out every value column of a row built from
    fewer than `min_count` quotes or outlets, and flag which rows were
    suppressed."""
    threshold = min_count if min_count is not None else get_settings().suppression_min_count
    out = df.copy()
    mask = out[count_col] < threshold
    for col in value_cols:
        out.loc[mask, col] = None
    out["suppressed"] = mask
    return out


def suppress_with_secondary(
    df: pd.DataFrame, group_col: str, value_col: str, count_col: str, min_count: int | None = None
) -> pd.DataFrame:
    """Primary suppression plus secondary (complementary) suppression.

    A group where exactly one cell was primary-suppressed and a published
    total for the group exists is not actually protected: the suppressed
    value is exactly `total - sum(everything else published)`. This
    additionally suppresses the smallest remaining published cell in any
    such group, so at least two values are unknown and the primary
    suppression can no longer be recovered by subtraction.

    This handles the single-total, single-level case. A hierarchy with
    several overlapping published totals over the same cells needs a
    cascading linear-programming solver to guarantee no combination of
    published totals recovers a suppressed cell; that is out of scope for
    this phase and is noted as such in docs/backlog.md.
    """
    out = suppress_small_cells(df, count_col, [value_col], min_count)
    out["secondary_suppressed"] = False
    for _, idx in out.groupby(group_col).groups.items():
        group = out.loc[idx]
        primary = group[group["suppressed"]]
        remaining = group[~group["suppressed"]]
        if len(primary) == 1 and len(remaining) >= 1:
            secondary_idx = remaining[value_col].idxmin()
            out.loc[secondary_idx, value_col] = None
            out.loc[secondary_idx, "suppressed"] = True
            out.loc[secondary_idx, "secondary_suppressed"] = True
    return out
