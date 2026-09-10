# UI/UX Spec — Retreat Finance Ops

**Status:** Draft for review
**Last updated:** 2026-09-10
**Related:** [PRD.md](PRD.md) · [TRD.md](TRD.md)

---

## 1. Design direction

An **internal finance-ops tool**, not a trading dashboard and not a marketing site. It should
feel like something a finance analyst keeps open in a tab all day: dense, legible, fast, quiet.

**One theme: dark "finance-ops".** Committing to dark only (no light/dark toggle — that is scope
creep per the brief). Rationale: consistent with the trading-terminal palette of my Options
Pricing Engine, and high-contrast dark reads well for dense numeric tables under long use. The
palette is desaturated slate, not neon — this is an ops tool, so the only saturated color on
screen is a status signal.

### Palette (tokens)

| Token | Hex | Use |
|-------|-----|-----|
| `--bg` | `#0f1419` | app background |
| `--surface` | `#171d26` | cards, table surface |
| `--surface-2` | `#1e2631` | raised elements, header, hover row |
| `--border` | `#2b3542` | hairlines, dividers |
| `--text` | `#e6edf3` | primary text |
| `--text-dim` | `#8b97a5` | labels, secondary |
| `--text-faint` | `#5c6773` | captions, provenance |
| `--accent` | `#4c8dff` | links, active nav, focus ring, selected filter |
| `--pos` | `#3fb950` | status: paid / on-track / matched |
| `--warn` | `#d29922` | status: due soon / approaching overdue / low confidence |
| `--neg` | `#f85149` | status: overdue / exception / over-budget |
| `--neutral` | `#8b97a5` | status: no action needed / N/A |

### Status system (never color-only)

Every status is **icon + text label + color**, so it survives color-blindness and grayscale.

| Meaning | Color | Icon (unicode / inline SVG) | Label text |
|---------|-------|------|------------|
| Paid / on-track / matched | `--pos` | ● check | `Paid` / `On track` / `Matched` |
| Due soon / approaching (≤ 7 days to due) / low-confidence match | `--warn` | ▲ | `Due soon` / `Review` |
| Overdue / reconciliation exception / >10% over budget | `--neg` | ■ | `Overdue 34d` / `Exception` / `Over 18%` |
| No action needed / not applicable | `--neutral` | – | `—` |

Aging buckets use a fixed 4-step ramp (not a red gradient): `0–30` neutral, `31–60` warn-dim,
`61–90` warn, `90+` neg. The same ramp is used in the bar chart and the table cell tint.

### Typography

- UI text: system sans (`-apple-system, Segoe UI, Roboto, …`).
- **All money, dates, counts, percentages, IDs, and confidence scores: monospace**
  (`ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace`), tabular figures, right-aligned
  in tables. This is the single most important type rule — columns of numbers must align.
- Sizes: 13px base, 12px table body, 11px captions/provenance, 20/16px card metrics.

### Density

Compact. 28–32px table rows, 8px cell padding. This is a power-user tool; whitespace is not the
priority, scannability is.

## 2. Layout shell

```
┌────────────────────────────────────────────────────────────────────────────┐
│  RETREAT FINANCE OPS          data as of 2011-12-09      [⚙ tolerance]       │  ← header bar
├───────────┬────────────────────────────────────────────────────────────────┤
│ Dashboard │                                                                │
│ AR        │                      < main view area >                        │
│ AP        │                                                                │
│ Reconcile │                                                                │
│ Budget    │                                                                │
│ Benchmarks│                                                                │
├───────────┴────────────────────────────────────────────────────────────────┤
│ Data: UCI Online Retail II · Berka PKDD'99 · SEC EDGAR · web-cited pricing  │  ← provenance
│ [what's real vs constructed ▸]                                              │     footer
└────────────────────────────────────────────────────────────────────────────┘
```

- **Left nav:** 6 items, icon + label, active item marked with `--accent` left border + text,
  and `aria-current="page"`. Collapses to a top row of tabs under 900px.
- **Header:** product name, "data as of <date>" (from `/api/health`), and the **⚙ tolerance**
  button (opens the config popover, §7).
- **Provenance footer:** always visible, one line of source names; the
  "what's real vs constructed ▸" link expands an in-page panel (content from `/api/provenance`,
  mirrors DATA_NOTES.md). This is deliberate — provenance is a feature, so it lives in the chrome.

## 3. Screen: Dashboard / Summary

Purpose: the 5-second morning glance (PRD G1).

**Row of 6 metric cards** (`/api/dashboard/summary`):

