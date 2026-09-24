"""Audit: the append-only, hash-chained event log; chain verification;
identifying which run an export came from; verifying that a registered
run still reproduces its registered headline and that its raw layer still
replays through its transformation log; rebuilding a registered forecast or
scenario and checking its backtest digest for digest; and replaying the
stored characteristics layers.

Open to every role, viewer included: the auditor in the platform's
audience list is a viewer with read access to the audit log, and none of
this changes anything. Before this page the log could only be read
through the evidence pack's extract sheet and `verify_chain` had no
caller in the product.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from pricelab.core import audit, db
from pricelab.core.config import get_settings
from pricelab.core.registry import IndexRunORM, _code_version, reproduce
from pricelab.core.security import safe_csv
from pricelab.data import store
from pricelab.reporting import readback

from . import common


def _events(limit: int, action: str | None, needle: str) -> pd.DataFrame:
    with db.session_scope() as s:
        query = s.query(audit.AuditEventORM).order_by(audit.AuditEventORM.id.desc())
        if action:
            query = query.filter(audit.AuditEventORM.action == action)
        rows = [{"id": e.id, "created_at": e.created_at, "actor": e.actor, "action": e.action,
                 "target": e.target, "params": e.params_json, "hash": e.hash[:12]}
                for e in query.limit(limit).all()
                if not needle or needle in e.target or needle in e.params_json or needle in e.actor]
    return pd.DataFrame(rows, columns=["id", "created_at", "actor", "action", "target", "params",
                                       "hash"])


def identify_export(data: bytes, name: str) -> Any:
    """Read the provenance stamp out of an exported file, whichever format
    it is, by extension then by sniffing."""
    lower = name.lower()
    readers = {
        ".csv": lambda b: readback.from_csv(b.decode("utf-8", errors="replace")),
        ".md": lambda b: readback.from_markdown(b.decode("utf-8", errors="replace")),
        ".docx": readback.from_docx, ".pptx": readback.from_pptx, ".xlsx": readback.from_xlsx,
        ".pdf": readback.from_pdf, ".xml": readback.from_sdmx,
    }
    for ext, reader in readers.items():
        if lower.endswith(ext):
            return reader(data)
    raise readback.StampNotFound(f"{name}: not an export format this tool writes")


def code_version_check(registered: str, running: str) -> dict[str, Any]:
    """Whether the code reproducing a run is the code that registered it.

    `reproduce` re-runs the stored input and configuration with the code
    running now. A matching headline with different code shows the change
    did not move that number, not that nothing changed; so the check passes
    only when both are the same clean commit."""
    from pricelab.core.registry import DIRTY_SUFFIX, describe_code_version

    same_clean = (registered == running and registered != "unknown"
                  and not registered.endswith(DIRTY_SUFFIX))
    if same_clean:
        return {"ok": True, "detail": f"reproduced with the registering commit {registered}"}
    return {"ok": False,
            "detail": (f"registered with {describe_code_version(registered)}; reproduced with "
                       f"{describe_code_version(running)}. The reproduction used the code "
                       "running now, so it is evidence about that code, not a replay of the "
                       "original")}


def verify_run(run_id: str, actor: str) -> dict[str, Any]:
    """Reproduce a registered run and check it against what the registry
    recorded; then, if the raw layer and its transformation log are in
    the store, replay the cleaned layer and compare it with the
    reproduction's. Returns the checks, each with a verdict and detail."""
    checks: dict[str, dict[str, Any]] = {}
    with db.session_scope() as s:
        row = s.query(IndexRunORM).filter_by(run_id=run_id).one()
        input_hash, headline_value, headline_series = row.input_hash, row.headline_value, row.headline_series
        registered_code = str(row.code_version)
        result = reproduce(s, run_id, actor=actor)
    series = headline_series or "All items"
    if headline_value is None:
        checks["headline"] = {"ok": False, "detail": "no headline recorded for this run"}
    else:
        live = float(result["indices"][series].iloc[-1])
        checks["headline"] = {"ok": abs(live - headline_value) < 1e-6,
                              "detail": f"registered {headline_value:.6f}, reproduced {live:.6f}"}
    checks["code_version"] = code_version_check(registered_code, _code_version())
    store_dir = Path(get_settings().store_dir)
    raw_path = store_dir / "raw" / f"{input_hash}.parquet"
    log_path = store_dir / "cleaned" / f"{input_hash}.log.json"
    if raw_path.exists():
        raw_layer = pd.read_parquet(raw_path)
        checks["raw_layer"] = {"ok": store.content_hash_of(raw_layer) == input_hash,
                               "detail": f"{raw_path.name} hashes to the registered input"}
        receipts = store.load_vintages(store_dir, input_hash)
        checks["receipt"] = {"ok": bool(receipts),
                             "detail": (f"{len(receipts)} receipt(s); first from "
                                        f"{receipts[0].received_by} at {receipts[0].received_at:%Y-%m-%d %H:%M}"
                                        if receipts else "no vintage receipt beside the raw layer")}
        if log_path.exists():
            log = store.TransformationLog.from_json(log_path.read_text(encoding="utf-8"))
            try:
                replayed = store.replay(raw_layer, log)
                same = replayed.reset_index(drop=True).equals(result["imputed"].reset_index(drop=True))
                checks["replay"] = {"ok": same, "detail": "transformation log replays the cleaned "
                                    "layer identically" if same else "replay differs from the "
                                    "reproduction's cleaned layer"}
            except store.ImmutableLayerError as exc:
                checks["replay"] = {"ok": False, "detail": str(exc)}
        else:
            checks["replay"] = {"ok": False, "detail": "no transformation log in the store for this input"}
    else:
        checks["raw_layer"] = {"ok": False, "detail": "raw layer not in this deployment's store "
                               "(the run may have been registered elsewhere or the store not backed up)"}
    with db.session_scope() as s:
        chain_ok, first_bad = audit.verify_chain(s)
    checks["audit_chain"] = {"ok": chain_ok, "detail": "intact" if chain_ok
                             else f"broken at event {first_bad}"}
    return checks


