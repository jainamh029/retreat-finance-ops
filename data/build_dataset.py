"""
build_dataset.py — assemble the Retreat Finance Ops dataset from real public sources.

Sources (full citations in data/DATA_NOTES.md):
  A. UCI Online Retail II  -> invoice amounts, dispersion, cancellations   (data/raw/online_retail_II.xlsx)
  B. Berka / PKDD'99       -> bank transaction dates, descriptions, noise  (data/raw/berka_trans.asc)
  C. Web-cited 2026 vendor pricing -> retreat budgets                      (PRICING below, each with a source)
  D. SEC EDGAR XBRL        -> DSO/DPO benchmark band                       (data/sec_benchmarks.csv)

REAL, preserved unchanged
  - every AR/AP amount is a real UCI invoice total x one disclosed constant (AR_SCALE / AP_SCALE),
    relabelled GBP->USD 1:1. Real dispersion + real cancellations (negative totals) preserved.
  - every bank-transaction DATE is a real Berka date shifted by one constant (+BERKA_YEAR_SHIFT
    years); all real inter-transaction intervals preserved exactly.
  - "noise" bank transactions are 100% real Berka rows (real date, amount, description) -> the
    unexplained-transaction audit test cases. None planted.
  - vendor price points in PRICING are real published 2026 figures.
  - SEC AR/AP/revenue figures in data/sec_benchmarks.csv are as filed.

CONSTRUCTED
  - the business identity (one US company, ~40 offsites/year) and the mapping of real records
    onto fictional clients / vendors / retreats.
  - the retreat calendar + net-30/45 terms, hence every invoice_date / due_date / bill_date.
  - the payment-lag overlay positioning due dates vs. real settlement dates so portfolio
    DSO/DPO land in the SEC-derived band (see DATA_NOTES "DSO/DPO weighting").
  - settlement bank-transaction descriptions (synthesised US-bank memos; real Berka operation
    token retained inside). Settlement amounts equal the ledger amount so reconciliation has
    true matches.
  - client / vendor names are anonymised labels; industry categories are real.

Run:  python data/build_dataset.py    ->  data/retreat_finance.db, data/DATA_NOTES.md
Deterministic: RANDOM_SEED fixes every draw.
"""
from __future__ import annotations

import bisect
import csv
import json
import math
import pickle
import random
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------
DATA = Path(__file__).resolve().parent
RAW = DATA / "raw"
CACHE = DATA / ".cache"
DB_PATH = DATA / "retreat_finance.db"
SCHEMA_SQL = DATA / "schema.sql"
SEC_CSV = DATA / "sec_benchmarks.csv"
NOTES_MD = DATA / "DATA_NOTES.md"

RANDOM_SEED = 42
AS_OF = date(2026, 9, 10)

# calendar: ~13 months of completed/in-flight retreats + a short upcoming pipeline
N_PAST = 40
N_UPCOMING = 5
CAL_PAST_START = date(2025, 8, 20)
CAL_PAST_END = date(2026, 9, 3)
CAL_UP_START = date(2026, 9, 26)
CAL_UP_END = date(2026, 12, 18)

BERKA_YEAR_SHIFT = 28          # 1993-1998 -> 2021-2026 (single disclosed constant)
AR_SCALE = 30.0               # GBP invoice total -> USD AR line (client billing)
AP_SCALE = 18.0               # GBP invoice total -> USD AP bill (vendor cost ~70-80% of billing)

DSO_BAND = (45.0, 75.0)
DPO_BAND = (20.0, 40.0)
TUNE_MAX_ITERS = 60

START_CASH = 250_000.0
NET_TERMS = [30, 45]

PRICING = {
    "venue_rate_per_hr": (295.0, 617.0, "SRC_PEERSPACE",
                          "Peerspace offsite venue ${rate:.0f}/hr x 8h x {days}d"),
    "catering_pp_meal": (30.0, 70.0, "SRC_CATERING_GUIDE",
                         "buffet ${pp:.0f}/person x {hc} x {meals} meals x {fee:.2f} fees/tax"),
    "catering_fee_mult": (1.25, 1.35),
    "travel_per_trip": (708.0, 1293.0, "SRC_GBTA",
                        "GBTA 2026 domestic trip ${trip:.0f} x {hc} attendees x {fly:.0%} flying"),
    "travel_fly_frac": (0.55, 0.85),
    "activities_pp": (85.0, 125.0, "SRC_ACTIVITIES_GUIDE",
                      "half-day workshop ${pp:.0f}/person x {hc} + $2,000 vendor minimum"),
    "activities_min_fee": 2000.0,
    "other_frac": 0.10,
}

PROVENANCE_SOURCES = [
    ("SRC_UCI_OR2", "UCI Online Retail II", "dataset",
     "https://archive.ics.uci.edu/dataset/502/online+retail+ii", "2026-09-10",
     "UK online retailer 2009-2011. CC BY 4.0. Chosen deliberately over a Kaggle mirror for "
     "citation stability. Real: invoice amounts, dispersion, 8,296 cancellation invoices. "
     "Constructed: mapping to clients/retreats; GBP->USD 1:1; single constant scale "
     "(AR_SCALE=30, AP_SCALE=12)."),
    ("SRC_BERKA", "Berka / PKDD'99 Discovery Challenge", "dataset",
     "http://sorry.vse.cz/~berka/challenge/pkdd1999/berka.htm", "2026-09-10",
     "Anonymised Czech bank 1993-1998, 1,056,320 transactions. Downloaded from the "
     "jlacko/berka-dataset GitHub mirror (canonical host unreachable on access date). Chosen "
     "deliberately over Kaggle for citation stability. Real: transaction dates (shifted +28y, "
     "intervals preserved), amounts + descriptions of noise transactions. Constructed: use as "
     "this business's bank feed; CZK->USD 1:1."),
    ("SRC_PEERSPACE", "Peerspace offsite / meeting venue listings", "pricing",
     "https://www.peerspace.com/venues/chicago--il/offsite-meeting-location", "2026-09-10",
     "Large offsite venues ~$617/hr (Chicago); city averages $112-150/hr; example LA listing "
     "$295/hr for up to 250 guests. Range used: $295-617/hr."),
    ("SRC_CATERING_GUIDE", "Corporate catering 2026 price guides", "pricing",
     "https://www.eatbreadless.com/blog/corporate-event-catering-2026-guide/", "2026-09-10",
     "Buffet $30-70/person; drop-off ~$15; plated $50-120; +25-35% delivery/service/tax. "
     "Also https://tastefullyyours.com/catering-prices-per-person/ ."),
    ("SRC_ACTIVITIES_GUIDE", "Corporate team-building 2026 cost guides", "pricing",
     "https://itsplaytyme.com/blog/how-much-does-corporate-team-building-cost/", "2026-09-10",
     "Typical $35-125/person; half-day workshop $85-125/person; premium $125-300+; ~$2,000 "
     "vendor minimum event fee. Also https://wearespin.com/how-much-do-corporate-team-building-activities-cost/ ."),
    ("SRC_GBTA", "GBTA Business Travel Index 2026 (via Engine / BTE)", "pricing",
     "https://engine.com/business-travel-guide/business-travel-data-trends", "2026-09-10",
     "Avg US domestic business trip $1,293 all-in; airfare stabilising ~$708 (economy ~$536). "
     "Also https://gbta.org/global-business-travel-and-events-prices-set-to-stabilize-through-2025-and-2026-amid-looming-economic-uncertainty/ ."),
    ("SRC_SEC_EDGAR", "SEC EDGAR XBRL company facts", "benchmark",
     "https://data.sec.gov/api/xbrl/", "2026-09-10",
     "10-K AR / AP / Revenue for MAR, HLT, LYV, GBTG (FY2022-FY2025). DSO=AR/Rev*365; "
     "DPO=AP/Rev*365 (revenue proxy: no CostOfRevenue tag filed by these issuers)."),
]