| Card | Value | Sub-line |
|------|-------|----------|
| Total AR outstanding | `$X` mono | `N open invoices` |
| Total AP outstanding | `$X` mono | `N open bills` |
| DSO | `X days` mono | `benchmark 45–75d` · green tick if inside, warn ▲ if outside |
| DPO | `X days` mono | `benchmark 20–40d` (proxy) · same in/out marker |
| Cash position | `$X` mono | `projected wk13: $Y` with ▲/▼ |
| Overdue invoices | `N` mono, `--neg` if > 0 | `$X overdue` |

**Two charts below:**

1. **Aging bar chart** — grouped bars, AR and AP side by side, x = 4 buckets, y = $.
   Bucket colors = the aging ramp (§1). **Clicking a bar** navigates to AR (or AP) view
   pre-filtered to that bucket (PRD US-3).
2. **13-week cash flow line chart** (`/api/cashflow/forecast`) — two series (expected
   collections, scheduled payments) as bars + `ending_balance` as a line on a secondary axis.
   A horizontal zero line; any week where `ending_balance < 0` gets a `--neg` marker and is
   called out in a caption ("projected shortfall in week of Mar 3").

Below charts: **"Audit findings" strip** — count by severity with a link into the Reconcile
view's findings panel. If `high` severity > 0, the strip is `--neg`.

## 4. Screen: AR view

Purpose: PRD US-1, US-3.

- **Filter bar:** status (`open` / `paid` / `overdue` / `all`), client (select), date range,
  and a bucket chip row (`0–30 | 31–60 | 61–90 | 90+ | clear`). Active filters shown as
  removable chips. Bucket chip state is reflected in the URL (`?bucket=61-90`) so it's linkable
  from the dashboard chart.
- **Table** (`/api/invoices`), columns:
  `Invoice # · Client · Retreat · Invoice date · Due date · Amount · Status · Days overdue`
  - money + dates + days monospace, right-aligned
  - Status cell = status chip (§1.3)
  - Days-overdue cell tinted with the aging ramp
  - default sort: days overdue desc (so collections priority is the top of the list)
  - every column header sortable; sort state in URL
- **Row click:** expands an inline detail drawer — full invoice fields, linked retreat, and the
  `source_ref` provenance ("built from UCI Online Retail II invoice 536365, 2010-12-01").
- Footer of table: filtered count + summed amount.

## 5. Screen: AP view

Identical structure to AR (`/api/bills`), columns:
`Bill # · Vendor · Category · Bill date · Due date · Amount · Status · Days overdue`.
Filters: status, vendor, **category** (venue / catering / travel / activities / other), date range,
bucket chips. Same sorting, drawer, provenance, and footer totals.

## 6. Screen: Reconciliation

Purpose: PRD US-4, US-5, US-6, US-7.

**Layout:** tabbed (not 3 columns — columns get cramped on a laptop). Tab bar:
`Matched (N) · Unmatched bank (N) · Unmatched ledger (N) · Audit findings (N)`.
A stats strip above the tabs: `match rate 87% · 142 matched · 11 bank unmatched · 6 ledger
unmatched · tolerance: ±1% / ±$5 / ±5d / name≥80` (the tolerance summary is a live echo of the
config popover).

- **Matched tab:** table — `Bank txn (date, desc, amount) · → · Ledger item (type, id, counterparty,
  amount) · Confidence · Method`. Confidence rendered as a mono number + a 5-block meter;
  < 90 gets the `--warn` ▲ and sorts to the top of the matched list ("verify these").
- **Unmatched bank tab:** table of bank transactions with no accepted match. Row click →
  **exception panel** (`/api/reconciliation/exceptions/{txn_id}`): shows the transaction and its
  top 3 candidate ledger items, each with a **score breakdown**: amount Δ ($ and %), date Δ
  (days), name score (0–100), and the sentence "rejected: date Δ 9d exceeds ±5d window"
  (PRD US-6).
- **Unmatched ledger tab:** invoices/bills with no bank movement — i.e. genuinely unpaid, or paid
  outside the data window. Each row shows `days since due` so stale ones are obvious.
- **Audit findings tab:** grouped by `finding_type` (Duplicate / Stale 90+ / Unexplained txn /
  Possible double-payment). Each finding: severity chip, description, the `related_ids` as links
  into AR/AP/Reconcile, and `source_refs` linking to the underlying raw records (PRD SC-3). A
  banner at top: "N findings, all traceable to real records in the source data — none planted."

## 7. Config popover (⚙ tolerance)

Opened from the header. Small form, matches the "gear" pattern from my Options Pricing Engine:

```
Matching tolerance
  Amount tolerance   [ 1.0 ] %     and  [ 5.00 ] $ absolute
  Date window        [ 5 ] days ±
  Min name score     [ 80 ] / 100
  [ Reset defaults ]              [ Re-run reconciliation ]
```

- Changing a field and hitting "Re-run" re-requests `/api/reconciliation/report` with the new
  params and re-renders the Reconcile view + the dashboard match-rate. No page reload.
