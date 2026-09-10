"""Reconciliation engine tests.

Model-logic unit tests use tiny hand-built frames. The integration block runs the engine on the
shipped DB and asserts the ground-truth rediscovery rate meets the PRD's >=85% success criterion
(SC-1) against the reconciliation_matches table build_dataset.py wrote.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backend.config import ReconConfig
from backend.logic import reconcile as R


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def _bank(rows):
    return pd.DataFrame(rows)


def _txn(tid, dt, amount, desc, bal=0.0):
    return {"transaction_id": tid, "transaction_date": dt, "description": desc,
            "amount": amount, "running_balance": bal, "source_ref": f"BERKA:trans_id={tid}"}


_INV_COLS = ["invoice_id", "client_id", "retreat_id", "invoice_date", "due_date", "amount",
             "status", "payment_date", "line_role", "source_ref"]
_BILL_COLS = ["bill_id", "vendor_id", "retreat_id", "category", "bill_date", "due_date",
              "amount", "status", "payment_date", "source_ref"]


def _invoices(rows):
    base = dict(client_id="C1", retreat_id="R1", invoice_date="2026-05-01",
                status="open", payment_date=None, line_role="final", source_ref="UCI_OR2:invoice=x")
    return pd.DataFrame([{**base, **r} for r in rows], columns=_INV_COLS)


def _bills(rows):
    base = dict(vendor_id="V1", retreat_id="R1", category="venue", bill_date="2026-05-01",
                status="open", payment_date=None, source_ref="UCI_OR2:invoice=x")
    return pd.DataFrame([{**base, **r} for r in rows], columns=_BILL_COLS)


CLIENTS = pd.DataFrame([{"client_id": "C1", "name": "Client A", "industry_category": "x",
                         "contract_start_date": "2024-01-01"},
                        {"client_id": "C2", "name": "Client B", "industry_category": "x",
                         "contract_start_date": "2024-01-01"}])
VENDORS = pd.DataFrame([{"vendor_id": "V1", "name": "Vendor 01 - Venue", "category": "venue",
                         "payment_terms_days": 30},
                        {"vendor_id": "V2", "name": "Vendor 02 - Catering", "category": "catering",
                         "payment_terms_days": 15}])


def run(bank, invoices=None, bills=None, cfg=None):
    invoices = invoices if invoices is not None else _invoices([])
    bills = bills if bills is not None else _bills([])
    return R.reconcile(bank, invoices, bills, CLIENTS, VENDORS, cfg or ReconConfig())


# --------------------------------------------------------------------------------------
# unit: matching behaviour
# --------------------------------------------------------------------------------------
def test_exact_match():
    inv = _invoices([{"invoice_id": "AR1", "due_date": "2026-06-01", "amount": 12345.67}])
    bank = _bank([_txn("BT1", "2026-06-02", 12345.67, "ACH CREDIT CLIENT A INV AR1")])
    res = run(bank, invoices=inv)
    assert len(res.matched) == 1
    m = res.matched[0]
    assert m.matched_id == "AR1" and m.matched_type == "invoice"
    assert m.amount_delta == 0.0
    assert m.confidence >= 95
    assert "exact_amount" in m.match_method


def test_within_tolerance_match_and_out_of_tolerance_rejected():
    inv = _invoices([{"invoice_id": "AR1", "due_date": "2026-06-01", "amount": 10000.00}])
    within = _bank([_txn("BT1", "2026-06-03", 9950.00, "WIRE IN CLIENT A INV AR1")])   # -0.5%
    assert len(run(within, invoices=inv).matched) == 1

    outside = _bank([_txn("BT2", "2026-06-03", 9800.00, "WIRE IN CLIENT A INV AR1")])  # -2%
    res = run(outside, invoices=inv)
    assert res.matched == []
    assert res.unmatched_bank[0]["transaction_id"] == "BT2"


def test_date_window_enforced():
    inv = _invoices([{"invoice_id": "AR1", "due_date": "2026-06-01", "amount": 5000.0}])
    cfg = ReconConfig(date_window_days=5)
    assert run(_bank([_txn("BT1", "2026-06-04", 5000.0, "CLIENT A AR1")]), invoices=inv, cfg=cfg).matched
    assert run(_bank([_txn("BT2", "2026-06-20", 5000.0, "CLIENT A AR1")]), invoices=inv, cfg=cfg).matched == []


def test_polarity_receipt_only_matches_invoice():
    inv = _invoices([{"invoice_id": "AR1", "due_date": "2026-06-01", "amount": 7000.0}])
    bill = _bills([{"bill_id": "AP1", "due_date": "2026-06-01", "amount": 7000.0}])
    # a positive (receipt) transaction must not match the bill
    res = run(_bank([_txn("BT1", "2026-06-02", 7000.0, "DEPOSIT CLIENT A")]), invoices=inv, bills=bill)
    assert res.matched[0].matched_type == "invoice"
    # a negative (payment) transaction must not match the invoice
    res2 = run(_bank([_txn("BT2", "2026-06-02", -7000.0, "BILL PAY VENDOR 01 - VENUE")]),
               invoices=inv, bills=bill)
    assert res2.matched[0].matched_type == "bill"


def test_fuzzy_name_disambiguates_same_amount():
    inv = _invoices([
        {"invoice_id": "AR1", "client_id": "C1", "due_date": "2026-06-01", "amount": 8000.0},
        {"invoice_id": "AR2", "client_id": "C2", "due_date": "2026-06-01", "amount": 8000.0},
    ])
    bank = _bank([_txn("BT1", "2026-06-02", 8000.0, "ACH CREDIT CLIENT B INV AR2 REF999")])
    res = run(bank, invoices=inv)
    assert res.matched[0].matched_id == "AR2"          # name breaks the amount tie
    assert res.matched[0].name_score >= 80


def test_ambiguous_lowers_confidence():
    inv = _invoices([
        {"invoice_id": "AR1", "client_id": "C1", "due_date": "2026-06-01", "amount": 9000.00},
        {"invoice_id": "AR2", "client_id": "C1", "due_date": "2026-06-02", "amount": 9000.00},
    ])
    bank = _bank([_txn("BT1", "2026-06-02", 9000.00, "ACH CREDIT CLIENT A")])
    res = run(bank, invoices=inv)
    assert len(res.matched) == 1
    # two near-identical candidates -> runner-up gap small -> confidence haircut applied
    assert res.matched[0].runner_up_gap < 12
    assert res.matched[0].confidence < 99


def test_one_bank_line_matches_one_ledger_row():
    inv = _invoices([{"invoice_id": "AR1", "due_date": "2026-06-01", "amount": 4000.0}])
    bank = _bank([
        _txn("BT1", "2026-06-02", 4000.0, "ACH CREDIT CLIENT A INV AR1"),
        _txn("BT2", "2026-06-03", 4000.0, "ACH CREDIT CLIENT A INV AR1"),
    ])
    res = run(bank, invoices=inv)
    assert len(res.matched) == 1                       # AR1 can't be matched twice
    assert len(res.unmatched_bank) == 1
    # the second identical receipt should surface as a double-payment finding
    assert any(f["finding_type"] == "double_payment" for f in res.findings)


def test_candidate_panel_explains_non_match():
    # amount 4% off -> outside the +/-1% tolerance -> not matched, but the relaxed explain-panel
    # should still surface AR1 as the closest row and say why it was rejected (PRD US-6).
    inv = _invoices([{"invoice_id": "AR1", "due_date": "2026-06-01", "amount": 10000.0}])
    bank = _bank([_txn("BT1", "2026-06-09", 9600.0, "WIRE IN CLIENT A")])
    res = run(bank, invoices=inv)
    assert res.matched == []
    panel = res.candidates_by_txn["BT1"]
    assert panel and panel[0]["matched_id"] == "AR1"
    assert "amount off by $400" in panel[0]["verdict"]


def test_unexplained_when_no_candidate():
    bank = _bank([_txn("BT1", "2026-06-01", -2500.0, "OUTGOING TRANSFER INTEREST [UROK]")])
    res = run(bank)
    assert [f["finding_type"] for f in res.findings] == ["unexplained_txn"]


# --------------------------------------------------------------------------------------
# integration: shipped DB, ground-truth rediscovery (PRD SC-1)
# --------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def recon_on_real(real_db):
    res = R.reconcile(real_db["bank_transactions"], real_db["invoices_ar"], real_db["bills_ap"],
                      real_db["clients"], real_db["vendors"], ReconConfig())
    gt = R.score_against_ground_truth(res, real_db["reconciliation_matches"])
    return res, gt


def test_match_rate_meets_prd_criterion(recon_on_real):
    _, gt = recon_on_real
    assert gt["recall_pct"] >= 85.0, (
        f"PRD SC-1: ground-truth recall {gt['recall_pct']}% < 85% "
        f"(rediscovered {gt['rediscovered']}/{gt['truth_primary_settlements']})")


def test_precision_and_no_noise_false_positives(recon_on_real):
    _, gt = recon_on_real
    assert gt["precision_pct"] >= 95.0
    assert gt["false_positive_on_noise"] == 0, "engine must not match Berka noise rows to ledger items"


def test_all_injected_double_payments_flagged(recon_on_real):
    res, gt = recon_on_real
    dp = [f for f in res.findings if f["finding_type"] == "double_payment"]
    assert len(dp) == 3, "the build injected exactly 3 duplicate settlements"
    assert gt["duplicate_settlement_pairs_flagged"] == "3/3"
    for f in dp:
        assert f["severity"] == "high"
        assert sum(1 for i in f["related_ids"] if str(i).startswith("BT")) == 2


def test_real_unexplained_transactions_surface(recon_on_real, real_db):
    res, _ = recon_on_real
    unexp = [f for f in res.findings if f["finding_type"] == "unexplained_txn"]
    # the 24 real Berka noise rows (interest / penalty / SIPO / pension / insurance / loan)
    assert len(unexp) >= 20
    bt_ids = {i for f in unexp for i in f["related_ids"]}
    bank = real_db["bank_transactions"].set_index("transaction_id")
    real_noise = sum(1 for i in bt_ids if "real row, intact" in bank.loc[i, "source_ref"])
    assert real_noise >= 20, "most unexplained findings must be the genuine Berka noise rows"


def test_every_finding_is_traceable(recon_on_real, real_db):
    res, _ = recon_on_real
    bank_ids = set(real_db["bank_transactions"]["transaction_id"])
    inv_ids = set(real_db["invoices_ar"]["invoice_id"])
    bill_ids = set(real_db["bills_ap"]["bill_id"])
    known = bank_ids | inv_ids | bill_ids
    for f in res.findings:
        assert all(str(i) in known for i in f["related_ids"]), f


def test_config_is_not_hardcoded(recon_on_real, real_db):
    # tightening the window must change the result (proves tolerance flows through)
    tight = R.reconcile(real_db["bank_transactions"], real_db["invoices_ar"], real_db["bills_ap"],
                        real_db["clients"], real_db["vendors"],
                        ReconConfig(date_window_days=2, amount_tol_pct=0.1, amount_tol_abs=1.0))
    res, _ = recon_on_real
    assert tight.stats["matched"] < res.stats["matched"]
