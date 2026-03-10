/* FinSense v2.0 — Full Dashboard Application */
let actionChart;
let confidenceChart;
let technicalChart;
let factorChart;
let incidentChart;
let cmpChart1;
let cmpChart2;
let cmpChart3;
let latestRows = [];
let latestTickers = [];
let latestDetailPayload = null;
let latestAnalogs = [];
let latestBacktestRows = [];
let forwardUniverseTickers = [];
let waterRotateTimer = null;
let tableSortField = "confidence";
let tableSortDir = "desc";
let btSortField = "ticker";
let btSortDir = "asc";

const WATER_MODES = [
  { icon: "⛵", lines: ["Sailing through tickers...", "Riding market currents...", "Navigating alpha waters..."] },
  { icon: "🏄", lines: ["Surfing trend waves...", "Catching momentum swells...", "Reading wave patterns..."] },
  { icon: "🏄‍♂️", lines: ["Wakeboarding through signals...", "Carving across volatility wakes...", "Holding balance in chop..."] },
  { icon: "🚣", lines: ["Rowing through fundamentals...", "Pacing through market currents...", "Steady strokes, steady alpha..."] },
  { icon: "🛶", lines: ["Canoeing through regimes...", "Gliding over macro flow...", "Scanning quieter channels..."] },
];

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------
function updateWaterBannerText(forceText = "") {
  const iconEl = document.getElementById("waterSportIcon");
  const textEl = document.getElementById("workText");
  const idx = Math.floor(Date.now() / 1900) % WATER_MODES.length;
  const mode = WATER_MODES[idx];
  iconEl.textContent = mode.icon;
  if (forceText) { textEl.textContent = forceText; return; }
  const msgIdx = Math.floor(Date.now() / 2300) % mode.lines.length;
  textEl.textContent = mode.lines[msgIdx];
}

function setWorking(isWorking, text = "Crunching market data...") {
  const banner = document.getElementById("workBanner");
  banner.classList.toggle("hidden", !isWorking);
  if (!isWorking) {
    if (waterRotateTimer) { clearInterval(waterRotateTimer); waterRotateTimer = null; }
    return;
  }
  updateWaterBannerText(text);
  if (!waterRotateTimer) waterRotateTimer = setInterval(() => updateWaterBannerText(), 1700);
}

function fmtPct(value) { const sign = value > 0 ? "+" : ""; return `${sign}${value.toFixed(2)}%`; }
function badge(action) { return `<span class="chip ${action.toLowerCase()}">${action}</span>`; }
function qualityBadge(q) { return `<span class="chip ${q}">${q}</span>`; }
function setStatus(text) { document.getElementById("statusLine").textContent = text; }
function setDetailStatus(msg) { document.getElementById("detailStatus").textContent = msg; }
function setForwardStatus(msg) { const el = document.getElementById("forwardStatus"); if (el) el.textContent = msg; }
function setBacktestStatus(msg) { const el = document.getElementById("backtestStatus"); if (el) el.textContent = msg; }
function setBacktestRunning(isRunning) {
  const btn = document.getElementById("runBacktestBtn");
  if (!btn) return;
  btn.disabled = isRunning;
  btn.textContent = isRunning ? "Running..." : "Run Backtest";
}
function sanitizeTickerInput(raw) { return String(raw || "").trim().toUpperCase().replace(/[^A-Z0-9.\-]/g, "").slice(0, 10); }
function normalizeActionLabel(action) {
  const raw = String(action || "").toUpperCase();
  if (raw.includes(".")) return raw.split(".").slice(-1)[0];
  return raw || "HOLD";
}
function dedupeUpper(items) {
  const seen = new Set(); const out = [];
  (items || []).forEach((x) => { const tk = sanitizeTickerInput(x); if (!tk || seen.has(tk)) return; seen.add(tk); out.push(tk); });
  return out;
}
function defaultBacktestDate() { const d = new Date(); d.setDate(d.getDate() - 60); return d.toISOString().slice(0, 10); }
function backtestStatusBadge(status) {
  if (status === "ok") return `<span class="chip hold">OK</span>`;
  if (status === "no_forward_data") return `<span class="chip warn">No Forward Data</span>`;
  return `<span class="chip sell">Error</span>`;
}

// ---------------------------------------------------------------------------
// Backtest filters & sorting
// ---------------------------------------------------------------------------
function getBacktestFilters() {
  return {
    ticker: String(document.getElementById("btFilterTicker")?.value || "").trim().toUpperCase(),
    status: String(document.getElementById("btFilterStatus")?.value || "ALL"),
    prediction: String(document.getElementById("btFilterPrediction")?.value || "ALL"),
    outcome: String(document.getElementById("btFilterOutcome")?.value || "ALL"),
    partialOnly: Boolean(document.getElementById("btFilterPartialOnly")?.checked),
  };
}
function rowOutcomeLabel(row) { if (row?.status !== "ok") return "NA"; return row?.hit ? "PASS" : "FAIL"; }

function compareBacktestRows(a, b, field, dir) {
  function val(row) { if (field === "pass_fail") return rowOutcomeLabel(row); return row?.[field]; }
  const av = val(a); const bv = val(b);
  const numericFields = new Set(["predicted_confidence","horizon_days_used","start_price","end_price","realized_forward_return_pct"]);
  const dateFields = new Set(["as_of_used","evaluation_end_date"]);
  let cmp = 0;
  if (numericFields.has(field)) cmp = Number(av || 0) - Number(bv || 0);
  else if (dateFields.has(field)) cmp = String(av || "").localeCompare(String(bv || ""));
  else if (field === "status") { const rank = {ok:0,no_forward_data:1,error:2}; cmp = (rank[String(av||"")]??99)-(rank[String(bv||"")]??99); }
  else if (field === "pass_fail") { const rank = {PASS:0,FAIL:1,NA:2}; cmp = (rank[String(av||"NA")]??99)-(rank[String(bv||"NA")]??99); }
  else cmp = String(av ?? "").localeCompare(String(bv ?? ""), undefined, { sensitivity: "base" });
  return dir === "asc" ? cmp : -cmp;
}

function updateBacktestSortIndicators() {
  document.querySelectorAll("th.bt-sortable").forEach((th) => {
    const field = th.getAttribute("data-bt-sort-field");
    const span = th.querySelector(".sort-ind");
    if (!span) return;
    span.textContent = field === btSortField ? (btSortDir === "asc" ? "▲" : "▼") : "";
  });
}

function applyBacktestFilters(rows) {
  const f = getBacktestFilters();
  return (rows || []).filter((r) => {
    if (f.ticker && !String(r.ticker || "").toUpperCase().includes(f.ticker)) return false;
    if (f.status !== "ALL" && String(r.status || "") !== f.status) return false;
    if (f.prediction !== "ALL" && String(r.predicted_action || "").toUpperCase() !== f.prediction) return false;
    if (f.outcome !== "ALL" && rowOutcomeLabel(r) !== f.outcome) return false;
    if (f.partialOnly && !(r.status === "ok" && r.partial_horizon)) return false;
    return true;
  });
}

function renderBacktestTable(rows) {
  const body = document.getElementById("backtestBody");
  const sorted = [...(rows || [])].sort((a, b) => compareBacktestRows(a, b, btSortField, btSortDir));
  updateBacktestSortIndicators();
  body.innerHTML = sorted.map((r) => {
    const conf = Number(r.predicted_confidence || 0);
    const realized = Number(r.realized_forward_return_pct || 0);
    const passFail = r.status === "ok" ? (r.hit ? `<span class="chip pass">PASS</span>` : `<span class="chip fail">FAIL</span>`) : `<span class="chip warn">N/A</span>`;
    return `<tr>
      <td>${r.ticker || ""}</td>
      <td>${backtestStatusBadge(r.status)}</td>
      <td>${r.predicted_action || "-"}</td>
      <td>${(conf * 100).toFixed(1)}%</td>
      <td>${r.as_of_used || "-"}</td>
      <td>${r.evaluation_end_date || "-"}</td>
      <td>${r.status === "ok" ? `${Number(r.horizon_days_used || 0)}${r.partial_horizon ? " (partial)" : ""}` : "-"}</td>
      <td>${r.start_price ?? "-"}</td>
      <td>${r.end_price ?? "-"}</td>
      <td class="${realized >= 0 ? "pos" : "neg"}">${r.status === "ok" ? fmtPct(realized) : "-"}</td>
      <td>${passFail}</td>
    </tr>`;
  }).join("");
}

