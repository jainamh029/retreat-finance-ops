# PRD — Retreat Finance Ops

**Status:** Draft for review
**Owner:** Jainam Shah
**Last updated:** 2026-09-10

---

## 1. Summary

Retreat Finance Ops is a single-user internal finance tool for a corporate-retreat-planning
business (modeled on companies like TeamOut / Offsite: the business bills client companies for
offsites and pays a roster of vendors — venues, caterers, travel providers, activity providers).

It gives one finance/ops person a daily operating picture of:

- what clients owe and how overdue it is (AR aging + DSO),
- what the business owes vendors and when it is due (AP aging + DPO),
- whether near-term cash covers upcoming vendor payments (13-week cash flow forecast),
- whether each retreat is running over budget (budget vs. actual per retreat),
- whether the books are clean (bank reconciliation + audit findings).

It is built on **real public data** recontextualized into the retreat-business scenario, not
fabricated numbers. See [DATA_NOTES.md](../data/DATA_NOTES.md) and Section 8 below.

## 2. Problem statement

A retreat-planning business at ~40 events/year runs its finances in spreadsheets and a bank
portal. Concretely, the pain is:

1. **No prioritized view of overdue receivables.** Client invoices (often 50% deposit + 50% on
   completion, net-30/net-45) are tracked in a sheet that is manually re-sorted. Collections
   calls are reactive; nobody knows the true DSO.
2. **Manual bank reconciliation.** Someone eyeballs the bank export against the AR/AR ledgers
   line by line. Hundreds of items per quarter, messy vendor descriptions, partial payments,
   and timing gaps between invoice date and cleared date make this slow and error-prone.
3. **No cash flow forecast.** Vendor deposits (venue, caterer) are due weeks before the client
   pays the final invoice. Without a forward view the business can be technically profitable on
   a retreat and still short of cash the week a venue balance is due.
4. **Budget overruns found too late.** Actual vendor bills routinely drift above the budget
   quoted to the client. Today that is discovered at month-end, after the next client has
   already been quoted using the stale budget.
5. **No audit trail before close.** Duplicate bills, double-paid invoices, and unexplained bank
   debits are found (if at all) during the annual review, not during monthly close.

## 3. Target user

A **junior finance / finance-ops analyst** — the role this project is an application for. They:

- open the tool every morning to see what changed,
- need to prioritize collections work by dollars and days overdue,
- run the reconciliation before month-end close and work the exception list,
- produce the "can we cover payroll + vendor payments for the next quarter" answer for their manager,
- are not an accountant closing the books in an ERP; this tool sits *upstream* of the GL.

Not a target user: executives wanting board reporting, multiple concurrent finance staff,
external clients, or vendors.

## 4. Goals / non-goals

### Goals

- G1. One dashboard that answers "what's owed, what's overdue, what's our cash position" in < 5 seconds.
- G2. Automated bank reconciliation that matches the large majority of real transactions and
  produces a short, reviewable exception list.
- G3. A 13-week rolling cash flow forecast that updates from the same ledger data.
- G4. Budget-vs-actual per retreat with automatic > 10% overrun flags.
- G5. An audit-findings report (duplicates, stale invoices, unexplained transactions) where every
  finding is traceable to a real record in the underlying source data.
- G6. Full data provenance: every dollar figure is traceable to a cited real source, and that
  provenance is visible in the UI, not buried in a README.

### Non-goals

- NG1. **Not multi-tenant SaaS.** One business, one user, runs locally or on one small host.
- NG2. **No authentication, roles, or permissions.** Anyone who can reach the app can use it.
- NG3. **No payments integration.** The tool never moves money; it does not touch Stripe/ACH.
- NG4. **No live bank feed.** No Plaid / no bank API. Bank data is loaded from a file exactly as
  a real analyst would export a CSV from their bank portal. Refreshing data = re-running the
  build script against updated files.
- NG5. **Not an accounting system.** No general ledger, no journal entries, no trial balance,
  no financial statements.
- NG6. **No write path for financial data in v1.** The UI is read + analyze only. The only
  writable setting is reconciliation tolerance (see TRD). Marking an invoice paid happens in
  the source data, not the UI.