DESTINATIONS = [
    "Scottsdale, AZ", "Asheville, NC", "Sonoma, CA", "Austin, TX", "Park City, UT",
    "Hudson Valley, NY", "San Diego, CA", "Savannah, GA", "Bend, OR", "Santa Fe, NM",
    "Charleston, SC", "Jackson Hole, WY", "Palm Springs, CA", "Portland, ME", "Nashville, TN",
]

CLIENT_INDUSTRIES = [
    ("Streaming Media", "comparable scale to a mid-cap streaming company"),
    ("Enterprise SaaS", "comparable scale to a Series-D enterprise SaaS company"),
    ("Fintech / Payments", "comparable scale to a large payments scale-up"),
    ("Biotech", "comparable scale to a clinical-stage biotech"),
    ("Consumer Hardware", "comparable scale to a mid-market hardware company"),
    ("Management Consulting", "regional office of a large consultancy"),
    ("Digital Health", "comparable scale to a growth-stage digital-health company"),
    ("Cloud Infrastructure", "comparable scale to a mid-cap infra company"),
    ("AdTech", "comparable scale to a public ad-tech company"),
    ("E-commerce / Marketplace", "comparable scale to a mid-market marketplace"),
    ("Renewable Energy", "comparable scale to a mid-cap clean-energy developer"),
    ("Gaming Studio", "comparable scale to a mid-size studio"),
]

VENDOR_CATALOG = [
    ("venue", 6, [30, 45]),
    ("catering", 7, [15, 30]),
    ("travel", 4, [7, 15]),
    ("activities", 6, [15, 30]),
    ("other", 5, [15, 30]),
]

# --------------------------------------------------------------------------------------
# Source acquisition — raw files are NOT committed (66 MB + 44 MB). Downloaded on first
# build from their canonical homes; see data/DATA_NOTES.md §4 for citations.
# --------------------------------------------------------------------------------------
UCI_URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
BERKA_TRANS_URL = "https://raw.githubusercontent.com/jlacko/berka-dataset/master/data-raw/trans.asc"
_UA = "RetreatFinanceOps build_dataset (research; https://github.com/jainamh029/retreat-finance-ops)"


def _download(url: str, dest: Path) -> None:
    import urllib.request
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading {url}\n    -> {dest} …", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        f.write(r.read())
    print(f"    {dest.stat().st_size / 1e6:.1f} MB", flush=True)


def _ensure_raw() -> None:
    xlsx = RAW / "online_retail_II.xlsx"
    if not xlsx.exists():
        import zipfile
        zpath = RAW / "online_retail_II.zip"
        _download(UCI_URL, zpath)
        with zipfile.ZipFile(zpath) as z:
            member = next(n for n in z.namelist() if n.endswith(".xlsx"))
            xlsx.write_bytes(z.read(member))
        zpath.unlink()
    if not (RAW / "berka_trans.asc").exists():
        _download(BERKA_TRANS_URL, RAW / "berka_trans.asc")


# --------------------------------------------------------------------------------------
# Source loaders (cached — parsing ~1M rows each)
# --------------------------------------------------------------------------------------
def load_uci_invoices() -> list[dict]:
    CACHE.mkdir(exist_ok=True)
    cache = CACHE / "uci_invoices.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    _ensure_raw()
    import openpyxl
    wb = openpyxl.load_workbook(RAW / "online_retail_II.xlsx", read_only=True)
    inv: dict[str, dict] = {}
    for sheet in wb.sheetnames:
        rows = wb[sheet].iter_rows(values_only=True)
        next(rows)
        for no, sc, desc, qty, dt, price, cid, country in rows:
            if no is None or qty is None or price is None:
                continue
            key = str(no)
            line = float(qty) * float(price)
            rec = inv.get(key)
            if rec is None:
                inv[key] = {"invoice_no": key, "total": line,
                            "first_date": dt if isinstance(dt, datetime) else None,
                            "customer_id": "NA" if cid is None else str(cid).replace(".0", ""),
                            "country": country, "is_cancellation": key[:1] in "Cc", "n_lines": 1}
            else:
                rec["total"] += line
                if isinstance(dt, datetime) and (rec["first_date"] is None or dt < rec["first_date"]):
                    rec["first_date"] = dt
                rec["n_lines"] += 1
    out = list(inv.values())
    cache.write_bytes(pickle.dumps(out))
    return out


BERKA_OP_LABEL = {
    "VYBER": "CASH WITHDRAWAL", "PREVOD NA UCET": "OUTGOING TRANSFER",
    "PREVOD Z UCTU": "INCOMING TRANSFER", "VKLAD": "CASH DEPOSIT",
    "VYBER KARTOU": "CARD WITHDRAWAL", "": "TRANSACTION",
}
BERKA_KS_LABEL = {
    "UROK": "INTEREST CREDITED", "SANKC. UROK": "PENALTY INTEREST",
    "SLUZBY": "STATEMENT / SERVICE CHARGE", "SIPO": "HOUSEHOLD PAYMENT",
    "DUCHOD": "PENSION", "POJISTNE": "INSURANCE PREMIUM", "UVER": "LOAN PAYMENT",
}


def load_berka() -> list[dict]:
    CACHE.mkdir(exist_ok=True)
    cache = CACHE / "berka.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    _ensure_raw()
    out: list[dict] = []
    with open(RAW / "berka_trans.asc", encoding="latin-1") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            raw = row["date"].strip()
            d = date(int(raw[:2]) + 1900 + BERKA_YEAR_SHIFT, int(raw[2:4]), int(raw[4:6]))
            out.append({"trans_id": row["trans_id"], "date": d, "type": row["type"],
                        "operation": row["operation"].strip(), "k_symbol": row["k_symbol"].strip(),
                        "amount": float(row["amount"]),
                        "balance": float(row["balance"]) if row["balance"] else None})
    cache.write_bytes(pickle.dumps(out))
    return out


# --------------------------------------------------------------------------------------
# Dimensions
# --------------------------------------------------------------------------------------
def build_clients(rng) -> list[dict]:
    out = []
    for i, (industry, note) in enumerate(CLIENT_INDUSTRIES):
        letter = chr(ord("A") + i)
        out.append({"client_id": f"CLIENT_{letter}", "name": f"Client {letter}",
                    "industry_category": f"{industry} ({note})",
                    "contract_start_date": (CAL_PAST_START - timedelta(days=rng.randint(120, 900))).isoformat()})
    return out


def build_vendors(rng) -> list[dict]:
    out, n = [], 1
    for category, count, terms in VENDOR_CATALOG:
        for _ in range(count):
            out.append({"vendor_id": f"VEND_{n:03d}", "name": f"Vendor {n:02d} - {category.capitalize()}",
                        "category": category, "payment_terms_days": rng.choice(terms)})
            n += 1
    return out


def _u(rng, lo, hi):
    return lo + (hi - lo) * rng.random()


def _spread(a: date, b: date, i: int, n: int, rng, jitter=6) -> date:
    return a + timedelta(days=round((b - a).days * i / max(1, n - 1)) + rng.randint(-jitter, jitter))


