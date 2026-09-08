"""PriceLab.

Upload a price collection. The tool diagnoses it, cleans it, builds the index,
writes the findings and produces a deck and a report you can send on.

The interface holds no analytical logic. Everything it shows comes from the
library, which is tested independently, so what appears on screen is exactly
what a batch run would produce.
"""

import io
import json

import pandas as pd
import streamlit as st

from pricelab import (RunConfig, Schema, QualityConfig, ImputationConfig, IndexConfig,
                      standardise, validate, infer_schema, auto_configure, analyse,
                      build_deck, build_docx, build_markdown, method_note)

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

KIND_LABEL = {"quality": "Data quality", "structure": "Sample structure",
              "trend": "Price movement", "seasonal": "Seasonality",
              "method": "Method"}
METHODS = ["none", "class_mean", "carry_forward", "seasonal_hold"]


# ----------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def read_upload(file_bytes: bytes, name: str, sheet):
    buf = io.BytesIO(file_bytes)
    if name.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(buf, sheet_name=sheet)
    return pd.read_csv(buf)


@st.cache_data(show_spinner=False)
def sheet_names(file_bytes: bytes, name: str):
    if not name.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return None
    return pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names


@st.cache_resource(show_spinner=False)
def run_analysis(_df: pd.DataFrame, label: str, cfg_json: str):
    # cache_resource, not cache_data: the result carries matplotlib Figure
    # objects (deliberately built outside pyplot's registry, see charts.py),
    # and those are not reliably picklable, which is what cache_data requires
    # to store and copy a cached value.
    return analyse(_df, label, RunConfig.from_dict(json.loads(cfg_json)))


def landing():
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


# ----------------------------------------------------------------------
st.sidebar.markdown("### PriceLab")
st.sidebar.caption("Price collection to index, findings and deck.")

if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

upload = st.sidebar.file_uploader(
    "Price collection", type=["xlsx", "xlsm", "csv"],
    key=f"uploader_{st.session_state.uploader_key}")

if upload is not None:
    if st.sidebar.button("Clear data", use_container_width=True):
        st.session_state.uploader_key += 1
        st.rerun()

if upload is None:
    landing()
    st.stop()

file_bytes = upload.getvalue()
sheets = sheet_names(file_bytes, upload.name)
sheet = st.sidebar.selectbox("Worksheet", sheets) if sheets else 0
raw = read_upload(file_bytes, upload.name, sheet)

guess = infer_schema(raw)
with st.sidebar.expander("Column mapping", expanded=False):
    st.caption("Detected automatically. Correct anything that looks wrong.")
    cols = list(raw.columns)

    def sel(lbl, cur, optional=False):
        opts = ([""] if optional else []) + cols
        return st.selectbox(lbl, opts, index=opts.index(cur) if cur in opts else 0)

    schema = Schema(
        date=sel("Period", guess.date), item_id=sel("Item identifier", guess.item_id),
        item_name=sel("Item name", guess.item_name),
        category=sel("Category", guess.category), price=sel("Price", guess.price),
        weight=sel("Expenditure weight", guess.weight or "", optional=True) or None)

df = standardise(raw, schema)
report = validate(df)
label = st.sidebar.text_input("Title for the outputs", upload.name.rsplit(".", 1)[0])

if not report.passed:
    st.error("This file cannot be analysed as mapped.")
    for e in report.errors:
        st.error(e)
    st.caption("Correct the column mapping in the sidebar, or fix the source file.")
    st.dataframe(raw.head(20), use_container_width=True)
    st.stop()

# Automatic configuration, then optional override
auto_cfg, decisions = auto_configure(df, label)

