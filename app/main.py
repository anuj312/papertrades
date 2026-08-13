import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .kitehub import kitehub, market_is_open_ist
from .store import store

IST = ZoneInfo("Asia/Kolkata")

app = FastAPI(title="Paper Trading (Demo Mode)")

BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.on_event("startup")
def startup():
    # Init DB table(s)
    try:
        db.init_db()
    except Exception as e:
        print("DB init failed:", e)

    # Load instruments + start websocket for quotes
    store.load_instruments(kitehub.kite, exchanges=("NSE", "NFO"))
    try:
        kitehub.start_ws()
    except Exception:
        pass


# ---------------- Pages ----------------
@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "market_open": market_is_open_ist(),
    })


@app.get("/trade", response_class=HTMLResponse)
def trade_page(request: Request):
    return templates.TemplateResponse("trade.html", {
        "request": request,
        "market_open": market_is_open_ist(),
    })


@app.get("/daily-pnl", response_class=HTMLResponse)
def daily_pnl_page(request: Request):
    return templates.TemplateResponse("daily_pnl.html", {
        "request": request,
        "market_open": market_is_open_ist(),
    })


# ---------------- APIs ----------------
@app.get("/api/search")
def api_search(q: str):
    return store.search_instruments(q)


@app.get("/api/quote/{instrument_id}")
def api_quote(instrument_id: int):
    ins = store.get_instrument(instrument_id)
    if not ins:
        raise HTTPException(404, "Instrument not found")

    token = int(ins.get("instrument_token") or 0) or None
    if token:
        kitehub.ensure_subscribed(token)

    ltp = kitehub.ltp(ins["exchange"], ins["tradingsymbol"], token)

    lot_size = int(ins.get("lot_size") or 1)
    capital_per_lot = (float(ltp) * lot_size) if ltp is not None else None

    return {
        "instrument_id": int(ins["instrument_id"]),
        "exchange": ins["exchange"],
        "tradingsymbol": ins["tradingsymbol"],
        "instrument_type": ins.get("instrument_type") or "",
        "lot_size": lot_size,
        "ltp": ltp,
        "capital_per_lot": capital_per_lot,
        "market_open": market_is_open_ist(),
        "server_time": datetime.utcnow().isoformat(),
    }


@app.post("/api/order")
def api_order(
    instrument_id: int | None = Form(None),
    side: str | None = Form(None),
    lots: int | None = Form(None),
):
    if instrument_id is None or side is None or lots is None:
        raise HTTPException(400, "Missing instrument_id/side/lots")

    ins = store.get_instrument(int(instrument_id))
    if not ins:
        raise HTTPException(404, "Instrument not found")

    token = int(ins.get("instrument_token") or 0) or None
    ltp = kitehub.ltp(ins["exchange"], ins["tradingsymbol"], token)
    if ltp is None:
        raise HTTPException(503, "Price unavailable (market closed or quote not available)")

    ok, msg, oid = store.place_paper_order(
        instrument=ins,
        side=str(side),
        lots=int(lots),
        fill_price=float(ltp),
    )
    if not ok:
        raise HTTPException(400, msg)

    return {"ok": True, "message": msg, "order_id": oid, "fill_price": float(ltp)}


@app.post("/api/exit")
def api_exit(instrument_id: int | None = Form(None)):
    if instrument_id is None:
        raise HTTPException(400, "Missing instrument_id")

    instrument_id = int(instrument_id)

    pos = store.positions.get(instrument_id)
    if not pos or int(pos.net_qty) == 0:
        raise HTTPException(400, "No open position to exit")

    ins = store.get_instrument(instrument_id)
    if not ins:
        raise HTTPException(404, "Instrument not found")

    net_qty = int(pos.net_qty)
    lot_size = int(pos.lot_size or ins.get("lot_size") or 1)
    if lot_size <= 0:
        lot_size = 1

    abs_qty = abs(net_qty)
    if abs_qty % lot_size != 0:
        raise HTTPException(400, f"Position qty {abs_qty} is not a whole multiple of lot size {lot_size}")

    lots = abs_qty // lot_size
    side = "SELL" if net_qty > 0 else "BUY"

    token = int(ins.get("instrument_token") or 0) or None
    ltp = kitehub.ltp(ins["exchange"], ins["tradingsymbol"], token)
    if ltp is None:
        raise HTTPException(503, "Price unavailable (market closed or quote not available)")

    ok, msg, oid = store.place_paper_order(
        instrument=ins,
        side=side,
        lots=int(lots),
        fill_price=float(ltp),
    )
    if not ok:
        raise HTTPException(400, msg)

    return {
        "ok": True,
        "message": "EXIT FILLED",
        "order_id": oid,
        "fill_price": float(ltp),
        "side": side,
        "lots": int(lots),
    }


