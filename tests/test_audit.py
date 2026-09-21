"""Tests for the hash-chained audit log."""


import pytest
from dbtarget import database_url

from pricelab.core import audit, db
from pricelab.core.config import get_settings


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "audit_test.db"))
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield
    db.reset_db_state()
    get_settings.cache_clear()


def test_chain_is_intact_after_normal_writes(fresh_db):
    with db.session_scope() as s:
        audit.record_event(s, "alice", audit.LOGIN, "session")
        audit.record_event(s, "alice", audit.DATA_LOAD, "upload.xlsx", {"rows": 100})
        audit.record_event(s, "alice", audit.CALCULATION_RUN, "run-1", {"formula": "jevons"})

    with db.session_scope() as s:
        ok, break_id = audit.verify_chain(s)
    assert ok is True
    assert break_id is None


def test_tampering_with_a_record_is_detected(fresh_db):
    with db.session_scope() as s:
        audit.record_event(s, "alice", audit.LOGIN, "session")
        audit.record_event(s, "alice", audit.DATA_LOAD, "upload.xlsx")
        audit.record_event(s, "alice", audit.EXPORT, "report.docx")

    with db.session_scope() as s:
        row = s.query(audit.AuditEventORM).filter_by(action=audit.DATA_LOAD).one()
        row.target = "a different file entirely"
        s.add(row)

    with db.session_scope() as s:
        ok, break_id = audit.verify_chain(s)
    assert ok is False
    assert break_id == 2  # the tampered record, first in the chain to fail


def test_deleting_a_record_is_detected(fresh_db):
    with db.session_scope() as s:
        audit.record_event(s, "alice", audit.LOGIN, "session")
        audit.record_event(s, "alice", audit.DATA_LOAD, "upload.xlsx")
        audit.record_event(s, "alice", audit.EXPORT, "report.docx")

    with db.session_scope() as s:
        row = s.query(audit.AuditEventORM).filter_by(action=audit.DATA_LOAD).one()
        s.delete(row)

    with db.session_scope() as s:
        ok, break_id = audit.verify_chain(s)
    assert ok is False
    assert break_id is not None


def test_empty_log_is_trivially_valid(fresh_db):
    with db.session_scope() as s:
        ok, break_id = audit.verify_chain(s)
    assert (ok, break_id) == (True, None)
