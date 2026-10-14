"use strict";
// Gold News Agent dashboard. Plain JS: no framework, no eval, no innerHTML for data.
// Every string that came from a news source or the API is written with textContent.

const WIB_OFFSET = 7 * 3600; // the chart library shows UTC; shift bars so the axis reads WIB
const TF_SECONDS = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400 };
const CHART_TF = "M5";

const wibFormat = new Intl.DateTimeFormat("id-ID", {
  timeZone: "Asia/Jakarta", weekday: "short", day: "numeric", month: "short",
  hour: "2-digit", minute: "2-digit",
});
const wibClock = new Intl.DateTimeFormat("id-ID", {
  timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", second: "2-digit",
});

const $ = (selector) => document.querySelector(selector);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = String(value);
    else node.setAttribute(key, String(value));
  }
  for (const child of children) {
    if (child === undefined || child === null) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

const num = (value, places = 2) =>
  value === null || value === undefined || Number.isNaN(value) ? "–" : Number(value).toFixed(places);
const signed = (value, places = 2) =>
  value === null || value === undefined ? "–" : (value >= 0 ? "+" : "") + Number(value).toFixed(places);
const pct = (value) => (value === null || value === undefined ? "–" : `${Math.round(value * 100)}%`);
const wib = (iso) => (iso ? wibFormat.format(new Date(iso)) : "–");

function duration(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return `${s} dtk`;
  const minutes = Math.round(s / 60);
  if (minutes < 60) return `${minutes} mnt`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `${hours} jam ${minutes % 60} mnt` : `${hours} jam`;
}

async function api(path) {
  const response = await fetch(path, { credentials: "same-origin" });
  if (response.status === 401) {
    location.href = "/login";
    throw new Error("login diperlukan");
  }
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

// ------------------------------------------------------------------ live page

function levelBox(label, value) {
  return el("div", {}, el("span", { text: label }), el("b", { text: value }));
}

function componentBars(components) {
  const wrap = el("div", { class: "bars" });
  const names = { N: "Berita", M: "Makro", T: "Teknikal", P: "Harga" };
  for (const [key, label] of Object.entries(names)) {
    const value = components ? components[key] : 0;
    const bar = el("div", { class: "bar" });
    const fill = el("i", { class: value >= 0 ? "pos" : "neg" });
    fill.style.width = `${Math.min(50, Math.abs(value) * 50)}%`;
    bar.append(fill);
    wrap.append(el("span", { class: "muted", text: label }), bar, el("span", { text: signed(value) }));
  }
  return wrap;
}

function renderSignalCard(payload) {
  const card = clear($("#signal-card"));
  const state = payload.state;
  const active = payload.active;
  const action = active ? active.action : state ? state.action : "WAIT";
  const head = el("div", { class: "signal-head" },
    el("span", { class: `action ${action}`, text: action }),
    el("span", { class: "muted", text: active ? `Confidence ${active.confidence}/100` :
      state ? `skor ${signed(state.composite_score)} · confidence ${state.confidence}` : "status belum ada" }));
  if (payload.halted) head.append(el("span", { class: "badge warn", text: "KILL SWITCH" }));
  head.append(el("span", { class: "badge", text: payload.mode }));
  card.append(head);

  if (active) {
    card.append(el("p", { class: "muted",
      text: `${active.created_at_wib} · ${active.session} · ${active.setup_type}` }));
    card.append(el("div", { class: "levels" },
      levelBox("Entry", `${num(active.entry_low)}–${num(active.entry_high)}`),
      levelBox("Stop loss", num(active.stop_loss)),
      levelBox("TP1 (1R)", num(active.tp1)),
      levelBox("TP2 (2R)", num(active.tp2)),
      levelBox("TP3", num(active.tp3)),
      levelBox("Berlaku s/d", active.valid_until_wib),
      levelBox("Lot", active.lot_size !== null ? String(active.lot_size) : "–")));
    if (active.lot_note) card.append(el("p", { class: "muted", text: `Lot: ${active.lot_note}` }));
    const reasons = el("ul", { class: "notes" });
    for (const reason of active.top_reasons) reasons.append(el("li", { text: reason }));
    card.append(el("h2", { text: "Alasan" }), reasons);
    const cancel = el("ul", { class: "notes warn" });
    for (const item of active.invalidation) cancel.append(el("li", { text: item }));
    card.append(el("h2", { text: "Batal jika" }), cancel);
  } else if (state) {
    const waits = el("ul", { class: "notes warn" });
    for (const reason of state.wait_reasons) waits.append(el("li", { text: reason }));
    if (state.wait_reasons.length) card.append(el("h2", { text: "Menunggu karena" }), waits);
    const reasons = el("ul", { class: "notes" });
    for (const reason of state.reasons) reasons.append(el("li", { text: reason }));
    if (state.reasons.length) card.append(el("h2", { text: "Faktor saat ini" }), reasons);
  }
  card.append(componentBars(state ? state.components : null));
  if (payload.next_event) {
    const ev = payload.next_event;
    card.append(el("p", { class: "countdown",
      text: `Event berikutnya: ${ev.country} ${ev.title} · ${ev.wib} WIB (${duration(ev.seconds_until)} lagi)` }));
  }
  card.append(el("p", { class: "muted", text: "Bukan saran keuangan. Keputusan & risiko di tanganmu." }));
}

function renderPrice(context) {
  const price = $("#price");
  if (!context || context.mid === null) {
    price.textContent = "data harga tidak tersedia";
    price.classList.add("stale");
    return;
  }
  const proxy = context.proxy ? " (proxy)" : "";
  price.textContent = `${num(context.mid)} · ${context.session}${proxy}${context.stale ? " · BASI" : ""}`;
  price.classList.toggle("stale", Boolean(context.stale));
}

function newsItem(item) {
  const meta = el("div", { class: "meta" },
    el("span", { class: `badge ${item.direction}`, text: item.direction }),
    el("span", { text: `dampak ${item.impact}/5` }),
    el("span", { text: item.category }),
    el("span", { text: `${item.first_seen_wib} WIB` }),
    el("span", { text: `latensi ${num(item.latency_seconds, 1)} dtk` }),
    el("span", { text: item.sources.join(", ") }));
  if (item.needs_confirmation) meta.append(el("span", { class: "badge warn", text: "belum terkonfirmasi" }));
  if (item.ai_pending) meta.append(el("span", { class: "badge", text: "aturan (AI tertunda)" }));
  return el("li", { "data-id": item.id },
    el("div", { text: item.summary || item.title }),
    el("div", { class: "muted", text: item.title }), meta);
}

function renderNews(items) {
  const list = clear($("#news"));
  if (!items.length) list.append(el("li", { class: "muted", text: "Belum ada berita relevan." }));
  for (const item of items) list.append(newsItem(item));
}

function prependNews(item) {
  const list = $("#news");
  const existing = list.querySelector(`li[data-id="${CSS.escape(item.id)}"]`);
  if (existing) existing.replaceWith(newsItem(item));
  else list.prepend(newsItem(item));
  while (list.children.length > 80) list.removeChild(list.lastChild);
}

// ------------------------------------------------------------------ chart

const chartState = { chart: null, series: null, markers: null, last: null };

function initChart() {
  const lib = window.LightweightCharts;
  if (!lib || chartState.chart) return;
  const chart = lib.createChart($("#chart"), {
    autoSize: true,
    // The logo injects an inline <style> the CSP forbids; the footer carries the attribution
    // notice and the tradingview.com link the library's license asks for instead.
    layout: { background: { color: "#151a23" }, textColor: "#d7dce5", attributionLogo: false },
    grid: { vertLines: { color: "#1f2533" }, horzLines: { color: "#1f2533" } },
    timeScale: { timeVisible: true, secondsVisible: false },
    rightPriceScale: { borderColor: "#262e3f" },
  });
  chartState.chart = chart;
  chartState.series = chart.addSeries(lib.CandlestickSeries, {
    upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
    wickUpColor: "#26a69a", wickDownColor: "#ef5350",
  });
  chartState.markers = lib.createSeriesMarkers(chartState.series, []);
}

async function loadChart() {
  initChart();
  if (!chartState.series) return;
  const [bars, marks] = await Promise.all([
    api(`/api/candles?tf=${CHART_TF}`), api("/api/markers?hours=24")]);
  const shifted = bars.map((b) => ({ ...b, time: b.time + WIB_OFFSET }));
  chartState.series.setData(shifted);
  chartState.last = shifted.length ? { ...shifted[shifted.length - 1] } : null;
  const step = TF_SECONDS[CHART_TF];
  const snap = (t) => Math.floor((t + WIB_OFFSET) / step) * step;
  const markers = [
    ...marks.news.map((n) => ({
      time: snap(n.time),
      position: n.direction === "bearish" ? "aboveBar" : "belowBar",
      shape: "circle",
      color: n.direction === "bullish" ? "#26a69a" : n.direction === "bearish" ? "#ef5350" : "#8a93a6",
      text: `${n.impact}`,
    })),
    ...marks.signals.map((s) => ({
      time: snap(s.time),
      position: s.action === "BUY" ? "belowBar" : "aboveBar",
      shape: s.action === "BUY" ? "arrowUp" : "arrowDown",
      color: s.action === "BUY" ? "#26a69a" : "#ef5350",
      text: s.action,
    })),
  ].sort((a, b) => a.time - b.time);
  chartState.markers.setMarkers(markers);
}

function onTick(tick) {
  if (!chartState.series) return;
  const step = TF_SECONDS[CHART_TF];
  const time = Math.floor((tick.time + WIB_OFFSET) / step) * step;
  const last = chartState.last;
  const bar = last && last.time === time
    ? { ...last, high: Math.max(last.high, tick.mid), low: Math.min(last.low, tick.mid), close: tick.mid }
    : { time, open: tick.mid, high: tick.mid, low: tick.mid, close: tick.mid };
  if (last && time < last.time) return; // late tick
  chartState.series.update(bar);
  chartState.last = bar;
}

// ------------------------------------------------------------------ outlook

let countdownTarget = null;

function renderOutlook(outlook, statePayload) {
  const body = clear($("#outlook-table tbody"));
  const notes = clear($("#outlook-notes"));
  $("#outlook-narrative").textContent = "";
  if (!outlook) {
    $("#outlook-meta").textContent = "· belum tersedia";
    return;
  }
  $("#outlook-meta").textContent = `· ${wib(outlook.created_at)} WIB (${outlook.trigger})`;
  for (const row of outlook.rows) {
    const volatility = row.expected_range_usd !== null
      ? `${row.volatility_label} (~$${num(row.expected_range_usd, 0)})` : row.volatility_label;
    const classes = [row.bias === "PASAR TUTUP" ? "closed" : "", row.events.length ? "event" : ""]
      .filter(Boolean).join(" ");
    const scenario = el("td", { text: row.scenario, "data-label": "Skenario" });
    if (row.risky.length) scenario.append(el("div", { class: "muted", text: `⚠ ${row.risky.join("; ")}` }));
    body.append(el("tr", { class: classes },
      el("td", { text: row.label_wib }),
      el("td", { text: row.session, "data-label": "Sesi" }),
      el("td", { text: row.events.join(", ") || "–", "data-label": "Event" }),
      el("td", { text: volatility, "data-label": "Volatilitas" }),
      el("td", { text: row.bias, "data-label": "Bias" }),
      scenario,
      el("td", { class: "num", text: row.confidence === null ? "–" : row.confidence,
        "data-label": "Confidence" })));
  }
  if (outlook.narrative_id) $("#outlook-narrative").textContent = outlook.narrative_id;
  for (const note of outlook.notes) notes.append(el("li", { text: note }));
  const next = statePayload && statePayload.next_event;
  countdownTarget = next ? { label: `${next.country} ${next.title} · ${next.wib} WIB`,
    at: Date.now() + next.seconds_until * 1000 } : null;
  tickCountdown();
}

function tickCountdown() {
  const node = $("#countdown");
  if (!countdownTarget) {
    node.textContent = "Tidak ada event high-impact dalam 7 hari.";
    return;
  }
  const seconds = (countdownTarget.at - Date.now()) / 1000;
  node.textContent = seconds > 0
    ? `Event berikutnya: ${countdownTarget.label} — ${duration(seconds)} lagi`
    : `Event berikutnya: ${countdownTarget.label} — sedang berlangsung`;
}

// ------------------------------------------------------------------ calendar

function renderCalendar(rows) {
  const body = clear($("#calendar-table tbody"));
  if (!rows.length) {
    body.append(el("tr", {}, el("td", { colspan: 6, class: "muted", text: "Tidak ada event." })));
    return;
  }
  for (const row of rows) {
    const playbookCell = el("td", { "data-label": "Playbook" });
    if (row.playbook) {
      const details = el("details", {}, el("summary", { text: "lihat" }));
      const list = el("ul", { class: "notes" });
      for (const bucket of row.playbook.buckets) {
        const range = bucket.actual_min !== null || bucket.actual_max !== null
          ? `${bucket.actual_min ?? "…"} – ${bucket.actual_max ?? "…"}${row.playbook.unit}`
          : `z ${bucket.hawkish_z_min ?? "…"} – ${bucket.hawkish_z_max ?? "…"}`;
        list.append(el("li", { text: `${range}: ${bucket.hint_id}` }));
      }
      details.append(list);
      playbookCell.append(details);
    } else {
      playbookCell.textContent = "–";
    }
    body.append(el("tr", { class: row.impact === "high" ? "event" : "" },
      el("td", { text: `${row.wib} · ${row.country} ${row.title}` }),
      el("td", { "data-label": "Impact" },
        el("span", { class: `badge ${row.impact === "high" ? "SELL" : "warn"}`, text: row.impact })),
      el("td", { class: "num", text: row.forecast ?? "–", "data-label": "Forecast" }),
      el("td", { class: "num", text: row.previous ?? "–", "data-label": "Previous" }),
      el("td", { class: "num", text: row.actual ?? "–", "data-label": "Actual" }),
      playbookCell));
  }
}

// ------------------------------------------------------------------ performance

function tile(label, value) {
  return el("div", { class: "tile" }, el("div", { class: "label", text: label }),
    el("div", { class: "value", text: value }));
}

function groupTable(title, groups) {
  const body = el("tbody");
  for (const g of groups) {
    body.append(el("tr", {},
      el("td", { text: g.label }), el("td", { class: "num", text: g.n }),
      el("td", { class: "num", text: g.win_rate === null ? "–" :
        `${pct(g.win_rate)} [${pct(g.wilson[0])}–${pct(g.wilson[1])}]` }),
      el("td", { class: "num", text: signed(g.avg_r) })));
  }
  if (!groups.length) body.append(el("tr", {}, el("td", { colspan: 4, class: "muted", text: "belum ada data" })));
  return el("article", { class: "card" }, el("h2", { text: title }),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", { text: "Grup" }), el("th", { text: "n" }),
        el("th", { text: "Win rate [95% CI]" }), el("th", { text: "Rata-rata R" }))), body)));
}