## 5. Core user stories

| # | As a… | I want… | so that… |
|---|-------|---------|----------|
| US-1 | finance analyst | to see all overdue client invoices sorted by days overdue, with amount and client | I can prioritize collections calls by risk. |
| US-2 | finance analyst | AR and AP aging summarized into 0–30 / 31–60 / 61–90 / 90+ buckets with totals and counts | I can see concentration of risk at a glance. |
| US-3 | finance analyst | to click an aging bucket and have the invoice/bill table filter to it | I can work one bucket at a time. |
| US-4 | finance analyst | to reconcile bank transactions against the AR/AP ledger automatically | I don't manually cross-check hundreds of line items. |
| US-5 | finance analyst | to see reconciliation output as Matched / Unmatched-bank / Unmatched-ledger buckets | I know exactly what needs human follow-up. |
| US-6 | finance analyst | to click a reconciliation exception and see the match logic's confidence score and the reason it did/didn't match | I can decide whether to accept it or investigate. |
| US-7 | finance analyst | to adjust the matching tolerance (amount %, date-window days) and re-run reconciliation live | I can tune it to this business's real payment messiness. |
| US-8 | finance analyst | a 13-week rolling cash flow forecast: projected collections minus scheduled vendor payments, with running ending balance | I know whether we can cover upcoming vendor payments. |
| US-9 | finance analyst | budget vs. actual per retreat with variance $ and %, flagging anything > 10% over | I can flag cost overruns before the next client is quoted. |
| US-10 | finance analyst | an audit-findings report: duplicate entries, invoices unpaid 90+ days, unexplained bank transactions | I catch errors before month-end close. |
| US-11 | finance analyst | DSO and DPO for the business, shown next to real industry benchmark ranges | I can tell whether our collection/payment timing is normal for the sector. |
| US-12 | hiring manager reviewing the project | a visible statement of which numbers are real and which are constructed | I can trust the data literacy behind the project. |

## 6. Functional requirements

- FR-1. Load clients, vendors, retreats, AR invoices, AP bills, and bank transactions from the
  SQLite database produced by the data pipeline.
- FR-2. Compute AR aging and AP aging into 0–30 / 31–60 / 61–90 / 90+ day buckets, as of a
  configurable "as-of" date (default: max date in the data).
- FR-3. Compute DSO = (AR balance / revenue over trailing period) × days, and DPO = (AP balance /
  spend over trailing period) × days. Denominator basis documented in the TRD.
- FR-4. Reconciliation engine matches each bank transaction to at most one invoice or bill using:
  amount tolerance (percent or absolute, configurable) **and** date window (± N days,
  configurable) **and** fuzzy string match on the transaction description (rapidfuzz).
- FR-5. Reconciliation output is bucketed: matched (with confidence 0–100 and method),
  unmatched bank transactions, unmatched invoices/bills.
- FR-6. Audit findings detects, at minimum: (a) duplicate invoices/bills (same counterparty +
  amount + near-date), (b) AR invoices unpaid 90+ days past due, (c) bank transactions with no
  plausible ledger match above a threshold ("unexplained"), (d) apparent double payments.
- FR-7. 13-week forecast: for each of the next 13 weeks, sum expected client collections (from
  open AR, using due date adjusted by observed average days-late) and scheduled vendor payments
  (from open AP due dates), and carry a running ending cash balance from a starting balance.
- FR-8. Budget vs. actual: for each retreat, compare budgeted category costs (venue / catering /
  travel / activities / other) against summed actual AP bills in that category, producing
  variance $ and %, with a boolean over-budget flag at the > 10% threshold.
- FR-9. Every displayed figure carries or links to its provenance (source dataset or cited price).
- FR-10. Reconciliation tolerance parameters are supplied at request time and are not hardcoded.

## 7. Success criteria

- SC-1. **Reconciliation coverage:** the engine auto-matches **≥ 85%** of bank transactions that
  have a true corresponding ledger entry, at the default tolerance, measured against a
  hand-labeled sample of ≥ 50 transactions. (Target is a range, tuned in Section 6 of the build;
  the exact number is reported in the README once measured, not assumed.)
