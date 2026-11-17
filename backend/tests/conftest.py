from __future__ import annotations
import os
import tempfile

import pytest

os.environ["DAYSEAL_DATA"] = tempfile.mkdtemp(prefix="dayseal-test-")
os.environ.pop("DAYSEAL_FAULT_INJECT", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.config import DB_PATH  # noqa: E402
from app.db import connect  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402


@pytest.fixture()
def client():
    if DB_PATH.exists():
        DB_PATH.unlink()
    seed_if_empty()
    os.environ.pop("DAYSEAL_FAULT_INJECT", None)
    with TestClient(app) as c:
        yield c
    os.environ.pop("DAYSEAL_FAULT_INJECT", None)


@pytest.fixture()
def db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def table_counts(conn) -> dict[str, int]:
    tables = [
        "close_batches",
        "close_snapshots",
        "variance_rows",
        "close_day_locks",
        "cash_lines",
    ]
    return {
        t: int(conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]) for t in tables
    }
