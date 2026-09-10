"""View / composition layer between the SQLite tables and the API.

Route handlers call these functions and return the result verbatim — all filtering, pagination,
column derivation and cross-module composition lives here, not in `backend/api/main.py`
(TRD: "no business logic in route handlers"). The heavy lifting (matching, aging, forecast) is
delegated to `backend/logic/*`; this module only shapes and joins.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date

import pandas as pd

from backend import db
from backend.config import AS_OF, ForecastConfig, ReconConfig
from backend.logic import aging as _aging
from backend.logic import cashflow as _cashflow
from backend.logic import historical as _hist
from backend.logic import reconcile as _recon

# --------------------------------------------------------------------------------------
# cached table load (data is static — rebuilt offline by data/build_dataset.py)
# --------------------------------------------------------------------------------------
_CACHE: dict = {}


def tables(refresh: bool = False) -> dict[str, pd.DataFrame]:
    if refresh or "t" not in _CACHE:
        _CACHE["t"] = db.load_all()
        _CACHE.pop("recon", None)
    return _CACHE["t"]


def data_as_of() -> str:
    return str(AS_OF)


def _as_of(v: str | None) -> date:
    return date.fromisoformat(v) if v else AS_OF


def _d(v) -> date | None:
    if v is None or v is pd.NaT:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return _aging._as_date(v)


def _iso(v) -> str | None:
    d = _d(v)
    return d.isoformat() if d else None


# --------------------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------------------
def provenance() -> dict:
    df = tables()["provenance_sources"]
    return {
        "sources": df.to_dict("records"),
        "data_notes": "data/DATA_NOTES.md",
        "headline_caveat": (
            "Bank-feed timing, amounts and duplicate/noise structure are real (Berka). The "
            "matchable description text on settlement transactions is synthesised — the "
            "anonymised source has no counterparty text to fuzzy-match against."
        ),
    }


def _money_provenance() -> list[dict]:
    return [
        {"field": "invoice/bill amounts", "source": "UCI Online Retail II",
         "url": "https://archive.ics.uci.edu/dataset/502/online+retail+ii", "accessed": "2026-09-10"},
        {"field": "bank transaction dates", "source": "Berka / PKDD'99",
         "url": "http://sorry.vse.cz/~berka/challenge/pkdd1999/berka.htm", "accessed": "2026-09-10"},
        {"field": "DSO/DPO benchmarks", "source": "SEC EDGAR XBRL",
         "url": "https://data.sec.gov/api/xbrl/", "accessed": "2026-09-10"},
    ]


# --------------------------------------------------------------------------------------
# AR / AP list views
# --------------------------------------------------------------------------------------
_SORTABLE = {"amount", "due_date", "invoice_date", "bill_date", "days_overdue", "status"}


def _paginate(rows: list[dict], limit: int | None, offset: int | None) -> tuple[list[dict], int]:
    total = len(rows)
    o = offset or 0
    rows = rows[o: o + limit] if limit is not None else rows[o:]
    return rows, total


def _list_view(kind: str, *, status, counterparty_id, category, date_from, date_to, bucket,
               sort, limit, offset) -> dict:
    t = tables()
    if kind == "ar":
        df = t["invoices_ar"].copy()
        names = t["clients"].set_index("client_id")["name"].to_dict()
        df["counterparty"] = df["client_id"].map(names)
        df["id"] = df["invoice_id"]
        base_date_col, cp_col = "invoice_date", "client_id"
    else:
        df = t["bills_ap"].copy()
        names = t["vendors"].set_index("vendor_id")["name"].to_dict()
        df["counterparty"] = df["vendor_id"].map(names)
        df["id"] = df["bill_id"]
        base_date_col, cp_col = "bill_date", "vendor_id"

    as_of = AS_OF
    df["days_overdue"] = df["due_date"].map(lambda v: (as_of - _aging._as_date(v)).days)
    df["aging_bucket"] = df.apply(
        lambda r: "n/a" if r["status"] not in ("open", "partial") or r["amount"] <= 0
        else _aging._bucket(r["days_overdue"]), axis=1)

    if status and status != "all":
        if status == "overdue":
            df = df[(df["status"].isin(["open", "partial"])) & (df["days_overdue"] > 0) & (df["amount"] > 0)]
        else:
            df = df[df["status"] == status]
    if counterparty_id:
        df = df[df[cp_col] == counterparty_id]
    if category and kind == "ap":
        df = df[df["category"] == category]
    if date_from:
        df = df[df[base_date_col].map(_aging._as_date) >= date.fromisoformat(date_from)]
    if date_to:
        df = df[df[base_date_col].map(_aging._as_date) <= date.fromisoformat(date_to)]
    if bucket:
        df = df[df["aging_bucket"] == bucket]

    sort_col = sort if sort in _SORTABLE else "days_overdue"
    ascending = sort_col not in ("days_overdue", "amount")
    df = df.sort_values(sort_col, ascending=ascending, kind="stable")

    out_cols = ["id", "counterparty", "retreat_id", base_date_col, "due_date", "amount",
                "status", "payment_date", "days_overdue", "aging_bucket", "source_ref"]
    if kind == "ar":
        out_cols.insert(3, "line_role")
    if kind == "ap":
        out_cols.insert(3, "category")
    recs = []
    for r in df[out_cols].to_dict("records"):
        r[base_date_col] = _iso(r[base_date_col])
        r["due_date"] = _iso(r["due_date"])
        r["payment_date"] = _iso(r["payment_date"])
        r["amount"] = round(float(r["amount"]), 2)
        recs.append(r)

    page, total = _paginate(recs, limit, offset)
    return {
        "count": len(page), "total": total, "limit": limit, "offset": offset or 0,
        "sort": sort_col, "filters_applied": {
            "status": status, "counterparty_id": counterparty_id, "category": category,
            "date_from": date_from, "date_to": date_to, "bucket": bucket},
        "filtered_amount": round(sum(r["amount"] for r in recs), 2),
        "rows": page,
        "provenance": _money_provenance(),
    }


def invoices_view(**kw) -> dict:
    return _list_view("ar", counterparty_id=kw.pop("client_id", None), category=None, **kw)


def bills_view(**kw) -> dict:
    return _list_view("ap", counterparty_id=kw.pop("vendor_id", None), **kw)


# --------------------------------------------------------------------------------------
# aging
# --------------------------------------------------------------------------------------
def _aging_payload(res: _aging.AgingResult, benchmark: tuple[float, float]) -> dict:
    return {
        "as_of": res.as_of,
        "buckets": res.buckets,
        "total_open": res.total_open,
        "current_total": res.current_total,
        "overdue_total": res.overdue_total,
        "metric": {
            "name": res.metric_name, "value": res.metric_value,
            "numerator": round(res.metric_numerator, 2),
            "denominator": round(res.metric_denominator, 2),
            "benchmark_range": list(benchmark),
            "in_benchmark": benchmark[0] <= res.metric_value <= benchmark[1],
            "basis": "revenue/spend proxy (no COGS split); see DATA_NOTES §3",
        },
        "rows": res.rows,
        "provenance": _money_provenance(),
    }


def aging_ar(as_of: str | None = None) -> dict:
    return _aging_payload(_aging.age_ar(tables()["invoices_ar"], _as_of(as_of)), (45.0, 75.0))


def aging_ap(as_of: str | None = None) -> dict:
    return _aging_payload(_aging.age_ap(tables()["bills_ap"], _as_of(as_of)), (20.0, 40.0))


# --------------------------------------------------------------------------------------
# cash flow
# --------------------------------------------------------------------------------------
def cashflow_forecast(start_cash: float | None = None, weeks: int = 13,
                      as_of: str | None = None) -> dict:
    t = tables()
    cfg = ForecastConfig(weeks=weeks)
    res = _cashflow.forecast(t["invoices_ar"], t["bills_ap"], t["bank_transactions"],
                             _as_of(as_of), start_cash, cfg, retreats=t["retreats"])
    return {**asdict(res), "provenance": _money_provenance()}


# --------------------------------------------------------------------------------------
# reconciliation (cached per config)
# --------------------------------------------------------------------------------------
def _cfg_key(cfg: ReconConfig) -> tuple:
    return (cfg.amount_tol_pct, cfg.amount_tol_abs, cfg.date_window_days, cfg.min_name_score,
            cfg.accept_score, cfg.w_amount, cfg.w_date, cfg.w_name, cfg.material_amount)


def _run_recon(cfg: ReconConfig):
    t = tables()                                  # must precede setdefault: tables() clears "recon"
    cache = _CACHE.setdefault("recon", {})
    key = _cfg_key(cfg)
    if key not in cache:
        res = _recon.reconcile(t["bank_transactions"], t["invoices_ar"], t["bills_ap"],
                               t["clients"], t["vendors"], cfg)
        gt = _recon.score_against_ground_truth(res, t["reconciliation_matches"])
        cache[key] = (res, gt)
    return cache[key]


def reconciliation_report(cfg: ReconConfig | None = None) -> dict:
    cfg = cfg or ReconConfig()
    res, gt = _run_recon(cfg)
    d = _recon.result_to_dict(res)
    d["ground_truth"] = gt
    d["ground_truth_note"] = (
        "recall/precision measure the algorithm's timing-resolution and exception-handling "
        "accuracy on a corpus with a known amount simplification (settlement amounts were built "
        "to equal ledger amounts exactly), not amount-fuzzing robustness — see DATA_NOTES.md."
    )
    # Reconcile stats.matched against the ground-truth counters, so the arithmetic is explicit:
    #   matched (431 on the shipped default run)
    #     = rediscovered (426, correct primary settlements)
    #     + wrong_target (2, right txn / wrong ledger id, both in the 437-row primary set)
    #     + duplicate_settlement_first_of_pair (3): the FIRST bank line of each of the 3 injected
    #       duplicate-settlement pairs, correctly matched to its bill (BT0253->AP0188,
    #       BT0329->AP0284, BT0355->AP0220). These sit OUTSIDE the 437 "primary settlements"
    #       because their matched_id is shared by two truth rows, so score_against_ground_truth
    #       scores them via `duplicate_settlement_pairs_flagged` (their twins are the
    #       double_payment findings), not via recall. They are NOT addon/credit lines.
    dup_first = (gt["duplicate_settlement_rows_in_truth"] // 2)
    d["matched_accounting"] = {
        "matched_total": res.stats["matched"],
        "rediscovered_primary": gt["rediscovered"],
        "wrong_target_primary": gt["wrong_target"],
        "duplicate_settlement_first_of_pair": dup_first,
        "sum_check": gt["rediscovered"] + gt["wrong_target"] + dup_first,
        "note": "matched_total = rediscovered_primary + wrong_target_primary + "
                "duplicate_settlement_first_of_pair; the 2nd line of each duplicate pair is a "
                "double_payment finding, not a match.",
    }
    return d


def reconciliation_exception(txn_id: str, cfg: ReconConfig | None = None) -> dict:
    cfg = cfg or ReconConfig()
    res, _ = _run_recon(cfg)
    bank = tables()["bank_transactions"].set_index("transaction_id")
    if txn_id not in bank.index:
        raise KeyError(txn_id)
    trow = bank.loc[txn_id]
    matched = next((m for m in res.matched if m.transaction_id == txn_id), None)
    cands = res.candidates_by_txn.get(txn_id, [])
    return {
        "transaction": {
            "transaction_id": txn_id,
            "transaction_date": _iso(trow["transaction_date"]),
            "description": trow["description"],
            "amount": round(float(trow["amount"]), 2),
            "running_balance": None if pd.isna(trow["running_balance"]) else round(float(trow["running_balance"]), 2),
            "source_ref": trow["source_ref"],
        },
        "matched": asdict(matched) if matched else None,
        "status": "matched" if matched else "exception",
        "candidates": cands,
        "config": _recon._cfg_public(cfg),
    }


# --------------------------------------------------------------------------------------
# audit findings (from the DB table the build wrote)
# --------------------------------------------------------------------------------------
def audit_findings(severity: str | None = None, finding_type: str | None = None) -> dict:
    import json
    df = tables()["audit_findings"].copy()
    if severity:
        df = df[df["severity"] == severity]
    if finding_type:
        df = df[df["finding_type"] == finding_type]
    rows = []
    for r in df.to_dict("records"):
        r["related_ids"] = json.loads(r["related_ids"]) if isinstance(r["related_ids"], str) else r["related_ids"]
        r["date_found"] = _iso(r["date_found"])
        rows.append(r)
    from collections import Counter
    return {
        "count": len(rows),
        "by_type": dict(sorted(Counter(r["finding_type"] for r in rows).items())),
        "by_severity": dict(sorted(Counter(r["severity"] for r in rows).items())),
        "rows": rows,
        "note": "these are the findings materialised by data/build_dataset.py at default "
                "tolerance; /api/reconciliation/report recomputes findings live for any tolerance.",
    }


# --------------------------------------------------------------------------------------
# retreats + budget vs actual
# --------------------------------------------------------------------------------------
def _actuals_by_cat(retreat_id: str) -> dict[str, float]:
    b = tables()["bills_ap"]
    b = b[b["retreat_id"] == retreat_id]
    return {c: round(float(b.loc[b["category"] == c, "amount"].sum()), 2)
            for c in ("venue", "catering", "travel", "activities", "other")}


def retreats_view(client_id: str | None = None) -> dict:
    t = tables()
    r = t["retreats"].copy()
    if client_id:
        r = r[r["client_id"] == client_id]
    cnames = t["clients"].set_index("client_id")["name"].to_dict()
    rows = []
    for rec in r.to_dict("records"):
        acts = _actuals_by_cat(rec["retreat_id"])
        actual = round(sum(acts.values()), 2)
        budget = round(float(rec["budget_total"]), 2)
        var_pct = round((actual - budget) / budget * 100, 1) if budget else 0.0
        rows.append({
            "retreat_id": rec["retreat_id"], "client_id": rec["client_id"],
            "client_name": cnames.get(rec["client_id"]),
            "destination": rec["destination"], "headcount": int(rec["headcount"]),
            "start_date": _iso(rec["start_date"]), "end_date": _iso(rec["end_date"]),
            "budget_total": budget, "actual_total": actual,
            "variance_abs": round(actual - budget, 2), "variance_pct": var_pct,
            "over_budget": actual > budget * 1.10 and actual > 0,
            "has_actuals": actual > 0,
        })
    rows.sort(key=lambda x: x["variance_pct"], reverse=True)
    return {"count": len(rows), "rows": rows}


def budget_vs_actual(retreat_id: str) -> dict:
    t = tables()
    r = t["retreats"]
    match = r[r["retreat_id"] == retreat_id]
    if match.empty:
        raise KeyError(retreat_id)
    rec = match.iloc[0].to_dict()
    cname = t["clients"].set_index("client_id")["name"].to_dict().get(rec["client_id"])

    prov = t["retreat_budget_provenance"]
    prov = prov[prov["retreat_id"] == retreat_id].set_index("category")
    psrc = t["provenance_sources"].set_index("source_id")
    acts = _actuals_by_cat(retreat_id)

    lines = []
    for cat in ("venue", "catering", "travel", "activities", "other"):
        budget = round(float(rec[f"budget_{cat}"]), 2)
        actual = acts[cat]
        var = round(actual - budget, 2)
        var_pct = round(var / budget * 100, 1) if budget else 0.0
        src = None
        if cat in prov.index:
            pr = prov.loc[cat]
            s = psrc.loc[pr["source_id"]]
            src = {"basis": pr["basis"], "source": s["label"], "url": s["url"],
                   "accessed": _iso(s["accessed"])}
        lines.append({
            "category": cat, "budget": budget, "actual": actual,
            "variance_abs": var, "variance_pct": var_pct,
            "over_10pct": actual > budget * 1.10 and actual > 0,
            "price_source": src,
        })
    tb = round(float(rec["budget_total"]), 2)
    ta = round(sum(acts.values()), 2)
    return {
        "retreat": {
            "retreat_id": retreat_id, "client_id": rec["client_id"], "client_name": cname,
            "destination": rec["destination"], "headcount": int(rec["headcount"]),
            "start_date": _iso(rec["start_date"]), "end_date": _iso(rec["end_date"]),
        },
        "lines": lines,
        "total": {"budget": tb, "actual": ta, "variance_abs": round(ta - tb, 2),
                  "variance_pct": round((ta - tb) / tb * 100, 1) if tb else 0.0,
                  "over_10pct": ta > tb * 1.10 and ta > 0},
    }


# --------------------------------------------------------------------------------------
# benchmarks
# --------------------------------------------------------------------------------------
def benchmarks() -> dict:
    df = tables()["sec_benchmarks"].copy()
    rows = df.to_dict("records")
    dsos = [r["computed_dso"] for r in rows]
    dpos = [r["computed_dpo"] for r in rows]
    return {
        "rows": rows,
        "sec_observed": {"dso": [min(dsos), max(dsos)], "dpo": [min(dpos), max(dpos)]},
        "applied_ranges": {"dso": [45.0, 75.0], "dpo": [20.0, 40.0]},
        "method": "DSO = AR/Revenue*365; DPO = AP/Revenue*365 (revenue proxy — no CostOfRevenue "
                  "tag filed by these issuers). Applied range reasoning: DATA_NOTES §3.",
    }


# --------------------------------------------------------------------------------------
# dashboard summary
# --------------------------------------------------------------------------------------
def dashboard_summary(as_of: str | None = None) -> dict:
    t = tables()
    aod = _as_of(as_of)
    ar = _aging.age_ar(t["invoices_ar"], aod)
    ap = _aging.age_ap(t["bills_ap"], aod)
    res, gt = _run_recon(ReconConfig())
    fc = _cashflow.forecast(t["invoices_ar"], t["bills_ap"], t["bank_transactions"], aod,
                            retreats=t["retreats"])

    overdue_ct = int(((t["invoices_ar"]["status"].isin(["open", "partial"]))
                      & (t["invoices_ar"]["amount"] > 0)
                      & (t["invoices_ar"]["due_date"].map(_aging._as_date) < aod)).sum())

    rv = retreats_view()
    budget_var_total = round(sum(x["variance_abs"] for x in rv["rows"] if x["has_actuals"]), 2)
    over_ct = sum(1 for x in rv["rows"] if x["over_budget"])

    from collections import Counter
    fbt = Counter(f["finding_type"] for f in res.findings)

    return {
        "as_of": str(aod),
        "cards": {
            "ar_outstanding": ar.total_open,
            "ar_open_count": sum(b["count"] for b in ar.buckets),
            "ap_outstanding": ap.total_open,
            "ap_open_count": sum(b["count"] for b in ap.buckets),
            "dso": {"value": ar.metric_value, "benchmark": [45.0, 75.0],
                    "in_benchmark": 45.0 <= ar.metric_value <= 75.0},
            "dpo": {"value": ap.metric_value, "benchmark": [20.0, 40.0],
                    "in_benchmark": 20.0 <= ap.metric_value <= 40.0},
            "cash_position": fc.start_cash,
            "cash_position_week13": fc.weeks[-1]["ending_balance"],
            "overdue_invoice_count": overdue_ct,
            "overdue_amount": ar.overdue_total,
            "budget_variance_total": budget_var_total,
            "retreats_over_budget": over_ct,
        },
        "aging_chart": {"ar": ar.buckets, "ap": ap.buckets},
        "cashflow_chart": fc.weeks,
        "reconciliation": {
            "match_rate_bank": res.stats["match_rate_bank"],
            "ground_truth_recall_pct": gt["recall_pct"],
            "ground_truth_precision_pct": gt["precision_pct"],
            "matched": res.stats["matched"],
            "unmatched_bank": res.stats["unmatched_bank"],
            "unmatched_ledger": res.stats["unmatched_ledger"],
            "findings_by_type": dict(sorted(fbt.items())),
        },
        "shortfall_weeks": fc.shortfall_weeks,
        "provenance": _money_provenance(),
    }


# --------------------------------------------------------------------------------------
# config (for the frontend gear-icon tolerance control)
# --------------------------------------------------------------------------------------
def recon_defaults() -> dict:
    c = ReconConfig()
    f = ForecastConfig()
    return {
        "reconciliation": {
            "defaults": _recon._cfg_public(c),
            "bounds": {
                "amount_tol_pct": [0.0, 10.0], "amount_tol_abs": [0.0, 1000.0],
                "date_window_days": [0, 120], "min_name_score": [0.0, 100.0],
                "accept_score": [0.0, 100.0],
            },
            "tunable": ["amount_tol_pct", "amount_tol_abs", "date_window_days", "min_name_score"],
            "note": "score weights (w_amount/w_date/w_name) are fixed at 0.50/0.20/0.30.",
        },
        "forecast": {"defaults": asdict(f)},
        "data_as_of": data_as_of(),
    }


# --------------------------------------------------------------------------------------
# dashboard insights — heavier derived KPIs (one payload; all from real underlying data)
# --------------------------------------------------------------------------------------
def _overdue_open(df: pd.DataFrame, id_col: str, aod: date) -> pd.DataFrame:
    m = (df["status"].isin(["open", "partial"]) & (df["amount"] > 0)
         & (df["due_date"].map(_aging._as_date) < aod))
    return df[m]


def dashboard_insights(as_of: str | None = None) -> dict:
    t = tables()
    aod = _as_of(as_of)
    inv, bills = t["invoices_ar"], t["bills_ap"]
    cnames = t["clients"].set_index("client_id")["name"].to_dict()
    vnames = t["vendors"].set_index("vendor_id")["name"].to_dict()

    # --- DSO/DPO reconstructed trend ---
    trend = _hist.dso_dpo_trend(inv, bills, end=aod, months=7)

    # --- Collection Effectiveness Index (classic formula, trailing 90d) ---
    cei = _hist.collection_effectiveness_index(inv, end=aod, period_days=90)

    # --- top overdue AR by client / AP by vendor ---
    ar_od = _overdue_open(inv, "invoice_id", aod)
    ar_top = (ar_od.groupby("client_id")["amount"].agg(["sum", "count"])
              .sort_values("sum", ascending=False).head(8).reset_index())
    top_ar = [{"id": r["client_id"], "name": cnames.get(r["client_id"], r["client_id"]),
               "overdue_amount": round(float(r["sum"]), 2), "invoice_count": int(r["count"])}
              for _, r in ar_top.iterrows()]
    ap_od = _overdue_open(bills, "bill_id", aod)
    ap_top = (ap_od.groupby("vendor_id")["amount"].agg(["sum", "count"])
              .sort_values("sum", ascending=False).head(8).reset_index())
    top_ap = [{"id": r["vendor_id"], "name": vnames.get(r["vendor_id"], r["vendor_id"]),
               "overdue_amount": round(float(r["sum"]), 2), "bill_count": int(r["count"])}
              for _, r in ap_top.iterrows()]

    # --- vendor category spend (all AP, real) ---
    cat = (bills.groupby("category")["amount"].agg(["sum", "count"]).reset_index())
    spend = [{"category": r["category"], "amount": round(float(r["sum"]), 2),
              "bill_count": int(r["count"])}
             for _, r in cat.sort_values("sum", ascending=False).iterrows()]

    # --- audit-findings breakdown (exception-aging substitute: date_found is the single
    #     build date, so "days open" is not derivable — severity x type instead) ---
    f = t["audit_findings"]
    from collections import Counter
    by_type = dict(sorted(Counter(f["finding_type"]).items()))
    by_sev = dict(sorted(Counter(f["severity"]).items()))
    grid = (f.groupby(["finding_type", "severity"]).size().reset_index(name="n"))
    fb = {
        "total": int(len(f)),
        "by_type": {k: int(v) for k, v in by_type.items()},
        "by_severity": {k: int(v) for k, v in by_sev.items()},
        "grid": [{"finding_type": r["finding_type"], "severity": r["severity"], "n": int(r["n"])}
                 for _, r in grid.iterrows()],
        "real_vs_injected": {"organically_real": int(len(f) - (f["finding_type"] == "double_payment").sum()),
                             "injected_double_payment": int((f["finding_type"] == "double_payment").sum())},
        "note": "date_found is the single dataset build date, so open-duration is not derivable; "
                "shown as a severity x type breakdown. duplicate + unexplained_txn + stale_90plus "
                "are organically real; double_payment is the 3 injected operational-error cases.",
    }

    return {
        "as_of": str(aod),
        "dso_dpo_trend": trend,
        "cei": cei,
        "top_overdue_ar": top_ar,
        "top_overdue_ap": top_ap,
        "vendor_category_spend": spend,
        "findings_breakdown": fb,
        "provenance": _money_provenance(),
    }


# --------------------------------------------------------------------------------------
# reconciliation match-rate sensitivity to the date window (the Step-6 story, computed live)
# --------------------------------------------------------------------------------------
def recon_sensitivity(windows: tuple[int, ...] = (5, 10, 15, 20, 30, 45)) -> dict:
    pts = []
    for w in windows:
        res, gt = _run_recon(ReconConfig(date_window_days=w))
        pts.append({
            "date_window_days": w,
            "recall_pct": gt["recall_pct"],
            "precision_pct": gt["precision_pct"],
            "matched": res.stats["matched"],
        })
    return {
        "points": pts,
        "default_window": ReconConfig().date_window_days,
        "note": "ground-truth recall/precision vs. the due-date match window, recomputed live. "
                "Measures timing-resolution + exception handling, not amount-fuzzing "
                "(settlement amounts equal ledger amounts by construction — see DATA_NOTES.md).",
    }
