"""Shared fixtures.

`real_db` loads the shipped SQLite database (built by data/build_dataset.py). Tests that use it
are skipped with a clear message if the DB is missing, so a fresh clone can still run the pure
unit tests before the pipeline has been run.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backend import db
from backend.config import DB_PATH


@pytest.fixture(scope="session")
def real_db() -> dict[str, pd.DataFrame]:
    if not DB_PATH.exists():
        pytest.skip(f"{DB_PATH} not found — run `python data/build_dataset.py` first")
    return db.load_all()


@pytest.fixture
def tiny_clients() -> pd.DataFrame:
    return pd.DataFrame([
        {"client_id": "C1", "name": "Client A", "industry_category": "SaaS",
         "contract_start_date": "2024-01-01"},
        {"client_id": "C2", "name": "Client B", "industry_category": "Biotech",
         "contract_start_date": "2024-01-01"},
    ])


@pytest.fixture
def tiny_vendors() -> pd.DataFrame:
    return pd.DataFrame([
        {"vendor_id": "V1", "name": "Vendor 01 - Venue", "category": "venue",
         "payment_terms_days": 30},
        {"vendor_id": "V2", "name": "Vendor 02 - Catering", "category": "catering",
         "payment_terms_days": 15},
    ])
