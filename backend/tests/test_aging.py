"""Aging bucket + DSO/DPO tests.

Model-logic tests (hand-built frames) are separate from the integration check that the shipped
DB reproduces the build's DSO 74.0 / DPO 27.3.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backend.logic import aging

AS_OF = date(2026, 9, 10)


def _ar(rows):
    return pd.DataFrame(rows)


def _inv(iid, due, amount, status="open", invoice_date="2026-08-01"):
    return {"invoice_id": iid, "client_id": "C1", "retreat_id": "R1",
            "invoice_date": invoice_date, "due_date": due, "amount": amount,
            "status": status, "payment_date": None}


# --------------------------------------------------------------------------------------
# bucket boundaries
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("due,expected", [
    ("2026-09-20", "current"),   # -10 days (not due)
    ("2026-09-10", "0-30"),      # 0
    ("2026-08-11", "0-30"),      # 30
    ("2026-08-10", "31-60"),     # 31
    ("2026-07-12", "31-60"),     # 60
    ("2026-07-11", "61-90"),     # 61
    ("2026-06-12", "61-90"),     # 90
    ("2026-06-11", "90+"),       # 91
])
def test_bucket_boundaries(due, expected):
    res = aging.age_ar(_ar([_inv("AR1", due, 1000.0)]), AS_OF)
    got = next(b["bucket"] for b in res.buckets if b["count"] > 0)
    assert got == expected


def test_void_and_zero_excluded():
    df = _ar([
        _inv("AR1", "2026-06-01", 5000.0, status="void"),
        _inv("AR2", "2026-06-01", 0.0, status="open"),
        _inv("AR3", "2026-06-01", 4000.0, status="open"),
    ])
    res = aging.age_ar(df, AS_OF)
    assert res.total_open == 4000.0
    assert sum(b["amount"] for b in res.buckets) == pytest.approx(4000.0)


def test_paid_excluded_from_open_but_counts_in_dso_denominator():
    df = _ar([
        _inv("AR1", "2026-09-30", 10000.0, status="open", invoice_date="2026-08-31"),
        _inv("AR2", "2026-05-01", 90000.0, status="paid", invoice_date="2026-04-01"),
    ])
    res = aging.age_ar(df, AS_OF)
    assert res.total_open == 10000.0
    # trailing-12m billed includes both invoices (100k); DSO = 10k / 100k * 365
    assert res.metric_denominator == pytest.approx(100000.0)
    assert res.metric_value == pytest.approx(10000.0 / 100000.0 * 365, abs=0.1)


def test_bucket_sums_equal_total():
    rows = [_inv(f"AR{i}", d, amt) for i, (d, amt) in enumerate([
        ("2026-09-20", 1000), ("2026-09-01", 2000), ("2026-08-01", 3000),
        ("2026-07-01", 4000), ("2026-05-01", 5000)])]
    res = aging.age_ar(_ar(rows), AS_OF)
    assert sum(b["amount"] for b in res.buckets) == pytest.approx(res.total_open)
    assert sum(b["count"] for b in res.buckets) == 5


def test_dpo_shape():
    bills = pd.DataFrame([
        {"bill_id": "AP1", "vendor_id": "V1", "retreat_id": "R1", "category": "venue",
         "bill_date": "2026-08-01", "due_date": "2026-09-30", "amount": 20000.0,
         "status": "open", "payment_date": None},
        {"bill_id": "AP2", "vendor_id": "V1", "retreat_id": "R1", "category": "venue",
         "bill_date": "2026-03-01", "due_date": "2026-03-31", "amount": 180000.0,
         "status": "paid", "payment_date": "2026-04-02"},
    ])
    res = aging.age_ap(bills, AS_OF)
    assert res.metric_name == "DPO"
    assert res.metric_denominator == pytest.approx(200000.0)
    assert res.metric_value == pytest.approx(20000.0 / 200000.0 * 365, abs=0.1)


# --------------------------------------------------------------------------------------
# integration: shipped DB reproduces the build's figures
# --------------------------------------------------------------------------------------
def test_shipped_db_reproduces_build_dso_dpo(real_db):
    ar = aging.age_ar(real_db["invoices_ar"])
    ap = aging.age_ap(real_db["bills_ap"])
    assert ar.metric_value == pytest.approx(74.0, abs=0.1), "DSO must match build_dataset.py"
    assert ap.metric_value == pytest.approx(27.3, abs=0.1), "DPO must match build_dataset.py"
    # both must sit inside the SEC-derived validation gate documented in DATA_NOTES §3
    assert 45.0 <= ar.metric_value <= 75.0
    assert 20.0 <= ap.metric_value <= 40.0


def test_shipped_db_buckets_sum_to_open_balance(real_db):
    for res in (aging.age_ar(real_db["invoices_ar"]), aging.age_ap(real_db["bills_ap"])):
        assert sum(b["amount"] for b in res.buckets) == pytest.approx(res.total_open, abs=0.5)
        assert res.overdue_total + res.current_total == pytest.approx(res.total_open, abs=0.5)
        assert all(b["bucket"] in ("current", "0-30", "31-60", "61-90", "90+") for b in res.buckets)
