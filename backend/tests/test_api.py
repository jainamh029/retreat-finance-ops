"""API-layer tests.

Per the TRD, these do NOT touch the real SQLite file. Every test patches the data-access layer
(`backend.data_access.*`) with canned returns, so what's under test is purely the FastAPI layer:
routing, query-param parsing/validation, error-code mapping, and that handlers are thin
pass-throughs (return the dao result verbatim, forward params unchanged).

One `test_wiring_composes_on_fake_db` patches `backend.db.load_all` with tiny in-memory frames
to prove the route -> data_access -> logic composition holds end-to-end without a DB file.
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app

client = TestClient(app)


# --------------------------------------------------------------------------------------
# thin-handler tests: data-access layer fully mocked
# --------------------------------------------------------------------------------------
def test_health_shape():
    with patch("backend.data_access.tables", return_value={"clients": pd.DataFrame({"x": [1, 2]})}):
        r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and "data_as_of" in body
    assert body["db_rows"] == {"clients": 2}


def test_dashboard_summary_is_passthrough():
    canned = {"as_of": "2026-09-10", "cards": {"dso": {"value": 74.0}}, "reconciliation": {}}
    with patch("backend.data_access.dashboard_summary", return_value=canned) as m:
        r = client.get("/api/dashboard/summary?as_of=2026-08-01")
    assert r.status_code == 200
    assert r.json() == canned
    m.assert_called_once_with("2026-08-01")


def test_dashboard_insights_passthrough():
    canned = {"cei": {"cei": 69.4}, "dso_dpo_trend": {"points": []}, "top_overdue_ar": []}
    with patch("backend.data_access.dashboard_insights", return_value=canned) as m:
        r = client.get("/api/dashboard/insights?as_of=2026-07-01")
    assert r.status_code == 200 and r.json() == canned
    m.assert_called_once_with("2026-07-01")


def test_reconciliation_sensitivity_passthrough():
    canned = {"points": [{"date_window_days": 5, "recall_pct": 20.1}], "default_window": 30}
    with patch("backend.data_access.recon_sensitivity", return_value=canned) as m:
        r = client.get("/api/reconciliation/sensitivity")
    assert r.status_code == 200 and r.json() == canned
    m.assert_called_once_with()


def test_invoices_forwards_all_filters():
    with patch("backend.data_access.invoices_view", return_value={"rows": []}) as m:
        r = client.get("/api/invoices?status=overdue&client_id=CLIENT_A&bucket=61-90"
                       "&date_from=2026-01-01&date_to=2026-06-30&sort=amount&limit=5&offset=10")
    assert r.status_code == 200
    kwargs = m.call_args.kwargs
    assert kwargs == {"status": "overdue", "client_id": "CLIENT_A", "bucket": "61-90",
                      "date_from": "2026-01-01", "date_to": "2026-06-30", "sort": "amount",
                      "limit": 5, "offset": 10}


def test_bills_forwards_category_and_vendor():
    with patch("backend.data_access.bills_view", return_value={"rows": []}) as m:
        r = client.get("/api/bills?vendor_id=VEND_003&category=travel&bucket=90%2B")
    assert r.status_code == 200
    kw = m.call_args.kwargs
    assert kw["vendor_id"] == "VEND_003" and kw["category"] == "travel" and kw["bucket"] == "90+"


@pytest.mark.parametrize("path", ["/api/invoices?bucket=nonsense", "/api/bills?category=food",
                                  "/api/cashflow/forecast?weeks=0",
                                  "/api/reconciliation/report?date_window_days=999",
                                  "/api/audit/findings?severity=critical"])
def test_bad_query_params_return_422(path):
    r = client.get(path)
    assert r.status_code == 422


def test_aging_endpoints_passthrough():
    for ep, fn in [("/api/aging/ar", "aging_ar"), ("/api/aging/ap", "aging_ap")]:
        with patch(f"backend.data_access.{fn}", return_value={"buckets": [], "metric": {}}) as m:
            r = client.get(ep + "?as_of=2026-07-01")
        assert r.status_code == 200 and r.json() == {"buckets": [], "metric": {}}
        m.assert_called_once_with("2026-07-01")


def test_cashflow_defaults_and_overrides():
    with patch("backend.data_access.cashflow_forecast", return_value={"weeks": []}) as m:
        client.get("/api/cashflow/forecast")
        assert m.call_args.kwargs == {"start_cash": None, "weeks": 13, "as_of": None}
        client.get("/api/cashflow/forecast?weeks=8&start_cash=100000&as_of=2026-09-01")
        assert m.call_args.kwargs == {"start_cash": 100000.0, "weeks": 8, "as_of": "2026-09-01"}


def test_reconciliation_report_builds_config_from_params():
    with patch("backend.data_access.reconciliation_report", return_value={"stats": {}}) as m:
        client.get("/api/reconciliation/report")
        cfg = m.call_args.args[0]
        assert (cfg.amount_tol_pct, cfg.date_window_days, cfg.min_name_score) == (1.0, 30, 45.0)

        client.get("/api/reconciliation/report?amount_tol_pct=2.5&date_window_days=7&min_name_score=60")
        cfg = m.call_args.args[0]
        assert cfg.amount_tol_pct == 2.5 and cfg.date_window_days == 7 and cfg.min_name_score == 60.0
        assert cfg.amount_tol_abs == 5.0  # untouched default still present


def test_reconciliation_exception_404_maps_keyerror():
    with patch("backend.data_access.reconciliation_exception", side_effect=KeyError("BT9999")):
        r = client.get("/api/reconciliation/exceptions/BT9999")
    assert r.status_code == 404
    assert "BT9999" in r.json()["detail"]


def test_reconciliation_exception_ok_passthrough():
    canned = {"transaction": {"transaction_id": "BT1"}, "status": "exception", "candidates": []}
    with patch("backend.data_access.reconciliation_exception", return_value=canned) as m:
        r = client.get("/api/reconciliation/exceptions/BT1?date_window_days=12")
    assert r.status_code == 200 and r.json() == canned
    assert m.call_args.args[0] == "BT1"
    assert m.call_args.args[1].date_window_days == 12


def test_budget_vs_actual_404_maps_keyerror():
    with patch("backend.data_access.budget_vs_actual", side_effect=KeyError("NOPE")):
        r = client.get("/api/retreats/NOPE/budget-vs-actual")
    assert r.status_code == 404


def test_audit_findings_type_alias_and_severity():
    with patch("backend.data_access.audit_findings", return_value={"rows": []}) as m:
        client.get("/api/audit/findings?type=unexplained_txn&severity=high")
    assert m.call_args.kwargs == {"severity": "high", "finding_type": "unexplained_txn"}


def test_config_endpoint_exposes_tolerance_defaults_and_bounds():
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()["reconciliation"]
    assert body["defaults"]["date_window_days"] == 30
    assert body["defaults"]["amount_tol_pct"] == 1.0
    assert set(body["bounds"]) >= {"amount_tol_pct", "date_window_days", "min_name_score"}
    assert "date_window_days" in body["tunable"]


# --------------------------------------------------------------------------------------
# CORS
# --------------------------------------------------------------------------------------
def test_cors_allows_configured_and_pages_origins():
    for origin in ("http://localhost:5173", "https://someuser.github.io"):
        r = client.get("/api/health", headers={"Origin": origin})
        assert r.headers.get("access-control-allow-origin") == origin


def test_cors_blocks_unknown_origin():
    r = client.get("/api/health", headers={"Origin": "https://evil.example.com"})
    assert r.headers.get("access-control-allow-origin") not in ("https://evil.example.com", "*")


# --------------------------------------------------------------------------------------
# wiring: patch the DB loader with tiny frames, let route->dao->logic run for real
# --------------------------------------------------------------------------------------
def _fake_tables() -> dict[str, pd.DataFrame]:
    clients = pd.DataFrame([{"client_id": "C1", "name": "Client A", "industry_category": "SaaS",
                             "contract_start_date": "2024-01-01"}])
    vendors = pd.DataFrame([{"vendor_id": "V1", "name": "Vendor 01 - Venue", "category": "venue",
                             "payment_terms_days": 30}])
    retreats = pd.DataFrame([{"retreat_id": "R1", "client_id": "C1", "destination": "Austin, TX",
                              "headcount": 40, "start_date": "2026-07-01", "end_date": "2026-07-04",
                              "budget_venue": 10000.0, "budget_catering": 20000.0,
                              "budget_travel": 30000.0, "budget_activities": 5000.0,
                              "budget_other": 6500.0, "budget_total": 71500.0}])
    invoices = pd.DataFrame([
        {"invoice_id": "AR1", "client_id": "C1", "retreat_id": "R1", "invoice_date": "2026-05-01",
         "due_date": "2026-06-01", "amount": 40000.0, "status": "paid",
         "payment_date": "2026-06-02", "source_ref": "UCI_OR2:invoice=1", "line_role": "deposit"},
        {"invoice_id": "AR2", "client_id": "C1", "retreat_id": "R1", "invoice_date": "2026-07-05",
         "due_date": "2026-08-04", "amount": 55000.0, "status": "open", "payment_date": None,
         "source_ref": "UCI_OR2:invoice=2", "line_role": "final"},
    ])
    bills = pd.DataFrame([
        {"bill_id": "AP1", "vendor_id": "V1", "retreat_id": "R1", "category": "venue",
         "bill_date": "2026-05-10", "due_date": "2026-06-09", "amount": 11500.0, "status": "paid",
         "payment_date": "2026-06-10", "source_ref": "UCI_OR2:invoice=3"},
    ])
    bank = pd.DataFrame([
        {"transaction_id": "BT1", "transaction_date": "2026-06-02", "description":
         "ACH CREDIT CLIENT A INV AR1 REF1", "amount": 40000.0, "running_balance": 240000.0,
         "source_ref": "BERKA:trans_id=1 (real date; amount=ledger; memo synthesised)"},
        {"transaction_id": "BT2", "transaction_date": "2026-06-10", "description":
         "BILL PAY VENDOR 01 - VENUE AP1 REF2", "amount": -11500.0, "running_balance": 228500.0,
         "source_ref": "BERKA:trans_id=2 (real date; amount=ledger; memo synthesised)"},
        {"transaction_id": "BT3", "transaction_date": "2026-06-15", "description":
         "TRANSACTION INTEREST CREDITED  [UROK]", "amount": 120.0, "running_balance": 228620.0,
         "source_ref": "BERKA:trans_id=3 (real row, intact)"},
    ])
    matches = pd.DataFrame([
        {"match_id": "M1", "transaction_id": "BT1", "matched_type": "invoice", "matched_id": "AR1",
         "confidence_score": 100.0, "match_method": "ground_truth_build"},
        {"match_id": "M2", "transaction_id": "BT2", "matched_type": "bill", "matched_id": "AP1",
         "confidence_score": 100.0, "match_method": "ground_truth_build"},
    ])
    findings = pd.DataFrame([
        {"finding_id": "F1", "finding_type": "unexplained_txn", "related_ids": '["BT3"]',
         "description": "interest, no ledger match", "severity": "medium", "date_found": "2026-09-10"},
    ])
    sec = pd.DataFrame([
        {"company_name": "Marriott", "ticker": "MAR", "fiscal_period": "FY2024",
         "ar_balance": 1.0, "ap_balance": 1.0, "revenue": 1.0, "computed_dso": 40.6,
         "computed_dpo": 11.1, "source_url": "https://data.sec.gov/..."},
    ])
    prov = pd.DataFrame([
        {"source_id": "SRC_UCI_OR2", "label": "UCI Online Retail II", "kind": "dataset",
         "url": "https://archive.ics.uci.edu/dataset/502/online+retail+ii", "accessed": "2026-09-10",
         "notes": "x"},
        {"source_id": "SRC_PEERSPACE", "label": "Peerspace", "kind": "pricing",
         "url": "https://peerspace.com", "accessed": "2026-09-10", "notes": "x"},
    ])
    bprov = pd.DataFrame([
        {"retreat_id": "R1", "category": "venue", "source_id": "SRC_PEERSPACE",
         "basis": "$500/hr x 8h x 3d"},
    ])
    out = {"clients": clients, "vendors": vendors, "retreats": retreats, "invoices_ar": invoices,
           "bills_ap": bills, "bank_transactions": bank, "reconciliation_matches": matches,
           "audit_findings": findings, "sec_benchmarks": sec, "provenance_sources": prov,
           "retreat_budget_provenance": bprov}
    for name, dcols in {"invoices_ar": ["invoice_date", "due_date", "payment_date"],
                        "bills_ap": ["bill_date", "due_date", "payment_date"],
                        "bank_transactions": ["transaction_date"],
                        "retreats": ["start_date", "end_date"],
                        "audit_findings": ["date_found"],
                        "clients": ["contract_start_date"]}.items():
        for c in dcols:
            out[name][c] = pd.to_datetime(out[name][c])
    return out


@pytest.fixture
def fake_db():
    import backend.data_access as da
    da._CACHE.clear()
    with patch("backend.db.load_all", side_effect=lambda *a, **k: _fake_tables()):
        yield
    da._CACHE.clear()


def test_reconciliation_report_works_cold(fake_db):
    # regression: /api/reconciliation/report as the FIRST call to touch the data layer
    # (tables() clears the recon sub-cache on load — _run_recon must load tables first)
    import backend.data_access as da
    da._CACHE.clear()
    r = client.get("/api/reconciliation/report")
    assert r.status_code == 200
    acc = r.json()["matched_accounting"]
    assert acc["sum_check"] == acc["matched_total"]


def test_wiring_composes_on_fake_db(fake_db):
    # dashboard: real aging + real reconcile + real forecast run on the tiny frames
    d = client.get("/api/dashboard/summary").json()
    assert d["cards"]["ar_outstanding"] == 55000.0          # only AR2 is open
    assert d["cards"]["ap_outstanding"] == 0.0
    assert d["reconciliation"]["matched"] == 2               # BT1->AR1, BT2->AP1
    assert d["reconciliation"]["findings_by_type"].get("unexplained_txn") == 1  # BT3 interest

    rep = client.get("/api/reconciliation/report").json()
    assert rep["ground_truth"]["recall_pct"] == 100.0
    assert {m["matched_id"] for m in rep["matched"]} == {"AR1", "AP1"}
    assert [f["finding_type"] for f in rep["findings"]] == ["unexplained_txn"]

    exc = client.get("/api/reconciliation/exceptions/BT3").json()
    assert exc["status"] == "exception" and exc["matched"] is None

    bva = client.get("/api/retreats/R1/budget-vs-actual").json()
    venue = next(x for x in bva["lines"] if x["category"] == "venue")
    assert venue["actual"] == 11500.0 and venue["budget"] == 10000.0 and venue["over_10pct"] is True
    assert venue["price_source"]["source"] == "Peerspace"

    inv = client.get("/api/invoices?bucket=31-60").json()
    assert inv["total"] == 1 and inv["rows"][0]["id"] == "AR2"   # AR2 open, ~37d overdue at as_of

    ins = client.get("/api/dashboard/insights").json()
    assert ins["dso_dpo_trend"]["points"][-1]["as_of"] == "2026-09-10"
    assert ins["cei"]["cei"] is not None
    # AR2 ($55k, open, overdue, client C1) is the sole overdue receivable
    assert ins["top_overdue_ar"][0]["id"] == "C1" and ins["top_overdue_ar"][0]["overdue_amount"] == 55000.0
    assert {c["category"] for c in ins["vendor_category_spend"]} <= {"venue", "catering", "travel", "activities", "other"}
    assert ins["findings_breakdown"]["total"] == 1

    sens = client.get("/api/reconciliation/sensitivity").json()
    assert [p["date_window_days"] for p in sens["points"]] == [5, 10, 15, 20, 30, 45]
    assert all(p["recall_pct"] <= sens["points"][i + 1]["recall_pct"]
               for i, p in enumerate(sens["points"][:-1]))   # monotonic in the window
