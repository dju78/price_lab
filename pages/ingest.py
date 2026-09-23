"""Ingest: upload a price collection, map its columns, review what the tool
decided automatically, adjust the method, and compile the run.

Column mapping and data-quality validation go through `data.mapping` and
`data.validation` (Phase 2): a mapping must be confirmed once per distinct
file (by content hash) before compiling, and a critical validation finding
blocks compiling until an analyst accepts, excludes, corrects or justifies
it, with the decision persisted and audited rather than only shown once
and forgotten.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from pricelab import (
    ImputationConfig,
    IndexConfig,
    QualityConfig,
    RunConfig,
    Schema,
    auto_configure,
    standardise,
    validate,
)
from pricelab.core import audit, db, ledger
from pricelab.core.config import QualityAdjustmentConfig, get_settings
from pricelab.core.models import Role
from pricelab.core.ratelimit import RateLimited, upload_limiter
from pricelab.core.security import FormulaError, require_role
from pricelab.data import classification, loaders, mapping, store, validation
from pricelab.engine.custom import (
    ELEMENTARY_VARIABLES,
    custom_formula_parameters,
    validate_aggregate_formula,
    validate_elementary_formula,
)
from pricelab.engine.index import QUANTITY_FORMULAE, formula_availability, quantity_series

from . import common

#: Kept in sync with the `st.file_uploader(type=[...])` call in `render`
#: below; also enforced explicitly in `validate_upload` because the widget's
#: own `type=` restriction is a client-side filter on the picker dialog, not
#: a guarantee about what the server actually receives.
ALLOWED_UPLOAD_EXTENSIONS = (".xlsx", ".xlsm", ".xls", ".csv", ".parquet", ".json")


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
def _read_upload(file_bytes: bytes, name: str, sheet: object, max_mb: float) -> loaders.LoadResult:
    """`data.loaders.read_upload`: format dispatch (CSV, Excel, Parquet,
    JSON), encoding and delimiter detection, header-row inference, and
    the memory-footprint cap enforced before and during the read. What
    it detected is shown beside the preview, because a wrong delimiter
    guess is something the analyst can see and a silent one is not."""
    return loaders.read_upload(file_bytes, name, sheet_name=sheet if sheet is not None else 0,
                               max_mb=max_mb)


@st.cache_data(show_spinner=False)
def _sheet_names(file_bytes: bytes, name: str) -> list[str] | None:
    return loaders.list_sheets(file_bytes, name)


def classification_codes_for(categories: list[str]) -> list[str] | None:
    """The classification tree's codes, when this collection's categories
    are codes in it -- the conformity check only makes sense against a
    scheme the collection is actually coded to, and a collection of plain
    labels ("Bread") would otherwise fail conformity wholesale."""
    with db.session_scope() as s:
        codes = [str(n.code) for n in s.query(classification.ClassificationNodeORM).all()]
    known = set(codes)
    return codes if any(c in known for c in categories) else None


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
                "identifier, a category and a price. Excel, CSV, Parquet or JSON; encoding, "
                "delimiter and a title row above the header are detected. Columns are "
                "detected automatically and you can correct the mapping if it guesses wrong. "
                "An optional expenditure weight column turns the aggregate into a weighted "
                "index with contributions.")
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
        "Price collection", type=["xlsx", "xlsm", "xls", "csv", "parquet", "json"],
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

    # Rate limit, per signed-in user, checked before the bytes are read:
    # the same file re-rendering on every Streamlit rerun is one upload,
    # keyed by name and size, not one per rerun.
    upload_key = f"{upload.name}:{upload.size}"
    if st.session_state.get("counted_upload") != upload_key:
        try:
            upload_limiter().acquire(common.current_username())
        except RateLimited as exc:
            st.error(f"Upload refused: {exc}")
            return
        st.session_state["counted_upload"] = upload_key

    file_bytes = upload.getvalue()
    st.session_state["file_bytes"] = file_bytes
    st.session_state["file_name"] = upload.name

    sheets = _sheet_names(file_bytes, upload.name)
    sheet = st.selectbox("Worksheet", sheets) if sheets else 0
    try:
        loaded = _read_upload(file_bytes, upload.name, sheet, settings.upload_max_mb)
    except loaders.MemoryFootprintError as exc:
        st.error(f"Upload refused before it was read: {exc}")
        return
    except loaders.LoaderError as exc:
        st.error(f"The file could not be read: {exc}")
        return
    raw = loaded.df
    detected = [f"{len(raw):,} rows"]
    if loaded.encoding:
        detected.append(f"encoding {loaded.encoding}")
    if loaded.delimiter:
        detected.append(f"delimiter {loaded.delimiter!r}")
    if not loaded.has_header:
        detected.append("no header row: the first line is data, so the columns are numbered "
                        "and must be mapped by position")
    elif loaded.header_row:
        detected.append(f"header found on row {loaded.header_row + 1} (rows above it skipped)")
    st.caption("Read: " + ", ".join(detected) + ".")

    file_hash = mapping.file_hash_of(file_bytes)
    confirm_key = f"mapping_confirmed_{file_hash}"

    with db.session_scope() as s:
        stored_schema = mapping.get_confirmed_mapping(s, file_hash)

    if stored_schema is not None:
        schema = stored_schema
        st.session_state[confirm_key] = True
        st.caption("Column mapping: using the mapping already confirmed for this exact file.")
    else:
        suggestions = mapping.suggest_column_mapping(raw)
        cols = list(raw.columns)
        with st.expander("Column mapping", expanded=True):
            st.caption("Detected automatically, with a confidence score per field. Correct "
                       "anything that looks wrong, then confirm before compiling.")

            def sel(lbl: str, suggestion: mapping.MappingSuggestion,
                    optional: bool = False) -> str:
                opts = ([""] if optional else []) + cols
                cur = suggestion.column or ""
                return str(st.selectbox(
                    f"{lbl} ({suggestion.confidence:.0%} confidence)", opts,
                    index=opts.index(cur) if cur in opts else 0))

            schema = Schema(
                date=sel("Period", suggestions["date"]),
                item_id=sel("Item identifier", suggestions["item_id"]),
                item_name=sel("Item name", suggestions["item_name"]),
                category=sel("Category", suggestions["category"]),
                price=sel("Price", suggestions["price"]),
                weight=sel("Expenditure weight", suggestions["weight"], optional=True) or None,
                quantity=sel("Quantity", suggestions["quantity"], optional=True) or None,
                expenditure=sel("Expenditure (price x quantity)", suggestions["expenditure"],
                                optional=True) or None,
                unit=sel("Unit of measurement", suggestions["unit"], optional=True) or None)
            st.caption("Quantity or expenditure (either; both are cross-checked) is what the "
                       "quantity-weighted formulae -- Paasche, Fisher, Törnqvist, Walsh, "
                       "Marshall-Edgeworth, the geometric forms and the unit value -- need. "
                       "Leave them blank for a price-only collection.")

            if st.button("Confirm column mapping"):
                with db.session_scope() as s:
                    mapping.store_confirmed_mapping(
                        s, file_hash, schema, actor=common.current_username())
                common.record(audit.MAPPING_CONFIRMED, upload.name, {"file_hash": file_hash})
                st.session_state[confirm_key] = True
                st.rerun()

        if not st.session_state.get(confirm_key):
            st.warning("Confirm the column mapping above before compiling.")
            return

    df = standardise(raw, schema)
    structural = validate(df)
    label = st.text_input("Title for the outputs", upload.name.rsplit(".", 1)[0])

    content_hash = store.content_hash_of(df)
    st.session_state["content_hash"] = content_hash
    # The immutable raw layer and the vintage receipt: written once per
    # distinct content (idempotent), so every later export can name the
    # file, the hash, the receipt time and who supplied it.
    if st.session_state.get("data_vintage_hash") != content_hash:
        vintage = store.record_upload(
            df, kind="prices", file_name=upload.name, file_bytes=file_bytes,
            actor=common.current_username(), directory=settings.store_dir)
        st.session_state["data_vintage"] = vintage.to_dict()
        st.session_state["data_vintage_hash"] = content_hash
        common.record(audit.DATA_LOAD, upload.name, {
            "content_hash": content_hash, "file_sha256": vintage.file_sha256,
            "raw_path": vintage.raw_path, "rows": len(df), "kind": "prices"})
    assessment = validation.assess(
        df, classification_codes=classification_codes_for(list(df["category"].astype(str).unique())),
        reference_date=pd.Timestamp.now(),
        expenditure_tolerance=QualityConfig().expenditure_tolerance)
    with db.session_scope() as s:
        already_overridden = {
            o.dimension for o in
            s.query(validation.ValidationOverrideORM).filter_by(content_hash=content_hash).all()}
    for dim in already_overridden:
        assessment.override(dim)

    if assessment.blocking:
        st.error("This file has critical data-quality findings that must be resolved before "
                 "it can be compiled.")
        for finding in assessment.by_severity(validation.Severity.CRITICAL):
            if finding.overridden:
                continue
            with st.expander(f"[{finding.dimension}] {finding.message}", expanded=True):
                decision = st.selectbox(
                    "Decision", ["accept", "exclude", "correct", "justify"],
                    key=f"decision_{content_hash}_{finding.dimension}")
                reason = st.text_input(
                    "Reason (required)", key=f"reason_{content_hash}_{finding.dimension}")
                if st.button("Submit", key=f"submit_{content_hash}_{finding.dimension}"):
                    if not reason.strip():
                        st.error("A reason is required.")
                    else:
                        with db.session_scope() as s:
                            validation.record_override(
                                s, actor=common.current_username(), content_hash=content_hash,
                                dimension=finding.dimension, decision=decision, reason=reason)
                        common.record(audit.VALIDATION_OVERRIDE, upload.name, {
                            "dimension": finding.dimension, "decision": decision})
                        st.rerun()
        st.caption("Correct the column mapping above, or fix the source file, or resolve each "
                   "finding above.")
        st.dataframe(raw.head(20), use_container_width=True)
        return

    common.record(audit.DATA_LOAD, upload.name, {
        "rows": structural.facts.get("rows"), "categories": structural.facts.get("categories"),
        "items": structural.facts.get("items")})

    auto_cfg, decisions = auto_configure(df, label)

    with st.expander("Decisions taken automatically", expanded=False):
        st.caption("Every one is visible and overridable below. A tool that makes choices "
                   "you cannot see is not one you can defend.")
        for d in decisions:
            st.markdown(f'<div class="pl-card">{d}</div>', unsafe_allow_html=True)
        st.markdown("**Validation**")
        for k, v in structural.facts.items():
            st.markdown(f"- **{k.title()}**: {v}")
        for finding in assessment.findings:
            if finding.severity == validation.Severity.CRITICAL:
                continue  # already resolved above, or this run would not have reached here
            level = {"high": st.warning, "medium": st.warning, "low": st.info}[finding.severity.value]
            level(f"[{finding.dimension}] {finding.message}")

    with st.expander("Adjust the method", expanded=False):
        st.caption("Chosen from the diagnosis. Change any of them and the compiled run "
                   "updates on every other page.")
        window = st.slider("Reference window, periods", 5, 25,
                           auto_cfg.quality.reference_window, step=2)
        lo = st.slider("Fault band, lower", 0.5, 2.0, auto_cfg.quality.scale_log10_low, 0.1)
        hi = st.slider("Fault band, upper", 2.0, 4.0, auto_cfg.quality.scale_log10_high, 0.1)
        do_repair = st.checkbox("Repair faults rather than drop", True)
        # Every formula the engine has, the unavailable ones labelled with
        # the reason rather than hidden: a compiler learns what a Fisher
        # needs instead of wondering where it went.
        availability = formula_availability(df)
        labels = {name: (name if availability.get(name) is None
                         else f"{name} -- unavailable: {availability[name]}")
                  for name in common.INDEX_FORMULAS}
        choice = st.selectbox("Index formula", [labels[n] for n in common.INDEX_FORMULAS],
                              index=common.INDEX_FORMULAS.index(auto_cfg.index.formula))
        formula = next(n for n, lbl in labels.items() if lbl == choice)
        formula_error: str | None = availability.get(formula)
        if formula_error:
            st.error(f"{formula} cannot be compiled on this collection: {formula_error}.")
        quantities = quantity_series(df, "price_reported")
        if quantities is not None and formula in QUANTITY_FORMULAE:
            st.caption("Quantities " + ("derived as expenditure / price (no quantity column was "
                                       "mapped); each result is flagged as built on derived "
                                       "quantities." if quantities.name == "quantity_derived"
                                       else "from the mapped quantity column.")
                       + (" With quantities, Laspeyres is the quantity-basket form; without, "
                          "the share-weighted (Young) form." if formula == "laspeyres" else ""))
        homogeneity: str | None = None
        if formula == "unit_value":
            homogeneity = st.text_area(
                "Homogeneity assertion (required for the unit value index)",
                help="The unit value index is only defined over items that are one homogeneous "
                     "product with additive quantities (CPI Manual 2020, 8.87). State why that "
                     "holds for every category here; the assertion is recorded with the run.") or None
            if not (homogeneity or "").strip():
                formula_error = "state the homogeneity assertion above"
                st.warning("The unit value index needs the homogeneity assertion before it can "
                           "be compiled.")

        # A compiler who needs a formula this tool does not ship writes it
        # here. It goes through the whitelist parser, never eval, and is
        # rejected on the spot rather than part way through a compile.
        custom_expression: str | None = None
        custom_error: str | None = None
        if formula == "custom":
            custom_expression = st.text_input(
                "Custom formula", value="(carli * harmonic_mean) ** 0.5",
                help="An arithmetic expression combining the standard elementary indices: "
                     f"{', '.join(ELEMENTARY_VARIABLES)}. A run using one is marked "
                     "non-standard in every export.")
            try:
                validate_elementary_formula(custom_expression or "")
            except FormulaError as exc:
                custom_error = str(exc)
                st.error(f"That formula cannot be used: {exc}")
            else:
                st.caption(
                    "This run will be marked as non-standard on the deck, the written "
                    "report and every data export.")

        chained = st.radio("Chaining", ["Chained", "Fixed base"], horizontal=True) == "Chained"

        # The three reference periods a price index actually has, chosen
        # from the periods the data contains. Defaults reproduce the
        # engine's own: first period for the price reference, the price
        # reference for the index reference, no weight reference.
        periods = [pd.Timestamp(p) for p in sorted(df["period"].unique())]
        period_labels = ["default"] + [f"{p:%Y-%m-%d}" for p in periods]
        st.markdown("**Reference periods**")
        price_ref = "default"
        if not chained:
            price_ref = st.selectbox(
                "Price reference period (denominator of every relative)", period_labels,
                help="Fixed base only: every period is compared with this one.")
        index_ref = st.selectbox(
            "Index reference period (the series reads 100 here)", period_labels,
            help="A presentational rebasing of the finished series; defaults to the price "
                 "reference period.")
        weight_ref = "default"
        has_weights = "weight" in df.columns and df["weight"].notna().any()
        if has_weights:
            weight_ref = st.selectbox(
                "Weight reference period (where the expenditure weights come from)",
                period_labels,
                help="Deliberately earlier than the price reference for a Lowe or Young "
                     "index; the price-updating effect is reported on Index build.")

        # The aggregate escape hatch: an expression over the category
        # index levels, same whitelist walker, same non-standard mark.
        aggregate_expression: str | None = None
        categories = sorted(df["category"].astype(str).unique())
        if len(categories) > 1:
            aggregate_expression = st.text_input(
                "Custom aggregate formula (optional)", value="",
                help="An expression over the category index levels of the same period, e.g. "
                     "(bread + milk) / 2, with all_items for the standard aggregate. Names are "
                     "the category labels in lower case with spaces as underscores. Leave blank "
                     "for the standard aggregate.") or None
            if aggregate_expression:
                try:
                    validate_aggregate_formula(
                        aggregate_expression, pd.Index([*categories, "All items"]))
                except FormulaError as exc:
                    custom_error = str(exc)
                    st.error(f"That aggregate formula cannot be used: {exc}")
                else:
                    st.caption("The aggregate will be analyst-defined and the run marked "
                               "non-standard in every export.")

        by_cat = dict(auto_cfg.imputation.by_category)
        if by_cat:
            st.markdown("**Imputation**")
            for c, m in list(by_cat.items()):
                by_cat[c] = st.selectbox(c, common.IMPUTATION_METHODS,
                                         index=common.IMPUTATION_METHODS.index(m), key=f"imp_{c}")

    if custom_error:
        st.info("Correct the custom formula above, or choose a standard one, to compile.")
        return
    if formula_error:
        st.info("Choose a formula this collection supports, or map the columns it needs, to "
                "compile.")
        return

    # Approved replacement valuations recorded against this exact data
    # (by content hash) ride in the configuration, so the registry hash,
    # the cache key and every export already cover them. Approvals are
    # made on the Quality adjustment page; this is where they take effect.
    with db.session_scope() as s:
        approved = ledger.active_entries(s, content_hash)

    cfg = RunConfig(
        schema=schema,
        quality=QualityConfig(missing_codes=auto_cfg.quality.missing_codes,
                              reference_window=window, scale_log10_low=lo,
                              scale_log10_high=hi, repair_scale_errors=do_repair),
        imputation=ImputationConfig(default_method="none", by_category=by_cat),
        index=IndexConfig(formula=formula, custom_formula=custom_expression, chained=chained,
                          custom_aggregate_formula=aggregate_expression,
                          homogeneity_justification=homogeneity,
                          price_reference_period=None if price_ref == "default" else price_ref,
                          index_reference_period=None if index_ref == "default" else index_ref,
                          weight_reference_period=None if weight_ref == "default" else weight_ref,
                          min_matched_items=auto_cfg.index.min_matched_items),
        quality_adjustment=QualityAdjustmentConfig(entries=approved),
        label=label)
    if approved:
        st.caption(f"{len(approved)} approved quality adjustment(s) for this data will be "
                   "applied; see the Quality adjustment page.")

    if cfg.quality.repair_scale_errors != auto_cfg.quality.repair_scale_errors:
        common.record(audit.QUALITY_OVERRIDE, label, {"repair_scale_errors": do_repair})
    if by_cat != auto_cfg.imputation.by_category:
        common.record(audit.IMPUTATION_OVERRIDE, label, {"by_category": by_cat})
    if cfg.to_json() != auto_cfg.to_json():
        common.record(audit.CONFIGURATION_CHANGE, label, {
            "formula": formula, "chained": chained, "reference_window": window,
            **custom_formula_parameters(cfg.index)})

    cached = common.compile_and_store(df, cfg, label, file_bytes, trigger="ingest")

    if "indices" not in cached["result"]:
        st.error("The pipeline could not build an index from this data; see Validation above.")
        return

    st.success(
        f"Compiled **{label}**. Open Quality, Imputation, Index build, Findings, "
        "Diagnostics or Reports to see the result.")
