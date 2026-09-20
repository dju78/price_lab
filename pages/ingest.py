"""Ingest: upload a price collection, map its columns, review what the tool
decided automatically, adjust the method, and compile the run.

Moved from the top of the original single-page app.py, unchanged in logic:
the same `infer_schema`, `auto_configure`, `standardise` and `validate`
calls, the same sidebar widgets, now living on their own page instead of
running unconditionally at the top of every script execution.
"""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from pricelab import (
    ImputationConfig,
    IndexConfig,
    QualityConfig,
    RunConfig,
    Schema,
    analyse,
    auto_configure,
    infer_schema,
    standardise,
    validate,
)
from pricelab.core import audit
from pricelab.core.cache import content_key, get_analysis_cache
from pricelab.core.config import get_settings
from pricelab.core.models import Role
from pricelab.core.security import require_role

from . import common

#: Kept in sync with the `st.file_uploader(type=[...])` call in `render`
#: below; also enforced explicitly in `validate_upload` because the widget's
#: own `type=` restriction is a client-side filter on the picker dialog, not
#: a guarantee about what the server actually receives.
ALLOWED_UPLOAD_EXTENSIONS = (".xlsx", ".xlsm", ".csv")


def validate_upload(name: str, size_bytes: int, max_mb: float) -> str | None:
    """Check an upload's name and size before a single byte of it is read.

    Returns an error message if the upload should be rejected, or None if
    it is acceptable. Deliberately takes plain values rather than a
    Streamlit `UploadedFile`, so it is testable without driving the actual
    file-upload widget, and deliberately never touches `.getvalue()`: the
    whole point is to decide before the file's bytes are pulled into memory.
    """
    if not name.lower().endswith(ALLOWED_UPLOAD_EXTENSIONS):
        return (f"'{name}' is not one of the accepted file types "
                f"({', '.join(ALLOWED_UPLOAD_EXTENSIONS)}).")
    size_mb = size_bytes / (1024 * 1024)
    if size_mb > max_mb:
        return (f"This file is {size_mb:.1f} MB, which is over the {max_mb:.0f} MB limit "
                "configured for this deployment. Upload a smaller extract, or ask an "
                "administrator to raise PRICELAB_UPLOAD_MAX_MB.")
    return None


@st.cache_data(show_spinner=False)
def _read_upload(file_bytes: bytes, name: str, sheet: object) -> pd.DataFrame:
    buf = io.BytesIO(file_bytes)
    if name.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(buf, sheet_name=sheet)
    return pd.read_csv(buf)


@st.cache_data(show_spinner=False)
def _sheet_names(file_bytes: bytes, name: str) -> list[str] | None:
    if not name.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return None
    return pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names


