# Retreat Finance Ops

An AP/AR reconciliation, aging, budgeting, and 13-week cash-flow toolkit for a corporate-retreat
planning business — the kind of company that **bills client companies for offsites and pays a
roster of vendors** (venues, caterers, travel, activity providers). It is a single-user internal
finance tool: a FastAPI backend over SQLite, a dependency-free HTML/CSS/JS dashboard, and a
cross-checked Excel model.

It is built on **real public data** stitched into the retreat-business scenario, not fabricated
numbers. Which numbers are real and which are a construction is spelled out in
[§ What's real vs. constructed](#whats-real-vs-constructed) and, exhaustively, in
[`data/DATA_NOTES.md`](data/DATA_NOTES.md).

---

## Live site

- **Frontend (dashboard):** https://jainamh029.github.io/retreat-finance-ops/
- **Backend API:** https://retreat-finance-ops-api.onrender.com  ·  health: [`/api/health`](https://retreat-finance-ops-api.onrender.com/api/health)  ·  docs: [`/docs`](https://retreat-finance-ops-api.onrender.com/docs)

> **Free-tier cold start:** the Render backend sleeps after ~15 min of inactivity. The first
> request then takes **30–50 s** to wake it. The dashboard detects this and shows a
> "Backend is waking up…" pill with automatic retry — it is not broken, just cold. Reload once
> it loads and it's instant. The ⚙ gear also lets you point the frontend at a different backend
> URL (e.g. a local `uvicorn`).

---

## Table of contents

- [What this is, and why](#what-this-is-and-why)
- [Repo structure](#repo-structure)
- [Live site](#live-site)
- [Run it locally](#run-it-locally)
- [What's real vs. constructed](#whats-real-vs-constructed)
- [Methodology notes](#methodology-notes)
  - [Aging buckets & DSO / DPO](#aging-buckets--dso--dpo)
  - [Reconciliation: the 5 → 30 day window](#reconciliation-the-5--30-day-window)
  - [Cash-flow forecast assumptions](#cash-flow-forecast-assumptions)
- [The Excel model](#the-excel-model)
- [Design & spec documents](#design--spec-documents)
- [Tests](#tests)
- [Deployment](#deployment)
- [Known limitations / planned](#known-limitations--planned)

---

## What this is, and why

A retreat-planning business at ~40 events/year runs its finances in spreadsheets and a bank
portal. The concrete pain:

- **No prioritized view of overdue receivables.** Client invoices (a 50% deposit at booking,
  50% on completion, net-30/45) are tracked in a sheet that gets manually re-sorted. Collections
  are reactive; nobody knows the real DSO.
- **Manual bank reconciliation.** Someone eyeballs the bank export against the AR/AP ledgers,
  hundreds of lines a quarter, with messy vendor descriptions and timing gaps.
- **No forward cash view.** Venue and caterer deposits are due weeks before the client pays the
  final invoice. A retreat can be profitable and still leave the business short of cash the week
  a venue balance is due.
- **Budget overruns found at month-end**, after the next client has already been quoted off the
  stale budget.
- **No audit trail before close** — duplicate bills, double-paid invoices, unexplained debits.

This tool sits **upstream of the GL**. It is the daily operating picture a **junior
finance / finance-ops analyst** needs: what's owed, what's overdue, near-term cash, per-retreat
budget vs. actual, and a reviewable exception list. It is deliberately *not* an accounting
system, not multi-tenant, and has no auth — see [Known limitations](#known-limitations--planned).

Full problem statement and user stories: [`docs/PRD.md`](docs/PRD.md).

---

## Repo structure

```
retreat-finance-ops/
├── docs/
│   ├── PRD.md              product requirements
│   ├── TRD.md              tech requirements + architecture diagram
│   ├── UIUX_SPEC.md        dashboard design spec (dark finance-ops theme)
│   └── SCHEMA.md           SQLite schema + ER diagram
├── data/
│   ├── build_dataset.py    sources & assembles the real data -> SQLite (deterministic, seed=42)
│   ├── schema.sql          DDL (also embedded in SCHEMA.md §4)
│   ├── raw/                untouched downloaded sources (UCI xlsx, Berka .asc, SEC JSON)
│   ├── retreat_finance.db  the built SQLite database  (committed — small & deterministic)
│   ├── sec_benchmarks.csv  real DSO/DPO from SEC EDGAR
│   ├── DATA_NOTES.md        full provenance + real-vs-constructed master
│   └── DATA_SOURCING_STATUS.md   the sourcing checkpoint written before any code
├── backend/
│   ├── config.py           ReconConfig / ForecastConfig — every tolerance, one place
│   ├── db.py               SQLite -> pandas (SQLAlchemy-swap ready)
│   ├── data_access.py      view / composition layer (all filtering, aggregation, joins)
│   ├── api/main.py         FastAPI — thin routes only, zero business logic
│   ├── logic/
│   │   ├── reconcile.py    amount + date + rapidfuzz matching; findings; ground-truth scoring
│   │   ├── aging.py        AR/AP aging buckets, DSO/DPO
│   │   └── cashflow.py     13-week rolling forecast + pipeline projection
│   └── tests/              pytest — logic (real frames), API (mocked layer), DB integrity, Excel↔API
├── frontend/
│   ├── index.html · style.css · app.js     vanilla, no build step; Chart.js from CDN + table fallback
├── build_excel_model.py    generates the workbook from backend.data_access (one source of truth)
├── retreat_finance_model.xlsx
├── render.yaml             Render blueprint (backend)
├── .github/workflows/deploy-pages.yml   GitHub Pages deploy (frontend)
├── requirements.txt
└── README.md
```

---

## Run it locally

Two terminals, same pattern as my other finance/quant projects.

```bash
# one-time
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# (re)build the dataset — optional; data/retreat_finance.db is committed. The large raw sources
# (UCI xlsx ~44 MB, Berka trans.asc ~66 MB) are NOT committed — this script downloads them from
# their canonical homes on first run, then caches parsed forms. Deterministic (seed=42): the
# same DB every time. ~2 min first run.
python data/build_dataset.py
```

> `data/raw/` is git-ignored except the tiny SEC EDGAR JSON. A fresh clone runs against the
> committed `retreat_finance.db`; run `python data/build_dataset.py` only if you want to rebuild.

**Terminal 1 — backend** (FastAPI on :8000, interactive docs at `/docs`):

```bash
uvicorn backend.api.main:app --reload --port 8000
```

**Terminal 2 — frontend** (any static server; it calls the backend via `fetch`):

```bash
python -m http.server 5173 --directory frontend
# open http://localhost:5173
```

`frontend/index.html` points at `http://localhost:8000` by default (`window.RETREAT_API_BASE`);
the GitHub Pages workflow rewrites it for production. CORS on the backend allows `localhost` dev
origins and `*.github.io`.

**Rebuild the Excel model:**

```bash
python build_excel_model.py     # writes retreat_finance_model.xlsx + prints a 31-row cross-check
```

---

## What's real vs. constructed

This is the crux of the project, so it is stated plainly. Every disclosure made while building it
is consolidated here; [`data/DATA_NOTES.md`](data/DATA_NOTES.md) is the exhaustive version.

### Real sources

| Source | What it provides | Why this one |
|---|---|---|
| **[UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii)** (UK online retailer, 2009–2011, CC BY 4.0) | every AR invoice and AP bill **amount**; real dispersion; 8,296 real cancellation invoices | permanent, peer-cited academic home + published data dictionary |
| **[Berka / PKDD'99 Discovery Challenge](http://sorry.vse.cz/~berka/challenge/pkdd1999/berka.htm)** (anonymized Czech bank, 1993–1998; 1,056,320 transactions) | every bank-transaction **date**; the noise/anomaly transactions in full | same — permanent citation |
| **Live 2026 web pricing** (Peerspace, hotel day-delegate rates, catering guides, GBTA travel index) | the venue / catering / travel / activity **price points** each retreat budget is built from | each URL + access date cited in `DATA_NOTES.md §4C` and carried per-line into the API and the Excel model |
| **[SEC EDGAR XBRL](https://data.sec.gov/api/xbrl/)** — MAR, HLT, LYV, GBTG 10-Ks | real Accounts Receivable / Payable / Revenue → real DSO/DPO benchmarks | public domain; `data/sec_benchmarks.csv` |

**Chosen over Kaggle deliberately, not as a fallback.** The Kaggle datasets named in the brief
are user-controlled mirrors that can be renamed, made private, or deleted. UCI #502 and PKDD'99
have permanent, formally-citable homes. For a project whose entire point is traceable
provenance, a stable citation beats convenience. (The Berka canonical host was unreachable on
the access date, so the file came from the faithful `jlacko/berka-dataset` GitHub mirror — same
bytes.)

### What was constructed

The **business identity itself** — that these records belong to one company running ~40 corporate
offsites a year — is the construction. No public dataset is natively labelled this way.
Specifically:

- **Mapping** real UCI invoices → fictional clients / retreats / vendors, and real Berka rows →
  this business's bank feed. Every row keeps a `source_ref` back to the original record
  (`UCI_OR2:invoice=…;cust=…` or `BERKA:trans_id=…`).
- **Two AR line items per retreat** — a `deposit` invoice raised at booking and a `final`
  invoice raised just after the event, each with its own date / due date / status. An *upcoming*
  retreat has only the deposit invoice on the books (the final isn't raised until completion, as
  in real event billing). This gives aging and reconciliation two real payment events per
  retreat instead of one lump. Each line's amount is an independent real UCI invoice total, so
  the deposit/final split reflects real invoice sizes, not a hardcoded 50/50.
- **Scale + currency.** GBP → USD 1:1, times one disclosed constant per ledger side:
  `AR_SCALE = 30`, `AP_SCALE = 18` (so vendor cost lands at ~70–80% of client billing, ≈ a
  25–30% gross margin — realistic for event management). DSO/DPO are ratio metrics and therefore
  **scale-invariant**; the constants only set absolute dollar levels. Relative magnitudes,
  dispersion, and anomaly rates are the real data's.
- **Dates.** Invoice / bill dates are constructed from the retreat calendar + real net-30/45
  terms. Bank-transaction dates are **real Berka dates shifted by one constant, +28 years**
  (1993–1998 → 2021–2026), which preserves every real inter-transaction interval exactly and
  lands the project in a 2025–26 window. "Days late" is therefore a real settlement date minus a
  positioned due date.
- **The bank feed — the one claim to state precisely.** Verbatim from `DATA_NOTES.md §1/§6:`
  > Timing, amounts, and the duplicate / noise structure in `bank_transactions` are **real**
  > (Berka): every transaction date is a real Berka date (shifted by one constant), every
  > noise-row amount and description is a real Berka row, and the real Berka operation code is
  > preserved on every row. **The matchable description text on _settlement_ transactions is
  > synthesised** — a US-bank-style memo (`ACH CREDIT CLIENT H INV AR0002 …`) — **because the
  > real anonymised source data contains no counterparty text to fuzzy-match against.** The
  > fuzzy-matching component of `reconcile.py` therefore runs against constructed memo text; the
  > amount-tolerance and date-window components run against real Berka timing and real ledger
  > amounts.
- **Client / vendor names** are anonymized labels (`Client A`, `Vendor 12 — Catering`). Industry
  categories are real (`Streaming Media`, `Enterprise SaaS`, …); any "comparable scale to …"
  note is a size reference only and implies no billing relationship. Real UCI customer ids /
  Berka account ids survive only inside `source_ref`, for audit — a test asserts they never leak
  into a name.

### The DSO / DPO band (45–75 / 20–40 days) — derived, not averaged

The validation gate the dataset build must hit. It is **not** a naive average of four dissimilar
comps. Reasoning (full version in `DATA_NOTES.md §3`):

| Comp | Business model | DSO (FY22–25) | DPO |
|---|---|---|---|
| Marriott, Hilton | hotel — venue **supplier** | 40–55 | 11–16 |
| Live Nation | live events; paid up front | 27–33 | ~4 |
| Global Business Travel Group | corporate travel & meetings **intermediary** | 86–151 | 40–69 |

GBTG is the closest *business-model* analogue — an intermediary that invoices corporate clients
on terms and pays suppliers — so its DSO is the natural upper anchor. But GBTG carries
structural drag a small retreat shop does not: multi-national receivables, 60–90 day enterprise
MSAs, supplier pre-funding float, GDS settlement cycles. Marriott/Hilton are the wrong anchor the
other way — they are *suppliers*, not a business issuing net-45 client invoices with deposits.

The business is stated to bill **net-30 / net-45 with a 50% deposit**. Those terms put a hard
floor under DSO before any late behaviour: a book split between net-30 and net-45, invoiced
evenly, sits around 30–40 days even if every client pays exactly on time. A modest late-payment
overlay lifts that into the **mid-40s to mid-70s**. So **45–75 is the terms-consistent range** —
floor from the stated terms, ceiling held well below GBTG's enterprise drag. DPO 20–40 is set the
same way: above hotel suppliers (who pay fast), below GBTG (whose terms are enterprise-scale).

**Achieved on the shipped dataset:** DSO **74.0**, DPO **27.3** — both in band. DSO lands at the
top of the range, as expected: the two-invoice deposit structure raises the structural floor.

### Audit findings — 3 injected, 31 organically real

The `audit_findings` table has **34** findings at the default tolerance:

| Type | n | Real occurrence, or constructed? |
|---|---:|---|
| `unexplained_txn` | 24 | **fully real** — Berka interest / penalty-interest / household / pension / loan rows with no ledger counterpart, kept 100% intact (real date, amount, description) |
| `duplicate` | 4 | **fully real** — identical `(date, amount)` Berka rows |
| `stale_90plus` | 3 | derived condition over real records (open AR > 90 days past due) |
| `double_payment` | 3 | **injected** — an operational-error scenario: one bill settled by two bank lines, +1 day apart. The only planted anomalies in the dataset, and disclosed as such. |

`reconcile.py` recomputes findings live for any tolerance; at the default it reports 3 / 4 / 27
(the 27 = the 24 real Berka noise rows + 3 AP settlements it correctly leaves flagged rather
than force-matching).

### The 431-matched breakdown

`/api/reconciliation/report` returns `matched_accounting` so the arithmetic is explicit, and the
Reconciliation view prints it verbatim rather than an opaque headline:

```
matched 431  =  426 rediscovered_primary
             +    2 wrong_target_primary   (right bank line, wrong ledger id; both in the 437-row primary set)
             +    3 duplicate_settlement_first_of_pair
```

The 3: the *first* bank line of each injected duplicate-settlement pair (`BT0253→AP0188`,
`BT0329→AP0284`, `BT0355→AP0220`) — correctly matched to its bill. They sit **outside** the
437-row "primary settlements" set because their `matched_id` is shared by two ground-truth rows,
so `score_against_ground_truth` scores them via `duplicate_settlement_pairs_flagged` (their twins
are the `double_payment` findings), not via recall. They are **not** addon/credit lines.

---

## Methodology notes

### Aging buckets & DSO / DPO

Per open item (`status ∈ {open, partial}`, `amount > 0`), by days between the as-of date and the
**due date**:

| days `d` | bucket |
|---|---|
| `d < 0` | `current` (not yet due) |
| `0 ≤ d ≤ 30` | `0-30` |
| `31 ≤ d ≤ 60` | `31-60` |
| `61 ≤ d ≤ 90` | `61-90` |
| `d > 90` | `90+` |

```
DSO = AR_open / (trailing-12-month billed AR) × 365
DPO = AP_open / (trailing-12-month AP)        × 365
```

DPO uses **spend as the denominator** (revenue/spend proxy) — there is no COGS split in this
dataset, and none of the four SEC comps file a `CostOfRevenue` XBRL tag, so the benchmark is
computed the same way. Stated everywhere DPO appears.

### Reconciliation: the 5 → 30 day window

Each bank transaction is matched to at most one ledger row on **amount tolerance** (±1% or ±$5)
**and** a **date window** around the ledger's **due date** **and** a rapidfuzz `token_set_ratio`
on the description; survivors are scored (`0.50·amount + 0.20·date + 0.30·name`) and assigned
greedily.

The TRD originally sketched a ±5-day window. That is far too tight: matching anchors on the
**due date** (the only date an analyst has before reconciling), and real payments land from a
few days early to ~4 weeks late on net-15..45 terms. Measured on the shipped dataset — this is a
demonstrated finding, not just a config value, and the frontend's gear control reproduces it
live:

| date window | ground-truth recall | precision |
|---|---:|---:|
| ± 5 d | **20.1%** | 80.7% |
| ± 10 d | 47.8% | 91.3% |
| ± 15 d | 73.9% | 97.6% |
| ± 20 d | 87.4% | 98.5% |
| **± 30 d (default)** | **97.5%** | **99.5%** |

**What that 97.5% / 99.5% measures — verbatim from `DATA_NOTES.md`:** the algorithm's
**timing-resolution accuracy** (the window story above) and its **exception discipline**
(correctly leaving the 3 genuinely unmatchable AP settlements flagged rather than
force-assigning them). It is **not** a measure of amount-fuzzing robustness — settlement amounts
in the dataset were built to equal the ledger amount exactly, a known and disclosed
simplification. Amount and name still gate and score every candidate; they just aren't the hard
part on this corpus. This sentence is reproduced in the app (Reconciliation view), in the Excel
Dashboard tab (text block + cell comment), and in `/api/reconciliation/report`'s
`ground_truth_note`, so it can't exist only in one place.

Tolerances are never hardcoded in `reconcile.py` — they live in `backend/config.py::ReconConfig`
and are passed per request. `GET /api/config` exposes the defaults + bounds for the gear control.

### Cash-flow forecast assumptions

`ForecastConfig` defaults mirror the dataset build's converged payment model:

- expected collection date for an open invoice `= due_date + 1d` (or `as_of + 10d` if already
  overdue), weighted by `ar_collect_prob = 0.89`.
- expected payment date for an open bill `= due_date + 12d` (or catch-up), weighted by
  `ap_pay_prob = 0.90`.
- **pipeline projection**: retreats starting inside the 13-week horizon whose deposit invoice
  isn't raised yet contribute a projected deposit ≈ `0.40 × budget_total × ar_collect_prob`, so
  the back half of the horizon isn't artificially empty.
- `ending_balance[w] = ending_balance[w-1] + collections + pipeline − payments`, seeded by the
  real latest running balance in `bank_transactions`.

---

## The Excel model

`retreat_finance_model.xlsx` — six tabs (AR, AP, Budget vs Actual, Cash Flow Forecast,
Dashboard, Data Sources) plus a hidden chart-data sheet.

**One source of truth.** `build_excel_model.py` imports `backend.data_access` — the exact layer
FastAPI serves — so the workbook and the API are one calculation. The AR/AP aging buckets,
DSO/DPO, and cash-flow endings are **live Excel formulas** (`SUMIFS`, `=open/trailing×365`,
running `ending = prev + net`) so a reviewer sees the chain; `check` columns flag any drift.

**Verified two ways, both automated:** 31 build-time assertions (`python build_excel_model.py`
prints them), and `backend/tests/test_excel_matches_api.py` — which builds the workbook, has
**LibreOffice headless recalculate it**, reads the results back with `openpyxl(data_only=True)`,
and asserts DSO, DPO, cash position, match rate, every aging bucket, all 13 cash-flow endings,
and a budget-variance figure equal the live API. If `cashflow.py` / `reconcile.py` / `aging.py`
ever change in a way the formulas don't mirror, that test fails.

The `ground_truth_note` and the cited price source per budget line are carried into the
workbook, not left API-only.

---

## Design & spec documents

Written and reviewed before the code, in this order:

- [`docs/PRD.md`](docs/PRD.md) — problem, target user, goals/non-goals, 12 user stories, success criteria
- [`docs/TRD.md`](docs/TRD.md) — architecture (Mermaid), tech-stack rationale, every endpoint, NFRs, testing approach, deployment
- [`docs/UIUX_SPEC.md`](docs/UIUX_SPEC.md) — the dark finance-ops theme, palette tokens, the colour+icon+text status system, all six screens, interactions, accessibility
- [`docs/SCHEMA.md`](docs/SCHEMA.md) — SQLite schema, ER diagram, indexing, the DDL
- [`data/DATA_NOTES.md`](data/DATA_NOTES.md) — the authoritative, standalone-readable provenance master

---

## Tests

```bash
pytest backend/tests -q          # 72 passed
ruff check .                     # All checks passed!
```

| File | Covers |
|---|---|
| `test_reconcile.py` | matching behaviour (exact / within-tol / rejected / polarity / fuzzy-name / ambiguity haircut / 1:1), explain-panel, **integration: ground-truth recall ≥ 85% (PRD SC-1), precision, 0 noise false-positives, all 3 double-payments flagged, every finding traceable** |
| `test_aging.py` | bucket boundaries, void/zero exclusion, DSO/DPO shape; **integration: shipped DB reproduces DSO 74.0 / DPO 27.3** |
| `test_cashflow.py` | 13 weeks, seeding, probability weighting, overdue catch-up, shortfall detection, pipeline projection |
| `test_api.py` | FastAPI layer only — data layer mocked; status codes, query-param plumbing, 404/422 mapping, CORS, cold-start-safe recon report; one end-to-end `fake_db` wiring test |
| `test_db_integrity.py` | 0 orphan FKs, status↔payment_date invariant, 100% `source_ref` coverage, `budget_total` = component sum, running-balance continuity, no id leak into names, findings resolve, DSO/DPO still in the SEC gate, row counts in range |
| `test_excel_matches_api.py` | **LibreOffice-recalculated** workbook == live API for DSO, DPO, cash position, match rate, aging buckets, all 13 cash-flow endings, budget variance |

`test_db_integrity.py` and `test_excel_matches_api.py` **skip** (not fail) if the DB isn't built
or LibreOffice isn't installed, so the pure unit suite still runs anywhere.

---

## Deployment

Same pattern as my other projects: backend on Render via a Blueprint, frontend on GitHub Pages
via Actions. Both auto-redeploy on every push to `main`.

**Backend → Render** — [`render.yaml`](render.yaml) at the repo root. Python, `pip install -r
requirements.txt`, `uvicorn backend.api.main:app --host 0.0.0.0 --port $PORT`, free plan, health
check `/api/health`. `data/retreat_finance.db` is committed (small, deterministic) so the
service has data on boot. Optionally set `ALLOWED_ORIGINS` to the exact Pages URL (the
`*.github.io` regex already covers it).

**Frontend → GitHub Pages** — [`.github/workflows/deploy-pages.yml`](.github/workflows/deploy-pages.yml),
Actions deployment method (not the legacy branch/folder method). Publishes `frontend/` verbatim
on push. `index.html` resolves the API base itself: served from `*.github.io` it defaults to the
Render backend URL; the ⚙ gear lets a viewer override it (persisted), and `?api=<url>` is a
one-off override.

**Two one-time manual steps** (can't be done from a push, same as the Render connection on my
other projects):

1. **GitHub → repo Settings → Pages → Build and deployment → Source: "GitHub Actions".**
2. **[render.com](https://render.com) → New → Blueprint → pick this repo → Apply.**

**Cold start.** Render free tier sleeps after ~15 min idle; the first request then takes
30–50 s. `app.js` calls `/api/health` on load and, if slow, shows a "Backend is waking up…" pill
with auto-retry (exponential backoff) instead of a broken dashboard.

CORS: `GET`/`OPTIONS` only; `allow_origins` = configured dev origins + `allow_origin_regex =
https://.*\.github\.io`.

---

## Known limitations / planned

Called out honestly — these are real, not hand-waved.

- **No live bank feed.** Data is loaded from files by `data/build_dataset.py`, exactly as an
  analyst would export a CSV from their bank portal. No Plaid, no bank API. "Refresh" = re-run
  the build.
- **Single-user, no auth, no roles.** Anyone who can reach the app can use it. Not multi-tenant.
- **Not an accounting system.** No general ledger, journal entries, trial balance, or financial
  statements. This sits upstream of the GL.
- **No multi-currency / FX / tax.** GBP/CZK are relabelled to USD 1:1; sales tax, VAT and
  withholding are out of scope.
- **Settlement amounts are exact** (built to equal the ledger amount) — see the
  reconciliation caveat above. A v2 dataset would introduce partial payments and short-pays to
  exercise amount-fuzzing.
- **Settlement memos are synthesised** (the source has no counterparty text); only the
  noise/unexplained bank rows carry fully real descriptions.
- **`stale_90plus` / `double_payment` findings** are a derived condition / an injected scenario
  respectively; `duplicate` and `unexplained_txn` are fully real. Counts are reported, not
  smoothed over.
- **DB write path.** The UI is read + analyse only; marking an invoice paid happens in the
  source data and a rebuild, not in the app. The one writable setting is reconciliation
  tolerance (per request, not persisted server-side).
- **Excel cross-check needs LibreOffice** to run as a test; without it, that test skips.

Planned, roughly in order: partial-payment support in the dataset; a small SQLAlchemy write path
for "mark paid"; a Postgres profile (the DB layer is already abstracted for it); per-vendor
collections/aging drill-down.

---

_Built on real public data. Where a number is a construction, this README and
`data/DATA_NOTES.md` say so — that transparency is the point, not a footnote._