with st.sidebar.expander("Adjust the method", expanded=False):
    st.caption("Chosen from the diagnosis. Change any of them and the analysis, deck "
               "and report all update.")
    window = st.slider("Reference window, periods", 5, 25,
                       auto_cfg.quality.reference_window, step=2)
    lo = st.slider("Fault band, lower", 0.5, 2.0, auto_cfg.quality.scale_log10_low, 0.1)
    hi = st.slider("Fault band, upper", 2.0, 4.0, auto_cfg.quality.scale_log10_high, 0.1)
    do_repair = st.checkbox("Repair faults rather than drop", True)
    formula = st.selectbox("Elementary formula", ["jevons", "dutot", "carli", "laspeyres"],
                           index=["jevons", "dutot", "carli",
                                  "laspeyres"].index(auto_cfg.index.formula))
    chained = st.radio("Chaining", ["Chained", "Fixed base"], horizontal=True) == "Chained"
    by_cat = dict(auto_cfg.imputation.by_category)
    if by_cat:
        st.markdown("**Imputation**")
        for c, m in list(by_cat.items()):
            by_cat[c] = st.selectbox(c, METHODS, index=METHODS.index(m), key=f"imp_{c}")

cfg = RunConfig(
    schema=schema,
    quality=QualityConfig(missing_codes=auto_cfg.quality.missing_codes,
                          reference_window=window, scale_log10_low=lo,
                          scale_log10_high=hi, repair_scale_errors=do_repair),
    imputation=ImputationConfig(default_method="none", by_category=by_cat),
    index=IndexConfig(formula=formula, chained=chained,
                      min_matched_items=auto_cfg.index.min_matched_items),
    label=label)

with st.spinner("Diagnosing, cleaning, indexing and writing the findings…"):
    out = run_analysis(df, label, cfg.to_json())

res, nar, charts = out["result"], out["narrative"], out["charts"]
I, yoy = res["indices"], res["inflation"]
years = (I.index[-1] - I.index[0]).days / 365.25

# ----------------------------------------------------------------------
st.markdown('<div class="pl-eyebrow">Analysis complete</div>', unsafe_allow_html=True)
st.markdown(f'<div class="pl-headline">{nar.headline}</div>', unsafe_allow_html=True)
st.markdown(f'<div class="pl-sub">{nar.subtitle}</div>', unsafe_allow_html=True)

