"""
streamlit_app/app.py — BTC Strategy Mobile Dashboard
Deploy free: streamlit.io → connect GitHub repo → done.
Mobile optimised. Charts, signal, backtest, history, learning.
"""

import os, sys, json, time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

import streamlit as st
import pandas as pd
import numpy as np

# Page config — MUST be first Streamlit call
st.set_page_config(
    page_title="BTC Strategy",
    page_icon="₿",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ── Mobile-first CSS ──────────────────────────────────────────────
st.markdown("""
<style>
  /* Full-width on mobile */
  .block-container { padding: 1rem 0.5rem; max-width: 100%; }
  
  /* Signal card */
  .signal-card {
    border-radius: 16px; padding: 20px; margin: 8px 0;
    text-align: center; font-size: 1.4em; font-weight: bold;
  }
  .long-card  { background: linear-gradient(135deg,#00c853,#1b5e20); color: white; }
  .short-card { background: linear-gradient(135deg,#d50000,#b71c1c); color: white; }
  .none-card  { background: linear-gradient(135deg,#424242,#212121); color: #aaa; }
  
  /* Metric cards */
  .metric-row { display: flex; gap: 8px; margin: 4px 0; }
  .metric-box {
    flex: 1; background: #1e1e1e; border-radius: 12px;
    padding: 12px 8px; text-align: center;
  }
  .metric-val { font-size: 1.3em; font-weight: bold; color: #00e5ff; }
  .metric-lbl { font-size: 0.7em; color: #888; }
  
  /* Score bars */
  .score-bar-wrap { background: #1e1e1e; border-radius: 8px; height: 12px; margin: 4px 0; overflow: hidden; }
  .score-bar-long  { background: linear-gradient(90deg,#00c853,#69f0ae); height: 100%; border-radius: 8px; }
  .score-bar-short { background: linear-gradient(90deg,#d50000,#ff5252); height: 100%; border-radius: 8px; }
  
  /* Indicator chip */
  .chip-active { display:inline-block; background:#00c853; color:#000; border-radius:20px; padding:2px 10px; font-size:0.75em; margin:2px; font-weight:600; }
  .chip-inactive { display:inline-block; background:#2a2a2a; color:#555; border-radius:20px; padding:2px 10px; font-size:0.75em; margin:2px; }
  
  /* Hide streamlit header */
  header { visibility: hidden; }
  .stDeployButton { display: none; }
  
  /* Tab styling */
  .stTabs [data-baseweb="tab"] { font-size: 0.85em; padding: 8px 12px; }
</style>
""", unsafe_allow_html=True)

import signal_runner as sr


# ══════════════════════════════════════════════════════════════════
# STATE
# ══════════════════════════════════════════════════════════════════

if "signal" not in st.session_state:
    st.session_state.signal    = None
if "last_fetch" not in st.session_state:
    st.session_state.last_fetch = 0
if "backtest_result" not in st.session_state:
    st.session_state.backtest_result = None


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

def pct_bar_html(pct, color):
    w = min(100, max(0, pct*100))
    return f'<div class="score-bar-wrap"><div style="width:{w}%;background:{color};height:100%;border-radius:8px;"></div></div>'

def chip(label, active):
    cls = "chip-active" if active else "chip-inactive"
    return f'<span class="{cls}">{label}</span>'


# ══════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════

st.markdown("## ₿ BTC Strategy")
st.caption(f"13-Indicator Tiered Confluence | {datetime.utcnow().strftime('%H:%M UTC')}")

# ── Tabs ──────────────────────────────────────────────────────────
tab_signal, tab_backtest, tab_history, tab_learn = st.tabs(
    ["📡 Signal", "📊 Backtest", "📋 History", "🧠 Learn"])


# ══════════════════════════════════════════════════════════════════
# TAB 1 — LIVE SIGNAL
# ══════════════════════════════════════════════════════════════════

with tab_signal:
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown("### Live Signal")
    with col2:
        refresh = st.button("🔄 Refresh", use_container_width=True)

    # Auto-fetch if stale (>60s) or manual refresh
    now = time.time()
    if refresh or (now - st.session_state.last_fetch > 60) or st.session_state.signal is None:
        with st.spinner("Fetching live data..."):
            sig = sr.get_signal()
            st.session_state.signal     = sig
            st.session_state.last_fetch = now

    sig = st.session_state.signal

    if sig and "error" not in sig:
        d     = sig["direction"]
        price = sig["price"]
        ls    = sig["long_score"]
        ss    = sig["short_score"]
        cats  = sig["categories"]
        ep    = sig.get("entry_plan")
        inds  = sig.get("indicators", {})

        # ── Signal card ──────────────────────────────────────────
        if   d == "LONG":  card_cls="long-card";  emoji="🟢 LONG"
        elif d == "SHORT": card_cls="short-card";  emoji="🔴 SHORT"
        else:              card_cls="none-card";   emoji="⚪ NO SIGNAL"

        st.markdown(f"""
        <div class="signal-card {card_cls}">
          {emoji}<br>
          <span style="font-size:1.8em">${price:,.2f}</span><br>
          <span style="font-size:0.6em;font-weight:normal">
            {sig["timestamp"][:16].replace("T"," ")} UTC
          </span>
        </div>
        """, unsafe_allow_html=True)

        # ── Scores ───────────────────────────────────────────────
        st.markdown("**Confluence Scores**")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"🟢 Long: **{ls:.2f}**")
            st.markdown(pct_bar_html(ls/12, "#00c853"), unsafe_allow_html=True)
        with c2:
            st.markdown(f"🔴 Short: **{ss:.2f}**")
            st.markdown(pct_bar_html(ss/12, "#d50000"), unsafe_allow_html=True)

        # ── Category breakdown ───────────────────────────────────
        st.markdown("**Category Breakdown**")
        cat_data = {
            "Category": ["📈 Trend", "📍 Zone", "⚡ Momentum", "🎯 Positioning"],
            "Long":  [cats["trend"]["long"], cats["zone"]["long"], cats["momentum"]["long"], cats["positioning"]["long"]],
            "Short": [cats["trend"]["short"],cats["zone"]["short"],cats["momentum"]["short"],cats["positioning"]["short"]],
        }
        st.dataframe(pd.DataFrame(cat_data), use_container_width=True, hide_index=True)

        # ── Active reasons ───────────────────────────────────────
        active_reasons = sig.get("reasons_long" if d=="LONG" else "reasons_short", [])
        if active_reasons:
            st.markdown(f"**Active Indicators ({len(active_reasons)})**")
            chips_html = " ".join(chip(r, True) for r in active_reasons)
            st.markdown(chips_html, unsafe_allow_html=True)

        # ── All indicators status ────────────────────────────────
        with st.expander("🔬 All Indicator Status"):
            all_inds = [
                ("4H Bias Bull",    bool(sig.get("reasons_long") and "4H Bias Bullish" in sig.get("reasons_long",[]))),
                ("4H Bias Bear",    bool(sig.get("reasons_short") and "4H Bias Bearish" in sig.get("reasons_short",[]))),
                ("EMA Ribbon",      "EMA Ribbon Bull" in sig.get("reasons_long",[])),
                ("Above EMA200",    inds.get("ema200",0) and price > inds["ema200"]),
                ("Near Support",    "Near Support" in sig.get("reasons_long",[])),
                ("Near Resistance", "Near Resistance" in sig.get("reasons_short",[])),
                ("Fib 0.618",       "Fib 0.618" in sig.get("reasons_long",[])+sig.get("reasons_short",[])),
                ("Bullish OB",      "Bullish OB" in sig.get("reasons_long",[])),
                ("Below VWAP",      price < inds.get("vwap",price+1)),
                (f"RSI {inds.get('rsi',50):.0f}", inds.get("rsi",50) < 40 or inds.get("rsi",50) > 60),
                ("Vol Confirm",     inds.get("vol_ratio",1) > 1),
            ]
            html = ""
            for name, active in all_inds:
                html += chip(name, active) + " "
            st.markdown(html, unsafe_allow_html=True)
            c1, c2, c3 = st.columns(3)
            c1.metric("RSI", f"{inds.get('rsi',0):.1f}")
            c2.metric("Vol Ratio", f"{inds.get('vol_ratio',0):.2f}x")
            c3.metric("ATR", f"${inds.get('atr',0):,.0f}")

        # ── Entry plan ───────────────────────────────────────────
        if ep:
            color = "#1b5e20" if d=="LONG" else "#b71c1c"
            st.markdown(f"**{d} Entry Plan**")
            col1, col2, col3 = st.columns(3)
            col1.metric("Entry",       f"${ep['entry']:,.0f}")
            col2.metric("Stop Loss",   f"${ep['stop_loss']:,.0f}")
            col3.metric("Take Profit", f"${ep['take_profit']:,.0f}")
            st.info(f"R:R Ratio — 1:{ep['rr']:.1f}")

        # ── Price chart ──────────────────────────────────────────
        with st.expander("📈 Price Chart (1H)"):
            df_chart = sig.get("df")
            if df_chart is not None and not df_chart.empty:
                tail = df_chart.tail(100)
                chart_data = pd.DataFrame({
                    "Close":  tail["close"],
                    "EMA21":  tail["ema21"],
                    "EMA55":  tail["ema55"],
                    "EMA200": tail["ema200"],
                })
                st.line_chart(chart_data, use_container_width=True)

                # RSI
                rsi_data = pd.DataFrame({"RSI": tail["rsi"]})
                st.markdown("**RSI**")
                st.line_chart(rsi_data, use_container_width=True, height=120)

    elif sig and "error" in sig:
        st.error(f"Error: {sig['error']}")
    else:
        st.info("Click Refresh to fetch live signal")


