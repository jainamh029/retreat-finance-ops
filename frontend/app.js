/* Retreat Finance Ops — vanilla dashboard. No framework, no build step.
   Implements docs/UIUX_SPEC.md against the Step-7 FastAPI endpoints. */

const API_BASE = (window.RETREAT_API_BASE || "http://localhost:8000").replace(/\/$/, "");

// ---------------------------------------------------------------------------
// tiny helpers
// ---------------------------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const usd = (n) => (n == null || isNaN(n)) ? "—"
  : (n < 0 ? "-$" : "$") + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const usd0 = (n) => (n == null || isNaN(n)) ? "—"
  : (n < 0 ? "-$" : "$") + Math.abs(Math.round(n)).toLocaleString("en-US");
const pct = (n) => (n == null || isNaN(n)) ? "—" : n.toFixed(1) + "%";
const daysAgo = (n) => n == null ? "—" : n < 0 ? `in ${-n}d` : `${n}d`;

const REDUCED_MOTION = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// count-up tween for dashboard KPI values. `fmt` is one of the formatters above (by name).
const FMT = { usd, usd0, pct, days: (n) => `${Number(n).toFixed(1)} days`, int: (n) => Math.round(n).toLocaleString("en-US") };
function countUp(el, to, fmtName, from = 0, dur = 480) {
  const fmt = FMT[fmtName] || String;
  if (REDUCED_MOTION || from === to || !Number.isFinite(to)) { el.textContent = fmt(to); return; }
  const t0 = performance.now();
  const step = (now) => {
    const p = Math.min(1, (now - t0) / dur);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = fmt(from + (to - from) * eased);
    if (p < 1) requestAnimationFrame(step); else el.textContent = fmt(to);
  };
  requestAnimationFrame(step);
}
// brief highlight when a value changes after a live re-run
function pulse(el) {
  if (!el || REDUCED_MOTION) return;
  el.classList.remove("flash");
  void el.offsetWidth;               // restart the animation
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 750);
}
// skeleton placeholders shown while a view's data loads
const skeletonCards = () =>
  `<div class="skel"><div class="skel-line" style="width:180px"></div>
     <div class="skel-card-row">${"<div class='skel-card'></div>".repeat(6)}</div>
     <div class="skel-table">${"<div class='skel-tr'><span class='skel-cell'></span><span class='skel-cell'></span></div>".repeat(8)}</div></div>`;
const skeletonTable = (cols = 7, rows = 12) =>
  `<div class="skel"><div class="skel-line" style="width:220px"></div>
     <div class="skel-table">${(`<div class='skel-tr'>${"<span class='skel-cell'></span>".repeat(cols)}</div>`).repeat(rows)}</div></div>`;

async function api(path) {
  const r = await fetch(API_BASE + path, { headers: { "Accept": "application/json" } });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch { /* noop */ }
    throw new Error(`${r.status} ${detail}`);
  }
  return r.json();
}

function banner(msg, retry) {
  const b = $("#banner");
  b.innerHTML = `<span>${esc(msg)}</span>`;
  if (retry) {
    const btn = document.createElement("button");
    btn.textContent = "retry";
    btn.onclick = () => { b.hidden = true; retry(); };
    b.appendChild(btn);
  }
  b.hidden = false;
}
const clearBanner = () => { $("#banner").hidden = true; };

// ---------------------------------------------------------------------------
// status system — color + icon + text label, ALWAYS (a11y: never colour alone)
// ---------------------------------------------------------------------------
const STATUS = {
  paid:    { cls: "pos", ic: "●", label: "Paid" },
  ontrack: { cls: "pos", ic: "●", label: "On track" },
  matched: { cls: "pos", ic: "●", label: "Matched" },
  open:    { cls: "neutral", ic: "–", label: "Open" },
  partial: { cls: "warn", ic: "▲", label: "Partial" },
  void:    { cls: "neutral", ic: "–", label: "Void" },
  due_soon:{ cls: "warn", ic: "▲", label: "Due soon" },
  exception:{ cls: "neg", ic: "■", label: "Exception" },
};
function statusChip(row) {
  let key = row.status;
  if (row.status === "open" && row.days_overdue > 0) {
    const s = row.days_overdue > 0
      ? { cls: "neg", ic: "■", label: `Overdue ${row.days_overdue}d` } : STATUS.open;
    return chip(s);
  }
  if (row.status === "open" && row.days_overdue != null && row.days_overdue > -7) return chip(STATUS.due_soon);
  return chip(STATUS[key] || STATUS.open);
}
const chip = (s) => `<span class="chip ${s.cls}"><span class="ic" aria-hidden="true">${s.ic}</span>${esc(s.label)}</span>`;

function confBand(c) {
  if (c >= 90) return { cls: "pos", ic: "●", label: `High ${c.toFixed(0)}` };
  if (c >= 75) return { cls: "warn", ic: "▲", label: `Review ${c.toFixed(0)}` };
  return { cls: "neg", ic: "■", label: `Low ${c.toFixed(0)}` };
}
function meter(c) {
  const on = Math.round(c / 20);
  const cls = c >= 90 ? "" : c >= 75 ? "warn" : "neg";
  return `<span class="meter ${cls}" aria-hidden="true">${[0, 1, 2, 3, 4].map(i => `<i class="${i < on ? "on" : ""}"></i>`).join("")}</span>`;
}
const AGE_CLASS = { current: "age-current", "0-30": "age-0", "31-60": "age-1", "61-90": "age-2", "90+": "age-3" };
const BUCKETS = ["current", "0-30", "31-60", "61-90", "90+"];
// mirrors backend.data_access._list_view: these columns sort DESC, the rest ASC
const SORT_DESC = new Set(["days_overdue", "amount"]);
const sortCls = (col, active) =>
  col === active ? (SORT_DESC.has(col) ? "sorted-desc" : "sorted-asc") : "";