d1, d2, d3, d4 = st.columns(4)
with d1:
    st.download_button(
        "Slide deck", build_deck(res, nar, dict(charts), label), f"{label} analysis.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        use_container_width=True, type="primary")
with d2:
    st.download_button(
        "Written report", build_docx(res, nar, dict(charts), label), f"{label} report.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        use_container_width=True)
with d3:
    st.download_button("Cleaned data", res["imputed"].to_csv(index=False),
                       f"{label} cleaned.csv", "text/csv", use_container_width=True)
with d4:
    st.download_button("Configuration", cfg.to_json(), "pricelab_config.json",
                       "application/json", use_container_width=True,
                       help="Reproduces every figure in this session")

st.divider()
tabs = st.tabs(["Findings", "Index", "Data quality", "What the tool decided", "Method note"])

# --------------------------------------------------------------- Findings
with tabs[0]:
    m = st.columns(4)
    if "All items" in I.columns:
        m[0].metric(f"All items, {I.index[0]:%b %Y} = 100", f"{I['All items'].iloc[-1]:.1f}")
        m[1].metric("Average annual rate",
                    f"{((I['All items'].iloc[-1] / 100) ** (1 / years) - 1) * 100:.1f}%")
        s_ = yoy["All items"].dropna()
        if len(s_):
            m[2].metric("Peak rate", f"{s_.max():.1f}%", f"{s_.idxmax():%b %Y}",
                        delta_color="off")
    q = res["quality"]["flag_summary"]["count"]
    m[3].metric("Faults repaired",
                f"{int(q.get('scale_error_x100', 0) + q.get('scale_error_div100', 0)):,}")
    st.markdown("")

    for f in nar.findings:
        st.markdown(f'<div class="pl-eyebrow">{KIND_LABEL.get(f.kind, f.kind)}</div>',
                    unsafe_allow_html=True)
        st.markdown(f"##### {f.headline}")
        st.write(f.detail)
        if f.evidence:
            st.markdown(f'<div class="pl-ev">{f.evidence}</div>', unsafe_allow_html=True)
        if f.chart and f.chart in charts:
            st.pyplot(charts[f.chart], use_container_width=True)
        if f.table is not None and len(f.table):
            with st.expander("Supporting figures"):
                st.dataframe(f.table, use_container_width=True, hide_index=True)
        if f.action:
            st.info(f.action, icon="➡️")
        st.divider()

# ------------------------------------------------------------------ Index
with tabs[1]:
    st.pyplot(charts["index"], use_container_width=True)
    c1, c2 = st.columns([3, 2])
    with c1:
        if "inflation" in charts:
            st.pyplot(charts["inflation"], use_container_width=True)
    with c2:
        st.markdown("**Level and annualised rate**")
        st.dataframe(pd.DataFrame({
            "Final level": I.iloc[-1].round(1),
            "Annualised %": (((I.iloc[-1] / I.iloc[0]) ** (1 / years) - 1) * 100).round(2),
        }).sort_values("Final level", ascending=False), use_container_width=True)
    st.markdown("**Matched items behind each comparison**")
    st.caption("An index built on two matched items is a weaker statistic than one built "
               "on six, and that difference is invisible in the published series.")
    st.pyplot(charts["coverage"], use_container_width=True)
    st.download_button("Download index series", I.round(3).to_csv(),
                       f"{label} indices.csv", "text/csv")

# ----------------------------------------------------------- Data quality
with tabs[2]:
    clean = res["clean"]
    st.pyplot(charts["quality_bands"], use_container_width=True)
    st.caption("Faults separate into bands rather than forming a continuous tail. Genuine "
               "volatility does not cluster at exactly one hundred times the local level; "
               "a unit of measurement fault does. That shape is the diagnosis, and it is "
               "what makes the true value recoverable.")

    mech = res["quality"]["missing_mechanisms"]
    if len(mech):
        st.markdown("**Gap mechanisms**")
        show = mech.copy()
        show["calendar_months"] = show["calendar_months"].apply(
            lambda ms: ", ".join(pd.Timestamp(2000, mth, 1).strftime("%b") for mth in ms))
        show["first"] = pd.to_datetime(show["first"]).dt.strftime("%b %Y")
        show["last"] = pd.to_datetime(show["last"]).dt.strftime("%b %Y")
        st.dataframe(show, use_container_width=True, hide_index=True)

    st.markdown("**Every altered observation**")
    st.caption("Nothing is changed silently. If you dispute a threshold, adjust it in the "
               "sidebar and the whole analysis updates.")
    altered = clean.loc[clean["flag"] != "none",
                        ["period", "category", "item_name", "price_reported",
                         "price_clean", "flag", "log10_deviation"]]
    st.dataframe(altered, use_container_width=True, hide_index=True, height=320)
    st.download_button("Download flagged observations", altered.to_csv(index=False),
                       f"{label} flagged.csv", "text/csv")

# --------------------------------------------------- What the tool decided
with tabs[3]:
    st.markdown("#### Decisions taken automatically")
    st.caption("Every one is visible and overridable in the sidebar. A tool that makes "
               "choices you cannot see is not one you can defend.")
    # Read from the diagnostic pass, not out["decisions"]: analyse() only
    # returns decisions when it ran auto_configure itself, and the app always
    # passes an explicit config (auto-detected or overridden) so that list is
    # empty. The diagnosis behind the automatic default stays worth showing
    # even after the user has overridden it in the sidebar.
    for d in decisions:
        st.markdown(f'<div class="pl-card">{d}</div>', unsafe_allow_html=True)
    st.markdown("#### Validation")
    for k, v in report.facts.items():
        st.markdown(f"- **{k.title()}**: {v}")
    for w in report.warnings:
        st.warning(w)

# ------------------------------------------------------------ Method note
with tabs[4]:
    st.markdown(method_note(res))
    st.divider()
    st.download_button("Download report as Markdown", build_markdown(res, nar, label),
                       f"{label} report.md", "text/markdown")
    with st.expander("Configuration used"):
        st.code(cfg.to_json(), language="json")
