"""AR / AP aging buckets + DSO / DPO.

Pure logic. Must reproduce the figures data/build_dataset.py prints (DSO 74.0, DPO 27.3 on the
shipped DB) — test_aging.py asserts this.

Definitions
-----------
Aging bucket (per open item, by days between as_of and DUE date):
    d < 0            -> "current"  (not yet due)
    0  <= d <= 30    -> "0-30"
    31 <= d <= 60    -> "31-60"
    61 <= d <= 90    -> "61-90"
    d  > 90          -> "90+"
Only rows with status in {open, partial} and amount > 0 are aged.

DSO = AR_open / (trailing-12-month billed AR) * 365
DPO = AP_open / (trailing-12-month AP)        * 365
where AR_open / AP_open are the summed open (status in {open,partial}, amount>0) balances, and
the trailing window is [as_of - 365d, as_of] on invoice_date / bill_date. DPO uses spend as the
denominator (revenue/spend proxy — no COGS split in this dataset; consistent with the SEC
benchmark method, see DATA_NOTES §3 / TRD NFR-4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from backend.config import AGING_BUCKETS, AS_OF


def _as_date(v) -> date:
    if isinstance(v, pd.Timestamp):
        return v.date()
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return pd.Timestamp(v).date()


def _bucket(days: int) -> str:
    if days < 0:
        return "current"
    if days <= 30:
        return "0-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    return "90+"


@dataclass
class AgingResult:
    as_of: str
    buckets: list[dict]          # [{bucket, count, amount}]
    total_open: float
    overdue_total: float         # everything except "current"
    current_total: float
    metric_name: str             # "DSO" or "DPO"
    metric_value: float
    metric_numerator: float      # open balance
    metric_denominator: float    # trailing-12m billed / spend
    rows: list[dict] = field(default_factory=list)   # per-item, for table drill-down


def _age(df: pd.DataFrame, due_col: str, as_of: date) -> tuple[dict, list[dict]]:
    buckets = {b: {"bucket": b, "count": 0, "amount": 0.0} for b in AGING_BUCKETS}
    rows: list[dict] = []
    open_mask = df["status"].isin(["open", "partial"]) & (df["amount"] > 0)
    for r in df[open_mask].to_dict("records"):
        d = (as_of - _as_date(r[due_col])).days
        b = _bucket(d)
        buckets[b]["count"] += 1
        buckets[b]["amount"] += float(r["amount"])
        rows.append({"id": r.get("invoice_id") or r.get("bill_id"),
                     "counterparty_id": r.get("client_id") or r.get("vendor_id"),
                     "retreat_id": r.get("retreat_id"),
                     "amount": round(float(r["amount"]), 2),
                     "due_date": str(_as_date(r[due_col])),
                     "days_overdue": d, "bucket": b, "status": r["status"]})
    for b in buckets.values():
        b["amount"] = round(b["amount"], 2)
    return buckets, rows


def _trailing_positive(df: pd.DataFrame, date_col: str, as_of: date) -> float:
    lo = as_of - timedelta(days=365)
    s = df[(df["amount"] > 0)
           & (df[date_col].map(_as_date) >= lo)
           & (df[date_col].map(_as_date) <= as_of)]["amount"].sum()
    return round(float(s), 2)


def age_ar(invoices: pd.DataFrame, as_of: date | None = None) -> AgingResult:
    as_of = as_of or AS_OF
    buckets, rows = _age(invoices, "due_date", as_of)
    open_bal = round(float(invoices.loc[invoices["status"].isin(["open", "partial"])
                                        & (invoices["amount"] > 0), "amount"].sum()), 2)
    billed = _trailing_positive(invoices, "invoice_date", as_of)
    dso = round(open_bal / billed * 365, 1) if billed else 0.0
    overdue = round(sum(b["amount"] for k, b in buckets.items() if k != "current"), 2)
    return AgingResult(
        as_of=str(as_of), buckets=list(buckets.values()), total_open=open_bal,
        overdue_total=overdue, current_total=buckets["current"]["amount"],
        metric_name="DSO", metric_value=dso, metric_numerator=open_bal,
        metric_denominator=billed, rows=rows,
    )


def age_ap(bills: pd.DataFrame, as_of: date | None = None) -> AgingResult:
    as_of = as_of or AS_OF
    buckets, rows = _age(bills, "due_date", as_of)
    open_bal = round(float(bills.loc[bills["status"].isin(["open", "partial"])
                                     & (bills["amount"] > 0), "amount"].sum()), 2)
    spend = _trailing_positive(bills, "bill_date", as_of)
    dpo = round(open_bal / spend * 365, 1) if spend else 0.0
    overdue = round(sum(b["amount"] for k, b in buckets.items() if k != "current"), 2)
    return AgingResult(
        as_of=str(as_of), buckets=list(buckets.values()), total_open=open_bal,
        overdue_total=overdue, current_total=buckets["current"]["amount"],
        metric_name="DPO", metric_value=dpo, metric_numerator=open_bal,
        metric_denominator=spend, rows=rows,
    )


def dso(invoices: pd.DataFrame, as_of: date | None = None) -> float:
    return age_ar(invoices, as_of).metric_value


def dpo(bills: pd.DataFrame, as_of: date | None = None) -> float:
    return age_ap(bills, as_of).metric_value
