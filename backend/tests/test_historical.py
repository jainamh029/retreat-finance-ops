"""Reconstructed-history tests: open-balance-as-of, DSO/DPO trend, CEI."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backend.logic import historical as H
from backend.logic.aging import age_ap, age_ar

AS_OF = date(2026, 9, 10)

_INV_COLS = ["invoice_id", "client_id", "retreat_id", "invoice_date", "due_date", "amount",
             "status", "payment_date", "line_role"]


def _inv(rows):
    base = dict(client_id="C1", retreat_id="R1", line_role="final", status="open", payment_date=None)
    return pd.DataFrame([{**base, **r} for r in rows], columns=_INV_COLS)


# --------------------------------------------------------------------------------------
# open-at-a-date reconstruction
# --------------------------------------------------------------------------------------
def test_open_at_date_rules():
    df = _inv([
        {"invoice_id": "A", "invoice_date": "2026-01-01", "due_date": "2026-02-01", "amount": 100,
         "status": "paid", "payment_date": "2026-06-01"},          # open on 2026-03-01, closed on 2026-07-01
        {"invoice_id": "B", "invoice_date": "2026-05-01", "due_date": "2026-06-01", "amount": 200,
         "status": "open", "payment_date": None},                  # not raised yet on 2026-03-01
        {"invoice_id": "C", "invoice_date": "2026-01-01", "due_date": "2026-02-01", "amount": 999,
         "status": "void", "payment_date": None},                  # void — never counts
    ])
    assert H._open_balance_at(df, date(2026, 3, 1), "invoice_date") == 100.0   # only A
    assert H._open_balance_at(df, date(2026, 7, 1), "invoice_date") == 200.0   # A paid, B now raised
    assert H._open_balance_at(df, date(2026, 1, 15), "invoice_date") == 100.0  # A raised, B/C excluded


def test_month_ends_shape():
    pts = H._month_ends(AS_OF, 4)
    assert len(pts) == 4
    assert pts[-1] == AS_OF
    assert all(pts[i] < pts[i + 1] for i in range(len(pts) - 1))


# --------------------------------------------------------------------------------------
# CEI
# --------------------------------------------------------------------------------------
def test_cei_known_values():
    # trailing 30d ending AS_OF; begin = AS_OF-30 = 2026-08-11
    df = _inv([
        # raised well before the window, still open at AS_OF -> counts in begin_AR and end_total,
        # and is 'current' at AS_OF (due in the future) so also in end_current_AR
        {"invoice_id": "OLD", "invoice_date": "2026-06-01", "due_date": "2026-10-01", "amount": 1000,
         "status": "open", "payment_date": None},
        # billed inside the window, collected inside the window -> credit_sales, not in end balances
        {"invoice_id": "NEW1", "invoice_date": "2026-08-20", "due_date": "2026-09-19", "amount": 400,
         "status": "paid", "payment_date": "2026-09-05"},
        # billed inside the window, still open + overdue at AS_OF -> credit_sales + end_total (not current)
        {"invoice_id": "NEW2", "invoice_date": "2026-08-15", "due_date": "2026-09-01", "amount": 300,
         "status": "open", "payment_date": None},
    ])
    r = H.collection_effectiveness_index(df, end=AS_OF, period_days=30)
    assert r["inputs"]["beginning_receivables"] == 1000.0
    assert r["inputs"]["credit_sales"] == 700.0
    assert r["inputs"]["ending_total_receivables"] == 1300.0     # OLD + NEW2
    assert r["inputs"]["ending_current_receivables"] == 1000.0   # only OLD is not-yet-due
    # CEI = (1000 + 700 - 1300) / (1000 + 700 - 1000) * 100 = 400 / 700 * 100
    assert r["cei"] == pytest.approx(400 / 700 * 100, abs=0.1)


def test_cei_perfect_when_nothing_outstanding():
    df = _inv([{"invoice_id": "X", "invoice_date": "2026-08-20", "due_date": "2026-09-05",
                "amount": 500, "status": "paid", "payment_date": "2026-09-06"}])
    r = H.collection_effectiveness_index(df, end=AS_OF, period_days=30)
    assert r["cei"] == pytest.approx(100.0, abs=0.1)


# --------------------------------------------------------------------------------------
# integration: trend endpoint agrees with the point-in-time aging logic
# --------------------------------------------------------------------------------------
def test_trend_last_point_matches_current_dso_dpo(real_db):
    tr = H.dso_dpo_trend(real_db["invoices_ar"], real_db["bills_ap"], months=7)
    assert len(tr["points"]) == 7
    last = tr["points"][-1]
    assert last["as_of"] == str(AS_OF)
    assert last["dso"] == pytest.approx(age_ar(real_db["invoices_ar"]).metric_value, abs=0.2)
    assert last["dpo"] == pytest.approx(age_ap(real_db["bills_ap"]).metric_value, abs=0.2)
    # every reconstructed point is a sane positive number
    assert all(0 < p["dso"] < 400 and 0 < p["dpo"] < 400 for p in tr["points"])


def test_cei_on_real_db_is_plausible(real_db):
    r = H.collection_effectiveness_index(real_db["invoices_ar"])
    assert 0 < r["cei"] <= 120
    ins = r["inputs"]
    assert ins["ending_total_receivables"] > ins["ending_current_receivables"] >= 0
    assert ins["credit_sales"] > 0 and ins["beginning_receivables"] > 0
