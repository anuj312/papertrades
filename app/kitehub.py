import os
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Tuple

from kiteconnect import KiteConnect, KiteTicker

IST = ZoneInfo("Asia/Kolkata")


def market_is_open_ist(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(IST)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(9, 15) <= t <= dtime(15, 30)


class KiteHub:
    """
    Market-data-only helper.
    - Uses websocket LTP cache when available
    - Falls back to REST kite.ltp()

    IMPORTANT: No order placement functions exist here.
    """
    def __init__(self):
        api_key = os.getenv("KITE_API_KEY", "").strip()
        access_token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
        if not api_key or not access_token:
            raise RuntimeError("Missing KITE_API_KEY / KITE_ACCESS_TOKEN env vars")

        self.api_key = api_key
        self.access_token = access_token

        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)

        self._prices: Dict[int, Tuple[float, float]] = {}  # token -> (ltp, epoch)
        self._subscribed: set[int] = set()
        self._kws: Optional[KiteTicker] = None
        self._ws_started = False

    def start_ws(self) -> None:
        if self._ws_started:
            return
        self._ws_started = True

        kws = KiteTicker(self.api_key, self.access_token)
        self._kws = kws

        def on_connect(ws, _resp):
            toks = list(self._subscribed)
            if toks:
                ws.subscribe(toks)
                ws.set_mode(ws.MODE_LTP, toks)

        def on_ticks(_ws, ticks):
            now = time.time()
            for t in ticks:
                tok = t.get("instrument_token")
                ltp = t.get("last_price")
                if tok and ltp is not None:
                    self._prices[int(tok)] = (float(ltp), now)

        def on_close(_ws, *_a):
            # Keeping it simple; UI REST fallback will still work.
            pass

        def on_error(_ws, *_a):
            pass

        kws.on_connect = on_connect
        kws.on_ticks = on_ticks
        kws.on_close = on_close
        kws.on_error = on_error

        kws.connect(threaded=True)

    def ensure_subscribed(self, token: int) -> None:
        token = int(token)
        if token <= 0:
            return
        self._subscribed.add(token)
        if self._kws is not None:
            try:
                self._kws.subscribe([token])
                self._kws.set_mode(self._kws.MODE_LTP, [token])
            except Exception:
                pass

    def ltp(self, exchange: str, tradingsymbol: str, token: Optional[int] = None) -> Optional[float]:
        # Prefer websocket cache if fresh
        if token is not None:
            c = self._prices.get(int(token))
            if c and (time.time() - c[1]) <= 5.0:
                return float(c[0])

        # REST fallback
        try:
            key = f"{exchange}:{tradingsymbol}"
            q = self.kite.ltp([key])
            return float(q[key]["last_price"])
        except Exception:
            return None


kitehub = KiteHub()