@app.get("/api/dashboard")
def api_dashboard():
    acct = store.account

    pos_rows = []
    unrealized = 0.0

    for p in store.positions.values():
        if p.net_qty == 0:
            continue

        ins = store.get_instrument(p.instrument_id)
        token = int(ins.get("instrument_token") or 0) if ins else 0
        if token:
            kitehub.ensure_subscribed(token)

        ltp = kitehub.ltp(p.exchange, p.tradingsymbol, token or None) or 0.0
        pnl = (float(ltp) - float(p.avg_price)) * int(p.net_qty)
        unrealized += float(pnl)

        pos_rows.append({
            "instrument_id": int(p.instrument_id),
            "symbol": f"{p.exchange}:{p.tradingsymbol}",
            "qty": int(p.net_qty),
            "avg": float(p.avg_price),
            "ltp": float(ltp),
            "pnl": float(pnl),
            "lot": int(p.lot_size),
        })

    net_liq = float(acct.cash) + float(unrealized)

    # persist today's netliq so daily pnl updates when dashboard refreshes
    try:
        day_iso = datetime.now(IST).date().isoformat()
        db.upsert_daily_pnl(day_iso, net_liq)
    except Exception as e:
        print("Daily PnL DB write failed:", e)

    return {
        "cash": float(acct.cash),
        "realized": float(acct.realized_pnl),
        "unrealized": float(unrealized),
        "net_liq": float(net_liq),
        "positions": pos_rows,
        "orders": [{
            "id": o.id, "symbol": o.symbol, "side": o.side,
            "lots": o.lots, "qty": o.qty, "price": o.price,
            "status": o.status, "time": o.created_at.isoformat(timespec="seconds")
        } for o in store.orders[:30]],
        "trades": [{
            "id": t.id, "symbol": t.symbol, "side": t.side,
            "qty": t.qty, "price": t.price,
            "time": t.traded_at.isoformat(timespec="seconds")
        } for t in store.trades[:50]],
        "market_open": market_is_open_ist(),
    }


@app.get("/api/daily-pnl")
def api_daily_pnl():
    try:
        rows = db.get_daily_pnl(3650)

        total_pnl_sum = sum(float(r.get("pnl") or 0.0) for r in rows) if rows else 0.0
        total_shared_sum = sum(
            float(r["shared_realized_pnl"])
            for r in rows
            if r.get("shared_realized_pnl") is not None
        )
        total_charges_sum = sum(float(r.get("day_charges") or 0.0) for r in rows)

        overall_change = 0.0
        if rows:
            newest = rows[0]
            oldest = rows[-1]
            overall_change = float(newest["last_net_liq"]) - float(oldest["opening_net_liq"])

        today = datetime.now(IST).date().isoformat()
        today_row = next((r for r in rows if r["day"] == today), None)

        return {
            "ok": True,
            "days": rows,
            "count": len(rows),
            "today": today,
            "today_pnl": float(today_row["pnl"]) if today_row else 0.0,
            "total_pnl_sum": float(total_pnl_sum),
            "overall_change": float(overall_change),
            "total_shared_sum": float(total_shared_sum),
            "total_charges_sum": float(total_charges_sum),
        }
    except Exception as e:
        raise HTTPException(503, f"DB error: {e}")