function renderBacktest(payload) {
  const rows = payload.rows || [];
  latestBacktestRows = rows;
  const scored = rows.filter((r) => r.status === "ok");
  const hits = scored.filter((r) => r.hit).length;
  const hitRate = payload.hit_rate == null ? "-" : `${(Number(payload.hit_rate) * 100).toFixed(1)}%`;
  const avgFwd = scored.length ? `${(scored.reduce((s, r) => s + Number(r.realized_forward_return_pct || 0), 0) / scored.length).toFixed(2)}%` : "-";
  document.getElementById("btScored").textContent = `${payload.count_scored ?? 0}/${payload.count_total ?? 0}`;
  document.getElementById("btHits").textContent = `${hits}`;
  document.getElementById("btHitRate").textContent = hitRate;
  document.getElementById("btAvgFwd").textContent = avgFwd;
  renderBacktestTable(applyBacktestFilters(rows));
}

function rerenderBacktestWithFilters() { renderBacktestTable(applyBacktestFilters(latestBacktestRows)); }

async function runBacktest() {
  const asOfDate = document.getElementById("backtestAsOfDate").value || defaultBacktestDate();
  const horizonDays = Number(document.getElementById("backtestHorizon").value || 20);
  const tickers = latestTickers.length ? latestTickers : [...new Set((latestRows || []).map((r) => r.ticker).filter(Boolean))];
  if (!tickers.length) { setBacktestStatus("No tickers available yet. Refresh watchlist first."); return; }
  setBacktestRunning(true);
  setWorking(true, "Running historical validation...");
  setBacktestStatus(`Backtest running: ${tickers.length} tickers as of ${asOfDate} (${horizonDays}D)...`);
  try {
    const res = await fetch("/validate/backtest", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tickers, as_of_date: asOfDate, horizon_days: horizonDays }) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    renderBacktest(payload);
    const partialCount = (payload.rows || []).filter((r) => r.status === "ok" && r.partial_horizon).length;
    setBacktestStatus(`Backtest completed: ${payload.count_scored || 0}/${payload.count_total || 0} scored, hit rate ${payload.hit_rate == null ? "-" : `${(payload.hit_rate * 100).toFixed(1)}%`}${partialCount ? `, partial horizons: ${partialCount}` : ""}.`);
  } catch (error) { setBacktestStatus(`Backtest failed: ${error.message}`); }
  finally { setBacktestRunning(false); setWorking(false); }
}

// ---------------------------------------------------------------------------
// Forward Horizon Predictor
// ---------------------------------------------------------------------------
function getForwardGridHorizonConfig() {
  const fixed = [
    { key: "7", days: 7, label: "7D" }, { key: "10", days: 10, label: "10D" },
    { key: "30", days: 30, label: "30D" }, { key: "60", days: 60, label: "60D" },
    { key: "90", days: 90, label: "90D" }, { key: "126", days: 126, label: "6M" },
    { key: "252", days: 252, label: "1Y" },
  ];
  const customEl = document.getElementById("forwardCustomDays");
  let customDays = Number(customEl?.value || 45);
  if (!Number.isFinite(customDays)) customDays = 45;
  customDays = Math.max(5, Math.min(1095, Math.round(customDays)));
  if (customEl) customEl.value = String(customDays);
  return { horizonsParam: [...fixed.map((x) => x.days), customDays].join(","), fixed, customDays };
}

function renderForwardPredictions(payload) {
  const body = document.getElementById("forwardBody");
  const { fixed, customDays } = getForwardGridHorizonConfig();
  function cellFor(byHorizon, days, label) {
    const r = byHorizon.get(days);
    if (!r || r.status !== "ok") return { cls: "forward-cell forward-err", html: `<span class="chip warn">ERR</span>` };
    const action = normalizeActionLabel(r.action || "HOLD");
    const conf = `${(Number(r.confidence || 0) * 100).toFixed(0)}%`;
    const date = r.predicted_for_date || "-";
    const tooltip = `${label} -> ${date} | Pos ${Number(r.position_pct || 0).toFixed(2)}% | Score ${Number(r.weighted_score || 0).toFixed(3)}`;
    const actionCls = action === "BUY" ? "forward-buy" : action === "SELL" ? "forward-sell" : "forward-hold";
    return { cls: `forward-cell ${actionCls}`, html: `<span title="${tooltip}">${badge(action)} ${conf}</span>` };
  }
  const asOf = payload.as_of_date || "latest";
  body.innerHTML = (payload.items || []).map((item) => {
    const byHorizon = new Map((item.rows || []).map((r) => [Number(r.horizon_days || 0), r]));
    const cells = [7,10,30,60,90,126,252].map((d,i) => cellFor(byHorizon, d, fixed[i].label));
    const cCustom = cellFor(byHorizon, customDays, `${customDays}D`);
    return `<tr><td>${item.ticker || "-"}</td><td>${asOf}</td>${cells.map(c=>`<td class="${c.cls}">${c.html}</td>`).join("")}<td class="${cCustom.cls}">${cCustom.html}</td></tr>`;
  }).join("");
}

function getForwardUniverse(extraTicker = "") {
  return dedupeUpper([...forwardUniverseTickers, ...latestTickers, ...(latestRows || []).map((r) => r.ticker), extraTicker]);
}

