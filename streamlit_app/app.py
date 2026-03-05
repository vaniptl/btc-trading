"""
app.py — BTC Strategy v6 Mobile Dashboard
Deploy: streamlit.io → connect GitHub repo → done.

v6 new features:
  • EMA 50 & EMA 200 with golden/death cross display
  • Fibonacci 0.382 / 0.500 / 0.618 with actual price values
  • S/R levels shown for 1H, 4H, and 1D timeframes
  • RSI trend-zone labels (Bull Buy Zone / Bear Sell Zone)
  • Real buying pressure indicator
  • CVD divergence / absorption
  • RSI hidden divergence
  • Indicator rating stars (1-5)
  • 1D backtest mode
  • Daily Trade History tab with per-trade log, daily/weekly/monthly P&L
"""

import os, sys, json, time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
import pandas as pd
import numpy as np

st.set_page_config(
    page_title="BTC Strategy v6",
    page_icon="₿",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  .block-container { padding: 1rem 0.5rem; max-width: 100%; }
  .signal-card { border-radius:16px; padding:20px; margin:8px 0; text-align:center; font-size:1.4em; font-weight:bold; }
  .long-card   { background:linear-gradient(135deg,#00c853,#1b5e20); color:white; }
  .short-card  { background:linear-gradient(135deg,#d50000,#b71c1c); color:white; }
  .none-card   { background:linear-gradient(135deg,#424242,#212121); color:#aaa; }
  .metric-box  { flex:1; background:#1e1e1e; border-radius:12px; padding:12px 8px; text-align:center; }
  .metric-val  { font-size:1.3em; font-weight:bold; color:#00e5ff; }
  .metric-lbl  { font-size:0.7em; color:#888; }
  .score-bar-wrap  { background:#1e1e1e; border-radius:8px; height:12px; margin:4px 0; overflow:hidden; }
  .chip-bull   { display:inline-block; background:#00c853; color:#000; border-radius:20px; padding:2px 10px; font-size:0.75em; margin:2px; font-weight:600; }
  .chip-bear   { display:inline-block; background:#d50000; color:#fff; border-radius:20px; padding:2px 10px; font-size:0.75em; margin:2px; font-weight:600; }
  .chip-off    { display:inline-block; background:#2a2a2a; color:#555; border-radius:20px; padding:2px 10px; font-size:0.75em; margin:2px; }
  .sr-box      { background:#1a1a2e; border-left:3px solid #00e5ff; padding:8px 12px; margin:4px 0; border-radius:4px; font-size:0.85em; }
  .fib-box     { background:#1a2a1a; border-left:3px solid #00c853; padding:8px 12px; margin:4px 0; border-radius:4px; font-size:0.85em; }
  header { visibility:hidden; }
  .stDeployButton { display:none; }
  .stTabs [data-baseweb="tab"] { font-size:0.82em; padding:8px 10px; }
</style>
""", unsafe_allow_html=True)

import signal_runner as sr


# ══════════════════════════════════════════════════════════════════
# SESSION STATE
# ══════════════════════════════════════════════════════════════════

for key, val in [
    ("signal", None), ("last_fetch", 0),
    ("backtest_result", None), ("daily_history", None),
]:
    if key not in st.session_state:
        st.session_state[key] = val


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

def bar_html(pct, color):
    w = min(100, max(0, pct * 100))
    return f'<div class="score-bar-wrap"><div style="width:{w}%;background:{color};height:100%;border-radius:8px;"></div></div>'

def chip(label, state="off"):
    cls = {"bull": "chip-bull", "bear": "chip-bear"}.get(state, "chip-off")
    return f'<span class="{cls}">{label}</span>'

def stars(rating, max_r=5):
    return "★" * rating + "☆" * (max_r - rating)

def pnl_color(val):
    return "🟢" if val >= 0 else "🔴"


# ══════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════

st.markdown("## ₿ BTC Strategy **v6**")
st.caption(f"Multi-TF Confluence · EMA50/200 · Fib · S/R · Real Buying · {datetime.utcnow().strftime('%H:%M UTC')}")

tab_signal, tab_backtest, tab_daily, tab_history, tab_learn = st.tabs(
    ["📡 Signal", "📊 Backtest", "📅 Daily Trades", "📋 History", "🧠 Learn"]
)


# ══════════════════════════════════════════════════════════════════
# TAB 1 — LIVE SIGNAL
# ══════════════════════════════════════════════════════════════════

with tab_signal:
    c1, c2 = st.columns([3, 1])
    with c1:
        st.markdown("### Live Signal")
    with c2:
        refresh = st.button("🔄 Refresh", use_container_width=True)

    now = time.time()
    if refresh or (now - st.session_state.last_fetch > 60) or st.session_state.signal is None:
        with st.spinner("Fetching live data..."):
            sig = sr.get_signal()
            st.session_state.signal     = sig
            st.session_state.last_fetch = now

    sig = st.session_state.signal

    if not sig or "error" in sig:
        st.error(f"Error: {sig.get('error','Unknown error') if sig else 'No data'}")
        st.stop()

    d     = sig["direction"]
    price = sig["price"]
    ls    = sig["long_score"]
    ss    = sig["short_score"]
    cats  = sig["categories"]
    ep    = sig.get("entry_plan")
    inds  = sig.get("indicators", {})
    sr_d  = sig.get("sr", {})
    fib_d = sig.get("fibonacci", {})

    # ── Signal card ───────────────────────────────────────────────
    card_cls = {"LONG": "long-card", "SHORT": "short-card"}.get(d, "none-card")
    emoji    = {"LONG": "🟢 LONG",   "SHORT": "🔴 SHORT"}.get(d, "⚪ NO SIGNAL")

    st.markdown(f"""
    <div class="signal-card {card_cls}">
      {emoji}<br>
      <span style="font-size:1.8em">${price:,.2f}</span><br>
      <span style="font-size:0.6em;font-weight:normal">{sig["timestamp"][:16].replace("T"," ")} UTC</span>
    </div>
    """, unsafe_allow_html=True)

    # ── Scores ────────────────────────────────────────────────────
    st.markdown("**Confluence Scores**")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"🟢 Long: **{ls:.2f}**")
        st.markdown(bar_html(ls/15, "#00c853"), unsafe_allow_html=True)
    with c2:
        st.markdown(f"🔴 Short: **{ss:.2f}**")
        st.markdown(bar_html(ss/15, "#d50000"), unsafe_allow_html=True)

    # ── Category breakdown ────────────────────────────────────────
    st.markdown("**Category Breakdown**")
    cat_df = pd.DataFrame({
        "Category": ["📈 Trend (★★★★★)", "📍 Zone (★★★★☆)", "⚡ Momentum (★★★☆☆)", "🎯 Positioning (★★☆☆☆)"],
        "Long":  [cats["trend"]["long"],  cats["zone"]["long"],  cats["momentum"]["long"],  cats["positioning"]["long"]],
        "Short": [cats["trend"]["short"], cats["zone"]["short"], cats["momentum"]["short"], cats["positioning"]["short"]],
    })
    st.dataframe(cat_df, use_container_width=True, hide_index=True)

    # ── EMA status ────────────────────────────────────────────────
    st.markdown("**EMA Status**")
    ema50_v  = inds.get("ema50",  0)
    ema200_v = inds.get("ema200", 0)
    cross    = inds.get("ema200_cross", "")
    c1, c2, c3 = st.columns(3)
    c1.metric("EMA 50",  f"${ema50_v:,.0f}",  delta="✅ Above" if price > ema50_v  else "❌ Below")
    c2.metric("EMA 200", f"${ema200_v:,.0f}", delta="✅ Above" if price > ema200_v else "❌ Below")
    c3.metric("Cross",   f"{'🌟 Golden' if cross=='Golden' else '💀 Death'}")

    # ── RSI with zone label ───────────────────────────────────────
    rsi_v    = inds.get("rsi", 50)
    rsi_zone = inds.get("rsi_zone", "Neutral")
    c1, c2, c3 = st.columns(3)
    c1.metric("RSI",       f"{rsi_v:.1f}")
    c2.metric("RSI Zone",  rsi_zone)
    c3.metric("Vol Ratio", f"{inds.get('vol_ratio',1):.2f}x")

    # ── Real buying/selling ───────────────────────────────────────
    real_buy = inds.get("real_buying",  False)
    real_sel = inds.get("real_selling", False)
    c1, c2 = st.columns(2)
    c1.markdown(f"**Real Buying:** {'🟢 CONFIRMED' if real_buy else '⚫ No'}")
    c2.markdown(f"**Real Selling:** {'🔴 CONFIRMED' if real_sel else '⚫ No'}")

    # ── S/R + Fibonacci per timeframe ─────────────────────────────
    st.markdown("**Support & Resistance + Fibonacci Levels**")
    for tf in ["1H", "4H", "1D"]:
        sr_tf  = sr_d.get(tf, {})
        fib_tf = fib_d.get(tf, {})
        ns  = sr_tf.get("nearest_support")
        nr  = sr_tf.get("nearest_resistance")
        f50  = fib_tf.get("fib50",  0)
        f618 = fib_tf.get("fib618", 0)
        f382 = fib_tf.get("fib382", 0)
        fh   = fib_tf.get("anchor_high", 0)
        fl   = fib_tf.get("anchor_low",  0)

        with st.expander(f"📍 {tf} Levels"):
            sc1, sc2 = st.columns(2)
            sc1.metric("Nearest Support",    f"${ns:,.0f}"  if ns else "N/A",
                       delta=f"{(price-ns)/ns*100:+.1f}%" if ns else None)
            sc2.metric("Nearest Resistance", f"${nr:,.0f}"  if nr else "N/A",
                       delta=f"{(nr-price)/price*100:+.1f}%" if nr else None)

            st.markdown(f"**Fibonacci** (High: `${fh:,.0f}` | Low: `${fl:,.0f}`)")
            fc1, fc2, fc3 = st.columns(3)
            fc1.metric("Fib 0.382", f"${f382:,.0f}")
            fc2.metric("Fib 0.500", f"${f50:,.0f}")
            fc3.metric("Fib 0.618", f"${f618:,.0f}")

    # ── Entry plan ────────────────────────────────────────────────
    if ep:
        st.markdown(f"**{d} Entry Plan**")
        c1, c2, c3 = st.columns(3)
        c1.metric("Entry",       f"${ep['entry']:,.0f}")
        c2.metric("Stop Loss",   f"${ep['stop_loss']:,.0f}")
        c3.metric("Take Profit", f"${ep['take_profit']:,.0f}")
        st.info(f"R:R Ratio — 1:{ep['rr']:.1f}")

    # ── All indicators ────────────────────────────────────────────
    with st.expander("🔬 All Indicator Status (rated ★1-5)"):
        ind_rows = [
            # label,                       long_active,                                        short_active,                                          rating
            ("4H Bias",                    bool(sig.get("reasons_long") and "4H Bias Bullish" in sig.get("reasons_long",[])),   bool("4H Bias Bearish" in sig.get("reasons_short",[])),  5),
            ("EMA Ribbon",                 "EMA Ribbon Bull" in sig.get("reasons_long",[]),    "EMA Ribbon Bear" in sig.get("reasons_short",[]),       5),
            ("Above/Below EMA 50",         price > ema50_v,                                   price < ema50_v,                                        5),
            ("Above/Below EMA 200",        price > ema200_v,                                  price < ema200_v,                                        5),
            ("Golden/Death Cross",         cross == "Golden",                                  cross == "Death",                                        5),
            ("BOS",                        "BOS Up"   in sig.get("reasons_long",[]),           "BOS Down" in sig.get("reasons_short",[]),               5),
            ("CHoCH",                      "CHoCH"    in sig.get("reasons_long",[]),           "CHoCH" in sig.get("reasons_short",[]),                  3),
            ("Near Support/Resistance",    "Near Support"    in sig.get("reasons_long",[]),    "Near Resistance" in sig.get("reasons_short",[]),        4),
            ("Fib 0.618",                  "Fib 0.618" in sig.get("reasons_long",[]),          "Fib 0.618" in sig.get("reasons_short",[]),              4),
            ("Fib 0.500",                  "Fib 0.500" in sig.get("reasons_long",[]),          "Fib 0.500" in sig.get("reasons_short",[]),              4),
            ("Fib 0.382",                  "Fib 0.382" in sig.get("reasons_long",[]),          "Fib 0.382" in sig.get("reasons_short",[]),              3),
            ("Order Block",                "Bullish OB" in sig.get("reasons_long",[]),         "Bearish OB" in sig.get("reasons_short",[]),             4),
            ("VWAP",                       price < inds.get("vwap",price+1),                   price > inds.get("vwap",price-1),                        3),
            ("RSI Zone",                   rsi_v < 40 or "RSI Bull Zone" in sig.get("reasons_long",[]), rsi_v > 60 or "RSI Bear Zone" in sig.get("reasons_short",[]), 4),
            ("RSI Reg Divergence",         "RSI Bull Div" in sig.get("reasons_long",[]),       "RSI Bear Div" in sig.get("reasons_short",[]),           4),
            ("RSI Hidden Divergence",      "RSI Hidden Bull Div" in sig.get("reasons_long",[]),"RSI Hidden Bear Div" in sig.get("reasons_short",[]),    4),
            ("CVD Direction",              inds.get("cvd_bull_div"),                            inds.get("cvd_bear_div"),                                3),
            ("CVD Divergence/Absorption",  "CVD Bull Div" in sig.get("reasons_long",[]),       "CVD Bear Div" in sig.get("reasons_short",[]),           4),
            ("Real Buying/Selling",        real_buy,                                           real_sel,                                                4),
            ("Volume Confirm",             "Vol Confirm" in sig.get("reasons_long",[]),        "Vol Confirm" in sig.get("reasons_short",[]),             2),
            ("Liquidations",               "Long Liq" in sig.get("reasons_long",[]),           "Short Liq" in sig.get("reasons_short",[]),              3),
            ("Funding Rate",               "Funding Short Ext" in sig.get("reasons_long",[]),  "Funding Long Ext" in sig.get("reasons_short",[]),       3),
            ("Open Interest",              "OI Long" in sig.get("reasons_long",[]),            "OI Short" in sig.get("reasons_short",[]),               3),
        ]

        rows = []
        for label, la, sa, rating in ind_rows:
            rows.append({
                "Rating":     stars(rating),
                "Indicator":  label,
                "Bull 🟢":    "✅" if la else "⚫",
                "Bear 🔴":    "✅" if sa else "⚫",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("RSI",       f"{rsi_v:.1f}")
        c2.metric("Vol Ratio", f"{inds.get('vol_ratio',0):.2f}x")
        c3.metric("ATR",       f"${inds.get('atr',0):,.0f}")
        c4.metric("VWAP",      f"${inds.get('vwap',0):,.0f}")

    # ── Price chart ───────────────────────────────────────────────
    with st.expander("📈 Price Chart (1H)"):
        df_chart = sig.get("df")
        if df_chart is not None and not df_chart.empty:
            tail = df_chart.tail(100)
            st.line_chart(pd.DataFrame({
                "Close":  tail["close"],
                "EMA50":  tail["ema50"],
                "EMA200": tail["ema200"],
                "EMA21":  tail["ema21"],
            }), use_container_width=True)
            st.markdown("**RSI**")
            st.line_chart(pd.DataFrame({"RSI": tail["rsi"]}),
                         use_container_width=True, height=120)
            st.markdown("**CVD**")
            st.line_chart(pd.DataFrame({"CVD": tail["cvd"]}),
                         use_container_width=True, height=120)


# ══════════════════════════════════════════════════════════════════
# TAB 2 — BACKTEST
# ══════════════════════════════════════════════════════════════════

with tab_backtest:
    st.markdown("### Run Backtest")

    c1, c2 = st.columns(2)
    period    = c1.select_slider("Period", options=["1M","3M","6M","1Y","3Y"], value="1Y")
    timeframe = c2.radio("Timeframe", ["1H", "1D"], horizontal=True)

    c1, c2, c3 = st.columns(3)
    min_score = c1.number_input("Min Score",   value=float(sr.MIN_SCORE), step=0.5, min_value=1.0, max_value=15.0)
    atr_sl    = c2.number_input("ATR SL Mult", value=float(sr.ATR_SL),   step=0.5, min_value=0.5, max_value=5.0)
    atr_tp    = c3.number_input("ATR TP Mult", value=float(sr.ATR_TP),   step=0.5, min_value=1.0, max_value=10.0)

    run_btn = st.button("🚀 Run Backtest", use_container_width=True, type="primary")

    if run_btn:
        try:
            import vectorbt as vbt
        except ImportError:
            st.error("vectorbt not installed. Add it to requirements.txt")
            st.stop()

        with st.spinner(f"Running {period} | {timeframe} backtest..."):
            days   = sr.PERIOD_DAYS[period]
            df_1h  = sr.fetch_ohlcv("hour", 1, days_back=days)
            df_4h  = sr.fetch_ohlcv("hour", 4, days_back=days)
            df_1d  = sr.fetch_ohlcv("day",  1, days_back=days)
            liq    = sr.fetch_liquidations(max_days=min(days, 30))
            fund   = sr.fetch_funding()
            oi     = sr.fetch_oi()

            if df_1h.empty:
                st.error("No OHLCV data available")
                st.stop()

            df = sr.compute_indicators(df_1h, df_4h, liq, fund, oi, df_1d)
            df = sr.compute_scores(df, min_score=min_score)

            # 1D: resample signals to daily
            if timeframe == "1D":
                df_bt = df.resample("1D").agg({
                    "open":"first","high":"max","low":"min","close":"last",
                    "volume":"sum","atr":"mean",
                    "long_signal":"any","short_signal":"any",
                }).dropna(subset=["close"])
                vbt_freq = "1D"
            else:
                df_bt    = df.copy()
                vbt_freq = "1h"

            n_sig = int(df_bt["long_signal"].sum()) + int(df_bt["short_signal"].sum())
            if n_sig == 0:
                st.warning(f"No signals — try lowering Min Score (currently {min_score})")
                st.stop()

            atr_col  = df_bt["atr"] if "atr" in df_bt.columns else sr._atr(df_bt)
            sl_stop  = atr_col * atr_sl / df_bt["close"]
            tp_stop  = atr_col * atr_tp / df_bt["close"]

            pf = vbt.Portfolio.from_signals(
                close=df_bt["close"], high=df_bt["high"], low=df_bt["low"],
                entries=df_bt["long_signal"], short_entries=df_bt["short_signal"],
                sl_stop=sl_stop, tp_stop=tp_stop,
                init_cash=sr.INIT_CASH, fees=sr.FEES, slippage=sr.SLIPPAGE,
                freq=vbt_freq,
            )

            try:
                t    = pf.trades.records_readable
                wins = len(t[t["PnL"] > 0]); losses = len(t[t["PnL"] < 0])
                wr   = wins / len(t) if len(t) > 0 else 0
                aw   = float(t[t["PnL"] > 0]["PnL"].mean()) if wins   > 0 else 0
                al   = float(t[t["PnL"] < 0]["PnL"].mean()) if losses > 0 else 0
                pfr  = abs(aw / al) if al != 0 else 0
                trades_df = t
            except Exception:
                wr = aw = al = pfr = 0; trades_df = pd.DataFrame()

            bh = float(df_bt["close"].iloc[-1] / df_bt["close"].iloc[0]) - 1

            result = {
                "run_id":        datetime.utcnow().strftime("%Y%m%d_%H%M%S"),
                "run_ts":        datetime.utcnow().isoformat(),
                "period":        period,
                "timeframe":     timeframe,
                "start":         str(df_bt.index[0].date()),
                "end":           str(df_bt.index[-1].date()),
                "total_return":  round(float(pf.total_return()), 4),
                "buy_and_hold":  round(bh, 4),
                "beat_bh":       bool(pf.total_return() > bh),
                "sharpe_ratio":  round(float(pf.sharpe_ratio()  or 0), 3),
                "sortino_ratio": round(float(pf.sortino_ratio() or 0), 3),
                "max_drawdown":  round(float(pf.max_drawdown()), 4),
                "total_trades":  int(pf.trades.count()),
                "win_rate":      round(wr, 4),
                "avg_win":       round(aw, 2),
                "avg_loss":      round(al, 2),
                "profit_factor": round(pfr, 3),
                "final_value":   round(float(pf.final_value()), 2),
                "init_cash":     sr.INIT_CASH,
                "config": {
                    "min_score":      min_score,
                    "atr_sl_mult":    atr_sl,
                    "atr_tp_mult":    atr_tp,
                    "min_categories": sr.MIN_CATS,
                    "trend_required": sr.TREND_REQ,
                    "enable_shorts":  sr.ENABLE_SHORT,
                },
                "trades_df": trades_df,
                "equity":    pf.value(),
                "pf":        pf,
            }
            sr.save_to_memory({k: v for k, v in result.items()
                               if k not in ("trades_df", "equity", "pf")})
            st.session_state.backtest_result = result

            # Build daily history
            summary, td, daily, weekly, monthly = sr.build_daily_trade_history(
                pf, sr.INIT_CASH
            )
            st.session_state.daily_history = {
                "summary": summary, "trades": td,
                "daily": daily, "weekly": weekly, "monthly": monthly,
                "period": period, "timeframe": timeframe,
            }

    r = st.session_state.backtest_result
    if r:
        tf_label = r.get("timeframe", "1H")
        st.markdown(f"#### Results — {r['period']} | {tf_label}  `{r['start']} → {r['end']}`")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Return",   f"{r['total_return']:.1%}", delta=f"B&H: {r['buy_and_hold']:.1%}")
        c2.metric("Sharpe",   f"{r['sharpe_ratio']:.3f}")
        c3.metric("Max DD",   f"{r['max_drawdown']:.1%}")
        c4.metric("Trades",   r["total_trades"])

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Win Rate",      f"{r['win_rate']:.1%}")
        c2.metric("Profit Factor", f"{r['profit_factor']:.3f}")
        c3.metric("Avg Win",       f"${r['avg_win']:,.0f}")
        c4.metric("Avg Loss",      f"${r['avg_loss']:,.0f}")

        st.metric("Final Value", f"${r['final_value']:,.2f}",
                  delta=f"Started ${r['init_cash']:,.0f}")
        st.success("✅ Beat Buy & Hold" if r["beat_bh"] else "❌ Did not beat Buy & Hold")

        if "equity" in r and r["equity"] is not None:
            st.markdown("**Equity Curve**")
            eq = r["equity"].reset_index()
            eq.columns = ["Date", "Portfolio Value"]
            st.line_chart(eq.set_index("Date"), use_container_width=True)

        if "trades_df" in r and not r["trades_df"].empty:
            with st.expander(f"📋 Trade Log ({r['total_trades']} trades)"):
                display_cols = [c for c in [
                    "Direction", "Entry Timestamp", "Entry Price",
                    "Exit Timestamp", "Exit Price", "PnL", "Return"
                ] if c in r["trades_df"].columns]
                st.dataframe(
                    r["trades_df"][display_cols] if display_cols else r["trades_df"],
                    use_container_width=True
                )

        st.info("👉 Go to **📅 Daily Trades** tab to see the full day-by-day breakdown.")


# ══════════════════════════════════════════════════════════════════
# TAB 3 — DAILY TRADE HISTORY
# ══════════════════════════════════════════════════════════════════

with tab_daily:
    st.markdown("### 📅 Daily Trade History")

    h = st.session_state.daily_history

    if not h:
        st.info("Run a backtest first (Backtest tab), then come back here.")
    else:
        summary  = h["summary"]
        td       = h["trades"]
        daily    = h["daily"]
        weekly   = h["weekly"]
        monthly  = h["monthly"]
        period   = h.get("period", "")
        tf_label = h.get("timeframe", "")

        st.markdown(f"**Period:** {period} | **Timeframe:** {tf_label}")

        # ── Summary metrics ───────────────────────────────────────
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Trades", summary["total_trades"])
        c2.metric("Win Rate",     f"{summary['win_rate']:.1%}")
        c3.metric("Total P&L",    f"${summary['total_pnl']:+,.2f}")
        c4.metric("Final Balance",f"${summary['final_balance']:,.2f}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Wins",        summary["wins"])
        c2.metric("Losses",      summary["losses"])
        c3.metric("Best Trade",  f"${summary['best_trade']:+,.2f}", delta=summary["best_date"])
        c4.metric("Worst Trade", f"${summary['worst_trade']:+,.2f}", delta=summary["worst_date"])

        # ── Per-trade log ─────────────────────────────────────────
        with st.expander(f"📋 Per-Trade Log ({summary['total_trades']} trades)", expanded=True):
            if not td.empty:
                display_td = td[[
                    "entry_date", "exit_date", "direction",
                    "entry_price", "exit_price", "pnl", "pnl_pct",
                    "balance", "exit_reason"
                ]].copy()
                display_td.columns = [
                    "Entry Date", "Exit Date", "Direction",
                    "Entry $", "Exit $", "PnL $", "PnL %",
                    "Balance After", "Exit Reason"
                ]
                # Colour coding
                display_td["PnL $"] = display_td["PnL $"].round(2)
                display_td["PnL %"] = display_td["PnL %"].round(2)
                display_td["Entry $"] = display_td["Entry $"].round(2)
                display_td["Exit $"]  = display_td["Exit $"].round(2)
                display_td["Balance After"] = display_td["Balance After"].round(2)
                st.dataframe(display_td, use_container_width=True, hide_index=True)

                # Download
                csv = display_td.to_csv(index=False)
                st.download_button(
                    "⬇️ Download Trade Log CSV",
                    data=csv,
                    file_name=f"btc_trades_{period}_{tf_label}.csv",
                    mime="text/csv",
                )

        # ── Daily P&L ─────────────────────────────────────────────
        with st.expander("📆 Daily P&L Summary", expanded=True):
            if not daily.empty:
                daily_disp = daily[[
                    "exit_date", "trades", "wins", "wr",
                    "net_pnl", "best_pnl", "worst_pnl", "balance_eod"
                ]].copy()
                daily_disp.columns = [
                    "Date", "Trades", "Wins", "WR",
                    "Net P&L", "Best", "Worst", "Balance EOD"
                ]
                daily_disp["Net P&L"] = daily_disp["Net P&L"].round(2)
                daily_disp["WR"]      = (daily_disp["WR"] * 100).round(1).astype(str) + "%"
                st.dataframe(daily_disp, use_container_width=True, hide_index=True)

                # P&L bar chart
                chart_df = daily[["exit_date", "net_pnl"]].set_index("exit_date")
                chart_df.index = pd.to_datetime(chart_df.index)
                st.markdown("**Daily P&L Chart**")
                st.bar_chart(chart_df, use_container_width=True)

                # Download
                csv2 = daily_disp.to_csv(index=False)
                st.download_button(
                    "⬇️ Download Daily Summary CSV",
                    data=csv2,
                    file_name=f"btc_daily_pnl_{period}_{tf_label}.csv",
                    mime="text/csv",
                )

        # ── Weekly rollup ─────────────────────────────────────────
        with st.expander("📅 Weekly P&L Rollup"):
            if not weekly.empty:
                w_disp = weekly[["week_str", "trades", "wr", "net_pnl", "cum_pnl"]].copy()
                w_disp.columns = ["Week", "Trades", "WR", "Net P&L", "Cumulative P&L"]
                w_disp["WR"]            = (w_disp["WR"] * 100).round(1).astype(str) + "%"
                w_disp["Net P&L"]       = w_disp["Net P&L"].round(2)
                w_disp["Cumulative P&L"]= w_disp["Cumulative P&L"].round(2)
                st.dataframe(w_disp, use_container_width=True, hide_index=True)

        # ── Monthly rollup ────────────────────────────────────────
        with st.expander("🗓️ Monthly P&L Rollup"):
            if not monthly.empty:
                m_disp = monthly[["month_str","trades","wr","net_pnl","ret_pct","cum_pnl"]].copy()
                m_disp.columns = ["Month","Trades","WR","Net P&L","Return %","Cumulative P&L"]
                m_disp["WR"]            = (m_disp["WR"] * 100).round(1).astype(str) + "%"
                m_disp["Net P&L"]       = m_disp["Net P&L"].round(2)
                m_disp["Cumulative P&L"]= m_disp["Cumulative P&L"].round(2)
                st.dataframe(m_disp, use_container_width=True, hide_index=True)

                # Monthly return chart
                m_chart = monthly[["month_str","ret_pct"]].set_index("month_str")
                st.markdown("**Monthly Return % Chart**")
                st.bar_chart(m_chart, use_container_width=True)

        # ── Cumulative P&L curve ──────────────────────────────────
        if not td.empty:
            st.markdown("**Cumulative P&L Curve**")
            cum_df = td[["exit_time","balance"]].set_index("exit_time")
            cum_df.columns = ["Balance"]
            st.line_chart(cum_df, use_container_width=True)


# ══════════════════════════════════════════════════════════════════
# TAB 4 — HISTORY
# ══════════════════════════════════════════════════════════════════

with tab_history:
    st.markdown("### Backtest History")
    mem = sr.load_memory()

    if not mem:
        st.info("No backtest runs yet. Go to Backtest tab and run one.")
    else:
        rows = []
        for r in reversed(mem[-50:]):
            rows.append({
                "Run ID":    r.get("run_id", ""),
                "Period":    r.get("period", ""),
                "TF":        r.get("timeframe", "1H"),
                "Return":    f"{r.get('total_return',0):.1%}",
                "Sharpe":    f"{r.get('sharpe_ratio',0):.3f}",
                "Max DD":    f"{r.get('max_drawdown',0):.1%}",
                "Trades":    r.get("total_trades", 0),
                "Win Rate":  f"{r.get('win_rate',0):.1%}",
                "Beat B&H":  "✅" if r.get("beat_bh") else "❌",
                "Min Score": r.get("config", {}).get("min_score", ""),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.download_button(
            "⬇️ Download History JSON",
            data=json.dumps(mem, indent=2, default=str),
            file_name="btc_backtest_memory.json",
            mime="application/json",
        )

        returns = [r.get("total_return", 0) for r in mem]
        if len(returns) > 1:
            st.markdown("**Return Distribution**")
            st.bar_chart(pd.DataFrame({"Return": returns}))


# ══════════════════════════════════════════════════════════════════
# TAB 5 — LEARN
# ══════════════════════════════════════════════════════════════════

with tab_learn:
    st.markdown("### 🧠 Learning Report")
    mem = sr.load_memory()

    if not mem:
        st.info("Run at least 2-3 backtests to see learning insights.")
    else:
        ranked = sorted(mem, key=lambda x: x.get("sharpe_ratio", 0), reverse=True)
        best   = ranked[0]
        worst  = ranked[-1]

        st.success(f"🥇 Best: {best.get('period','')} | {best.get('timeframe','1H')} | Return: {best['total_return']:.1%} | Sharpe: {best['sharpe_ratio']:.3f}")
        st.error(f"🔴 Worst: {worst.get('period','')} | Return: {worst['total_return']:.1%} | Sharpe: {worst['sharpe_ratio']:.3f}")

        profitable = [r for r in mem if r["total_return"] > 0]
        beat_bh    = [r for r in mem if r.get("beat_bh")]
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Runs",  len(mem))
        c2.metric("Profitable",  f"{len(profitable)}/{len(mem)}")
        c3.metric("Beat B&H",    f"{len(beat_bh)}/{len(mem)}")

        # By period
        st.markdown("**Average Sharpe by Period**")
        by_period = {}
        for r in mem:
            p = r["period"]
            if p not in by_period: by_period[p] = []
            by_period[p].append(r["sharpe_ratio"])
        st.dataframe(pd.DataFrame([
            {"Period": p, "Avg Sharpe": round(sum(v)/len(v),3), "Runs": len(v)}
            for p, v in by_period.items()
        ]).sort_values("Avg Sharpe", ascending=False),
        use_container_width=True, hide_index=True)

        # By timeframe
        st.markdown("**Average Sharpe by Timeframe**")
        by_tf = {}
        for r in mem:
            t = r.get("timeframe", "1H")
            if t not in by_tf: by_tf[t] = []
            by_tf[t].append(r["sharpe_ratio"])
        st.dataframe(pd.DataFrame([
            {"Timeframe": t, "Avg Sharpe": round(sum(v)/len(v),3), "Runs": len(v)}
            for t, v in by_tf.items()
        ]).sort_values("Avg Sharpe", ascending=False),
        use_container_width=True, hide_index=True)

        # Min score sensitivity
        scores_tested = list(set(r["config"].get("min_score","") for r in mem if "config" in r))
        if len(scores_tested) > 1:
            st.markdown("**Min Score Sensitivity**")
            score_rows = []
            for sc in sorted([s for s in scores_tested if s]):
                runs = [r for r in mem if r.get("config", {}).get("min_score") == sc]
                if runs:
                    score_rows.append({
                        "Min Score":  sc,
                        "Avg Sharpe": round(sum(r["sharpe_ratio"] for r in runs)/len(runs), 3),
                        "Avg Return": f"{sum(r['total_return'] for r in runs)/len(runs):.1%}",
                        "Runs":       len(runs),
                    })
            if score_rows:
                st.dataframe(pd.DataFrame(score_rows), use_container_width=True, hide_index=True)

        # Best config
        st.markdown("**💡 Recommended Config (from best run)**")
        bc = best.get("config", {})
        c1, c2 = st.columns(2)
        c1.code(f"""MIN_SCORE      = {bc.get('min_score','?')}
MIN_CATEGORIES = {bc.get('min_categories','?')}
TREND_REQUIRED = {bc.get('trend_required','?')}
ATR_SL_MULT    = {bc.get('atr_sl_mult','?')}
ATR_TP_MULT    = {bc.get('atr_tp_mult','?')}
TIMEFRAME      = {best.get('timeframe','1H')}""")
        c2.metric("Expected Return", f"{best['total_return']:.1%}")
        c2.metric("Expected Sharpe", f"{best['sharpe_ratio']:.3f}")
