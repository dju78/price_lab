"""Streamlit pages: one module per lifecycle stage. Each exposes a single
`render()` function, decorated with `core.security.require_role`, and is
wired into navigation from app.py via `st.Page(module.render, ...)`.

No analytical logic lives here: every page calls into the `pricelab`
package facade exactly as the original single-page app.py did. Splitting
the workflow into pages moved code; it did not rewrite it.
"""
