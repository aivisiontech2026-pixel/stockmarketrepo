"""
Combined end-of-day Telegram report for the intraday stock + options bots.

Invoked every cron tick alongside the two traders, but only actually
sends once per day: it no-ops until 15:30 IST, then sends the first time
it runs at or after that, guarded by a meta flag in intraday_trades.db so
later ticks in the same day don't resend it.

Sends a text summary (both bots' closed trades + combined P&L vs the
daily target) and the same report as an attached .txt document.
"""

from datetime import date, datetime
from pathlib import Path

import intraday_trader as stocks
import options_trader as opts

HERE = Path(__file__).parent
CFG = stocks.CFG
EOD_TIME = 15 * 60 + 30  # 15:30 IST

# ------------------------------------------------------------------ alerts ---
def telegram_text(msg):
    tg = CFG.get("telegram", {})
    if not (tg.get("bot_token") and tg.get("chat_id")):
        return
    import requests
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage",
            json={"chat_id": tg["chat_id"], "text": msg}, timeout=10)
        if not r.ok:
            print(f"  (telegram text rejected: {r.status_code} {r.text})")
    except Exception as e:
        print(f"  (telegram text failed: {e})")

def telegram_document(filename, content, caption=None):
    tg = CFG.get("telegram", {})
    if not (tg.get("bot_token") and tg.get("chat_id")):
        return
    import requests
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{tg['bot_token']}/sendDocument",
            data={"chat_id": tg["chat_id"], "caption": caption or ""},
            files={"document": (filename, content.encode())},
            timeout=20)
        if not r.ok:
            print(f"  (telegram document rejected: {r.status_code} {r.text})")
    except Exception as e:
        print(f"  (telegram document failed: {e})")

# ------------------------------------------------------------------ report ---
def build_report(today):
    conn_s = stocks.db()
    conn_o = opts.db_init()

    stock_rows = conn_s.execute(
        "SELECT symbol,qty,entry,exit_px,pnl,reason FROM closed_trades "
        "WHERE exit_time >= ?", (today.isoformat(),)).fetchall()
    opt_rows = conn_o.execute(
        "SELECT symbol,option_type,strike,qty,entry_price,exit_price,pnl,reason "
        "FROM options_trades WHERE exit_time >= ?", (today.isoformat(),)).fetchall()

    lines = [f"EOD REPORT {today} (15:30 IST)", ""]

    lines.append("=== STOCKS ===")
    if stock_rows:
        for sym, qty, entry, exit_px, pnl, reason in stock_rows:
            lines.append(f"  {sym} x{qty} {entry:.2f}->{exit_px:.2f} "
                        f"Rs.{pnl:,.0f} ({reason})")
    else:
        lines.append("  no trades closed today")
    stock_total = sum(r[4] for r in stock_rows)
    lines.append(f"  Stocks total: Rs.{stock_total:,.0f} | Cash: Rs.{stocks.cash(conn_s):,.0f}")

    lines.append("")
    lines.append("=== OPTIONS (NIFTY/BANKNIFTY) ===")
    if opt_rows:
        for sym, opt_type, strike, qty, entry, exit_px, pnl, reason in opt_rows:
            lines.append(f"  {sym} {opt_type} {strike} x{qty} {entry:.2f}->{exit_px:.2f} "
                        f"Rs.{pnl:,.0f} ({reason})")
    else:
        lines.append("  no trades closed today")
    opt_total = sum(r[6] for r in opt_rows)
    lines.append(f"  Options total: Rs.{opt_total:,.0f} | Cash: Rs.{opts.cash(conn_o):,.0f}")

    combined = stock_total + opt_total
    target = stocks.DAILY_PROFIT_TARGET
    status = "TARGET MET" if combined >= target else "target not reached"
    lines.append("")
    lines.append("=== TOTAL ===")
    lines.append(f"Day P&L: Rs.{combined:,.0f} | Target Rs.{target:,.0f} ({status})")

    conn_s.close()
    conn_o.close()
    return "\n".join(lines)

# -------------------------------------------------------------------- main ---
def main():
    today = date.today()
    now = datetime.now().astimezone()
    now_min = now.hour * 60 + now.minute

    if now_min < EOD_TIME:
        print(f"[{now:%H:%M}] EOD report not due yet (fires at 15:30 IST).")
        return

    conn_s = stocks.db()
    if stocks.meta_get(conn_s, f"eod_final_sent:{today}"):
        print(f"[{now:%H:%M}] EOD report already sent today.")
        conn_s.close()
        return

    report = build_report(today)
    print(report)
    telegram_text(report)
    telegram_document(f"eod_report_{today}.txt", report, caption=f"EOD log {today}")

    stocks.meta_set(conn_s, f"eod_final_sent:{today}", "1")
    conn_s.commit()
    conn_s.close()

if __name__ == "__main__":
    main()
