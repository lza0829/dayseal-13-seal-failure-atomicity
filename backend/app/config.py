from __future__ import annotations
import os
from pathlib import Path

PROJECT_NAME = "Dayseal"
API_PORT = 10700

_default_data_dir = Path(__file__).resolve().parents[2] / "data"
DATA_DIR = Path(os.environ.get("DAYSEAL_DATA") or _default_data_dir)
DB_PATH = DATA_DIR / "dayseal.db"