def _landing() -> None:
    st.markdown('<div class="pl-eyebrow">PriceLab</div>', unsafe_allow_html=True)
    st.markdown('<div class="pl-headline">Upload a price collection. '
                'Get the analysis and the presentation.</div>', unsafe_allow_html=True)
    st.markdown('<div class="pl-sub">For anyone who has to turn a messy price collection '
                'into a defensible index, and then explain it to someone else.</div>',
                unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**It diagnoses before it cleans**")
        st.caption("Missing values are classified by mechanism first. A gap that recurs "
                   "every winter is seasonal unavailability; a gap that hits every item at "
                   "once for three months is collection failure. They get different "
                   "treatments, because they are different problems.")
    with c2:
        st.markdown("**It repairs rather than deletes**")
        st.caption("Where an outlier's cause is a known factor of one hundred, the true "
                   "value is recoverable. Every change is flagged, listed and exportable, "
                   "so nothing is altered silently and any decision can be reversed.")
    with c3:
        st.markdown("**It writes the findings**")
        st.caption("The tool ranks what it found by materiality, states each finding with "
                   "the number behind it, and exports a slide deck with speaker notes and "
                   "a written report carrying the full method note.")

    st.divider()
    st.markdown("#### What your file needs")
    st.markdown("One row per item per period, with columns for the period, an item "
                "identifier, a category and a price. Excel or CSV. Columns are detected "
                "automatically and you can correct the mapping if it guesses wrong. An "
                "optional expenditure weight column turns the aggregate into a weighted "
                "index.")
    st.dataframe(pd.DataFrame({
        "Date": ["2015-01-01", "2015-01-01", "2015-02-01"],
        "Category": ["Bread", "Bread", "Bread"],
        "Item_ID": ["120150101", "120150102", "120150101"],
        "Item_Name": ["White 550g loaf", "Brown 550g loaf", "White 550g loaf"],
        "Reported_Price": [1.17, 0.96, 1.19],
    }), hide_index=True, use_container_width=True)


@require_role(Role.ADMINISTRATOR, Role.COMPILER)
def render() -> None:
    st.markdown("### Ingest and compile")
    st.caption("Upload a collection, confirm the column mapping, review what the tool "
               "decided automatically, adjust the method if you disagree, then compile. "
               "Every other page reads the result this page produces.")

    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = 0

    upload = st.file_uploader(
        "Price collection", type=["xlsx", "xlsm", "csv"],
        key=f"uploader_{st.session_state.uploader_key}")

    if upload is not None and st.button("Clear data"):
        st.session_state.uploader_key += 1
        for key in ("file_bytes", "file_name", "analysis", "loaded_run_id"):
            st.session_state.pop(key, None)
        st.rerun()

    if upload is None:
        if not common.has_active_analysis():
            _landing()
        return

    settings = get_settings()
    upload_error = validate_upload(upload.name, upload.size, settings.upload_max_mb)
    if upload_error:
        st.error(upload_error)
        return

    file_bytes = upload.getvalue()
    st.session_state["file_bytes"] = file_bytes
    st.session_state["file_name"] = upload.name

    sheets = _sheet_names(file_bytes, upload.name)
    sheet = st.selectbox("Worksheet", sheets) if sheets else 0
    raw = _read_upload(file_bytes, upload.name, sheet)

    guess = infer_schema(raw)
    with st.expander("Column mapping", expanded=False):
        st.caption("Detected automatically. Correct anything that looks wrong.")
        cols = list(raw.columns)

        def sel(lbl: str, cur: str, optional: bool = False) -> str:
            opts = ([""] if optional else []) + cols
            return str(st.selectbox(lbl, opts, index=opts.index(cur) if cur in opts else 0))

        schema = Schema(
            date=sel("Period", guess.date), item_id=sel("Item identifier", guess.item_id),
            item_name=sel("Item name", guess.item_name),
            category=sel("Category", guess.category), price=sel("Price", guess.price),
            weight=sel("Expenditure weight", guess.weight or "", optional=True) or None)

    df = standardise(raw, schema)
    report = validate(df)
    label = st.text_input("Title for the outputs", upload.name.rsplit(".", 1)[0])

    if not report.passed:
        st.error("This file cannot be analysed as mapped.")
        for e in report.errors:
            st.error(e)
        st.caption("Correct the column mapping above, or fix the source file.")
        st.dataframe(raw.head(20), use_container_width=True)
        return

    common.record(audit.DATA_LOAD, upload.name, {
        "rows": report.facts.get("rows"), "categories": report.facts.get("categories"),
        "items": report.facts.get("items")})

    auto_cfg, decisions = auto_configure(df, label)

    with st.expander("Decisions taken automatically", expanded=False):
        st.caption("Every one is visible and overridable below. A tool that makes choices "
                   "you cannot see is not one you can defend.")
        for d in decisions:
            st.markdown(f'<div class="pl-card">{d}</div>', unsafe_allow_html=True)
        st.markdown("**Validation**")
        for k, v in report.facts.items():
            st.markdown(f"- **{k.title()}**: {v}")
        for w in report.warnings:
            st.warning(w)

    with st.expander("Adjust the method", expanded=False):
        st.caption("Chosen from the diagnosis. Change any of them and the compiled run "
                   "updates on every other page.")
        window = st.slider("Reference window, periods", 5, 25,
                           auto_cfg.quality.reference_window, step=2)
        lo = st.slider("Fault band, lower", 0.5, 2.0, auto_cfg.quality.scale_log10_low, 0.1)
        hi = st.slider("Fault band, upper", 2.0, 4.0, auto_cfg.quality.scale_log10_high, 0.1)
        do_repair = st.checkbox("Repair faults rather than drop", True)
        formula = st.selectbox("Elementary formula", common.INDEX_FORMULAS,
                               index=common.INDEX_FORMULAS.index(auto_cfg.index.formula))
        chained = st.radio("Chaining", ["Chained", "Fixed base"], horizontal=True) == "Chained"
        by_cat = dict(auto_cfg.imputation.by_category)
        if by_cat:
            st.markdown("**Imputation**")
            for c, m in list(by_cat.items()):
                by_cat[c] = st.selectbox(c, common.IMPUTATION_METHODS,
                                         index=common.IMPUTATION_METHODS.index(m), key=f"imp_{c}")

    cfg = RunConfig(
        schema=schema,
        quality=QualityConfig(missing_codes=auto_cfg.quality.missing_codes,
                              reference_window=window, scale_log10_low=lo,
                              scale_log10_high=hi, repair_scale_errors=do_repair),
        imputation=ImputationConfig(default_method="none", by_category=by_cat),
        index=IndexConfig(formula=formula, chained=chained,
                          min_matched_items=auto_cfg.index.min_matched_items),
        label=label)

    if cfg.quality.repair_scale_errors != auto_cfg.quality.repair_scale_errors:
        common.record(audit.QUALITY_OVERRIDE, label, {"repair_scale_errors": do_repair})
    if by_cat != auto_cfg.imputation.by_category:
        common.record(audit.IMPUTATION_OVERRIDE, label, {"by_category": by_cat})
    if cfg.to_json() != auto_cfg.to_json():
        common.record(audit.CONFIGURATION_CHANGE, label, {
            "formula": formula, "chained": chained, "reference_window": window})

    cache = get_analysis_cache()
    key = content_key(file_bytes, cfg.to_json(), label)
    cached = cache.get(key)
    if cached is None:
        with st.spinner("Diagnosing, cleaning, indexing and writing the findings…"):
            cached = analyse(df, label, cfg)
        cached.pop("charts", None)  # rebuilt fresh on demand; see core/cache.py
        cache.set(key, cached)
        common.record(audit.CALCULATION_RUN, label, {
            "formula": formula, "chained": chained, "rows": len(df)})

    st.session_state["analysis"] = {**cached, "label": label}
    st.session_state["input_df"] = df
    st.session_state.pop("loaded_run_id", None)

    if "indices" not in cached["result"]:
        st.error("The pipeline could not build an index from this data; see Validation above.")
        return

    st.success(
        f"Compiled **{label}**. Open Quality, Imputation, Index build, Findings, "
        "Diagnostics or Reports to see the result.")
