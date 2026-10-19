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
  charts[name] = { chart, series, sessions, markers: lib.createSeriesMarkers(series, []), last: null, lines: [] };
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
    api(`/api/candles?tf=${CHART_TF}`), api("/api/markers?hours=24")]);
  const shifted = bars.map((b) => ({ ...b, time: b.time + WIB_OFFSET }));
  state.series.setData(shifted);
  state.last = shifted.length ? { ...shifted[shifted.length - 1] } : null;
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
  const points = projectionPath(state.outlook.rows, state.last.time, state.last.close, state.active, TF_SECONDS[CHART_TF]);
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
  const step = TF_SECONDS[CHART_TF];
  const time = Math.floor((tick.time + WIB_OFFSET) / step) * step;
  for (const state of Object.values(charts)) {
    const last = state.last;
    if (last && time < last.time) continue; // late tick
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
    const chart = makeChart("live", $("#chart"), false);
    await loadChart(chart);
    drawSignal(chart, state.active);
  },
  outlook: async () => {
    const [outlook, state] = await Promise.all([api("/api/outlook"), api("/api/state")]);
    renderOutlook(outlook, state);
  },
  kalender: async () => renderCalendar(await api("/api/calendar")),
  performa: async () => renderPerformance(await api("/api/performance")),
  kesehatan: async () => renderHealth(await api("/api/health")),
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
  api("/api/state").then((state) => {
    $("nav a[data-page=tradingview]").hidden = !state.tradingview_symbol;
  }).catch(() => {});
  connect();
  setInterval(tickCountdown, 1000);
});