- SC-2. **Cross-model agreement:** AR aging bucket totals, AP aging bucket totals, DSO, DPO, and
  every weekly cash-flow figure match between `retreat_finance_model.xlsx` and the API to the
  cent (≤ $1 rounding tolerance).
- SC-3. **Traceable findings:** 100% of audit findings link to specific real records in the raw
  source data. The README states how many real anomalies were found and what they were. No
  anomaly is planted.
- SC-4. **Benchmark realism:** the constructed business's DSO and DPO fall within the real
  industry benchmark ranges computed from SEC EDGAR data (Section 8D), or the deviation is
  explicitly explained in DATA_NOTES.md.
- SC-5. **Provenance:** a reviewer can start from any headline number in the UI and reach the
  cited source in ≤ 2 clicks.
- SC-6. **Reproducibility:** `build_dataset.py` run from a clean checkout reproduces the SQLite
  database deterministically (fixed random seed for any sampling).

## 8. What's real vs. constructed (data)

Full detail lives in [DATA_NOTES.md](../data/DATA_NOTES.md); summary here so the PRD is self-contained.

| Piece | Source | Real | Constructed |
|-------|--------|------|-------------|
| A. AR invoice amounts, dates, payment timing, duplicates, cancellations | UCI Online Retail II (UK online retailer, 2009–2011), CC BY 4.0 | dollar amounts, invoice numbers, order/ship dates, cancellations (credit notes), repeat-customer cadence | mapping each invoice to a client + retreat; relabeling GBP as USD 1:1 (documented) |
| B. Bank transactions: descriptions, timing, balances, duplicates, unexplained debits | Berka / PKDD'99 Discovery Challenge (anonymized Czech bank, 1993–1998) | transaction amounts, dates, running balances, operation/characterization codes, real duplicates and unexplained movements | mapping transactions to the retreat business's bank feed; currency relabeled (documented) |
| C. Vendor pricing (venue, catering, travel, activities) | Live web listings + published 2026 rate guides (Peerspace, hotel day-delegate rates, catering guides, GBTA travel index) — each URL + access date cited | the price points and ranges as published on the access date | applying them to a hypothetical headcount / night count / destination |
| D. DSO / DPO benchmarks | SEC EDGAR XBRL API — Marriott, Hilton, Live Nation, Global Business Travel Group 10-K filings | reported Accounts Receivable, Accounts Payable, Revenue; DSO/DPO computed from them | choosing these four as the comparison set; using revenue as the DPO denominator (proxy, documented) |
| E. Client + vendor names | — | industry categories are real (e.g. "Streaming Media", "Enterprise SaaS") | all client and vendor *names* are anonymized labels ("Client A", "Vendor 12 — Catering"). No real company is named as a counterparty. |

The retreat-business identity itself — that these records belong to a company running ~40
offsites a year — is the **construction**. No public dataset is natively labeled this way. This
is stated plainly in the README because showing the seam is better data practice than hiding it.

## 9. Assumptions & open questions

- A-1. No Kaggle credentials are available in the build environment, so datasets (A) and (B) use
  fully open, directly-downloadable equivalents (UCI Online Retail II; Berka PKDD'99) rather
  than the Kaggle datasets named as examples in the brief. Both are genuinely real and better
  documented. **Confirm this substitution is acceptable.**
- A-2. Currency: source amounts are treated as USD at 1:1 (no FX conversion applied), with the
  relabeling disclosed. Multi-currency is out of scope (NG / Section 10).
- A-3. The fictional business: US-based, ~40 domestic offsites/year, 30–60 attendees, 2–4 nights.
  Client terms net-30 or net-45 with a 50% deposit. **Confirm scale/shape.**
- A-4. "Revenue over trailing period" for DSO uses trailing 12 months of billed AR; DPO uses
  trailing 12 months of AP. Documented in TRD §NFR.

## 10. Out of scope

Multi-currency and FX; sales tax / VAT / withholding; payroll and headcount cost; real-time or
API bank integration; user accounts, auth, roles, permissions; general ledger / journal entries
/ financial statements; multi-entity or multi-tenant; mobile-first UI; email/Slack alerting;
write-back of financial data from the UI.
