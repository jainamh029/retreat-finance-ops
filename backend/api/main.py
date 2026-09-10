"""FastAPI app — thin REST layer over backend/data_access.py + backend/logic/*.

Route handlers do exactly three things: read query params, build a config object, call ONE
data-access/logic function, return its result. No filtering, no aggregation, no math here
(TRD constraint). All money-bearing responses carry a `provenance` block.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend import data_access as dao
from backend.config import ReconConfig

app = FastAPI(
    title="Retreat Finance Ops API",
    version="0.1.0",
    description="AP/AR reconciliation, aging, budgeting and 13-week cash-flow for a "
                "corporate-retreat business. Built on real public data — see /api/provenance.",
)

# --- CORS: GitHub Pages origin + local dev; GET only ---
_ORIGINS = [o for o in os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://localhost:8000,http://127.0.0.1:5173,http://127.0.0.1:8000",
).split(",") if o]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ORIGINS,
    allow_origin_regex=r"https://.*\.github\.io",
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


def _recon_cfg(amount_tol_pct, amount_tol_abs, date_window_days, min_name_score) -> ReconConfig:
    d = ReconConfig()
    return ReconConfig(
        amount_tol_pct=amount_tol_pct if amount_tol_pct is not None else d.amount_tol_pct,
        amount_tol_abs=amount_tol_abs if amount_tol_abs is not None else d.amount_tol_abs,
        date_window_days=date_window_days if date_window_days is not None else d.date_window_days,
        min_name_score=min_name_score if min_name_score is not None else d.min_name_score,
    )


# --------------------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------------------
@app.get("/api/health")
def health():
    t = dao.tables()
    return {"status": "ok", "data_as_of": dao.data_as_of(),
            "db_rows": {k: len(v) for k, v in t.items()}}


@app.get("/api/provenance")
def provenance():
    return dao.provenance()


@app.get("/api/config")
def config():
    return dao.recon_defaults()


@app.get("/api/benchmarks")
def benchmarks():
    return dao.benchmarks()


# --------------------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------------------
@app.get("/api/dashboard/summary")
def dashboard_summary(as_of: str | None = None):
    return dao.dashboard_summary(as_of)


# --------------------------------------------------------------------------------------
# AR / AP lists
# --------------------------------------------------------------------------------------
@app.get("/api/invoices")
def invoices(
    status: str | None = None,
    client_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    bucket: str | None = Query(None, pattern="^(current|0-30|31-60|61-90|90\\+)$"),
    sort: str | None = None,
    limit: int | None = Query(None, ge=1, le=1000),
    offset: int | None = Query(None, ge=0),
):
    return dao.invoices_view(status=status, client_id=client_id, date_from=date_from,
                             date_to=date_to, bucket=bucket, sort=sort, limit=limit, offset=offset)


@app.get("/api/bills")
def bills(
    status: str | None = None,
    vendor_id: str | None = None,
    category: str | None = Query(None, pattern="^(venue|catering|travel|activities|other)$"),
    date_from: str | None = None,
    date_to: str | None = None,
    bucket: str | None = Query(None, pattern="^(current|0-30|31-60|61-90|90\\+)$"),
    sort: str | None = None,
    limit: int | None = Query(None, ge=1, le=1000),
    offset: int | None = Query(None, ge=0),
):
    return dao.bills_view(status=status, vendor_id=vendor_id, category=category,
                          date_from=date_from, date_to=date_to, bucket=bucket, sort=sort,
                          limit=limit, offset=offset)


# --------------------------------------------------------------------------------------
# aging
# --------------------------------------------------------------------------------------
@app.get("/api/aging/ar")
def aging_ar(as_of: str | None = None):
    return dao.aging_ar(as_of)


@app.get("/api/aging/ap")
def aging_ap(as_of: str | None = None):
    return dao.aging_ap(as_of)


# --------------------------------------------------------------------------------------
# cash flow
# --------------------------------------------------------------------------------------
@app.get("/api/cashflow/forecast")
def cashflow_forecast(
    start_cash: float | None = None,
    weeks: int = Query(13, ge=1, le=52),
    as_of: str | None = None,
):
    return dao.cashflow_forecast(start_cash=start_cash, weeks=weeks, as_of=as_of)


# --------------------------------------------------------------------------------------
# reconciliation
# --------------------------------------------------------------------------------------
@app.get("/api/reconciliation/report")
def reconciliation_report(
    amount_tol_pct: float | None = Query(None, ge=0, le=10),
    amount_tol_abs: float | None = Query(None, ge=0, le=1000),
    date_window_days: int | None = Query(None, ge=0, le=120),
    min_name_score: float | None = Query(None, ge=0, le=100),
):
    return dao.reconciliation_report(
        _recon_cfg(amount_tol_pct, amount_tol_abs, date_window_days, min_name_score))


@app.get("/api/reconciliation/exceptions/{txn_id}")
def reconciliation_exception(
    txn_id: str,
    amount_tol_pct: float | None = Query(None, ge=0, le=10),
    amount_tol_abs: float | None = Query(None, ge=0, le=1000),
    date_window_days: int | None = Query(None, ge=0, le=120),
    min_name_score: float | None = Query(None, ge=0, le=100),
):
    try:
        return dao.reconciliation_exception(
            txn_id, _recon_cfg(amount_tol_pct, amount_tol_abs, date_window_days, min_name_score))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"transaction {txn_id!r} not found") from None


@app.get("/api/audit/findings")
def audit_findings(
    severity: str | None = Query(None, pattern="^(low|medium|high)$"),
    type: str | None = Query(None, alias="type"),
):
    return dao.audit_findings(severity=severity, finding_type=type)


# --------------------------------------------------------------------------------------
# retreats + budget vs actual
# --------------------------------------------------------------------------------------
@app.get("/api/retreats")
def retreats(client_id: str | None = None):
    return dao.retreats_view(client_id=client_id)


@app.get("/api/retreats/{retreat_id}/budget-vs-actual")
def budget_vs_actual(retreat_id: str):
    try:
        return dao.budget_vs_actual(retreat_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"retreat {retreat_id!r} not found") from None


@app.exception_handler(ValueError)
def _value_error(request, exc):  # bad date strings etc.
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422, content={"error": "bad_request", "detail": str(exc)})