@app.post("/api/daily-pnl/share")
def api_daily_pnl_share():
    """
    Snapshot saves:
      - shared_realized_pnl
      - shared_symbols      (UNDERLYING ONLY like ASTRAL)
      - shared_money_used   (ONLY opening/increasing exposure; exit not counted)
    """
    acct = store.account

    # compute unrealized same as dashboard
    unrealized = 0.0
    for p in store.positions.values():
        if p.net_qty == 0:
            continue
        ins = store.get_instrument(p.instrument_id)
        token = int(ins.get("instrument_token") or 0) if ins else 0
        if token:
            kitehub.ensure_subscribed(token)
        ltp = kitehub.ltp(p.exchange, p.tradingsymbol, token or None) or 0.0
        unrealized += (float(ltp) - float(p.avg_price)) * int(p.net_qty)

    net_liq = float(acct.cash) + float(unrealized)
    realized = float(acct.realized_pnl)
    day_iso = datetime.now(IST).date().isoformat()

    # --- filter today's trades (IST day) ---
    today_date = datetime.now(IST).date()
    trades_today = []
    for t in store.trades:
        t_ist_date = t.traded_at.replace(tzinfo=timezone.utc).astimezone(IST).date()
        if t_ist_date == today_date:
            trades_today.append(t)

    trades_today.sort(key=lambda t: t.traded_at)

    # --- underlying name extraction (ASTRAL) ---
    def _underlying_from_trade(t) -> str:
        ins = store.get_instrument(int(t.instrument_id))
        if ins:
            nm = (ins.get("name") or "").strip()
            if nm:
                return nm.upper()

            ts = (ins.get("tradingsymbol") or "").strip().upper()
            if ts:
                m = re.match(r"^([A-Z]+)", ts)
                return (m.group(1) if m else ts)

        raw = (t.symbol or "").split(":")[-1].strip().upper()
        m = re.match(r"^([A-Z]+)", raw)
        return (m.group(1) if m else raw)

    seen = set()
    uniq_underlyings = []
    for t in trades_today:
        u = _underlying_from_trade(t)
        if u and u not in seen:
            uniq_underlyings.append(u)
            seen.add(u)

    shared_symbols = ", ".join(uniq_underlyings)  # e.g. "ASTRAL"

    # --- ✅ FIX: money used counts ONLY opening / increase (exit not counted) ---
    def _sgn(x: int) -> int:
        return 0 if x == 0 else (1 if x > 0 else -1)

    net_by_iid = defaultdict(int)
    shared_money_used = 0.0

    for t in trades_today:
        qty = int(t.qty)
        px = float(t.price)
        side_u = (t.side or "").upper().strip()
        signed = qty if side_u == "BUY" else -qty

        iid = int(t.instrument_id)
        net = int(net_by_iid[iid])
        new_net = net + signed

        # opening_qty = only the part that OPENS / INCREASES exposure
        if net == 0:
            opening_qty = abs(signed)
        elif _sgn(net) == _sgn(new_net):
            opening_qty = max(0, abs(new_net) - abs(net))
        else:
            # crossed zero: closing old + opening opposite
            opening_qty = abs(new_net)

        shared_money_used += float(opening_qty) * px
        net_by_iid[iid] = new_net

    try:
        db.upsert_daily_pnl(day_iso, net_liq)  # ensure row exists
        db.set_shared_snapshot(day_iso, realized, shared_symbols, shared_money_used)
    except Exception as e:
        raise HTTPException(503, f"DB error: {e}")

    return {
        "ok": True,
        "day": day_iso,
        "realized": realized,
        "net_liq": float(net_liq),
        "shared_symbols": shared_symbols,
        "shared_money_used": float(shared_money_used),
    }


@app.post("/api/daily-pnl/reset")
def api_daily_pnl_reset(admin_token: str | None = Form(None)):
    need = os.environ.get("ADMIN_TOKEN", "").strip()
    if not need:
        raise HTTPException(403, "ADMIN_TOKEN not configured on server")
    if admin_token != need:
        raise HTTPException(401, "Invalid admin token")

    try:
        db.reset_daily_pnl()
        return {"ok": True, "message": "Daily PnL DB cleared"}
    except Exception as e:
        raise HTTPException(503, f"DB error: {e}")


@app.post("/api/reset")
def api_reset():
    store.reset_demo()
    return {"ok": True, "message": "Demo reset to ₹10,00,000"}