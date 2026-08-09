# app/main.py
import os
from datetime import datetime

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from zoneinfo import ZoneInfo
from . import db

from .kitehub import kitehub, market_is_open_ist
from .store import store

app = FastAPI(title="Paper Trading (Demo Mode)")
IST = ZoneInfo("Asia/Kolkata")

BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.on_event("startup")
def startup():
    # init db (don’t crash app if db temporarily down)
    try:
        db.init_db()
    except Exception as e:
        print("DB init failed:", e)

    store.load_instruments(kitehub.kite, exchanges=("NSE", "NFO"))
    try:
        kitehub.start_ws()
    except Exception:
        pass
    
@app.get("/daily-pnl", response_class=HTMLResponse)
def daily_pnl_page(request: Request):
    return templates.TemplateResponse("daily_pnl.html", {
        "request": request,
        "market_open": market_is_open_ist(),
    })
    
@app.post("/api/daily-pnl/share")
def api_daily_pnl_share():
    acct = store.account

    # compute unrealized quickly (same logic as dashboard)
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
    day_iso = datetime.now(IST).date().isoformat()
    realized = float(acct.realized_pnl)

    try:
        # ensure today's row exists + updated netliq
        db.upsert_daily_pnl(day_iso, net_liq)
        # save realized snapshot
        db.set_shared_realized(day_iso, realized)
    except Exception as e:
        raise HTTPException(503, f"DB error: {e}")

    return {"ok": True, "day": day_iso, "realized": realized, "net_liq": float(net_liq)}    


@app.get("/api/daily-pnl")
def api_daily_pnl():
    try:
        rows = db.get_daily_pnl(3650)  # ~10 years
        total_pnl_sum = sum(float(r["pnl"]) for r in rows) if rows else 0.0

        # overall change from first opening to latest last (best “total”)
        overall_change = 0.0
        if rows:
            newest = rows[0]
            oldest = rows[-1]
            overall_change = float(newest["last_net_liq"]) - float(oldest["opening_net_liq"])

        return {
            "ok": True,
            "days": rows,
            "total_pnl_sum": float(total_pnl_sum),
            "overall_change": float(overall_change),
            "count": len(rows),
        }
    except Exception as e:
        raise HTTPException(503, f"DB error: {e}")     


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


# Make /api/order tolerant (avoids 422 -> gives clear 400)
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


# ✅ NEW: exit full position server-side (no JS lots calc)
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

    # ensure whole lots
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

    # ✅ DEFINE net_liq HERE (this fixes your NameError)
    net_liq = float(acct.cash) + float(unrealized)

    # ✅ Save today's netliq to Neon (don’t crash dashboard if DB is down)
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


@app.post("/api/reset")
def api_reset():
    store.reset_demo()
    return {"ok": True, "message": "Demo reset to ₹10,00,000"}