"""The signed-in session's own state out of a Streamlit AppTest, on any
Streamlit version the project allows.

Streamlit 1.63 exposed it through the private `filtered_state` of
`AppTest.session_state` and gave that object no dict methods; 1.64 removed
`filtered_state` and added `items()`, `keys()` and the rest. Every page test
reads it through here, so the next change is one edit, not eight.
"""

from __future__ import annotations

from typing import Any


def user_state(at: Any) -> dict[str, Any]:
    """User-level session state: Streamlit's internal `$$` keys removed."""
    state = at.session_state
    try:
        items = state.items()               # Streamlit >= 1.64
    except AttributeError:
        items = state.filtered_state.items()  # Streamlit <= 1.63
    return {k: v for k, v in items if not str(k).startswith("$$")}
