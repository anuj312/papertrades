// app/static/app.js

async function jget(url) {
  const r = await fetch(url);
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
  return Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function fmtINR(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  return "₹ " + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function setPnLClass(el, v) {
  if (!el) return;
  el.classList.remove("text-success", "text-danger");
  if (v > 0) el.classList.add("text-success");
  if (v < 0) el.classList.add("text-danger");
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

// ---------------- Dashboard ----------------
async function refreshDashboard() {
  const data = await jget("/api/dashboard");

  document.getElementById("kCash").textContent = "₹ " + fmtNum(data.cash);
  document.getElementById("kUnr").textContent = "₹ " + fmtNum(data.unrealized);
  document.getElementById("kRel").textContent = "₹ " + fmtNum(data.realized);
  document.getElementById("kNet").textContent = "₹ " + fmtNum(data.net_liq);

  setPnLClass(document.getElementById("kUnr"), data.unrealized);
  setPnLClass(document.getElementById("kRel"), data.realized);

  // Positions
  const tb = document.querySelector("#posTable tbody");
  tb.innerHTML = "";
  for (const p of data.positions) {
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

  // Orders
  const ob = document.querySelector("#ordTable tbody");
  ob.innerHTML = "";
  for (const o of data.orders) {
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
  for (const t of data.trades) {
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

// ---------------- Trade ----------------
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
    if (!currentInstrument || !lastQuote || lastQuote.ltp === null || lastQuote.ltp === undefined) {
      clearCapitalUI();
      return;
    }

    const ltp = Number(lastQuote.ltp);
    const lot = Number(lastQuote.lot_size || currentInstrument.lot_size || 1);
    const lots = Math.max(1, Number(lotsInp.value || 1));
    const side = String(sideSel.value || "BUY").toUpperCase();

    const perLotQty = lot;
    const ordQty = lot * lots;

    const perLotNotional = ltp * perLotQty;
    const ordNotional = ltp * ordQty;

    qtyPerLot.textContent = String(perLotQty);
    capPerLot.textContent = fmtINR(perLotNotional);

    qtyOrder.textContent = String(ordQty);
    capOrder.textContent = side === "SELL"
      ? `${fmtINR(ordNotional)} (credit)`
      : fmtINR(ordNotional);
  }

  const doSearch = debounce(async () => {
    const q = searchBox.value.trim();
    results.innerHTML = "";
    if (q.length < 2) return;

    const items = await jget(`/api/search?q=${encodeURIComponent(q)}`);

    for (const it of items) {
      const b = document.createElement("button");
      b.className = "list-group-item list-group-item-action bg-dark text-light border-secondary";
      b.type = "button";

      const exp = it.expiry ? ` • ${it.expiry}` : "";
      const strike = (it.strike !== null && it.strike !== undefined) ? ` • ${it.strike}` : "";
      const typ = it.instrument_type ? ` • ${it.instrument_type}` : "";
      const label = `${it.exchange}:${it.tradingsymbol}${typ}${strike}${exp}`;

      b.innerHTML = `
        <div class="fw-bold">${label}</div>
        <div class="small text-secondary">${it.name || ""} • lot ${it.lot_size}</div>
      `;

      b.onclick = () => {
        currentInstrument = it;
        instrumentId.value = it.instrument_id;

        selSymbol.textContent = `${it.exchange}:${it.tradingsymbol}`;
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

  // Quote polling
  setInterval(async () => {
    if (!currentInstrument) return;
    const q = await jget(`/api/quote/${currentInstrument.instrument_id}`);
    lastQuote = q;

    selLtp.textContent = (q.ltp === null ? "—" : Number(q.ltp).toFixed(2));
    mktStatus.textContent = q.market_open
      ? "Market open (live quotes)"
      : "Market closed (last quote if available)";

    updateCapitalUI();
  }, 1500);

  // Account mini panel polling
  setInterval(async () => {
    const d = await jget("/api/dashboard");
    dashCash.textContent = fmtINR(d.cash);
    dashNet.textContent = fmtINR(d.net_liq);
    setPnLClass(dashNet, d.net_liq - d.cash);
  }, 2500);

  // Place order
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
      orderMsg.innerHTML = `
        <div class="alert alert-success py-2">
          Order #${res.order_id}: FILLED @ ${Number(res.fill_price).toFixed(2)}
        </div>
      `;
    } catch (err) {
      orderMsg.innerHTML = `<div class="alert alert-danger py-2">${err}</div>`;
    }
  });
}

// ---------------- Boot per page ----------------
window.addEventListener("load", () => {
  const page = (window.PAPERTRADE && window.PAPERTRADE.page) || "";
  if (page === "dashboard") {
    initDashboardExitHandler();
    refreshDashboard();
    setInterval(refreshDashboard, 2000);
  }
  if (page === "trade") {
    initTrade();
  }
});