function renderPerformance(perf) {
  const tiles = clear($("#perf-tiles"));
  tiles.append(
    tile("Sinyal", perf.n_signals), tile("Tertutup", perf.n_closed), tile("Terbuka", perf.n_open),
    tile("Win rate", perf.win_rate === null ? "–" :
      `${pct(perf.win_rate)} [${pct(perf.wilson[0])}–${pct(perf.wilson[1])}]`),
    tile("Expectancy", perf.avg_r === null ? "–" : `${signed(perf.avg_r)}R` +
      (perf.expectancy_ci ? ` [${signed(perf.expectancy_ci[0])}…${signed(perf.expectancy_ci[1])}]` : "")),
    tile("Total", `${signed(perf.total_r)}R`), tile("Max drawdown", `${num(perf.max_drawdown_r)}R`));
  const notes = clear($("#perf-notes"));
  for (const note of perf.notes) notes.append(el("li", { text: note }));
  const groups = clear($("#perf-groups"));
  groups.append(groupTable("Per kategori berita", perf.by_category),
    groupTable("Per sesi", perf.by_session), groupTable("Per jam (WIB)", perf.by_hour),
    groupTable("Per setup", perf.by_setup), groupTable("Kalibrasi confidence", perf.calibration));
}

// ------------------------------------------------------------------ health

