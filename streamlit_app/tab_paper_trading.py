"""
streamlit_app/tab_paper_trading.py — Paper Trading Dashboard Tab
Render with: render_paper_trading_tab()
"""

import json
import time
import subprocess
import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

TRADES_FILE = Path("paper_trading/trades.json")
STATE_FILE  = Path("paper_trading/state.json")
INIT_CAP    = 10000.0


def _load(path: Path, default):
    if path.exists():
        try: return json.loads(path.read_text())
        except: pass
    return default

def _color(val: float, fmt: str = "+.2f") -> str:
    color = "green" if val >= 0 else "red"
    return f'<span style="color:{color};font-weight:500">{val:{fmt}}</span>'


def render_paper_trading_tab():
    st.markdown("## 📄 Paper Trading")
    st.caption("Simulated 24/7 trading using Trinity.AI signals + real Binance prices. No real money.")

    state  = _load(STATE_FILE,  {})
    trades = _load(TRADES_FILE, [])

    # ── Auto-refresh every 30s ────────────────────────────────────
    if "paper_last_refresh" not in st.session_state:
        st.session_state.paper_last_refresh = 0
    if time.time() - st.session_state.paper_last_refresh > 30:
        st.session_state.paper_last_refresh = time.time()
        st.rerun()

    col_r, _ = st.columns([1, 3])
    with col_r:
        if st.button("🔄 Refresh", use_container_width=True, key="paper_refresh"):
            st.rerun()

    # ── Account summary ───────────────────────────────────────────
    account    = float(state.get("account_value",      INIT_CAP))
    peak       = float(state.get("peak_value",         INIT_CAP))
    daily_start= float(state.get("daily_start_value",  INIT_CAP))
    total_ret  = (account - INIT_CAP) / INIT_CAP
    daily_ret  = (account - daily_start) / max(daily_start, 1)
    drawdown   = (peak - account) / max(peak, 1)
    total_t    = int(state.get("total_trades", 0))
    wins       = int(state.get("winning_trades", 0))
    wr         = wins / max(total_t, 1)
    cons_loss  = int(state.get("consecutive_losses", 0))
    cooldown   = state.get("cooldown_until", "")

    # Account card
    ret_color = "#00c853" if total_ret >= 0 else "#d50000"
    st.markdown(f"""
    <div style="background:var(--color-background-secondary);border-radius:12px;
                padding:20px;margin-bottom:12px;text-align:center;">
      <div style="font-size:0.85em;color:var(--color-text-secondary)">Account Value</div>
      <div style="font-size:2.2em;font-weight:500;color:var(--color-text-primary)">${account:,.2f}</div>
      <div style="font-size:1em;color:{ret_color};margin-top:4px">
        {total_ret:+.2%} total &nbsp;|&nbsp; {daily_ret:+.2%} today
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Metric row
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Trades",  total_t)
    c2.metric("Win Rate",      f"{wr:.0%}")
    c3.metric("Max Drawdown",  f"{drawdown:.1%}")
    c4.metric("Consec. Losses",cons_loss)
    c5.metric("Peak Value",    f"${peak:,.0f}")

    # Circuit breaker warnings
    daily_loss = (daily_start - account) / max(daily_start, 1)
    if daily_loss >= 0.04:
        st.error(f"⚠️ Daily loss at {daily_loss:.1%} — approaching 5% limit")
    if cooldown:
        try:
            cd_end = datetime.fromisoformat(cooldown)
            if cd_end.tzinfo is None: cd_end = cd_end.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if now < cd_end:
                remaining = int((cd_end - now).total_seconds() / 60)
                st.warning(f"🕐 Cooldown active — {remaining}min remaining (after {cons_loss} consecutive losses)")
        except: pass

    # ── Open positions ────────────────────────────────────────────
    open_trades = [t for t in trades if t.get("status") == "OPEN"]
    if open_trades:
        st.markdown("### 🟢 Open Positions")
        for t in open_trades:
            direction = t.get("direction", "")
            entry     = t.get("entry_price", 0)
            sl        = t.get("stop_loss",   0)
            tp        = t.get("take_profit", 0)
            size      = t.get("size_usd",    0)
            size_pct  = t.get("size_pct",    0)
            regime    = t.get("regime",      "")
            open_time = t.get("open_time",   "")[:16].replace("T", " ")
            card_color= "#1b5e20" if direction == "LONG" else "#7f0000"

            st.markdown(f"""
            <div style="background:{card_color};border-radius:10px;padding:14px 16px;margin-bottom:8px;color:white">
              <b>{'🟢' if direction=='LONG' else '🔴'} {direction} — {regime}</b>
              &nbsp;|&nbsp; Entry: <b>${entry:,.2f}</b>
              &nbsp;|&nbsp; Size: <b>${size:,.0f}</b> ({size_pct:.1%})
              <br>
              <span style="font-size:0.85em;opacity:0.85">
                SL: ${sl:,.2f} &nbsp;|&nbsp; TP: ${tp:,.2f} &nbsp;|&nbsp; Opened: {open_time} UTC
              </span>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No open positions")

    # ── Trade history ─────────────────────────────────────────────
    closed = sorted([t for t in trades if t.get("status") == "CLOSED"],
                    key=lambda t: t.get("exit_time", ""), reverse=True)

    if closed:
        st.markdown(f"### 📋 Trade History ({len(closed)} closed trades)")

        rows = []
        for t in closed[:50]:
            pnl   = t.get("pnl_usd", 0)
            pct   = t.get("pnl_pct", 0)
            rows.append({
                "ID":        t.get("trade_id", ""),
                "Dir":       t.get("direction", ""),
                "Regime":    t.get("regime", ""),
                "Entry":     f"${t.get('entry_price',0):,.2f}",
                "Exit":      f"${t.get('exit_price',0):,.2f}",
                "PnL $":     f"${pnl:+,.2f}",
                "PnL %":     f"{pct:+.2%}",
                "Exit":      t.get("exit_reason", ""),
                "Opened":    t.get("open_time", "")[:16].replace("T", " "),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # Equity curve
        st.markdown("**Equity Curve**")
        eq = [INIT_CAP]
        for t in sorted(closed, key=lambda x: x.get("exit_time", "")):
            eq.append(round(eq[-1] + t.get("pnl_usd", 0), 2))
        st.line_chart(pd.DataFrame({"Account Value ($)": eq}), use_container_width=True)

        # P&L distribution
        pnls = [t.get("pnl_usd", 0) for t in closed]
        wins_   = sum(1 for p in pnls if p > 0)
        losses_ = sum(1 for p in pnls if p < 0)
        avg_w   = sum(p for p in pnls if p > 0) / max(wins_, 1)
        avg_l   = sum(p for p in pnls if p < 0) / max(losses_, 1)
        pf      = abs(avg_w / avg_l) if avg_l != 0 else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Avg Win",       f"${avg_w:,.2f}")
        c2.metric("Avg Loss",      f"${avg_l:,.2f}")
        c3.metric("Profit Factor", f"{pf:.2f}")
        c4.metric("Best Trade",    f"${max(pnls):+,.2f}" if pnls else "—")

    else:
        st.info("No closed trades yet — bot is running, waiting for signals to fire.")

    # ── Bot control hints ─────────────────────────────────────────
    with st.expander("⚙️ Bot Controls"):
        st.code(
            "# Start paper trading bot (runs 24/7):\n"
            "python paper_trading/paper_bot.py\n\n"
            "# Or via GitHub Actions (add to workflow):\n"
            "# python paper_trading/paper_bot.py &\n\n"
            "# Environment variables:\n"
            "PAPER_CAPITAL=10000\n"
            "PAPER_CHECK_INTERVAL=60\n"
            "PAPER_MAX_DAILY_LOSS=0.05\n"
            "PAPER_MAX_CONCURRENT=2\n"
            "PAPER_COOLDOWN_LOSSES=3\n"
            "PAPER_COOLDOWN_HOURS=4\n"
            "PAPER_KELLY_FRACTION=0.25",
            language="bash",
        )

    # ── Download trade log ────────────────────────────────────────
    if trades:
        st.download_button(
            "⬇️ Download Full Trade Log (JSON)",
            data=json.dumps(trades, indent=2, default=str),
            file_name="paper_trades.json",
            mime="application/json",
        )
