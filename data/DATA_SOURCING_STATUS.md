# Data Sourcing Status — review checkpoint

**Date:** 2026-09-10
**Purpose:** show every real data source, with citations, **before** any logic is built on top of
it (per Section 8 of the brief). This file becomes the backbone of `DATA_NOTES.md` once
`build_dataset.py` runs.

Nothing below is fabricated. Where a real value had to be recontextualized (e.g. a real invoice
assigned to a fictional retreat), that is called out explicitly.

---

## Summary table

| Brief item | Chosen real source | Status | Format / size | Licence |
|------------|-------------------|--------|---------------|---------|
| A — invoice / AR data | **UCI Online Retail II** | ✅ downloaded | `.xlsx`, 45.6 MB, 1,067,371 line items | CC BY 4.0 |
| B — bank transaction data | **Berka / PKDD'99 Discovery Challenge** (Czech bank, anonymized) | ✅ downloaded | `.asc` (semicolon CSV), 69.4 MB, 1,056,320 transactions | Public research dataset, freely redistributed |
| C — vendor pricing | Live web listings + 2026 published rate guides (5 categories) | ✅ captured (URLs + figures + access date below) | text quotes | cited, quoted for commentary |
| D — DSO/DPO benchmarks | **SEC EDGAR XBRL API** — MAR, HLT, LYV, GBTG | ✅ pulled, `data/sec_benchmarks.csv` written | JSON → CSV, 16 rows | public domain (US gov) |

> **Substitution note (please confirm):** the brief names Kaggle datasets as *examples* ("e.g.")
> for A and B. The build environment has **no Kaggle credentials**, so I used two fully open,
> directly-downloadable, well-documented real datasets instead. Both are genuinely real
> transaction data and arguably stronger for provenance (no login wall, stable canonical URLs,
> published data dictionaries). If you specifically want a Kaggle source, add
> `~/.kaggle/kaggle.json` and I'll swap it in.

---

## A — Real invoice / AR transaction data