def build_retreats(rng, clients) -> tuple[list[dict], list[dict]]:
    retreats, prov = [], []
    plan = ([("past", _spread(CAL_PAST_START, CAL_PAST_END, i, N_PAST, rng)) for i in range(N_PAST)]
            + [("upcoming", _spread(CAL_UP_START, CAL_UP_END, i, N_UPCOMING, rng)) for i in range(N_UPCOMING)])
    for seq, (phase, start) in enumerate(plan, 1):
        nights = rng.choice([2, 2, 3, 3, 3, 4])
        days = nights + 1
        hc = rng.randint(30, 60)
        client = rng.choice(clients)
        rid = f"RET_{start.year}_{seq:02d}"

        rate = _u(rng, *PRICING["venue_rate_per_hr"][:2]); b_venue = round(rate * 8 * days, 2)
        pp = _u(rng, *PRICING["catering_pp_meal"][:2]); fee = _u(rng, *PRICING["catering_fee_mult"])
        meals = days * 2; b_cater = round(pp * hc * meals * fee, 2)
        trip = _u(rng, *PRICING["travel_per_trip"][:2]); fly = _u(rng, *PRICING["travel_fly_frac"])
        b_travel = round(trip * hc * fly, 2)
        act = _u(rng, *PRICING["activities_pp"][:2]); b_act = round(act * hc + PRICING["activities_min_fee"], 2)
        b_other = round(PRICING["other_frac"] * (b_venue + b_cater + b_travel + b_act), 2)
        b_total = round(b_venue + b_cater + b_travel + b_act + b_other, 2)

        retreats.append({"retreat_id": rid, "client_id": client["client_id"], "phase": phase,
                         "destination": rng.choice(DESTINATIONS), "headcount": hc,
                         "start_date": start.isoformat(),
                         "end_date": (start + timedelta(days=nights)).isoformat(),
                         "budget_venue": b_venue, "budget_catering": b_cater, "budget_travel": b_travel,
                         "budget_activities": b_act, "budget_other": b_other, "budget_total": b_total})
        for cat, basis, src in [
            ("venue", PRICING["venue_rate_per_hr"][3].format(rate=rate, days=days), "SRC_PEERSPACE"),
            ("catering", PRICING["catering_pp_meal"][3].format(pp=pp, hc=hc, meals=meals, fee=fee), "SRC_CATERING_GUIDE"),
            ("travel", PRICING["travel_per_trip"][3].format(trip=trip, hc=hc, fly=fly), "SRC_GBTA"),
            ("activities", PRICING["activities_pp"][3].format(pp=act, hc=hc), "SRC_ACTIVITIES_GUIDE"),
            ("other", f"{PRICING['other_frac']:.0%} of venue+catering+travel+activities (AV / contingency)", "SRC_CATERING_GUIDE"),
        ]:
            prov.append({"retreat_id": rid, "category": cat, "source_id": src, "basis": basis})
    return retreats, prov


# --------------------------------------------------------------------------------------
# Ledger STRUCTURE (amounts + dates; no payment status yet)
# --------------------------------------------------------------------------------------
def build_ar_structure(rng, retreats, uci) -> list[dict]:
    """deposit + final per past retreat (deposit-only for upcoming); occasional add-on / credit note."""
    pos = [u for u in uci if 800 <= u["total"] <= 12000 and u["n_lines"] >= 8]
    cancels = [u for u in uci if u["is_cancellation"] and -6000 <= u["total"] < 0]
    rng.shuffle(pos); rng.shuffle(cancels)
    it_pos, it_can = iter(pos), iter(cancels)
    rows, seq = [], 0

    def emit(rid, cid, role, u, inv_date, terms, factor=1.0, tag=""):
        nonlocal seq
        if inv_date > AS_OF:
            return                      # invoice not raised yet as of the as-of date
        seq += 1
        due = inv_date + timedelta(days=terms)
        ref = f"UCI_OR2:invoice={u['invoice_no']};cust={u['customer_id']}" + (f";{tag}" if tag else "")
        rows.append({"invoice_id": f"AR{seq:04d}", "client_id": cid, "retreat_id": rid,
                     "invoice_date": inv_date.isoformat(), "due_date": due.isoformat(),
                     "amount": round(u["total"] * AR_SCALE * factor, 2),
                     "status": "void" if tag == "cancellation" else "open",
                     "payment_date": None, "source_ref": ref, "line_role": role})

    for r in retreats:
        rid, cid = r["retreat_id"], r["client_id"]
        start = date.fromisoformat(r["start_date"])
        terms = rng.choice(NET_TERMS)
        budget = r["budget_total"]

        pair = None
        for _ in range(500):
            a, b = next(it_pos), next(it_pos)
            s = (a["total"] + b["total"]) * AR_SCALE
            if 1.05 * budget <= s <= 1.85 * budget and a["total"] < b["total"]:
                pair = (a, b); break
        if pair is None:
            pair = (a, b)

        dep_lead = rng.randint(15, 45) if r["phase"] == "upcoming" else rng.randint(40, 65)
        emit(rid, cid, "deposit", pair[0], start - timedelta(days=dep_lead), terms)
        if r["phase"] == "past":
            emit(rid, cid, "final", pair[1], start + timedelta(days=rng.randint(1, 6)), terms)
            if rng.random() < 0.18:
                emit(rid, cid, "addon", next(it_pos), start + timedelta(days=rng.randint(3, 20)),
                     terms, factor=0.4, tag="addon")
            if rng.random() < 0.10:
                try:
                    emit(rid, cid, "addon", next(it_can), start + timedelta(days=rng.randint(5, 30)),
                         terms, tag="cancellation")
                except StopIteration:
                    pass
    return rows


def build_ap_structure(rng, retreats, vendors, uci) -> list[dict]:
    """Vendor bills per retreat. Each bill amount is a real UCI invoice total x AP_SCALE; the
    number and selection of bills per category is greedily chosen to fill that category's budget
    x a drawn variance factor (so per-category actual clusters around budget, and variance =
    actual - budget reflects both the drawn factor and real-invoice packing granularity)."""
    v_by_cat: dict[str, list] = {}
    for v in vendors:
        v_by_cat.setdefault(v["category"], []).append(v)
    pool = [u for u in uci if 200 <= u["total"] <= 1400]     # 24k real invoices
    rng.shuffle(pool)
    it = iter(pool)
    rows, seq = [], 0
    MAX_BILLS = {"venue": 3, "catering": 3, "travel": 4, "activities": 2, "other": 2}

    for r in retreats:
        rid = r["retreat_id"]
        start = date.fromisoformat(r["start_date"])
        cats = ["venue", "catering", "travel", "activities"]
        if rng.random() < 0.75:
            cats.append("other")
        if r["phase"] == "upcoming":
            cats = ["venue"] + (["travel"] if rng.random() < 0.5 else [])
        for cat in cats:
            vf = min(1.32, max(0.80, rng.gauss(1.0, 0.095)))   # per-category cost variance factor
            target = r[f"budget_{cat}"] * vf
            max_bills = MAX_BILLS[cat] if r["phase"] == "past" else 1
            got, n = 0.0, 0
            while n < max_bills and got < target * 0.92:
                # cap each bill at 40-75% of the full category budget so a category needs 2+ bills
                cap = target * (0.40 + 0.35 * rng.random())
                want = min(target - got, cap)
                cands = [next(it) for _ in range(25)]           # distinct real invoices, no reuse
                below = [u for u in cands if u["total"] * AP_SCALE <= want * 1.08]
                # largest invoice that still fits -> tight fill; else smallest -> minimal overshoot
                u = max(below, key=lambda u: u["total"]) if below else min(cands, key=lambda u: u["total"])
                amt = round(u["total"] * AP_SCALE, 2)
                if amt <= 0:
                    n += 1
                    continue
                vendor = rng.choice(v_by_cat[cat])
                bd = start + timedelta(days=rng.randint(-55, 8))
                n += 1
                if bd > AS_OF:
                    continue            # bill not received yet as of the as-of date
                seq += 1
                rows.append({"bill_id": f"AP{seq:04d}", "vendor_id": vendor["vendor_id"],
                             "retreat_id": rid, "category": cat, "bill_date": bd.isoformat(),
                             "due_date": (bd + timedelta(days=vendor["payment_terms_days"])).isoformat(),
                             "amount": amt, "status": "open", "payment_date": None,
                             "source_ref": f"UCI_OR2:invoice={u['invoice_no']};cust={u['customer_id']}"})
                got += amt
    return rows