async function loadForwardPredictions(extraTicker = "") {
  forwardUniverseTickers = getForwardUniverse(extraTicker);
  if (!forwardUniverseTickers.length) { setForwardStatus("No tickers available."); document.getElementById("forwardBody").innerHTML = ""; return; }
  const cfg = getForwardGridHorizonConfig();
  setForwardStatus(`Loading horizon grid for ${forwardUniverseTickers.length} tickers...`);
  try {
    const qTickers = encodeURIComponent(forwardUniverseTickers.join(","));
    const qHorizons = encodeURIComponent(cfg.horizonsParam);
    const res = await fetch(`/dashboard/forward-horizons?tickers=${qTickers}&horizons=${qHorizons}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    renderForwardPredictions(payload);
    setForwardStatus(`Forward prediction grid ready for ${payload.items?.length || 0} tickers.`);
  } catch (error) { setForwardStatus(`Failed: ${error.message}`); }
}

async function addForwardTickerFromInput() {
  const input = document.getElementById("forwardExtraTicker");
  const tk = sanitizeTickerInput(input?.value || "");
  if (!tk) { setForwardStatus("Enter a valid ticker."); return; }
  forwardUniverseTickers = dedupeUpper([...forwardUniverseTickers, tk]);
  if (input) input.value = "";
  await loadForwardPredictions();
}

// ---------------------------------------------------------------------------
// Watchlist table (sortable)
// ---------------------------------------------------------------------------
function compareRows(a, b, field, dir) {
  const av = a?.[field]; const bv = b?.[field]; let cmp = 0;
  if (typeof av === "number" && typeof bv === "number") cmp = av - bv;
  else cmp = String(av ?? "").localeCompare(String(bv ?? ""), undefined, { sensitivity: "base" });
  return dir === "asc" ? cmp : -cmp;
}

function updateSortIndicators() {
  document.querySelectorAll("th.sortable").forEach((th) => {
    const field = th.getAttribute("data-sort-field");
    const span = th.querySelector(".sort-ind");
    if (!span) return;
    span.textContent = field === tableSortField ? (tableSortDir === "asc" ? "▲" : "▼") : "";
  });
}

function upsertWatchlistRow(row) {
  if (!row || !row.ticker) return;
  const tk = String(row.ticker).toUpperCase();
  const idx = latestRows.findIndex((r) => String(r.ticker || "").toUpperCase() === tk);
  if (idx >= 0) latestRows[idx] = row; else latestRows.push(row);
  latestRows.sort((a, b) => Number(b.confidence || 0) - Number(a.confidence || 0));
}

function detailToWatchlistRow(detailPayload) {
  const analysis = detailPayload?.analysis || {};
  const consensus = analysis.consensus || {};
  const market = analysis.market || {};
  const waveEdge = detailPayload?.wave_edge_indicator || {};
  return {
    ticker: String(market.ticker || detailPayload?.ticker || "").toUpperCase(),
    action: normalizeActionLabel(consensus.action || "HOLD"),
    confidence: Number(consensus.confidence || 0),
    position_pct: Number(consensus.recommended_position_pct || 0),
    weighted_score: Number(consensus.weighted_score || 0),
    price: Number(market.price || 0),
    returns_1d: Number(market.returns_1d || 0) * 100,
    returns_20d: Number(market.returns_20d || 0) * 100,
    rsi_14: Number(market.rsi_14 || 50),
    beta: Number(market.beta_to_market || 1),
    realized_vol_20d: Number(market.realized_vol_20d || 0) * 100,
    news_sentiment: Number(market.news_sentiment || 0),
    macro_regime: String(market.macro_regime || ""),
    top_headline: (market.news_headlines || [""])[0] || "",
    wave_edge_score: Number(waveEdge.score || 0),
    wave_edge_up_prob: Number(waveEdge.up_probability_pct || 50),
    data_quality: String(market.data_quality || "unknown"),
  };
}

function renderTable(rows) {
  const body = document.getElementById("watchlistBody");
  const query = document.getElementById("searchInput").value.trim().toUpperCase();
  const dedup = new Map();
  rows.forEach((r) => r?.ticker && dedup.set(r.ticker, r));
  const filtered = [...dedup.values()].filter((r) => !query || String(r.ticker || "").includes(query));
  const sorted = [...filtered].sort((a, b) => compareRows(a, b, tableSortField, tableSortDir));
  updateSortIndicators();
  body.innerHTML = sorted.map((r) => {
    const retClass1d = r.returns_1d >= 0 ? "pos" : "neg";
    const retClass20d = r.returns_20d >= 0 ? "pos" : "neg";
    const newsClass = r.news_sentiment >= 0 ? "pos" : "neg";
    const weClass = Number(r.wave_edge_score || 0) >= 0 ? "pos" : "neg";
    return `<tr>
      <td>${r.ticker || ""}</td>
      <td>${badge(normalizeActionLabel(r.action))}</td>
      <td>${(r.confidence * 100).toFixed(1)}%</td>
      <td>${r.position_pct.toFixed(2)}%</td>
      <td>${r.weighted_score.toFixed(3)}</td>
      <td>$${r.price.toFixed(2)}</td>
      <td class="${retClass1d}">${fmtPct(r.returns_1d)}</td>
      <td class="${retClass20d}">${fmtPct(r.returns_20d)}</td>
      <td>${Number(r.rsi_14 || 50).toFixed(1)}</td>
      <td>${r.beta.toFixed(2)}</td>
      <td>${r.realized_vol_20d.toFixed(2)}%</td>
      <td class="${newsClass}">${r.news_sentiment.toFixed(2)}</td>
      <td class="${weClass}">${Number(r.wave_edge_score || 0).toFixed(1)}</td>
      <td>${r.macro_regime || ""}</td>
      <td>${qualityBadge(r.data_quality || "unknown")}</td>
    </tr>`;
  }).join("");
}

// ---------------------------------------------------------------------------
// Charts: Doughnut + Confidence Bar
// ---------------------------------------------------------------------------
function renderCharts(payload) {
  const actions = payload.action_distribution || { BUY: 0, HOLD: 0, SELL: 0 };
  const rows = payload.rows || [];
  const top = [...rows].slice(0, 8);
  const byAction = { BUY: rows.filter((r) => normalizeActionLabel(r.action) === "BUY"), HOLD: rows.filter((r) => normalizeActionLabel(r.action) === "HOLD"), SELL: rows.filter((r) => normalizeActionLabel(r.action) === "SELL") };
  const actionColor = { BUY: "#2ecc71", HOLD: "#f1c40f", SELL: "#ff5b6e" };

  if (actionChart) actionChart.destroy();
  actionChart = new Chart(document.getElementById("actionChart"), {
    type: "doughnut",
    data: { labels: ["BUY","HOLD","SELL"], datasets: [{ data: [actions.BUY||0, actions.HOLD||0, actions.SELL||0], backgroundColor: ["#2ecc71","#f1c40f","#ff5b6e"] }] },
    options: { cutout: "75%", plugins: { legend: { labels: { color: "#e5ecff" } }, tooltip: { callbacks: {
      label: (ctx) => `${ctx.label}: ${ctx.parsed} tickers`,
      afterLabel: (ctx) => {
        const list = byAction[ctx.label] || [];
        if (!list.length) return "No tickers";
        const avgConf = (list.reduce((s, x) => s + Number(x.confidence || 0), 0) / list.length) * 100;
        const avgPos = list.reduce((s, x) => s + Number(x.position_pct || 0), 0) / list.length;
        const tickers = list.map((x) => x.ticker).slice(0, 8).join(", ");
        return [`Tickers: ${tickers}`, `Avg confidence: ${avgConf.toFixed(1)}%`, `Avg position: ${avgPos.toFixed(2)}%`];
      }
    }}}}
  });

  const barLabelPlugin = {
    id: "barLabelPlugin",
    afterDatasetsDraw(chart) {
      const { ctx } = chart; const meta = chart.getDatasetMeta(0);
      ctx.save(); ctx.fillStyle = "#e5ecff"; ctx.font = "11px Segoe UI"; ctx.textAlign = "center";
      meta.data.forEach((bar, idx) => { const value = chart.data.datasets[0].data[idx]; ctx.fillText(`${value.toFixed(0)}%`, bar.x, bar.y + 14); });
      ctx.restore();
    }
  };

  if (confidenceChart) confidenceChart.destroy();
  confidenceChart = new Chart(document.getElementById("confidenceChart"), {
    type: "bar",
    data: { labels: top.map((x) => x.ticker), datasets: [{ label: "Confidence %", data: top.map((x) => x.confidence * 100), backgroundColor: top.map((x) => actionColor[normalizeActionLabel(x.action)] || "#6ca5ff"), borderColor: top.map((x) => actionColor[normalizeActionLabel(x.action)] || "#6ca5ff"), borderWidth: 1, barThickness: 36 }] },
    options: { scales: { x: { ticks: { color: "#e5ecff" } }, y: { ticks: { color: "#e5ecff" }, beginAtZero: true, max: 100 } }, plugins: { legend: { labels: { color: "#e5ecff" } }, tooltip: { callbacks: { afterLabel: (ctx) => { const row = top[ctx.dataIndex]; if (!row) return ""; return [`Action: ${normalizeActionLabel(row.action)}`, `Pos size: ${row.position_pct.toFixed(2)}%`, `20D return: ${row.returns_20d.toFixed(2)}%`]; } } } } },
    plugins: [barLabelPlugin],
  });
  document.getElementById("confidenceExplain").textContent = "Bars are ticker-coded by signal (BUY green, HOLD yellow, SELL red). Read as conviction ranking + action quality.";
}

// ---------------------------------------------------------------------------
// KPIs
// ---------------------------------------------------------------------------
function renderKpis(payload) {
  document.getElementById("kpiCount").textContent = payload.count;
  const dist = payload.action_distribution || {};
  document.getElementById("kpiBuy").textContent = dist.BUY || 0;
  document.getElementById("kpiHold").textContent = dist.HOLD || 0;
  document.getElementById("kpiSell").textContent = dist.SELL || 0;
  document.getElementById("kpiConfidence").textContent = `${(payload.avg_confidence * 100).toFixed(1)}%`;
  document.getElementById("kpiPosition").textContent = `${payload.avg_position_pct.toFixed(2)}%`;
  document.getElementById("kpiUpdated").textContent = payload.generated_at_utc || "-";
}

function renderTickerSelect(tickers) {
  const select = document.getElementById("tickerSelect");
  latestTickers = [...new Set((tickers || []).map((t) => String(t || "").toUpperCase()).filter(Boolean))];
  select.innerHTML = latestTickers.map((t) => `<option value="${t}">${t}</option>`).join("");
}

// ---------------------------------------------------------------------------
// Ticker Deep-Dive
// ---------------------------------------------------------------------------
function renderFactorExplain(tech) {
  const items = [
    `RSI ${Number(tech.rsi_14 || 50).toFixed(1)}: >70 overbought, <30 oversold.`,
    `MACD ${Number(tech.macd_value || 0).toFixed(3)} vs signal ${Number(tech.macd_signal || 0).toFixed(3)}: MACD above signal is bullish momentum.`,
    `SMA20 ${Number(tech.sma_20 || 0).toFixed(2)} vs SMA50 ${Number(tech.sma_50 || 0).toFixed(2)}: short-term trend strength.`,
    `SMA50 ${Number(tech.sma_50 || 0).toFixed(2)} vs SMA200 ${Number(tech.sma_200 || 0).toFixed(2)}: medium vs long-term trend regime.`,
    `ADX ${Number(tech.adx_14 || 0).toFixed(1)}: >25 trending, <20 ranging.`,
    `Stochastic %K ${Number(tech.stochastic_k || 50).toFixed(1)}: >80 overbought, <20 oversold.`,
    `Bollinger %B ${Number(tech.bollinger_pct_b || 0.5).toFixed(3)}: >1 above upper band, <0 below lower band.`,
  ];
  document.getElementById("factorExplainList").innerHTML = items.map((x) => `<li>${x}</li>`).join("");
}

function renderWaveEdge(payload) {
  const w = payload.wave_edge_indicator || {};
  const score = Number(w.score || 0);
  const up = Number(w.up_probability_pct || 50);
  const fillPct = Math.max(0, Math.min(100, ((score + 100) / 200) * 100));
  const scoreEl = document.getElementById("waveEdgeScore");
  const labelEl = document.getElementById("waveEdgeLabel");
  const fillEl = document.getElementById("waveEdgeFill");
  const probVal = document.getElementById("waveProbValue");
  const probFill = document.getElementById("waveProbFill");
  const interp = document.getElementById("waveEdgeInterpretation");
  const compList = document.getElementById("waveEdgeComponents");
  if (!scoreEl||!labelEl||!fillEl||!probVal||!probFill||!interp||!compList) return;
  scoreEl.textContent = score.toFixed(1);
  labelEl.textContent = String(w.label || "Balanced / Neutral");
  fillEl.style.width = `${fillPct.toFixed(1)}%`;
  probVal.textContent = `${up.toFixed(1)}%`;
  probFill.style.width = `${Math.max(1, Math.min(99, up)).toFixed(1)}%`;
  // Interpretation text
  let interpText = "";
  if (score >= 35) interpText = "Strong bullish edge detected — momentum, analogs, and consensus align upward. Consider full position with risk caps.";
  else if (score >= 12) interpText = "Moderate bullish tilt — positive signals outweigh negatives. Partial position warranted with confirmation.";
  else if (score <= -35) interpText = "Strong bearish edge — multiple factors point to downside. Reduce exposure or hedge.";
  else if (score <= -12) interpText = "Moderate bearish tilt — caution warranted. Lighten position, wait for reversal signals.";
  else interpText = "Balanced / neutral — no strong directional edge. Wait for clearer signals or keep minimal exposure.";
  interp.textContent = interpText;
  const c = w.components || {};
  const lines = [
    `Momentum (20D): ${Number(c.momentum_20d_pct || 0).toFixed(2)}%`,
    `Analog forward avg: ${Number(c.analog_forward_avg_pct || 0).toFixed(2)}%`,
    `Consensus bias: ${Number(c.consensus_bias || 0).toFixed(2)}`,
    `News component: ${Number(c.news || 0).toFixed(2)}`,
    `Confidence component: ${Number(c.confidence || 0).toFixed(2)}`,
    `Volatility penalty: ${Number(c.vol_penalty || 0).toFixed(2)}`,
    `Regime component: ${Number(c.regime || 0).toFixed(2)}`,
  ];
  compList.innerHTML = lines.map((x) => `<li>${x}</li>`).join("");
}

function renderTickerDetail(payload) {
  latestDetailPayload = payload;
  const thesis = payload.thesis_pack || {};
  document.getElementById("thesisText").textContent = thesis.thesis || "-";
  document.getElementById("catalystText").textContent = thesis.catalyst_risks || "-";
  document.getElementById("repeatText").textContent = thesis.repeatability_view || "-";
  document.getElementById("quantView").textContent = thesis.quant_view || "-";
  document.getElementById("fundView").textContent = thesis.fundamental_view || "-";
  document.getElementById("mlView").textContent = thesis.ml_view || "-";

  // Risk notes
  const risk = payload.analysis?.risk || {};
  document.getElementById("riskList").innerHTML = (risk.risk_notes || []).map((n) => `<li>${n}</li>`).join("") || "<li>No risk data</li>";

  // Analogs table
  const analogs = [...(payload.historical_analogs || [])].sort((a, b) => String(b.event_date || "").localeCompare(String(a.event_date || "")));
  latestAnalogs = analogs;
  document.getElementById("analogBody").innerHTML = analogs.map((a, idx) => {
    const d = a.factor_deltas || {};
    const deltas = `dMom ${d.delta_momentum_pct ?? 0}%, dVol ${d.delta_vol_pct ?? 0}%, dBench ${d.delta_benchmark_pct ?? 0}%`;
    const p0 = Number(a.setup_start_price ?? NaN); const p1 = Number(a.event_price ?? NaN); const p2 = Number(a.forward_20d_price ?? NaN);
    const setupPct = Number.isFinite(p0) && Number.isFinite(p1) && p0 !== 0 ? ((p1/p0)-1)*100 : NaN;
    const fwdPct = Number.isFinite(p1) && Number.isFinite(p2) && p1 !== 0 ? ((p2/p1)-1)*100 : NaN;
    const setupColor = setupPct >= 0 ? "pos" : "neg";
    const fwdColor = fwdPct >= 0 ? "pos" : "neg";
    return `<tr>
      <td>A${idx+1} ${a.event_date || ""}</td>
      <td>${a.setup_start_price ?? "-"} → ${a.event_price ?? "-"}<br/><small class="${setupColor}">From price: ${Number.isFinite(setupPct) ? setupPct.toFixed(2) : "-"}%</small></td>
      <td>${a.forward_20d_price ?? "-"}<br/><small class="${fwdColor}">From event: ${Number.isFinite(fwdPct) ? fwdPct.toFixed(2) : "-"}%</small></td>
      <td class="${setupColor}">${a.lookback_return_pct ?? ""}%</td>
      <td>${a.lookback_vol_pct ?? ""}%</td>
      <td class="${(a.benchmark_20d_return_pct || 0) >= 0 ? "pos" : "neg"}">${a.benchmark_20d_return_pct ?? ""}%</td>
      <td class="${fwdColor}">${a.forward_20d_return_pct ?? ""}%</td>
      <td title="${deltas}">${(a.matching_factors || []).join(", ") || "Return proximity only"}<br/><small>${a.why_matched || deltas}</small></td>
      <td><button class="inspect-btn" data-incident="${a.event_date || ""}">View</button></td>
    </tr>`;
  }).join("");

  document.getElementById("sourceList").innerHTML = (payload.sources || []).map((s) => `<li>${s}</li>`).join("") || "<li>No source metadata</li>";
  document.getElementById("newsList").innerHTML = (payload.news_items || []).slice(0, 6).map((n) => {
    const title = n.title || ""; const pub = n.publisher || ""; const link = n.link || "";
    return link ? `<li><a href="${link}" target="_blank" rel="noreferrer">${title}</a><br/><small>${pub}</small></li>` : `<li>${title}<br/><small>${pub}</small></li>`;
  }).join("") || "<li>No recent news available from provider.</li>";

  wireAnalogInspectButtons();
  renderAnalogCompareControls();
  populatePickedEventOptions();
  renderIncidentSelector(analogs);
  renderDetailCharts(payload);
  renderComparisonCharts(payload);
  renderWaveEdge(payload);
}

// ---------------------------------------------------------------------------
// Detail Charts: Technical + Factor
// ---------------------------------------------------------------------------
function axisDateTickCallback(value) {
  const label = this.getLabelForValue(value) || "";
  const span = Math.max(1, Number(this.max) - Number(this.min));
  if (span <= 45) return label;
  if (span <= 140) return label.slice(0, 10);
  return label.slice(0, 7);
}

function renderDetailCharts(payload) {
  const chart = payload.chart || {};
  const dates = chart.dates || []; const close = chart.close || [];
  const sma20 = chart.sma20 || []; const sma50 = chart.sma50 || [];
  const markers = chart.analog_markers || []; const markerLabels = chart.analog_marker_labels || [];
  if (technicalChart) technicalChart.destroy();
  technicalChart = new Chart(document.getElementById("technicalChart"), {
    type: "line",
    data: { labels: dates, datasets: [
      { label: "Price (Close)", data: close, borderColor: "#9EE2FF", pointRadius: 0, borderWidth: 2.4 },
      { label: "SMA 20", data: sma20, borderColor: "#2ecc71", pointRadius: 0, borderWidth: 1.2 },
      { label: "SMA 50", data: sma50, borderColor: "#f1c40f", pointRadius: 0, borderWidth: 1.2 },
      { label: "Historical match points", data: markers, showLine: false, pointRadius: 6, pointHoverRadius: 8, pointBackgroundColor: "#ff5b6e", pointBorderColor: "#ff5b6e" },
    ]},
    options: {
      interaction: { mode: "index", intersect: false },
      scales: { x: { ticks: { color: "#e5ecff", maxTicksLimit: 12, callback: function(value) { return axisDateTickCallback.call(this, value); } } }, y: { ticks: { color: "#e5ecff" } } },
      plugins: { legend: { labels: { color: "#e5ecff" } },
        zoom: { pan: { enabled: true, mode: "x" }, zoom: { wheel: { enabled: true, modifierKey: "ctrl" }, pinch: { enabled: true }, mode: "x" } },
        tooltip: { mode: "index", intersect: false, callbacks: {
          title: (ctx) => dates[ctx[0].dataIndex] || "",
          label: (ctx) => `${ctx.dataset.label}: ${Number(ctx.parsed.y||0).toFixed(2)}`,
          afterLabel: (ctx) => (ctx.datasetIndex === 3 ? `${markerLabels[ctx.dataIndex]||""}` : ""),
        }}
      }
    }
  });

  const tech = payload.technicals || {};
  const rsiNorm = Math.max(0, Math.min(100, Number(tech.rsi_14 || 50)));
  const macdNorm = Math.max(0, Math.min(100, 50 + Number(tech.macd_value || 0) * 15));
  const trend20 = Math.max(0, Math.min(100, Number(tech.sma_20||0) > 0 ? 50 + ((Number(tech.sma_20)-Number(tech.sma_50||tech.sma_20))/Number(tech.sma_20))*400 : 50));
  const trend50 = Math.max(0, Math.min(100, Number(tech.sma_50||0) > 0 ? 50 + ((Number(tech.sma_50)-Number(tech.sma_200||tech.sma_50))/Number(tech.sma_50))*400 : 50));
  if (factorChart) factorChart.destroy();
  factorChart = new Chart(document.getElementById("factorChart"), {
    type: "bar",
    data: { labels: ["RSI","MACD","SMA20>SMA50","SMA50>SMA200"], datasets: [{ label: "Technical Strength (0-100)", data: [rsiNorm,macdNorm,trend20,trend50], backgroundColor: ["#6ca5ff","#ff5b6e","#2ecc71","#f1c40f"] }] },
    options: { scales: { x: { ticks: { color: "#e5ecff" } }, y: { ticks: { color: "#e5ecff" }, beginAtZero: true, max: 100 } }, plugins: { legend: { labels: { color: "#e5ecff" } } } },
  });
  renderFactorExplain(tech);
}

// ---------------------------------------------------------------------------
// Incident Explorer
// ---------------------------------------------------------------------------
function renderIncidentSelector(analogs) {
  const select = document.getElementById("incidentDateSelect");
  const options = (analogs || []).map((a) => a.event_date).filter(Boolean);
  select.innerHTML = options.map((d) => `<option value="${d}">${d}</option>`).join("");
  if (options.length > 0) { select.value = options[0]; renderIncidentChart(options[0]); }
  else if (incidentChart) incidentChart.destroy();
}

function wireAnalogInspectButtons() {
  document.querySelectorAll(".inspect-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const date = btn.getAttribute("data-incident");
      if (!date) return;
      document.getElementById("incidentDateSelect").value = date;
      renderIncidentChart(date);
    });
  });
}

function renderIncidentChart(incidentDate) {
  if (!latestDetailPayload) return;
  const chart = latestDetailPayload.chart || {};
  const dates = chart.dates || []; const close = chart.close || [];
  const idx = dates.indexOf(incidentDate);
  if (idx < 0 || close.length === 0) return;
  const windowDays = Number(document.getElementById("incidentWindowSelect").value || 30);
  const start = Math.max(0, idx - windowDays);
  const end = Math.min(close.length - 1, idx + windowDays);
  const slicedDates = dates.slice(start, end + 1);
  const slicedClose = close.slice(start, end + 1);
  const base = close[idx];
  const normalized = slicedClose.map((v) => ((v / base) - 1) * 100);
  const eventRelativeDays = slicedDates.map((_, i) => i + start - idx);
  if (incidentChart) incidentChart.destroy();
  incidentChart = new Chart(document.getElementById("incidentChart"), {
    type: "line",
    data: { labels: slicedDates, datasets: [{ label: `${incidentDate} event path`, data: normalized, borderColor: "#ff5b6e", borderWidth: 2, pointRadius: 0 }] },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { title: { display: true, text: "Date", color: "#e5ecff" }, ticks: { color: "#e5ecff", maxTicksLimit: 10, callback: function(value) { return axisDateTickCallback.call(this, value); } } },
        y: { title: { display: true, text: "Return from event (%)", color: "#e5ecff" }, ticks: { color: "#e5ecff" } },
      },
      plugins: { legend: { labels: { color: "#e5ecff" } },
        zoom: { pan: { enabled: true, mode: "x" }, zoom: { wheel: { enabled: true, modifierKey: "ctrl" }, pinch: { enabled: true }, mode: "x" } },
        tooltip: { callbacks: { title: (ctx) => slicedDates[ctx[0].dataIndex] || "", afterLabel: (ctx) => `Day ${eventRelativeDays[ctx.dataIndex] >= 0 ? "+" : ""}${eventRelativeDays[ctx.dataIndex]}` } }
      }
    }
  });
  const endPrice = slicedClose[slicedClose.length - 1];
  document.getElementById("incidentSummary").textContent =
    `Incident ${incidentDate}: price moved ${base.toFixed(2)} → ${endPrice.toFixed(2)} (${(((endPrice/base)-1)*100).toFixed(2)}%) over selected window.`;
}

// ---------------------------------------------------------------------------
// Multi-Timeframe Comparison Studio
// ---------------------------------------------------------------------------
function timeframeToDays(tf) { return { "6M": 126, "1Y": 252, "3Y": 756, "5Y": 1260, "10Y": 2520 }[tf] || 252; }
function getDateInputRange(frame) { return { from: document.getElementById(`cmpFrom${frame}`)?.value || "", to: document.getElementById(`cmpTo${frame}`)?.value || "" }; }
function setDateInputs(frame, from, to) { const f = document.getElementById(`cmpFrom${frame}`); const t = document.getElementById(`cmpTo${frame}`); if (f) f.value = from||""; if (t) t.value = to||""; }

function futureDateLabels(lastDateStr, n) {
  const out = []; const d = new Date(lastDateStr);
  for (let i = 0; i < n; i++) { d.setDate(d.getDate()+1); while(d.getDay()===0||d.getDay()===6) d.setDate(d.getDate()+1); out.push(d.toISOString().slice(0,10)); }
  return out;
}

function projectedPath(closeValues, horizon = 20, lookback = null) {
  const look = Math.min(lookback || 30, closeValues.length);
  const arr = closeValues.slice(-look);
  let sx=0,sy=0,sxy=0,sxx=0;
  for (let i = 0; i < arr.length; i++) { sx+=i; sy+=arr[i]; sxy+=i*arr[i]; sxx+=i*i; }
  const n = arr.length||1;
  const slope = (n*sxy - sx*sy) / Math.max(1, (n*sxx - sx*sx));
  const last = closeValues[closeValues.length-1];
  const proj = [];
  for (let j = 1; j <= horizon; j++) proj.push(last + slope * j);
  return proj;
}

function makeCompareChart(canvasId, tf, payload, existingChart, frame) {
  const chart = payload.chart || {};
  const dates = chart.dates || []; const close = chart.close || [];
  const sma20 = chart.sma20 || []; const sma50 = chart.sma50 || [];
  const analogMarkersAll = chart.analog_markers || []; const analogMarkerLabelsAll = chart.analog_marker_labels || [];
  if (!dates.length) return existingChart;
  let d=[],c=[],s20=[],s50=[],m=[],ml=[];
  const { from, to } = getDateInputRange(frame);
  if (tf === "custom" && from && to) {
    for (let i = 0; i < dates.length; i++) { if (dates[i] >= from && dates[i] <= to) { d.push(dates[i]); c.push(close[i]); s20.push(sma20[i]); s50.push(sma50[i]); m.push(analogMarkersAll[i]); ml.push(analogMarkerLabelsAll[i]); } }
  } else {
    const days = timeframeToDays(tf); const start = Math.max(0, dates.length - days);
    d=dates.slice(start); c=close.slice(start); s20=sma20.slice(start); s50=sma50.slice(start); m=analogMarkersAll.slice(start); ml=analogMarkerLabelsAll.slice(start);
    if (d.length) setDateInputs(frame, d[0], d[d.length-1]);
  }
  if (!d.length) { const fb = Math.max(0, dates.length-252); d=dates.slice(fb); c=close.slice(fb); s20=sma20.slice(fb); s50=sma50.slice(fb); m=analogMarkersAll.slice(fb); ml=analogMarkerLabelsAll.slice(fb); if (d.length) setDateInputs(frame,d[0],d[d.length-1]); }
  const lookback = Math.max(12, Math.min(90, Math.round(c.length*0.22)));
  const proj = projectedPath(c, 20, lookback);
  const futureLabels = futureDateLabels(d[d.length-1], 20);
  const allLabels = [...d, ...futureLabels];
  const projSeries = new Array(Math.max(0,c.length-1)).fill(null).concat([Number(c[c.length-1].toFixed(4)), ...proj.map(x=>Number(x.toFixed(4)))]);
  const closeSeries = c.concat(new Array(20).fill(null));
  const s20Series = s20.concat(new Array(20).fill(null));
  const s50Series = s50.concat(new Array(20).fill(null));
  const markerSeries = m.concat(new Array(20).fill(null));
  const markerLabels = ml.concat(new Array(20).fill(null));
  if (existingChart) existingChart.destroy();
  return new Chart(document.getElementById(canvasId), {
    type: "line",
    data: { labels: allLabels, datasets: [
      { label: "Price (Close)", data: closeSeries, borderColor: "#9EE2FF", borderWidth: 2.2, pointRadius: 0 },
      { label: "SMA20", data: s20Series, borderColor: "#2ecc71", borderWidth: 1.2, pointRadius: 0 },
      { label: "SMA50", data: s50Series, borderColor: "#f1c40f", borderWidth: 1.2, pointRadius: 0 },
      { label: "Similarity points", data: markerSeries, showLine: false, pointRadius: 4.5, pointHoverRadius: 7, pointBackgroundColor: "#ff5b6e", pointBorderColor: "#ff5b6e" },
      { label: "20D projection", data: projSeries, borderColor: "#ff9d4d", borderDash: [6,4], borderWidth: 1.6, pointRadius: 0 },
    ]},
    options: {
      interaction: { mode: "index", intersect: false },
      scales: { x: { ticks: { color: "#e5ecff", maxTicksLimit: 8, callback: function(value) { return axisDateTickCallback.call(this, value); } } }, y: { ticks: { color: "#e5ecff" } } },
      plugins: { legend: { labels: { color: "#e5ecff" } },
        zoom: { pan: { enabled: true, mode: "x" }, zoom: { wheel: { enabled: true, modifierKey: "ctrl" }, pinch: { enabled: true }, mode: "x" } },
        tooltip: { mode: "index", intersect: false, callbacks: { label: (ctx) => `${ctx.dataset.label}: ${Number(ctx.parsed.y||0).toFixed(2)}`, afterLabel: (ctx) => (ctx.datasetIndex === 3 ? `${markerLabels[ctx.dataIndex]||""}` : "") } }
      }
    }
  });
}

function renderComparisonCharts(payload) {
  const ticker = String(payload?.ticker || payload?.analysis?.market?.ticker || "").toUpperCase();
  const topLabel = ticker ? `- ${ticker}` : "";
  [1,2,3].forEach((i) => { const t=document.getElementById(`cmpTicker${i}`); const m=document.getElementById(`cmpMark${i}`); if(t) t.textContent=topLabel; if(m) m.textContent=ticker; });
  cmpChart1 = makeCompareChart("cmpChart1", document.getElementById("cmpTf1").value, payload, cmpChart1, 1);
  cmpChart2 = makeCompareChart("cmpChart2", document.getElementById("cmpTf2").value, payload, cmpChart2, 2);
  cmpChart3 = makeCompareChart("cmpChart3", document.getElementById("cmpTf3").value, payload, cmpChart3, 3);
  const analogs = payload.historical_analogs || [];
  const recent = analogs.map(a=>a.event_date).filter(Boolean).sort((a,b)=>String(b).localeCompare(String(a))).slice(0,3);
  const hint = recent.length ? `Similarity events to compare: ${recent.join(", ")}. Use 10Y default, then narrow with custom.` : "No similarity events found.";
  const hintEl = document.getElementById("compareHint");
  if (hintEl) hintEl.textContent = hint;
}

function setAnalogCompareLabel(rank, analog) {
  const wrap=document.getElementById(`cmpA${rank}Wrap`); const label=document.getElementById(`cmpA${rank}Label`); const check=document.getElementById(`cmpA${rank}`);
  if (!wrap||!label||!check) return;
  if (!analog) { wrap.style.opacity="0.45"; label.textContent=`A${rank} unavailable`; check.checked=false; check.disabled=true; return; }
  wrap.style.opacity="1"; check.disabled=false; check.checked=true; label.textContent=`A${rank} (${analog.event_date})`;
}
function renderAnalogCompareControls() { setAnalogCompareLabel(1,latestAnalogs[0]); setAnalogCompareLabel(2,latestAnalogs[1]); setAnalogCompareLabel(3,latestAnalogs[2]); }

function populatePickedEventOptions() {
  const picks = [document.getElementById("cmpPick1"), document.getElementById("cmpPick2"), document.getElementById("cmpPick3")];
  const options = latestAnalogs.map((a,idx) => ({ value: String(a.event_date||""), label: `A${idx+1} ${a.event_date||""}` })).filter(x=>x.value);
  picks.forEach((sel, idx) => { if (!sel) return; sel.innerHTML = [`<option value="">Pick event ${idx+1}</option>`].concat(options.map(o=>`<option value="${o.value}">${o.label}</option>`)).join(""); if (options[idx]) sel.value = options[idx].value; });
}

function dateAtTradingOffset(dates, eventDate, offset) {
  const idx = dates.indexOf(eventDate); if (idx < 0) return eventDate;
  return dates[Math.min(dates.length-1, Math.max(0, idx+offset))];
}

function applySelectedAnalogWindows() {
  if (!latestDetailPayload) return;
  const chart = latestDetailPayload.chart || {}; const dates = chart.dates || []; if (!dates.length) return;
  const picks = [];
  if (document.getElementById("cmpA1")?.checked && latestAnalogs[0]) picks.push(latestAnalogs[0]);
  if (document.getElementById("cmpA2")?.checked && latestAnalogs[1]) picks.push(latestAnalogs[1]);
  if (document.getElementById("cmpA3")?.checked && latestAnalogs[2]) picks.push(latestAnalogs[2]);
  if (!picks.length) return;
  for (let i = 0; i < Math.min(3, picks.length); i++) {
    const frame = i+1; const from = dateAtTradingOffset(dates, String(picks[i].event_date||""), -10); const to = dateAtTradingOffset(dates, String(picks[i].event_date||""), 20);
    setDateInputs(frame, from, to); const tf = document.getElementById(`cmpTf${frame}`); if (tf) tf.value = "custom";
  }
  renderComparisonCharts(latestDetailPayload);
}

function applyPickedEventWindows() {
  if (!latestDetailPayload) return;
  const chart = latestDetailPayload.chart || {}; const dates = chart.dates || []; if (!dates.length) return;
  const picked = [document.getElementById("cmpPick1")?.value||"", document.getElementById("cmpPick2")?.value||"", document.getElementById("cmpPick3")?.value||""].filter(Boolean);
  if (!picked.length) return;
  for (let i = 0; i < Math.min(3, picked.length); i++) {
    const frame = i+1; const from = dateAtTradingOffset(dates, picked[i], -10); const to = dateAtTradingOffset(dates, picked[i], 20);
    setDateInputs(frame, from, to); const tf = document.getElementById(`cmpTf${frame}`); if (tf) tf.value = "custom";
  }
  renderComparisonCharts(latestDetailPayload);
}

// ---------------------------------------------------------------------------
// Quick Analyze (on-the-fly)
// ---------------------------------------------------------------------------
async function analyzeTickerOnTheFly(rawTicker) {
  const ticker = sanitizeTickerInput(rawTicker);
  if (!ticker) { setStatus("Enter a valid ticker to analyze."); return; }
  setWorking(true, `Analyzing ${ticker} on the fly...`);
  setStatus(`Computing ${ticker} ...`);
  try {
    const response = await fetch(`/dashboard/ticker/${encodeURIComponent(ticker)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const detailPayload = await response.json();
    const row = detailToWatchlistRow(detailPayload);
    upsertWatchlistRow(row);
    renderTable(latestRows);
    renderCharts({ action_distribution: { BUY: latestRows.filter(r=>normalizeActionLabel(r.action)==="BUY").length, HOLD: latestRows.filter(r=>normalizeActionLabel(r.action)==="HOLD").length, SELL: latestRows.filter(r=>normalizeActionLabel(r.action)==="SELL").length }, rows: latestRows });
    renderTickerSelect(latestRows.map(x=>x.ticker));
    syncOverviewTickerSelect(latestTickers);
    document.getElementById("tickerSelect").value = ticker;
    document.getElementById("ovTickerSelect").value = ticker;
    renderTickerDetail(detailPayload);
    await loadForwardPredictions(ticker);
    setStatus(`${ticker} analyzed and added to dashboard universe.`);
    setDetailStatus(`Detailed analysis loaded for ${ticker}.`);
    await runBacktest();
  } catch (error) { setStatus(`Failed to analyze ${ticker}: ${error.message}`); }
  finally { setWorking(false); }
}

// ---------------------------------------------------------------------------
// Ticker Deep-Dive loader
// ---------------------------------------------------------------------------
async function loadTickerDetail(ticker) {
  if (!ticker) return;
  setWorking(true, `Analyzing ${ticker} in depth...`);
  setDetailStatus(`Loading deep-dive for ${ticker}...`);
  try {
    const response = await fetch(`/dashboard/ticker/${encodeURIComponent(ticker)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    renderTickerDetail(payload);
    await loadForwardPredictions(ticker);
    setDetailStatus(`Detailed analysis loaded for ${ticker}.`);
  } catch (error) { setDetailStatus(`Failed loading ticker detail: ${error.message}`); }
  finally { setWorking(false); }
}

// ---------------------------------------------------------------------------
// Main Dashboard Loader
// ---------------------------------------------------------------------------
async function loadDashboard() {
  setWorking(true, "Scanning watchlist...");
  setStatus("Refreshing watchlist intelligence...");
  try {
    const response = await fetch("/dashboard/watchlist");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    latestRows = payload.rows || [];
    renderKpis(payload);
    renderTable(latestRows);
    renderCharts(payload);
    renderTickerSelect(payload.rows.map((x) => x.ticker));
    syncOverviewTickerSelect(latestTickers);
    forwardUniverseTickers = dedupeUpper(payload.rows.map((x) => x.ticker));
    const backtestDateInput = document.getElementById("backtestAsOfDate");
    if (backtestDateInput && !backtestDateInput.value) backtestDateInput.value = defaultBacktestDate();
    if (latestTickers.length) {
      loadOverviewTicker(latestTickers[0]);
    }
    const errCount = Object.keys(payload.errors || {}).length;
    setStatus(errCount ? `Loaded with ${errCount} ticker warnings.` : "Loaded successfully.");
  } catch (error) { setStatus(`Failed to load data: ${error.message}`); }
  finally { setWorking(false); }
}

// ---------------------------------------------------------------------------
// Event Listeners
// ---------------------------------------------------------------------------
document.getElementById("refreshBtn").addEventListener("click", loadDashboard);
document.getElementById("openDocsBtn").addEventListener("click", () => window.open("/docs", "_blank"));
document.getElementById("searchInput").addEventListener("input", () => renderTable(latestRows));
document.getElementById("quickAnalyzeBtn").addEventListener("click", () => analyzeTickerOnTheFly(document.getElementById("quickTickerInput").value));
document.getElementById("quickTickerInput").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); analyzeTickerOnTheFly(e.target.value); } });
document.getElementById("loadDetailBtn").addEventListener("click", () => loadTickerDetail(document.getElementById("tickerSelect").value));
document.getElementById("tickerSelect").addEventListener("change", (e) => loadTickerDetail(e.target.value));
document.getElementById("loadForwardBtn").addEventListener("click", () => loadForwardPredictions());
document.getElementById("forwardAddTickerBtn").addEventListener("click", addForwardTickerFromInput);
document.getElementById("forwardExtraTicker").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addForwardTickerFromInput(); } });
document.getElementById("forwardCustomDays").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); loadForwardPredictions(); } });
document.getElementById("loadIncidentBtn").addEventListener("click", () => renderIncidentChart(document.getElementById("incidentDateSelect").value));
document.getElementById("incidentDateSelect").addEventListener("change", (e) => renderIncidentChart(e.target.value));
document.getElementById("incidentWindowSelect").addEventListener("change", () => renderIncidentChart(document.getElementById("incidentDateSelect").value));
document.getElementById("techZoomInBtn").addEventListener("click", () => technicalChart?.zoom(1.2));
document.getElementById("techZoomOutBtn").addEventListener("click", () => technicalChart?.zoom(0.8));
document.getElementById("techZoomResetBtn").addEventListener("click", () => technicalChart?.resetZoom());
document.getElementById("incidentZoomInBtn").addEventListener("click", () => incidentChart?.zoom(1.2));
document.getElementById("incidentZoomOutBtn").addEventListener("click", () => incidentChart?.zoom(0.8));
document.getElementById("incidentZoomResetBtn").addEventListener("click", () => incidentChart?.resetZoom());
document.getElementById("cmpTf1").addEventListener("change", () => latestDetailPayload && renderComparisonCharts(latestDetailPayload));
document.getElementById("cmpTf2").addEventListener("change", () => latestDetailPayload && renderComparisonCharts(latestDetailPayload));
document.getElementById("cmpTf3").addEventListener("change", () => latestDetailPayload && renderComparisonCharts(latestDetailPayload));
document.getElementById("cmpFrom1").addEventListener("change", () => { document.getElementById("cmpTf1").value="custom"; latestDetailPayload && renderComparisonCharts(latestDetailPayload); });
document.getElementById("cmpTo1").addEventListener("change", () => { document.getElementById("cmpTf1").value="custom"; latestDetailPayload && renderComparisonCharts(latestDetailPayload); });
document.getElementById("cmpFrom2").addEventListener("change", () => { document.getElementById("cmpTf2").value="custom"; latestDetailPayload && renderComparisonCharts(latestDetailPayload); });
document.getElementById("cmpTo2").addEventListener("change", () => { document.getElementById("cmpTf2").value="custom"; latestDetailPayload && renderComparisonCharts(latestDetailPayload); });
document.getElementById("cmpFrom3").addEventListener("change", () => { document.getElementById("cmpTf3").value="custom"; latestDetailPayload && renderComparisonCharts(latestDetailPayload); });
document.getElementById("cmpTo3").addEventListener("change", () => { document.getElementById("cmpTf3").value="custom"; latestDetailPayload && renderComparisonCharts(latestDetailPayload); });
document.getElementById("compareAnalogsBtn").addEventListener("click", applySelectedAnalogWindows);
document.getElementById("comparePickedBtn").addEventListener("click", applyPickedEventWindows);
document.getElementById("runBacktestBtn").addEventListener("click", runBacktest);
document.getElementById("btFilterTicker").addEventListener("input", rerenderBacktestWithFilters);
document.getElementById("btFilterStatus").addEventListener("change", rerenderBacktestWithFilters);
document.getElementById("btFilterPrediction").addEventListener("change", rerenderBacktestWithFilters);
document.getElementById("btFilterOutcome").addEventListener("change", rerenderBacktestWithFilters);
document.getElementById("btFilterPartialOnly").addEventListener("change", rerenderBacktestWithFilters);
document.querySelectorAll("th.bt-sortable").forEach((th) => {
  th.addEventListener("click", () => {
    const field = th.getAttribute("data-bt-sort-field"); if (!field) return;
    if (btSortField === field) btSortDir = btSortDir === "asc" ? "desc" : "asc";
    else { btSortField = field; btSortDir = "asc"; }
    rerenderBacktestWithFilters();
  });
});
document.querySelectorAll("th.sortable").forEach((th) => {
  th.addEventListener("click", () => {
    const field = th.getAttribute("data-sort-field"); if (!field) return;
    if (tableSortField === field) tableSortDir = tableSortDir === "asc" ? "desc" : "asc";
    else { tableSortField = field; tableSortDir = "desc"; }
    renderTable(latestRows);
  });
});

Chart.defaults.devicePixelRatio = Math.min(2, window.devicePixelRatio || 1);

// ---------------------------------------------------------------------------
// Tab Navigation
// ---------------------------------------------------------------------------
let deepTabLoaded = false;
async function loadDeepTabData() {
  if (deepTabLoaded) return;
  deepTabLoaded = true;
  setWorking(true, "Loading deep analysis data...");
  try {
    if (latestTickers.length) await loadTickerDetail(latestTickers[0]);
    await runBacktest();
  } catch (e) { console.error("Deep tab load error", e); }
  finally { setWorking(false); }
}

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
    btn.classList.add("active");
    const tabId = btn.getAttribute("data-tab");
    const panel = document.getElementById(`tab-${tabId}`);
    if (panel) panel.classList.add("active");
    if (tabId === "deep") loadDeepTabData();
  });
});

// ---------------------------------------------------------------------------
// Overview Tab — Ticker Snapshot (original dashboard style)
// ---------------------------------------------------------------------------
let ovPriceChart = null;

function renderOverviewExpert(prefix, expert) {
  if (!expert) return;
  const sigEl = document.getElementById(`ov${prefix}Signal`);
  const ratEl = document.getElementById(`ov${prefix}Rationale`);
  const action = normalizeActionLabel(expert.signal || "HOLD");
  const conf = (Number(expert.confidence || 0) * 100).toFixed(1);
  sigEl.innerHTML = `${badge(action)} <span style="color:var(--muted);font-size:12px;margin-left:6px">score ${Number(expert.raw_score||0).toFixed(3)} · conf ${conf}%</span>`;
  ratEl.innerHTML = (expert.rationale || []).slice(0, 4).map((r) => `<div>${r}</div>`).join("");
}

function renderOverviewDetail(payload) {
  const analysis = payload.analysis || {};
  const consensus = analysis.consensus || {};
  const market = analysis.market || {};
  const risk = analysis.risk || {};
  const experts = analysis.experts || {};
  const we = payload.wave_edge_indicator || {};
  const ticker = String(market.ticker || payload.ticker || "").toUpperCase();

  document.getElementById("ovTicker").textContent = ticker;
  document.getElementById("ovDetailStatus").textContent = `Loaded ${ticker} snapshot.`;

  // Show hidden sections
  ["ovSummaryStrip","ovExpertCards","ovRiskNews","ovChartWrap"].forEach((id) => {
    document.getElementById(id).style.display = "";
  });

  // Summary strip
  const action = normalizeActionLabel(consensus.action || "HOLD");
  document.getElementById("ovPrice").textContent = `$${Number(market.price||0).toFixed(2)}`;
  document.getElementById("ovAction").innerHTML = badge(action);
  document.getElementById("ovConf").textContent = `${(Number(consensus.confidence||0)*100).toFixed(1)}%`;
  document.getElementById("ovPos").textContent = `${Number(consensus.recommended_position_pct||0).toFixed(2)}%`;
  document.getElementById("ovWave").textContent = `${Number(we.score||0).toFixed(1)} ${we.label||""}`;
  document.getElementById("ovQuality").innerHTML = qualityBadge(market.data_quality || "unknown");

  // Expert cards
  renderOverviewExpert("Eq", experts.wall_street_quant);
  renderOverviewExpert("Ef", experts.harvard_fundamental);
  renderOverviewExpert("Em", experts.stanford_ml);

  // Risk
  document.getElementById("ovRiskList").innerHTML = (risk.risk_notes || []).map((n) => `<li>${n}</li>`).join("") || "<li>No risk data</li>";

  // News
  const headlines = market.news_headlines || [];
  document.getElementById("ovNewsList").innerHTML = headlines.length
    ? headlines.slice(0, 6).map((h) => `<li>${h}</li>`).join("")
    : "<li>No headlines available</li>";

  // Price chart
  renderOverviewPriceChart(payload.chart, ticker);
}

function renderOverviewPriceChart(chart, ticker) {
  if (!chart || !chart.dates) return;
  if (ovPriceChart) ovPriceChart.destroy();

  const dates = chart.dates || [];
  const close = chart.close || [];
  const sma20 = chart.sma20 || [];
  const sma50 = chart.sma50 || [];
  const markers = chart.analog_markers || [];
  const markerLabels = chart.analog_marker_labels || [];

  ovPriceChart = new Chart(document.getElementById("ovPriceChart"), {
    type: "line",
    data: { labels: dates, datasets: [
      { label: "Close", data: close, borderColor: "#9EE2FF", backgroundColor: "rgba(158,226,255,0.05)", borderWidth: 2, pointRadius: 0, fill: true },
      { label: "SMA 20", data: sma20, borderColor: "#2ecc71", borderWidth: 1.2, pointRadius: 0, borderDash: [4,2] },
      { label: "SMA 50", data: sma50, borderColor: "#f1c40f", borderWidth: 1.2, pointRadius: 0, borderDash: [4,2] },
      { label: "Analog", data: markers, borderColor: "rgba(255,91,110,0.8)", backgroundColor: "rgba(255,91,110,0.6)", pointRadius: markers.map((v) => v !== null ? 6 : 0), pointStyle: "triangle", showLine: false },
    ]},
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: true, position: "top", labels: { color: "#e5ecff", font: { size: 11 } } },
        tooltip: { callbacks: {
          title: (items) => dates[items[0].dataIndex] || "",
          label: (item) => {
            if (item.dataset.label === "Analog" && item.raw !== null) {
              const lbl = markerLabels[item.dataIndex];
              return lbl ? `${lbl}: $${Number(item.raw).toFixed(2)}` : `Analog: $${Number(item.raw).toFixed(2)}`;
            }
            return `${item.dataset.label}: $${Number(item.raw).toFixed(2)}`;
          },
        }},
      },
      scales: {
        x: { ticks: { color: "#e5ecff", font: { size: 9 }, maxRotation: 45, maxTicksLimit: 15 }, grid: { color: "rgba(255,255,255,0.03)" } },
        y: { ticks: { color: "#e5ecff", font: { size: 10 } }, grid: { color: "rgba(255,255,255,0.03)" } },
      },
    },
  });
}

async function loadOverviewTicker(ticker) {
  if (!ticker) return;
  setWorking(true, `Loading ${ticker} snapshot...`);
  document.getElementById("ovDetailStatus").textContent = `Loading ${ticker}...`;
  try {
    const response = await fetch(`/dashboard/ticker/${encodeURIComponent(ticker)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    renderOverviewDetail(payload);
  } catch (error) {
    document.getElementById("ovDetailStatus").textContent = `Failed: ${error.message}`;
  } finally { setWorking(false); }
}

// Overview tab event listeners
document.getElementById("ovLoadDetailBtn").addEventListener("click", () => loadOverviewTicker(document.getElementById("ovTickerSelect").value));
document.getElementById("ovTickerSelect").addEventListener("change", (e) => loadOverviewTicker(e.target.value));

// Make watchlist rows clickable to load overview ticker
document.getElementById("watchlistBody").addEventListener("click", (e) => {
  const row = e.target.closest("tr");
  if (!row) return;
  const ticker = row.children[0]?.textContent?.trim();
  if (ticker) {
    document.getElementById("ovTickerSelect").value = ticker;
    loadOverviewTicker(ticker);
  }
});

// Sync overview ticker select when watchlist loads
function syncOverviewTickerSelect(tickers) {
  const select = document.getElementById("ovTickerSelect");
  select.innerHTML = tickers.map((t) => `<option value="${t}">${t}</option>`).join("");
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
loadDashboard();
