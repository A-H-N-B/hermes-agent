"use strict";
// Gold News Agent dashboard. Plain JS: no framework, no eval, no innerHTML for data.
// Every string that came from a news source or the API is written with textContent.

const WIB_OFFSET = 7 * 3600; // the chart library shows UTC; shift bars so the axis reads WIB
const TF_SECONDS = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400, W1: 604800, MN: 2592000 };
const RING_TFS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN"];
const DEFAULT_TF = "M5";

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

const MODEL_NAMES = {
  "claude-fable-5-1": "Fable 5.1", "claude-opus-5-5": "Opus 5.5", "claude-opus-5": "Opus 5",
  "claude-opus-4-8": "Opus 4.8", "claude-sonnet-5-5": "Sonnet 5.5",
};
const modelName = (model) => MODEL_NAMES[model] || model || "AI";

// The plan's chances within its validity (signals/odds.py): a three-part bar and one line.
function oddsBlock(o) {
  const bar = el("div", { class: "odds-bar", title: "hijau TP1 dulu · merah SL dulu · abu-abu tidak tersentuh" });
  for (const [cls, value] of [["tp", o.tp1], ["sl", o.stop], ["none", o.neither]]) {
    const part = el("span", { class: cls });
    part.style.width = `${Math.max(0, value * 100).toFixed(1)}%`; // CSSOM: the CSP forbids inline styles
    bar.append(part);
  }
  const band = o.ev_r_ci95 ? ` (95%: ${signed(o.ev_r_ci95[0])}…${signed(o.ev_r_ci95[1])}R)` : "";
  const basis = o.setups ? `model + ${o.setups} setup serupa di masa lalu` : "model volatilitas (belum ada setup historis)";
  return [
    el("p", { class: "odds" }, el("b", { text: `🎲 Peluang TP1 sebelum SL ${pct(o.tp1)}` }),
      ` · SL dulu ${pct(o.stop)} · tidak tersentuh ${pct(o.neither)} · EV ${signed(o.ev_r)}R${band}`),
    bar,
    el("p", { class: "muted", text: `${basis} · ${o.drift_basis} · volatilitas ${num(o.sigma)}$/candle (${o.sigma_model})` }),
  ];
}

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
    const review = active.review;
    if (review && review.model) {
      const count = review.votes > 1 ? ` · ${review.agree}/${review.votes} suara` : "";
      const names = [...new Set((review.models && review.models.length ? review.models : [review.model]).map(modelName))].join(" + ");
      card.append(el("p", { class: "ai-review" }, el("b", { text: `🤖 Diputuskan AI (${names})${count} · chart ${active.timeframe} · confidence AI ${review.confidence ?? "–"}/100` })));
      const why = el("ul", { class: "notes" });
      for (const reason of review.reasons) why.append(el("li", { text: reason }));
      card.append(why);
      if (review.caution) card.append(el("p", { class: "agree WARN", text: `⚠️ ${review.caution}` }));
      if (review.dissent) card.append(el("p", { class: "muted", text: `🗳️ Suara yang menahan: ${review.dissent}` }));
      if (review.note) card.append(el("p", { class: "muted", text: review.note }));
    } else if (review) {
      card.append(el("p", { class: "muted", text: `⚙️ Tanpa review AI: ${review.note || "AI tidak tersedia"}` }));
    }
    if (active.odds) card.append(...oddsBlock(active.odds));
    const reasons = el("ul", { class: "notes" });
    for (const reason of active.top_reasons) reasons.append(el("li", { text: reason }));
    card.append(el("h2", { text: "Alasan" }), reasons);
    const cancel = el("ul", { class: "notes warn" });
    for (const item of active.invalidation) cancel.append(el("li", { text: item }));
    card.append(el("h2", { text: "Batal jika" }), cancel);
    if (payload.tradingview_code) {
      const status = el("span", { class: "muted", role: "status" });
      const copy = el("button", { type: "button", class: "button", text: "Salin" });
      copy.addEventListener("click", () => copyText(payload.tradingview_code, status));
      card.append(el("h2", { text: "Kode TradingView" }),
        el("p", { class: "tv-code" }, el("code", { text: payload.tradingview_code }), copy, status));
    }
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

// One builder for both candle charts: Live, and the "Gold News Agent" indicator chart on the
// TradingView page, which adds the indicator's session bands and 08:30 New York data marks.
const charts = {};

