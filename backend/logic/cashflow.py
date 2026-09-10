"""13-week rolling cash-flow forecast.

Pure logic. Mirrors the payment model in data/build_dataset.py (ForecastConfig defaults ==
the build's converged params):

  expected collection date for an open invoice
      = due_date + ar_lag_days           if due_date >= as_of
      = as_of + overdue_catchup_days     if already past due
  expected payment date for an open bill  -- same shape with ap_lag_days

  weekly expected_collections = sum(amount * ar_collect_prob) over invoices landing that week
  weekly scheduled_payments   = sum(amount * ap_pay_prob)     over bills   landing that week
  ending_balance[w] = ending_balance[w-1] + collections[w] - payments[w],  seeded by start_cash

start_cash defaults to the latest running_balance in bank_transactions at/of the as-of date
(the real current cash position), else a supplied figure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from backend.config import AS_OF, ForecastConfig


def _as_date(v) -> date:
    if isinstance(v, pd.Timestamp):
        return v.date()
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return pd.Timestamp(v).date()


def _week_index(d: date, week0_start: date) -> int:
    return (d - week0_start).days // 7


@dataclass
class ForecastResult:
    as_of: str
    start_cash: float
    weeks: list[dict]            # [{week, week_start, week_end, expected_collections,
                                #   pipeline_collections, scheduled_payments, net, ending_balance}]
    min_ending_balance: float
    min_week_start: str
    shortfall_weeks: list[str]   # week_start values where ending_balance < 0
    assumptions: dict


def latest_cash_position(bank: pd.DataFrame, as_of: date | None = None) -> float:
    as_of = as_of or AS_OF
    b = bank.copy()
    b["d"] = b["transaction_date"].map(_as_date)
    b = b[b["d"] <= as_of].sort_values(["d", "transaction_id"])
    if b.empty:
        return 0.0
    return round(float(b.iloc[-1]["running_balance"]), 2)


def forecast(invoices: pd.DataFrame, bills: pd.DataFrame, bank: pd.DataFrame | None = None,
             as_of: date | None = None, start_cash: float | None = None,
             cfg: ForecastConfig | None = None,
             retreats: pd.DataFrame | None = None) -> ForecastResult:
    as_of = as_of or AS_OF
    cfg = cfg or ForecastConfig()
    if start_cash is None:
        start_cash = latest_cash_position(bank, as_of) if bank is not None else 0.0

    week0 = as_of - timedelta(days=as_of.weekday())        # Monday of the as-of week
    horizon_end = week0 + timedelta(days=7 * cfg.weeks)

    weeks = [{
        "week": i + 1,
        "week_start": (week0 + timedelta(days=7 * i)).isoformat(),
        "week_end": (week0 + timedelta(days=7 * i + 6)).isoformat(),
        "expected_collections": 0.0,      # from open AR already on the books
        "pipeline_collections": 0.0,      # projected deposits for upcoming retreats not yet invoiced
        "scheduled_payments": 0.0,
    } for i in range(cfg.weeks)]

    def _place(df, due_col, lag, prob, key):
        open_rows = df[df["status"].isin(["open", "partial"]) & (df["amount"] > 0)]
        for r in open_rows.to_dict("records"):
            due = _as_date(r[due_col])
            exp = (due + timedelta(days=lag)) if due >= as_of else (as_of + timedelta(days=cfg.overdue_catchup_days))
            if exp < week0 or exp >= horizon_end:
                continue
            wi = _week_index(exp, week0)
            weeks[wi][key] += float(r["amount"]) * prob

    _place(invoices, "due_date", cfg.ar_lag_days, cfg.ar_collect_prob, "expected_collections")
    _place(bills, "due_date", cfg.ap_lag_days, cfg.ap_pay_prob, "scheduled_payments")

    # pipeline: retreats starting inside the horizon whose deposit invoice has not been raised yet
    # -> project a deposit ~= pipeline_deposit_frac * budget_total, invoiced ~45d before start on
    #    net-37 terms, collected at due + ar_lag, weighted by ar_collect_prob.
    pipeline_note = "disabled (no retreats df passed)"
    if retreats is not None and cfg.pipeline_deposit_frac > 0:
        invoiced_retreats = set(invoices["retreat_id"])
        n = 0
        for r in retreats.to_dict("records"):
            if r["retreat_id"] in invoiced_retreats:
                continue
            start = _as_date(r["start_date"])
            deposit_due = start - timedelta(days=45) + timedelta(days=37)   # invoice ~45d out, net-37
            exp = deposit_due + timedelta(days=cfg.ar_lag_days)
            if exp < week0 or exp >= horizon_end:
                continue
            amt = float(r["budget_total"]) * cfg.pipeline_deposit_frac * cfg.ar_collect_prob
            weeks[_week_index(exp, week0)]["pipeline_collections"] += amt
            n += 1
        pipeline_note = (f"{n} upcoming retreat(s) projected at "
                         f"{cfg.pipeline_deposit_frac:.0%} of budget as a client deposit")

    bal = start_cash
    min_bal, min_wk = float("inf"), weeks[0]["week_start"]
    shortfalls = []
    for w in weeks:
        w["expected_collections"] = round(w["expected_collections"], 2)
        w["pipeline_collections"] = round(w["pipeline_collections"], 2)
        w["scheduled_payments"] = round(w["scheduled_payments"], 2)
        w["net"] = round(w["expected_collections"] + w["pipeline_collections"]
                         - w["scheduled_payments"], 2)
        bal = round(bal + w["net"], 2)
        w["ending_balance"] = bal
        if bal < min_bal:
            min_bal, min_wk = bal, w["week_start"]
        if bal < 0:
            shortfalls.append(w["week_start"])

    return ForecastResult(
        as_of=str(as_of), start_cash=round(start_cash, 2), weeks=weeks,
        min_ending_balance=min_bal, min_week_start=min_wk, shortfall_weeks=shortfalls,
        assumptions={
            "weeks": cfg.weeks,
            "ar_collect_prob": cfg.ar_collect_prob, "ap_pay_prob": cfg.ap_pay_prob,
            "ar_lag_days": cfg.ar_lag_days, "ap_lag_days": cfg.ap_lag_days,
            "overdue_catchup_days": cfg.overdue_catchup_days,
            "pipeline": pipeline_note,
            "note": "expected values are probability-weighted; lags/probs mirror the dataset "
                    "build's converged payment model (DATA_NOTES §3). pipeline_collections are "
                    "projected deposits for upcoming retreats not yet invoiced.",
        },
    )
