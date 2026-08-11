# app/db.py
import os
import psycopg2
from psycopg2.extras import RealDictCursor


def db_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL env var is missing")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def init_db() -> None:
    """
    Create/upgrade table.
    """
    with psycopg2.connect(db_url(), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS daily_pnl (
                  day date PRIMARY KEY,
                  opening_net_liq double precision NOT NULL,
                  last_net_liq double precision NOT NULL,
                  pnl double precision NOT NULL,
                  updated_at timestamptz NOT NULL DEFAULT now(),
                  shared_realized_pnl double precision,
                  shared_at timestamptz
                );
            """)

            # ✅ NEW: charges per day (safe upgrade)
            cur.execute(
                "ALTER TABLE daily_pnl "
                "ADD COLUMN IF NOT EXISTS day_charges double precision NOT NULL DEFAULT 0;"
            )

            # keep these safe upgrades too (if you already had them, no harm)
            cur.execute("ALTER TABLE daily_pnl ADD COLUMN IF NOT EXISTS shared_realized_pnl double precision;")
            cur.execute("ALTER TABLE daily_pnl ADD COLUMN IF NOT EXISTS shared_at timestamptz;")


def upsert_daily_pnl(day_iso: str, net_liq: float) -> None:
    sql = """
    INSERT INTO daily_pnl(day, opening_net_liq, last_net_liq, pnl)
    VALUES (%s::date, %s, %s, 0)
    ON CONFLICT (day) DO UPDATE
    SET last_net_liq = EXCLUDED.last_net_liq,
        pnl = EXCLUDED.last_net_liq - daily_pnl.opening_net_liq,
        updated_at = now();
    """
    with psycopg2.connect(db_url(), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (day_iso, float(net_liq), float(net_liq)))


def set_shared_realized(day_iso: str, realized: float) -> None:
    sql = """
    UPDATE daily_pnl
    SET shared_realized_pnl = %s,
        shared_at = now()
    WHERE day = %s::date;
    """
    with psycopg2.connect(db_url(), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (float(realized), day_iso))


def get_daily_pnl(limit: int = 3650):
    sql = """
    SELECT
      day::text as day,
      opening_net_liq,
      last_net_liq,
      pnl,
      day_charges,
      updated_at,
      shared_realized_pnl,
      shared_at
    FROM daily_pnl
    ORDER BY day DESC
    LIMIT %s;
    """
    with psycopg2.connect(db_url(), connect_timeout=5) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (int(limit),))
            return cur.fetchall()


def reset_daily_pnl() -> None:
    with psycopg2.connect(db_url(), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE daily_pnl;")