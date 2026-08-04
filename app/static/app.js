// app/static/app.js

async function jget(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return await r.json();
}

async function jpostForm(url, obj) {
  const fd = new FormData();
  for (const [k, v] of Object.entries(obj)) fd.append(k, String(v));
  const r = await fetch(url, { method: "POST", body: fd });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || JSON.stringify(data));
  return data;
}

function fmtNum(n) {
  if (n === null || n === undefined) return "—";
  const x = Number(n);
  if (!Number.isFinite(x)) return "—";
  return x.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function fmtINR(n) {
  if (n === null || n === undefined) return "—";
  const x = Number(n);
  if (!Number.isFinite(x)) return "—";
  return "₹ " + x.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function fmtPct(x) {
  const n = Number(x);
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(2) + "%";
}

function setPnLClass(el, v) {
  if (!el) return;
  el.classList.remove("text-success", "text-danger");
  const x = Number(v);
  if (!Number.isFinite(x)) return;
  if (x > 0) el.classList.add("text-success");
  if (x < 0) el.classList.add("text-danger");
}

function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function showDashMsg(html) {
  const el = document.getElementById("dashMsg");
  if (!el) return;
  el.innerHTML = html;
  setTimeout(() => { el.innerHTML = ""; }, 3000);
}

// ---------------- Pretty labels for NFO options (used in search + modal) ----------------
function fmtStrike(x) {
  if (x === null || x === undefined) return "";
  const n = Number(x);
  if (!Number.isFinite(n)) return String(x);
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

// Fix for 26AUG14 parsing: split by stripping "<strike><CE/PE>" suffix
function parseOptCodeFromTS(tradingsymbol, strike, optType, name) {
  const ts = String(tradingsymbol || "").toUpperCase().replace(/\s+/g, "");
  const type = String(optType || "").toUpperCase();
  const under = String(name || "").toUpperCase().replace(/\s+/g, "");

  const strikeNum = Number(strike);
  const strikeStrs = [];
  if (Number.isFinite(strikeNum)) {
    const s = Number.isInteger(strikeNum) ? String(parseInt(strikeNum, 10)) : String(strikeNum);
    strikeStrs.push(s);
    strikeStrs.push(s.replace(".", ""));
  }

  for (const ss of strikeStrs) {
    const suf = ss + type;
    if (type && ss && ts.endsWith(suf)) {
      const prefix = ts.slice(0, ts.length - suf.length);
      let exp = prefix;
      if (under && prefix.startsWith(under)) exp = prefix.slice(under.length);
      else exp = prefix.replace(/^[A-Z]+/, "");
      return { under: under || "", exp, strike: ss, type };
    }
  }

  // fallback regex (rare)
  const m = ts.match(/^([A-Z]+)(\d{1,2}[A-Z]{3}\d{0,2})(\d+(?:\.\d+)?)(CE|PE)$/);
  if (!m) return null;
  return { under: m[1], exp: m[2], strike: m[3], type: m[4] };
}

function prettyLabel(it) {
  const exch = String(it.exchange || "");
  const ts = String(it.tradingsymbol || "");
  const name = String(it.name || "").toUpperCase();
  const type = String(it.instrument_type || "").toUpperCase();

  if (exch === "NFO" && (type === "CE" || type === "PE")) {
    const parsed = parseOptCodeFromTS(ts, it.strike, type, name);
    let expCode = parsed?.exp || "";
    expCode = expCode.replace(/^(\d{1,2}[A-Z]{3})\d{2}$/, "$1"); // strip 2-digit year
    const strike = fmtStrike(it.strike ?? parsed?.strike);
    const under = name || parsed?.under || ts;
    return `${under} ${expCode} ${strike} ${type}`.replace(/\s+/g, " ").trim();
  }

  return `${exch}:${ts}`;
}

// ===================== Dashboard refresh + EXIT =====================
async function refreshDashboard() {
  if (!document.getElementById("posTable")) return null;

  const data = await jget("/api/dashboard");

  document.getElementById("kCash").textContent = "₹ " + fmtNum(data.cash);
  document.getElementById("kUnr").textContent  = "₹ " + fmtNum(data.unrealized);
  document.getElementById("kRel").textContent  = "₹ " + fmtNum(data.realized);
  document.getElementById("kNet").textContent  = "₹ " + fmtNum(data.net_liq);

  setPnLClass(document.getElementById("kUnr"), data.unrealized);
  setPnLClass(document.getElementById("kRel"), data.realized);

  // Positions (✅ remove NFO:/NSE: prefix + ✅ add RET%)
  const tb = document.querySelector("#posTable tbody");
  tb.innerHTML = "";

  for (const p of (data.positions || [])) {
    const rawSym = String(p.symbol || "");
    const dispSym = rawSym.includes(":") ? rawSym.split(":").pop() : rawSym;

    const qty = Number(p.qty || 0);
    const avg = Number(p.avg || 0);
    const pnl = Number(p.pnl || 0);

    // Return% based on entry notional (premium × qty)
    const denom = Math.abs(qty) * avg;
    const retPct = denom > 0 ? (pnl / denom) * 100.0 : null;

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${dispSym}</td>
      <td class="text-end">${qty}</td>
      <td class="text-end">${fmtNum(avg)}</td>
      <td class="text-end">${fmtNum(p.ltp)}</td>
      <td class="text-end fw-bold">${fmtPct(retPct)}</td>
      <td class="text-end fw-bold">${fmtNum(pnl)}</td>
      <td class="text-end">
        <button class="btn btn-sm btn-outline-warning exit-btn" data-instrument-id="${p.instrument_id}">
          EXIT
        </button>
      </td>
    `;

    // Color RET% and P&L
    setPnLClass(tr.querySelector("td:nth-child(5)"), retPct);
    setPnLClass(tr.querySelector("td:nth-child(6)"), pnl);

    tb.appendChild(tr);
  }

  // Orders
  const ob = document.querySelector("#ordTable tbody");
  ob.innerHTML = "";
  for (const o of (data.orders || [])) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${o.id}</td>
      <td>${o.symbol}</td>
      <td>${o.side}</td>
      <td class="text-end">${o.lots}</td>
      <td class="text-end">${o.qty}</td>
      <td class="text-end">${fmtNum(o.price)}</td>
      <td>${o.status}</td>
    `;
    ob.appendChild(tr);
  }

  // Trades
  const th = document.querySelector("#trdTable tbody");
  th.innerHTML = "";
  for (const t of (data.trades || [])) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${t.id}</td>
      <td>${t.symbol}</td>
      <td>${t.side}</td>
      <td class="text-end">${t.qty}</td>
      <td class="text-end">${fmtNum(t.price)}</td>
      <td class="text-secondary small">${t.time}</td>
    `;
    th.appendChild(tr);
  }

  return data;
}

function initDashboardExitHandler() {
  const table = document.getElementById("posTable");
  if (!table) return;

  table.addEventListener("click", async (e) => {
    const btn = e.target.closest(".exit-btn");
    if (!btn) return;

    const instrumentId = btn.getAttribute("data-instrument-id");
    if (!instrumentId) return;

    btn.disabled = true;
    const oldText = btn.textContent;
    btn.textContent = "EXITING...";

    try {
      const res = await jpostForm("/api/exit", { instrument_id: instrumentId });
      showDashMsg(`<div class="alert alert-success py-2">
        ${res.message} • ${res.side} ${res.lots} lots @ ${Number(res.fill_price).toFixed(2)} (Order #${res.order_id})
      </div>`);
      await refreshDashboard();
    } catch (err) {
      showDashMsg(`<div class="alert alert-danger py-2">Exit failed: ${err}</div>`);
      btn.disabled = false;
      btn.textContent = oldText || "EXIT";
    }
  });
}

// ===================== Dashboard Quick Trade (Navbar Search + Modal) =====================
function initDashboardQuickTrade() {
  const box = document.getElementById("dashSearchBox");
  const results = document.getElementById("dashSearchResults");
  const modalEl = document.getElementById("orderModal");
  if (!box || !results || !modalEl || typeof bootstrap === "undefined") return;

  const modal = new bootstrap.Modal(modalEl);

  const mSymbol = document.getElementById("mSymbol");
  const mType = document.getElementById("mType");
  const mLot = document.getElementById("mLot");
  const mLtp = document.getElementById("mLtp");
  const mMkt = document.getElementById("mMkt");
  const mCapPerLot = document.getElementById("mCapPerLot");
  const mCapOrder = document.getElementById("mCapOrder");
  const mMsg = document.getElementById("mMsg");

  const mForm = document.getElementById("mOrderForm");
  const mInstrumentId = document.getElementById("mInstrumentId");
  const mSide = document.getElementById("mSide");
  const mLots = document.getElementById("mLots");

  let selected = null;
  let quoteTimer = null;
  let lastQuote = null;

  function clearQuoteUI() {
    if (mLtp) mLtp.textContent = "—";
    if (mMkt) mMkt.textContent = "";
    if (mCapPerLot) mCapPerLot.textContent = "—";
    if (mCapOrder) mCapOrder.textContent = "—";
  }

  function updateCapUI() {
    if (!selected || !lastQuote || lastQuote.ltp == null) {
      clearQuoteUI();
      return;
    }
    const ltp = Number(lastQuote.ltp);
    const lot = Number(lastQuote.lot_size || selected.lot_size || 1);
    const lots = Math.max(1, Number(mLots?.value || 1));
    const side = String(mSide?.value || "BUY").toUpperCase();

    const perLot = ltp * lot;
    const ord = ltp * lot * lots;

    if (mCapPerLot) mCapPerLot.textContent = fmtINR(perLot);
    if (mCapOrder) mCapOrder.textContent = side === "SELL" ? `${fmtINR(ord)} (credit)` : fmtINR(ord);
  }

  async function pollQuoteOnce() {
    if (!selected) return;
    try {
      const q = await jget(`/api/quote/${selected.instrument_id}`);
      lastQuote = q;
      if (mLtp) mLtp.textContent = (q.ltp == null ? "—" : Number(q.ltp).toFixed(2));
      if (mMkt) mMkt.textContent = q.market_open ? "Market open (live quotes)" : "Market closed (last quote if available)";
      updateCapUI();
    } catch {
      // ignore
    }
  }

  function startQuotePoll() {
    stopQuotePoll();
    pollQuoteOnce();
    quoteTimer = setInterval(pollQuoteOnce, 1500);
  }

  function stopQuotePoll() {
    if (quoteTimer) clearInterval(quoteTimer);
    quoteTimer = null;
  }

  modalEl.addEventListener("hidden.bs.modal", () => {
    stopQuotePoll();
    selected = null;
    lastQuote = null;
    if (mMsg) mMsg.innerHTML = "";
  });

  const doSearch = debounce(async () => {
    const q = box.value.trim();
    results.innerHTML = "";
    if (q.length < 2) return;

    let items = [];
    try {
      items = await jget(`/api/search?q=${encodeURIComponent(q)}`);
    } catch {
      return;
    }

    for (const it of (items || [])) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "list-group-item list-group-item-action bg-dark text-light border-secondary";

      const primary = prettyLabel(it);
      const secondary = `${it.exchange}:${it.tradingsymbol} • lot ${it.lot_size}`;
      b.innerHTML = `<div class="fw-bold">${primary}</div><div class="small text-secondary">${secondary}</div>`;

      b.onclick = () => {
        selected = it;
        results.innerHTML = "";
        box.value = "";

        if (mInstrumentId) mInstrumentId.value = it.instrument_id;
        if (mSymbol) mSymbol.textContent = primary;
        if (mType) mType.textContent = it.instrument_type || "—";
        if (mLot) mLot.textContent = it.lot_size;

        if (mLots) mLots.value = "1";
        if (mSide) mSide.value = "BUY";

        lastQuote = null;
        clearQuoteUI();
        if (mMsg) mMsg.innerHTML = "";

        modal.show();
        startQuotePoll();
      };

      results.appendChild(b);
    }
  }, 200);

  box.addEventListener("input", doSearch);
  mLots?.addEventListener("input", updateCapUI);
  mSide?.addEventListener("change", updateCapUI);

  mForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (mMsg) mMsg.innerHTML = "";

    if (!mInstrumentId?.value) {
      if (mMsg) mMsg.innerHTML = `<div class="alert alert-warning py-2">No instrument selected.</div>`;
      return;
    }

    try {
      const res = await jpostForm("/api/order", {
        instrument_id: mInstrumentId.value,
        side: mSide?.value || "BUY",
        lots: mLots?.value || "1",
      });

      if (mMsg) {
        mMsg.innerHTML = `<div class="alert alert-success py-2">
          Order #${res.order_id}: FILLED @ ${Number(res.fill_price).toFixed(2)}
        </div>`;
      }

      await refreshDashboard();
    } catch (err) {
      if (mMsg) mMsg.innerHTML = `<div class="alert alert-danger py-2">${err}</div>`;
    }
  });
}

// ===================== Trade page =====================
async function initTrade() {
  const searchBox = document.getElementById("searchBox");
  const results = document.getElementById("searchResults");

  const selSymbol = document.getElementById("selSymbol");
  const selType = document.getElementById("selType");
  const selLot = document.getElementById("selLot");
  const selLtp = document.getElementById("selLtp");
  const mktStatus = document.getElementById("mktStatus");

  const qtyPerLot = document.getElementById("qtyPerLot");
  const capPerLot = document.getElementById("capPerLot");
  const qtyOrder  = document.getElementById("qtyOrder");
  const capOrder  = document.getElementById("capOrder");

  const instrumentId = document.getElementById("instrumentId");
  const orderForm = document.getElementById("orderForm");
  const orderMsg = document.getElementById("orderMsg");

  const dashCash = document.getElementById("dashCash");
  const dashNet = document.getElementById("dashNet");

  if (!searchBox || !results || !orderForm) return;

  const sideSel = orderForm.querySelector('select[name="side"]');
  const lotsInp = orderForm.querySelector('input[name="lots"]');

  let currentInstrument = null;
  let lastQuote = null;

  function clearCapitalUI() {
    qtyPerLot.textContent = "—";
    capPerLot.textContent = "—";
    qtyOrder.textContent = "—";
    capOrder.textContent = "—";
  }

  function updateCapitalUI() {
    if (!currentInstrument || !lastQuote || lastQuote.ltp == null) {
      clearCapitalUI();
      return;
    }
    const ltp = Number(lastQuote.ltp);
    const lot = Number(lastQuote.lot_size || currentInstrument.lot_size || 1);
    const lots = Math.max(1, Number(lotsInp.value || 1));
    const side = String(sideSel.value || "BUY").toUpperCase();

    const perLotQty = lot;
    const ordQty = lot * lots;

    qtyPerLot.textContent = String(perLotQty);
    capPerLot.textContent = fmtINR(ltp * perLotQty);

    qtyOrder.textContent = String(ordQty);
    capOrder.textContent = side === "SELL"
      ? `${fmtINR(ltp * ordQty)} (credit)`
      : fmtINR(ltp * ordQty);
  }

  const doSearch = debounce(async () => {
    const q = searchBox.value.trim();
    results.innerHTML = "";
    if (q.length < 2) return;

    const items = await jget(`/api/search?q=${encodeURIComponent(q)}`);
    for (const it of (items || [])) {
      const b = document.createElement("button");
      b.className = "list-group-item list-group-item-action bg-dark text-light border-secondary";
      b.type = "button";

      const primary = prettyLabel(it);
      const secondary = `${it.exchange}:${it.tradingsymbol} • lot ${it.lot_size}`;
      b.innerHTML = `<div class="fw-bold">${primary}</div><div class="small text-secondary">${secondary}</div>`;

      b.onclick = () => {
        currentInstrument = it;
        instrumentId.value = it.instrument_id;

        selSymbol.textContent = primary;
        selType.textContent = it.instrument_type || "—";
        selLot.textContent = it.lot_size;

        results.innerHTML = "";
        lastQuote = null;
        clearCapitalUI();
      };

      results.appendChild(b);
    }
  }, 200);

  searchBox.addEventListener("input", doSearch);
  lotsInp.addEventListener("input", updateCapitalUI);
  sideSel.addEventListener("change", updateCapitalUI);

  setInterval(async () => {
    if (!currentInstrument) return;
    const q = await jget(`/api/quote/${currentInstrument.instrument_id}`);
    lastQuote = q;

    selLtp.textContent = (q.ltp == null ? "—" : Number(q.ltp).toFixed(2));
    mktStatus.textContent = q.market_open ? "Market open (live quotes)" : "Market closed (last quote if available)";
    updateCapitalUI();
  }, 2000); // Render-safe

  setInterval(async () => {
    const d = await jget("/api/dashboard");
    dashCash.textContent = fmtINR(d.cash);
    dashNet.textContent = fmtINR(d.net_liq);
    setPnLClass(dashNet, d.net_liq - d.cash);
  }, 4000);

  orderForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    orderMsg.innerHTML = "";

    if (!instrumentId.value) {
      orderMsg.innerHTML = `<div class="alert alert-warning py-2">Select an instrument first.</div>`;
      return;
    }

    try {
      const res = await jpostForm("/api/order", {
        instrument_id: instrumentId.value,
        side: sideSel.value,
        lots: lotsInp.value,
      });
      orderMsg.innerHTML = `<div class="alert alert-success py-2">
        Order #${res.order_id}: FILLED @ ${Number(res.fill_price).toFixed(2)}
      </div>`;
    } catch (err) {
      orderMsg.innerHTML = `<div class="alert alert-danger py-2">${err}</div>`;
    }
  });
}

// ---------------- Boot ----------------
window.addEventListener("load", () => {
  const page = (window.PAPERTRADE && window.PAPERTRADE.page) || "";

  if (page === "dashboard") {
    initDashboardExitHandler();
    initDashboardQuickTrade();

    // Render-safe dynamic refresh: 1.5s for 0/1 position, else 5s. Pauses when tab hidden.
    let inFlight = false;

    async function loop() {
      if (document.hidden) {
        setTimeout(loop, 2000);
        return;
      }
      if (inFlight) return;

      inFlight = true;
      let delay = 5000;

      try {
        const data = await refreshDashboard();
        const npos = data?.positions?.length ? data.positions.length : 0;
        delay = (npos <= 1) ? 1500 : 5000;
      } catch {
        delay = 5000;
      } finally {
        inFlight = false;
        setTimeout(loop, delay);
      }
    }

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) loop();
    });

    loop();
  }

  if (page === "trade") {
    initTrade().catch(() => {});
  }
});