function renderHealth(health) {
  const tiles = clear($("#health-tiles"));
  const ctx = health.context;
  tiles.append(
    tile("Umur data harga", health.context_age_seconds === null ? "tidak ada" : duration(health.context_age_seconds)),
    tile("Sumber harga", ctx ? `${ctx.source || "–"}${ctx.proxy ? " (proxy)" : ""}` : "–"),
    tile("Biaya LLM hari ini", `$${num(health.llm_cost_today, 2)} / $${num(health.llm_budget, 2)}`),
    tile("Sumber berita", `${health.sources.filter((s) => s.status === "ok").length} ok / ${health.sources.length}`));
  const body = clear($("#sources-table tbody"));
  for (const s of health.sources) {
    const badge = s.status === "ok" ? "BUY" : s.status === "down" ? "SELL" : "warn";
    body.append(el("tr", {},
      el("td", { text: s.source_id }),
      el("td", { "data-label": "Status" }, el("span", { class: `badge ${badge}`, text: s.status })),
      el("td", { text: wib(s.last_success_at), "data-label": "Sukses terakhir" }),
      el("td", { text: wib(s.last_item_at), "data-label": "Item terakhir" }),
      el("td", { class: "num", text: s.consecutive_errors, "data-label": "Error beruntun" }),
      el("td", { class: "num", text: s.lag_p50 === null ? "–" : num(s.lag_p50, 1), "data-label": "Lag p50" }),
      el("td", { class: "num", text: s.lag_p95 === null ? "–" : num(s.lag_p95, 1), "data-label": "Lag p95" }),
      el("td", { class: "muted", text: s.last_error || "–", "data-label": "Error terakhir" })));
  }
  if (!health.sources.length) body.append(el("tr", {}, el("td", { colspan: 8, class: "muted", text: "belum ada data sumber" })));
  const stages = clear($("#stages-table tbody"));
  for (const s of health.stages) {
    stages.append(el("tr", {}, el("td", { text: s.stage }), el("td", { text: s.source_id }),
      el("td", { class: "num", text: s.samples }), el("td", { class: "num", text: num(s.p50_seconds, 1) }),
      el("td", { class: "num", text: num(s.p95_seconds, 1) })));
  }
  const beats = clear($("#heartbeats"));
  for (const [name, beat] of Object.entries(health.heartbeats)) {
    beats.append(el("li", {}, el("b", { text: name }), ` · ${duration(beat.age_seconds)} lalu`));
  }
  const alerts = clear($("#alerts"));
  for (const a of health.alerts) {
    alerts.append(el("li", {}, el("span", { class: `badge ${a.severity === "critical" ? "SELL" : a.severity === "warning" ? "warn" : ""}`, text: a.kind }),
      ` ${a.title}`, el("div", { class: "meta", text: `${wib(a.created_at)} · ${a.detail}` })));
  }
  const notes = clear($("#notifications"));
  for (const n of health.notifications) {
    notes.append(el("li", { text: `${wib(n.created_at)} · ${n.kind} (${n.priority}) · ${n.sent ? "terkirim" : n.reason || "tertunda"}` }));
  }
}