- Values persist in `localStorage` (wrapped in try/catch) so the analyst's preferred tolerance
  survives a refresh; falls back to `config.py` defaults if unset or unreadable.
- The current values are always echoed in the Reconcile stats strip so it's never ambiguous what
  produced the numbers on screen.

## 8. Screen: Budget vs Actual

Purpose: PRD US-9.

- **Retreat picker** (list or select) → per-retreat table (`/api/retreats/{id}/budget-vs-actual`):
  `Category · Budget · Actual · Variance $ · Variance % · Flag`
  rows: venue / catering / travel / activities / other / **Total**.
  - Variance % cell tinted: within ±10% neutral, 10–25% over `--warn`, > 25% over `--neg`;
    under budget shown `--pos` but not alarmed.
  - Flag column = ■ `Over 18%` style chip when `over_10pct`.
- Each budget cell has a tiny info affordance → tooltip/popover with the **cited price source**
  ("Venue budget: Peerspace large offsite venue $617/hr × 8h × 3 days; peerspace.com/…, accessed
  2026-09-10").
- Above the table: retreat meta (client, destination, headcount, dates) and a
  `budget_total vs actual_total` headline with the same variance treatment.
- An "all retreats" overview table is the landing state: one row per retreat, `variance %` and
  flag, sortable by overrun — so the worst overruns are one glance away.

## 9. Screen: Benchmarks

Purpose: PRD US-11, transparency.

- Table from `/api/benchmarks`: `Company · Ticker · Fiscal period · AR ($M) · AP ($M) ·
  Revenue ($M) · DSO · DPO · Source` (source = link to the SEC filing / EDGAR concept URL).
- Above it: the derived **benchmark ranges** actually used by the app (DSO 45–75d, DPO 20–40d
  proxy — final numbers set by the data build) and a one-paragraph note on method (revenue as
  DPO denominator proxy; why these four comparables).
- The business's own DSO/DPO plotted as a marker on a simple range strip against each benchmark.

## 10. Interaction requirements (consolidated)

- Aging bar click → filtered AR/AP table (US-3). ✔ §3, §4
- Reconciliation exception click → confidence + match-reason breakdown (US-6). ✔ §6
- Tolerance config control → live re-run against API (US-7). ✔ §7
- All tables: header-click sort, URL-reflected filters/sort (shareable, back-button safe).
- Row-click detail drawers everywhere a `source_ref` exists → provenance always ≤ 2 clicks (PRD SC-5).
- Loading: skeleton rows for tables, shimmer for cards. Never a blank screen.
- Cold start (Render free tier): if `/api/health` doesn't respond in ~2s, full-screen
  "backend is waking up…" with spinner + auto-retry (exponential backoff, cap 60s), then load
  normally. Mirrors the Options Pricing Engine behavior. (TRD §9)
- Errors: non-blocking top banner `Couldn't refresh <thing> — showing last loaded data`, with a
  manual retry button. Last good render stays on screen.

## 11. Responsive behavior

- **Target: laptop (1280–1440px).** Fully supported, primary.
- **1024–1280:** left nav stays; charts stack to full width; tables scroll horizontally inside
  their own container (page never scrolls sideways).
- **< 1024:** left nav → top tab strip. Cards go 2-up then 1-up. Config popover becomes a
  full-width sheet.
- **< 640 (phone):** not a priority (internal tool) but must not break — single column, tables
  in horizontal-scroll containers, all actions still reachable. No feature removed, just stacked.

## 12. Accessibility

- Contrast: body text `--text` on `--surface` ≥ 7:1; status colors on their chips ≥ 4.5:1;
  chips always carry text + icon so color is never the sole signal (§1.3).
- Keyboard: every interactive element focusable in DOM order; visible `--accent` focus ring
  (never `outline:none` without a replacement). Tables navigable with arrow keys within a row
  group; sort headers are `<button>`s; drawers are focus-trapped and `Esc`-closable.
- Semantics: one `<h1>`, sectioned `<h2>`s per screen; tables are real `<table>` with `<th
  scope>`; charts have a `<figcaption>` and an adjacent visually-hidden data table so the
  numbers aren't chart-only.
- `prefers-reduced-motion`: disable shimmer/skeleton animation and chart entrance transitions.
- Live regions: the cold-start status and the error banner are `aria-live="polite"`.

## 13. Frontend build notes (ties to TRD §6)

- `index.html` (shell + nav + footer), `style.css` (tokens + components), `app.js` (router +
  fetch layer + render functions). Optional `charts.js` module wrapping the CDN chart lib with a
  table fallback.
- Client-side hash router (`#/ar`, `#/reconcile`, …) so GitHub Pages needs no server rewrites.
- `API_BASE` single constant, swapped at deploy time by the Pages workflow.
- No framework, no bundler, no `node_modules`.
