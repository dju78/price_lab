"""From a raw upload to a published PDF bulletin, and back: the headline
printed in the bulletin is traced, hop by hop, to the individual source
quotes it was computed from, through the provenance stamp, the run
registry, the immutable raw layer, the vintage receipt and the audit
trail. Each hop is a real lookup that would fail if the link were
broken -- the trace is demonstrated, not asserted.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import pytest
from dbtarget import database_url
from pypdf import PdfReader

from pricelab import analyse, auto_configure, infer_schema, run_pipeline, standardise
from pricelab.core import audit, db
from pricelab.core.config import RunConfig, get_settings
from pricelab.core.provenance import build_stamp
from pricelab.core.registry import IndexRunORM, _hash_dataframe, approve_run, register_run
from pricelab.data import store
from pricelab.reporting import bulletin, readback

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "supermarket_price_collection.xlsx"


@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    """A fresh database and an empty Parquet store, like a new install."""
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "e2e.db"))
    monkeypatch.setenv("PRICELAB_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def test_upload_to_bulletin_and_the_headline_traces_back_to_source_quotes(deployment):
    settings = get_settings()
    actor = "compiler1"
    trace: list[str] = []

    # ---- 1. Upload: raw layer, vintage receipt, audit event -------------
    file_bytes = FIXTURE.read_bytes()
    raw = pd.read_excel(io.BytesIO(file_bytes), sheet_name="Price_Data")
    df = standardise(raw, infer_schema(raw))
    vintage = store.record_upload(df, kind="prices", file_name=FIXTURE.name, file_bytes=file_bytes,
                                  actor=actor, directory=settings.store_dir)
    with db.session_scope() as s:
        audit.record_event(s, actor, audit.DATA_LOAD, FIXTURE.name, {
            "content_hash": vintage.content_hash, "file_sha256": vintage.file_sha256,
            "raw_path": vintage.raw_path, "rows": len(df), "kind": "prices"})
    trace.append(f"upload {FIXTURE.name} -> raw layer {vintage.raw_path} (content {vintage.content_hash[:12]})")

    # ---- 2. Compile, log the cleaned layer, audit the run ----------------
    cfg, _decisions = auto_configure(df, "Supermarket prices")
    out = analyse(df, "Supermarket prices", cfg)
    res = out["result"]
    store.write_cleaned_layer(df, cfg, settings.store_dir)
    with db.session_scope() as s:
        audit.record_event(s, actor, audit.CALCULATION_RUN, "Supermarket prices", {
            "correlation_id": res["correlation_id"], "content_hash": vintage.content_hash,
            "formula": cfg.index.formula})

    # ---- 3. Register, approve, release the bulletin ---------------------
    with db.session_scope() as s:
        run = register_run(s, df, cfg, "Supermarket prices", result=res,
                           data_source=vintage.source, data_received_at=vintage.received_at.isoformat())
        approve_run(s, run.run_id)
        audit.record_event(s, actor, audit.CALCULATION_RUN, f"run {run.run_id}",
                           {"registered": True, "approved": True})
        stamp = build_stamp(res, "Supermarket prices", run=run)
        pdf = bulletin.build_bulletin(res, out["narrative"], out["charts"], stamp, run=run)
        run_id, registry_headline = run.run_id, run.headline_value
    trace.append(f"registered run {run_id}, headline {registry_headline:.6f}, bulletin {len(pdf)} bytes")

    # ---- 4. TRACE. Start from the PDF alone. -----------------------------
    # 4a. The stamp in the PDF names the run and the data vintage.
    found = readback.from_pdf(pdf)
    assert found.run_id == run_id and found.registered
    assert found.data_vintage == vintage.content_hash
    page1 = PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert f"{registry_headline:.1f}" in page1, "the printed headline is the registry's number"
    trace.append(f"PDF stamp -> run {found.run_id}, data vintage {found.data_vintage[:12]}")

    # 4b. The run id resolves in the registry to the headline and the input hash.
    with db.session_scope() as s:
        row = s.query(IndexRunORM).filter_by(run_id=found.run_id).one()
        assert row.headline_value == pytest.approx(registry_headline)
        assert row.input_hash == found.data_vintage
        registered_input = pd.read_parquet(io.BytesIO(row.input_parquet))
        registered_config = RunConfig.from_dict(json.loads(row.config_json))
    assert _hash_dataframe(registered_input) == found.data_vintage
    trace.append(f"registry {row.run_id} -> input hash {row.input_hash[:12]} == stamp vintage")

    # 4c. The data vintage is the raw layer's file name; the file hashes to it.
    raw_path = Path(settings.store_dir) / "raw" / f"{found.data_vintage}.parquet"
    assert raw_path.exists()
    raw_layer = pd.read_parquet(raw_path)
    assert store.content_hash_of(raw_layer) == found.data_vintage
    receipts = store.load_vintages(settings.store_dir, found.data_vintage)
    assert receipts and receipts[0].received_by == actor and receipts[0].file_name == FIXTURE.name
    assert receipts[0].file_sha256 == vintage.file_sha256
    trace.append(f"raw layer {raw_path.name} hashes to the vintage; receipt: {receipts[0].file_name} "
                 f"from {receipts[0].received_by} at {receipts[0].received_at:%Y-%m-%d %H:%M}")

    # 4d. The audit trail links the same identifiers, and its chain is intact.
    with db.session_scope() as s:
        extract = audit.extract_for_run(s, label="Supermarket prices", content_hash=found.data_vintage,
                                        run_id=found.run_id, correlation_id=res["correlation_id"])
        chain_ok, _first_bad = audit.verify_chain(s)
    assert chain_ok
    actions = list(extract["action"])
    assert audit.DATA_LOAD in actions and audit.CALCULATION_RUN in actions
    load_event = extract[extract["action"] == audit.DATA_LOAD].iloc[0]
    assert json.loads(load_event["params"])["raw_path"] == str(raw_path)
    assert json.loads(load_event["params"])["file_sha256"] == vintage.file_sha256
    trace.append(f"audit trail: {len(extract)} events for this run, hash chain verified, DATA_LOAD "
                 f"names {raw_path.name}")

    # 4e. Individual source quotes: pick one item's rows in the raw layer and
    #     show they are the uploaded file's rows, byte for byte in value.
    item = raw_layer["item_id"].iloc[0]
    quotes = raw_layer[raw_layer["item_id"] == item].sort_values("period")
    uploaded = df[df["item_id"] == item].sort_values("period")
    pd.testing.assert_frame_equal(quotes.reset_index(drop=True), uploaded.reset_index(drop=True))
    trace.append(f"source quotes for item {item}: {len(quotes)} rows in the raw layer, identical to "
                 f"the uploaded file's rows")

    # 4f. And those quotes, through the registered configuration, give the
    #     registry's headline -- the whole chain closes on the number.
    recomputed = run_pipeline(raw_layer, registered_config)
    assert recomputed["indices"]["All items"].iloc[-1] == pytest.approx(registry_headline, abs=1e-9)
    # The transformation log replays the cleaned layer from the raw layer too.
    log_path = Path(settings.store_dir) / "cleaned" / f"{found.data_vintage}.log.json"
    log = store.TransformationLog.from_json(log_path.read_text(encoding="utf-8"))
    replayed = store.replay(raw_layer, log)
    pd.testing.assert_frame_equal(
        replayed.reset_index(drop=True), recomputed["imputed"].reset_index(drop=True))
    trace.append(f"raw layer + registered config -> headline {registry_headline:.6f} (matches the PDF)")

    assert len(trace) == 8
    print("\n".join(trace))


def test_the_bulletin_refuses_a_result_that_disagrees_with_the_registry(deployment):
    raw = pd.read_excel(FIXTURE, sheet_name="Price_Data")
    df = standardise(raw, infer_schema(raw))
    cfg, _ = auto_configure(df, "x")
    out = analyse(df, "x", cfg)
    res = out["result"]
    with db.session_scope() as s:
        run = register_run(s, df, cfg, "x", result=res)
        stamp = build_stamp(res, "x", run=run)
        tampered = dict(res)
        tampered["indices"] = res["indices"] * 1.01
        with pytest.raises(bulletin.BulletinError, match="registry holds"):
            bulletin.build_bulletin(tampered, out["narrative"], out["charts"], stamp, run=run)


def test_the_bulletin_carries_a_revision_statement_for_a_corrected_vintage(deployment):
    from pricelab.core.registry import correct_run

    raw = pd.read_excel(FIXTURE, sheet_name="Price_Data")
    df = standardise(raw, infer_schema(raw))
    cfg, _ = auto_configure(df, "x")
    out = analyse(df, "x", cfg)
    with db.session_scope() as s:
        first = register_run(s, df, cfg, "x", result=out["result"])
        approve_run(s, first.run_id)
        first_id = first.run_id
    cfg2 = cfg.model_copy(update={"label": "x corrected"}, deep=True)
    out2 = analyse(df, "x corrected", cfg2)
    with db.session_scope() as s:
        second = correct_run(s, first_id, df, cfg2, "x corrected", "late quotes for December received")
        stamp = build_stamp(out2["result"], "x corrected", run=second)
        pdf = bulletin.build_bulletin(out2["result"], out2["narrative"], out2["charts"], stamp, run=second)
        assert second.vintage == 2
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "Vintage 2" in text and first_id in text and "late quotes for December" in text
    assert readback.from_pdf(pdf).supersedes_run_id == first_id
