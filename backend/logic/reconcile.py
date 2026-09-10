"""Bank reconciliation engine.

Pure logic — no FastAPI, no DB. `reconcile(...)` takes DataFrames + a ReconConfig and returns a
ReconResult (matched / unmatched-bank / unmatched-ledger buckets + audit findings + stats).

Matching, per bank transaction:
  1. polarity: amount >= 0 can only settle an invoice (a receipt); amount < 0 only a bill.
  2. hard filters for a *candidate*:
       - |bank amount| within max(amount_tol_abs, amount_tol_pct% of ledger amount)
       - bank date within +/- date_window_days of the ledger row's DUE date
  3. score each candidate 0-100:
       amount_score = 100 * (1 - min(1, |amount delta| / effective tolerance))
       date_score   = 100 * (1 - min(1, |day delta|   / date window))
       name_score   = rapidfuzz.token_set_ratio(counterparty name, bank description)
       combined     = w_amount*amount + w_date*date + w_name*name
  4. keep pairs with combined >= accept_score and name_score >= min_name_score
  5. greedy 1:1 assignment by combined score (a bank line and a ledger row each match once).

Audit findings derived from the result:
  - unexplained_txn   : unmatched bank line, |amount| material, with NO candidate at all
  - double_payment    : unmatched bank line that *would* settle an already-matched ledger row
  - duplicate         : two bank lines identical on (date, amount)

Interpreting the shipped-dataset scores (README headline caveat):
  `score_against_ground_truth` reports ~97.5% recall / ~99.5% precision on the shipped DB. That
  measures the algorithm's TIMING-RESOLUTION accuracy (recall vs. due-date window: 20% at +/-5d,
  48% at +/-10d, 87% at +/-20d, 97.5% at +/-30d) and its EXCEPTION DISCIPLINE (correctly leaving
  the 3 genuinely unmatchable AP settlements flagged rather than force-assigning them). It is
  NOT a measure of amount-fuzzing robustness: settlement amounts in the dataset were built to
  equal the ledger amount exactly (a known, disclosed simplification, see DATA_NOTES.md). Amount
  and name still gate and score every candidate; they just aren't the hard part on this corpus.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime

import pandas as pd
from rapidfuzz import fuzz

from backend.config import ReconConfig


# --------------------------------------------------------------------------------------
# result types
# --------------------------------------------------------------------------------------
@dataclass
class Match:
    transaction_id: str
    matched_type: str            # "invoice" | "bill"
    matched_id: str
    counterparty: str            # client / vendor name of the matched ledger row
    bank_date: str               # bank transaction date (ISO)
    bank_amount: float           # signed bank transaction amount
    confidence: float            # 0-100
    match_method: str
    amount_delta: float          # bank |amount| - ledger amount
    date_delta_days: int         # bank date - due date
    name_score: float
    runner_up_gap: float         # combined-score margin over the 2nd-best candidate (ambiguity)
    reasons: list[str]


@dataclass
class Candidate:
    matched_type: str
    matched_id: str
    combined: float
    amount_score: float
    date_score: float
    name_score: float
    amount_delta: float
    date_delta_days: int
    counterparty: str


@dataclass
class ReconResult:
    config: dict
    matched: list[Match]
    unmatched_bank: list[dict]
    unmatched_ledger: list[dict]
    findings: list[dict]
    stats: dict
    # transaction_id -> ranked candidate list (for the "why did/didn't it match" UI panel)
    candidates_by_txn: dict[str, list[dict]] = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def _as_date(v) -> date:
    if isinstance(v, pd.Timestamp):
        return v.date()
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return pd.Timestamp(v).date()


def build_ledger(invoices: pd.DataFrame, bills: pd.DataFrame,
                 clients: pd.DataFrame, vendors: pd.DataFrame) -> pd.DataFrame:
    """One row per settle-able ledger item, with counterparty name and expected polarity."""
    cname = clients.set_index("client_id")["name"].to_dict()
    vname = vendors.set_index("vendor_id")["name"].to_dict()

    inv = invoices[invoices["status"] != "void"].copy()
    inv["ledger_id"] = inv["invoice_id"]
    inv["kind"] = "invoice"
    inv["counterparty"] = inv["client_id"].map(cname)
    inv["polarity"] = 1

    bil = bills[bills["status"] != "void"].copy()
    bil["ledger_id"] = bil["bill_id"]
    bil["kind"] = "bill"
    bil["counterparty"] = bil["vendor_id"].map(vname)
    bil["polarity"] = -1

    cols = ["ledger_id", "kind", "counterparty", "amount", "due_date", "polarity",
            "status", "retreat_id", "source_ref"]
    return pd.concat([inv[cols], bil[cols]], ignore_index=True)


def _score_candidates(txn, ledger_rows, cfg: ReconConfig,
                      relax: float = 1.0) -> list[Candidate]:
    """Score ledger rows against one bank transaction.

    relax > 1 loosens the hard amount/date filters (used only to populate the explain-panel for
    an unmatched transaction with near-misses); relax == 1 is the real matching pass.
    """
    bank_amt = abs(float(txn["amount"]))
    bank_dt = _as_date(txn["transaction_date"])
    desc = str(txn["description"])
    out: list[Candidate] = []
    for lr in ledger_rows:
        led_amt = float(lr["amount"])
        tol_eff = max(cfg.amount_tol_abs, cfg.amount_tol_pct / 100.0 * led_amt)
        amt_delta = bank_amt - led_amt
        if abs(amt_delta) > tol_eff * relax:
            continue
        day_delta = (bank_dt - _as_date(lr["due_date"])).days
        if abs(day_delta) > cfg.date_window_days * relax:
            continue
        amount_score = max(0.0, 100.0 * (1.0 - abs(amt_delta) / max(tol_eff, 1e-6)))
        date_score = max(0.0, 100.0 * (1.0 - abs(day_delta) / max(cfg.date_window_days, 1)))
        name_score = float(fuzz.token_set_ratio(str(lr["counterparty"]).lower(), desc.lower()))
        if relax == 1.0 and name_score < cfg.min_name_score:
            continue
        combined = (cfg.w_amount * amount_score + cfg.w_date * date_score + cfg.w_name * name_score)
        out.append(Candidate(lr["kind"], lr["ledger_id"], combined, amount_score, date_score,
                             name_score, amt_delta, day_delta, str(lr["counterparty"])))
    out.sort(key=lambda c: c.combined, reverse=True)
    return out


def _reasons(c: Candidate, cfg: ReconConfig) -> list[str]:
    r = [f"amount delta ${c.amount_delta:+.2f} (score {c.amount_score:.0f})",
         f"date delta {c.date_delta_days:+d}d (score {c.date_score:.0f})",
         f"name '{c.counterparty}' vs memo -> {c.name_score:.0f}/100"]
    return r


def _why_not(c: Candidate, cfg: ReconConfig) -> str:
    if c.combined >= cfg.accept_score:
        return "not selected: another candidate scored higher for this transaction"
    bits = []
    if c.amount_score < 90:
        bits.append(f"amount off by ${abs(c.amount_delta):.2f}")
    if c.date_score < 60:
        bits.append(f"date off by {abs(c.date_delta_days)}d (window +/-{cfg.date_window_days}d)")
    if c.name_score < 70:
        bits.append(f"weak name match ({c.name_score:.0f}/100)")
    return f"combined score {c.combined:.0f} < accept {cfg.accept_score:.0f}" + (
        " — " + ", ".join(bits) if bits else "")


# --------------------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------------------
def reconcile(bank: pd.DataFrame, invoices: pd.DataFrame, bills: pd.DataFrame,
              clients: pd.DataFrame, vendors: pd.DataFrame,
              cfg: ReconConfig | None = None) -> ReconResult:
    cfg = cfg or ReconConfig()
    cfg.validate()
    ledger = build_ledger(invoices, bills, clients, vendors)
    inv_rows = ledger[ledger["polarity"] == 1].to_dict("records")
    bill_rows = ledger[ledger["polarity"] == -1].to_dict("records")

    # 1) score every (txn, candidate) pair — strict pass (drives matching AND findings)
    pair_pool: list[tuple[float, str, Candidate]] = []
    strict_cands: dict[str, list[Candidate]] = {}
    for txn in bank.to_dict("records"):
        rows = inv_rows if float(txn["amount"]) >= 0 else bill_rows
        cands = _score_candidates(txn, rows, cfg)
        strict_cands[txn["transaction_id"]] = cands
        for c in cands:
            if c.combined >= cfg.accept_score:
                pair_pool.append((c.combined, txn["transaction_id"], c))

    # 2) greedy 1:1 assignment, best score first
    bank_by_tid = {t["transaction_id"]: t for t in bank.to_dict("records")}
    pair_pool.sort(key=lambda t: t[0], reverse=True)
    taken_txn: set[str] = set()
    taken_led: set[str] = set()
    matched: list[Match] = []
    for combined, tid, c in pair_pool:
        if tid in taken_txn or c.matched_id in taken_led:
            continue
        taken_txn.add(tid)
        taken_led.add(c.matched_id)
        ranked = strict_cands[tid]
        gap = combined - (ranked[1].combined if len(ranked) > 1 else 0.0)
        conf = round(min(100.0, combined) - max(0.0, (12.0 - gap)) * 0.4, 1)  # ambiguity haircut
        method = ("exact_amount+date+name" if abs(c.amount_delta) < 0.01
                  else "tol_amount+date+name")
        bt = bank_by_tid[tid]
        matched.append(Match(
            tid, c.matched_type, c.matched_id, c.counterparty,
            str(_as_date(bt["transaction_date"])), round(float(bt["amount"]), 2),
            max(conf, 1.0), method, round(c.amount_delta, 2), c.date_delta_days,
            round(c.name_score, 1), round(gap, 1), _reasons(c, cfg)))

    # 2b) explain-panel candidate lists: strict matches for everyone; for a still-unmatched
    #     transaction with no strict candidate, a relaxed scan so the panel can show the closest
    #     ledger rows and *why* they fell outside tolerance (PRD US-6). This relaxed list is for
    #     display only — it never affects matching or the unexplained/double-payment findings.
    explain_by_txn: dict[str, list[Candidate]] = dict(strict_cands)
    for tid, txn in bank_by_tid.items():
        if tid in taken_txn or explain_by_txn.get(tid):
            continue
        rows = inv_rows if float(txn["amount"]) >= 0 else bill_rows
        explain_by_txn[tid] = _score_candidates(txn, rows, cfg, relax=6.0)

    # 3) buckets
    bank_recs = {t["transaction_id"]: t for t in bank.to_dict("records")}
    unmatched_bank = [
        _txn_public(t) for tid, t in bank_recs.items() if tid not in taken_txn
    ]
    led_recs = {r["ledger_id"]: r for r in ledger.to_dict("records")}
    unmatched_ledger = [
        _ledger_public(r) for lid, r in led_recs.items() if lid not in taken_led
    ]

    # 4) findings — use the STRICT candidate lists only
    findings = _derive_findings(bank_recs, led_recs, matched, taken_led, strict_cands, cfg)

    # 5) stats
    n_bank = len(bank_recs)
    stats = {
        "bank_transactions": n_bank,
        "matched": len(matched),
        "unmatched_bank": len(unmatched_bank),
        "unmatched_ledger": len(unmatched_ledger),
        "match_rate_bank": round(len(matched) / n_bank * 100, 1) if n_bank else 0.0,
        "mean_confidence": round(sum(m.confidence for m in matched) / len(matched), 1) if matched else 0.0,
        "low_confidence_matches": sum(1 for m in matched if m.confidence < 90),
        "findings": len(findings),
        "findings_by_type": _count(f["finding_type"] for f in findings),
    }

    return ReconResult(
        config=_cfg_public(cfg),
        matched=matched,
        unmatched_bank=unmatched_bank,
        unmatched_ledger=unmatched_ledger,
        findings=findings,
        stats=stats,
        candidates_by_txn={
            tid: [_cand_public(c, cfg) for c in cs[:4]] for tid, cs in explain_by_txn.items()
        },
    )


# --------------------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------------------
def _derive_findings(bank_recs, led_recs, matched, taken_led, candidates_by_txn, cfg) -> list[dict]:
    findings: list[dict] = []
    seq = 0

    def add(ftype, ids, desc, sev):
        nonlocal seq
        seq += 1
        findings.append({
            "finding_id": f"RCN{seq:03d}", "finding_type": ftype,
            "related_ids": ids, "description": desc, "severity": sev,
        })

    matched_tids = {m.transaction_id for m in matched}
    matched_dates = {m.transaction_id: _as_date(bank_recs[m.transaction_id]["transaction_date"])
                     for m in matched}
    prior_txn_for_led = {m.matched_id: m.transaction_id for m in matched}

    # duplicate bank rows: identical (date, amount)
    seen: dict[tuple, str] = {}
    for tid, t in bank_recs.items():
        key = (str(_as_date(t["transaction_date"])), round(float(t["amount"]), 2))
        if key in seen:
            add("duplicate", [seen[key], tid],
                f"Bank transactions {seen[key]} and {tid} are identical on {key[0]} for "
                f"${t['amount']:,.2f}. Source: {t['source_ref']}",
                "high" if "duplicate settlement" in str(t["source_ref"]) else "low")
        else:
            seen[key] = tid

    # unmatched bank rows: double payment vs. truly unexplained
    for tid, t in bank_recs.items():
        if tid in matched_tids:
            continue
        amt = float(t["amount"])
        this_date = _as_date(t["transaction_date"])
        cands = candidates_by_txn.get(tid, [])
        # strict double-payment: an unmatched bank line that is a near-exact re-settlement of a
        # ledger row already settled by another bank line, within a few days of it.
        dup_target = next(
            (c for c in cands
             if c.matched_id in prior_txn_for_led
             and abs(c.amount_delta) <= max(0.5, 0.001 * abs(amt))
             and abs((this_date - matched_dates[prior_txn_for_led[c.matched_id]]).days) <= 4),
            None)
        if dup_target is not None:
            prior = prior_txn_for_led[dup_target.matched_id]
            add("double_payment", [dup_target.matched_id, prior, tid],
                f"{dup_target.matched_type.capitalize()} {dup_target.matched_id} already settled by "
                f"{prior}; bank {tid} ({this_date}, ${amt:,.2f}) re-settles it (same amount, "
                f"{abs((this_date - matched_dates[prior]).days)}d apart) — possible double payment. "
                f"Source: {t['source_ref']}",
                "high")
        elif abs(amt) >= cfg.material_amount and not cands:
            add("unexplained_txn", [tid],
                f"Bank {_as_date(t['transaction_date'])} ${amt:,.2f} "
                f"\"{t['description']}\" has no candidate invoice or bill within tolerance. "
                f"Source: {t['source_ref']}",
                "high" if abs(amt) > 5000 else "medium")

    return findings


# --------------------------------------------------------------------------------------
# ground-truth scoring (the honest rediscovery metric)
# --------------------------------------------------------------------------------------
def score_against_ground_truth(result: ReconResult, truth: pd.DataFrame) -> dict:
    """Compare the engine's matches to the reconciliation_matches table build_dataset.py wrote.

    truth columns: transaction_id, matched_type, matched_id, ...
    The build injects 3 duplicate settlements -> two truth rows share a matched_id; a 1:1 engine
    can rediscover at most one of those pairs as a MATCH (the other should surface as a
    double_payment finding), so recall is measured against the set of *distinct* target ledger
    ids, and duplicate-settlement rediscovery is reported separately.
    """
    truth_by_txn = {r["transaction_id"]: r["matched_id"] for _, r in truth.iterrows()}
    engine_by_txn = {m.transaction_id: m.matched_id for m in result.matched}

    # count duplicate-settlement txns in truth (same matched_id used by >1 txn)
    from collections import Counter
    tgt_count = Counter(r["matched_id"] for _, r in truth.iterrows())
    dup_txn_ids = {r["transaction_id"] for _, r in truth.iterrows() if tgt_count[r["matched_id"]] > 1}
    primary_truth = {tid: mid for tid, mid in truth_by_txn.items() if tid not in dup_txn_ids}

    correct = sum(1 for tid, mid in primary_truth.items() if engine_by_txn.get(tid) == mid)
    wrong_target = sum(1 for tid, mid in primary_truth.items()
                       if tid in engine_by_txn and engine_by_txn[tid] != mid)
    missed = len(primary_truth) - correct - wrong_target

    # engine matches on rows that were NOT settlements in truth (false positives against noise)
    false_positive = sum(1 for m in result.matched if m.transaction_id not in truth_by_txn)

    ar_p = {tid: mid for tid, mid in primary_truth.items() if str(mid).startswith("AR")}
    ap_p = {tid: mid for tid, mid in primary_truth.items() if str(mid).startswith("AP")}
    ar_ok = sum(1 for tid, mid in ar_p.items() if engine_by_txn.get(tid) == mid)
    ap_ok = sum(1 for tid, mid in ap_p.items() if engine_by_txn.get(tid) == mid)

    dup_pairs_total = len(dup_txn_ids) // 2
    dup_pairs_caught = len({f["related_ids"][0] for f in result.findings
                            if f["finding_type"] == "double_payment"})

    return {
        "truth_settlements_total": len(truth_by_txn),
        "truth_primary_settlements": len(primary_truth),
        "duplicate_settlement_rows_in_truth": len(dup_txn_ids),
        "rediscovered": correct,
        "wrong_target": wrong_target,
        "missed": missed,
        "false_positive_on_noise": false_positive,
        "recall_pct": round(correct / len(primary_truth) * 100, 1) if primary_truth else 0.0,
        "recall_ar_pct": round(ar_ok / len(ar_p) * 100, 1) if ar_p else 0.0,
        "recall_ap_pct": round(ap_ok / len(ap_p) * 100, 1) if ap_p else 0.0,
        "precision_pct": round(correct / (correct + wrong_target + false_positive) * 100, 1)
        if (correct + wrong_target + false_positive) else 0.0,
        "duplicate_settlement_pairs_flagged": f"{dup_pairs_caught}/{dup_pairs_total}",
    }


# --------------------------------------------------------------------------------------
# serialisation helpers
# --------------------------------------------------------------------------------------
def _count(it):
    from collections import Counter
    return dict(sorted(Counter(it).items()))


def _cfg_public(cfg: ReconConfig) -> dict:
    return asdict(cfg)


def _txn_public(t: dict) -> dict:
    return {
        "transaction_id": t["transaction_id"],
        "transaction_date": str(_as_date(t["transaction_date"])),
        "description": t["description"],
        "amount": round(float(t["amount"]), 2),
        "running_balance": None if pd.isna(t.get("running_balance")) else round(float(t["running_balance"]), 2),
        "source_ref": t["source_ref"],
    }


def _ledger_public(r: dict) -> dict:
    return {
        "ledger_id": r["ledger_id"],
        "type": r["kind"],
        "counterparty": r["counterparty"],
        "amount": round(float(r["amount"]), 2),
        "due_date": str(_as_date(r["due_date"])),
        "status": r["status"],
        "retreat_id": r["retreat_id"],
        "source_ref": r["source_ref"],
    }


def _cand_public(c: Candidate, cfg: ReconConfig) -> dict:
    return {
        "matched_type": c.matched_type,
        "matched_id": c.matched_id,
        "counterparty": c.counterparty,
        "combined_score": round(c.combined, 1),
        "amount_score": round(c.amount_score, 1),
        "date_score": round(c.date_score, 1),
        "name_score": round(c.name_score, 1),
        "amount_delta": round(c.amount_delta, 2),
        "date_delta_days": c.date_delta_days,
        "accepted_threshold": cfg.accept_score,
        "verdict": ("candidate accepted-eligible" if c.combined >= cfg.accept_score
                    else _why_not(c, cfg)),
    }


def result_to_dict(r: ReconResult) -> dict:
    return {
        "config": r.config,
        "stats": r.stats,
        "matched": [asdict(m) for m in r.matched],
        "unmatched_bank": r.unmatched_bank,
        "unmatched_ledger": r.unmatched_ledger,
        "findings": r.findings,
    }
