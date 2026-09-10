import os
import re
import time
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")


def _env_bool(key: str, default: str = "1") -> bool:
    v = os.getenv(key, default).strip().lower()
    return v in ("1", "true", "yes", "y", "on")


# If enabled, and user did NOT type an expiry explicitly, options results will be restricted
# to a month window (current month + next month by default).
LIMIT_OPTION_EXPIRIES_TO_MONTH_WINDOW = _env_bool("LIMIT_OPTION_EXPIRIES_TO_MONTH_WINDOW", "1")

# How many months ahead to include (1 => current + next month)
OPTION_MONTHS_AHEAD = int(os.getenv("OPTION_MONTHS_AHEAD", "1"))

# Cache ignored if older than this (seconds)
INSTRUMENTS_CACHE_MAX_AGE_SEC = int(os.getenv("INSTRUMENTS_CACHE_MAX_AGE_SEC", "21600"))  # 6 hours

# How many strikes around target strike (3 => 7 strikes total)
NEAREST_STRIKES = int(os.getenv("NEAREST_STRIKES", "3"))

# Safety cap
SEARCH_LIMIT_MAX = int(os.getenv("SEARCH_LIMIT_MAX", "500"))


# =========================
# Demo account + records
# =========================
@dataclass
class DemoAccount:
    cash: float = 1000000.0
    realized_pnl: float = 0.0


@dataclass
class PositionRec:
    instrument_id: int
    exchange: str
    tradingsymbol: str
    lot_size: int
    net_qty: int = 0
    avg_price: float = 0.0
    realized_pnl: float = 0.0


@dataclass
class OrderRec:
    id: int
    instrument_id: int
    symbol: str
    side: str
    lots: int
    qty: int
    price: float
    status: str
    created_at: datetime


@dataclass
class TradeRec:
    id: int
    order_id: int
    instrument_id: int
    symbol: str
    side: str
    qty: int
    price: float
    traded_at: datetime


