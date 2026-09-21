"""Tests for the upload pipeline: format dispatch, encoding/delimiter
detection, header inference, and the memory-footprint check -- both the
upfront estimate and the mid-read abort for CSV.
"""

import io
import json
from pathlib import Path

import pandas as pd
import pytest

from pricelab.data import loaders


def _csv_bytes(text: str, encoding: str = "utf-8") -> bytes:
    return text.encode(encoding)


# ---------------------------------------------------------------------
# Encoding / delimiter / header detection
# ---------------------------------------------------------------------
def test_detect_encoding_prefers_utf8_when_it_decodes():
    assert loaders.detect_encoding(b"plain ascii") in ("utf-8-sig", "utf-8")


def test_detect_encoding_falls_back_for_latin1_only_bytes():
    sample = "café".encode("cp1252")
    assert loaders.detect_encoding(sample) in ("cp1252", "latin-1")


def test_detect_delimiter_finds_semicolon():
    text = "a;b;c\n1;2;3\n4;5;6\n"
    assert loaders.detect_delimiter(text) == ";"


def test_detect_delimiter_finds_comma():
    text = "a,b,c\n1,2,3\n4,5,6\n"
    assert loaders.detect_delimiter(text) == ","


def test_infer_header_row_finds_row_below_a_title_line():
    rows = [["My collection export"], ["Date", "Category", "Price"], ["2020-01-01", "Bread", "1.0"]]
    assert loaders.infer_header_row(rows) == 1


def test_infer_header_row_defaults_to_zero_when_header_is_already_first():
    rows = [["Date", "Category", "Price"], ["2020-01-01", "Bread", "1.0"]]
    assert loaders.infer_header_row(rows) == 0


def test_sniff_csv_reports_encoding_delimiter_and_header_row():
    text = "Export title only\nDate;Category;Reported_Price\n2020-01-01;Bread;1.0\n"
    encoding, delimiter, header_row = loaders.sniff_csv(text.encode("utf-8"))
    assert delimiter == ";"
    assert header_row == 1
    assert "utf-8" in encoding


# ---------------------------------------------------------------------
# Format dispatch
# ---------------------------------------------------------------------
def test_read_upload_reads_csv():
    text = "Date,Category,Reported_Price\n2020-01-01,Bread,1.0\n2020-02-01,Bread,1.1\n"
    result = loaders.read_upload(text.encode(), "prices.csv")
    assert list(result.df.columns) == ["Date", "Category", "Reported_Price"]
    assert len(result.df) == 2
    assert result.delimiter == ","


def test_read_upload_reads_csv_with_a_title_row_above_the_header():
    text = "Weekly export\nDate,Category,Reported_Price\n2020-01-01,Bread,1.0\n"
    result = loaders.read_upload(text.encode(), "prices.csv")
    assert list(result.df.columns) == ["Date", "Category", "Reported_Price"]
    assert result.header_row == 1


def test_read_upload_reads_xlsx(tmp_path):
    df = pd.DataFrame({"Date": ["2020-01-01"], "Category": ["Bread"], "Reported_Price": [1.0]})
    path = tmp_path / "prices.xlsx"
    df.to_excel(path, index=False)
    result = loaders.read_upload(path.read_bytes(), "prices.xlsx")
    assert list(result.df.columns) == ["Date", "Category", "Reported_Price"]
    assert result.encoding is None


def test_list_sheets_returns_all_sheet_names_for_a_multi_sheet_workbook(tmp_path):
    path = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="First", index=False)
        pd.DataFrame({"b": [2]}).to_excel(writer, sheet_name="Second", index=False)
    sheets = loaders.list_sheets(path.read_bytes(), "multi.xlsx")
    assert sheets == ["First", "Second"]


def test_list_sheets_is_none_for_csv():
    assert loaders.list_sheets(b"a,b\n1,2\n", "prices.csv") is None


def test_read_upload_reads_a_specific_sheet(tmp_path):
    path = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="First", index=False)
        pd.DataFrame({"b": [2]}).to_excel(writer, sheet_name="Second", index=False)
    result = loaders.read_upload(path.read_bytes(), "multi.xlsx", sheet_name="Second")
    assert list(result.df.columns) == ["b"]


def test_read_upload_reads_parquet(tmp_path):
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    path = tmp_path / "prices.parquet"
    df.to_parquet(path, index=False)
    result = loaders.read_upload(path.read_bytes(), "prices.parquet")
    pd.testing.assert_frame_equal(result.df, df)


def test_read_upload_reads_json():
    records = [{"Date": "2020-01-01", "Category": "Bread", "Reported_Price": 1.0}]
    result = loaders.read_upload(json.dumps(records).encode(), "prices.json")
    assert list(result.df.columns) == ["Date", "Category", "Reported_Price"]


def test_read_upload_rejects_an_unrecognised_extension():
    with pytest.raises(loaders.LoaderError, match="not a recognised format"):
        loaders.read_upload(b"whatever", "prices.txt")


# ---------------------------------------------------------------------
# Memory footprint
# ---------------------------------------------------------------------
def test_estimate_memory_mb_is_positive_for_a_real_csv():
    text = "Date,Category,Reported_Price\n" + "2020-01-01,Bread,1.0\n" * 100
    estimate = loaders.estimate_memory_mb(text.encode(), "prices.csv")
    assert estimate > 0


def test_read_upload_refuses_a_csv_whose_estimated_footprint_exceeds_the_cap():
    text = "Date,Category,Reported_Price\n" + "2020-01-01,Bread,1.0\n" * 1000
    with pytest.raises(loaders.MemoryFootprintError):
        loaders.read_upload(text.encode(), "prices.csv", max_mb=0.0001)


def test_read_upload_accepts_a_csv_within_the_cap():
    text = "Date,Category,Reported_Price\n2020-01-01,Bread,1.0\n"
    result = loaders.read_upload(text.encode(), "prices.csv", max_mb=50.0)
    assert len(result.df) == 1


def test_chunked_read_aborts_partway_when_running_memory_exceeds_the_cap():
    """A large CSV whose sample-based estimate might pass, but whose
    actual chunk-by-chunk measured memory is checked as it is read, still
    gets refused -- proving the abort is a real check on measured memory,
    not only a projection from the first sample."""
    rows = "\n".join(f"{i},Bread,{1.0 + i * 0.001}" for i in range(loaders.CHUNK_ROWS * 3))
    text = "Date,Category,Reported_Price\n" + rows + "\n"
    with pytest.raises(loaders.MemoryFootprintError):
        loaders._read_csv_chunked(text.encode(), "utf-8", ",", 0, max_mb=0.001)


def test_chunked_read_produces_the_same_data_as_a_direct_read():
    rows = "\n".join(f"{i},Bread,{1.0 + i * 0.001}" for i in range(loaders.CHUNK_ROWS + 10))
    text = "Date,Category,Reported_Price\n" + rows + "\n"
    chunked = loaders._read_csv_chunked(text.encode(), "utf-8", ",", 0, max_mb=None)
    direct = pd.read_csv(io.StringIO(text))
    pd.testing.assert_frame_equal(chunked, direct)


def test_the_real_fixture_loads_identically_through_read_upload():
    fixture = Path(__file__).resolve().parents[1] / "supermarket_price_collection.xlsx"
    if not fixture.exists():
        pytest.skip("fixture workbook not present")
    file_bytes = fixture.read_bytes()
    result = loaders.read_upload(file_bytes, "supermarket_price_collection.xlsx",
                                  sheet_name="Price_Data")
    direct = pd.read_excel(fixture, sheet_name="Price_Data")
    pd.testing.assert_frame_equal(result.df, direct)
