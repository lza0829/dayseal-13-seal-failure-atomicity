from __future__ import annotations
import hashlib
import math
import os
from datetime import datetime, timezone
from typing import Any
from fastapi import HTTPException
from app.db import connect
from app.repositories import closes as close_repo
from app.repositories import lines as line_repo
from app.repositories import settings as settings_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_declared_cash(raw: Any) -> float:
    if raw is None or isinstance(raw, bool):
        raise HTTPException(400, "实盘现金缺失或非法")
    try:
        declared = float(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, f"实盘现金非法：{raw!r}")
    if not math.isfinite(declared):
        raise HTTPException(400, f"实盘现金非法：{raw!r}")
    if declared < 0:
        raise HTTPException(400, f"实盘现金不得为负：{declared}")
    return round(declared, 2)


def _lines_digest(lines: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for row in lines:
        h.update(
            "|".join(
                str(row.get(k, ""))
                for k in ("id", "line_no", "till_code", "occurred_at", "amount", "pay_method")
            ).encode("utf-8")
        )
        h.update(b"\n")
    return h.hexdigest()


# Deterministic fault injection for tests only: each value names a step that
# must fail after its write has happened inside the still-open transaction.
def _fault(step: str) -> None:
    wanted = os.environ.get("DAYSEAL_FAULT_INJECT", "")
    if wanted and wanted == step:
        raise RuntimeError(f"注入故障：{step}")


def preview(business_day: str | None = None) -> dict[str, Any]:
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day") or ""
        total, count = line_repo.day_total(conn, day)
        sealed_batch = close_repo.get_sealed_batch(conn, day)
        return {
            "business_day": day,
            "system_total": total,
            "line_count": count,
            "sealed": sealed_batch is not None,
            "sealed_batch_id": sealed_batch["id"] if sealed_batch else None,
            "lines": line_repo.list_lines(conn, day),
        }
    finally:
        conn.close()


def seal(body: dict[str, Any]) -> dict[str, Any]:
    # BEGIN IMMEDIATE takes the SQLite write lock up front: seals for the same
    # database are globally serialized, so the day check below cannot race.
    conn = connect(immediate=True)
    try:
        day = body.get("business_day") or settings_repo.get(conn, "business_day")
        if not day:
            raise HTTPException(400, "未指定营业日")
        if close_repo.is_sealed(conn, day):
            raise HTTPException(409, f"营业日 {day} 已封账")
        declared = _parse_declared_cash(body.get("declared_cash"))

        lines = line_repo.list_lines(conn, day)
        total = round(sum(float(r["amount"]) for r in lines), 2)
        count = len(lines)
        variance = round(declared - total, 2)
        sealed_at = _now()
        disposition = "pending" if abs(variance) > 1e-9 else "balanced"

        # Step 1: batch header.
        batch_id = close_repo.create_batch(
            conn,
            batch_no=close_repo.next_batch_no(conn),
            business_day=day,
            system_total=total,
            declared_cash=declared,
            variance=variance,
            line_count=count,
            status="sealed",
            sealed_at=sealed_at,
            note=body.get("note"),
        )
        _fault("after_batch")

        # Step 2: immutable detail/summary snapshot of the sealed lines.
        close_repo.add_snapshot(
            conn,
            batch_id=batch_id,
            business_day=day,
            line_count=count,
            system_total=total,
            lines_digest=_lines_digest(lines),
            created_at=sealed_at,
        )
        _fault("after_snapshot")

        # Step 3: variance row.
        close_repo.add_variance(
            conn,
            batch_id=batch_id,
            business_day=day,
            variance=variance,
            disposition=disposition,
            created_at=sealed_at,
        )
        _fault("after_variance")

        # Step 4: day lock — the only thing write paths consult.
        close_repo.lock_day(conn, day, batch_id, sealed_at)
        _fault("after_lock")

        _fault("before_commit")
        conn.commit()
        return {
            "batch_id": batch_id,
            "business_day": day,
            "system_total": total,
            "declared_cash": declared,
            "variance": variance,
            "line_count": count,
            "status": "sealed",
            "disposition": disposition,
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, f"封账失败，已整笔回滚：{exc}") from exc
    finally:
        conn.close()


def integrity() -> dict[str, Any]:
    conn = connect()
    try:
        return close_repo.integrity(conn)
    finally:
        conn.close()


def list_batches() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return close_repo.list_batches(conn)
    finally:
        conn.close()


def list_variances() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return close_repo.list_variances(conn)
    finally:
        conn.close()