# =========================
# In-memory store
# =========================
class InMemoryStore:
    """
    Demo in-memory store.

    Options behavior (NFO CE/PE):
      - Default window: current month + next month only (configurable)
      - "ICICI 1660" => nearest strikes around 1660 for ALL expiries in the window
      - "ICICI" => all options for expiries in the window (limited by `limit`)
      - If user types explicit expiry text (e.g. 26AUG), we DO NOT apply the month-window filter.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.account = DemoAccount()

        self._next_oid = 1
        self._next_tid = 1

        self.positions: Dict[int, PositionRec] = {}
        self.orders: List[OrderRec] = []
        self.trades: List[TradeRec] = []

        self.instruments_df: Optional[pd.DataFrame] = None

        # underlying name_u -> dataframe index for NFO CE/PE options
        self._nfo_opt_idx: Dict[str, pd.Index] = {}

    # ---------------------------
    # Instruments load + indexing
    # ---------------------------
    def load_instruments(self, kite, exchanges=("NSE", "NFO")) -> None:
        """
        Loads instruments into memory from Kite instruments dump.
        Optional local cache (CSV gzip) to speed up restarts.
        Cache is ignored if older than INSTRUMENTS_CACHE_MAX_AGE_SEC.
        """
        cache_path = os.getenv("INSTRUMENTS_CACHE_PATH", "./instruments_cache.csv.gz")

        use_cache = False
        if os.path.exists(cache_path):
            try:
                age = time.time() - os.path.getmtime(cache_path)
                use_cache = (age <= INSTRUMENTS_CACHE_MAX_AGE_SEC)
            except Exception:
                use_cache = False

        if use_cache:
            df = pd.read_csv(cache_path)

            if "instrument_id" not in df.columns:
                df = df.reset_index(drop=True)
                df["instrument_id"] = df.index.astype(int) + 1

            for c, default in [
                ("exchange", ""),
                ("tradingsymbol", ""),
                ("name", ""),
                ("instrument_type", ""),
                ("instrument_token", 0),
                ("lot_size", 1),
                ("strike", None),
                ("expiry", ""),
            ]:
                if c not in df.columns:
                    df[c] = default

            df["exchange"] = df["exchange"].fillna("").astype(str)
            df["tradingsymbol"] = df["tradingsymbol"].fillna("").astype(str)
            df["name"] = df["name"].fillna("").astype(str)
            df["instrument_type"] = df["instrument_type"].fillna("").astype(str)

            df["instrument_token"] = pd.to_numeric(df["instrument_token"], errors="coerce").fillna(0).astype(int)
            df["lot_size"] = pd.to_numeric(df["lot_size"], errors="coerce").fillna(1).astype(int)
            df["strike"] = pd.to_numeric(df["strike"], errors="coerce")

            df["ts_u"] = df["tradingsymbol"].str.upper()
            df["name_u"] = df["name"].str.upper()
            df["expiry_dt"] = pd.to_datetime(df.get("expiry", None), errors="coerce")

            self.instruments_df = df
            self._build_nfo_index()
            return

        # Download from Kite
        frames: List[pd.DataFrame] = []
        for exch in exchanges:
            rows = kite.instruments(exch)
            df = pd.DataFrame(rows)
            keep = [
                "exchange", "tradingsymbol", "name", "instrument_token",
                "segment", "instrument_type", "expiry", "strike",
                "lot_size", "tick_size",
            ]
            df = df[[c for c in keep if c in df.columns]].copy()
            frames.append(df)

        df = pd.concat(frames, ignore_index=True)

        for c, default in [
            ("exchange", ""),
            ("tradingsymbol", ""),
            ("name", ""),
            ("instrument_type", ""),
            ("instrument_token", 0),
            ("lot_size", 1),
            ("strike", None),
            ("expiry", ""),
        ]:
            if c not in df.columns:
                df[c] = default

        df["exchange"] = df["exchange"].fillna("").astype(str)
        df["tradingsymbol"] = df["tradingsymbol"].fillna("").astype(str)
        df["name"] = df["name"].fillna("").astype(str)
        df["instrument_type"] = df["instrument_type"].fillna("").astype(str)

        df["instrument_token"] = pd.to_numeric(df["instrument_token"], errors="coerce").fillna(0).astype(int)
        df["lot_size"] = pd.to_numeric(df["lot_size"], errors="coerce").fillna(1).astype(int)
        df["strike"] = pd.to_numeric(df["strike"], errors="coerce")

        df["expiry_dt"] = pd.to_datetime(df.get("expiry", None), errors="coerce")
        df["expiry"] = df["expiry_dt"].dt.date.astype("string").fillna("")

        df = df.reset_index(drop=True)
        df["instrument_id"] = df.index.astype(int) + 1

        df["ts_u"] = df["tradingsymbol"].str.upper()
        df["name_u"] = df["name"].str.upper()

        self.instruments_df = df
        self._build_nfo_index()

        try:
            cache_df = df.drop(columns=["ts_u", "name_u", "expiry_dt"], errors="ignore")
            cache_df.to_csv(cache_path, index=False)
        except Exception:
            pass

    def _build_nfo_index(self) -> None:
        df = self.instruments_df
        self._nfo_opt_idx = {}
        if df is None:
            return

        nfo = df[(df["exchange"] == "NFO") & (df["instrument_type"].isin(["CE", "PE"]))].copy()
        if nfo.empty:
            return

        for under, grp in nfo.groupby("name_u"):
            self._nfo_opt_idx[str(under)] = grp.index

    # ---------------------------
    # Instrument access
    # ---------------------------
    def get_instrument(self, instrument_id: int) -> Optional[dict]:
        df = self.instruments_df
        if df is None:
            return None
        row = df.loc[df["instrument_id"] == int(instrument_id)]
        if row.empty:
            return None
        return row.iloc[0].to_dict()

    # ---------------------------
    # Search helpers
    # ---------------------------
    def _parse_query(self, q: str) -> dict:
        raw = (q or "").strip()
        u = raw.upper().strip()

        exch_hint = None
        if u.startswith("NFO:"):
            exch_hint = "NFO"
            u = u[4:]
        elif u.startswith("NSE:"):
            exch_hint = "NSE"
            u = u[4:]

        m = re.match(r"^([A-Z]+)(\d{1,2}[A-Z]{3}\d{0,2})(\d+(?:\.\d+)?)(CE|PE)$", u.replace(" ", ""))
        exact_symbol = None
        underlying = None
        strike = None
        opt_type = None
        expiry_text = None

        if m:
            underlying = m.group(1)
            expiry_text = m.group(2)
            strike = float(m.group(3))
            opt_type = m.group(4)
            strike_str = str(int(strike)) if float(strike).is_integer() else str(strike)
            exact_symbol = f"{underlying}{expiry_text}{strike_str}{opt_type}"

        parts = re.split(r"[\s,;:/\-_]+", u)
        parts = [p for p in parts if p]

        for p in parts:
            if p in ("CE", "PE"):
                opt_type = p

        for p in parts:
            if re.fullmatch(r"\d+(\.\d+)?", p):
                strike = float(p)

        for p in parts:
            if re.fullmatch(r"\d{1,2}[A-Z]{3}\d{0,2}", p):
                expiry_text = p
                break

        alpha = [p for p in parts if re.fullmatch(r"[A-Z]{3,}", p)]
        if alpha and not underlying:
            underlying = max(alpha, key=len)

        return {
            "raw": raw,
            "q_u": u,
            "exch_hint": exch_hint,
            "underlying": underlying,
            "strike": strike,
            "opt_type": opt_type,
            "expiry_text": expiry_text,
            "exact_symbol": exact_symbol,
        }

    def _allowed_year_months(self, today: datetime.date) -> set[tuple[int, int]]:
        """
        Returns {(year, month)} for current month .. current+OPTION_MONTHS_AHEAD.
        Handles year rollover (Dec -> Jan).
        """
        out: set[tuple[int, int]] = set()
        y, m = today.year, today.month
        for i in range(max(0, int(OPTION_MONTHS_AHEAD)) + 1):
            mm = m + i
            yy = y + (mm - 1) // 12
            mo = ((mm - 1) % 12) + 1
            out.add((yy, mo))
        return out

    def _apply_month_window_filter(self, base: pd.DataFrame, *, expiry_text: Optional[str]) -> pd.DataFrame:
        """
        - Always removes expired contracts (expiry < today IST)
        - If month-window enabled and user did NOT type expiry_text, keep only current+next month
        """
        if base.empty or "expiry_dt" not in base.columns:
            return base

        today = datetime.now(IST).date()

        base = base[pd.notna(base["expiry_dt"])].copy()
        if base.empty:
            return base

        # remove expired (strictly before today)
        base = base[base["expiry_dt"].dt.date >= today].copy()
        if base.empty:
            return base

        if LIMIT_OPTION_EXPIRIES_TO_MONTH_WINDOW and not expiry_text:
            allowed = self._allowed_year_months(today)
            base = base[
                base["expiry_dt"].dt.year.astype(int).combine(
                    base["expiry_dt"].dt.month.astype(int),
                    lambda yy, mo: (yy, mo)
                ).isin(allowed)
            ].copy()

        return base

    def _options_base_for_underlying(self, underlying: str) -> pd.DataFrame:
        df = self.instruments_df
        if df is None:
            return pd.DataFrame()

        under_u = underlying.upper()

        idx = None
        if under_u in self._nfo_opt_idx:
            idx = self._nfo_opt_idx[under_u]
        else:
            keys = [k for k in self._nfo_opt_idx.keys() if under_u in k]
            if keys:
                keys.sort(key=len)
                idx = self._nfo_opt_idx[keys[0]]

        if idx is None:
            base = df[
                (df["exchange"] == "NFO") &
                (df["instrument_type"].isin(["CE", "PE"])) &
                (df["name_u"].str.contains(under_u, na=False))
            ].copy()
        else:
            base = df.loc[idx].copy()

        return base[(base["exchange"] == "NFO") & (base["instrument_type"].isin(["CE", "PE"]))].copy()

    # ---------------------------
    # Search
    # ---------------------------
    def search_instruments(self, q: str, limit: int = 200) -> List[dict]:
        df = self.instruments_df
        if df is None:
            return []

        limit = max(1, min(int(limit), SEARCH_LIMIT_MAX))

        info = self._parse_query(q)
        q_u = info["q_u"]
        if len(q_u) < 2:
            return []

        # 1) exact symbol match
        if info["exact_symbol"]:
            sym = info["exact_symbol"]
            if info["exch_hint"] in ("NFO", "NSE"):
                exact = df[(df["exchange"] == info["exch_hint"]) & (df["ts_u"] == sym)]
            else:
                exact = df[(df["exchange"] == "NFO") & (df["ts_u"] == sym)]
            if not exact.empty:
                return self._rows_out(exact.head(limit))

        # 2) underlying + strike => nearest strikes around strike across all expiries in window
        if info["underlying"] and info["strike"] is not None:
            return self._search_options_near_strike(
                underlying=str(info["underlying"]),
                strike=float(info["strike"]),
                opt_type=info["opt_type"],
                expiry_text=info["expiry_text"],
                limit=limit,
            )

        # 3) underlying only => all options in window (limited)
        if info["underlying"] and info["strike"] is None:
            return self._search_options_window_all(
                underlying=str(info["underlying"]),
                opt_type=info["opt_type"],
                expiry_text=info["expiry_text"],
                limit=limit,
            )

        # 4) fallback global search
        m = df["ts_u"].str.contains(q_u, na=False) | df["name_u"].str.contains(q_u, na=False)
        out = df.loc[m].copy()

        if info["exch_hint"] in ("NSE", "NFO"):
            out = out[out["exchange"] == info["exch_hint"]]

        if info["opt_type"] in ("CE", "PE"):
            out = out[out["instrument_type"] == info["opt_type"]]

        # Apply month window to option rows when browsing without explicit expiry
        if LIMIT_OPTION_EXPIRIES_TO_MONTH_WINDOW and info["expiry_text"] is None:
            nfo_opts = out[(out["exchange"] == "NFO") & (out["instrument_type"].isin(["CE", "PE"]))].copy()
            if not nfo_opts.empty:
                nfo_opts = self._apply_month_window_filter(nfo_opts, expiry_text=None)
                non_opts = out[~((out["exchange"] == "NFO") & (out["instrument_type"].isin(["CE", "PE"])))]
                out = pd.concat([non_opts, nfo_opts], ignore_index=True)

        out = out.sort_values(by=["exchange", "tradingsymbol"], ascending=[True, True])
        return self._rows_out(out.head(limit))

    def _search_options_window_all(
        self,
        *,
        underlying: str,
        opt_type: Optional[str],
        expiry_text: Optional[str],
        limit: int
    ) -> List[dict]:
        base = self._options_base_for_underlying(underlying)
        if base.empty:
            return []

        if opt_type in ("CE", "PE"):
            base = base[base["instrument_type"] == opt_type].copy()

        if expiry_text:
            base = base[base["ts_u"].str.contains(expiry_text.upper(), na=False)].copy()

        base = self._apply_month_window_filter(base, expiry_text=expiry_text)
        if base.empty:
            return []

        base = base[pd.notna(base["strike"])].copy()
        if base.empty:
            return []

        base = base.sort_values(
            by=["expiry_dt", "strike", "instrument_type"],
            ascending=[True, True, True],
        )

        return self._rows_out(base.head(limit))

    def _search_options_near_strike(
        self,
        *,
        underlying: str,
        strike: float,
        opt_type: Optional[str],
        expiry_text: Optional[str],
        limit: int
    ) -> List[dict]:
        base = self._options_base_for_underlying(underlying)
        if base.empty:
            return []

        if opt_type in ("CE", "PE"):
            base = base[base["instrument_type"] == opt_type].copy()

        if expiry_text:
            base = base[base["ts_u"].str.contains(expiry_text.upper(), na=False)].copy()

        # apply: remove expired + keep current+next month (unless explicit expiry typed)
        base = self._apply_month_window_filter(base, expiry_text=expiry_text)
        if base.empty:
            return []

        base = base[pd.notna(base["strike"])].copy()
        if base.empty:
            return []

        base["diff"] = (base["strike"].astype(float) - float(strike)).abs()

        want_unique = max(1, (2 * int(NEAREST_STRIKES) + 1))

        # nearest unique strikes (picked using earliest expiry to define "nearest")
        nearest = (
            base.sort_values(["diff", "expiry_dt"], ascending=[True, True])
                .drop_duplicates(subset=["strike"])
                .head(want_unique)
        )
        strikes = nearest["strike"].astype(float).tolist()

        # IMPORTANT: return ALL expiries (within month window) for these strikes
        out = base[base["strike"].astype(float).isin(strikes)].copy()
        out["diff2"] = (out["strike"].astype(float) - float(strike)).abs()

        out = out.sort_values(
            by=["diff2", "expiry_dt", "strike", "instrument_type"],
            ascending=[True, True, True, True],
        )

        return self._rows_out(out.head(limit))

    def _rows_out(self, out_df: pd.DataFrame) -> List[dict]:
        cols = [
            "instrument_id", "exchange", "tradingsymbol", "name",
            "instrument_token", "instrument_type", "lot_size", "strike", "expiry"
        ]
        cols = [c for c in cols if c in out_df.columns]
        rows = out_df[cols].to_dict("records")

        for r in rows:
            r["instrument_id"] = int(r.get("instrument_id") or 0)
            r["instrument_token"] = int(r.get("instrument_token") or 0)
            r["lot_size"] = int(r.get("lot_size") or 1)

            strike = r.get("strike", None)
            if strike is None or (isinstance(strike, float) and pd.isna(strike)):
                r["strike"] = None
            else:
                try:
                    r["strike"] = float(strike)
                except Exception:
                    r["strike"] = None

            exp = r.get("expiry", "")
            r["expiry"] = "" if exp is None or (isinstance(exp, float) and pd.isna(exp)) else str(exp)

        return rows

    # ---------------------------
    # Paper execution (market fill at LTP)
    # ---------------------------
    def _sgn(self, x: int) -> int:
        return 0 if x == 0 else (1 if x > 0 else -1)

    def place_paper_order(
        self,
        *,
        instrument: dict,
        side: str,
        lots: int,
        fill_price: float
    ) -> Tuple[bool, str, Optional[int]]:
        side = (side or "").upper().strip()
        if side not in ("BUY", "SELL"):
            return False, "Invalid side", None
        if lots <= 0:
            return False, "Lots must be > 0", None
        if fill_price <= 0:
            return False, "Invalid fill price", None

        lot_size = int(instrument.get("lot_size") or 1)
        qty = int(lots) * lot_size
        if qty <= 0:
            return False, "Invalid quantity", None

        iid = int(instrument["instrument_id"])
        symbol = f"{instrument['exchange']}:{instrument['tradingsymbol']}"
        notional = float(qty) * float(fill_price)

        with self.lock:
            acct = self.account

            if side == "BUY" and acct.cash < notional:
                return False, f"Insufficient cash. Need ₹{notional:,.2f}", None

            signed_qty = +qty if side == "BUY" else -qty
            acct.cash += (-notional if side == "BUY" else +notional)

            oid = self._next_oid
            self._next_oid += 1
            self.orders.insert(0, OrderRec(
                id=oid,
                instrument_id=iid,
                symbol=symbol,
                side=side,
                lots=int(lots),
                qty=int(qty),
                price=float(fill_price),
                status="FILLED",
                created_at=datetime.utcnow(),
            ))

            tid = self._next_tid
            self._next_tid += 1
            self.trades.insert(0, TradeRec(
                id=tid,
                order_id=oid,
                instrument_id=iid,
                symbol=symbol,
                side=side,
                qty=int(qty),
                price=float(fill_price),
                traded_at=datetime.utcnow(),
            ))

            p = self.positions.get(iid)
            if not p:
                p = PositionRec(
                    instrument_id=iid,
                    exchange=instrument["exchange"],
                    tradingsymbol=instrument["tradingsymbol"],
                    lot_size=lot_size,
                )
                self.positions[iid] = p

            net = int(p.net_qty)
            avg = float(p.avg_price)

            if net == 0 or self._sgn(net) == self._sgn(signed_qty):
                new_net = net + signed_qty
                new_avg = (abs(net) * avg + abs(signed_qty) * fill_price) / (abs(new_net) + 1e-9)
                p.net_qty = int(new_net)
                p.avg_price = float(new_avg)
            else:
                closing_qty = min(abs(net), abs(signed_qty))
                pnl = (fill_price - avg) * closing_qty * self._sgn(net)
                p.realized_pnl += float(pnl)
                acct.realized_pnl += float(pnl)

                new_net = net + signed_qty
                p.net_qty = int(new_net)
                p.avg_price = 0.0 if new_net == 0 else float(fill_price)

            return True, "FILLED", oid

    def reset_demo(self) -> None:
        with self.lock:
            self.account = DemoAccount()
            self.positions.clear()
            self.orders.clear()
            self.trades.clear()
            self._next_oid = 1
            self._next_tid = 1


store = InMemoryStore()