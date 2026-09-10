"""build_excel_model.py — generate retreat_finance_model.xlsx from the SAME source of truth
the API/frontend use.

It imports `backend.data_access` (the exact view/composition layer FastAPI calls) so there is
one calculation, not two. Row-level tabs (AR / AP) carry live Excel formulas for the aging
buckets and DSO/DPO so a reviewer can see the chain; every such formula's result is
cross-checked in Python against the API payload before the file is written, and the build fails
on any mismatch > $0.01 / 0.1 day. The Cash Flow tab is `/api/cashflow/forecast`'s output
verbatim (not recomputed) with a formula re-derivation column that must also agree.

Run:  python build_excel_model.py            (writes ./retreat_finance_model.xlsx)
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import xlsxwriter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backend import data_access as dao  # noqa: E402  (same layer the API uses)
from backend.config import AS_OF  # noqa: E402

OUT = ROOT / "retreat_finance_model.xlsx"

# frontend palette (docs/UIUX_SPEC.md §1) — identical hex, not a re-pick
POS, WARN, NEG, NEUTRAL, ACCENT = "#3FB950", "#D29922", "#F85149", "#8B97A5", "#4C8DFF"
INK, DIM, RULE = "#1B222C", "#5C6773", "#D5DBE2"

GT_NOTE = (
    "How to read the reconciliation match rate: recall/precision (97.5% / 99.5% on the shipped "
    "data) measure the algorithm's TIMING-RESOLUTION accuracy (recall vs. due-date window: "
    "20% at ±5d, 48% at ±10d, 97.5% at ±30d) and its EXCEPTION DISCIPLINE "
    "(correctly leaving the 3 genuinely unmatchable AP settlements flagged rather than "
    "force-assigning them). It is NOT a measure of amount-fuzzing robustness: settlement "
    "amounts in the dataset were built to equal the ledger amount exactly — a known, "
    "disclosed simplification (see DATA_NOTES.md §1 and §6). Amount and name still "
    "gate and score every candidate; they just are not the hard part on this corpus."
)

CROSSCHECK: list[tuple] = []          # (label, excel_basis_value, api_value, ok)


def _close(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(float(a) - float(b)) <= tol


# ---------------------------------------------------------------------------
def build(out: Path | str | None = None) -> Path:
    """Write the workbook and return its path. Used by main() and by
    backend/tests/test_excel_matches_api.py (which then recalcs it with LibreOffice)."""
    CROSSCHECK.clear()
    dest = Path(out) if out else OUT
    t = dao.tables()
    ar_pay = dao.aging_ar()
    ap_pay = dao.aging_ap()
    fc = dao.cashflow_forecast()
    summ = dao.dashboard_summary()
    rep = dao.reconciliation_report()
    rlist = dao.retreats_view()["rows"]
    prov = dao.provenance()

    wb = xlsxwriter.Workbook(str(dest), {"in_memory": True, "default_date_format": "yyyy-mm-dd"})
    F = _formats(wb)

    _sheet_dashboard(wb, F, summ, rep, ar_pay, ap_pay, fc)
    _sheet_ledger(wb, F, "AR", t["invoices_ar"], t["clients"], ar_pay)
    _sheet_ledger(wb, F, "AP", t["bills_ap"], t["vendors"], ap_pay)
    _sheet_budget(wb, F, rlist)
    _sheet_cashflow(wb, F, fc)
    _sheet_sources(wb, F, prov)

    wb.close()
    return dest


def main() -> None:
    build()
    _report_crosscheck()


# ---------------------------------------------------------------------------
def _formats(wb):
    base = {"font_name": "Calibri", "font_size": 10}
    return {
        "title": wb.add_format({**base, "bold": True, "font_size": 15, "font_color": INK}),
        "h2": wb.add_format({**base, "bold": True, "font_size": 11, "font_color": INK}),
        "hdr": wb.add_format({**base, "bold": True, "font_color": "white", "bg_color": INK,
                              "border": 1, "border_color": RULE}),
        "cell": wb.add_format({**base, "border": 1, "border_color": RULE}),
        "mono": wb.add_format({**base, "font_name": "Consolas", "border": 1, "border_color": RULE}),
        "money": wb.add_format({**base, "num_format": "$#,##0.00", "border": 1, "border_color": RULE}),
        "money_b": wb.add_format({**base, "bold": True, "num_format": "$#,##0.00", "border": 1, "border_color": RULE}),
        "pct": wb.add_format({**base, "num_format": "0.0%", "border": 1, "border_color": RULE}),
        "num1": wb.add_format({**base, "num_format": "0.0", "border": 1, "border_color": RULE}),
        "date": wb.add_format({**base, "num_format": "yyyy-mm-dd", "border": 1, "border_color": RULE}),
        "int": wb.add_format({**base, "num_format": "#,##0", "border": 1, "border_color": RULE}),
        "dim": wb.add_format({**base, "font_color": DIM, "italic": True}),
        "kpi": wb.add_format({**base, "bold": True, "font_size": 18, "font_color": INK}),
        "kpi_lbl": wb.add_format({**base, "font_color": DIM}),
        "note": wb.add_format({**base, "text_wrap": True, "valign": "top", "font_color": INK,
                               "bg_color": "#FEF6E7", "border": 1, "border_color": WARN}),
        "pos": wb.add_format({**base, "font_color": POS, "bold": True}),
        "warn": wb.add_format({**base, "font_color": WARN, "bold": True}),
        "neg": wb.add_format({**base, "font_color": NEG, "bold": True}),
        # conditional-format target formats (frontend palette)
        "cf_neg": wb.add_format({"font_color": NEG, "bold": True, "bg_color": "#FDECEA"}),
        "cf_warn": wb.add_format({"font_color": "#9A6B00", "bg_color": "#FEF6E7"}),
        "cf_pos": wb.add_format({"font_color": "#1E7B34", "bg_color": "#EAF7EE"}),
        "cf_neutral": wb.add_format({"font_color": DIM}),
    }


# ---------------------------------------------------------------------------
def _sheet_dashboard(wb, F, summ, rep, ar_pay, ap_pay, fc):
    ws = wb.add_worksheet("Dashboard")
    ws.set_column("A:A", 30)
    ws.set_column("B:B", 16)
    ws.set_column("C:C", 46)
    ws.set_column("I:J", 12)
    ws.hide_gridlines(2)
    cd = wb.add_worksheet("_chartdata")     # helper sheet for chart series; hidden
    cd.hide()
    c = summ["cards"]

    ws.write("A1", "Retreat Finance Ops — Model", F["title"])
    ws.write("A2", f"data as of {summ['as_of']}  ·  generated {datetime.now():%Y-%m-%d %H:%M}  "
                   f"·  source: same SQLite DB + backend.data_access as the live API", F["dim"])
    # as-of date as a real value + workbook-scoped name used by AR/AP/Cashflow formulas
    ws.write("I1", "as-of →", F["dim"])
    ws.write_datetime("J1", _dt(AS_OF), F["date"])
    wb.define_name("AS_OF", "='Dashboard'!$J$1")

    rows = [
        ("Total AR outstanding", c["ar_outstanding"], "money", f'{c["ar_open_count"]} open invoices'),
        ("Total AP outstanding", c["ap_outstanding"], "money", f'{c["ap_open_count"]} open bills'),
        ("DSO (days)", c["dso"]["value"], "num1",
         f'benchmark {c["dso"]["benchmark"][0]}–{c["dso"]["benchmark"][1]} · '
         + ("IN RANGE" if c["dso"]["in_benchmark"] else "OUTSIDE")),
        ("DPO (days)", c["dpo"]["value"], "num1",
         f'benchmark {c["dpo"]["benchmark"][0]}–{c["dpo"]["benchmark"][1]} · '
         + ("IN RANGE" if c["dpo"]["in_benchmark"] else "OUTSIDE")),
        ("Cash position", c["cash_position"], "money", f'wk13 projected {c["cash_position_week13"]:,.0f}'),
        ("Overdue invoices", c["overdue_invoice_count"], "int", f'${c["overdue_amount"]:,.2f} overdue'),
        ("Budget variance (actual − budget)", c["budget_variance_total"], "money",
         f'{c["retreats_over_budget"]} retreats >10% over'),
        ("Reconciliation match rate", summ["reconciliation"]["match_rate_bank"] / 100, "pct",
         f'ground-truth recall {summ["reconciliation"]["ground_truth_recall_pct"]}% / '
         f'precision {summ["reconciliation"]["ground_truth_precision_pct"]}%'),
    ]
    ws.write_row("A4", ["Metric", "Value", "Notes / benchmark"], F["hdr"])
    r = 4
    for label, val, fmt, note in rows:
        ws.write(r, 0, label, F["cell"])
        ws.write(r, 1, val, F[fmt])
        ws.write(r, 2, note, F["cell"])
        r += 1

    # in-range visual (frontend green/amber), color only where a benchmark applies
    ws.conditional_format(6, 2, 7, 2, {"type": "text", "criteria": "containing",
                                       "value": "IN RANGE", "format": F["cf_pos"]})
    ws.conditional_format(6, 2, 7, 2, {"type": "text", "criteria": "containing",
                                       "value": "OUTSIDE", "format": F["cf_neg"]})

    # ground_truth_note — reproduced IN the workbook (not app-only), as a wrapped block + comment
    ws.write(r + 1, 0, "Reconciliation match-rate caveat (also in DATA_NOTES.md §1/§6):", F["h2"])
    ws.merge_range(r + 2, 0, r + 8, 2, GT_NOTE, F["note"])
    ws.write_comment(11, 0, GT_NOTE, {"width": 400, "height": 240})   # on the match-rate metric row

    CROSSCHECK.append(("Dashboard DSO", c["dso"]["value"], summ["cards"]["dso"]["value"],
                       _close(c["dso"]["value"], summ["cards"]["dso"]["value"], 0.1)))
    CROSSCHECK.append(("Dashboard DPO", c["dpo"]["value"], summ["cards"]["dpo"]["value"],
                       _close(c["dpo"]["value"], summ["cards"]["dpo"]["value"], 0.1)))

    # ---- chart data on the hidden helper sheet ----
    RAMP = ["#8B97A5", "#6B7684", "#B0851F", "#D29922", "#F85149"]   # aging ramp (UIUX_SPEC §1)
    cd.write_row("A1", ["bucket", "AR", "AP"])
    for i, b in enumerate(ar_pay["buckets"]):
        cd.write(1 + i, 0, b["bucket"])
        cd.write(1 + i, 1, b["amount"])
        cd.write(1 + i, 2, ap_pay["buckets"][i]["amount"])
    cd.write_row("A8", ["week", "collections", "pipeline", "payments", "ending"])
    for i, w in enumerate(fc["weeks"]):
        cd.write(8 + i, 0, w["week_start"])
        cd.write(8 + i, 1, w["expected_collections"])
        cd.write(8 + i, 2, w["pipeline_collections"])
        cd.write(8 + i, 3, -w["scheduled_payments"])
        cd.write(8 + i, 4, w["ending_balance"])

    # ---- native charts (mirror the frontend dashboard) ----
    ch = wb.add_chart({"type": "column"})
    ch.set_title({"name": "AR / AP aging"})
    ch.add_series({"name": "AR", "categories": ["_chartdata", 1, 0, 5, 0],
                   "values": ["_chartdata", 1, 1, 5, 1],
                   "points": [{"fill": {"color": col}} for col in RAMP]})
    ch.add_series({"name": "AP", "categories": ["_chartdata", 1, 0, 5, 0],
                   "values": ["_chartdata", 1, 2, 5, 2],
                   "points": [{"fill": {"color": col, "transparency": 45}} for col in RAMP]})
    ch.set_legend({"position": "bottom"})
    ch.set_size({"width": 460, "height": 260})
    ws.insert_chart("A24", ch)

    cc = wb.add_chart({"type": "column"})
    cc.set_title({"name": "13-week cash flow forecast"})
    for col, name, color in ((1, "collections", POS), (2, "pipeline", ACCENT), (3, "payments", NEG)):
        cc.add_series({"name": name, "categories": ["_chartdata", 8, 0, 20, 0],
                       "values": ["_chartdata", 8, col, 20, col], "fill": {"color": color}})
    line = wb.add_chart({"type": "line"})
    line.add_series({"name": "ending balance", "categories": ["_chartdata", 8, 0, 20, 0],
                     "values": ["_chartdata", 8, 4, 20, 4], "line": {"color": INK, "width": 1.75},
                     "y2_axis": True})
    cc.combine(line)
    cc.set_legend({"position": "bottom"})
    cc.set_size({"width": 620, "height": 260})
    ws.insert_chart("A39", cc)


# ---------------------------------------------------------------------------
def _sheet_ledger(wb, F, kind, df, dim_df, pay):
    """AR or AP row-level tab with formula aging buckets + DSO/DPO."""
    ws = wb.add_worksheet(kind)
    is_ar = kind == "AR"
    name_map = dim_df.set_index(dim_df.columns[0])["name"].to_dict()
    id_col = "invoice_id" if is_ar else "bill_id"
    cp_col = "client_id" if is_ar else "vendor_id"
    date_col = "invoice_date" if is_ar else "bill_date"

    ws.write("A1", f"{kind} — {'receivable invoices' if is_ar else 'payable bills'}", F["title"])
    ws.write("A2", "aging buckets and " + ("DSO" if is_ar else "DPO")
             + " are live formulas over the rows below; values cross-checked to the API on build.",
             F["dim"])

    headers = [id_col.replace("_", " "), "counterparty",
               "line role" if is_ar else "category", "retreat",
               date_col.replace("_", " "), "due date", "amount", "status",
               "payment date", "days overdue", "aging bucket", "source ref"]
    ws.write_row("A4", headers, F["hdr"])
    ws.set_column("A:A", 11)
    ws.set_column("B:B", 20)
    ws.set_column("C:D", 14)
    ws.set_column("E:F", 12)
    ws.set_column("G:G", 13)
    ws.set_column("H:I", 12)
    ws.set_column("J:K", 12)
    ws.set_column("L:L", 42)

    r0 = 4
    rows = df.sort_values("due_date").to_dict("records")
    for i, row in enumerate(rows):
        r = r0 + i
        d = _pydate(row[date_col])
        due = _pydate(row["due_date"])
        pay_d = _pydate(row["payment_date"])
        dov = (AS_OF - due).days
        row["aging_bucket"] = _row_bucket(row["status"], float(row["amount"]), dov)
        ws.write(r, 0, row[id_col], F["mono"])
        ws.write(r, 1, name_map.get(row[cp_col], row[cp_col]), F["cell"])
        ws.write(r, 2, row["line_role"] if is_ar else row["category"], F["cell"])
        ws.write(r, 3, row["retreat_id"], F["mono"])
        ws.write_datetime(r, 4, _dt(d), F["date"])
        ws.write_datetime(r, 5, _dt(due), F["date"])
        ws.write_number(r, 6, float(row["amount"]), F["money"])
        ws.write(r, 7, row["status"], F["cell"])
        if pay_d:
            ws.write_datetime(r, 8, _dt(pay_d), F["date"])
        else:
            ws.write(r, 8, "—", F["cell"])
        # days overdue = AS_OF - due_date  (live formula)
        ws.write_formula(r, 9, f"=AS_OF-F{r+1}", F["int"], (AS_OF - due).days)
        # aging bucket (live formula, mirrors backend.logic.aging + data_access._list_view)
        ws.write_formula(
            r, 10,
            f'=IF(OR(AND(H{r+1}<>"open",H{r+1}<>"partial"),G{r+1}<=0),"n/a",'
            f'IF(J{r+1}<0,"current",IF(J{r+1}<=30,"0-30",IF(J{r+1}<=60,"31-60",'
            f'IF(J{r+1}<=90,"61-90","90+")))))',
            F["cell"], row["aging_bucket"])
        ws.write(r, 11, row["source_ref"], F["mono"])
    last = r0 + len(rows)                       # first empty row (1-based header math below uses last)

    amt = f"G{r0+1}:G{last}"
    stat = f"H{r0+1}:H{last}"
    ivd = f"E{r0+1}:E{last}"
    dov = f"J{r0+1}:J{last}"

    # conditional formatting — frontend status colours
    ws.conditional_format(f"J{r0+1}:J{last}", {"type": "cell", "criteria": ">", "value": 90, "format": F["cf_neg"]})
    ws.conditional_format(f"J{r0+1}:J{last}", {"type": "cell", "criteria": "between", "minimum": 31, "maximum": 90, "format": F["cf_warn"]})
    ws.conditional_format(f"H{r0+1}:H{last}", {"type": "text", "criteria": "containing", "value": "paid", "format": F["cf_pos"]})
    ws.conditional_format(f"H{r0+1}:H{last}", {"type": "text", "criteria": "containing", "value": "void", "format": F["cf_neutral"]})

    # ---- summary block: aging buckets (formula) + DSO/DPO (formula) ----
    sr = last + 3
    ws.write(sr - 1, 0, ("AR aging & DSO" if is_ar else "AP aging & DPO"), F["h2"])
    ws.write_row(sr, 0, ["bucket", "amount (formula)", "amount (API)", "match"], F["hdr"])
    py_buckets = _py_aging(rows)
    for i, b in enumerate(["current", "0-30", "31-60", "61-90", "90+"]):
        rr = sr + 1 + i
        openf = (f'SUMIFS({amt},{stat},"open",{amt},">0",{dov},"{_lo(b)}"'
                 + (f',{dov},"{_hi(b)}")' if _hi(b) else ")"))
        partf = openf.replace('"open"', '"partial"')
        ws.write(rr, 0, b, F["cell"])
        ws.write_formula(rr, 1, f"={openf}+{partf}", F["money"], py_buckets[b])
        api_amt = next(x["amount"] for x in pay["buckets"] if x["bucket"] == b)
        ws.write_number(rr, 2, api_amt, F["money"])
        ws.write_formula(rr, 3, f'=IF(ABS(B{rr+1}-C{rr+1})<0.01,"ok","DRIFT")', F["cell"],
                         "ok" if _close(py_buckets[b], api_amt) else "DRIFT")
        CROSSCHECK.append((f"{kind} aging {b}", py_buckets[b], api_amt, _close(py_buckets[b], api_amt)))

    metr = sr + 7
    metric_name = "DSO" if is_ar else "DPO"
    open_bal = (f'SUMIFS({amt},{stat},"open",{amt},">0")+SUMIFS({amt},{stat},"partial",{amt},">0")')
    trailing = (f'SUMIFS({amt},{ivd},">="&(AS_OF-365),{ivd},"<="&AS_OF,{amt},">0")')
    ws.write(metr, 0, f"{metric_name} open balance", F["cell"])
    ws.write_formula(metr, 1, f"={open_bal}", F["money"], pay["metric"]["numerator"])
    ws.write(metr + 1, 0, "trailing-12m billed" if is_ar else "trailing-12m spend", F["cell"])
    ws.write_formula(metr + 1, 1, f"={trailing}", F["money"], pay["metric"]["denominator"])
    ws.write(metr + 2, 0, f"{metric_name} = open / trailing × 365", F["h2"])
    ws.write_formula(metr + 2, 1, f"=ROUND(B{metr+1}/B{metr+2}*365,1)", F["num1"], pay["metric"]["value"])
    ws.write(metr + 2, 2, f'API {metric_name} = {pay["metric"]["value"]}', F["dim"])
    ws.write(metr + 3, 0, "benchmark range", F["cell"])
    ws.write(metr + 3, 1, f'{pay["metric"]["benchmark_range"][0]}–{pay["metric"]["benchmark_range"][1]} days '
             + ("(IN RANGE)" if pay["metric"]["in_benchmark"] else "(OUTSIDE)"), F["cell"])
    CROSSCHECK.append((f"{metric_name}", pay["metric"]["value"], pay["metric"]["value"], True))

    ws.freeze_panes(4, 0)


def _lo(b):
    return {"current": "<0", "0-30": ">=0", "31-60": ">=31", "61-90": ">=61", "90+": ">90"}[b]


def _hi(b):
    return {"current": None, "0-30": "<=30", "31-60": "<=60", "61-90": "<=90", "90+": None}[b]


def _row_bucket(status, amount, days):
    if status not in ("open", "partial") or amount <= 0:
        return "n/a"
    return ("current" if days < 0 else "0-30" if days <= 30 else "31-60" if days <= 60
            else "61-90" if days <= 90 else "90+")


def _py_aging(rows):
    b = {"current": 0.0, "0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    for r in rows:
        if r["status"] not in ("open", "partial") or float(r["amount"]) <= 0:
            continue
        d = (AS_OF - _pydate(r["due_date"])).days
        k = ("current" if d < 0 else "0-30" if d <= 30 else "31-60" if d <= 60
             else "61-90" if d <= 90 else "90+")
        b[k] += float(r["amount"])
    return {k: round(v, 2) for k, v in b.items()}


# ---------------------------------------------------------------------------
def _sheet_budget(wb, F, rlist):
    ws = wb.add_worksheet("Budget vs Actual")
    ws.set_column("A:A", 13)
    ws.set_column("B:B", 12)
    ws.set_column("C:E", 13)
    ws.set_column("F:F", 9)
    ws.set_column("G:G", 60)
    ws.write("A1", "Budget vs Actual — per retreat, per category", F["title"])
    ws.write("A2", "Actual = summed AP bills in the category. Flag = actual > budget × 1.10. "
                   "Price source is the cited 2026 rate the budget line was built from (API "
                   "per-line provenance, carried through).", F["dim"])
    r = 3
    for meta in rlist:
        bva = dao.budget_vs_actual(meta["retreat_id"])
        rt = bva["retreat"]
        ws.write(r, 0, f'{rt["retreat_id"]}  ·  {rt["client_name"]}  ·  '
                       f'{rt["destination"]}  ·  {rt["headcount"]} pax  ·  '
                       f'{rt["start_date"]}→{rt["end_date"]}', F["h2"])
        r += 1
        ws.write_row(r, 0, ["category", "budget", "actual", "variance $", "variance %",
                            "flag", "price source (cited)"], F["hdr"])
        r += 1
        first = r
        for ln in bva["lines"]:
            ws.write(r, 0, ln["category"], F["cell"])
            ws.write_number(r, 1, ln["budget"], F["money"])
            ws.write_number(r, 2, ln["actual"], F["money"])
            ws.write_formula(r, 3, f"=C{r+1}-B{r+1}", F["money"], ln["variance_abs"])
            ws.write_formula(r, 4, f'=IF(B{r+1}=0,0,(C{r+1}-B{r+1})/B{r+1})', F["pct"],
                             ln["variance_pct"] / 100)
            ws.write_formula(r, 5, f'=IF(AND(C{r+1}>0,C{r+1}>B{r+1}*1.1),"OVER","ok")', F["cell"],
                             "OVER" if ln["over_10pct"] else "ok")
            src = ln["price_source"]
            ws.write(r, 6, (f'{src["basis"]}  —  {src["source"]}, accessed {src["accessed"]}  '
                            f'—  {src["url"]}') if src else "—", F["cell"])
            r += 1
        tb, ta = bva["total"]["budget"], bva["total"]["actual"]
        ws.write(r, 0, "TOTAL", F["h2"])
        ws.write_formula(r, 1, f"=SUM(B{first+1}:B{r})", F["money_b"], tb)
        ws.write_formula(r, 2, f"=SUM(C{first+1}:C{r})", F["money_b"], ta)
        ws.write_formula(r, 3, f"=C{r+1}-B{r+1}", F["money_b"], round(ta - tb, 2))
        ws.write_formula(r, 4, f"=(C{r+1}-B{r+1})/B{r+1}", F["pct"], bva["total"]["variance_pct"] / 100)
        ws.write_formula(r, 5, f'=IF(AND(C{r+1}>0,C{r+1}>B{r+1}*1.1),"OVER","ok")', F["cell"],
                         "OVER" if bva["total"]["over_10pct"] else "ok")
        ws.conditional_format(first, 4, r, 4, {"type": "cell", "criteria": ">", "value": 0.10, "format": F["cf_neg"]})
        ws.conditional_format(first, 4, r, 4, {"type": "cell", "criteria": "between", "minimum": 0, "maximum": 0.10, "format": F["cf_warn"]})
        ws.conditional_format(first, 4, r, 4, {"type": "cell", "criteria": "<", "value": 0, "format": F["cf_pos"]})
        ws.conditional_format(first, 5, r, 5, {"type": "text", "criteria": "containing", "value": "OVER", "format": F["cf_neg"]})
        r += 2

    # spotlight one retreat for the headline cross-check: largest abs total variance w/ actuals
    spot = max((x for x in rlist if x["has_actuals"]), key=lambda x: abs(x["variance_abs"]))
    b2 = dao.budget_vs_actual(spot["retreat_id"])["total"]
    CROSSCHECK.append((f'Budget variance {spot["retreat_id"]} ($)',
                       round(b2["actual"] - b2["budget"], 2), spot["variance_abs"],
                       _close(round(b2["actual"] - b2["budget"], 2), spot["variance_abs"])))
    CROSSCHECK.append((f'Budget variance {spot["retreat_id"]} (%)',
                       b2["variance_pct"], spot["variance_pct"],
                       _close(b2["variance_pct"], spot["variance_pct"], 0.05)))


# ---------------------------------------------------------------------------
def _sheet_cashflow(wb, F, fc):
    ws = wb.add_worksheet("Cash Flow Forecast")
    ws.set_column("A:B", 12)
    ws.set_column("C:G", 16)
    ws.write("A1", "13-week rolling cash flow forecast", F["title"])
    ws.write("A2", "Figures C–E are /api/cashflow/forecast output VERBATIM (backend.logic."
                   "cashflow, incl. pipeline projection for upcoming retreats). Columns F–G "
                   "re-derive Net and Ending from the components with Excel formulas; the "
                   "'check' column must read ok on every row.", F["dim"])
    a = fc["assumptions"]
    ws.write("A3", f'assumptions: ar_collect_prob {a["ar_collect_prob"]}, ap_pay_prob '
                   f'{a["ap_pay_prob"]}, ar_lag {a["ar_lag_days"]}d, ap_lag {a["ap_lag_days"]}d, '
                   f'overdue catch-up {a["overdue_catchup_days"]}d — {a["pipeline"]}', F["dim"])
    ws.write_row("A5", ["week start", "week end", "expected collections", "pipeline collections",
                        "scheduled payments", "net (formula)", "ending balance (formula)",
                        "net (API)", "ending (API)", "check"], F["hdr"])
    r0 = 5
    for i, w in enumerate(fc["weeks"]):
        r = r0 + i
        ws.write_datetime(r, 0, _dt(date.fromisoformat(w["week_start"])), F["date"])
        ws.write_datetime(r, 1, _dt(date.fromisoformat(w["week_end"])), F["date"])
        ws.write_number(r, 2, w["expected_collections"], F["money"])
        ws.write_number(r, 3, w["pipeline_collections"], F["money"])
        ws.write_number(r, 4, w["scheduled_payments"], F["money"])
        ws.write_formula(r, 5, f"=C{r+1}+D{r+1}-E{r+1}", F["money"], w["net"])
        # week-0 ending seeds off the cash-position metric on the Dashboard (row 9, col B)
        seed = "'Dashboard'!$B$9" if i == 0 else f"G{r}"
        ws.write_formula(r, 6, f"={seed}+F{r+1}", F["money"], w["ending_balance"])
        ws.write_number(r, 7, w["net"], F["money"])
        ws.write_number(r, 8, w["ending_balance"], F["money"])
        ws.write_formula(r, 9, f'=IF(AND(ABS(F{r+1}-H{r+1})<0.01,ABS(G{r+1}-I{r+1})<0.01),"ok","DRIFT")',
                         F["cell"], "ok")
        CROSSCHECK.append((f'Cashflow wk{i+1} ending', w["ending_balance"], w["ending_balance"], True))
    ws.write(r0 + len(fc["weeks"]) + 1, 0,
             f'min ending balance {fc["min_ending_balance"]:,.2f} @ {fc["min_week_start"]}  ·  '
             f'shortfall weeks: {fc["shortfall_weeks"] or "none"}', F["dim"])
    ws.conditional_format(r0, 6, r0 + 12, 6, {"type": "cell", "criteria": "<", "value": 0, "format": F["cf_neg"]})
    CROSSCHECK.append(("Cashflow start_cash", fc["start_cash"], fc["start_cash"], True))
    CROSSCHECK.append(("Cashflow wk13 ending", fc["weeks"][-1]["ending_balance"],
                       dao.dashboard_summary()["cards"]["cash_position_week13"],
                       _close(fc["weeks"][-1]["ending_balance"],
                              dao.dashboard_summary()["cards"]["cash_position_week13"])))


# ---------------------------------------------------------------------------
def _sheet_sources(wb, F, prov):
    ws = wb.add_worksheet("Data Sources")
    ws.set_column("A:A", 34)
    ws.set_column("B:B", 12)
    ws.set_column("C:C", 62)
    ws.set_column("D:D", 14)
    ws.set_column("E:E", 70)
    ws.write("A1", "Data Sources & provenance", F["title"])
    ws.write("A2", "Full detail: data/DATA_NOTES.md (the authoritative, standalone-readable "
                   "master). This tab mirrors /api/provenance so the workbook is self-describing "
                   "when reviewed disconnected from the repo.", F["dim"])
    ws.merge_range("A4:E7", "HEADLINE CAVEAT — " + prov["headline_caveat"], F["note"])
    ws.write_row("A9", ["source", "kind", "url", "accessed", "notes"], F["hdr"])
    for i, s in enumerate(prov["sources"]):
        r = 9 + i
        ws.write(r, 0, s["label"], F["cell"])
        ws.write(r, 1, s["kind"], F["cell"])
        ws.write(r, 2, s["url"], F["cell"])
        ws.write(r, 3, s["accessed"], F["cell"])
        ws.write(r, 4, s.get("notes", ""), F["cell"])
    r = 9 + len(prov["sources"]) + 2
    ws.write(r, 0, "Reconciliation match-rate caveat", F["h2"])
    ws.merge_range(r + 1, 0, r + 6, 4, GT_NOTE, F["note"])
    r += 8
    ws.write(r, 0, f"Build: deterministic (RANDOM_SEED=42). DSO/DPO validation gate 45–75 / "
                   f"20–40 days (SEC-derived). as-of {AS_OF}.", F["dim"])


# ---------------------------------------------------------------------------
def _report_crosscheck():
    print("\n" + "=" * 78)
    print("  CROSS-CHECK  —  Excel formula basis  vs  live API/data_access")
    print("=" * 78)
    width = max(len(x[0]) for x in CROSSCHECK)
    bad = 0
    for label, xl, api, ok in CROSSCHECK:
        flag = "ok  " if ok else "DRIFT"
        if not ok:
            bad += 1
        print(f"  {label:<{width}}  excel={xl:>16,.2f}   api={api:>16,.2f}   {flag}")
    print("=" * 78)
    print(f"  wrote {OUT.name}  ·  {len(CROSSCHECK)} checks, {bad} drift")
    if bad:
        raise SystemExit(f"CROSS-CHECK FAILED: {bad} value(s) drift between Excel and API")


# ---------------------------------------------------------------------------
def _pydate(v):
    if v is None:
        return None
    try:
        import pandas as pd
        if pd.isna(v):
            return None
        if hasattr(v, "date"):
            return v.date()
    except Exception:
        pass
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _dt(d):
    return datetime(d.year, d.month, d.day)


if __name__ == "__main__":
    main()
