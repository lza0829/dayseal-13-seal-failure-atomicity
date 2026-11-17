from __future__ import annotations
import sqlite3
from typing import Any


def is_sealed(conn: sqlite3.Connection, business_day: str) -> bool:
    """A day is sealed only when the day lock and a sealed batch agree.

    The lock and the batch header are written in the same transaction, so a
    write rejection (409) can always be backed by a real sealed batch. A lock
    pointing at a non-sealed batch, or a sealed batch with no lock, does not
    lock the day.
    """
    return get_sealed_batch(conn, business_day) is not None


def next_batch_no(conn: sqlite3.Connection) -> str:
    n = conn.execute("SELECT COALESCE(MAX(id),0) c FROM close_batches").fetchone()["c"] + 1
    return f"CB-{n:04d}"


def create_batch(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        '''INSERT INTO close_batches(
             batch_no, business_day, system_total, declared_cash, variance,
             line_count, status, sealed_at, note
           ) VALUES (?,?,?,?,?,?,?,?,?)''',
        (
            fields["batch_no"],
            fields["business_day"],
            fields["system_total"],
            fields["declared_cash"],
            fields["variance"],
            fields["line_count"],
            fields["status"],
            fields["sealed_at"],
            fields.get("note"),
        ),
    )
    return int(cur.lastrowid)


def add_snapshot(conn: sqlite3.Connection, **fields: Any) -> None:
    conn.execute(
        '''INSERT INTO close_snapshots(
             batch_id, business_day, line_count, system_total, lines_digest, created_at
           ) VALUES (?,?,?,?,?,?)''',
        (
            fields["batch_id"],
            fields["business_day"],
            fields["line_count"],
            fields["system_total"],
            fields["lines_digest"],
            fields["created_at"],
        ),
    )


def add_variance(conn: sqlite3.Connection, **fields: Any) -> None:
    conn.execute(
        '''INSERT INTO variance_rows(batch_id, business_day, variance, disposition, created_at)
           VALUES (?,?,?,?,?)''',
        (
            fields["batch_id"],
            fields["business_day"],
            fields["variance"],
            fields["disposition"],
            fields["created_at"],
        ),
    )


def lock_day(conn: sqlite3.Connection, business_day: str, batch_id: int, locked_at: str) -> None:
    conn.execute(
        "INSERT INTO close_day_locks(business_day, batch_id, locked_at) VALUES (?,?,?)",
        (business_day, batch_id, locked_at),
    )


def get_day_lock(conn: sqlite3.Connection, business_day: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM close_day_locks WHERE business_day=?", (business_day,)
    ).fetchone()
    return dict(row) if row else None


def get_sealed_batch(conn: sqlite3.Connection, business_day: str) -> dict[str, Any] | None:
    """The sealed batch as seen by the write lock, or None if the day is open."""
    row = conn.execute(
        '''SELECT b.*
             FROM close_day_locks l
             JOIN close_batches b ON b.id = l.batch_id
            WHERE l.business_day=? AND b.status='sealed' AND b.business_day=l.business_day
            LIMIT 1''',
        (business_day,),
    ).fetchone()
    return dict(row) if row else None


def list_batches(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM close_batches ORDER BY id DESC").fetchall()]


def list_variances(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM variance_rows ORDER BY id DESC").fetchall()]


def sealed_batch_count(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute("SELECT COUNT(*) c FROM close_batches WHERE status='sealed'").fetchone()["c"]
    )


def integrity(conn: sqlite3.Connection) -> dict[str, Any]:
    """Report residue that would indicate a half-written seal."""

    def count(sql: str) -> int:
        return int(conn.execute(sql).fetchone()["c"])

    return {
        "sealed_batch_count": sealed_batch_count(conn),
        "non_sealed_batch_count": count(
            "SELECT COUNT(*) c FROM close_batches WHERE status<>'sealed'"
        ),
        "snapshot_count": count("SELECT COUNT(*) c FROM close_snapshots"),
        "variance_count": count("SELECT COUNT(*) c FROM variance_rows"),
        "day_lock_count": count("SELECT COUNT(*) c FROM close_day_locks"),
        "sealed_batches_without_lock": count(
            '''SELECT COUNT(*) c FROM close_batches b
                WHERE b.status='sealed'
                  AND NOT EXISTS (SELECT 1 FROM close_day_locks l WHERE l.batch_id=b.id)'''
        ),
        "locks_without_sealed_batch": count(
            '''SELECT COUNT(*) c FROM close_day_locks l
                WHERE NOT EXISTS (
                  SELECT 1 FROM close_batches b
                   WHERE b.id=l.batch_id AND b.status='sealed'
                     AND b.business_day=l.business_day)'''
        ),
        "sealed_batches_without_snapshot": count(
            '''SELECT COUNT(*) c FROM close_batches b
                WHERE b.status='sealed'
                  AND NOT EXISTS (SELECT 1 FROM close_snapshots s WHERE s.batch_id=b.id)'''
        ),
        "sealed_batches_without_variance": count(
            '''SELECT COUNT(*) c FROM close_batches b
                WHERE b.status='sealed'
                  AND NOT EXISTS (SELECT 1 FROM variance_rows v WHERE v.batch_id=b.id)'''
        ),
        "orphan_snapshots": count(
            '''SELECT COUNT(*) c FROM close_snapshots s
                WHERE NOT EXISTS (SELECT 1 FROM close_batches b WHERE b.id=s.batch_id)'''
        ),
        "orphan_variances": count(
            '''SELECT COUNT(*) c FROM variance_rows v
                WHERE NOT EXISTS (SELECT 1 FROM close_batches b WHERE b.id=v.batch_id)'''
        ),
    }
