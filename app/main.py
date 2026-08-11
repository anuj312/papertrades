@app.get("/api/daily-pnl")
def api_daily_pnl():
    try:
        rows = db.get_daily_pnl(3650)

        total_pnl_sum = sum(float(r.get("pnl") or 0.0) for r in rows) if rows else 0.0

        overall_change = 0.0
        if rows:
            newest = rows[0]    # latest day
            oldest = rows[-1]   # earliest day
            overall_change = float(newest["last_net_liq"]) - float(oldest["opening_net_liq"])

        today = datetime.now(IST).date().isoformat()
        today_row = next((r for r in rows if r["day"] == today), None)

        # ✅ NEW totals
        total_shared_sum = sum(
            float(r["shared_realized_pnl"])
            for r in rows
            if r.get("shared_realized_pnl") is not None
        )
        total_charges_sum = sum(float(r.get("day_charges") or 0.0) for r in rows)

        return {
            "ok": True,
            "days": rows,
            "count": len(rows),
            "today": today,
            "today_pnl": float(today_row["pnl"]) if today_row else 0.0,
            "total_pnl_sum": float(total_pnl_sum),
            "overall_change": float(overall_change),

            # ✅ NEW
            "total_shared_sum": float(total_shared_sum),
            "total_charges_sum": float(total_charges_sum),
        }
    except Exception as e:
        raise HTTPException(503, f"DB error: {e}")