function makeChart(name, container, indicator) {
  const lib = window.LightweightCharts;
  if (!lib || charts[name]) return charts[name];
  const chart = lib.createChart(container, {
    autoSize: true,
    // The logo injects an inline <style> the CSP forbids; the footer carries the attribution
    // notice and the tradingview.com link the library's license asks for instead.
    layout: { background: { color: "#151a23" }, textColor: "#d7dce5", attributionLogo: false },
    grid: { vertLines: { color: "#1f2533" }, horzLines: { color: "#1f2533" } },
    timeScale: { timeVisible: true, secondsVisible: false },
    rightPriceScale: { borderColor: "#262e3f" },
  });
  // Full-height bars on a hidden scale, drawn first so the candles stay on top.
  const sessions = indicator ? chart.addSeries(lib.HistogramSeries, {
    priceScaleId: "sessions", priceLineVisible: false, lastValueVisible: false,
  }) : null;
  if (sessions) chart.priceScale("sessions").applyOptions({ visible: false, scaleMargins: { top: 0, bottom: 0 } });
  const series = chart.addSeries(lib.CandlestickSeries, {
    upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
    wickUpColor: "#26a69a", wickDownColor: "#ef5350",
  });
  charts[name] = { chart, series, sessions, markers: lib.createSeriesMarkers(series, []), last: null, lines: [], tf: DEFAULT_TF };
  if (name === "live") {
    charts[name].ind = makeIndicatorSeries(lib, chart);
    charts[name].tf = savedTf();
    const forecastLine = (style) => chart.addSeries(lib.LineSeries, {
      lineWidth: 2, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      pointMarkersVisible: true, pointMarkersRadius: 3,
    });
    charts[name].forecast = { target: forecastLine(2), high: forecastLine(1), low: forecastLine(1) };
  }
  if (indicator) {
    // Forecast layer to the right of the last candle: centre path + expected-range band.
    const line = (width, style) => chart.addSeries(lib.LineSeries, {
      lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    const centre = line(2, 2);
    charts[name].projection = {
      centre, upper: line(1, 0), lower: line(1, 0), marks: lib.createSeriesMarkers(centre, []),
    };
  }
  return charts[name];
}

function localMinutes(zone) {
  const format = new Intl.DateTimeFormat("en-GB", { timeZone: zone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  return (unix) => {
    const parts = Object.fromEntries(format.formatToParts(new Date(unix * 1000)).map((p) => [p.type, p.value]));
    return Number(parts.hour) * 60 + Number(parts.minute);
  };
}
const londonMinutes = localMinutes("Europe/London");
const newYorkMinutes = localMinutes("America/New_York");

// Same windows as the agent (config/settings.yaml sessions) and the Pine indicator:
// London 08:00-16:00 London, New York 08:00-17:00 New York, daily break 17:00-18:00 New York.
function sessionColor(unix) {
  const london = londonMinutes(unix);
  const ny = newYorkMinutes(unix);
  const inLondon = london >= 480 && london < 960;
  const inNy = ny >= 480 && ny < 1020;
  if (ny >= 1020 && ny < 1080) return "rgba(138, 147, 166, 0.22)";
  if (inLondon && inNy) return "rgba(245, 176, 65, 0.14)";
  if (inLondon) return "rgba(93, 173, 226, 0.10)";
  if (inNy) return "rgba(155, 89, 182, 0.12)";
  return "rgba(0, 0, 0, 0)";
}

async function loadChart(state) {
  if (!state) return;
  const [bars, marks] = await Promise.all([
    api(`/api/candles?tf=${state.tf}`), api("/api/markers?hours=24")]);
  const shifted = bars.map((b) => ({ ...b, time: b.time + WIB_OFFSET }));
  state.series.setData(shifted);
  state.last = shifted.length ? { ...shifted[shifted.length - 1] } : null;
  const step = TF_SECONDS[state.tf];
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
  ];
  if (state.sessions) {
    state.sessions.setData(bars.map((b) => ({ time: b.time + WIB_OFFSET, value: 1, color: sessionColor(b.time) })));
    for (const b of bars) {
      const ny = newYorkMinutes(b.time);
      if (ny <= 510 && ny + step / 60 > 510) {
        markers.push({ time: b.time + WIB_OFFSET, position: "aboveBar", shape: "square", color: "#f5b041", text: "Data AS" });
      }
    }
  }
  state.markers.setMarkers(markers.sort((a, b) => a.time - b.time));
}

// ------------------------------------------------------------------ indicators (Live chart)
// EMA 20/50/200 and Bollinger(20, 2) over the candles, RSI 14 in its own pane, VWAP and
// support/resistance as price lines; each group can be switched off with the checkboxes.
// Indicators start switched off: each group is created when its box is ticked and removed (with
// its pane, for RSI and Stoch) when unticked, so an empty chart really is empty.
function makeIndicatorSeries(lib, chart) {
  const line = (color, width, style = 0, pane = 0) => chart.addSeries(lib.LineSeries, {
    color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false,
    crosshairMarkerVisible: false,
  }, pane);
  const newPane = () => chart.panes().length;
  const bounds = (series, high, low) => {
    series.createPriceLine({ price: high, color: "#ef5350", lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: String(high) });
    series.createPriceLine({ price: low, color: "#26a69a", lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: String(low) });
  };
  const factories = {
    ema: () => ({ ema20: line("#f5b041", 1), ema50: line("#5dade2", 1), ema200: line("#e74c3c", 2) }),
    bb: () => ({ bb_upper: line("rgba(155, 89, 182, 0.8)", 1, 2), bb_mid: line("rgba(155, 89, 182, 0.5)", 1, 1), bb_lower: line("rgba(155, 89, 182, 0.8)", 1, 2) }),
    sar: () => ({ sar: chart.addSeries(lib.LineSeries, {
      lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: 2, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false,
    }) }),
    rsi: () => {
      const rsi = line("#b39ddb", 1, 0, newPane());
      bounds(rsi, 70, 30);
      return { rsi14: rsi };
    },
    stoch: () => {
      const pane = newPane();
      const k = line("#5dade2", 1, 0, pane);
      bounds(k, 80, 20);
      return { stoch_k: k, stoch_d: line("#f5b041", 1, 0, pane) };
    },
  };
  return { chart, factories, groups: {}, rows: [], lines: { vwap: [], levels: [] }, summary: null };
}

const TOGGLES_KEY = "gna.indicators";

function restoreToggles() {
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem(TOGGLES_KEY) || "[]"); } catch { saved = []; }
  for (const box of document.querySelectorAll("#ind-toggles input")) box.checked = saved.includes(box.dataset.ind);
}

function saveToggles() {
  const on = [...document.querySelectorAll("#ind-toggles input:checked")].map((box) => box.dataset.ind);
  try { localStorage.setItem(TOGGLES_KEY, JSON.stringify(on)); } catch { /* not remembered */ }
}

function indicatorOn(key) {
  const box = document.querySelector(`#ind-toggles input[data-ind=${key}]`);
  return Boolean(box && box.checked);
}

function fillGroup(group, rows) {
  for (const [field, series] of Object.entries(group)) {
    series.setData(rows.map((row) => {
      if (row[field] === null) return { time: row.time + WIB_OFFSET };
      const point = { time: row.time + WIB_OFFSET, value: row[field] };
      // SAR dots: green below price (uptrend), red above (downtrend).
      if (field === "sar") point.color = row.sar_up ? "#26a69a" : "#ef5350";
      return point;
    }));
  }
}

function removeEmptyPanes(chart) {
  const panes = chart.panes();
  for (let i = panes.length - 1; i > 0; i -= 1) {
    if (panes[i].getSeries && panes[i].getSeries().length === 0) chart.removePane(i);
  }
  for (const pane of chart.panes().slice(1)) if (pane.setHeight) pane.setHeight(100);
}

function drawIndicatorLines(state) {
  const ind = state.ind;
  if (!ind || !ind.summary) return;
  for (const key of ["vwap", "levels"]) {
    for (const line of ind.lines[key]) state.series.removePriceLine(line);
    ind.lines[key] = [];
  }
  const summary = ind.summary;
  if (indicatorOn("vwap") && summary.vwap !== null && summary.vwap !== undefined) {
    ind.lines.vwap.push(state.series.createPriceLine({ price: summary.vwap, color: "#00bcd4", lineStyle: 1, lineWidth: 1,
      axisLabelVisible: true, title: summary.vwap_is_twap ? "TWAP" : "VWAP" }));
  }
  if (indicatorOn("levels")) {
    for (const level of summary.levels) {
      const color = level.kind === "support" ? "#26a69a" : level.kind === "resistance" ? "#ef5350" : "#8a93a6";
      ind.lines.levels.push(state.series.createPriceLine({ price: level.price, color, lineStyle: 3, lineWidth: 1,
        axisLabelVisible: false, title: level.name }));
    }
  }
}

function applyIndicatorToggles(state) {
  if (!state || !state.ind) return;
  const ind = state.ind;
  for (const [key, make] of Object.entries(ind.factories)) {
    const on = indicatorOn(key);
    if (on && !ind.groups[key]) {
      ind.groups[key] = make();
      fillGroup(ind.groups[key], ind.rows);
    } else if (!on && ind.groups[key]) {
      for (const series of Object.values(ind.groups[key])) ind.chart.removeSeries(series);
      delete ind.groups[key];
    }
  }
  removeEmptyPanes(ind.chart);
  drawIndicatorLines(state);
}

async function loadIndicators(state) {
  if (!state || !state.ind) return;
  const payload = await api(`/api/indicators?tf=${state.tf}`);
  state.ind.rows = payload.series;
  for (const group of Object.values(state.ind.groups)) fillGroup(group, payload.series);
  state.ind.summary = payload.summary;
  applyIndicatorToggles(state);
  renderIndicatorSummary(payload.summary);
}

let lastSummary = null;
let lastState = null;
let lastTiming = null;

function renderIndicatorSummary(summary) {
  lastSummary = summary;
  const chips = clear($("#ind-chips"));
  for (const r of summary.timeframes) {
    chips.append(el("span", { class: `chip verdict-${r.verdict.replace(" ", "-")}`, text: `${r.tf} ${r.verdict}` }));
  }
  if (lastTiming) renderBoard();
  const body = clear($("#ind-table tbody"));
  for (const r of summary.timeframes) {
    body.append(el("tr", {},
      el("td", { text: r.tf }),
      el("td", { class: `verdict-${r.verdict.replace(" ", "-")}`, text: r.verdict }),
      el("td", { class: "num", text: num(r.close) }),
      el("td", { class: "num", text: num(r.ema20) }),
      el("td", { class: "num", text: num(r.ema50) }),
      el("td", { class: "num", text: num(r.ema200) }),
      el("td", { class: "num", text: num(r.rsi14, 0) }),
      el("td", { class: "num", text: num(r.atr14) }),
      el("td", { class: "num", text: r.stoch_k === null || r.stoch_k === undefined ? "–" : `${num(r.stoch_k, 0)}/${num(r.stoch_d, 0)}` }),
      el("td", { class: r.sar_up === null || r.sar_up === undefined ? "muted" : r.sar_up ? "verdict-BUY" : "verdict-SELL",
        text: r.sar === null || r.sar === undefined ? "–" : `${num(r.sar)} ${r.sar_up ? "▲" : "▼"}` }),
      el("td", { class: "num", text: r.macd_hist === null || r.macd_hist === undefined ? "–" : r.macd_hist > 0 ? "▲" : "▼" }),
      el("td", { class: "num", text: num(r.adx, 0) }),
      el("td", { class: "num", text: signed(r.kalman_t, 1) }),
      el("td", { text: REGIME_TEXT[r.regime] || "–" }),
      el("td", { class: "muted", text: r.reasons.join(", ") })));
  }
  if (!summary.timeframes.length) body.append(el("tr", {}, el("td", { colspan: 15, class: "muted", text: "data indikator belum tersedia" })));
  renderTfDetail(summary.timeframes);
  renderDrivers(summary);
  const overall = $("#ind-overall");
  overall.textContent = summary.overall ? `${summary.overall.verdict} (${summary.overall.buy} BUY · ${summary.overall.sell} SELL dari ${summary.overall.of})` : "";
  overall.className = `badge ${summary.overall ? summary.overall.verdict : ""}`;
  const extra = [];
  if (summary.vwap !== null && summary.vwap !== undefined) extra.push(`${summary.vwap_is_twap ? "TWAP" : "VWAP"} ${num(summary.vwap)}`);
  if (summary.nearest_above) extra.push(`resistance terdekat ${summary.nearest_above.name} ${num(summary.nearest_above.price)}`);
  if (summary.nearest_below) extra.push(`support terdekat ${summary.nearest_below.name} ${num(summary.nearest_below.price)}`);
  if (summary.atr_m15 !== null && summary.atr_m15 !== undefined) extra.push(`ATR M15 ${num(summary.atr_m15)}`);
  if (summary.dxy_z_1h !== null && summary.dxy_z_1h !== undefined) extra.push(`DXY z-1j ${signed(summary.dxy_z_1h)}`);
  $("#ind-extra").textContent = extra.join(" · ");
}

// ------------------------------------------------------------------ detail per timeframe
// One small card per timeframe: each indicator's value with a coloured one-word meaning, so the
// numbers are there for those who want them and the meaning for everyone else.
const REGIME_TEXT = { trending: "tren kuat", ranging: "sideways", volatile: "sangat volatil", normal: "normal" };

function readRow(label, value, meaning, tone) {
  return el("div", { class: "read-row" }, el("span", { class: "read-label", text: label }),
    el("span", { class: "read-value", text: value }), el("span", { class: `tone ${tone || ""}`, text: meaning }));
}

function trendRead(r) {
  if (r.ema20 === null || r.ema50 === null || r.ema20 === undefined) return ["–", "belum ada data", ""];
  const up = r.ema20 > r.ema50 && r.close > r.ema50;
  const down = r.ema20 < r.ema50 && r.close < r.ema50;
  return [`EMA20 ${r.ema20 > r.ema50 ? ">" : "<"} EMA50`, up ? "naik" : down ? "turun" : "datar", up ? "up" : down ? "down" : ""];
}

function rsiRead(v) {
  if (v === null || v === undefined) return ["–", "–", ""];
  if (v >= 70) return [num(v, 0), "jenuh beli", "warn"];
  if (v >= 55) return [num(v, 0), "kuat", "up"];
  if (v <= 30) return [num(v, 0), "jenuh jual", "warn"];
  if (v <= 45) return [num(v, 0), "lemah", "down"];
  return [num(v, 0), "netral", ""];
}

function tfCard(r) {
  const verdict = r.verdict || "NETRAL";
  const head = el("h3", {}, el("span", { text: r.tf }),
    el("span", { class: `badge ${verdict.startsWith("BUY") ? "BUY" : verdict.startsWith("SELL") ? "SELL" : "WAIT"}`, text: verdict }));
  const card = el("div", { class: "tf-card" }, head);
  const [trendValue, trendText, trendTone] = trendRead(r);
  card.append(readRow("Tren", trendValue, trendText, trendTone));
  const [rsiValue, rsiText, rsiTone] = rsiRead(r.rsi14);
  card.append(readRow("RSI 14", rsiValue, rsiText, rsiTone));
  if (r.macd_hist !== null && r.macd_hist !== undefined) {
    card.append(readRow("MACD", `hist ${signed(r.macd_hist, 2)}`, r.macd_hist > 0 ? "momentum naik" : "momentum turun", r.macd_hist > 0 ? "up" : "down"));
  }
  if (r.stoch_k !== null && r.stoch_k !== undefined) {
    const rising = r.stoch_k > r.stoch_d;
    const edge = r.stoch_k >= 80 ? ", jenuh beli" : r.stoch_k <= 20 ? ", jenuh jual" : "";
    card.append(readRow("Stoch", `${num(r.stoch_k, 0)}/${num(r.stoch_d, 0)}`, `${rising ? "naik" : "turun"}${edge}`, edge ? "warn" : rising ? "up" : "down"));
  }
  if (r.sar_up !== null && r.sar_up !== undefined) {
    card.append(readRow("SAR", num(r.sar), r.sar_up ? "di bawah harga" : "di atas harga", r.sar_up ? "up" : "down"));
  }
  if (r.adx !== null && r.adx !== undefined) {
    const lead = (r.plus_di ?? 0) >= (r.minus_di ?? 0);
    const strong = r.adx >= 25;
    card.append(readRow("ADX", num(r.adx, 0), strong ? `tren kuat ${lead ? "naik" : "turun"}` : r.adx < 20 ? "tren lemah" : "sedang", strong ? (lead ? "up" : "down") : ""));
  }
  if (r.kalman_t !== null && r.kalman_t !== undefined) {
    const clear = Math.abs(r.kalman_t) >= 2;
    card.append(readRow("Tren Kalman", `t ${signed(r.kalman_t, 1)}`, clear ? `jelas ${r.kalman_t > 0 ? "naik" : "turun"}` : "belum jelas", clear ? (r.kalman_t > 0 ? "up" : "down") : ""));
  }
  if (r.sigma !== null && r.sigma !== undefined) {
    const p = r.vol_pct;
    const word = p === null || p === undefined ? "–" : p >= 0.9 ? "sangat ramai" : p >= 0.7 ? "ramai" : p <= 0.3 ? "sepi" : "normal";
    card.append(readRow("Volatilitas", `±$${num(r.sigma)}/candle`, word, p >= 0.9 ? "warn" : ""));
  }
  if (r.regime) {
    const tone = r.regime === "volatile" ? "warn" : r.regime === "trending" ? "up" : "";
    card.append(readRow("Kondisi", r.hurst !== null && r.hurst !== undefined ? `Hurst ${num(r.hurst)}` : "", REGIME_TEXT[r.regime], tone));
  }
  const up = r.reasons ? r.reasons.length : 0;
  card.append(el("p", { class: "tf-foot", text: `skor ${signed(r.score, 0)} dari ${r.votes} indikator · ${up ? r.reasons.slice(0, 3).join(", ") : "–"}` }));
  return card;
}

function renderTfDetail(reads) {
  const grid = clear($("#ind-detail"));
  for (const r of reads) grid.append(tfCard(r));
}

function pullTone(value) {
  if (value === null || value === undefined) return ["–", ""];
  if (value >= 0.05) return [`▲ mendukung (${signed(value)})`, "up"];
  if (value <= -0.05) return [`▼ menekan (${signed(value)})`, "down"];
  return [`• netral (${signed(value)})`, ""];
}

function renderDrivers(summary) {
  const body = clear($("#cross-table tbody"));
  for (const a of summary.cross || []) {
    const [pull, tone] = pullTone(a.effect);
    const symbol = a.note ? `${a.symbol} · ${a.note}` : a.symbol;
    body.append(el("tr", {},
      el("td", { "data-label": "Pasar" }, el("b", { text: a.label }), el("span", { class: "muted", text: ` ${symbol}` })),
      el("td", { class: "num", "data-label": "Harga", text: num(a.value, a.value > 100 ? 2 : 4) }),
      el("td", { class: "num", "data-label": "15 mnt", text: a.change_15m_pct === null ? "–" : `${signed(a.change_15m_pct)}%` }),
      el("td", { class: "num", "data-label": "1 jam", text: a.change_1h_pct === null ? "–" : `${signed(a.change_1h_pct)}%` }),
      el("td", { class: "num", "data-label": "1 hari", text: a.change_1d_pct === null ? "–" : `${signed(a.change_1d_pct)}%` }),
      el("td", { class: "num", "data-label": "Korelasi", text: a.corr_measured === null ? `${signed(a.corr)} (bawaan)` : signed(a.corr_measured) }),
      el("td", { class: "num", "data-label": "Bobot", text: pct(a.relevance) }),
      el("td", { "data-label": "Tarikan ke emas" }, el("span", { class: `tone ${tone}`, text: pull }))));
  }
  if (!(summary.cross || []).length) {
    body.append(el("tr", {}, el("td", { colspan: 8, class: "muted", text: "data pasar terkait belum tersedia (butuh MT5 terbuka dan login)" })));
  }
  const macro = summary.macro || {};
  const badge = $("#macro-badge");
  badge.textContent = macro.value === null || macro.value === undefined ? "" : `Makro ${signed(macro.value)}`;
  badge.className = `badge ${macro.value > 0.05 ? "BUY" : macro.value < -0.05 ? "SELL" : "WAIT"}`;
  $("#macro-parts").textContent = (macro.parts || []).length
    ? "Skor makro dari: " + macro.parts.map((p) => `${p.label} ${signed(p.pull)}`).join(" · ")
    : "Skor makro belum bisa dihitung.";
  const fund = summary.fundamentals;
  const fundBody = clear($("#fund-table tbody"));
  const fundBadge = $("#fund-badge");
  if (!fund) {
    $("#fund-summary").textContent = "Data fundamental belum tersedia.";
    fundBadge.textContent = "";
    $("#fund-missing").textContent = "";
    return;
  }
  $("#fund-summary").textContent = fund.summary;
  fundBadge.textContent = fund.bias === null ? "" : signed(fund.bias);
  fundBadge.className = `badge ${fund.bias > 0.1 ? "BUY" : fund.bias < -0.1 ? "SELL" : "WAIT"}`;
  for (const f of fund.factors) {
    const [pull, tone] = pullTone(f.effect);
    fundBody.append(el("tr", {},
      el("td", { "data-label": "Faktor", text: f.label }),
      el("td", { "data-label": "Nilai", text: f.value }),
      el("td", { "data-label": "Perubahan", text: f.change || "–" }),
      el("td", { "data-label": "Dampak ke emas" }, el("span", { class: `tone ${tone}`, text: pull })),
      el("td", { "data-label": "Artinya", class: "muted", text: f.note }),
      el("td", { "data-label": "Data", class: "muted", text: f.as_of || "–" })));
  }
  $("#fund-missing").textContent = fund.missing && fund.missing.length ? `Belum ada data: ${fund.missing.join(", ")}.` : "";
}

// ------------------------------------------------------------------ timing ("Kapan klik BUY / SELL")
// The server sends absolute times; every countdown here is recomputed each second against the
// browser clock, so "dalam 12:34" stays right between refreshes.
function clockText(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`;
}

function liveCountdown(iso, before, during) {
  return el("span", { class: "countdown-live", "data-target": String(Date.parse(iso)),
    "data-before": before, "data-during": during, text: "…" });
}

function tickLiveCountdowns() {
  const now = Date.now();
  for (const node of document.querySelectorAll(".countdown-live")) {
    const seconds = (Number(node.dataset.target) - now) / 1000;
    node.textContent = seconds > 0 ? `${node.dataset.before} ${clockText(seconds)}` : node.dataset.during;
  }
}

function bigBox(label, value, note, cls = "") {
  return el("div", { class: `big-box ${cls}` }, el("span", { text: label }), el("b", {}, value),
    note ? (note instanceof Node ? note : el("small", { text: note })) : null);
}

function renderTiming(t) {
  lastTiming = t;
  followTimeframe(t.timeframe ? t.timeframe.tf : null);
  renderBoard();
  renderTimingDetails(t);
}

// The board answers three questions at a glance: what to do now, when the next chance is
// (with a countdown against the WIB clock), and whether the chart's indicators agree.
// Where the news calculation stands right now, from SELL (left) to BUY (right): a signal fires
// when the marker passes either end, so the user can watch it move both ways.
function scoreGauge(t) {
  const n = t.now;
  const fallback = n.mode === "signal" ? (n.action === "BUY" ? t.threshold : -t.threshold) : null;
  const score = n.score ?? fallback;
  const wrap = el("div", { class: "gauge-wrap" });
  if (score === null || score === undefined) {
    wrap.append(el("p", { class: "muted", text: "Hitungan alat berita belum tersedia." }));
    return wrap;
  }
  const position = Math.max(-1, Math.min(1, score / t.threshold));
  const marker = el("span", { class: `gauge-marker ${position > 0 ? "BUY" : position < 0 ? "SELL" : ""}` });
  marker.style.left = `${50 + 50 * position}%`; // CSSOM: the CSP forbids inline styles
  wrap.append(
    el("div", { class: "gauge-labels" }, el("span", { class: "SELL", text: "◀ SELL" }), el("span", { class: "muted", text: "netral" }), el("span", { class: "BUY", text: "BUY ▶" })),
    el("div", { class: "gauge" }, marker),
    el("p", { class: "muted", text: `Hitungan alat berita sekarang ${signed(score)} · sinyal BUY bila ≥ +${num(t.threshold)}, SELL bila ≤ −${num(t.threshold)} · diperbarui ${headerClock.format(new Date())} WIB` }));
  return wrap;
}

// The chart trading uses now: chosen automatically (signals/timeframe.py), or the active
// signal's own chart (picked by the AI committee when it reviewed the signal).
const TF_SOURCE = { auto: "⚙️ Timeframe otomatis", signal: "📊 Sinyal aktif di chart", opus: "🤖 Sinyal aktif di chart" };
function timeframeLine(tf) {
  if (!tf) return null;
  const by = tf.source === "opus" ? " · dipilih komite AI" : "";
  return el("p", { class: `trading-line ${tf.source === "auto" ? "" : "on"}` },
    el("b", { text: `${TF_SOURCE[tf.source] || "Timeframe"}: ${tf.tf}` }), `${by}${tf.reason ? ` · ${tf.reason}` : ""}`);
}

// Why that timeframe: the per-timeframe table the choice was made from.
function timeframeTable(tf) {
  if (!tf || !tf.options || !tf.options.length) return null;
  const table = el("table", { class: "plan" });
  table.append(el("tr", {}, ...["Timeframe", "Bisa dipakai", "Akurasi sesi ini", "Spread/SL"].map((h) => el("th", { text: h }))));
  for (const o of tf.options) {
    const a = o.accuracy;
    const accuracy = a && a.calls ? `${pct(a.rate)} dari ${a.calls} (95%: ${pct(a.ci95[0])}–${pct(a.ci95[1])})` : "belum ada data";
    table.append(el("tr", { class: o.tf === tf.tf ? "chosen" : "" },
      el("td", { text: o.tf === tf.tf ? `${o.tf} ✔` : o.tf }),
      el("td", { text: o.eligible ? "ya" : o.note }),
      el("td", { text: accuracy }),
      el("td", { text: o.cost_ratio !== null && o.cost_ratio !== undefined ? pct(o.cost_ratio) : "–" })));
  }
  return el("div", { class: "table-wrap" }, table);
}

function renderBoard() {
  const t = lastTiming;
  const box = clear($("#timing"));
  const n = t.now;
  const line = timeframeLine(t.timeframe);
  if (line) box.append(line);
  const boxes = el("div", { class: "big-boxes" });
  let direction = null;
  if (n.mode === "signal") {
    direction = n.action;
    box.append(el("p", { class: `board-head ${n.action}`, text: `${n.action === "BUY" ? "🟢" : "🔴"} ${n.action} SEKARANG` }));
    boxes.append(
      bigBox("Masuk di", `${num(n.entry_low)} – ${num(n.entry_high)}`, n.entry_type === "market" ? "harga pasar, selama di zona ini" : `pasang ${n.action} ${n.entry_type === "limit" ? "Limit" : "Stop"}`, n.action),
      bigBox("Stop loss", num(n.stop_loss), "cut loss di sini", "SELL-soft"),
      bigBox("Target", `${num(n.tp1)} → ${num(n.tp2)}`, "TP1 ambil 50%, sisanya TP2", "BUY-soft"),
      bigBox("Sisa waktu", liveCountdown(n.until_utc, "", "kedaluwarsa"), `sampai ${n.until_wib} WIB`));
  } else {
    const next = t.windows[0];
    box.append(el("p", { class: "board-head WAIT", text: "⏳ TUNGGU · belum waktunya klik" }));
    if (next) {
      direction = next.action;
      boxes.append(bigBox("Prediksi berikutnya", `${next.action} jam ${next.label_wib.split("–")[0]} WIB`,
        el("small", {}, liveCountdown(next.from_utc, "dalam", "sedang berlangsung"), ` · confidence ${next.confidence ?? "–"}`), next.action));
    } else {
      boxes.append(bigBox("Prediksi berikutnya", "belum ada", "Outlook 24 jam belum condong BUY/SELL"));
    }
    if (t.blackout) {
      const b = t.blackout;
      boxes.append(bigBox("Jangan trading", `${b.from_wib} – ${b.to_wib} WIB`,
        el("small", {}, `rilis ${b.title} · `, b.active ? liveCountdown(b.to_utc, "selesai dalam", "sudah boleh") : liveCountdown(b.from_utc, "dalam", "sedang berlangsung")), "WARN"));
    }
    if (t.triggers && (t.triggers.buy_above || t.triggers.sell_below)) {
      const parts = [];
      if (t.triggers.buy_above) parts.push(`BUY > ${num(t.triggers.buy_above.price)}`);
      if (t.triggers.sell_below) parts.push(`SELL < ${num(t.triggers.sell_below.price)}`);
      boxes.append(bigBox("Pantau harga", parts.join(" · "), `sekarang ${num(t.triggers.mid)}`));
    }
  }
  box.append(boxes);
  box.append(scoreGauge(t));
  if (lastSummary && lastSummary.overall && lastSummary.overall.of) {
    const o = lastSummary.overall;
    const agree = direction && o.verdict === direction;
    const against = direction && o.verdict !== "NETRAL" && o.verdict !== direction;
    box.append(el("p", { class: `agree ${agree ? "BUY" : against ? "WARN" : ""}`,
      text: `Indikator: ${o.buy} dari ${o.of} timeframe BUY, ${o.sell} SELL${direction ? (agree ? ` · searah dengan ${direction} ✅` : against ? ` · berlawanan dengan ${direction} ⚠️ hati-hati` : " · netral") : ""}` }));
  }
  tickLiveCountdowns();
}

function renderTimingDetails(t) {
  const box = clear($("#timing-details"));
  const n = t.now;
  if (n.mode === "wait" && n.reasons.length) box.append(el("p", { class: "muted", text: `Kenapa menunggu: ${n.reasons.join("; ")}` }));
  const list = el("ul", { class: "timing-list" });
  for (const w of t.windows) {
    list.append(el("li", { class: w.action },
      el("b", { text: `${w.action} · ${w.label_wib} WIB` }), " · ",
      liveCountdown(w.from_utc, "dalam", "sedang berlangsung"),
      ` · confidence ${w.confidence ?? "–"}${w.conditional ? " (bersyarat)" : ""}`,
      el("div", { class: "meta", text: w.scenario })));
  }
  if (!t.windows.length) list.append(el("li", { class: "muted", text: "Belum ada jam yang condong BUY atau SELL di Outlook 24 jam ke depan." }));
  box.append(el("h2", { text: "Semua jadwal peluang" }), list);
  const table = timeframeTable(t.timeframe);
  if (table) box.append(el("h2", { text: "Kenapa timeframe ini" }), table);
  if (t.triggers) {
    const parts = [];
    if (t.triggers.buy_above) parts.push(`BUY bila tembus ke atas ${num(t.triggers.buy_above.price)} (${t.triggers.buy_above.name})`);
    if (t.triggers.sell_below) parts.push(`SELL bila tembus ke bawah ${num(t.triggers.sell_below.price)} (${t.triggers.sell_below.name})`);
    if (parts.length) box.append(el("p", {}, el("b", { text: "Konfirmasi harga: " }), parts.join("; ")));
  }
  box.append(el("p", { class: "muted", text: "Klik BUY/SELL di broker hanya saat papan berubah menjadi BUY/SELL SEKARANG (juga dikirim ke Telegram). Jadwal adalah prediksi kapan peluang paling mungkin muncul, bukan perintah." }));
  tickLiveCountdowns();
}

// ------------------------------------------------------------------ Claude credit
const CREDIT_STATUS = { aman: "", menipis: "warn", habis: "SELL" };

function renderCredit(c) {
  const badge = $("#credit");
  const b = c.balance;
  if (!b) {
    badge.textContent = c.has_key ? "Claude: isi saldo" : "Claude: tanpa API key";
    badge.className = "credit";
  } else {
    badge.textContent = `Claude $${num(b.remaining_usd)}${b.days_left !== null ? ` · ±${Math.floor(b.days_left)} hari` : ""}`;
    badge.className = `credit ${CREDIT_STATUS[b.status]}`;
  }
  const info = $("#credit-info");
  if (!info) return;
  clear(info);
  const rows = [
    ["Sisa saldo (perkiraan)", b ? `$${num(b.remaining_usd)}` : "–", b ? `dari $${num(b.balance_usd)} yang diisi ${b.as_of_wib} WIB, terpakai $${num(b.spent_since_usd, 4)}` : "belum diisi"],
    ["Pemakaian hari ini", `$${num(c.spent_today_usd, 4)}`, `batas keras $${num(c.daily_budget_usd)}/hari; berita biasa berhenti di $${num(c.routine_budget_usd)}, sisanya untuk keputusan sinyal dan berita besar`],
    ["Rata-rata per hari", b && b.per_day_usd !== null ? `$${num(b.per_day_usd, 4)}` : "–", "dari pemakaian 7 hari terakhir"],
    ["Perkiraan habis", b && b.empty_at_wib ? `${b.empty_at_wib} WIB` : "–", b && b.days_left !== null ? `±${num(b.days_left, 1)} hari lagi${b.status !== "aman" ? " · segera top-up" : ""}` : ""],
  ];
  const table = el("table", { class: "plan" });
  for (const [label, value, note] of rows) table.append(planRow(label, value, note));
  info.append(el("div", { class: "table-wrap" }, table));
  if (b && b.status !== "aman") info.append(el("p", { class: "agree WARN", text: b.status === "habis" ? "Saldo habis: analisis memakai aturan saja sampai di-top-up." : "Saldo menipis: top-up di console.anthropic.com → Billing." }));
}

async function refreshCredit() {
  try {
    renderCredit(await api("/api/credit"));
  } catch (error) {
    console.error(error);
  }
}

async function saveCredit(event) {
  event.preventDefault();
  const status = $("#credit-status");
  const value = Number($("#credit-input").value);
  const response = await fetch("/api/credit", {
    method: "POST", credentials: "same-origin",
    headers: { "content-type": "application/json" }, body: JSON.stringify({ balance_usd: value }),
  });
  if (!response.ok) {
    status.textContent = "Gagal menyimpan (isi angka saldo dalam USD).";
    return;
  }
  renderCredit(await response.json());
  status.textContent = "Tersimpan.";
}

const headerClock = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
});
function tickClock() {
  const now = new Date();
  const parts = Object.fromEntries(wibFormat.formatToParts(now).map((p) => [p.type, p.value]));
  $("#clock").textContent = `${parts.weekday} ${parts.day} ${parts.month} · ${headerClock.format(now)} WIB`;
}

// ------------------------------------------------------------------ timeframe ring + next-candle forecast
// MT5-style ring: picking a timeframe redraws the Live chart on it and asks the agent what it
// expects for the NEXT candle of that timeframe (M1 = the next minute, H4 = the next 4 hours...).
function savedTf() {
  try {
    const tf = localStorage.getItem("gna.tf");
    return RING_TFS.includes(tf) ? tf : DEFAULT_TF;
  } catch {
    return DEFAULT_TF;
  }
}

function buildRing() {
  const ring = $("#tf-ring");
  const first = -125;
  const stepDeg = 250 / (RING_TFS.length - 1);
  RING_TFS.forEach((tf, i) => {
    const angle = ((first + i * stepDeg) * Math.PI) / 180;
    const button = el("button", { type: "button", class: "tf-item", "data-tf": tf, text: tf });
    // CSSOM, not a style attribute: the CSP forbids inline styles.
    button.style.left = `${50 + 63 * Math.sin(angle)}%`;
    button.style.top = `${50 - 63 * Math.cos(angle)}%`;
    button.addEventListener("click", () => chooseTf(tf));
    ring.append(button);
  });
  $("#tf-button").addEventListener("click", () => { ring.hidden = !ring.hidden; });
  $("#tf-close").addEventListener("click", () => { ring.hidden = true; });
}

// The Live chart follows the timeframe the agent trades on now. A timeframe picked on the ring
// holds until the agent switches to another one.
let lastAutoTf = null;
function preferredTf(auto) {
  if (!auto) return savedTf();
  try {
    const pick = JSON.parse(localStorage.getItem("gna.tf.pick") || "null");
    if (pick && pick.auto === auto && RING_TFS.includes(pick.tf)) return pick.tf;
  } catch { /* unreadable: follow the agent */ }
  return auto;
}

function followTimeframe(auto) {
  lastAutoTf = auto;
  const state = charts.live;
  const tf = preferredTf(auto);
  if (state && currentPage === "live" && tf !== state.tf) showTf(tf);
}

async function chooseTf(tf) {
  try {
    localStorage.setItem("gna.tf", tf);
    localStorage.setItem("gna.tf.pick", JSON.stringify({ tf, auto: lastAutoTf }));
  } catch { /* private window: just not remembered */ }
  await showTf(tf);
}

async function showTf(tf) {
  $("#tf-ring").hidden = true;
  const state = charts.live;
  if (!state) return;
  state.tf = tf;
  markTf(tf);
  await loadChart(state);
  drawSignal(state, lastState ? lastState.active : null);
  await loadIndicators(state);
  state.chart.timeScale().fitContent();
  await refreshForecast();
}

function markTf(tf) {
  $("#tf-button").textContent = tf;
  for (const item of document.querySelectorAll(".tf-item")) item.classList.toggle("active", item.dataset.tf === tf);
}

const FORECAST_CLASS = { NAIK: "BUY", TURUN: "SELL" };
const FORECAST_ICON = { NAIK: "▲", TURUN: "▼", NETRAL: "◆", "TUNGGU DATA": "⏸", "PASAR TUTUP": "■" };

async function refreshForecast() {
  const state = charts.live;
  if (!state) return;
  const f = await api(`/api/forecast?tf=${state.tf}`);
  const box = clear($("#tf-forecast"));
  const cls = FORECAST_CLASS[f.direction] || "WAIT";
  box.append(el("p", { class: `forecast-head ${cls}` },
    `${f.tf} · ${f.horizon} ke depan: ${FORECAST_ICON[f.direction] || ""} ${f.direction}`,
    f.strength !== undefined ? el("span", { class: "muted", text: ` · kekuatan ${f.strength}/100` }) : null));
  if (f.expected) {
    box.append(el("p", {}, `Perkiraan candle berikutnya ${num(f.expected.low)} – ${num(f.expected.high)} (target ${num(f.expected.target)}) · sampai ${f.until_wib} WIB `,
      liveCountdown(f.until_utc, "·", "candle baru")));
  }
  box.append(el("p", { class: "muted", text: f.reasons.join(" · ") }));
  const accuracy = accuracyLines(f.accuracy, f.session, f.session_label);
  if (accuracy) box.append(...accuracy);
  drawForecast(state, f);
  tickLiveCountdowns();
}

// Walk-forward hit rate of this forecast (server: forecast_score.py). 50% is a coin flip, so the
// verdict only claims an edge when the whole 95% interval is above it.
function accuracyLines(a, session, sessionLabel) {
  if (!a) return null;
  const all = a.forecast;
  const here = (a.by_session || {})[session];
  const f = here && here.calls ? here : all;
  const scope = here && here.calls ? `sesi ${sessionLabel}` : "semua sesi";
  if (!f.calls) {
    return [el("p", { class: "muted", text: `Akurasi prediksi ${a.tf}: belum ada data. Dihitung otomatis dari candle yang sudah lewat setelah agent berjalan.` })];
  }
  const [lo, hi] = f.ci95;
  const verdict = lo > 0.5 ? "lebih baik dari tebakan acak ✅" : hi < 0.5 ? "lebih buruk dari acak ⚠️ jangan diandalkan" : "belum terbukti lebih baik dari acak (50%)";
  const part = (label, x) => `${label} ${x.calls ? `${pct(x.rate)} dari ${x.calls}` : "–"}`;
  return [
    el("p", { class: `accuracy ${lo > 0.5 ? "BUY" : hi < 0.5 ? "SELL" : ""}`,
      text: `Akurasi prediksi ${a.tf} (${scope}, ${a.days} hari): ${pct(f.rate)} benar dari ${f.calls} candle (95%: ${pct(lo)}–${pct(hi)}) · ${verdict}` }),
    el("p", { class: "muted", text: `${part("semua sesi", all)} · ${part("berita saja", a.news_only)} · ${part("indikator saja", a.chart_only)} · ${a.abstained} candle netral/tunggu data tidak dihitung` }),
  ];
}

function drawForecast(state, f) {
  const lines = state.forecast;
  if (!lines) return;
  const clearAll = () => { for (const line of Object.values(lines)) line.setData([]); };
  if (!f.expected || !state.last) {
    clearAll();
    return;
  }
  const from = state.last.time;
  const to = from + TF_SECONDS[state.tf];
  const colour = f.direction === "NAIK" ? "#26a69a" : f.direction === "TURUN" ? "#ef5350" : "#8a93a6";
  const pair = (value) => [{ time: from, value: state.last.close }, { time: to, value }];
  lines.target.applyOptions({ color: colour });
  lines.high.applyOptions({ color: "rgba(138, 147, 166, 0.6)" });
  lines.low.applyOptions({ color: "rgba(138, 147, 166, 0.6)" });
  lines.target.setData(pair(f.expected.target));
  lines.high.setData(pair(f.expected.high));
  lines.low.setData(pair(f.expected.low));
}

// ------------------------------------------------------------------ forecast (Proyeksi 24 jam)
// Drawn AHEAD of the last candle from what the agent already computed: each outlook hour's bias
// (BUY/SELL/condong naik/turun), its confidence and the expected range for that hour; while a
// signal is active the centre heads for its TP1 by the signal's expiry. Nothing here is a
// candle: it is the agent's forecast, redrawn from the latest close on every new bar.
function biasDirection(bias) {
  if (/BUY|naik/i.test(bias)) return 1;
  if (/SELL|turun/i.test(bias)) return -1;
  return 0;
}

const PROJECTION_COLORS = { 1: "38, 166, 154", "-1": "239, 83, 80", 0: "138, 147, 166" };

function projectionPath(rows, start, close, active, step) {
  const hours = rows.map((row) => ({ ...row, from: Date.parse(row.hour_start_utc) / 1000 + WIB_OFFSET }));
  const expiry = active ? Date.parse(active.valid_until_utc) / 1000 + WIB_OFFSET : null;
  const points = [];
  let centre = close;
  let lastRow = null;
  for (let t = start + step; t <= start + 24 * 3600; t += step) {
    const row = hours.find((h) => t >= h.from && t < h.from + 3600);
    if (!row || row.bias === "PASAR TUTUP") {
      points.push({ time: t, row: null });
      continue;
    }
    const direction = active && t <= expiry ? (active.action === "BUY" ? 1 : -1) : biasDirection(row.bias);
    const range = row.expected_range_usd;
    if (active && t <= expiry) {
      centre = close + (active.tp1 - close) * Math.min(1, (t - start) / (expiry - start));
    } else if (range !== null) {
      centre += direction * ((row.confidence ?? 0) / 100) * range * 0.5 * (step / 3600);
    }
    points.push({ time: t, row, centre, half: range !== null ? range / 2 : null, direction, first: row !== lastRow });
    lastRow = row;
  }
  return points;
}

function drawProjection(state) {
  const proj = state && state.projection;
  if (!proj || !state.outlook || !state.last) return;
  const points = projectionPath(state.outlook.rows, state.last.time, state.last.close, state.active, TF_SECONDS[state.tf]);
  const colour = (p, alpha) => `rgba(${PROJECTION_COLORS[p.direction]}, ${p.row.conditional ? alpha / 2 : alpha})`;
  const empty = (p) => ({ time: p.time });
  proj.centre.setData(points.map((p) => (p.row ? { time: p.time, value: p.centre, color: colour(p, 0.95) } : empty(p))));
  proj.upper.setData(points.map((p) => (p.row && p.half !== null ? { time: p.time, value: p.centre + p.half, color: colour(p, 0.5) } : empty(p))));
  proj.lower.setData(points.map((p) => (p.row && p.half !== null ? { time: p.time, value: p.centre - p.half, color: colour(p, 0.5) } : empty(p))));
  proj.marks.setMarkers(points.filter((p) => p.row && p.first && p.row.events.length).map((p) => ({
    time: p.time, position: "aboveBar", shape: "circle", color: "#f5b041", text: p.row.events[0].slice(0, 24),
  })));
  state.chart.timeScale().setVisibleRange({ from: state.last.time - 6 * 3600, to: state.last.time + 24 * 3600 });
}

// The active signal as price lines: entry zone, stop loss, TP1-TP3. Levels come from the same
// price feed as these candles, so they need no shifting (unlike the Pine indicator).
function drawSignal(state, active) {
  if (!state) return;
  for (const line of state.lines) state.series.removePriceLine(line);
  state.lines = [];
  if (!active) return;
  const side = active.action === "BUY" ? "#26a69a" : "#ef5350";
  const levels = [
    [active.entry_high, `${active.action} ${num(active.entry_low)}–${num(active.entry_high)}`, side, 2],
    [active.entry_low, `${active.action} zona bawah`, side, 2],
    [active.stop_loss, `SL ${num(active.stop_loss)}`, "#ef5350", 0],
    [active.tp1, `TP1 ${num(active.tp1)}`, "#2ecc71", 1],
    [active.tp2, `TP2 ${num(active.tp2)}`, "#2ecc71", 1],
    [active.tp3, `TP3 ${num(active.tp3)}`, "#2ecc71", 3],
  ];
  for (const [price, title, color, lineStyle] of levels) {
    state.lines.push(state.series.createPriceLine({ price, title, color, lineStyle, lineWidth: 2, axisLabelVisible: true }));
  }
}

// "Rencana trading": the active signal spelled out as the exact numbers to act on, or, while the
// engine waits, why there is no number yet and what the next hours' scenarios say.
const ENTRY_HOW = {
  market: (a) => `${a} sekarang di harga pasar, selama harga masih di dalam zona`,
  limit: (a) => `pasang ${a} Limit di dalam zona (tunggu harga kembali ke zona)`,
  stop: (a) => `pasang ${a} Stop di dalam zona (masuk saat harga menembus zona)`,
};

function planRow(label, value, note) {
  return el("tr", {}, el("th", { text: label }), el("td", { class: "num", text: value }),
    el("td", { class: "muted", text: note || "" }));
}

function renderPlan(state, outlook) {
  const box = clear($("#gna-plan"));
  const active = state.active;
  if (active) {
    const risk = Math.abs(active.entry_high - active.stop_loss);
    box.append(el("p", { class: `plan-head ${active.action}`,
      text: `${active.action} XAUUSD · confidence ${active.confidence}/100 · berlaku s/d ${active.valid_until_wib}` }));
    const table = el("table", { class: "plan" },
      planRow("Masuk (entry)", `${num(active.entry_low)} – ${num(active.entry_high)}`, ENTRY_HOW[active.entry_type](active.action)),
      planRow("Stop loss", num(active.stop_loss), `cut loss di sini (±${num(risk)} $ dari entry = 1R)`),
      planRow("TP1", num(active.tp1), "ambil 50%, lalu geser SL ke harga entry"),
      planRow("TP2", num(active.tp2), "tutup sisa posisi"),
      planRow("TP3", num(active.tp3), active.tp3_source ? `target lanjutan (${active.tp3_source})` : "target lanjutan"),
      planRow("Lot", active.lot_size !== null ? String(active.lot_size) : "–", active.lot_note || `risiko ${active.risk_pct}% per posisi`));
    box.append(el("div", { class: "table-wrap" }, table));
    if (active.trigger_summary) {
      box.append(el("p", {}, el("b", { text: "Dasar berita: " }), `${active.trigger_summary} (${active.trigger_source || "-"})`));
    }
    const reasons = el("ul", { class: "notes" });
    for (const reason of active.top_reasons) reasons.append(el("li", { text: reason }));
    const cancel = el("ul", { class: "notes warn" });
    for (const item of active.invalidation) cancel.append(el("li", { text: item }));
    box.append(el("h2", { text: "Alasan" }), reasons, el("h2", { text: "Batalkan rencana jika" }), cancel);
    if (active.proxy_price) {
      box.append(el("p", { class: "muted",
        text: `Angka dari harga ${active.price_source} (proxy). Di broker, tambahkan selisih harga broker − ${num((active.bid + active.ask) / 2)} saat sinyal dibuat ke semua angka, atau pakai indikator TradingView yang menggeser otomatis.` }));
    }
  } else {
    const current = state.state;
    box.append(el("p", { class: "plan-head WAIT", text: "TUNGGU · belum ada angka BUY/SELL yang layak" }));
    if (current) {
      const c = current.components;
      box.append(el("p", {}, el("b", { text: "Hitungan alat berita saat ini: " }),
        `skor ${signed(current.composite_score)} (Berita ${signed(c.N)} · Makro ${signed(c.M)} · Teknikal ${signed(c.T)} · Harga ${signed(c.P)}) · confidence ${current.confidence}`));
    }
    const waits = el("ul", { class: "notes warn" });
    for (const reason of current ? current.wait_reasons : []) waits.append(el("li", { text: reason }));
    if (waits.childElementCount) box.append(el("h2", { text: "Kenapa menunggu" }), waits);
    const rows = outlook ? outlook.rows.slice(0, 3) : [];
    if (rows.length) {
      const table = el("table", { class: "plan" });
      for (const row of rows) table.append(planRow(row.label_wib, row.bias, row.scenario));
      box.append(el("h2", { text: "Skenario 3 jam ke depan (Outlook)" }), el("div", { class: "table-wrap" }, table));
    }
    box.append(el("p", { class: "muted", text: "Angka masuk, SL dan TP muncul di sini dan di grafik begitu alat berita mengeluarkan sinyal BUY/SELL." }));
  }
  box.append(el("p", { class: "muted", text: "Bukan saran keuangan. Keputusan & risiko di tanganmu." }));
}

function onTick(tick) {
  const display = tick.time + WIB_OFFSET;
  for (const state of Object.values(charts)) {
    const step = TF_SECONDS[state.tf];
    const last = state.last;
    let time;
    if (!last) time = Math.floor(display / step) * step;
    else if (display < last.time) continue; // late tick
    else if (state.tf === "MN" || display < last.time + step) time = last.time; // same bar
    else time = last.time + Math.floor((display - last.time) / step) * step; // keep the bar anchor
    const bar = last && last.time === time
      ? { ...last, high: Math.max(last.high, tick.mid), low: Math.min(last.low, tick.mid), close: tick.mid }
      : { time, open: tick.mid, high: tick.mid, low: tick.mid, close: tick.mid };
    state.series.update(bar);
    const newBar = !last || last.time !== time;
    if (state.sessions && newBar) {
      state.sessions.update({ time, value: 1, color: sessionColor(time - WIB_OFFSET) });
    }
    state.last = bar;
    if (newBar) drawProjection(state);
    if (newBar && state.ind && currentPage === "live") refreshLiveExtras();
  }
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
  const desk = clear($("#perf-desk"));
  const deskTile = (label, d) => tile(label, !d || !d.n ? "belum ada" :
    `${d.tp1_rate === null ? "–" : pct(d.tp1_rate)} TP1 dulu${d.ci95 ? ` [${pct(d.ci95[0])}–${pct(d.ci95[1])}]` : ""} · ${d.tp1}/${d.tp1 + d.sl} (${d.expired} kedaluwarsa, ${d.open} berjalan)`);
  if (perf.desk) desk.append(deskTile("Disetujui AI", perf.desk.approved), deskTile("Ditahan AI (bayangan)", perf.desk.held));
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
    lastState = state;
    renderSignalCard(state);
    renderPrice(state.context);
    renderNews(news);
    const chart = makeChart("live", $("#chart"), false);
    const auto = (state.active && state.active.timeframe) || (state.timeframe && state.timeframe.tf) || (state.state && state.state.timeframe) || null;
    lastAutoTf = auto;
    chart.tf = preferredTf(auto);
    markTf(chart.tf);
    await loadChart(chart);
    drawSignal(chart, state.active);
    await loadIndicators(chart);
    renderTiming(await api("/api/timing"));
    await refreshForecast();
  },
  outlook: async () => {
    const [outlook, state] = await Promise.all([api("/api/outlook"), api("/api/state")]);
    renderOutlook(outlook, state);
  },
  kalender: async () => renderCalendar(await api("/api/calendar")),
  performa: async () => renderPerformance(await api("/api/performance")),
  kesehatan: async () => {
    renderHealth(await api("/api/health"));
    await refreshCredit();
  },
  tradingview: async () => {
    const [state, outlook] = await Promise.all([api("/api/state"), api("/api/outlook")]);
    renderTradingView(state);
    renderPlan(state, outlook);
    const chart = makeChart("gna", $("#gna-chart"), true);
    await loadChart(chart);
    drawSignal(chart, state.active);
    chart.outlook = outlook;
    chart.active = state.active;
    drawProjection(chart);
  },
};

async function copyText(text, status) {
  try {
    await navigator.clipboard.writeText(text);
    status.textContent = "Tersalin.";
  } catch {
    status.textContent = "Gagal menyalin otomatis: blok teksnya lalu tekan Ctrl+C.";
  }
}

async function copyIndicatorScript() {
  const status = $("#tv-copy-status");
  const response = await fetch("/static/gold_news_agent.pine", { credentials: "same-origin" });
  if (!response.ok) {
    status.textContent = "Script tidak bisa diambil.";
    return;
  }
  await copyText(await response.text(), status);
}

// Official TradingView embed widget, loaded only when this page is first opened so the
// third-party script never runs for anyone who does not use it.
let tradingViewLoaded = false;
function renderTradingView(state) {
  const symbol = state.tradingview_symbol;
  if (!symbol) return;
  $("#tv-symbol").textContent = symbol;
  $("#tv-open").href = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symbol)}`;
  if (tradingViewLoaded) return;
  tradingViewLoaded = true;
  const script = document.createElement("script");
  script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
  script.async = true;
  script.textContent = JSON.stringify({
    autosize: true,
    symbol,
    interval: "15",
    timezone: "Asia/Jakarta",
    theme: "dark",
    style: "1",
    locale: "id",
    allow_symbol_change: true,
    calendar: false,
    support_host: "https://www.tradingview.com",
  });
  $("#tv-widget").appendChild(script);
}

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

let extrasTimer = null;
function refreshLiveExtras() { // new bar: indicators move and the timing windows roll forward
  if (extrasTimer) return;
  extrasTimer = setTimeout(async () => {
    extrasTimer = null;
    await loadIndicators(charts.live);
    renderTiming(await api("/api/timing"));
    await refreshForecast();
  }, 1500);
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
      drawSignal(charts.live, state.active);
      renderTiming(await api("/api/timing"));
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
    else if (message.type === "outlook" && (currentPage === "outlook" || currentPage === "tradingview")) show(currentPage);
    else if (message.type === "signal" && currentPage === "tradingview") show("tradingview");
  });
}

window.addEventListener("hashchange", () => show(location.hash.slice(1)));
window.addEventListener("DOMContentLoaded", () => {
  show(location.hash.slice(1) || "live");
  $("#tv-copy-script").addEventListener("click", copyIndicatorScript);
  restoreToggles();
  $("#ind-toggles").addEventListener("change", () => { saveToggles(); applyIndicatorToggles(charts.live); });
  $("#credit-form").addEventListener("submit", saveCredit);
  buildRing();
  // The board, indicator read and forecast follow the agent: refresh every 30 s even when no bar,
  // signal or outlook event arrives.
  setInterval(() => { if (currentPage === "live") refreshLiveExtras(); }, 30000);
  refreshCredit();
  setInterval(refreshCredit, 60000);
  tickClock();
  api("/api/state").then((state) => {
    $("nav a[data-page=tradingview]").hidden = !state.tradingview_symbol;
  }).catch(() => {});
  connect();
  setInterval(() => { tickCountdown(); tickClock(); tickLiveCountdowns(); }, 1000);
});
