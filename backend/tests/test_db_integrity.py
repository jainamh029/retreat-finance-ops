"""Integrity pass on data/retreat_finance.db — the same checks data/build_dataset.py runs at
build time, kept as a permanent regression so nothing drifts across later steps.

Skipped (not failed) when the DB has not been built.
"""
from __future__ import annotations

import json

import pytest

from backend import db
from backend.config import AS_OF, DB_PATH


@pytest.fixture(scope="module")
def con():
    if not DB_PATH.exists():
        pytest.skip(f"{DB_PATH} not found — run `python data/build_dataset.py`")
    c = db.connect()
    yield c
    c.close()


def _scalar(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


# --------------------------------------------------------------------------------------
def test_no_orphan_foreign_keys(con):
    checks = {
        "invoices_ar.client_id": "SELECT COUNT(*) FROM invoices_ar i LEFT JOIN clients x USING(client_id) WHERE x.client_id IS NULL",
        "invoices_ar.retreat_id": "SELECT COUNT(*) FROM invoices_ar i LEFT JOIN retreats x USING(retreat_id) WHERE x.retreat_id IS NULL",
        "bills_ap.vendor_id": "SELECT COUNT(*) FROM bills_ap b LEFT JOIN vendors x USING(vendor_id) WHERE x.vendor_id IS NULL",
        "bills_ap.retreat_id": "SELECT COUNT(*) FROM bills_ap b LEFT JOIN retreats x USING(retreat_id) WHERE x.retreat_id IS NULL",
        "retreats.client_id": "SELECT COUNT(*) FROM retreats r LEFT JOIN clients x USING(client_id) WHERE x.client_id IS NULL",
        "matches.transaction_id": "SELECT COUNT(*) FROM reconciliation_matches m LEFT JOIN bank_transactions x USING(transaction_id) WHERE x.transaction_id IS NULL",
        "match->invoice": "SELECT COUNT(*) FROM reconciliation_matches m LEFT JOIN invoices_ar i ON m.matched_id=i.invoice_id WHERE m.matched_type='invoice' AND i.invoice_id IS NULL",
        "match->bill": "SELECT COUNT(*) FROM reconciliation_matches m LEFT JOIN bills_ap b ON m.matched_id=b.bill_id WHERE m.matched_type='bill' AND b.bill_id IS NULL",
        "budget_prov->retreat": "SELECT COUNT(*) FROM retreat_budget_provenance p LEFT JOIN retreats r USING(retreat_id) WHERE r.retreat_id IS NULL",
        "budget_prov->source": "SELECT COUNT(*) FROM retreat_budget_provenance p LEFT JOIN provenance_sources s USING(source_id) WHERE s.source_id IS NULL",
    }
    orphans = {k: _scalar(con, v) for k, v in checks.items()}
    assert orphans == {k: 0 for k in checks}, orphans


def test_status_payment_date_invariant(con):
    assert _scalar(con, "SELECT COUNT(*) FROM invoices_ar WHERE (status='paid') <> (payment_date IS NOT NULL)") == 0
    assert _scalar(con, "SELECT COUNT(*) FROM bills_ap   WHERE (status='paid') <> (payment_date IS NOT NULL)") == 0


def test_every_row_traces_to_a_real_source(con):
    assert _scalar(con, "SELECT COUNT(*) FROM invoices_ar WHERE source_ref NOT LIKE 'UCI_OR2:%'") == 0
    assert _scalar(con, "SELECT COUNT(*) FROM bills_ap   WHERE source_ref NOT LIKE 'UCI_OR2:%'") == 0
    assert _scalar(con, "SELECT COUNT(*) FROM bank_transactions WHERE source_ref NOT LIKE 'BERKA:%'") == 0
    assert _scalar(con, "SELECT COUNT(*) FROM invoices_ar WHERE source_ref IS NULL OR source_ref=''") == 0


def test_budget_total_equals_component_sum(con):
    bad = _scalar(con, """SELECT COUNT(*) FROM retreats
        WHERE ABS(budget_total-(budget_venue+budget_catering+budget_travel
                                +budget_activities+budget_other)) > 1""")
    assert bad == 0


def test_running_balance_is_continuous(con):
    bad = _scalar(con, """
        SELECT COUNT(*) FROM (
            SELECT running_balance,
                   ROUND(SUM(amount) OVER (ORDER BY transaction_id) + 250000, 2) AS calc
            FROM bank_transactions)
        WHERE ABS(running_balance - calc) > 0.011""")
    assert bad == 0


def test_no_real_counterparty_id_leaks_into_names(con):
    # real UCI customer ids / Berka account ids live only inside source_ref, never in a name
    assert _scalar(con, "SELECT COUNT(*) FROM clients WHERE name GLOB '*[0-9][0-9][0-9][0-9]*'") == 0
    assert _scalar(con, "SELECT COUNT(*) FROM vendors WHERE name GLOB '*[0-9][0-9][0-9][0-9]*'") == 0


def test_audit_findings_related_ids_all_resolve(con):
    known = set()
    for tbl, col in (("bank_transactions", "transaction_id"), ("invoices_ar", "invoice_id"),
                     ("bills_ap", "bill_id")):
        known |= {r[0] for r in con.execute(f"SELECT {col} FROM {tbl}")}
    for (rel,) in con.execute("SELECT related_ids FROM audit_findings"):
        for rid in json.loads(rel):
            assert str(rid) in known, rid


def test_status_and_bucket_domain_values(con):
    assert _scalar(con, "SELECT COUNT(*) FROM invoices_ar WHERE status NOT IN ('open','paid','partial','void')") == 0
    assert _scalar(con, "SELECT COUNT(*) FROM bills_ap   WHERE status NOT IN ('open','paid','partial','void')") == 0
    assert _scalar(con, "SELECT COUNT(*) FROM bills_ap   WHERE category NOT IN ('venue','catering','travel','activities','other')") == 0


def test_dso_dpo_still_inside_the_validation_gate(con):
    """The SEC-derived gate from DATA_NOTES §3 — asserts the shipped DB never drifted out of band."""
    lo = f"date('{AS_OF.isoformat()}','-365 day')"
    hi = f"'{AS_OF.isoformat()}'"
    dso = _scalar(con, f"""
        SELECT ROUND(
          (SELECT SUM(amount) FROM invoices_ar WHERE status IN('open','partial') AND amount>0)
          / (SELECT SUM(amount) FROM invoices_ar WHERE amount>0 AND invoice_date>={lo} AND invoice_date<={hi})
          * 365, 1)""")
    dpo = _scalar(con, f"""
        SELECT ROUND(
          (SELECT SUM(amount) FROM bills_ap WHERE status IN('open','partial') AND amount>0)
          / (SELECT SUM(amount) FROM bills_ap WHERE amount>0 AND bill_date>={lo} AND bill_date<={hi})
          * 365, 1)""")
    assert 45.0 <= dso <= 75.0, f"DSO {dso} outside SEC-derived gate 45–75"
    assert 20.0 <= dpo <= 40.0, f"DPO {dpo} outside SEC-derived gate 20–40"


def test_row_counts_in_expected_ranges(con):
    n = {t: _scalar(con, f"SELECT COUNT(*) FROM {t}") for t in
         ("clients", "vendors", "retreats", "invoices_ar", "bills_ap", "bank_transactions",
          "reconciliation_matches", "audit_findings", "sec_benchmarks")}
    assert 80 <= n["invoices_ar"] <= 150, n
    assert 300 <= n["bills_ap"] <= 400, n
    assert n["sec_benchmarks"] == 16
    assert n["reconciliation_matches"] >= n["bank_transactions"] - 60  # settlements + injected dups