# ══════════════════════════════════════════════════════════════════
# TAB 2 — BACKTEST
# ══════════════════════════════════════════════════════════════════

with tab_backtest:
    st.markdown("### Run Backtest")

    period = st.select_slider(
        "Select Period",
        options=["1M", "3M", "6M", "1Y", "3Y"],
        value="1Y",
    )

    c1, c2, c3 = st.columns(3)
    min_score = c1.number_input("Min Score",    value=float(sr.MIN_SCORE),   step=0.5, min_value=1.0, max_value=15.0)
    atr_sl    = c2.number_input("ATR SL Mult",  value=float(sr.ATR_SL),      step=0.5, min_value=0.5, max_value=5.0)
    atr_tp    = c3.number_input("ATR TP Mult",  value=float(sr.ATR_TP),      step=0.5, min_value=1.0, max_value=10.0)

    run_btn = st.button("🚀 Run Backtest", use_container_width=True, type="primary")

    if run_btn:
        try:
            import vectorbt as vbt
        except ImportError:
            st.error("vectorbt not installed. Add it to requirements.txt")
            st.stop()

        with st.spinner(f"Running {period} backtest... (2-5 min)"):
            days    = sr.PERIOD_DAYS[period]
            df_1h   = sr.fetch_ohlcv("hour", 1,  days_back=days)
            df_4h   = sr.fetch_ohlcv("hour", 4,  days_back=days)
            liq_df  = sr.fetch_liquidations(max_days=min(days,30))
            fund_df = sr.fetch_funding()
            oi_df   = sr.fetch_oi()

            if df_1h.empty:
                st.error("No OHLCV data — check CRYPTOCOMPARE_API_KEY in Streamlit secrets")
                st.stop()

            df = sr.compute_indicators(df_1h, df_4h, liq_df, fund_df, oi_df)

            # Override config for this run
            orig_ms = sr.MIN_SCORE; orig_sl = sr.ATR_SL; orig_tp = sr.ATR_TP
            sr.MIN_SCORE = min_score; sr.ATR_SL = atr_sl; sr.ATR_TP = atr_tp
            df = sr.compute_scores(df)
            sr.MIN_SCORE = orig_ms; sr.ATR_SL = orig_sl; sr.ATR_TP = orig_tp

            n_sig = int(df["long_signal"].sum()) + int(df["short_signal"].sum())
            if n_sig == 0:
                st.warning(f"No signals generated — try lowering Min Score (currently {min_score})")
                st.stop()

            sl_stop = df["atr"] * atr_sl / df["close"]
            tp_stop = df["atr"] * atr_tp / df["close"]

            pf = vbt.Portfolio.from_signals(
                close=df["close"], high=df["high"], low=df["low"],
                entries=df["long_signal"], short_entries=df["short_signal"],
                sl_stop=sl_stop, tp_stop=tp_stop,
                init_cash=sr.INIT_CASH, fees=sr.FEES, slippage=sr.SLIPPAGE, freq="1h"
            )

            try:
                t = pf.trades.records_readable
                wins = len(t[t["PnL"]>0]); losses = len(t[t["PnL"]<0])
                wr   = wins/len(t) if len(t)>0 else 0
                aw   = float(t[t["PnL"]>0]["PnL"].mean()) if wins>0 else 0
                al   = float(t[t["PnL"]<0]["PnL"].mean()) if losses>0 else 0
                pfr  = abs(aw/al) if al!=0 else 0
                trades_df = t
            except:
                wr=aw=al=pfr=0; trades_df=pd.DataFrame()

            bh = float(df["close"].iloc[-1]/df["close"].iloc[0])-1
            result = {
                "run_id":       datetime.utcnow().strftime("%Y%m%d_%H%M%S"),
                "run_ts":       datetime.utcnow().isoformat(),
                "period":       period,
                "start":        str(df.index[0].date()),
                "end":          str(df.index[-1].date()),
                "total_return": round(float(pf.total_return()),4),
                "buy_and_hold": round(bh,4),
                "beat_bh":      bool(pf.total_return()>bh),
                "sharpe_ratio": round(float(pf.sharpe_ratio() or 0),3),
                "max_drawdown": round(float(pf.max_drawdown()),4),
                "total_trades": int(pf.trades.count()),
                "win_rate":     round(wr,4),
                "avg_win":      round(aw,2),
                "avg_loss":     round(al,2),
                "profit_factor":round(pfr,3),
                "final_value":  round(float(pf.final_value()),2),
                "init_cash":    sr.INIT_CASH,
                "config":       {"min_score":min_score,"atr_sl_mult":atr_sl,"atr_tp_mult":atr_tp,
                                  "min_categories":sr.MIN_CATS,"trend_required":sr.TREND_REQ,
                                  "enable_shorts":sr.ENABLE_SHORT,"cat_weights":sr.CAT_WEIGHTS},
                "trades_df":    trades_df,
                "equity":       pf.value(),
            }
            sr.save_to_memory({k:v for k,v in result.items() if k not in ("trades_df","equity")})
            st.session_state.backtest_result = result

    # ── Display results ──────────────────────────────────────────
    r = st.session_state.backtest_result
    if r:
        st.markdown(f"#### Results — {r['period']}  `{r['start']} → {r['end']}`")

        ret_color = "normal" if r["total_return"] > 0 else "inverse"
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Return",       f"{r['total_return']:.1%}", delta=f"B&H: {r['buy_and_hold']:.1%}")
        c2.metric("Sharpe",       f"{r['sharpe_ratio']:.3f}")
        c3.metric("Max DD",       f"{r['max_drawdown']:.1%}")
        c4.metric("Trades",       r["total_trades"])

        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Win Rate",     f"{r['win_rate']:.1%}")
        c2.metric("Profit Factor",f"{r['profit_factor']:.3f}")
        c3.metric("Avg Win",      f"${r['avg_win']:,.0f}")
        c4.metric("Avg Loss",     f"${r['avg_loss']:,.0f}")

        st.metric("Final Value", f"${r['final_value']:,.2f}",
                  delta=f"Started ${r['init_cash']:,.0f}")

        # Equity curve
        if "equity" in r and r["equity"] is not None:
            st.markdown("**Equity Curve**")
            eq = r["equity"].reset_index()
            eq.columns = ["Date", "Portfolio Value"]
            st.line_chart(eq.set_index("Date"), use_container_width=True)

        # Trade log
        if "trades_df" in r and not r["trades_df"].empty:
            with st.expander(f"📋 Trade Log ({r['total_trades']} trades)"):
                display_cols = [c for c in ["Direction","Entry Timestamp","Entry Price",
                                             "Exit Timestamp","Exit Price","PnL","Return"]
                                if c in r["trades_df"].columns]
                st.dataframe(r["trades_df"][display_cols] if display_cols else r["trades_df"],
                             use_container_width=True)


