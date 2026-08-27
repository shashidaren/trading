/* Gold Trading Dashboard — frontend logic */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const REFRESH_MS = 30000;

  // ---- formatting helpers ----
  const fmtPrice = (v, d = 2) =>
    v === null || v === undefined ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  const fmtR = (v) =>
    v === null || v === undefined ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(2) + "R";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---- chart state ----
  let priceChart = null, candleSeries = null, emaFastSeries = null,
      emaSlowSeries = null, vwapSeries = null, volumeSeries = null;
  let rsiChart = null, rsiSeries = null;
  let showEma = true;
  let fallbackCanvas = null; // used if Lightweight Charts CDN unavailable

  // =====================================================================
  // Fetching
  // =====================================================================
  async function getJSON(url) {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  async function loadAll() {
    try {
      const [ov, candles, trades, news, cal] = await Promise.all([
        getJSON("/api/overview"),
        getJSON("/api/candles?bars=500"),
        getJSON("/api/trades"),
        getJSON("/api/news"),
        getJSON("/api/calendar"),
      ]);
      renderOverview(ov);
      renderCandles(candles);
      renderTrades(trades);
      renderNews(news);
      renderCalendar(cal);
      $("footStatus").textContent = "Updated " + new Date().toLocaleTimeString();
    } catch (e) {
      $("footStatus").textContent = "Refresh error: " + e.message;
    }
  }

  // =====================================================================
  // Overview / signal
  // =====================================================================
  function renderOverview(ov) {
    // source badge
    const badge = $("sourceBadge");
    badge.textContent = ov.source === "demo" ? "DEMO" : "LIVE";
    badge.className = "status-chip " + (ov.source === "demo" ? "demo" : "live");

    // price
    $("price").textContent = fmtPrice(ov.price);
    const ch = $("priceChange");
    if (ov.change_5m === null) {
      ch.textContent = "—";
      ch.className = "price-change";
    } else {
      const s = ov.change_5m > 0 ? "+" : "";
      ch.textContent = s + fmtPrice(ov.change_5m) + " (5m)";
      ch.className = "price-change " + (ov.change_5m > 0 ? "up" : ov.change_5m < 0 ? "down" : "flat");
    }

    // session chip
    $("sessionChip").textContent = ov.session_ok === null ? "SESSION —" : ov.session_ok ? "SESSION IN" : "SESSION OUT";
    $("sessionChip").className = "status-chip dim " + (ov.session_ok ? "live" : "");

    // KPIs
    const bias = ov.bias || "NONE";
    const biasEl = $("kpiBias");
    biasEl.textContent = bias;
    biasEl.className = "kpi-value " + (bias === "BUY" ? "up" : bias === "SELL" ? "down" : "");
    $("kpiRsi").textContent = ov.rsi === null ? "—" : ov.rsi.toFixed(1);
    $("kpiAtr").textContent = ov.atr === null ? "—" : ov.atr.toFixed(2);
    $("kpiWinrate").textContent = "—"; // set by trades
    $("kpiExpect").textContent = "—";
    $("kpiResolved").textContent = "—";

    // signal banner
    const big = $("biasBig");
    big.textContent = bias;
    big.className = "bias-big " + bias;
    const subs = [];
    if (ov.filter_reason) subs.push("filtered: " + ov.filter_reason);
    if (ov.bias_relaxed && ov.bias_relaxed !== ov.bias) subs.push("relaxed mode: " + ov.bias_relaxed);
    $("biasSub").textContent = subs.length ? subs.join(" · ") : (bias === "NONE" ? "no fresh cross — watching" : "based on latest closed candle");

    // levels
    $("lvEntry").textContent = fmtPrice(ov.entry);
    $("lvSL").textContent = fmtPrice(ov.sl);
    $("lvTP").textContent = fmtPrice(ov.tp);

    // gauges
    const rsi = ov.rsi;
    if (rsi !== null) {
      $("rsiFill").style.width = Math.max(0, Math.min(100, rsi)) + "%";
      $("rsiVal").textContent = rsi.toFixed(1);
    }
    const pctl = ov.atr_pctl;
    if (pctl !== null) {
      $("atrFill").style.width = Math.max(0, Math.min(100, pctl)) + "%";
      $("atrVal").textContent = Math.round(pctl) + "th";
    }

    // meta
    $("metaRisk").textContent = ov.risk_pct === null ? "—" : "$" + fmtPrice(ov.risk_usd) + " (" + ov.risk_pct.toFixed(1) + "%)";
    setOk("metaSession", ov.session_ok);
    setOk("metaVol", ov.vol_ok);
    $("metaLast").textContent = ov.state.last_bias ? (ov.state.last_bias + (ov.state.mins_since_alert !== null ? " · " + ov.state.mins_since_alert + "m ago" : "")) : "none yet";

    // cooldown left
    if (ov.state.mins_since_alert === null) {
      $("metaCooldown").textContent = "—";
    } else {
      const need = ov.state.cooldown_minutes;
      const left = Math.max(0, need - ov.state.mins_since_alert);
      $("metaCooldown").textContent = left > 0 ? Math.ceil(left) + "m" : "clear";
    }

    // filter note
    const note = $("filterNote");
    if (ov.filter_reason) {
      note.style.display = "block";
      note.textContent = "⚠ " + ov.filter_reason;
    } else {
      note.style.display = "none";
    }
  }

  function setOk(id, val) {
    const el = $(id);
    el.textContent = val === null ? "—" : val ? "ON" : "OFF";
    el.className = val === null ? "" : val ? "ok" : "bad";
  }

  // =====================================================================
  // Candles chart
  // =====================================================================
  function toUnix(iso) {
    return Math.floor(new Date(iso).getTime() / 1000);
  }

  function renderCandles(data) {
    if (!data.candles.length) {
      renderEmptyChart();
      return;
    }
    const candles = data.candles.map((c) => ({
      time: toUnix(c.time), open: c.open, high: c.high, low: c.low, close: c.close,
    }));

    const line = (series, name) => series
      .map((v, i) => (v === null || v === undefined ? null : { time: toUnix(data.candles[i].time), value: v }))
      .filter(Boolean);

    if (typeof LightweightCharts !== "undefined") {
      renderLWChart(candles, line(data.ema_fast), line(data.ema_slow), line(data.vwap), line(data.atr), data, line(data.rsi));
    } else {
      renderFallbackChart(candles, data);
    }
  }

  function initCharts() {
    if (typeof LightweightCharts === "undefined") return;

    if (!priceChart) {
      priceChart = LightweightCharts.createChart($("priceChart"), {
        layout: { background: { color: "transparent" }, textColor: "#8b96ab" },
        grid: { vertLines: { color: "#1b2230" }, horzLines: { color: "#1b2230" } },
        rightPriceScale: { borderColor: "#232b3b" },
        timeScale: { borderColor: "#232b3b", timeVisible: true, secondsVisible: false },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        autoSize: true,
      });
      candleSeries = priceChart.addCandlestickSeries({
        upColor: "#26a46b", downColor: "#e5484d", borderUpColor: "#26a46b",
        borderDownColor: "#e5484d", wickUpColor: "#26a46b", wickDownColor: "#e5484d",
      });
      emaFastSeries = priceChart.addLineSeries({ color: "#e8b34b", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      emaSlowSeries = priceChart.addLineSeries({ color: "#4c8dff", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      vwapSeries = priceChart.addLineSeries({ color: "#c084fc", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
      volumeSeries = priceChart.addHistogramSeries({
        color: "#33405a", priceFormat: { type: "volume" }, priceScaleId: "",
        lastValueVisible: false, priceLineVisible: false,
      });
      priceChart.priceScale("").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

      rsiChart = LightweightCharts.createChart($("rsiChart"), {
        layout: { background: { color: "transparent" }, textColor: "#8b96ab" },
        grid: { vertLines: { color: "#1b2230" }, horzLines: { color: "#1b2230" } },
        rightPriceScale: { borderColor: "#232b3b" },
        timeScale: { borderColor: "#232b3b", visible: false },
        autoSize: true,
      });
      rsiSeries = rsiChart.addLineSeries({ color: "#e8b34b", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      rsiSeries.createPriceLine({ price: 70, color: "#e5484d", lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: "" });
      rsiSeries.createPriceLine({ price: 30, color: "#26a46b", lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: "" });
    }
  }

  function renderLWChart(candles, emaFast, emaSlow, vwap, atr, data, rsiLine) {
    initCharts();
    candleSeries.setData(candles);
    emaFastSeries.setData(emaFast);
    emaSlowSeries.setData(emaSlow);
    vwapSeries.setData(vwap);
    volumeSeries.setData(
      data.candles.map((c) => ({ time: toUnix(c.time), value: c.volume, color: c.close >= c.open ? "rgba(38,164,107,0.4)" : "rgba(229,72,77,0.4)" }))
    );
    rsiSeries.setData(rsiLine);
    priceChart.timeScale().fitContent();
    rsiChart.timeScale().fitContent();
    applyEmaVisibility();
  }

  function applyEmaVisibility() {
    if (!emaFastSeries) return;
    const op = { visible: showEma };
    emaFastSeries.applyOptions(op);
    emaSlowSeries.applyOptions(op);
  }

  function renderEmptyChart() {
    if (typeof LightweightCharts !== "undefined") {
      initCharts();
      candleSeries.setData([]);
      rsiSeries.setData([]);
    }
    const el = $("priceChart");
    el.innerHTML = '<div class="empty" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;">' +
      "No candle data yet. Run <code>collector.py</code> to populate <code>gold_data.db</code>, or start with <code>--demo</code>.</div>";
  }

  // ---- fallback canvas chart (offline CDN) ----
  function renderFallbackChart(candles, data) {
    const wrap = $("priceChart");
    wrap.innerHTML = '<canvas id="fallbackPrice"></canvas>';
    const canvas = $("fallbackPrice");
    const dpr = window.devicePixelRatio || 1;
    const rect = wrap.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = 420 * dpr;
    canvas.style.width = rect.width + "px";
    canvas.style.height = "420px";
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);

    const W = rect.width, H = 420, pad = 8;
    const lows = candles.map((c) => c.low), highs = candles.map((c) => c.high);
    const min = Math.min(...lows), max = Math.max(...highs);
    const range = max - min || 1;
    const x = (i) => pad + (i / (candles.length - 1)) * (W - pad * 2);
    const y = (p) => pad + (1 - (p - min) / range) * (H - pad * 2 - 30);
    const bw = Math.max(1, (W - pad * 2) / candles.length * 0.6);

    ctx.clearRect(0, 0, W, H);
    candles.forEach((c, i) => {
      const up = c.close >= c.open;
      ctx.strokeStyle = up ? "#26a46b" : "#e5484d";
      ctx.fillStyle = up ? "#26a46b" : "#e5484d";
      ctx.beginPath();
      ctx.moveTo(x(i), y(c.high)); ctx.lineTo(x(i), y(c.low)); ctx.stroke();
      const yo = y(c.open), yc = y(c.close);
      ctx.fillRect(x(i) - bw / 2, Math.min(yo, yc), bw, Math.max(1, Math.abs(yc - yo)));
    });
    // EMAs
    [[data.ema_fast, "#e8b34b"], [data.ema_slow, "#4c8dff"]].forEach(([arr, col]) => {
      ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.beginPath();
      let started = false;
      arr.forEach((v, i) => {
        if (v == null) return;
        const px = x(i), py = y(v);
        if (!started) { ctx.moveTo(px, py); started = true; } else ctx.lineTo(px, py);
      });
      ctx.stroke();
    });
  }

  // =====================================================================
  // Trades / performance
  // =====================================================================
  function renderTrades(t) {
    $("tradesTag").textContent = t.has_journal ? t.total + " signals" : "empty";
    $("kpiWinrate").textContent = t.win_rate === null ? "—" : t.win_rate.toFixed(0) + "%";
    $("kpiExpect").textContent = t.expectancy === null && t.avg_r === null ? "—" : fmtR(t.avg_r);
    $("kpiResolved").textContent = t.resolved;

    $("stWins").textContent = t.wins;
    $("stLosses").textContent = t.losses;
    $("stBE").textContent = t.be;
    $("stPF").textContent = t.profit_factor === null ? "—" : t.profit_factor;
    $("stAvgR").textContent = t.avg_r === null ? "—" : fmtR(t.avg_r);
    $("stDD").textContent = t.max_drawdown === null ? "—" : fmtR(t.max_drawdown);

    drawEquity(t.equity_curve);

    // table
    const body = $("tradesBody");
    if (!t.trades.length) {
      body.innerHTML = '<tr><td colspan="8" class="empty">No journal yet — signals will appear here.</td></tr>';
      return;
    }
    body.innerHTML = t.trades.map((r) => {
      const oc = (r.Outcome || "").toUpperCase();
      const rv = r.R_Multiple;
      const rcls = rv == null ? "" : rv > 0 ? "r-pos" : rv < 0 ? "r-neg" : "";
      const ts = (r.Signal_TS || "").replace("T", " ").slice(5, 16);
      return `<tr>
        <td>${esc(ts)}</td>
        <td class="t-bias ${(r.Bias || "").toUpperCase() === "BUY" ? "up" : "down"}">${esc(r.Bias)}</td>
        <td>${esc(r.Mode)}</td>
        <td>${fmtPrice(r.Entry)}</td>
        <td>${fmtPrice(r.SL)}</td>
        <td>${fmtPrice(r.TP)}</td>
        <td><span class="outcome ${esc(oc)}">${esc(oc)}</span></td>
        <td class="${rcls}">${rv == null ? "—" : (rv >= 0 ? "+" : "") + rv}</td>
      </tr>`;
    }).join("");
  }

  function drawEquity(curve) {
    const canvas = $("equityChart");
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.parentElement.clientWidth;
    const H = 120;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    // zero line
    ctx.strokeStyle = "#232b3b";
    ctx.beginPath(); ctx.moveTo(0, H / 2); ctx.lineTo(W, H / 2); ctx.stroke();

    if (!curve || curve.length < 2) {
      ctx.fillStyle = "#5f6b80"; ctx.font = "12px Inter, sans-serif";
      ctx.fillText("No resolved trades yet — equity curve will appear here.", 8, H / 2 - 6);
      return;
    }

    const min = Math.min(0, ...curve), max = Math.max(0, ...curve);
    const range = (max - min) || 1;
    const x = (i) => (i / (curve.length - 1)) * W;
    const y = (v) => 6 + (1 - (v - min) / range) * (H - 12);

    // fill
    ctx.beginPath();
    ctx.moveTo(x(0), y(curve[0]));
    curve.forEach((v, i) => ctx.lineTo(x(i), y(v)));
    ctx.lineTo(x(curve.length - 1), H - 6); ctx.lineTo(x(0), H - 6); ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, "rgba(232,179,75,0.25)"); grad.addColorStop(1, "rgba(232,179,75,0)");
    ctx.fillStyle = grad; ctx.fill();

    // line
    ctx.strokeStyle = "#e8b34b"; ctx.lineWidth = 2; ctx.beginPath();
    curve.forEach((v, i) => i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v)));
    ctx.stroke();
  }

  // =====================================================================
  // News
  // =====================================================================
  function renderNews(n) {
    $("newsTag").textContent = n.live ? "live RSS" : "offline";
    const list = $("newsList");
    if (!n.items.length) {
      const srcs = (n.fallback_sources || []).map(([label, url]) =>
        `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>`).join(" · ");
      list.innerHTML = `<div class="empty">Live feeds unreachable from this server.<br/><br/>
        Recommended sources:<br/><span style="line-height:2">${srcs}</span><br/><br/>
        <span style="font-size:11px">On your production box the RSS feeds will populate automatically.</span></div>`;
      return;
    }
    list.innerHTML = n.items.map((it) => {
      const t = it.published ? new Date(it.published) : null;
      const when = t ? t.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
      return `<div class="news-item">
        <a href="${esc(it.link)}" target="_blank" rel="noopener"><div class="news-title">${esc(it.title)}</div></a>
        <div class="news-meta"><span class="news-src">${esc(it.source)}</span><span>${esc(when)}</span></div>
      </div>`;
    }).join("");
  }

  // =====================================================================
  // Calendar
  // =====================================================================
  function renderCalendar(cal) {
    const list = $("calList");
    if (!cal.events.length) {
      list.innerHTML = '<div class="empty">No upcoming events.</div>';
      return;
    }
    list.innerHTML = cal.events.map((e) => {
      const d = new Date(e.date + "T00:00:00Z");
      const day = d.getUTCDate(), mon = d.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
      const daysAway = e.days_away;
      let when = "";
      if (daysAway < 0) when = "past";
      else if (daysAway === 0) when = "TODAY";
      else if (daysAway === 1) when = "tomorrow";
      else when = daysAway + " days";
      const cls = daysAway === 0 ? "cal-item today" : "cal-item";
      return `<div class="${cls}">
        <div class="cal-date"><div class="d">${day}</div><div class="m">${mon}</div></div>
        <div class="cal-body">
          <div class="cal-event"><span class="impact ${e.impact}"></span>${esc(e.event)}</div>
          <div class="cal-note">${esc(e.note)}</div>
          <div class="cal-meta">${esc(e.ccy)} · ${esc(e.time_utc)} UTC · ${esc(when)}</div>
        </div>
      </div>`;
    }).join("");
  }

  // =====================================================================
  // Clock + lifecycle
  // =====================================================================
  function tick() {
    const now = new Date();
    $("clock").textContent = now.toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    }) + " UTC" + (now.getTimezoneOffset() === 0 ? "" : "");
  }

  $("toggleEma").addEventListener("click", function () {
    showEma = !showEma;
    this.classList.toggle("active", showEma);
    applyEmaVisibility();
  });

  window.addEventListener("resize", () => {
    if (priceChart) priceChart.applyOptions({ autoSize: true });
    if (rsiChart) rsiChart.applyOptions({ autoSize: true });
  });

  tick();
  setInterval(tick, 1000);
  loadAll();
  setInterval(loadAll, REFRESH_MS);
})();
