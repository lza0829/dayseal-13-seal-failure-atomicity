from __future__ import annotations

import pytest

from app.config import DB_PATH  # noqa: E402
from app.db import connect  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402
from app.repositories import closes as close_repo  # noqa: E402


@pytest.fixture()
def legacy_db():
    """A pre-upgrade database: sealed batch, no snapshot, no day lock."""
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = connect()
    try:
        conn.executescript(
            '''
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE tills (id INTEGER PRIMARY KEY, till_code TEXT NOT NULL UNIQUE, title TEXT NOT NULL);
            CREATE TABLE cash_lines (
              id INTEGER PRIMARY KEY, line_no TEXT NOT NULL UNIQUE, till_code TEXT NOT NULL,
              business_day TEXT NOT NULL, occurred_at TEXT NOT NULL, amount REAL NOT NULL,
              pay_method TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL);
            CREATE TABLE close_batches (
              id INTEGER PRIMARY KEY, batch_no TEXT NOT NULL UNIQUE, business_day TEXT NOT NULL,
              system_total REAL NOT NULL, declared_cash REAL NOT NULL, variance REAL NOT NULL,
              line_count INTEGER NOT NULL, status TEXT NOT NULL, sealed_at TEXT NOT NULL, note TEXT);
            CREATE TABLE variance_rows (
              id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL, business_day TEXT NOT NULL,
              variance REAL NOT NULL, disposition TEXT NOT NULL, created_at TEXT NOT NULL);
            '''
        )
        conn.execute("INSERT INTO settings VALUES ('business_day','2026-11-16')")
        conn.execute("INSERT INTO tills(till_code,title) VALUES ('TILL-A','一号收银')")
        conn.execute(
            '''INSERT INTO cash_lines(line_no,till_code,business_day,occurred_at,amount,pay_method,created_at)
               VALUES ('CL-0001','TILL-A','2026-11-16','2026-11-16T09:00:00Z',10,'现金','2026-11-16T09:00:00Z')'''
        )
        conn.execute(
            '''INSERT INTO close_batches(batch_no,business_day,system_total,declared_cash,variance,
                                       line_count,status,sealed_at)
               VALUES ('CB-0001','2026-11-15',10,10,0,1,'sealed','2026-11-15T20:00:00Z')'''
        )
        conn.execute(
            '''INSERT INTO variance_rows(batch_id,business_day,variance,disposition,created_at)
               VALUES (1,'2026-11-15',0,'balanced','2026-11-15T20:00:00Z')'''
        )
        conn.commit()
    finally:
        conn.close()
    yield
    if DB_PATH.exists():
        DB_PATH.unlink()


def test_legacy_sealed_day_gets_backfilled_lock(legacy_db):
    seed_if_empty()
    conn = connect()
    try:
        # Historical sealed day stays closed after the upgrade...
        assert close_repo.is_sealed(conn, "2026-11-15") is True
        lock = close_repo.get_day_lock(conn, "2026-11-15")
        assert lock is not None and lock["batch_id"] == 1
        # ...while the current open day is untouched.
        assert close_repo.is_sealed(conn, "2026-11-16") is False
    finally:
        conn.close()


def test_legacy_sealed_day_rejects_line_writes(legacy_db):
    seed_if_empty()
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        r = client.post(
            "/api/lines",
            json={"business_day": "2026-11-15", "till_code": "TILL-A", "amount": 1},
        )
        assert r.status_code == 409
        # Repeated seeding must not duplicate locks.
        seed_if_empty()
        integrity = client.get("/api/close/integrity").json()
        assert integrity["locks_without_sealed_batch"] == 0
        assert integrity["day_lock_count"] == 1