- **Dataset:** Online Retail II
- **Publisher:** UCI Machine Learning Repository (dataset #502). Donated by Dr Daqing Chen,
  London South Bank University.
- **URL:** https://archive.ics.uci.edu/dataset/502/online+retail+ii
- **Direct download used:** `https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip`
- **Accessed:** 2026-09-10
- **Licence:** Creative Commons Attribution 4.0 International (CC BY 4.0)
- **Saved to:** `data/raw/online_retail_II.xlsx` (sha256 `bcbe73b35f5b7bab…`), original zip kept alongside
- **What it actually is:** all transactions for a UK-based, registered, non-store online retailer
  between **2009-12-01 and 2011-12-09**. Real business, anonymized only by removing the company
  name and customer names (customers are numeric IDs).
- **Contents (measured):**
  - 1,067,371 invoice line items across 2 sheets (`Year 2009-2010`, `Year 2010-2011`)
  - **53,628 distinct invoices**
  - **8,292 cancellation invoices** (InvoiceNo starting `C`) — real credit notes / reversals
  - columns: `Invoice, StockCode, Description, Quantity, InvoiceDate, Price, Customer ID, Country`
  - 43 countries (dominated by United Kingdom)
- **Why it fits:** gives real invoice numbers, real per-invoice totals (Σ Quantity×Price), real
  timestamps, real repeat-customer cadence, and **real messiness we did not invent**: cancellations,
  negative quantities, zero/again-billed lines, the same customer invoiced many times.
- **Real vs constructed for this project:**
  - **Real, preserved unchanged:** invoice identifier, invoice date, line and invoice amount,
    cancellation status, which customer id recurs and how often.
  - **Constructed:** the mapping `customer id → Client A/B/C…`, `invoice → retreat_id`, and the
    treatment of GBP amounts as USD 1:1 (no FX). `source_ref = UCI_OR2:<Invoice>` on every row.
- **Sampling plan (executed in `build_dataset.py`, not yet run):** collapse to invoice level,
  then stratified-sample by invoice-total decile down to **80–150 AR invoices** for ~40
  retreats/year, keeping the natural cancellation/duplicate rate.

## B — Real bank transaction data

- **Dataset:** PKDD'99 Financial Dataset, a.k.a. the **Berka dataset**
- **Origin:** Petr Berka & Marta Sochorová, prepared for the PKDD'99 Discovery Challenge.
  Real anonymized data from a **Czech bank, 1993–1998**.
- **Canonical description page:** `http://sorry.vse.cz/~berka/challenge/pkdd1999/berka.htm`
  (host was unreachable from the build environment on the access date — see mirror below)
- **Mirror actually downloaded from:** `jlacko/berka-dataset` on GitHub
  `https://raw.githubusercontent.com/jlacko/berka-dataset/master/data-raw/trans.asc`
  (a faithful redistribution of the original `trans.asc`; also grabbed `account.asc`)
- **Accessed:** 2026-09-10
- **Licence / terms:** distributed for research and education, freely redistributed for ~25 years
  by CTU Prague's relational-data repository and dozens of mirrors. No commercial claim asserted.
- **Saved to:** `data/raw/berka_trans.asc` (sha256 `75ab2f39df9d79d7…`, 69.4 MB),
  `data/raw/berka_account.asc`
- **Contents (measured):**
  - **1,056,320 transactions**, dates **930101–981231** (1993-01-01 … 1998-12-31, `YYMMDD`)
  - columns: `trans_id, account_id, date, type, operation, amount, balance, k_symbol, bank, account`
  - `type` ∈ {PRIJEM (credit), VYDAJ (debit), VYBER (withdrawal)}; `operation` and `k_symbol`
    are real transaction-characterization codes (e.g. `VKLAD` deposit, `PREVOD Z UCTU` transfer
    from account, `UROK` interest, `SANKC. UROK` penalty interest)
  - **real running `balance`** after each transaction
- **Why it fits:** this is the `reconcile.py` "bank feed" side. Real messy descriptions (the
  `operation`/`k_symbol` text), real timing (many transactions per account per month, irregular),
  real running balances, and real anomalies — repeated identical postings, interest/penalty
  lines with no matching invoice, transfers with sparse counterparty text.
- **Real vs constructed:**
  - **Real, preserved unchanged:** transaction date, amount, running balance, the
    operation/characterization text used as the fuzzy-match `description`.
  - **Constructed:** treating these as the retreat business's operating-account feed; currency
    relabel (CZK → USD 1:1, disclosed); `source_ref = BERKA:<trans_id>`.
  - Bank rows that correspond to vendor payments will be aligned to AP bills during relabeling so
    the reconciliation engine has real true-positives to find; genuinely unmatched interest/
    penalty/transfer lines are **kept** as the unexplained-transaction test cases (not deleted,
    not planted).

## C — Real vendor pricing (for retreat budgets)

All figures below are as published on the access date **2026-09-10**. Quoted for
factual commentary; source URLs recorded. Ranges (not single prices) are captured so budgets can
be built at a defensible point and the source range shown in the UI tooltip.

### C1 — Offsite / meeting venue

| Data point | Value (as published) | Source | URL |
|-----------|----------------------|--------|-----|
| Peerspace meeting/offsite space, avg hourly | NYC ≈ $126/hr; SF ≈ $150/hr; Chicago ≈ $117/hr; Minneapolis ≈ $112/hr | Peerspace city "offsite meeting location" listing pages | https://www.peerspace.com/venues/new-york--ny/offsite-meeting-location · https://www.peerspace.com/venues/san-francisco--ca/offsite-meeting-location · https://www.peerspace.com/venues/chicago--il/offsite-meeting-location |
| Peerspace large venues, hourly | ≈ $617/hr (Chicago, larger-capacity venues) | Peerspace Chicago offsite listing page | https://www.peerspace.com/venues/chicago--il/offsite-meeting-location |
| Peerspace example listing | $295/hr, up to 250 guests, 3,000 sq ft (Los Angeles) | Peerspace listing | https://www.peerspace.com/pages/listings/59f7c6d7638095a50155f5ea |
| Hotel **Day Delegate Rate** (all-in per person/day: room hire + AV + working lunch + 2 breaks) | from £40/person (Hampshire Court); from £85 inc VAT (Richmond Hill Hotel, 2026 booking offer) → ≈ $50–$110/person/day | chatlyn DDR glossary; Meet Beyond London; Richmond Hill Hotel offers page | https://chatlyn.com/en/glossary/ddr-day-delegate-rate/ · https://www.meetbeyondlondon.com/news/day-delegate-rates-from-%C2%A340-per-person · https://www.richmondhill-hotel.co.uk/meetings-and-events-in-richmond/offers-and-packages |

### C2 — Catering (per person)

| Data point | Value (as published) | Source | URL |
|-----------|----------------------|--------|-----|
| Drop-off / basic | ≈ $15/person | Breadless corporate catering guide 2026 | https://www.eatbreadless.com/blog/corporate-event-catering-2026-guide/ |
| Buffet | $30–$70/person (team lunch 20–50 ppl often budgeted $25–$35) | Breadless; Tastefully Yours 2026 catering price guide | https://www.eatbreadless.com/blog/corporate-event-catering-2026-guide/ · https://tastefullyyours.com/catering-prices-per-person/ |
| Plated / sit-down | $50–$120/person nationally | Tastefully Yours 2026 | https://tastefullyyours.com/catering-prices-per-person/ |
| Add-on for delivery + service charge + tax + disposables | +25–35% on menu price | Breadless 2026 | https://www.eatbreadless.com/blog/corporate-event-catering-2026-guide/ |

### C3 — Team-building / activities (per person)

| Data point | Value (as published) | Source | URL |
|-----------|----------------------|--------|-----|
| Typical professional activity | $35–$125/person | It's Playtyme 2026 cost guide; SPIN | https://itsplaytyme.com/blog/how-much-does-corporate-team-building-cost/ · https://wearespin.com/how-much-do-corporate-team-building-activities-cost/ |
| Half-day facilitated workshop | $85–$125/person | It's Playtyme 2026 | https://itsplaytyme.com/blog/how-much-does-corporate-team-building-cost/ |
| Premium / full-day offsite experience | $125–$300+/person | It's Playtyme 2026 | https://itsplaytyme.com/blog/how-much-does-corporate-team-building-cost/ |
| Typical vendor minimum event fee | ≈ $2,000 | It's Playtyme 2026 | https://itsplaytyme.com/blog/how-much-does-corporate-team-building-cost/ |

### C4 — Travel (per attendee)

| Data point | Value (2026 projection) | Source | URL |
|-----------|------------------------|--------|-----|
| Avg U.S. domestic business trip, all-in | $1,293/trip | GBTA benchmarking, via Engine business-travel data | https://engine.com/business-travel-guide/business-travel-data-trends |
| Avg airfare (stabilized) | ≈ $708 (global ≈ $756; economy ≈ $536) | GBTA Business Travel Index 2026, via GBTA + Business Travel Executive | https://gbta.org/global-business-travel-and-events-prices-set-to-stabilize-through-2025-and-2026-amid-looming-economic-uncertainty/ · https://www.businesstravelexecutive.com/news/gbta-forecasts-business-travel-prices-stay-elevated-through-2026/ |

**How these become budgets (in `build_dataset.py`):** e.g. a 40-person, 3-day Scottsdale retreat
→ venue = large-venue rate × 8h × 3d; catering = buffet midpoint × 40 × 3 × 1.30 fees; travel =
domestic-trip figure × 40; activities = half-day workshop midpoint × 40 + vendor minimum; other =
10% contingency. Each line is stored with its `source_id` and a `basis` string
(`retreat_budget_provenance` table, SCHEMA §2.10).

## D — Real AR/AP benchmarks from SEC EDGAR

- **API:** SEC EDGAR XBRL `companyconcept` — `https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json`
- **Accessed:** 2026-09-10, with a descriptive `User-Agent` per SEC fair-access policy
- **Raw JSON archived to:** `data/raw/sec/` (12 files)
- **Output:** `data/sec_benchmarks.csv` (16 rows, FY2022–FY2025)
- **Tags used:** `AccountsReceivableNetCurrent` (MAR: `AccountsNotesAndLoansReceivableNetCurrent`),
  `AccountsPayableCurrent` (MAR: `AccountsPayableTradeCurrent` fallback), `Revenues`
  (GBTG: `RevenueFromContractWithCustomerExcludingAssessedTax`).
- **Method:** annual figures from 10-K filings only. DSO = AR / Revenue × 365.
  **DPO = AP / Revenue × 365** — revenue is used as the denominator **proxy** because none of the
  four issuers file a `CostOfRevenue` / `CostOfGoodsAndServicesSold` XBRL tag. This proxy is
  disclosed everywhere DPO is shown.

### Computed benchmarks

| Company | Ticker | Sector relevance | FY22 | FY23 | FY24 | FY25 |
|---|---|---|---|---|---|---|
| Marriott International | MAR | hotel / venue supply side | DSO 45.2 / DPO 13.1 | 41.7 / 11.4 | 40.6 / 11.1 | 40.5 / 11.3 |
| Hilton Worldwide | HLT | hotel / venue supply side | 55.2 / 15.3 | 53.0 / 16.3 | 51.7 / 13.4 | 51.2 / 11.4 |
| Live Nation Entertainment | LYV | live events / activities | 32.1 / 3.9 | 32.5 / 4.3 | 27.5 / 3.8 | 29.1 / 3.7 |
| Global Business Travel Group | GBTG | **corporate travel & meetings — closest analog** | 150.9 / 49.9 | 115.7 / 48.1 | 86.0 / 39.6 | 116.7 / 69.2 |

- **Observed real ranges:** DSO **27.5 – 150.9 days**, DPO **3.7 – 69.2 days** (revenue-proxy).
- **Reading it:** hospitality suppliers (MAR/HLT) collect in ~40–55 days and pay vendors fast
  (~11–16 days). Corporate travel/meetings (GBTG), which — like a retreat business — **invoices
  corporate clients on terms**, runs much higher DSO (86–150) and DPO (40–70). Live events (LYV)
  collect fast and pay fast because ticket money arrives up front.
- **Target band for the constructed retreat business:** **DSO ≈ 45–75 days**, **DPO ≈ 20–40 days**
  — deliberately between "hotel supplier" and "corporate travel intermediary", matching a small
  business that bills clients net-30/45 with deposits but still fronts vendor deposits.
  `build_dataset.py` step 6 samples the AR/AP set until DSO and DPO land in this band (or
  documents the residual).

## E — Client / vendor naming

- **No fabricated company acting as a real counterparty.** No real company name appears as a
  client or vendor.
- Clients: `Client A`, `Client B`, … each tagged with a **real industry category**
  (Streaming Media, Enterprise SaaS, Biotech, Fintech, Consumer Hardware, Management Consulting,
  …) and an optional "comparable scale to …" note referencing a real company *as a size
  reference only*.
- Vendors: `Vendor 01 — Venue`, `Vendor 12 — Catering`, … category-labeled.
- The underlying real customer/account **identifiers** (UCI `Customer ID`, Berka `account_id`)
  are preserved inside `source_ref` so every anonymized entity is still traceable to its real
  source record for audit.

---

## What's real vs constructed — one-paragraph version (for README)

Real and preserved unchanged: every dollar amount, every date, every duplicate, every
cancellation, every unexplained bank movement, the venue/catering/travel/activity price points,
and the SEC-derived DSO/DPO benchmarks. Constructed: the identity of the business (that these
records belong to one company running ~40 corporate offsites a year), the mapping of real
invoices and transactions onto fictional clients/vendors/retreats, and the 1:1 currency
relabeling. No anomaly in the audit report is planted — they are all real occurrences in UCI
Online Retail II and the Berka dataset, surfaced by the reconciliation engine.

## Open items before build

1. **Confirm the Kaggle → UCI/Berka substitution** (or provide Kaggle creds).
2. **Confirm the business shape:** US-based, ~40 domestic offsites/yr, 30–60 attendees, 2–4
   nights, clients on net-30/45 with 50% deposit. (Assumption A-3 in the PRD.)
3. **Confirm the DSO/DPO target band** (45–75 / 20–40 days) as the validation gate.