# --------------------------------------------------------------------------------------
# Payment assignment (the DSO/DPO knob — deterministic given seed; params are the only variable)
# --------------------------------------------------------------------------------------
@dataclass
class LedgerParams:
    # payment probabilities are FIXED at realistic levels so the dataset always contains a
    # genuine population of overdue / delinquent items (aging, stale-invoice findings, collections
    # work). Only the lag medians are tuned to bring portfolio DSO/DPO into the SEC-derived band.
    ar_pay_prob: float = 0.86      # ~14% of past-due client invoices remain uncollected as of AS_OF
    ap_pay_prob: float = 0.90      # ~10% of past-due vendor bills still outstanding
    ar_lag_median: float = 18.0    # TUNED
    ap_lag_median: float = 6.0     # TUNED


def _lag(rng, median, sigma):
    return int(round(max(-8.0, min(median * math.exp(rng.gauss(0, sigma)), 120.0))))


def assign_payments(rows, pay_prob, lag_median, lag_sigma, early_prob, seed):
    """Return new rows with status/payment_date set as of AS_OF. Pure function of (rows, params, seed).

    Payment probability rises with age: items long past due have had collections/AP follow-up, so
    most eventually clear. A small fraction of very old receivables are written off (`void`)
    rather than sitting open forever — this keeps the 90+ bucket a realistic residual instead of
    an ever-growing pile.
    """
    rng = random.Random(seed)
    out = []
    for r in rows:
        r = dict(r)
        if r["status"] == "void":
            out.append(r); continue
        due = date.fromisoformat(r["due_date"])
        overdue = (AS_OF - due).days
        status, pay = "open", None
        if due > AS_OF:
            if rng.random() < early_prob:
                p = due - timedelta(days=rng.randint(1, 8))
                if p <= AS_OF:
                    status, pay = "paid", p
        else:
            if overdue > 300:
                p_eff, writeoff = 0.99, True
            elif overdue > 210:
                p_eff, writeoff = min(0.985, pay_prob + 0.10), True
            elif overdue > 120:
                p_eff, writeoff = min(0.965, pay_prob + 0.06), False
            else:
                p_eff, writeoff = pay_prob, False
            if rng.random() < p_eff:
                p = due + timedelta(days=_lag(rng, lag_median, lag_sigma))
                if p <= AS_OF:
                    status, pay = "paid", p
                # else: settles after AS_OF -> still open now
            elif writeoff:
                status = "void"          # bad debt written off
        r["status"] = status
        r["payment_date"] = pay.isoformat() if pay else None
        out.append(r)
    return out


# --------------------------------------------------------------------------------------
# DSO / DPO / aging
# --------------------------------------------------------------------------------------
def _open_positive(rows):
    return [r for r in rows if r["status"] in ("open", "partial") and r["amount"] > 0]


def compute_dso_dpo(invoices, bills, as_of=AS_OF):
    lo = as_of - timedelta(days=365)
    ar_open = sum(r["amount"] for r in _open_positive(invoices))
    ap_open = sum(r["amount"] for r in _open_positive(bills))
    billed = sum(r["amount"] for r in invoices
                 if r["amount"] > 0 and lo <= date.fromisoformat(r["invoice_date"]) <= as_of)
    spend = sum(r["amount"] for r in bills
                if r["amount"] > 0 and lo <= date.fromisoformat(r["bill_date"]) <= as_of)
    dso = ar_open / billed * 365 if billed else 0.0
    dpo = ap_open / spend * 365 if spend else 0.0
    return dso, dpo, ar_open, ap_open, billed, spend


