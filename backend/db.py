"""Thin SQLite access layer. Returns pandas DataFrames; no business logic here.

Swapping to Postgres would replace `sqlite3.connect` with a SQLAlchemy engine and leave every
call site unchanged (TRD §2).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from backend.config import DB_PATH

_TABLES = (
    "clients", "vendors", "retreats", "invoices_ar", "bills_ap", "bank_transactions",
    "reconciliation_matches", "audit_findings", "sec_benchmarks", "provenance_sources",
    "retreat_budget_provenance",
)

_DATE_COLS = {
    "clients": ["contract_start_date"],
    "retreats": ["start_date", "end_date"],
    "invoices_ar": ["invoice_date", "due_date", "payment_date"],
    "bills_ap": ["bill_date", "due_date", "payment_date"],
    "bank_transactions": ["transaction_date"],
    "audit_findings": ["date_found"],
}


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path or DB_PATH))
    con.execute("PRAGMA foreign_keys = ON")
    return con


def load_table(name: str, db_path: Path | str | None = None) -> pd.DataFrame:
    if name not in _TABLES:
        raise KeyError(f"unknown table {name!r}")
    with connect(db_path) as con:
        df = pd.read_sql_query(f"SELECT * FROM {name}", con)
    for col in _DATE_COLS.get(name, []):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def load_all(db_path: Path | str | None = None) -> dict[str, pd.DataFrame]:
    return {t: load_table(t, db_path) for t in _TABLES}
