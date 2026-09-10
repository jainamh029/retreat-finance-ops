"""Central configuration for the backend logic + API.

Nothing tolerance-related is hardcoded inside the logic modules — it all lives here as
documented defaults, and the API passes overrides in per request (TRD §4, NFR-2).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# --- paths ---
REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("RETREAT_DB_PATH", REPO_ROOT / "data" / "retreat_finance.db"))
DATA_NOTES_URL = "data/DATA_NOTES.md"

# --- the "as of" date the dataset was built around (data/build_dataset.py AS_OF) ---
AS_OF = date(2026, 9, 10)


@dataclass(frozen=True)
class ReconConfig:
    """Bank-reconciliation matching tolerances.

    A candidate ledger row is a *possible* match for a bank transaction only if BOTH:
      - |bank amount| is within max(amount_tol_abs, amount_tol_pct% of ledger amount), AND
      - the bank date is within date_window_days of the ledger's due date.
    Survivors are scored (amount / date / name); the best pairing above `accept_score` wins,
    assigned greedily so one bank line matches at most one ledger row and vice versa.

    date_window_days default is 30, not the 5 sketched in the TRD: matching anchors on the
    ledger's DUE date (the only date an analyst has before reconciling), and real payments land
    anywhere from a few days early to ~4 weeks late on net-15..45 terms. ±5d around the due date
    would miss the large majority of legitimately-late vendor payments. 30d is the honest
    default for rediscovering matches from the ledger alone; the UI still lets the analyst tune
    it, and the date component of the score still rewards closer dates within the window.
    """
    amount_tol_pct: float = 1.0        # percent of the ledger amount
    amount_tol_abs: float = 5.00       # dollars — floor for tiny invoices
    date_window_days: int = 30
    min_name_score: float = 45.0       # block candidates whose name barely matches the memo
    accept_score: float = 60.0        # combined 0-100 score required to accept a match
    # score weights (must sum to 1.0)
    w_amount: float = 0.50
    w_date: float = 0.20
    w_name: float = 0.30
    # a bank transaction below this absolute value is ignored for "unexplained" findings
    material_amount: float = 50.00

    def validate(self) -> None:
        if abs(self.w_amount + self.w_date + self.w_name - 1.0) > 1e-6:
            raise ValueError("recon score weights must sum to 1.0")
        if self.date_window_days < 0 or self.amount_tol_pct < 0 or self.amount_tol_abs < 0:
            raise ValueError("tolerances must be non-negative")


@dataclass(frozen=True)
class ForecastConfig:
    """13-week cash-flow forecast assumptions.

    Mirrors the payment model in data/build_dataset.py: open receivables are expected to collect
    at (due date + ar_lag_days), weighted by ar_collect_prob; open payables are expected to be
    paid at (due date + ap_lag_days), weighted by ap_pay_prob. Items already past due are
    expected within `overdue_catchup_days` of the as-of date.
    """
    weeks: int = 13
    ar_collect_prob: float = 0.89     # == build's converged ar_pay_prob
    ap_pay_prob: float = 0.90         # == build's converged ap_pay_prob
    ar_lag_days: int = 1             # == build's converged ar_lag_median
    ap_lag_days: int = 12            # == build's converged ap_lag_median
    overdue_catchup_days: int = 10    # overdue items assumed to clear this many days after as-of
    pipeline_deposit_frac: float = 0.40   # projected client deposit for an upcoming, not-yet-invoiced
                                         # retreat, as a fraction of budget_total (0 disables)


AGING_BUCKETS = ("current", "0-30", "31-60", "61-90", "90+")