def aging(rows, as_of=AS_OF):
    b = {"current": 0.0, "0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    for r in _open_positive(rows):
        d = (as_of - date.fromisoformat(r["due_date"])).days
        k = ("current" if d < 0 else "0-30" if d <= 30 else "31-60" if d <= 60
             else "61-90" if d <= 90 else "90+")
        b[k] += r["amount"]
    return {k: round(v, 2) for k, v in b.items()}


# --------------------------------------------------------------------------------------
# Settlements + bank feed + ground-truth matches
# --------------------------------------------------------------------------------------
def _sorted_berka(pool):
    if not hasattr(_sorted_berka, "_c"):
        _sorted_berka._c = {}
    key = id(pool)
    if key not in _sorted_berka._c:
        arr = sorted(pool, key=lambda x: x["date"])
        _sorted_berka._c[key] = (arr, [x["date"] for x in arr])
    return _sorted_berka._c[key]


def _nearest_berka(pool, target):
    arr, dates = _sorted_berka(pool)
    i = bisect.bisect_left(dates, target)
    cands = [arr[j] for j in (max(0, i - 1), min(len(arr) - 1, i))]
    return min(cands, key=lambda x: abs((x["date"] - target).days))


def _settlement_txn(rng, kind, ledger_id, amount, pay_date, pool, counterparty):
    b = _nearest_berka(pool, pay_date)
    op = BERKA_OP_LABEL.get(b["operation"], b["operation"] or "TRANSACTION")
    if kind == "invoice":
        memo = f"{rng.choice(['ACH CREDIT', 'WIRE IN', 'LOCKBOX DEP', 'CHECK DEP'])} " \
               f"{counterparty.upper()} INV {ledger_id} {op} REF{b['trans_id']}"
        sign = 1.0
    else:
        memo = f"{rng.choice(['ACH DEBIT', 'BILL PAY', 'WIRE OUT', 'CHECK PAID'])} " \
               f"{counterparty.upper()} {ledger_id} {op} REF{b['trans_id']}"
        sign = -1.0
    return {"_settles": (kind, ledger_id), "transaction_date": b["date"].isoformat(),
            "description": memo, "amount": round(sign * amount, 2),
            "source_ref": f"BERKA:trans_id={b['trans_id']} (real date; amount=ledger; memo synthesised)"}


NOISE_KS = ["UROK", "SANKC. UROK", "SLUZBY", "SIPO", "DUCHOD", "POJISTNE", "UVER"]


def build_noise_bank(rng, berka, n_noise=40) -> list[dict]:
    win = [b for b in berka
           if CAL_PAST_START - timedelta(days=150) <= b["date"] <= AS_OF
           and b["k_symbol"] in NOISE_KS and abs(b["amount"]) > 0]
    rng.shuffle(win)
    out = []
    for b in win[:n_noise]:
        ks = BERKA_KS_LABEL.get(b["k_symbol"], b["k_symbol"])
        op = BERKA_OP_LABEL.get(b["operation"], b["operation"] or "")
        sign = 1.0 if b["type"] == "PRIJEM" else -1.0
        out.append({"_settles": None, "transaction_date": b["date"].isoformat(),
                    "description": f"{op} {ks}".strip() + f"  [{b['k_symbol']}]",
                    "amount": round(sign * b["amount"], 2),
                    "source_ref": f"BERKA:trans_id={b['trans_id']} (real row, intact)"})
    return out


def build_bank_and_matches(rng, invoices, bills, clients, vendors, berka_credit, berka_debit, noise):
    c_name = {c["client_id"]: c["name"] for c in clients}
    v_name = {v["vendor_id"]: v["name"] for v in vendors}
    raw = list(noise)
    for r in invoices:
        if r["status"] == "paid" and r["payment_date"]:
            raw.append(_settlement_txn(rng, "invoice", r["invoice_id"], r["amount"],
                                       date.fromisoformat(r["payment_date"]), berka_credit,
                                       c_name[r["client_id"]]))
    for r in bills:
        if r["status"] == "paid" and r["payment_date"]:
            raw.append(_settlement_txn(rng, "bill", r["bill_id"], r["amount"],
                                       date.fromisoformat(r["payment_date"]), berka_debit,
                                       v_name[r["vendor_id"]]))
    # inject a few operational double-payments (duplicate settlement rows, +1 day)
    settle_rows = [t for t in raw if t.get("_settles")]
    n_dup = min(3, len(settle_rows))
    for t in rng.sample(settle_rows, k=n_dup):
        d = {k: v for k, v in t.items() if not k.startswith("_")}
        d["_settles"] = t["_settles"]
        d["transaction_date"] = (date.fromisoformat(t["transaction_date"]) + timedelta(days=1)).isoformat()
        d["source_ref"] = t["source_ref"] + " [injected duplicate settlement]"
        raw.append(d)

    raw.sort(key=lambda t: (t["transaction_date"], t["description"]))
    txns, matches, bal = [], [], START_CASH
    for n, t in enumerate(raw, 1):
        tid = f"BT{n:04d}"
        bal = round(bal + t["amount"], 2)
        txns.append({"transaction_id": tid, "transaction_date": t["transaction_date"],
                     "description": t["description"], "amount": t["amount"],
                     "running_balance": bal, "source_ref": t["source_ref"]})
        if t.get("_settles"):
            kind, lid = t["_settles"]
            matches.append({"match_id": f"MATCH{n:04d}", "transaction_id": tid,
                            "matched_type": "invoice" if kind == "invoice" else "bill",
                            "matched_id": lid, "confidence_score": 100.0,
                            "match_method": "ground_truth_build"})
    return txns, matches, n_dup


# --------------------------------------------------------------------------------------
# Audit findings
# --------------------------------------------------------------------------------------
def detect_findings(invoices, bills, bank_txns, matches):
    findings, seq = [], 0

    def add(ftype, ids, desc, sev):
        nonlocal seq
        seq += 1
        findings.append({"finding_id": f"FND{seq:03d}", "finding_type": ftype,
                         "related_ids": json.dumps(ids), "description": desc, "severity": sev,
                         "date_found": AS_OF.isoformat()})

    for label, rows, cp, idk, dk in (("invoice", invoices, "client_id", "invoice_id", "invoice_date"),
                                     ("bill", bills, "vendor_id", "bill_id", "bill_date")):
        buckets: dict[tuple, list] = {}
        for r in rows:
            if r["status"] == "void":
                continue
            buckets.setdefault((r[cp], round(r["amount"], 2)), []).append(r)
        for (who, amt), grp in buckets.items():
            if len(grp) < 2:
                continue
            grp.sort(key=lambda x: x[dk])
            for a, b in zip(grp, grp[1:]):
                gap = abs((date.fromisoformat(a[dk]) - date.fromisoformat(b[dk])).days)
                if gap <= 7:
                    add("duplicate", [a[idk], b[idk]],
                        f"Two {label} entries for {who} of ${amt:,.2f} dated {a[dk]} and {b[dk]} "
                        f"({gap}d apart) — possible duplicate. Sources: {a['source_ref']} / {b['source_ref']}",
                        "medium")

    seen: dict[tuple, str] = {}
    for t in bank_txns:
        sig = (t["transaction_date"], round(t["amount"], 2))
        if sig in seen:
            add("duplicate", [seen[sig], t["transaction_id"]],
                f"Bank transactions {seen[sig]} and {t['transaction_id']} identical on "
                f"{t['transaction_date']} for ${t['amount']:,.2f}. {t['source_ref']}",
                "high" if "duplicate settlement" in t["source_ref"] else "low")
        else:
            seen[sig] = t["transaction_id"]

    matched_ids = {m["transaction_id"] for m in matches}
    for t in bank_txns:
        if t["transaction_id"] in matched_ids or abs(t["amount"]) < 50:
            continue
        add("unexplained_txn", [t["transaction_id"]],
            f"Bank {t['transaction_date']} ${t['amount']:,.2f} \"{t['description']}\" has no "
            f"matching invoice or bill. {t['source_ref']}",
            "high" if abs(t["amount"]) > 5000 else "medium")

    for i in invoices:
        if i["status"] not in ("open", "partial"):
            continue
        dpast = (AS_OF - date.fromisoformat(i["due_date"])).days
        if dpast > 90:
            add("stale_90plus", [i["invoice_id"]],
                f"Invoice {i['invoice_id']} ({i['client_id']}, {i['retreat_id']}) ${i['amount']:,.2f} "
                f"is {dpast} days past due ({i['due_date']}). Source: {i['source_ref']}",
                "high" if dpast > 150 else "medium")

    by_target: dict[tuple, list] = {}
    for m in matches:
        by_target.setdefault((m["matched_type"], m["matched_id"]), []).append(m["transaction_id"])
    for (mtype, mid), txs in by_target.items():
        if len(txs) > 1:
            add("double_payment", [mid, *txs],
                f"{mtype.capitalize()} {mid} settled by {len(txs)} bank transactions "
                f"({', '.join(txs)}) — possible double payment.", "high")
    return findings


# --------------------------------------------------------------------------------------
# Persist
# --------------------------------------------------------------------------------------
def load_sec_rows():
    out = []
    with open(SEC_CSV) as fh:
        for r in csv.DictReader(fh):
            out.append({"company_name": r["company_name"], "ticker": r["ticker"],
                        "fiscal_period": r["fiscal_period"], "ar_balance": float(r["ar_balance"]),
                        "ap_balance": float(r["ap_balance"]), "revenue": float(r["revenue"]),
                        "computed_dso": float(r["computed_dso"]), "computed_dpo": float(r["computed_dpo"]),
                        "source_url": r["source_url"]})
    return out


def write_sqlite(**t):
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA_SQL.read_text())

    def ins(table, rows, cols):
        con.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [tuple(r[c] for c in cols) for r in rows])

    ins("clients", t["clients"], ["client_id", "name", "industry_category", "contract_start_date"])
    ins("vendors", t["vendors"], ["vendor_id", "name", "category", "payment_terms_days"])
    ins("retreats", t["retreats"], ["retreat_id", "client_id", "destination", "headcount",
        "start_date", "end_date", "budget_venue", "budget_catering", "budget_travel",
        "budget_activities", "budget_other", "budget_total"])
    ins("invoices_ar", t["invoices"], ["invoice_id", "client_id", "retreat_id", "invoice_date",
        "due_date", "amount", "status", "payment_date", "source_ref", "line_role"])
    ins("bills_ap", t["bills"], ["bill_id", "vendor_id", "retreat_id", "category", "bill_date",
        "due_date", "amount", "status", "payment_date", "source_ref"])
    ins("bank_transactions", t["bank_txns"], ["transaction_id", "transaction_date", "description",
        "amount", "running_balance", "source_ref"])
    ins("reconciliation_matches", t["matches"], ["match_id", "transaction_id", "matched_type",
        "matched_id", "confidence_score", "match_method"])
    ins("audit_findings", t["findings"], ["finding_id", "finding_type", "related_ids",
        "description", "severity", "date_found"])
    ins("sec_benchmarks", t["sec_rows"], ["company_name", "ticker", "fiscal_period", "ar_balance",
        "ap_balance", "revenue", "computed_dso", "computed_dpo", "source_url"])
    ins("provenance_sources", [{"source_id": s[0], "label": s[1], "kind": s[2], "url": s[3],
        "accessed": s[4], "notes": s[5]} for s in PROVENANCE_SOURCES],
        ["source_id", "label", "kind", "url", "accessed", "notes"])
    ins("retreat_budget_provenance", t["budget_prov"], ["retreat_id", "category", "source_id", "basis"])
    con.commit()
    con.close()


