"""Reconstructed historical views — DSO/DPO trend and Collection Effectiveness Index.

The shipped dataset is a single snapshot at AS_OF. There is no stored historical AR/AP balance,
so "the balance at an earlier date" is *reconstructed* from each row's raise date + payment date:

    a row is OPEN at date D  ⇔  raise_date ≤ D  AND  (payment_date is null OR payment_date > D)
                                 AND status != 'void'  AND  amount > 0

Documented caveat (surfaced in the API `note` field and the README):
  - `void` (written-off) rows are excluded at every historical point, even though the write-off
    may have happened after D — the dataset has no write-off date.
  - `payment_date` is only populated for rows settled on/before AS_OF, so reconstruction is exact
    for D ≤ AS_OF (which is all we plot).
This is a reconstruction, not a recorded time series — labelled as such wherever shown.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from backend.config import AS_OF
from backend.logic.aging import _as_date, age_ar


def _open_at(df: pd.DataFrame, at: date, raise_col: str) -> pd.DataFrame:
    raised = df[raise_col].map(_as_date) <= at
    paid_after = df["payment_date"].map(lambda v: v is not None and not pd.isna(v) and _as_date(v) <= at)
    not_void = df["status"] != "void"
    return df[raised & ~paid_after & not_void & (df["amount"] > 0)]


def _open_balance_at(df: pd.DataFrame, at: date, raise_col: str) -> float:
    return round(float(_open_at(df, at, raise_col)["amount"].sum()), 2)


def _trailing_billed(df: pd.DataFrame, at: date, raise_col: str, days: int = 365) -> float:
    lo = at - timedelta(days=days)
    m = (df["amount"] > 0) & (df[raise_col].map(_as_date) > lo) & (df[raise_col].map(_as_date) <= at)
    return round(float(df[m]["amount"].sum()), 2)


def _month_ends(end: date, n: int) -> list[date]:
    """`n` month-boundary dates ending at `end` (most recent last)."""
    out, y, m = [], end.year, end.month
    for _ in range(n):
        out.append(date(y, m, 1) + timedelta(days=32))
        out[-1] = out[-1].replace(day=1) - timedelta(days=1)   # last day of month (y, m)
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    out = sorted(x for x in out if x <= end)
    if out and out[-1] != end:
        out.append(end)
    return out[-n:]


def dso_dpo_trend(invoices: pd.DataFrame, bills: pd.DataFrame,
                  end: date | None = None, months: int = 7) -> dict:
    end = end or AS_OF
    pts = []
    for d in _month_ends(end, months):
        ar_open = _open_balance_at(invoices, d, "invoice_date")
        ap_open = _open_balance_at(bills, d, "bill_date")
        billed = _trailing_billed(invoices, d, "invoice_date")
        spend = _trailing_billed(bills, d, "bill_date")
        pts.append({
            "as_of": d.isoformat(),
            "dso": round(ar_open / billed * 365, 1) if billed else None,
            "dpo": round(ap_open / spend * 365, 1) if spend else None,
        })
    return {
        "points": pts,
        "dso_band": [45.0, 75.0], "dpo_band": [20.0, 40.0],
        "note": "reconstructed from invoice/bill + payment dates (no stored historical balance); "
                "void write-offs excluded at all points. See DATA_NOTES.md / README.",
    }


def collection_effectiveness_index(invoices: pd.DataFrame,
                                   end: date | None = None, period_days: int = 90) -> dict:
    """Classic CEI over a trailing period ending `end`.

        CEI = (begin_AR + credit_sales - end_total_AR)
              --------------------------------------------------- * 100
              (begin_AR + credit_sales - end_current_AR)

    All four inputs are computable from this dataset:
      begin_AR         = reconstructed open AR balance at (end - period_days)
      credit_sales     = billed AR in (end - period_days, end]
      end_total_AR     = current open AR balance
      end_current_AR   = the 'current' (not-yet-due) aging bucket at `end`
    """
    end = end or AS_OF
    begin = end - timedelta(days=period_days)
    begin_ar = _open_balance_at(invoices, begin, "invoice_date")
    credit_sales = _trailing_billed(invoices, end, "invoice_date", days=period_days)
    ar = age_ar(invoices, end)
    end_total = ar.total_open
    end_current = next(b["amount"] for b in ar.buckets if b["bucket"] == "current")

    num = begin_ar + credit_sales - end_total
    den = begin_ar + credit_sales - end_current
    cei = round(num / den * 100, 1) if den else None
    return {
        "cei": cei,
        "period_days": period_days,
        "period": {"from": begin.isoformat(), "to": end.isoformat()},
        "inputs": {
            "beginning_receivables": begin_ar,
            "credit_sales": credit_sales,
            "ending_total_receivables": end_total,
            "ending_current_receivables": round(end_current, 2),
        },
        "interpretation": ("100% = every receivable collectible in the period was collected; "
                           "lower = slower collection of what came due"),
        "note": "beginning receivables reconstructed from invoice/payment dates (no stored "
                "historical balance); void write-offs excluded. Trailing "
                f"{period_days}-day period.",
    }