def verify_characteristics_layers() -> list[dict[str, Any]]:
    """Replay every characteristics layer in the store from its raw layer
    through its transformation log, one check per log."""
    store_dir = Path(get_settings().store_dir)
    checks: list[dict[str, Any]] = []
    for log_path in sorted((store_dir / "cleaned").glob("characteristics-*.log.json")):
        log = store.CharacteristicsTransformationLog.from_json(
            log_path.read_text(encoding="utf-8"))
        raw_path = store_dir / "raw" / f"{log.raw_content_hash}.parquet"
        name = f"characteristics {log.raw_content_hash[:12]}"
        if not raw_path.exists():
            checks.append({"layer": name, "ok": False,
                           "detail": "raw layer missing from this deployment's store"})
            continue
        try:
            cleaned = store.replay_characteristics(pd.read_parquet(raw_path), log)
        except store.ImmutableLayerError as exc:
            checks.append({"layer": name, "ok": False, "detail": str(exc)})
        else:
            checks.append({"layer": name, "ok": True,
                           "detail": f"replays identically ({len(cleaned):,} items, "
                                     f"{len(log.steps)} logged steps)"})
    return checks


def _verify_projections() -> None:
    from pricelab.core.registry import ProjectionRunORM, reproduce_projection

    st.markdown("#### Verify a registered forecast or scenario")
    with db.session_scope() as s:
        rows = s.query(ProjectionRunORM).order_by(ProjectionRunORM.id.desc()).limit(100).all()
        options = {f"{r.kind} {r.projection_id} of {r.series}, run {r.run_id} "
                   f"({r.created_by}, {r.created_at[:10]})": r.projection_id for r in rows}
    if not options:
        st.caption("No forecast or scenario has been registered yet.")
        return
    choice = st.selectbox("Projection", list(options), key="au_projection")
    if not st.button("Rebuild and verify", key="au_projection_go"):
        return
    with db.session_scope() as s:
        projection, matches = reproduce_projection(s, options[choice],
                                                   common.current_username())
    if matches:
        st.success("Rebuilt from the run's stored input and the registered specification: the "
                   "backtest errors and the path match the registered digests exactly.")
    else:
        st.error("Rebuilt, but the backtest errors or the path differ from the registered "
                 "digests: this projection does not reproduce.")
    st.caption(projection.label)


