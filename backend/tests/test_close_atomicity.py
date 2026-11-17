from __future__ import annotations
import os
import threading

import pytest

from conftest import table_counts

DAY = "2026-11-16"


def _seal(client, declared=489.4):
    return client.post(
        "/api/close/seal",
        json={"business_day": DAY, "declared_cash": declared},
    )


def _dashboard(client):
    return client.get("/api/dashboard").json()


def _assert_no_residue(conn, before):
    assert table_counts(conn) == before


@pytest.mark.parametrize(
    "step",
    ["after_batch", "after_snapshot", "after_variance", "after_lock", "before_commit"],
)
def test_injected_failure_rolls_back_everything(client, db, step):
    before = table_counts(db)
    before_dash = _dashboard(client)

    os.environ["DAYSEAL_FAULT_INJECT"] = step
    r = _seal(client)
    os.environ.pop("DAYSEAL_FAULT_INJECT", None)

    assert r.status_code == 500
    body = r.json()
    assert step in body["detail"]
    _assert_no_residue(db, before)

    dash = _dashboard(client)
    assert dash["sealed_today"] is False
    assert dash["close_batch_count"] == before_dash["close_batch_count"]
    assert client.get(f"/api/close/preview?business_day={DAY}").json()["sealed"] is False

    integrity = client.get("/api/close/integrity").json()
    assert integrity["sealed_batch_count"] == 0
    assert integrity["day_lock_count"] == 0
    assert integrity["snapshot_count"] == 0

    # The write path must stay open: no phantom 409 for an unsealed day.
    r = client.post("/api/lines", json={"business_day": DAY, "till_code": "TILL-A", "amount": 5})
    assert r.status_code == 200
    assert r.json()["business_day"] == DAY

    # A later seal succeeds and writes all four kinds of rows together.
    r = _seal(client, declared=494.4)
    assert r.status_code == 200, r.text
    batch_id = r.json()["batch_id"]
    after = table_counts(db)
    assert after["close_batches"] == before["close_batches"] + 1
    assert after["close_snapshots"] == before["close_snapshots"] + 1
    assert after["variance_rows"] == before["variance_rows"] + 1
    assert after["close_day_locks"] == before["close_day_locks"] + 1
    snapshot = db.execute("SELECT * FROM close_snapshots WHERE batch_id=?", (batch_id,)).fetchone()
    assert snapshot is not None and snapshot["line_count"] == 6
    lock = db.execute("SELECT * FROM close_day_locks WHERE business_day=?", (DAY,)).fetchone()
    assert lock is not None and lock["batch_id"] == batch_id
    variance = db.execute("SELECT * FROM variance_rows WHERE batch_id=?", (batch_id,)).fetchone()
    assert variance is not None
    assert client.get("/api/close/integrity").json()["locks_without_sealed_batch"] == 0

    # Once sealed, the write path is closed and the 409 points at a real batch.
    r = client.post("/api/lines", json={"business_day": DAY, "till_code": "TILL-A", "amount": 1})
    assert r.status_code == 409
    assert db.execute(
        "SELECT 1 FROM close_batches WHERE id=? AND status='sealed'", (batch_id,)
    ).fetchone() is not None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"declared_cash": None},
        {"declared_cash": "abc"},
        {"declared_cash": "nan"},
        {"declared_cash": "inf"},
        {"declared_cash": -1},
        {"declared_cash": True},
    ],
)
def test_invalid_declared_cash_leaves_no_residue(client, db, payload):
    before = table_counts(db)
    r = client.post("/api/close/seal", json={"business_day": DAY, **payload})
    assert r.status_code == 400
    _assert_no_residue(db, before)
    assert _dashboard(client)["sealed_today"] is False
    assert _dashboard(client)["close_batch_count"] == 0
    assert client.post(
        "/api/lines", json={"business_day": DAY, "till_code": "TILL-A", "amount": 10}
    ).status_code == 200


def test_concurrent_seal_only_one_wins(client, db):
    before = table_counts(db)
    results: list[int] = []
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        results.append(_seal(client).status_code)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409]
    after = table_counts(db)
    assert after["close_batches"] == before["close_batches"] + 1
    assert after["close_snapshots"] == before["close_snapshots"] + 1
    assert after["variance_rows"] == before["variance_rows"] + 1
    assert after["close_day_locks"] == before["close_day_locks"] + 1
    integrity = client.get("/api/close/integrity").json()
    assert integrity["sealed_batch_count"] == 1
    assert integrity["sealed_batches_without_lock"] == 0
    assert integrity["sealed_batches_without_snapshot"] == 0
    assert integrity["sealed_batches_without_variance"] == 0


def test_failed_seal_does_not_hide_behind_409(client, db):
    os.environ["DAYSEAL_FAULT_INJECT"] = "after_lock"
    assert _seal(client).status_code == 500
    os.environ.pop("DAYSEAL_FAULT_INJECT", None)
    # Day must be reported unsealed and lines must stay writable.
    assert client.get(f"/api/close/preview?business_day={DAY}").json()["sealed"] is False
    assert client.post(
        "/api/lines", json={"business_day": DAY, "till_code": "TILL-B", "amount": 2}
    ).status_code == 200
    assert db.execute(
        "SELECT COUNT(*) c FROM close_batches WHERE status='sealed'"
    ).fetchone()["c"] == 0