// ---------------------------------------------------------------------------
// reconciliation tolerance config (persisted per viewer)
// ---------------------------------------------------------------------------
let CFG_DEFAULTS = null;         // from /api/config
let reconCfg = {};               // current working values
let reconCache = null;           // last /api/reconciliation/report payload

function loadCfg() {
  try {
    const raw = localStorage.getItem("retreat.reconCfg");
    if (raw) return JSON.parse(raw);
  } catch { /* private mode etc. */ }
  return null;
}
function saveCfg() {
  try { localStorage.setItem("retreat.reconCfg", JSON.stringify(reconCfg)); } catch { /* noop */ }
}
function cfgQuery() {
  const p = new URLSearchParams();
  for (const k of ["amount_tol_pct", "amount_tol_abs", "date_window_days", "min_name_score"]) {
    if (reconCfg[k] != null) p.set(k, reconCfg[k]);
  }
  const s = p.toString();
  return s ? "?" + s : "";
}
async function fetchRecon(force = false) {
  if (reconCache && !force) return reconCache;
  reconCache = await api("/api/reconciliation/report" + cfgQuery());
  return reconCache;
}

// ---------------------------------------------------------------------------
// router
// ---------------------------------------------------------------------------
const ROUTES = ["dashboard", "ar", "ap", "reconcile", "budget", "benchmarks"];
function parseHash() {
  const h = location.hash.replace(/^#\/?/, "") || "dashboard";
  const [path, qs] = h.split("?");
  const route = ROUTES.includes(path) ? path : "dashboard";
  return { route, params: new URLSearchParams(qs || "") };
}

async function render() {
  const { route, params } = parseHash();
  $$(".nav a").forEach(a => a.toggleAttribute("aria-current", a.dataset.nav === route) ||
    a.setAttribute("aria-current", a.dataset.nav === route ? "page" : "false"));
  $$(".nav a").forEach(a => { if (a.dataset.nav === route) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  const view = $("#view");
  view.innerHTML = route === "dashboard" ? skeletonCards()
    : (route === "ar" || route === "ap") ? skeletonTable(9, 14)
    : route === "reconcile" ? skeletonTable(8, 16)
    : `<div class="loading">loading ${esc(route)}…</div>`;
  try {
    clearBanner();
    if (route === "dashboard") await viewDashboard(view);
    else if (route === "ar") await viewLedger(view, "ar", params);
    else if (route === "ap") await viewLedger(view, "ap", params);
    else if (route === "reconcile") await viewReconcile(view, params);
    else if (route === "budget") await viewBudget(view, params);
    else if (route === "benchmarks") await viewBenchmarks(view);
    view.focus();
  } catch (e) {
    console.error(e);
    view.innerHTML = `<div class="panel">Couldn't load this view.<br><code>${esc(e.message)}</code></div>`;
    banner("Couldn't refresh — showing last state where possible.", render);
  }
}

// ---------------------------------------------------------------------------
// DASHBOARD
// ---------------------------------------------------------------------------
let _prevCards = {};
async function viewDashboard(view) {
  const d = await api("/api/dashboard/summary");
  const c = d.cards;
  const inb = (x) => x ? chip({ cls: "pos", ic: "●", label: "in range" }) : chip({ cls: "warn", ic: "▲", label: "outside" });

  view.innerHTML = `
    <h1>Dashboard</h1>
    <div class="card-row">
      ${card("Total AR outstanding", c.ar_outstanding, "usd0", `${c.ar_open_count} open invoices`)}
      ${card("Total AP outstanding", c.ap_outstanding, "usd0", `${c.ap_open_count} open bills`)}
      ${card("DSO", c.dso.value, "days", `benchmark ${c.dso.benchmark[0]}–${c.dso.benchmark[1]}d ${inb(c.dso.in_benchmark)}`)}
      ${card("DPO", c.dpo.value, "days", `benchmark ${c.dpo.benchmark[0]}–${c.dpo.benchmark[1]}d ${inb(c.dpo.in_benchmark)}`)}
      ${card("Cash position", c.cash_position, "usd0", `wk13 proj ${usd0(c.cash_position_week13)}`)}
      ${card("Overdue invoices", c.overdue_invoice_count, "int", usd0(c.overdue_amount) + " overdue", c.overdue_invoice_count > 0)}
    </div>

    <div class="panel-grid">
      <figure class="panel">
        <h2>AR / AP aging</h2>
        <div class="chart-wrap"><canvas id="agingChart"></canvas></div>
        <figcaption>Click a bar to filter the AR or AP table to that bucket.</figcaption>
        <table class="vh"><caption>Aging data</caption><tbody>${d.aging_chart.ar.map(b => `<tr><td>AR ${b.bucket}</td><td>${b.amount}</td></tr>`).join("")}${d.aging_chart.ap.map(b => `<tr><td>AP ${b.bucket}</td><td>${b.amount}</td></tr>`).join("")}</tbody></table>
      </figure>
      <figure class="panel">
        <h2>13-week cash flow forecast</h2>
        <div class="chart-wrap"><canvas id="cashChart"></canvas></div>
        <figcaption>${d.shortfall_weeks.length
          ? `<span class="chip neg"><span class="ic">■</span>projected shortfall: ${d.shortfall_weeks.join(", ")}</span>`
          : "No projected shortfall in the horizon."}</figcaption>
      </figure>
    </div>

    <div class="panel">
      <h2>Reconciliation & audit</h2>
      <p>
        <strong>${d.reconciliation.matched}</strong> matched ·
        ${d.reconciliation.unmatched_bank} unmatched bank ·
        ${d.reconciliation.unmatched_ledger} unmatched ledger &nbsp;|&nbsp;
        ground-truth recall <strong>${pct(d.reconciliation.ground_truth_recall_pct)}</strong>,
        precision ${pct(d.reconciliation.ground_truth_precision_pct)}
      </p>
      <p>${Object.entries(d.reconciliation.findings_by_type).map(([k, v]) =>
        `<span class="chip ${k === "double_payment" ? "neg" : k === "duplicate" ? "warn" : "neutral"}">
          <span class="ic">${k === "double_payment" ? "■" : "▲"}</span>${esc(k)} ${v}</span>`).join(" ")}
        &nbsp;<a href="#/reconcile">open reconciliation ▸</a></p>
    </div>`;

  drawAging($("#agingChart"), d.aging_chart);
  drawCash($("#cashChart"), d.cashflow_chart);

  // count-up each KPI from its previous value (0 on first load) to the new one
  const nextPrev = {};
  $$(".card .v[data-count]").forEach(el => {
    const to = parseFloat(el.dataset.count), fmt = el.dataset.fmt, key = el.dataset.key;
    countUp(el, to, fmt, _prevCards[key] ?? 0);
    nextPrev[key] = to;
  });
  _prevCards = nextPrev;
}

function card(k, value, fmtName, sub, alert) {
  const n = Number(value);
  return `<div class="card ${alert ? "alert" : ""}">
    <div class="k">${esc(k)}</div>
    <div class="v" data-count="${n}" data-fmt="${fmtName}" data-key="${esc(k)}">${(FMT[fmtName] || String)(n)}</div>
    <div class="sub">${sub}</div></div>`;
}

// ---------------------------------------------------------------------------
// charts (Chart.js if present, else a small table fallback)
// ---------------------------------------------------------------------------
const RAMP = { current: "#8b97a5", "0-30": "#6b7684", "31-60": "#b0851f", "61-90": "#d29922", "90+": "#f85149" };
const chartOpts = {
  responsive: true, maintainAspectRatio: false,
  plugins: { legend: { labels: { color: "#8b97a5", boxWidth: 10 } } },
  scales: {
    x: { ticks: { color: "#8b97a5" }, grid: { color: "#2b3542" } },
    y: { ticks: { color: "#8b97a5", callback: (v) => "$" + (v / 1000) + "k" }, grid: { color: "#2b3542" } },
  },
};
let _charts = {};
function drawAging(canvas, data) {
  if (!window.Chart) return fallbackTable(canvas, [["bucket", "AR", "AP"],
    ...BUCKETS.map(b => [b, data.ar.find(x => x.bucket === b).amount, data.ap.find(x => x.bucket === b).amount])]);
  _charts.aging?.destroy();
  _charts.aging = new Chart(canvas, {
    type: "bar",
    data: {
      labels: BUCKETS,
      datasets: [
        { label: "AR", data: BUCKETS.map(b => data.ar.find(x => x.bucket === b).amount), backgroundColor: BUCKETS.map(b => RAMP[b]) },
        { label: "AP", data: BUCKETS.map(b => data.ap.find(x => x.bucket === b).amount), backgroundColor: BUCKETS.map(b => RAMP[b] + "88") },
      ],
    },
    options: {
      ...chartOpts,
      onClick: (_e, els) => {
        if (!els.length) return;
        const bucket = BUCKETS[els[0].index];
        const which = els[0].datasetIndex === 0 ? "ar" : "ap";
        location.hash = `#/${which}?bucket=${encodeURIComponent(bucket)}`;
      },
    },
  });
}
function drawCash(canvas, weeks) {
  const labels = weeks.map(w => w.week_start.slice(5));
  if (!window.Chart) return fallbackTable(canvas, [["week", "collections", "pipeline", "payments", "ending"],
    ...weeks.map(w => [w.week_start, w.expected_collections, w.pipeline_collections, w.scheduled_payments, w.ending_balance])]);
  _charts.cash?.destroy();
  _charts.cash = new Chart(canvas, {
    data: {
      labels,
      datasets: [
        { type: "bar", label: "collections", data: weeks.map(w => w.expected_collections), backgroundColor: "#3fb95099", stack: "in" },
        { type: "bar", label: "pipeline", data: weeks.map(w => w.pipeline_collections), backgroundColor: "#4c8dff88", stack: "in" },
        { type: "bar", label: "payments", data: weeks.map(w => -w.scheduled_payments), backgroundColor: "#f8514999" },
        { type: "line", label: "ending balance", data: weeks.map(w => w.ending_balance), borderColor: "#e6edf3", pointRadius: 2, yAxisID: "y2" },
      ],
    },
    options: {
      ...chartOpts,
      scales: {
        ...chartOpts.scales,
        y2: { position: "right", ticks: { color: "#8b97a5", callback: (v) => "$" + (v / 1000) + "k" }, grid: { display: false } },
      },
    },
  });
}
function fallbackTable(canvas, rows) {
  const t = document.createElement("table");
  t.className = "grid";
  t.innerHTML = `<thead><tr>${rows[0].map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.slice(1).map(r => `<tr>${r.map((c, i) => `<td class="${i ? "num" : ""}">${i ? usd0(c) : esc(c)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  canvas.replaceWith(t);
}

// ---------------------------------------------------------------------------
// AR / AP LEDGER VIEW
// ---------------------------------------------------------------------------
async function viewLedger(view, kind, params) {
  const isAR = kind === "ar";
  const bucket = params.get("bucket") || "";
  const status = params.get("status") || "all";
  const sort = params.get("sort") || "days_overdue";
  const q = new URLSearchParams();
  if (bucket) q.set("bucket", bucket);
  if (status && status !== "all") q.set("status", status);
  if (sort) q.set("sort", sort);
  const cat = params.get("category") || "";
  if (!isAR && cat) q.set("category", cat);
  const cp = params.get("cp") || "";
  if (cp) q.set(isAR ? "client_id" : "vendor_id", cp);

  const [data, aging] = await Promise.all([
    api(`/api/${isAR ? "invoices" : "bills"}?` + q.toString()),
    api(`/api/aging/${kind}`),
  ]);

  const dateCol = isAR ? "invoice_date" : "bill_date";
  const setParam = (k, v) => {
    const p = new URLSearchParams(params); v ? p.set(k, v) : p.delete(k);
    location.hash = `#/${kind}?${p.toString()}`;
  };

  view.innerHTML = `
    <h1>${isAR ? "Accounts Receivable" : "Accounts Payable"}</h1>
    <div class="filterbar">
      <label>Status
        <select id="fStatus">
          ${["all", "open", "overdue", "paid", "void"].map(s => `<option ${s === status ? "selected" : ""}>${s}</option>`).join("")}
        </select>
      </label>
      ${isAR ? "" : `<label>Category
        <select id="fCat"><option value="">all</option>
        ${["venue", "catering", "travel", "activities", "other"].map(s => `<option ${s === cat ? "selected" : ""}>${s}</option>`).join("")}
        </select></label>`}
      <span class="bucket-chips" role="group" aria-label="aging bucket">
        ${BUCKETS.map(b => `<button data-bucket="${b}" aria-pressed="${b === bucket}">${b}</button>`).join("")}
        <button data-bucket="" aria-pressed="${!bucket}">clear</button>
      </span>
      <span class="active-filters">
        DSO/DPO ${aging.metric.value}d ${aging.metric.in_benchmark
          ? chip({ cls: "pos", ic: "●", label: "in range" }) : chip({ cls: "warn", ic: "▲", label: "outside" })}
      </span>
    </div>

    <div class="table-scroll">
      <table class="grid">
        <thead><tr>
          <th>${isAR ? "Invoice #" : "Bill #"}</th>
          <th>${isAR ? "Client" : "Vendor"}</th>
          ${isAR ? "<th>Role</th>" : "<th>Category</th>"}
          <th>Retreat</th>
          <th><button data-sort="${dateCol}" class="${sortCls(dateCol, sort)}">${isAR ? "Invoice date" : "Bill date"}</button></th>
          <th><button data-sort="due_date" class="${sortCls("due_date", sort)}">Due date</button></th>
          <th class="num"><button data-sort="amount" class="${sortCls("amount", sort)}">Amount</button></th>
          <th><button data-sort="status" class="${sortCls("status", sort)}">Status</button></th>
          <th class="num"><button data-sort="days_overdue" class="${sortCls("days_overdue", sort)}">Days overdue</button></th>
        </tr></thead>
        <tbody>
          ${data.rows.map(r => `
            <tr class="clickable" data-id="${esc(r.id)}">
              <td class="mono">${esc(r.id)}</td>
              <td>${esc(r.counterparty)}</td>
              <td class="mono">${esc(isAR ? r.line_role : r.category)}</td>
              <td class="mono">${esc(r.retreat_id)}</td>
              <td class="mono">${esc(r[dateCol])}</td>
              <td class="mono">${esc(r.due_date)}</td>
              <td class="num">${usd(r.amount)}</td>
              <td>${statusChip(r)}</td>
              <td class="num ${AGE_CLASS[r.aging_bucket] || ""}">${daysAgo(r.days_overdue)}</td>
            </tr>
            <tr class="detail" data-detail="${esc(r.id)}" hidden><td colspan="9">
              <div class="drawer"><dl>
                <dt>source</dt><dd>${esc(r.source_ref)}</dd>
                <dt>payment date</dt><dd>${esc(r.payment_date || "—")}</dd>
                <dt>aging bucket</dt><dd>${esc(r.aging_bucket)}</dd>
              </dl></div>
            </td></tr>`).join("")}
        </tbody>
      </table>
      <div class="tfoot">${data.count} of ${data.total} rows · ${usd(data.filtered_amount)}</div>
    </div>`;

  $("#fStatus").onchange = (e) => setParam("status", e.target.value === "all" ? "" : e.target.value);
  if ($("#fCat")) $("#fCat").onchange = (e) => setParam("category", e.target.value);
  $$(".bucket-chips button").forEach(b => b.onclick = () => setParam("bucket", b.dataset.bucket));
  $$("thead [data-sort]").forEach(b => b.onclick = () => setParam("sort", b.dataset.sort));
  $$("tr.clickable").forEach(tr => tr.onclick = () => {
    const d = $(`tr[data-detail="${CSS.escape(tr.dataset.id)}"]`);
    if (d) d.hidden = !d.hidden;
  });
}

// ---------------------------------------------------------------------------
// RECONCILIATION VIEW
// ---------------------------------------------------------------------------
async function viewReconcile(view, params) {
  const rep = await fetchRecon();
  const tab = params.get("tab") || "matched";
  const gt = rep.ground_truth;
  const st = rep.stats;

  const setTab = (t) => { location.hash = `#/reconcile?tab=${t}`; };

  view.innerHTML = `
    <h1>Reconciliation</h1>

    <div class="recon-headline">
      <div><div class="lbl">ground-truth recall</div><div class="big pos" id="recallBig">${pct(gt.recall_pct)}</div></div>
      <div><div class="lbl">precision</div><div class="big" id="precBig">${pct(gt.precision_pct)}</div></div>
      <div><div class="lbl">bank match rate</div><div class="big">${pct(st.match_rate_bank)}</div></div>
      <div><div class="lbl">matched</div><div class="big">${st.matched}</div></div>
      <div><div class="lbl">low-confidence (&lt;90)</div><div class="big">${st.low_confidence_matches}</div></div>
    </div>

    <div class="gt-note"><strong>How to read these numbers.</strong> ${esc(rep.ground_truth_note)}</div>

    <div class="tol-echo">tolerance: ±${rep.config.amount_tol_pct}% / ±$${rep.config.amount_tol_abs}
      / ±${rep.config.date_window_days}d / name ≥ ${rep.config.min_name_score}
      &nbsp;·&nbsp; <button class="linkish" id="openGear">adjust ⚙</button>
      &nbsp;·&nbsp; matched ${rep.matched_accounting.matched_total}
      = ${rep.matched_accounting.rediscovered_primary} correct
      + ${rep.matched_accounting.wrong_target_primary} wrong-target
      + ${rep.matched_accounting.duplicate_settlement_first_of_pair} dup-settlement first-of-pair</div>

    <div class="tabbar" role="tablist">
      ${[["matched", `Matched (${rep.matched.length})`],
         ["unmatched_bank", `Unmatched bank (${rep.unmatched_bank.length})`],
         ["unmatched_ledger", `Unmatched ledger (${rep.unmatched_ledger.length})`],
         ["findings", `Audit findings (${rep.findings.length})`]]
        .map(([k, lbl]) => `<button role="tab" data-tab="${k}" aria-selected="${k === tab}">${esc(lbl)}</button>`).join("")}
    </div>

    <div id="reconBody"></div>`;

  $("#openGear").onclick = openGear;
  $$('[data-tab]').forEach(b => b.onclick = () => setTab(b.dataset.tab));
  renderReconTab($("#reconBody"), rep, tab);
}

function renderReconTab(root, rep, tab) {
  if (tab === "matched") {
    const rows = [...rep.matched].sort((a, b) => a.confidence - b.confidence); // low-confidence first
    root.innerHTML = `<div class="table-scroll"><table class="grid">
      <thead><tr><th>Bank txn</th><th>Date</th><th class="num">Bank amount</th><th>→</th>
        <th>Ledger</th><th>Counterparty</th><th>Method</th><th class="num">Δ$ / Δd</th><th>Confidence</th></tr></thead>
      <tbody>${rows.map(m => {
        const b = confBand(m.confidence);
        const cls = m.confidence < 75 ? "lowconf vlow" : m.confidence < 90 ? "lowconf" : "";
        return `<tr class="${cls}">
          <td class="mono">${esc(m.transaction_id)}</td>
          <td class="mono">${esc(m.bank_date)}</td>
          <td class="num">${usd(m.bank_amount)}</td>
          <td>→</td>
          <td class="mono">${esc(m.matched_id)}</td>
          <td>${esc(m.counterparty)}</td>
          <td class="mono">${esc(m.match_method)}</td>
          <td class="num">${m.amount_delta.toFixed(2)} / ${m.date_delta_days}d</td>
          <td>${chip(b)} ${meter(m.confidence)}</td>
        </tr>`;
      }).join("")}</tbody></table>
      <div class="tfoot">sorted lowest-confidence first · ${rep.stats.low_confidence_matches} of ${rep.matched.length} below 90 (${(100 * rep.stats.low_confidence_matches / rep.matched.length).toFixed(0)}%) — amber = review, red = low</div>
    </div>`;
  } else if (tab === "unmatched_bank") {
    root.innerHTML = `<p class="banner-note">Click a row to see the closest ledger candidates and why each was rejected.</p>
      <div class="table-scroll"><table class="grid">
      <thead><tr><th>Txn</th><th>Date</th><th class="num">Amount</th><th>Description</th><th>Source</th></tr></thead>
      <tbody>${rep.unmatched_bank.map(t => `
        <tr class="clickable" data-txn="${esc(t.transaction_id)}">
          <td class="mono">${esc(t.transaction_id)}</td>
          <td class="mono">${esc(t.transaction_date)}</td>
          <td class="num">${usd(t.amount)}</td>
          <td>${esc(t.description)}</td>
          <td class="mono">${esc(t.source_ref.split(" ")[0])}</td>
        </tr>
        <tr class="detail" data-exc="${esc(t.transaction_id)}" hidden><td colspan="5"><div class="drawer">loading…</div></td></tr>`).join("")}
      </tbody></table></div>`;
    $$('tr.clickable[data-txn]').forEach(tr => tr.onclick = () => toggleException(tr.dataset.txn));
  } else if (tab === "unmatched_ledger") {
    root.innerHTML = `<div class="table-scroll"><table class="grid">
      <thead><tr><th>Ledger</th><th>Type</th><th>Counterparty</th><th class="num">Amount</th><th>Due</th><th>Status</th><th>Retreat</th></tr></thead>
      <tbody>${rep.unmatched_ledger.map(l => `<tr>
        <td class="mono">${esc(l.ledger_id)}</td><td class="mono">${esc(l.type)}</td>
        <td>${esc(l.counterparty)}</td><td class="num">${usd(l.amount)}</td>
        <td class="mono">${esc(l.due_date)}</td>
        <td>${chip(l.status === "open" ? { cls: "warn", ic: "▲", label: "unpaid" } : STATUS[l.status] || STATUS.open)}</td>
        <td class="mono">${esc(l.retreat_id)}</td></tr>`).join("")}</tbody></table></div>`;
  } else {
    const groups = {};
    for (const f of rep.findings) (groups[f.finding_type] ||= []).push(f);
    root.innerHTML = `
      <p class="banner-note">${rep.findings.length} findings, computed live at the current tolerance.
      <code>duplicate</code> (identical Berka rows) and <code>unexplained_txn</code> (real Berka
      interest / penalty / household rows) are genuine occurrences in the source data;
      <code>double_payment</code> are the injected operational-error scenarios. None are planted flags.</p>
      ${Object.entries(groups).map(([type, fs]) => `
        <h2>${esc(type)} <span class="chip neutral"><span class="ic">–</span>${fs.length}</span></h2>
        ${fs.map(f => `<div class="finding ${esc(f.severity)}">
          <div>${esc(f.description)}</div>
          <div class="ids">${(f.related_ids || []).map(esc).join(" · ")}</div>
        </div>`).join("")}`).join("")}`;
  }
}

async function toggleException(txnId) {
  const row = $(`tr[data-exc="${CSS.escape(txnId)}"]`);
  if (!row) return;
  row.hidden = !row.hidden;
  if (row.hidden || row.dataset.loaded) return;
  const box = $(".drawer", row);
  try {
    const d = await api(`/api/reconciliation/exceptions/${encodeURIComponent(txnId)}` + cfgQuery());
    row.dataset.loaded = "1";
    box.innerHTML = `
      <dl>
        <dt>transaction</dt><dd>${esc(d.transaction.description)}</dd>
        <dt>amount / date</dt><dd>${usd(d.transaction.amount)} · ${esc(d.transaction.transaction_date)}</dd>
        <dt>status</dt><dd>${chip(d.status === "matched" ? STATUS.matched : STATUS.exception)}</dd>
      </dl>
      <h3 style="margin-top:8px">Top candidates</h3>
      ${(d.candidates.length ? d.candidates : [{ verdict: "no ledger row within even the relaxed search window" }])
        .map(c => `<div class="cand">
          <div><strong class="mono">${esc(c.matched_id || "—")}</strong> ${esc(c.counterparty || "")}
            ${c.combined_score != null ? `· combined ${c.combined_score}` : ""}</div>
          ${c.amount_score != null ? `<div class="scorebits">
            <span>amount ${c.amount_score} (Δ ${c.amount_delta})</span>
            <span>date ${c.date_score} (Δ ${c.date_delta_days}d)</span>
            <span>name ${c.name_score}</span></div>` : ""}
          <div class="why">${esc(c.verdict || "")}</div>
        </div>`).join("")}`;
  } catch (e) {
    box.innerHTML = `<code>${esc(e.message)}</code>`;
  }
}

// ---------------------------------------------------------------------------
// BUDGET VS ACTUAL
// ---------------------------------------------------------------------------
async function viewBudget(view, params) {
  const list = await api("/api/retreats");
  const rid = params.get("r") || list.rows[0]?.retreat_id;
  const bva = rid ? await api(`/api/retreats/${encodeURIComponent(rid)}/budget-vs-actual`) : null;

  const varCls = (p) => p == null ? "" : p > 25 ? "var-neg" : p > 10 ? "var-warn" : p < 0 ? "var-pos" : "";
  const flag = (over, p) => over
    ? `<span class="chip neg"><span class="ic">■</span>over ${p.toFixed(0)}%</span>`
    : `<span class="chip neutral"><span class="ic">–</span>ok</span>`;

  view.innerHTML = `
    <h1>Budget vs Actual</h1>
    <div class="panel-grid">
      <div class="panel">
        <h2>All retreats <span class="chip neutral"><span class="ic">–</span>${list.count}</span></h2>
        <div class="table-scroll"><table class="grid">
          <thead><tr><th>Retreat</th><th>Client</th><th class="num">Budget</th><th class="num">Actual</th><th class="num">Var %</th><th>Flag</th></tr></thead>
          <tbody>${list.rows.map(r => `<tr class="clickable" data-r="${esc(r.retreat_id)}">
            <td class="mono">${esc(r.retreat_id)}</td><td>${esc(r.client_name)}</td>
            <td class="num">${usd0(r.budget_total)}</td><td class="num">${usd0(r.actual_total)}</td>
            <td class="num ${varCls(r.variance_pct)}">${r.has_actuals ? pct(r.variance_pct) : "—"}</td>
            <td>${r.has_actuals ? flag(r.over_budget, r.variance_pct) : `<span class="chip neutral"><span class="ic">–</span>no bills</span>`}</td>
          </tr>`).join("")}</tbody></table></div>
      </div>
      <div class="panel" id="bvaDetail"></div>
    </div>`;

  $$('tr[data-r]').forEach(tr => tr.onclick = () => { location.hash = `#/budget?r=${encodeURIComponent(tr.dataset.r)}`; });
  if (!bva) return;
  const t = bva.total;
  $("#bvaDetail").innerHTML = `
    <h2>${esc(bva.retreat.retreat_id)} — ${esc(bva.retreat.client_name)}</h2>
    <p class="mono" style="font-family:var(--mono);font-size:12px;color:var(--text-dim)">
      ${esc(bva.retreat.destination)} · ${bva.retreat.headcount} pax · ${esc(bva.retreat.start_date)} → ${esc(bva.retreat.end_date)}</p>
    <p>Total budget ${usd0(t.budget)} · actual ${usd0(t.actual)} ·
      <span class="${varCls(t.variance_pct)}">${pct(t.variance_pct)}</span> ${flag(t.over_10pct, t.variance_pct)}</p>
    <div class="table-scroll"><table class="grid">
      <thead><tr><th>Category</th><th class="num">Budget</th><th class="num">Actual</th><th class="num">Var $</th><th class="num">Var %</th><th>Flag</th></tr></thead>
      <tbody>${bva.lines.map(l => `<tr>
        <td>${esc(l.category)} ${l.price_source
          ? `<span class="src-info" title="${esc(l.price_source.basis)} — ${esc(l.price_source.source)}, ${esc(l.price_source.accessed)}">ⓘ</span>` : ""}</td>
        <td class="num">${usd0(l.budget)}</td><td class="num">${usd0(l.actual)}</td>
        <td class="num ${l.variance_abs > 0 ? "var-warn" : "var-pos"}">${usd0(l.variance_abs)}</td>
        <td class="num ${varCls(l.variance_pct)}">${pct(l.variance_pct)}</td>
        <td>${l.actual > 0 ? flag(l.over_10pct, l.variance_pct) : `<span class="chip neutral"><span class="ic">–</span>—</span>`}</td>
      </tr>`).join("")}</tbody></table></div>
    ${bva.lines.some(l => l.price_source) ? `<figcaption>ⓘ budget basis is a cited 2026 price — hover for source.</figcaption>` : ""}`;
}

// ---------------------------------------------------------------------------
// BENCHMARKS
// ---------------------------------------------------------------------------
async function viewBenchmarks(view) {
  const b = await api("/api/benchmarks");
  view.innerHTML = `
    <h1>DSO / DPO benchmarks</h1>
    <div class="panel">
      <p>SEC EDGAR 10-K figures (FY2022–25). Applied range for this business:
        <strong>DSO ${b.applied_ranges.dso[0]}–${b.applied_ranges.dso[1]}d</strong>,
        <strong>DPO ${b.applied_ranges.dpo[0]}–${b.applied_ranges.dpo[1]}d</strong>
        (SEC observed DSO ${b.sec_observed.dso[0]}–${b.sec_observed.dso[1]}, DPO ${b.sec_observed.dpo[0]}–${b.sec_observed.dpo[1]}).</p>
      <p style="color:var(--text-dim);font-size:12px">${esc(b.method)}</p>
      <div class="table-scroll"><table class="grid">
        <thead><tr><th>Company</th><th>Ticker</th><th>Period</th><th class="num">AR $M</th><th class="num">AP $M</th><th class="num">Revenue $M</th><th class="num">DSO</th><th class="num">DPO</th></tr></thead>
        <tbody>${b.rows.map(r => `<tr>
          <td>${esc(r.company_name)}</td><td class="mono">${esc(r.ticker)}</td><td class="mono">${esc(r.fiscal_period)}</td>
          <td class="num">${(r.ar_balance / 1e6).toFixed(0)}</td>
          <td class="num">${(r.ap_balance / 1e6).toFixed(0)}</td>
          <td class="num">${(r.revenue / 1e6).toFixed(0)}</td>
          <td class="num">${r.computed_dso}</td><td class="num">${r.computed_dpo}</td>
        </tr>`).join("")}</tbody></table></div>
    </div>`;
}

// ---------------------------------------------------------------------------
// gear popover
// ---------------------------------------------------------------------------
function openGear() {
  $("#tolPct").value = reconCfg.amount_tol_pct;
  $("#tolAbs").value = reconCfg.amount_tol_abs;
  $("#tolDays").value = reconCfg.date_window_days;
  $("#tolName").value = reconCfg.min_name_score;
  $("#gearPopover").hidden = false;
  $("#popoverScrim").hidden = false;
  $("#gearBtn").setAttribute("aria-expanded", "true");
  $("#tolDays").focus();
}
function closeGear() {
  $("#gearPopover").hidden = true;
  $("#popoverScrim").hidden = true;
  $("#gearBtn").setAttribute("aria-expanded", "false");
}
async function runRecon() {
  reconCfg = {
    amount_tol_pct: +$("#tolPct").value,
    amount_tol_abs: +$("#tolAbs").value,
    date_window_days: +$("#tolDays").value,
    min_name_score: +$("#tolName").value,
  };
  saveCfg();
  closeGear();
  const body = $("#view");
  const prev = { r: $("#recallBig")?.textContent, p: $("#precBig")?.textContent };
  body.style.opacity = REDUCED_MOTION ? "1" : ".55";
  body.style.transition = "opacity 160ms ease";
  try {
    await fetchRecon(true);
    if (parseHash().route !== "reconcile") location.hash = "#/reconcile";
    else await render();
    // pulse the headline numbers if they moved
    const rb = $("#recallBig"), pb = $("#precBig");
    if (rb && rb.textContent !== prev.r) pulse(rb);
    if (pb && pb.textContent !== prev.p) pulse(pb);
  } catch (e) {
    banner("Reconciliation re-run failed: " + e.message);
  } finally {
    body.style.opacity = "1";
  }
}

// ---------------------------------------------------------------------------
// provenance footer
// ---------------------------------------------------------------------------
async function loadProvenance() {
  try {
    const p = await api("/api/provenance");
    $("#provSources").textContent = p.sources.map(s => s.label).join(" · ");
    const panel = $("#provPanel");
    panel.innerHTML = `
      <div class="caveat">${esc(p.headline_caveat)}</div>
      <table><tbody>${p.sources.map(s => `<tr>
        <td><strong>${esc(s.label)}</strong> <span class="chip neutral"><span class="ic">–</span>${esc(s.kind)}</span></td>
        <td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a><br>
          <span style="color:var(--text-faint)">accessed ${esc(s.accessed)} — ${esc(s.notes)}</span></td>
      </tr>`).join("")}</tbody></table>
      <p style="margin-top:8px">Full detail: <code>${esc(p.data_notes)}</code></p>`;
  } catch { /* footer stays with its static fallback text */ }
}

// ---------------------------------------------------------------------------
// boot (with Render free-tier cold-start handling)
// ---------------------------------------------------------------------------
async function waitForBackend() {
  const view = $("#view");
  for (let attempt = 0, delay = 1500; attempt < 12; attempt++) {
    try {
      const h = await Promise.race([
        api("/api/health"),
        new Promise((_, rej) => setTimeout(() => rej(new Error("slow")), attempt === 0 ? 2500 : 15000)),
      ]);
      $("#asOf").textContent = "data as of " + h.data_as_of;
      return true;
    } catch {
      view.innerHTML = `<div class="coldstart">
        <div class="spinner" aria-hidden="true"></div>
        <div class="cs-title">Backend is waking up</div>
        <div class="cs-bar" aria-hidden="true"></div>
        <div class="cs-sub">Render's free tier sleeps after ~15&nbsp;min idle. The first request
          takes 30–50&nbsp;s to spin it back up — this isn't an error. Retrying automatically.</div>
        <div class="cs-attempt">attempt ${attempt + 1} / 12</div></div>`;
      await new Promise(r => setTimeout(r, delay));
      delay = Math.min(delay * 1.6, 8000);
    }
  }
  view.innerHTML = `<div class="panel">Can't reach the API at <code>${esc(API_BASE)}</code>.
    Locally: <code>uvicorn backend.api.main:app --port 8000</code> then reload.
    Or set a different backend URL from the ⚙ gear.</div>`;
  return false;
}

async function boot() {
  // gear wiring
  $("#gearBtn").onclick = () => $("#gearPopover").hidden ? openGear() : closeGear();
  $("#popoverScrim").onclick = closeGear;
  $("#tolReset").onclick = () => {
    reconCfg = { ...CFG_DEFAULTS };
    $("#tolPct").value = reconCfg.amount_tol_pct; $("#tolAbs").value = reconCfg.amount_tol_abs;
    $("#tolDays").value = reconCfg.date_window_days; $("#tolName").value = reconCfg.min_name_score;
  };
  $("#tolRun").onclick = runRecon;
  $$("#gearPopover .presets .chip").forEach(b => b.onclick = () => { $("#tolDays").value = b.dataset.days; runRecon(); });
  // backend API base override (persisted; survives reload) — see index.html resolution order
  $("#apiBase").value = API_BASE;
  $("#apiSave").onclick = () => {
    const v = $("#apiBase").value.trim().replace(/\/$/, "");
    try { v ? localStorage.setItem("retreat.apiBase", v) : localStorage.removeItem("retreat.apiBase"); } catch { /* noop */ }
    location.reload();
  };
  $("#apiReset").onclick = () => {
    try { localStorage.removeItem("retreat.apiBase"); } catch { /* noop */ }
    location.reload();
  };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeGear(); });
  $("#provToggle").onclick = () => {
    const p = $("#provPanel");
    p.hidden = !p.hidden;
    $("#provToggle").setAttribute("aria-expanded", String(!p.hidden));
  };

  if (!(await waitForBackend())) return;

  const cfgResp = await api("/api/config");
  CFG_DEFAULTS = {
    amount_tol_pct: cfgResp.reconciliation.defaults.amount_tol_pct,
    amount_tol_abs: cfgResp.reconciliation.defaults.amount_tol_abs,
    date_window_days: cfgResp.reconciliation.defaults.date_window_days,
    min_name_score: cfgResp.reconciliation.defaults.min_name_score,
  };
  reconCfg = loadCfg() || { ...CFG_DEFAULTS };

  loadProvenance();
  window.addEventListener("hashchange", render);
  if (!location.hash) location.hash = "#/dashboard";
  await render();
}

boot();
