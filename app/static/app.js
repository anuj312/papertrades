// app/static/app.js
// Full clean JS: Trade search (pretty option labels) + per-lot capital + paper order
// + Dashboard live refresh + EXIT (no confirmation) via /api/exit

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

// -----------------------------
// Pretty labels for NFO options
// -----------------------------
function fmtStrike(x) {
  if (x === null || x === undefined) return "";
  const n = Number(x);
  if (!Number.isFinite(n)) return String(x);
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

/**
 * Fix for the "26AUG14" problem:
 * Some symbols like ADANIGREEN26AUG1400CE can be mis-parsed by regex (expiry grabs "14").
 * Here we split by removing the known suffix "<strike><CE/PE>" from the end.
 */
function parseOptCodeFromTS(tradingsymbol, strike, optType, name) {
  const ts = String(tradingsymbol || "").toUpperCase().replace(/\s+/g, "");
  const type = String(optType || "").toUpperCase();
  const under = String(name || "").toUpperCase().replace(/\s+/g, "");

  const strikeNum = Number(strike);
  const strikeStrs = [];
  if (Number.isFinite(strikeNum)) {
    const s = Number.isInteger(strikeNum) ? String(parseInt(strikeNum, 10)) : String(strikeNum);
    strikeStrs.push(s);
    strikeStrs.push(s.replace(".", "")); // some symbols omit dot
  }

  // Best split: remove suffix "<strike><CE/PE>"
  for (const ss of strikeStrs) {
    const suf = ss + type;
    if (type && ss && ts.endsWith(suf)) {
      const prefix = ts.slice(0, ts.length - suf.length); // underlying + expiry
      let exp = prefix;

      if (under && prefix.startsWith(under)) exp = prefix.slice(under.length);
      else exp = prefix.replace(/^[A-Z]+/, ""); // remove leading letters if underlying unknown

      return { under: under || "", exp, strike: ss, type };
    }
  }

  // Fallback regex (rare)
  const m = ts.match(/^([A-Z]+)(\d{1,2}[A-Z]{3}\d{0,2})(\d+(?:\.\d+)?)(CE|PE)$/);
  if (!m) return null;
  return { under: m[1], exp: m[2], strike: m[3], type: m[4] };
}

function prettyLabel(it) {
  const exch = String(it.exchange || "");
  const ts = String(it.tradingsymbol || "");
  const name = String(it.name || "").toUpperCase();
  const type = String(it.instrument_type || "").toUpperCase();

  // Options: ADANIGREEN 26AUG 1400 CE
  if (exch === "NFO" && (type === "CE" || type === "PE")) {
    const parsed = parseOptCodeFromTS(ts, it.strike, type, name);
    let expCode = parsed?.exp || ""; // e.g. 26AUG or 26AUG24
    // Optional: strip 2-digit year if present (26AUG24 -> 26AUG)
    expCode = expCode.replace(/^(\d{1,2}[A-Z]{3})\d{2}$/, "$1");

    const strike = fmtStrike(it.strike ?? parsed?.strike);
    const under = name || parsed?.under || ts;

    return `${under} ${expCode} ${strike} ${type}`.replace(/\s+/g, " ").trim();
  }

  // Stocks/others
  return `${exch}:${ts}`;
}

// =====================
// Dashboard (Exit + P&L)
// =====================
async function refreshDashboard() {
  if (!document.getElementById("posTable")) return;

  const data = await jget("/api/dashboard");

  const kCash = document.getElementById("kCash");
  const kUnr = document.getElementById("kUnr");
  const kRel = document.getElementById("kRel");
  const kNet = document.getElementById("kNet");

  if (kCash) kCash.textContent = "₹ " + fmtNum(data.cash);
  if (kUnr) kUnr.textContent = "₹ " + fmtNum(data.unrealized);
  if (kRel) kRel.textContent = "₹ " + fmtNum(data.realized);
  if (kNet) kNet.textContent = "₹ " + fmtNum(data.net_liq);

  setPnLClass(kUnr, data.unrealized);
  setPnLClass(kRel, data.realized);

  // Positions
  const tb = document.querySelector("#posTable tbody");
  if (tb) {
    tb.innerHTML = "";
    for (const p of (data.positions || [])) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${p.symbol}</td>
        <td class="text-end">${p.qty}</td>
        <td class="text-end">${fmtNum(p.avg)}</td>
        <td class="text-end">${fmtNum(p.ltp)}</td>
        <td class="text-end fw-bold">${fmtNum(p.pnl)}</td>
        <td class="text-end">
          <button class="btn btn-sm btn-outline-warning exit-btn"
                  data-instrument-id="${p.instrument_id}">
            EXIT
          </button>
        </td>
      `;
      setPnLClass(tr.querySelector("td:nth-child(5)"), p.pnl);
      tb.appendChild(tr);
    }
  }

  // Orders
  const ob = document.querySelector("#ordTable tbody");
  if (ob) {
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
  }

  // Trades
  const th = document.querySelector("#trdTable tbody");
  if (th) {
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
  }
}

function initDashboardExitHandler() {
  const table = document.getElementById("posTable");
  if (!table) return;

  table.addEventListener("click", async (e) => {
    const btn = e.target.closest(".exit-btn");
    if (!btn) return;

    const instrumentId = btn.getAttribute("data-instrument-id");
    if (!instrumentId) return;

    // No confirmation: exit immediately
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

// =====================
// Trade page (Search/Buy/Sell)
// =====================
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
  const qtyOrder = document.getElementById("qtyOrder");
  const capOrder = document.getElementById("capOrder");

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
    if (qtyPerLot) qtyPerLot.textContent = "—";
    if (capPerLot) capPerLot.textContent = "—";
    if (qtyOrder) qtyOrder.textContent = "—";
    if (capOrder) capOrder.textContent = "—";
  }

  function updateCapitalUI() {
    if (!currentInstrument || !lastQuote || lastQuote.ltp === null || lastQuote.ltp === undefined) {
      clearCapitalUI();
      return;
    }

    const ltp = Number(lastQuote.ltp);
    const lot = Number(lastQuote.lot_size || currentInstrument.lot_size || 1);
    const lots = Math.max(1, Number(lotsInp?.value || 1));
    const side = String(sideSel?.value || "BUY").toUpperCase();

    const perLotQty = lot;
    const ordQty = lot * lots;

    const perLotNotional = ltp * perLotQty;
    const ordNotional = ltp * ordQty;

    if (qtyPerLot) qtyPerLot.textContent = String(perLotQty);
    if (capPerLot) capPerLot.textContent = fmtINR(perLotNotional);

    if (qtyOrder) qtyOrder.textContent = String(ordQty);
    if (capOrder) {
      capOrder.textContent = side === "SELL"
        ? `${fmtINR(ordNotional)} (credit)`
        : fmtINR(ordNotional);
    }
  }

  const doSearch = debounce(async () => {
    const q = searchBox.value.trim();
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
      b.className = "list-group-item list-group-item-action bg-dark text-light border-secondary";
      b.type = "button";

      const primary = prettyLabel(it);
      const secondary = `${it.exchange}:${it.tradingsymbol} • lot ${it.lot_size}`;

      b.innerHTML = `
        <div class="fw-bold">${primary}</div>
        <div class="small text-secondary">${secondary}</div>
      `;

      b.onclick = () => {
        currentInstrument = it;
        instrumentId.value = it.instrument_id;

        if (selSymbol) selSymbol.textContent = primary;
        if (selType) selType.textContent = it.instrument_type || "—";
        if (selLot) selLot.textContent = it.lot_size;

        results.innerHTML = "";
        lastQuote = null;
        clearCapitalUI();
      };

      results.appendChild(b);
    }
  }, 200);

  searchBox.addEventListener("input", doSearch);
  lotsInp?.addEventListener("input", updateCapitalUI);
  sideSel?.addEventListener("change", updateCapitalUI);

  // Quote polling
  setInterval(async () => {
    if (!currentInstrument) return;
    try {
      const q = await jget(`/api/quote/${currentInstrument.instrument_id}`);
      lastQuote = q;

      if (selLtp) selLtp.textContent = (q.ltp === null ? "—" : Number(q.ltp).toFixed(2));
      if (mktStatus) {
        mktStatus.textContent = q.market_open
          ? "Market open (live quotes)"
          : "Market closed (last quote if available)";
      }
      updateCapitalUI();
    } catch {
      // ignore
    }
  }, 1500);

  // Mini account panel polling
  setInterval(async () => {
    try {
      const d = await jget("/api/dashboard");
      if (dashCash) dashCash.textContent = fmtINR(d.cash);
      if (dashNet) {
        dashNet.textContent = fmtINR(d.net_liq);
        setPnLClass(dashNet, Number(d.net_liq) - Number(d.cash));
      }
    } catch {
      // ignore
    }
  }, 2500);

  // Place paper order
  orderForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (orderMsg) orderMsg.innerHTML = "";

    if (!instrumentId.value) {
      if (orderMsg) orderMsg.innerHTML = `<div class="alert alert-warning py-2">Select an instrument first.</div>`;
      return;
    }

    try {
      const res = await jpostForm("/api/order", {
        instrument_id: instrumentId.value,
        side: sideSel?.value || "BUY",
        lots: lotsInp?.value || "1",
      });

      if (orderMsg) {
        orderMsg.innerHTML = `
          <div class="alert alert-success py-2">
            Order #${res.order_id}: FILLED @ ${Number(res.fill_price).toFixed(2)}
          </div>
        `;
      }
    } catch (err) {
      if (orderMsg) orderMsg.innerHTML = `<div class="alert alert-danger py-2">${err}</div>`;
    }
  });
}

// ---------------- Boot ----------------
window.addEventListener("load", () => {
  const page = (window.PAPERTRADE && window.PAPERTRADE.page) || "";

  if (page === "dashboard") {
    initDashboardExitHandler();
    refreshDashboard().catch(() => {});
    setInterval(() => refreshDashboard().catch(() => {}), 2000);
  }

  if (page === "trade") {
    initTrade().catch(() => {});
  }
});