def _verify_characteristics() -> None:
    st.markdown("#### Replay the characteristics layers")
    if not st.button("Replay every characteristics layer", key="au_characteristics_go"):
        return
    checks = verify_characteristics_layers()
    if not checks:
        st.caption("No characteristics file has been stored in this deployment.")
        return
    common.record(audit.CALCULATION_RUN, "characteristics replay",
                  {"layers": len(checks), "ok": sum(c["ok"] for c in checks)})
    for check in checks:
        (st.success if check["ok"] else st.error)(f"{check['layer']}: {check['detail']}")


def render() -> None:
    st.markdown("### Audit")
    st.caption("The event log is append-only and hash-chained: each record carries the hash "
               "of the one before it, so an altered or deleted record breaks the chain.")

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("Verify the chain", type="primary"):
            with db.session_scope() as s:
                ok, first_bad = audit.verify_chain(s)
                n = s.query(audit.AuditEventORM).count()
            if ok:
                st.success(f"Chain intact: {n:,} events, every hash follows from the last.")
            else:
                st.error(f"Chain broken at event {first_bad}: that record, or one before it, "
                         "was altered or removed after it was written.")
    with c2:
        st.caption("Verification recomputes every record's hash from its content and the "
                   "previous hash, from the genesis record to the newest.")

    st.markdown("#### Events")
    actions = sorted({v for k, v in vars(audit).items()
                      if k.isupper() and isinstance(v, str) and k != "GENESIS_HASH"})
    f1, f2, f3 = st.columns([2, 2, 1])
    action = f1.selectbox("Action", ["(all)"] + actions)
    needle = f2.text_input("Containing (target, actor or parameters)", "")
    limit = int(f3.number_input("Show", 50, 5000, 500, step=50))
    events = _events(limit, None if action == "(all)" else action, needle.strip())
    st.dataframe(events, use_container_width=True, hide_index=True, height=360)
    if len(events) and st.download_button("Download this extract", safe_csv(events, index=False),
                                          "audit extract.csv", "text/csv"):
        common.record(audit.EXPORT, "audit extract.csv", {"rows": len(events)})

    st.markdown("#### Identify an export")
    st.caption("Upload a file this tool exported -- CSV, Markdown, Word, deck, Excel pack, "
               "PDF bulletin or SDMX -- to read its provenance stamp and find the run.")
    export = st.file_uploader("Exported file", key="audit_export",
                              type=["csv", "md", "docx", "pptx", "xlsx", "pdf", "xml"])
    if export is not None:
        try:
            stamp = identify_export(export.getvalue(), export.name)
        except readback.StampNotFound as exc:
            st.error(f"No provenance stamp found: {exc}")
        else:
            st.success(f"Run `{stamp.run_id}`" + ("" if stamp.registered else " (not registered)")
                       + f", data vintage `{stamp.data_vintage[:16]}…`, code `{stamp.code_version}`, "
                       f"generated {stamp.generated_at}.")
            if stamp.non_standard_formula:
                st.warning(f"Non-standard formula: {stamp.non_standard_expression}")
            st.dataframe(pd.DataFrame(stamp.rows(), columns=["field", "value"]),
                         use_container_width=True, hide_index=True)
            if stamp.registered:
                st.session_state["audit_run_id"] = stamp.run_id

    _verify_projections()
    _verify_characteristics()

    st.markdown("#### Verify a registered run")
    with db.session_scope() as s:
        runs = s.query(IndexRunORM).order_by(IndexRunORM.created_at.desc()).limit(100).all()
        options = {f"{r.label} — {r.run_id} (vintage {r.vintage}{', approved' if r.approved else ''})":
                   r.run_id for r in runs}
    if not options:
        st.caption("No run has been registered yet.")
        return
    preselect = st.session_state.get("audit_run_id")
    labels = list(options)
    default = next((i for i, k in enumerate(labels) if options[k] == preselect), 0)
    choice = st.selectbox("Run", labels, index=default)
    if st.button("Reproduce and verify"):
        checks = verify_run(options[choice], common.current_username())
        common.record(audit.CALCULATION_RUN, f"run {options[choice]}", {
            "verification": {k: v["ok"] for k, v in checks.items()}})
        for name, check in checks.items():
            (st.success if check["ok"] else st.error)(f"{name}: {check['detail']}")
        st.caption("The reproduction re-ran the pipeline from the registry's stored input and "
                   "configuration; the replay re-derived the cleaned layer from the raw "
                   "Parquet layer through the transformation log.")