// ------------------------------------------------------------------ routing + live socket

const loaders = {
  live: async () => {
    const [state, news] = await Promise.all([api("/api/state"), api("/api/news")]);
    renderSignalCard(state);
    renderPrice(state.context);
    renderNews(news);
    await loadChart();
  },
  outlook: async () => {
    const [outlook, state] = await Promise.all([api("/api/outlook"), api("/api/state")]);
    renderOutlook(outlook, state);
  },
  kalender: async () => renderCalendar(await api("/api/calendar")),
  performa: async () => renderPerformance(await api("/api/performance")),
  kesehatan: async () => renderHealth(await api("/api/health")),
};

let currentPage = "live";

async function show(page) {
  currentPage = loaders[page] ? page : "live";
  for (const section of document.querySelectorAll(".page")) section.hidden = section.id !== `page-${currentPage}`;
  for (const link of document.querySelectorAll("nav a")) link.classList.toggle("active", link.dataset.page === currentPage);
  try {
    await loaders[currentPage]();
  } catch (error) {
    console.error(error);
  }
}

let refreshTimer = null;
function refreshSoon() { // coalesce bursts of state/signal events into one fetch
  if (refreshTimer) return;
  refreshTimer = setTimeout(async () => {
    refreshTimer = null;
    if (currentPage === "live") {
      const state = await api("/api/state");
      renderSignalCard(state);
      renderPrice(state.context);
    }
  }, 500);
}

function connect(delay = 1000) {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${location.host}/ws`);
  socket.addEventListener("open", () => { $("#conn").classList.remove("off"); delay = 1000; });
  socket.addEventListener("close", () => {
    $("#conn").classList.add("off");
    setTimeout(() => connect(Math.min(delay * 2, 30000)), delay);
  });
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "tick") onTick(message.data);
    else if (message.type === "context") renderPrice(message.data);
    else if (message.type === "news" && currentPage === "live") prependNews(message.data);
    else if (message.type === "state" || message.type === "signal") refreshSoon();
    else if (message.type === "outlook" && currentPage === "outlook") show("outlook");
  });
}

window.addEventListener("hashchange", () => show(location.hash.slice(1)));
window.addEventListener("DOMContentLoaded", () => {
  show(location.hash.slice(1) || "live");
  connect();
  setInterval(tickCountdown, 1000);
});
