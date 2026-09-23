"""Every export format: carries the full provenance stamp (read back out
of the file, not assumed), is free of formula injection including its
headers, shows suppressed cells as suppressed with the rule, and -- for
SDMX-ML -- validates against the standard's own schema set.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pricelab import analyse, build_deck, build_docx, build_markdown
from pricelab.core.config import IndexConfig, RunConfig
from pricelab.core.provenance import STAMP_KEY, ProvenanceStamp, build_stamp
from pricelab.core.security import _DANGEROUS_LEADERS
from pricelab.reporting import excel, exports, readback

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "tests" / "fixtures" / "sdmx_2_1"

# Every leader a spreadsheet may read as the start of a formula, each
# used in a category name, an item name and the run label.
INJECTIONS = ['=HYPERLINK("http://evil")', "+cmd|' /C calc'!A0", "-2+3", "@SUM(A1)", "\tcmd"]


def _panel(n_items: int = 4, n_periods: int = 8) -> pd.DataFrame:
    periods = pd.date_range("2020-01-01", periods=n_periods, freq="MS")
    rows = []
    rng = np.random.default_rng(0)
    for c, cat in enumerate(INJECTIONS[:3] + ["Plain"]):
        for i in range(n_items):
            base = 10.0 + 5 * c + i
            for t, p in enumerate(periods):
                if cat == "Plain" and i >= 2 and t % 2:      # thin cell -> suppression
                    continue
                rows.append({"period": p, "category": cat, "item_id": f"{c}-{i}",
                             "item_name": INJECTIONS[(c + i) % len(INJECTIONS)],
                             "price_reported": base * (1 + 0.01 * t) + rng.normal(0, 0.05)})
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def run():
    df = _panel()
    cfg = RunConfig(index=IndexConfig(min_matched_items=2), label=INJECTIONS[0])
    out = analyse(df, INJECTIONS[0], cfg)
    res = out["result"]
    stamp = build_stamp(res, INJECTIONS[0], data_vintage="vintage-abc", data_source="upload:x.csv",
                        data_received_at="2026-09-21T00:00:00+00:00")
    return {"df": df, "res": res, "nar": out["narrative"], "charts": out["charts"], "stamp": stamp}


def _artifacts(run) -> dict[str, bytes | str]:
    res, nar, charts, stamp, df = run["res"], run["nar"], run["charts"], run["stamp"], run["df"]
    return {
        "csv": exports.index_csv(res, stamp, "NOTICE"),
        "cleaned_csv": exports.stamped_csv(res["imputed"], stamp),
        "markdown": build_markdown(res, nar, INJECTIONS[0], stamp=stamp),
        "docx": build_docx(res, nar, dict(charts), INJECTIONS[0], stamp=stamp),
        "pptx": build_deck(res, nar, dict(charts), INJECTIONS[0], stamp=stamp),
        "xlsx": excel.build_evidence_pack(res, stamp, source=df, decisions=["=decision"]),
        "sdmx": exports.to_sdmx_ml(res, stamp),
    }


# ---------------------------------------------------------------------
# Provenance stamp: one function, every format, read back
# ---------------------------------------------------------------------
def test_the_stamp_names_everything_the_specification_requires(run):
    stamp = run["stamp"]
    d = stamp.to_dict()
    for key in ("run_id", "data_vintage", "code_version", "parameters", "suppression_rules",
                "non_standard_formula", "generated_at", "quality_adjustments", "headline"):
        assert key in d, key
    assert d["parameters"]["index"]["min_matched_items"] == 2
    assert d["parameters"]["quality_adjustment"] == {"entries": []}
    assert d["suppression_rules"]["min_count"] >= 1
    assert re.match(r"\d{4}-\d{2}-\d{2}T", d["generated_at"])
    assert ProvenanceStamp.from_json(stamp.to_json()) == stamp


def test_every_export_carries_the_same_stamp_read_back_from_the_file(run):
    stamp = run["stamp"]
    artifacts = _artifacts(run)
    readers = {"csv": readback.from_csv, "cleaned_csv": readback.from_csv,
               "markdown": readback.from_markdown, "docx": readback.from_docx,
               "pptx": readback.from_pptx, "xlsx": readback.from_xlsx, "sdmx": readback.from_sdmx}
    for fmt, reader in readers.items():
        found = reader(artifacts[fmt])
        assert found == stamp, f"{fmt} stamp differs from the one embedded"
        assert found.parameters == stamp.parameters
        assert found.code_version == stamp.code_version


def test_a_non_standard_run_is_marked_in_the_stamp_of_every_format(run):
    df = run["df"]
    cfg = RunConfig(index=IndexConfig(formula="custom", custom_formula="(carli * harmonic_mean) ** 0.5",
                                      min_matched_items=2))
    out = analyse(df, "custom", cfg)
    stamp = build_stamp(out["result"], "custom")
    assert stamp.non_standard_formula and "carli" in (stamp.non_standard_expression or "")
    csv_text = exports.index_csv(out["result"], stamp, "NON-STANDARD")
    assert readback.from_csv(csv_text).non_standard_formula is True
    assert readback.from_sdmx(exports.to_sdmx_ml(out["result"], stamp)).non_standard_formula is True
    assert b'id="NON_STANDARD_FORMULA" value="true"' in exports.to_sdmx_ml(out["result"], stamp)


def test_an_unregistered_export_says_so_rather_than_inventing_an_identifier(run):
    assert run["stamp"].run_id == "unregistered"
    assert not run["stamp"].registered
    assert ("Run identifier", "unregistered (not registered)") in run["stamp"].rows()


# ---------------------------------------------------------------------
# Formula injection: neutralised in every format, headers included
# ---------------------------------------------------------------------
def _is_dangerous(text: str) -> bool:
    """A string a spreadsheet would read as a formula. A negative number
    written as text ("-0.0128") is a number to every spreadsheet, which
    is why the sanitiser leaves numeric columns alone; it is exempt here
    for the same reason."""
    if not isinstance(text, str) or text[:1] not in _DANGEROUS_LEADERS:
        return False
    try:
        float(text)
        return False
    except ValueError:
        return True


def test_csv_exports_are_free_of_formula_injection_including_headers(run):
    for text in (_artifacts(run)["csv"], _artifacts(run)["cleaned_csv"]):
        rows = list(csv.reader(io.StringIO("\n".join(
            line for line in text.splitlines() if not line.startswith("#")))))
        for row in rows:                       # header row included
            for cell in row:
                assert not _is_dangerous(cell), cell
        assert any("HYPERLINK" in cell for row in rows for cell in row)   # the data is still there


def test_the_excel_pack_is_free_of_formula_injection_on_every_sheet(run):
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(_artifacts(run)["xlsx"]))
    assert wb.sheetnames == list(excel.SHEETS)
    # Named, not only counted: the contributions sheet is where an auditor
    # checks the decomposition's arithmetic, so its absence must fail here.
    assert "Contributions" in wb.sheetnames
    seen_payload = False
    for ws in wb.worksheets:
        assert not _is_dangerous(ws.title)
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    assert not _is_dangerous(cell.value), (ws.title, cell.coordinate, cell.value)
                    assert cell.data_type != "f", (ws.title, cell.coordinate)
                    seen_payload = seen_payload or "HYPERLINK" in cell.value
    assert seen_payload


def test_docx_pptx_and_markdown_tables_are_free_of_formula_injection(run):
    from docx import Document
    from pptx import Presentation

    arts = _artifacts(run)
    doc = Document(io.BytesIO(arts["docx"]))
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                assert not _is_dangerous(cell.text), cell.text
    prs = Presentation(io.BytesIO(arts["pptx"]))
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    for r in para.runs:
                        assert not _is_dangerous(r.text), r.text
    for line in arts["markdown"].splitlines():
        if line.startswith("|") and not re.fullmatch(r"[|:\- ]+", line):   # skip the |---:| row
            for cell in line.strip("|").split("|"):
                assert not _is_dangerous(cell.strip()), cell


def test_sdmx_attribute_values_are_free_of_formula_injection(run):
    from lxml import etree

    root = etree.fromstring(_artifacts(run)["sdmx"])
    for el in root.iter():
        for value in el.attrib.values():
            assert not _is_dangerous(value), value


# ---------------------------------------------------------------------
# Disclosure control in the publication table
# ---------------------------------------------------------------------
def test_suppressed_cells_are_shown_as_suppressed_with_the_rule_never_blank(run):
    table = exports.publication_table(run["res"], min_count=3)
    primary = table[table["suppression_rule"].str.startswith("primary")]
    assert len(primary) > 0, "the fixture's thin Plain cell should be primary-suppressed"
    assert (primary["published"] == "suppressed").all()
    assert (primary["matched_items"] < 3).all()
    secondary = table[table["suppression_rule"].str.startswith("secondary")]
    for period in primary["period"].unique():
        # a period with a primary suppression has at least two suppressed
        # categories, so the aggregate cannot give the first away
        assert (table[(table["period"] == period) & (table["series"] != "All items")]["suppressed"]
                .sum()) >= 2 or len(table[(table["period"] == period) & (table["series"] != "All items")]) == 1
    assert (table.loc[table["suppressed"], "suppression_rule"] != "").all()
    assert (table.loc[~table["suppressed"], "suppression_rule"] == "").all()
    assert len(secondary) > 0
    wide = exports.wide_publication(table)
    assert (wide == "suppressed").any().any()
    assert not (wide == "").any().any()


def test_the_csv_and_sdmx_carry_suppression_rather_than_blanks(run):
    csv_text = exports.index_csv(run["res"], run["stamp"])
    assert "suppressed" in csv_text and "primary: built from fewer than" in csv_text
    xml = exports.to_sdmx_ml(run["res"], run["stamp"])
    assert b'id="OBS_STATUS" value="C"' in xml
    assert b"OBS_COMMENT" in xml


# ---------------------------------------------------------------------
# SDMX-ML validates against the real schema, not merely resembles it
# ---------------------------------------------------------------------
def test_sdmx_ml_validates_against_the_sdmx_2_1_schema_set(run):
    assert (SCHEMA_DIR / "SDMXMessage.xsd").exists(), "vendored SDMX 2.1 schemas are missing"
    xml = exports.to_sdmx_ml(run["res"], run["stamp"])
    ok, errors = exports.validate_sdmx_ml(xml, str(SCHEMA_DIR))
    assert ok, errors[:5]


def test_the_schema_validation_actually_rejects_a_broken_message(run):
    """The validator has teeth: an element the schema does not allow
    fails, so a pass above means conformance, not a validator that says
    yes to anything."""
    xml = exports.to_sdmx_ml(run["res"], run["stamp"])
    broken = xml.replace(b"<generic:ObsValue", b"<generic:NotInTheStandard", 1)
    ok, errors = exports.validate_sdmx_ml(broken, str(SCHEMA_DIR))
    assert not ok and any("NotInTheStandard" in e for e in errors)


def test_sdmx_message_has_the_series_and_observations_of_the_publication_table(run):
    from lxml import etree

    table = exports.publication_table(run["res"])
    root = etree.fromstring(exports.to_sdmx_ml(run["res"], run["stamp"]))
    ns = exports.NS
    series = root.findall(f".//{{{ns['generic']}}}Series")
    assert len(series) == table["series"].nunique()
    obs = root.findall(f".//{{{ns['generic']}}}Obs")
    assert len(obs) == len(table)
    values = root.findall(f".//{{{ns['generic']}}}ObsValue")
    assert len(values) == int((~table["suppressed"] & table["index"].notna()).sum())
    assert any(el.get("value") == STAMP_KEY or (el.text == STAMP_KEY)
               for el in root.iter(f"{{{ns['common']}}}AnnotationTitle"))