# ══════════════════════════════════════════════════════════════════
# TAB 3 — HISTORY
# ══════════════════════════════════════════════════════════════════

with tab_history:
    st.markdown("### Backtest History")
    mem = sr.load_memory()

    if not mem:
        st.info("No backtest runs yet. Go to Backtest tab and run one.")
    else:
        # Summary table
        rows = []
        for r in reversed(mem[-50:]):
            rows.append({
                "Run ID":      r.get("run_id",""),
                "Period":      r.get("period",""),
                "Return":      f"{r.get('total_return',0):.1%}",
                "Sharpe":      f"{r.get('sharpe_ratio',0):.3f}",
                "Max DD":      f"{r.get('max_drawdown',0):.1%}",
                "Trades":      r.get("total_trades",0),
                "Win Rate":    f"{r.get('win_rate',0):.1%}",
                "Beat B&H":    "✅" if r.get("beat_bh") else "❌",
                "min_score":   r.get("config",{}).get("min_score",""),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # Download memory as JSON
        st.download_button(
            "⬇️ Download Full History (JSON)",
            data=json.dumps(mem, indent=2, default=str),
            file_name="btc_backtest_memory.json",
            mime="application/json",
        )

        # Return distribution chart
        returns = [r.get("total_return",0) for r in mem]
        if len(returns) > 1:
            st.markdown("**Return Distribution**")
            st.bar_chart(pd.DataFrame({"Return": returns}))


# ══════════════════════════════════════════════════════════════════
# TAB 4 — LEARN
# ══════════════════════════════════════════════════════════════════

with tab_learn:
    st.markdown("### 🧠 Learning Report")
    mem = sr.load_memory()

    if not mem:
        st.info("Run at least 2-3 backtests to see learning insights.")
    else:
        ranked = sorted(mem, key=lambda x: x.get("sharpe_ratio",0), reverse=True)
        best   = ranked[0]
        worst  = ranked[-1]

        st.success(f"🥇 Best: {best['period']} | Return: {best['total_return']:.1%} | Sharpe: {best['sharpe_ratio']:.3f}")
        st.error(f"🔴 Worst: {worst['period']} | Return: {worst['total_return']:.1%} | Sharpe: {worst['sharpe_ratio']:.3f}")

        # Stats
        profitable = [r for r in mem if r["total_return"]>0]
        beat_bh    = [r for r in mem if r.get("beat_bh")]
        c1,c2,c3 = st.columns(3)
        c1.metric("Total Runs", len(mem))
        c2.metric("Profitable", f"{len(profitable)}/{len(mem)}")
        c3.metric("Beat B&H",   f"{len(beat_bh)}/{len(mem)}")

        # Period analysis
        st.markdown("**Average Sharpe by Period**")
        by_period = {}
        for r in mem:
            p = r["period"]
            if p not in by_period: by_period[p] = []
            by_period[p].append(r["sharpe_ratio"])
        period_df = pd.DataFrame([
            {"Period": p, "Avg Sharpe": round(sum(v)/len(v),3), "Runs": len(v)}
            for p, v in by_period.items()
        ]).sort_values("Avg Sharpe", ascending=False)
        st.dataframe(period_df, use_container_width=True, hide_index=True)

        # Min score sensitivity
        scores_tested = list(set(r["config"].get("min_score","") for r in mem if "config" in r))
        if len(scores_tested) > 1:
            st.markdown("**Min Score Sensitivity**")
            score_rows = []
            for sc in sorted([s for s in scores_tested if s]):
                runs = [r for r in mem if r.get("config",{}).get("min_score")==sc]
                if runs:
                    score_rows.append({
                        "Min Score":   sc,
                        "Avg Sharpe":  round(sum(r["sharpe_ratio"] for r in runs)/len(runs),3),
                        "Avg Return":  f"{sum(r['total_return'] for r in runs)/len(runs):.1%}",
                        "Runs":        len(runs),
                    })
            if score_rows:
                st.dataframe(pd.DataFrame(score_rows), use_container_width=True, hide_index=True)

        # Best config recommendation
        st.markdown("**💡 Recommended Config (from best run)**")
        bc = best.get("config",{})
        col1, col2 = st.columns(2)
        col1.code(f"""MIN_SCORE      = {bc.get('min_score','?')}
MIN_CATEGORIES = {bc.get('min_categories','?')}
TREND_REQUIRED = {bc.get('trend_required','?')}
ATR_SL_MULT    = {bc.get('atr_sl_mult','?')}
ATR_TP_MULT    = {bc.get('atr_tp_mult','?')}""")
        col2.metric("Expected Return", f"{best['total_return']:.1%}")
        col2.metric("Expected Sharpe", f"{best['sharpe_ratio']:.3f}")
