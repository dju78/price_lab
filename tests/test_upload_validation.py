"""Tests for pages/ingest.py's upload validation: rejected before a single
byte of the file is read, not merely relied on the file_uploader widget's
own client-side type filter."""

from pages.ingest import ALLOWED_UPLOAD_EXTENSIONS, validate_upload


def test_oversized_file_is_rejected_before_getvalue_would_be_called():
    error = validate_upload("collection.xlsx", size_bytes=60 * 1024 * 1024, max_mb=50.0)
    assert error is not None
    assert "60.0 MB" in error
    assert "50 MB" in error


def test_file_within_the_size_cap_is_accepted():
    assert validate_upload("collection.xlsx", size_bytes=10 * 1024 * 1024, max_mb=50.0) is None


def test_file_exactly_at_the_cap_is_accepted():
    ten_mb = 10 * 1024 * 1024
    assert validate_upload("collection.xlsx", size_bytes=ten_mb, max_mb=10.0) is None


def test_invalid_file_type_is_rejected():
    error = validate_upload("collection.exe", size_bytes=1024, max_mb=50.0)
    assert error is not None
    assert "collection.exe" in error
    assert "not one of the accepted file types" in error


def test_invalid_type_is_rejected_even_when_well_under_the_size_cap():
    """Type is checked regardless of size: a tiny malicious file is not
    waved through because it happens to be small."""
    error = validate_upload("payload.exe", size_bytes=1, max_mb=50.0)
    assert error is not None


def test_every_allowed_extension_is_accepted():
    for ext in ALLOWED_UPLOAD_EXTENSIONS:
        assert validate_upload(f"collection{ext}", size_bytes=1024, max_mb=50.0) is None


def test_extension_check_is_case_insensitive():
    assert validate_upload("COLLECTION.XLSX", size_bytes=1024, max_mb=50.0) is None