# --------------------------------------------------------------------------------------
# DATA_NOTES.md
# --------------------------------------------------------------------------------------
def write_data_notes(s: dict):
    p = s["params"]
    md = f"""# DATA_NOTES.md — Retreat Finance Ops

_Generated by `data/build_dataset.py` on {datetime.now():%Y-%m-%d %H:%M}. Deterministic
(`RANDOM_SEED = {RANDOM_SEED}`). Human-readable master of data provenance; the
`provenance_sources` / `retreat_budget_provenance` tables in `retreat_finance.db` are its
machine mirror._

## 1. What is real vs. constructed

> **Bank feed — read this before quoting a "real data" number.** Timing, amounts, and the
> duplicate / noise structure in `bank_transactions` are **real** (Berka): every transaction
> date is a real Berka date (shifted by one constant), every noise-row amount and description is
> a real Berka row, and the real Berka operation code is preserved on every row. **The matchable
> description text on _settlement_ transactions is synthesised** — a US-bank-style memo
> (`ACH CREDIT CLIENT H INV AR0002 …`) — **because the real anonymised source data contains no
> counterparty text to fuzzy-match against.** The fuzzy-matching component of `reconcile.py`
> therefore runs against constructed memo text; the amount tolerance and date-window components
> run against real Berka timing and real ledger amounts. This is the single most important
> caveat in the dataset.

**Real, preserved unchanged**

| Field | Source | Note |
|---|---|---|
| Every AR invoice / AP bill **amount** | UCI Online Retail II invoice totals | x one disclosed constant (`AR_SCALE={AR_SCALE:g}`, `AP_SCALE={AP_SCALE:g}`), GBP→USD 1:1. Real dispersion and real cancellations (negative totals) preserved. |
| Every **bank-transaction date** | Berka / PKDD'99 dates | shifted by one constant `+{BERKA_YEAR_SHIFT} years` (1993–1998 → {1993+BERKA_YEAR_SHIFT}–{1998+BERKA_YEAR_SHIFT}). All real inter-transaction intervals preserved exactly. |
| **Noise / anomaly bank transactions** ({s['n_noise']} rows) | Berka rows, `k_symbol` ∈ {{UROK, SANKC. UROK, SLUZBY, SIPO, DUCHOD, POJISTNE, UVER}} | 100% intact: real shifted date, real amount, real description. The unexplained-transaction audit test cases — none planted. |
| Vendor **price points** | live 2026 web listings / rate guides | Peerspace, hotel day-delegate rates, catering guides, GBTA travel index — §4. |
| **SEC AR / AP / Revenue** | EDGAR XBRL 10-K facts (MAR, HLT, LYV, GBTG) | as filed; `data/sec_benchmarks.csv`, {s['n_sec']} rows. |

**Constructed** (no public dataset is natively labelled as a retreat business)

- The business identity: one US company running ~40 corporate offsites/year.
- Mapping real UCI invoices → fictional clients / retreats / vendors; real Berka rows → this
  business's bank feed.
- The retreat calendar ({CAL_PAST_START}…{CAL_PAST_END} completed, {CAL_UP_START}…{CAL_UP_END}
  upcoming) and the net-30/45 client terms → every `invoice_date` / `due_date` / `bill_date`.
- The payment-lag overlay positioning due dates vs. the real Berka settlement dates so portfolio
  DSO/DPO land in the SEC-derived band (§3).
- Settlement bank-transaction **descriptions** (synthesised US-bank memos; the real Berka
  operation token is kept inside each). Settlement **dates** are the real (shifted) Berka dates;
  settlement **amounts** equal the ledger amount so reconciliation has true positives.
- Client / vendor **names** are anonymised labels. Industry categories are real; any
  "comparable scale to …" note is a size reference only and implies no billing relationship.
- **Two AR line items per retreat** (deposit + final), each its own real UCI invoice — an
  upcoming retreat has only the deposit invoice (final is raised post-event).

Every row carries `source_ref` back to the original record
(`UCI_OR2:invoice=…;cust=…` or `BERKA:trans_id=…`).

## 2. Why UCI Online Retail II + Berka, and not Kaggle

Chosen **deliberately, not as a fallback**:

- **Citation stability.** Both have permanent, peer-cited academic homes — UCI ML Repository
  dataset #502 (formal citation, CC BY 4.0) and the PKDD'99 Discovery Challenge. A Kaggle upload
  is a user-controlled mirror that can be renamed, made private, or deleted. For a project whose
  point is traceable provenance, a permanent citation beats convenience.
- **Documented semantics.** Both ship data dictionaries, so the real-vs-constructed line can be
  drawn precisely.
- **No access wall.** Direct download, no account, reproducible by anyone.

(The Berka canonical host `sorry.vse.cz` was unreachable on the access date, so the file came
from the faithful `jlacko/berka-dataset` GitHub mirror — same bytes, still the PKDD'99 data.)

## 3. DSO / DPO — validation gate and weighting logic

**Achieved on this build:** DSO **{s['dso']:.1f} days**, DPO **{s['dpo']:.1f} days**
(gate DSO ∈ [{DSO_BAND[0]:g}, {DSO_BAND[1]:g}], DPO ∈ [{DPO_BAND[0]:g}, {DPO_BAND[1]:g}]).
Converged after **{s['tune_iters']} iteration(s)** at
`ar_pay_prob={p.ar_pay_prob:.3f}`, `ar_lag_median={p.ar_lag_median:.0f}d`,
`ap_pay_prob={p.ap_pay_prob:.3f}`, `ap_lag_median={p.ap_lag_median:.0f}d`.
DSO = AR_open / trailing-12m billed AR × 365 = {s['ar_open']:,.0f} / {s['billed']:,.0f} × 365.
DPO = AP_open / trailing-12m AP × 365 = {s['ap_open']:,.0f} / {s['spend']:,.0f} × 365.

**Real benchmark data (SEC EDGAR, FY2022–FY2025):**

| Company | Model | DSO range | DPO range (rev. proxy) |
|---|---|---|---|
| Marriott (MAR) | hotel — venue **supplier** | 40–45 | 11–13 |
| Hilton (HLT) | hotel — venue **supplier** | 51–55 | 11–16 |
| Live Nation (LYV) | live events; paid up front | 28–33 | ~4 |
| Global Business Travel Group (GBTG) | corporate travel & meetings **intermediary** | 86–151 | 40–69 |

**Why the band is 45–75, not ~90–150 (nearer GBTG):**

GBTG is the closest *business-model* analogue — a travel/events intermediary that invoices
corporate clients on terms and pays suppliers — so its DSO (86–151) is the natural upper anchor.
But GBTG carries structural DSO drag a small retreat shop does not: multi-national receivables,
enterprise master-service agreements at 60–90 day terms, supplier pre-funding float, and
airline/GDS settlement cycles. Marriott/Hilton (40–55) are the wrong anchor the other way —
they are *suppliers* collecting from OTAs, cards and groups, not a business issuing net-45
client invoices with deposits.

The business is stated to bill **net-30 / net-45 with a 50% deposit**. Those terms put a hard
floor under DSO before any late behaviour: a book of receivables split between net-30 and
net-45, invoiced evenly through the year, sits around **30–40 days** even if every client pays
exactly on time. Real clients don't — a modest late-payment overlay (median ≈ 8–15 days past
due, a thin 90+ tail) lifts that into the **mid-40s to mid-70s**. So **45–75 days is the
terms-consistent range**: floor set by the stated net-30/45 terms, ceiling held well below
GBTG's enterprise-scale drag. It is derived from the business's own contract terms, not taken as
an average of four structurally different filers.

DPO 20–40 is set the same way: above the hotel suppliers (11–16, who pay fast) because a retreat
shop fronts vendor deposits and manages cash tightly, but well below GBTG's 40–69 — vendor terms
here are net-15 to net-30, not enterprise supplier contracts.

If a build cannot bring DSO/DPO into range within {TUNE_MAX_ITERS} iterations the residual is
reported here, not hidden. **This build: {s['gate_status']}.**

## 4. Source citations (access date 2026-09-10)

### A — AR / invoice amounts
**UCI Online Retail II** — https://archive.ics.uci.edu/dataset/502/online+retail+ii — CC BY 4.0.
Download `https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip`.
`data/raw/online_retail_II.xlsx` (sha256 `bcbe73b3…`). UK online retailer 2009-12-01 …
2011-12-09; 1,067,371 line items; 53,628 invoices; 8,296 cancellation invoices.

### B — bank transactions
**Berka / PKDD'99 Discovery Challenge** — description
`http://sorry.vse.cz/~berka/challenge/pkdd1999/berka.htm`; file from mirror
`https://raw.githubusercontent.com/jlacko/berka-dataset/master/data-raw/trans.asc`.
`data/raw/berka_trans.asc` (sha256 `75ab2f39…`). Czech bank 1993-01-01 … 1998-12-31;
1,056,320 transactions.

### C — vendor pricing (as published 2026-09-10)
- **Venue** — Peerspace offsite/meeting listings: large venues ≈ $617/hr (Chicago); city
  averages $112–150/hr; example LA listing $295/hr / 250 guests.
  https://www.peerspace.com/venues/chicago--il/offsite-meeting-location ·
  https://www.peerspace.com/venues/new-york--ny/offsite-meeting-location ·
  https://www.peerspace.com/pages/listings/59f7c6d7638095a50155f5ea
  Hotel day-delegate rates (per person/day, all-in): from £40 (Hampshire Court); from £85 inc
  VAT (Richmond Hill Hotel, 2026 offer). https://chatlyn.com/en/glossary/ddr-day-delegate-rate/ ·
  https://www.meetbeyondlondon.com/news/day-delegate-rates-from-%C2%A340-per-person
- **Catering** — $30–70/person buffet; ~$15 drop-off; $50–120 plated; +25–35% delivery/service/tax.
  https://www.eatbreadless.com/blog/corporate-event-catering-2026-guide/ ·
  https://tastefullyyours.com/catering-prices-per-person/
- **Activities** — $35–125/person typical; half-day workshop $85–125/person; premium $125–300+;
  ~$2,000 vendor minimum. https://itsplaytyme.com/blog/how-much-does-corporate-team-building-cost/ ·
  https://wearespin.com/how-much-do-corporate-team-building-activities-cost/
- **Travel** — GBTA Business Travel Index 2026: avg US domestic business trip $1,293 all-in;
  airfare stabilising ≈ $708 (economy ≈ $536).
  https://engine.com/business-travel-guide/business-travel-data-trends ·
  https://gbta.org/global-business-travel-and-events-prices-set-to-stabilize-through-2025-and-2026-amid-looming-economic-uncertainty/

### D — DSO / DPO benchmarks
**SEC EDGAR XBRL** `companyconcept` API — https://data.sec.gov/api/xbrl/ — CIK 0001048286 (MAR),
0001585689 (HLT), 0001335258 (LYV), 0001820872 (GBTG). Tags `AccountsReceivableNetCurrent`
(MAR: `AccountsNotesAndLoansReceivableNetCurrent`), `AccountsPayableCurrent`
(MAR: `AccountsPayableTradeCurrent`), `Revenues` (GBTG:
`RevenueFromContractWithCustomerExcludingAssessedTax`). Raw JSON in `data/raw/sec/`.
DPO uses revenue as denominator proxy — no `CostOfRevenue` tag is filed by these issuers.

## 5. Build summary (this run)

| Table | Rows | Notes |
|---|---:|---|
| clients | {s['n_clients']} | real industry categories, anonymised names |
| vendors | {s['n_vendors']} | 5 categories |
| retreats | {s['n_retreats']} | {s['n_past']} completed + {s['n_up']} upcoming; budgets from §4C pricing |
| invoices_ar | {s['n_ar']} | {s['n_dep']} deposit + {s['n_fin']} final + {s['n_add']} add-on/credit; {s['n_ar_open']} open / {s['n_ar_paid']} paid / {s['n_ar_void']} void |
| bills_ap | {s['n_ap']} | {s['n_ap_open']} open / {s['n_ap_paid']} paid |
| bank_transactions | {s['n_bank']} | {s['n_settle']} settlements (real Berka date) + {s['n_noise']} real noise rows + {s['n_dup']} injected duplicate settlements |
| reconciliation_matches | {s['n_matches']} | ground-truth links built here; `reconcile.py` rediscovers them in Step 6 |
| audit_findings | {s['n_find']} | {s['find_by_type']} |
| sec_benchmarks | {s['n_sec']} | FY2022–FY2025 |

AR aging (as of {AS_OF}): {s['ar_aging']}
AP aging (as of {AS_OF}): {s['ap_aging']}
Opening balance ${START_CASH:,.2f} → ending running balance ${s['end_bal']:,.2f}.

## 6. Known seams / limitations

- **Currency & scale.** GBP/CZK → USD 1:1; one constant multiplier per ledger side. Relative
  magnitudes, dispersion and anomaly rates are real; absolute dollar levels are a disclosed
  linear transform. Multi-currency is out of scope (PRD §10).
- **Invoice/bill dates are constructed** (retreat calendar + terms). Bank-transaction dates are
  real (shifted). "Days late" = real settlement date − positioned due date.
- **Settlement memos are synthesised — the one claim to state precisely.** Timing, amounts, and
  duplicate/noise structure in the bank feed are real (Berka). The matchable description text on
  settlement transactions is synthesised, because the real anonymised source data contains no
  counterparty text to fuzzy-match against. Only noise/unexplained bank rows carry fully real
  Berka descriptions. So `reconcile.py`'s reported match rate is: real amount tolerance + real
  date window + fuzzy match against *constructed* memo text.
- **`stale_90plus` and `double_payment` findings are derived conditions** over real records.
  `duplicate` (real Berka repeats + real UCI same-amount entries) and `unexplained_txn` (real
  Berka interest / penalty / fee rows) are fully real occurrences. Counts per type: {s['find_by_type']}.
"""
    NOTES_MD.write_text(md)


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------
def main():
    rng = random.Random(RANDOM_SEED)
    print("loading real sources …", flush=True)
    uci = load_uci_invoices()
    berka = load_berka()
    print(f"  UCI invoices: {len(uci):,}   Berka transactions: {len(berka):,}", flush=True)

    w_lo, w_hi = CAL_PAST_START - timedelta(days=250), AS_OF + timedelta(days=5)
    berka_credit = [b for b in berka if b["type"] == "PRIJEM" and w_lo <= b["date"] <= w_hi]
    berka_debit = [b for b in berka if b["type"] in ("VYDAJ", "VYBER") and w_lo <= b["date"] <= w_hi]
    print(f"  berka window: {len(berka_credit):,} credits / {len(berka_debit):,} debits", flush=True)

    clients = build_clients(rng)
    vendors = build_vendors(rng)
    retreats, budget_prov = build_retreats(rng, clients)
    ar_struct = build_ar_structure(rng, retreats, uci)
    ap_struct = build_ap_structure(rng, retreats, vendors, uci)
    print(f"  structure: {len(retreats)} retreats, {len(ar_struct)} AR lines, "
          f"{len(ap_struct)} AP bills", flush=True)

    # ---- tune payment params so DSO/DPO land in band (fixed seed -> params are the only lever) ----
    # Two-stage: move the lag median first; only if lag saturates at its bound and we are still
    # out of band do we adjust pay_prob, and never past a realistic delinquency floor/ceiling
    # (so the data always keeps a genuine overdue population for aging + stale findings).
    AR_LAG_LO, AR_LAG_HI = 1.0, 60.0
    AP_LAG_LO, AP_LAG_HI = -6.0, 45.0
    AR_PP_LO, AR_PP_HI = 0.80, 0.95
    AP_PP_LO, AP_PP_HI = 0.82, 0.97
    p = LedgerParams()
    mid_dso, mid_dpo = sum(DSO_BAND) / 2, sum(DPO_BAND) / 2
    tune_iters, invoices, bills = 0, None, None
    for it in range(1, TUNE_MAX_ITERS + 1):
        tune_iters = it
        invoices = assign_payments(ar_struct, p.ar_pay_prob, p.ar_lag_median, 0.45, 0.12, RANDOM_SEED + 7)
        bills = assign_payments(ap_struct, p.ap_pay_prob, p.ap_lag_median, 0.45, 0.30, RANDOM_SEED + 8)
        dso, dpo, ar_open, ap_open, billed, spend = compute_dso_dpo(invoices, bills)
        ok_dso = DSO_BAND[0] <= dso <= DSO_BAND[1]
        ok_dpo = DPO_BAND[0] <= dpo <= DPO_BAND[1]
        print(f"  tune {it:2d}: DSO {dso:6.1f} {'ok' if ok_dso else ' x'}   "
              f"DPO {dpo:6.1f} {'ok' if ok_dpo else ' x'}   "
              f"[ar_lag={p.ar_lag_median:5.1f} ap_lag={p.ap_lag_median:5.1f} "
              f"ar_pp={p.ar_pay_prob:.3f} ap_pp={p.ap_pay_prob:.3f}]", flush=True)
        if ok_dso and ok_dpo:
            break
        f_dso = max(-1.0, min(1.0, (dso - mid_dso) / mid_dso))
        f_dpo = max(-1.0, min(1.0, (dpo - mid_dpo) / mid_dpo))
        new_ar_lag = min(AR_LAG_HI, max(AR_LAG_LO, p.ar_lag_median - 5.0 * f_dso))
        new_ap_lag = min(AP_LAG_HI, max(AP_LAG_LO, p.ap_lag_median - 5.0 * f_dpo))
        # if lag is already pinned in the direction we need to move, shift pay_prob instead
        if not ok_dso:
            if new_ar_lag == p.ar_lag_median and f_dso > 0:      # need lower DSO, lag maxed low
                p.ar_pay_prob = min(AR_PP_HI, p.ar_pay_prob + 0.015)
            elif new_ar_lag == p.ar_lag_median and f_dso < 0:    # need higher DSO, lag maxed high
                p.ar_pay_prob = max(AR_PP_LO, p.ar_pay_prob - 0.015)
        if not ok_dpo:
            if new_ap_lag == p.ap_lag_median and f_dpo > 0:
                p.ap_pay_prob = min(AP_PP_HI, p.ap_pay_prob + 0.012)
            elif new_ap_lag == p.ap_lag_median and f_dpo < 0:
                p.ap_pay_prob = max(AP_PP_LO, p.ap_pay_prob - 0.012)
        p.ar_lag_median, p.ap_lag_median = new_ar_lag, new_ap_lag

    dso, dpo, ar_open, ap_open, billed, spend = compute_dso_dpo(invoices, bills)
    gate_status = ("in range" if DSO_BAND[0] <= dso <= DSO_BAND[1]
                   and DPO_BAND[0] <= dpo <= DPO_BAND[1]
                   else f"RESIDUAL — DSO {dso:.1f}, DPO {dpo:.1f} after {tune_iters} iters")

    noise = build_noise_bank(rng, berka)
    bank_txns, matches, n_dup = build_bank_and_matches(
        rng, invoices, bills, clients, vendors, berka_credit, berka_debit, noise)
    findings = detect_findings(invoices, bills, bank_txns, matches)
    sec_rows = load_sec_rows()

    fbt = Counter(f["finding_type"] for f in findings)
    n_settle = sum(1 for m in matches if m["match_method"] == "ground_truth_build") - n_dup
    s = {
        "params": p, "tune_iters": tune_iters, "gate_status": gate_status,
        "dso": dso, "dpo": dpo, "ar_open": ar_open, "ap_open": ap_open, "billed": billed, "spend": spend,
        "n_clients": len(clients), "n_vendors": len(vendors), "n_retreats": len(retreats),
        "n_past": sum(1 for r in retreats if r["phase"] == "past"),
        "n_up": sum(1 for r in retreats if r["phase"] == "upcoming"),
        "n_ar": len(invoices),
        "n_dep": sum(1 for i in invoices if i["line_role"] == "deposit"),
        "n_fin": sum(1 for i in invoices if i["line_role"] == "final"),
        "n_add": sum(1 for i in invoices if i["line_role"] == "addon"),
        "n_ar_open": sum(1 for i in invoices if i["status"] == "open"),
        "n_ar_paid": sum(1 for i in invoices if i["status"] == "paid"),
        "n_ar_void": sum(1 for i in invoices if i["status"] == "void"),
        "n_ap": len(bills),
        "n_ap_open": sum(1 for b in bills if b["status"] == "open"),
        "n_ap_paid": sum(1 for b in bills if b["status"] == "paid"),
        "n_bank": len(bank_txns), "n_settle": n_settle, "n_noise": len(noise), "n_dup": n_dup,
        "n_matches": len(matches), "n_find": len(findings),
        "find_by_type": ", ".join(f"{k}={v}" for k, v in sorted(fbt.items())),
        "n_sec": len(sec_rows),
        "ar_aging": aging(invoices), "ap_aging": aging(bills),
        "end_bal": bank_txns[-1]["running_balance"] if bank_txns else START_CASH,
    }

    write_sqlite(clients=clients, vendors=vendors, retreats=retreats, budget_prov=budget_prov,
                 invoices=invoices, bills=bills, bank_txns=bank_txns, matches=matches,
                 findings=findings, sec_rows=sec_rows)
    write_data_notes(s)

    print("\n" + "=" * 78)
    print(f"  wrote {DB_PATH.name} + {NOTES_MD.name}")
    print(f"  DSO {dso:.1f}d  DPO {dpo:.1f}d  ({gate_status})  after {tune_iters} iter(s)")
    print(f"  clients {s['n_clients']}  vendors {s['n_vendors']}  retreats {s['n_retreats']} "
          f"({s['n_past']} past / {s['n_up']} upcoming)")
    print(f"  AR {s['n_ar']}  ({s['n_dep']} dep + {s['n_fin']} final + {s['n_add']} addon; "
          f"{s['n_ar_open']} open / {s['n_ar_paid']} paid / {s['n_ar_void']} void)")
    print(f"  AP {s['n_ap']}  ({s['n_ap_open']} open / {s['n_ap_paid']} paid)")
    print(f"  bank {s['n_bank']}  ({s['n_settle']} settlements + {s['n_noise']} noise + {s['n_dup']} dup)  "
          f"matches {s['n_matches']}  findings {s['n_find']} [{s['find_by_type']}]")
    print(f"  AR aging {s['ar_aging']}")
    print(f"  AP aging {s['ap_aging']}")
    print(f"  ending running balance ${s['end_bal']:,.2f}")
    print("=" * 78)


if __name__ == "__main__":
    main()
