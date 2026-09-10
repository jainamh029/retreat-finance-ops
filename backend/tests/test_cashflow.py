"""13-week cash-flow forecast tests."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backend.config import ForecastConfig
from backend.logic import cashflow

AS_OF = date(2026, 9, 10)


_INV_COLS = ["invoice_id", "client_id", "retreat_id", "invoice_date", "due_date", "amount",
             "status", "payment_date", "line_role", "source_ref"]
_BILL_COLS = ["bill_id", "vendor_id", "retreat_id", "category", "bill_date", "due_date",
              "amount", "status", "payment_date", "source_ref"]


def _inv(rows):
    base = dict(client_id="C1", retreat_id="R1", invoice_date="2026-07-01", status="open",
                payment_date=None, line_role="final")
    return pd.DataFrame([{**base, **r} for r in rows], columns=_INV_COLS)


def _bill(rows):
    base = dict(vendor_id="V1", retreat_id="R1", category="venue", bill_date="2026-07-01",
                status="open", payment_date=None, source_ref="UCI_OR2:invoice=x")
    return pd.DataFrame([{**base, **r} for r in rows], columns=_BILL_COLS)


def test_thirteen_weeks_and_seeded_by_start_cash():
    res = cashflow.forecast(_inv([]), _bill([]), as_of=AS_OF, start_cash=100_000.0)
    assert len(res.weeks) == 13
    assert res.start_cash == 100_000.0
    assert all(w["ending_balance"] == 100_000.0 for w in res.weeks)  # nothing scheduled


def test_collection_and_payment_land_in_expected_week():
    cfg = ForecastConfig(ar_collect_prob=1.0, ap_pay_prob=1.0, ar_lag_days=0, ap_lag_days=0)
    inv = _inv([{"invoice_id": "AR1", "due_date": "2026-09-23", "amount": 50_000.0}])
    bil = _bill([{"bill_id": "AP1", "due_date": "2026-09-24", "amount": 20_000.0}])
    res = cashflow.forecast(inv, bil, as_of=AS_OF, start_cash=0.0, cfg=cfg)
    # week starting Mon 2026-09-21 holds both
    wk = next(w for w in res.weeks if w["week_start"] == "2026-09-21")
    assert wk["expected_collections"] == pytest.approx(50_000.0)
    assert wk["scheduled_payments"] == pytest.approx(20_000.0)
    assert wk["net"] == pytest.approx(30_000.0)
    assert res.weeks[-1]["ending_balance"] == pytest.approx(30_000.0)


def test_probability_weighting():
    cfg = ForecastConfig(ar_collect_prob=0.9, ar_lag_days=0)
    inv = _inv([{"invoice_id": "AR1", "due_date": "2026-09-23", "amount": 100_000.0}])
    res = cashflow.forecast(inv, _bill([]), as_of=AS_OF, start_cash=0.0, cfg=cfg)
    assert sum(w["expected_collections"] for w in res.weeks) == pytest.approx(90_000.0)


def test_overdue_item_placed_in_catchup_window():
    cfg = ForecastConfig(ar_collect_prob=1.0, overdue_catchup_days=10)
    inv = _inv([{"invoice_id": "AR1", "due_date": "2026-06-01", "amount": 10_000.0}])  # long overdue
    res = cashflow.forecast(inv, _bill([]), as_of=AS_OF, start_cash=0.0, cfg=cfg)
    # expected ~2026-09-20 -> week starting 2026-09-14
    wk = next(w for w in res.weeks if w["week_start"] == "2026-09-14")
    assert wk["expected_collections"] == pytest.approx(10_000.0)


def test_shortfall_detected():
    cfg = ForecastConfig(ap_pay_prob=1.0, ap_lag_days=0)
    bil = _bill([{"bill_id": "AP1", "due_date": "2026-09-23", "amount": 80_000.0}])
    res = cashflow.forecast(_inv([]), bil, as_of=AS_OF, start_cash=50_000.0, cfg=cfg)
    assert res.min_ending_balance == pytest.approx(-30_000.0)
    assert "2026-09-21" in res.shortfall_weeks


def test_pipeline_projects_deposits_for_uninvoiced_upcoming_retreats():
    cfg = ForecastConfig(ar_collect_prob=1.0, ar_lag_days=0, pipeline_deposit_frac=0.5)
    retreats = pd.DataFrame([
        {"retreat_id": "R_NEW", "start_date": "2026-11-02", "budget_total": 100_000.0},
        {"retreat_id": "R_HASINV", "start_date": "2026-11-02", "budget_total": 100_000.0},
    ])
    inv = _inv([{"invoice_id": "AR1", "retreat_id": "R_HASINV", "due_date": "2026-10-01",
                 "amount": 40_000.0, "status": "paid"}])
    res = cashflow.forecast(inv, _bill([]), as_of=AS_OF, start_cash=0.0, cfg=cfg, retreats=retreats)
    pipe = sum(w["pipeline_collections"] for w in res.weeks)
    assert pipe == pytest.approx(50_000.0)         # only R_NEW, 50% of 100k; R_HASINV already invoiced
    assert "1 upcoming retreat" in res.assumptions["pipeline"]


# --------------------------------------------------------------------------------------
# integration
# --------------------------------------------------------------------------------------
def test_shipped_db_forecast(real_db):
    res = cashflow.forecast(real_db["invoices_ar"], real_db["bills_ap"],
                            real_db["bank_transactions"], retreats=real_db["retreats"])
    assert len(res.weeks) == 13
    assert "upcoming retreat" in res.assumptions["pipeline"]
    assert res.start_cash == pytest.approx(
        cashflow.latest_cash_position(real_db["bank_transactions"]), abs=0.01)
    # running balance chains correctly week to week
    bal = res.start_cash
    for w in res.weeks:
        bal = round(bal + w["net"], 2)
        assert w["ending_balance"] == pytest.approx(bal, abs=0.01)
