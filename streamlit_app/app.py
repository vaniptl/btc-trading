"""
streamlit_app/app.py — BTC Strategy Mobile Dashboard v8.5
Regime-Aware · 5-Signal Voting · Volume Profile · Intelligence Layer
"""

import os, sys, json, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from tab_paper_trading import render_paper_trading_tab

# ── Path setup — must come before any local imports ───────────────
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root / "core"))
sys.path.insert(0, str(_root / "intelligence"))
sys.path.insert(0, str(_root / "execution"))
sys.path.insert(0, str(_root))

import streamlit as st
import pandas as pd
import numpy as np

# ── Page config — MUST be first Streamlit call ────────────────────
st.set_page_config(
    page_title="BTC Strategy",
    page_icon="₿",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  .block-container { padding: 1rem 0.5rem; max-width: 100%; }
  .signal-card {
    border-radius: 16px; padding: 20px; margin: 8px 0;
    text-align: center; font-size: 1.4em; font-weight: bold;
  }
  .long-card  { background: linear-gradient(135deg,#00c853,#1b5e20); color: white; }
  .short-card { background: linear-gradient(135deg,#d50000,#b71c1c); color: white; }
  .none-card  { background: linear-gradient(135deg,#424242,#212121); color: #aaa; }
  .regime-card { border-radius: 12px; padding: 12px 16px; margin: 8px 0; }
  .score-bar-wrap { background: #1e1e1e; border-radius: 8px; height: 12px; margin: 4px 0; overflow: hidden; }
  .chip-active   { display:inline-block; background:#00c853; color:#000; border-radius:20px; padding:2px 10px; font-size:0.75em; margin:2px; font-weight:600; }
  .chip-inactive { display:inline-block; background:#2a2a2a; color:#555; border-radius:20px; padding:2px 10px; font-size:0.75em; margin:2px; }
  .chip-bear     { display:inline-block; background:#d50000; color:#fff; border-radius:20px; padding:2px 10px; font-size:0.75em; margin:2px; font-weight:600; }
  header { visibility: hidden; }
  .stDeployButton { display: none; }
  .stTabs [data-baseweb="tab"] { font-size: 0.85em; padding: 8px 12px; }
</style>
""", unsafe_allow_html=True)

import signal_runner as sr

# ── Try importing intelligence tab (graceful if unavailable) ──────
try:
    from tab_intelligence import render_intelligence_tab
    INTEL_TAB_AVAILABLE = True
except ImportError:
    INTEL_TAB_AVAILABLE = False

# ══════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════

REGIME_COLORS = {
    "STRONG_BULL": "#1b5e20", "WEAK_BULL": "#2e7d32",
    "BEAR":        "#7f0000", "RANGING":   "#1a237e",
}
REGIME_EMOJI = {
    "STRONG_BULL": "🚀", "WEAK_BULL": "📈",
    "BEAR":        "🐻", "RANGING":   "↔️", "UNKNOWN": "❓",
}

# ══════════════════════════════════════════════════════════════════
# SESSION STATE
# ══════════════════════════════════════════════════════════════════

for k, v in [
    ("signal", None), ("last_fetch", 0),
    ("backtest_result", None), ("backtest_trades_df", None), ("backtest_equity", None),
]:
    if k not in st.session_state:
        st.session_state[k] = v

# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

def pct_bar(pct, color):
    w = min(100, max(0, pct * 100))
    return (f'<div class="score-bar-wrap">'
            f'<div style="width:{w}%;background:{color};height:100%;border-radius:8px;"></div>'
            f'</div>')

def chip(label, active, bear=False):
    if active and bear: return f'<span class="chip-bear">{label}</span>'
    if active:          return f'<span class="chip-active">{label}</span>'
    return f'<span class="chip-inactive">{label}</span>'

# ══════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════

st.markdown("## ₿ BTC Strategy v8.5")
st.caption(f"Regime-Aware · 5-Signal Voting · Volume Profile · {datetime.now(timezone.utc).strftime('%H:%M UTC')}")

# ── Tab definition (5 tabs: Intelligence tab added) ───────────────
tab_signal, tab_backtest, tab_history, tab_learn, tab_intel, tab_paper = st.tabs(
    ["📡 Signal", "📊 Backtest", "📋 History", "🧠 Learn", "🔬 Intelligence", "📄 Paper Trading"]
)
# tab_signal, tab_backtest, tab_history, tab_learn, tab_intel = st.tabs(_tab_labels)


# ══════════════════════════════════════════════════════════════════
# TAB 1 — LIVE SIGNAL
# ══════════════════════════════════════════════════════════════════

with tab_signal:
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown("### Live Signal")
    with col2:
        refresh = st.button("🔄 Refresh", use_container_width=True)

    now = time.time()
    if refresh or (now - st.session_state.last_fetch > 60) or st.session_state.signal is None:
        with st.spinner("Fetching live data (1H + 4H + 1D)..."):
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
        rl_   = sig.get("reasons_long",  [])
        rs_   = sig.get("reasons_short", [])

        # ── Signal card ───────────────────────────────────────────
        if   d == "LONG":  card_cls, label = "long-card",  "🟢 LONG"
        elif d == "SHORT": card_cls, label = "short-card", "🔴 SHORT"
        else:              card_cls, label = "none-card",  "⚪ NO SIGNAL"

        st.markdown(f"""
        <div class="signal-card {card_cls}">
          {label}<br>
          <span style="font-size:1.8em">${price:,.2f}</span><br>
          <span style="font-size:0.6em;font-weight:normal">
            {sig["timestamp"][:16].replace("T"," ")} UTC
          </span>
        </div>
        """, unsafe_allow_html=True)

        # ── Execution approval badge (from intelligence layer) ────
        exec_decision = sig.get("execution_decision") or {}
        if exec_decision:
            if exec_decision.get("approved"):
                pos_pct = exec_decision.get("position_pct", 0)
                pos_usd = exec_decision.get("position_usd", 0)
                conf    = exec_decision.get("combined_conf", 0)
                st.success(
                    f"✅ **Execution Approved** — Confidence {conf:.0f}% | "
                    f"Position: {pos_pct:.1%} = ${pos_usd:,.0f}"
                )
            elif d != "NONE":
                reason = exec_decision.get("rejection_reason", "")
                st.warning(f"❌ **Execution Blocked:** {reason}")

        # ── Regime banner ─────────────────────────────────────────
        regime  = sig.get("regime", "")
        rcfg_d  = sig.get("regime_config", {})
        vp      = sig.get("volume_profile", {})
        h1_str  = sig.get("h1_structure", "")
        h4_str  = sig.get("h4_structure", "")
        bear_ov = sig.get("bear_override", "NONE")
        rcolor  = REGIME_COLORS.get(regime, "#333")
        remoji  = REGIME_EMOJI.get(regime, "")

        st.markdown(f"""
        <div class="regime-card" style="background:{rcolor};">
          <b style="color:white;font-size:1.15em">{remoji} {regime}</b><br>
          <span style="color:#ddd;font-size:0.82em">{rcfg_d.get("description","")}</span><br>
          <span style="color:#bbb;font-size:0.78em">
            4H: <b>{h4_str}</b> &nbsp;|&nbsp; 1H: <b>{h1_str}</b>
            &nbsp;|&nbsp; SL: <b>{rcfg_d.get("atr_sl_mult","?")}x ATR</b>
            &nbsp;|&nbsp; TP: <b>{rcfg_d.get("atr_tp_mult","?")}x ATR</b>
            &nbsp;|&nbsp; Min L/S: <b>{rcfg_d.get("min_score_long","?")}</b> / <b>{rcfg_d.get("min_score_short","?")}</b>
          </span>
        </div>
        """, unsafe_allow_html=True)

        if bear_ov and bear_ov != "NONE":
            st.warning(f"⚡ Bear Override Active: **{bear_ov}**")

        # ── Research sentiment badge ───────────────────────────────
        sentiment    = sig.get("sentiment", "")
        alignment    = sig.get("market_alignment", "")
        research_conf = sig.get("research_confidence", 0)
        if sentiment:
            sent_icon = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪", "MIXED": "🟡"}.get(sentiment, "⚪")
            align_icon = {"ALIGNED": "✅", "DIVERGENT": "⚠️", "NEUTRAL": "↔️"}.get(alignment, "↔️")
            st.caption(
                f"{sent_icon} Sentiment: **{sentiment}** &nbsp;|&nbsp; "
                f"{align_icon} Alignment: **{alignment}** &nbsp;|&nbsp; "
                f"Research confidence: **{research_conf:.0f}%**"
            )

        # ── Volume Profile ────────────────────────────────────────
        if vp and vp.get("poc"):
            poc_v = vp.get("poc", 0); vah_v = vp.get("vah", 0); val_v = vp.get("val", 0)
            vc1, vc2, vc3 = st.columns(3)
            vc1.metric("POC", f"${poc_v:,.0f}", help="Point of Control — highest volume price")
            vc2.metric("VAH", f"${vah_v:,.0f}", help="Value Area High — regime switch level")
            vc3.metric("VAL", f"${val_v:,.0f}", help="Value Area Low — downside target")
            above_vah = inds.get("above_vah", False)
            st.markdown(f"**{'🟢 Above VAH — BH Bullish Zone' if above_vah else '🔴 Below VAH — BH Bearish Zone'}**")

        # ── Scores ────────────────────────────────────────────────
        st.markdown("**Score Breakdown**")
        max_possible = 15.0
        col_l, col_s = st.columns(2)
        with col_l:
            st.caption(f"🟢 Long: {ls:.2f}")
            st.markdown(pct_bar(ls / max_possible, "#00c853"), unsafe_allow_html=True)
        with col_s:
            st.caption(f"🔴 Short: {ss:.2f}")
            st.markdown(pct_bar(ss / max_possible, "#d50000"), unsafe_allow_html=True)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Trend L",  f"{cats['trend']['long']:.1f}")
        c2.metric("Zone L",   f"{cats['zone']['long']:.1f}")
        c3.metric("Mom L",    f"{cats['momentum']['long']:.1f}")
        c4.metric("Pos L",    f"{cats['positioning']['long']:.1f}")
        c1.metric("Trend S",  f"{cats['trend']['short']:.1f}")
        c2.metric("Zone S",   f"{cats['zone']['short']:.1f}")
        c3.metric("Mom S",    f"{cats['momentum']['short']:.1f}")
        c4.metric("Pos S",    f"{cats['positioning']['short']:.1f}")

        # ── Indicator chips ───────────────────────────────────────
        st.markdown("**Active Indicators**")
        is_bear_sig = d == "SHORT"
        all_inds = [
            ("4H Bull",      "4H Bullish"     in rl_),
            ("4H Bear",      "4H Bearish"     in rs_),
            ("EMA Ribbon ↑", "EMA Ribbon Bull" in rl_),
            ("EMA Ribbon ↓", "EMA Ribbon Bear" in rs_),
            ("Above EMA200", "Above EMA200"   in rl_),
            ("Above VAH",    "Above VAH"      in rl_),
            ("Near POC",     "Near POC"       in rl_ + rs_),
            ("AVWAP Bull",   "AVWAP Bull"     in rl_),
            ("Near Support", "Near Support"   in rl_),
            ("Near Resist",  "Near Resistance" in rs_),
            ("Fib 0.618",    "Fib 0.618"      in rl_ + rs_),
            ("Fib 0.500",    "Fib 0.500"      in rl_ + rs_),
            ("Fib 0.382",    "Fib 0.382"      in rl_ + rs_),
            ("Bullish OB",   "Bullish OB"     in rl_),
            ("Bearish OB",   "Bearish OB"     in rs_),
            ("RSI Bull Div", "RSI Bull Div"   in rl_),
            ("RSI Hid Bull", "RSI Hidden Bull" in rl_),
            ("MFI Bull",     any("MFI" in x and "Bull" in x for x in rl_)),
            ("Stoch Bull",   any("Stoch" in x for x in rl_)),
            ("Bull Flag",    inds.get("bull_flag",    False)),
            ("CVD Bull",     "CVD Bull Div"   in rl_),
            ("Real Buying",  inds.get("real_buying",  False)),
            ("Vol Confirm",  inds.get("vol_ratio",    0) > 1),
            ("Long Liq",     "Long Liq"       in rl_),
            ("Funding Short","Funding Short"  in rl_),
            ("OI Long",      "OI Long"        in rl_),
        ]
        html = "".join(chip(name, active, bear=is_bear_sig and active) + " " for name, active in all_inds)
        st.markdown(html, unsafe_allow_html=True)

        st.markdown("---")
        ci1, ci2, ci3, ci4, ci5 = st.columns(5)
        ci1.metric("RSI",       f"{inds.get('rsi',       0):.1f}")
        ci2.metric("MFI",       f"{inds.get('mfi',       0):.1f}")
        ci3.metric("Stoch K",   f"{inds.get('stoch_k',   0):.1f}")
        ci4.metric("Vol Ratio", f"{inds.get('vol_ratio', 0):.2f}x")
        ci5.metric("ATR",       f"${inds.get('atr',      0):,.0f}")
        st.markdown(
            f"EMA50: **${inds.get('ema50',0):,.0f}** &nbsp;|&nbsp; "
            f"EMA200: **${inds.get('ema200',0):,.0f}** &nbsp;|&nbsp; "
            f"VWAP: **${inds.get('vwap',0):,.0f}** &nbsp;|&nbsp; "
            f"Bull Flag: **{'🚩 YES' if inds.get('bull_flag') else 'No'}**"
        )

        # ── Regime reasoning ──────────────────────────────────────
        with st.expander("🧠 Regime Detection Reasoning"):
            for line in sig.get("regime_reasons", []):
                st.caption(line)

        # ── Entry plan ────────────────────────────────────────────
        if ep:
            st.markdown(f"**{d} Entry Plan** *(regime-adjusted)*")
            col1, col2, col3 = st.columns(3)
            col1.metric("Entry",       f"${ep['entry']:,.0f}")
            col2.metric("Stop Loss",   f"${ep['stop_loss']:,.0f}")
            col3.metric("Take Profit", f"${ep['take_profit']:,.0f}")
            st.info(
                f"R:R Ratio — 1:{ep['rr']:.1f}  |  "
                f"SL {rcfg_d.get('atr_sl_mult','?')}x ATR  |  "
                f"TP {rcfg_d.get('atr_tp_mult','?')}x ATR"
            )

        # ── Price chart ───────────────────────────────────────────
        with st.expander("📈 Price Chart (1H)"):
            df_chart = sig.get("df")
            if df_chart is not None and not df_chart.empty:
                tail = df_chart.tail(100)
                st.line_chart(pd.DataFrame({
                    "Close": tail["close"], "EMA21": tail["ema21"],
                    "EMA55": tail["ema55"], "EMA200": tail["ema200"],
                }), use_container_width=True)
                st.markdown("**RSI**")
                st.line_chart(pd.DataFrame({"RSI": tail["rsi"]}),
                              use_container_width=True, height=120)
                st.markdown("**MFI**")
                st.line_chart(pd.DataFrame({"MFI": tail["mfi"]}),
                              use_container_width=True, height=120)

    elif sig and "error" in sig:
        st.error(f"Error: {sig['error']}")
    else:
        st.info("Click Refresh to fetch live signal")


# ══════════════════════════════════════════════════════════════════
# TAB 2 — BACKTEST
# ══════════════════════════════════════════════════════════════════

with tab_backtest:
    st.markdown("### Run Backtest")

    period = st.select_slider("Select Period",
                               options=["1M", "3M", "6M", "1Y", "3Y"], value="1Y")
    tf_choice   = st.radio("Timeframe",
                            options=["1H — Hourly candles (more signals)", "1D — Daily candles (swing)"],
                            index=0, label_visibility="collapsed")
    backtest_tf = "1D" if tf_choice.startswith("1D") else "1H"

    st.markdown("**Parameters** *(regime auto-adjusts SL/TP)*")
    c1, c2, c3 = st.columns(3)
    min_score = c1.number_input("Min Score",   value=float(sr.MIN_SCORE), step=0.5, min_value=1.0, max_value=15.0)
    atr_sl    = c2.number_input("ATR SL Mult", value=float(sr.ATR_SL),   step=0.5, min_value=0.5, max_value=5.0)
    atr_tp    = c3.number_input("ATR TP Mult", value=float(sr.ATR_TP),   step=0.5, min_value=1.0, max_value=10.0)

    run_btn = st.button(f"🚀 Run Backtest  [{period} · {backtest_tf}]",
                         use_container_width=True, type="primary")

    if run_btn:
        try:
            import vectorbt as vbt
        except ImportError:
            st.error("vectorbt not installed — add it to requirements.txt"); st.stop()

        with st.spinner(f"Running {period} {backtest_tf} backtest… (1–3 min)"):
            days    = sr.PERIOD_DAYS[period]
            df_1h   = sr.fetch_ohlcv("hour", 1, days_back=days + 30)
            df_4h   = sr.fetch_ohlcv("hour", 4, days_back=days + 60)
            df_1d   = sr.fetch_ohlcv("day",  1, days_back=days + 250)
            liq_df  = sr.fetch_liquidations(max_days=min(days, 30))
            fund_df = sr.fetch_funding()
            oi_df   = sr.fetch_oi()

            if df_1h.empty:
                st.error("No OHLCV data — Binance API unreachable."); st.stop()

            df = sr.compute_indicators(df_1h, df_4h, df_1d, liq_df, fund_df, oi_df)
            regime, regime_reasons = sr.detect_regime(df, df_4h_ext=df_4h, df_1d_ext=df_1d)

            if not df_4h.empty:
                df_4h_ind = df_4h.copy()
                for col, span in [("ema8",8),("ema21",21),("ema55",55)]:
                    df_4h_ind[col] = sr._ema(df_4h_ind["close"], span)
                df_4h_ind["rsi"] = sr._rsi(df_4h_ind["close"])
                df_4h_ind["atr"] = sr._atr(df_4h_ind)
                h4_str = sr.detect_local_structure(df_4h_ind, lookback_bars=30, tf_label="4H")
            else:
                h4_str = "ranging"
            h1_str = sr.detect_local_structure(df, lookback_bars=48, tf_label="1H")

            df = sr.compute_scores_regime(df, regime, sr.MIN_CATS, sr.TREND_REQ,
                                           h1_structure=h1_str, h4_structure=h4_str)

            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)
            df_bt  = df[df.index >= cutoff].copy()
            if backtest_tf == "1D":
                df_bt = df_bt.resample("1D").agg({
                    "open":"first","high":"max","low":"min","close":"last",
                    "volume":"sum","atr":"last","long_signal":"any","short_signal":"any",
                }).dropna(subset=["close"])

            if df_bt.empty or df_bt["long_signal"].sum() + df_bt["short_signal"].sum() == 0:
                st.warning("No signals. Try lowering Min Score or extending period."); st.stop()

            rcfg   = sr.get_regime_config(regime)
            sl_act = rcfg["atr_sl_mult"]
            tp_act = rcfg["atr_tp_mult"]
            sl_stop = df_bt["atr"] * sl_act / df_bt["close"]
            tp_stop = df_bt["atr"] * tp_act / df_bt["close"]
            vbt_freq = "1D" if backtest_tf == "1D" else "1h"

            pf = vbt.Portfolio.from_signals(
                close=df_bt["close"], high=df_bt["high"], low=df_bt["low"],
                entries=df_bt["long_signal"], short_entries=df_bt["short_signal"],
                sl_stop=sl_stop, tp_stop=tp_stop,
                init_cash=sr.INIT_CASH, fees=sr.FEES, slippage=sr.SLIPPAGE, freq=vbt_freq,
            )

            # Trade stats — normalise column names across vbt versions
            try:
                trades_df = pf.trades.records_readable.copy()
                col_lower = {c: c.lower().replace(" ","_").replace("&","") for c in trades_df.columns}
                trades_df.rename(columns=col_lower, inplace=True)
                pnl_col = next((c for c in trades_df.columns
                                if c in ("pnl","pl","p_l","realized_pnl","profit")), None)
                if pnl_col:
                    pnl_s  = pd.to_numeric(trades_df[pnl_col], errors="coerce").dropna()
                    wins   = pnl_s[pnl_s > 0]; losses = pnl_s[pnl_s < 0]
                    wr     = len(wins) / len(pnl_s) if len(pnl_s) > 0 else 0
                    aw     = float(wins.mean())   if len(wins)   > 0 else 0
                    al     = float(losses.mean()) if len(losses) > 0 else 0
                    pfr    = abs(aw / al) if al != 0 else 0
                else:
                    wr = aw = al = pfr = 0
            except:
                trades_df = pd.DataFrame()
                wr = aw = al = pfr = 0

            bh = float(df_bt["close"].iloc[-1] / df_bt["close"].iloc[0]) - 1

            result = {
                "run_id":        f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{period}_{backtest_tf}",
                "period":        period, "timeframe":     backtest_tf,
                "regime":        regime, "h4_structure":  h4_str, "h1_structure":  h1_str,
                "start":         str(df_bt.index[0].date()), "end": str(df_bt.index[-1].date()),
                "total_return":  round(float(pf.total_return()), 4),
                "buy_and_hold":  round(bh, 4), "beat_bh": bool(pf.total_return() > bh),
                "sharpe_ratio":  round(float(pf.sharpe_ratio()  or 0), 3),
                "sortino_ratio": round(float(pf.sortino_ratio() or 0), 3),
                "max_drawdown":  round(float(pf.max_drawdown()), 4),
                "total_trades":  int(pf.trades.count()),
                "win_rate":      round(wr, 4), "avg_win": round(aw, 2), "avg_loss": round(al, 2),
                "profit_factor": round(pfr, 3), "final_value": round(float(pf.final_value()), 2),
                "init_cash":     sr.INIT_CASH,
                "config": {"min_score": min_score, "min_categories": sr.MIN_CATS,
                           "trend_required": sr.TREND_REQ, "atr_sl_mult": atr_sl,
                           "atr_tp_mult": atr_tp, "enable_shorts": sr.ENABLE_SHORT},
            }
            sr.save_to_memory(result)
            st.session_state.backtest_result    = result
            st.session_state.backtest_trades_df = trades_df
            st.session_state.backtest_equity    = pf.value()

    r = st.session_state.backtest_result
    if r:
        rem = REGIME_EMOJI.get(r.get("regime",""), "❓")
        st.markdown(f"#### Results — {r['period']} · {r.get('timeframe','1H')}  `{r['start']} → {r['end']}`")
        st.caption(f"{rem} Regime: **{r.get('regime','')}**  |  4H: {r.get('h4_structure','')}  |  1H: {r.get('h1_structure','')}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Return",  f"{r['total_return']:.1%}", delta=f"B&H: {r['buy_and_hold']:.1%}")
        c2.metric("Sharpe",  f"{r['sharpe_ratio']:.3f}")
        c3.metric("Max DD",  f"{r['max_drawdown']:.1%}")
        c4.metric("Trades",  r["total_trades"])
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Win Rate",      f"{r['win_rate']:.1%}")
        c2.metric("Profit Factor", f"{r['profit_factor']:.3f}")
        c3.metric("Avg Win",       f"${r['avg_win']:,.0f}")
        c4.metric("Avg Loss",      f"${r['avg_loss']:,.0f}")
        st.metric("Final Value", f"${r['final_value']:,.2f}", delta=f"Started ${r['init_cash']:,.0f}")

        eq = st.session_state.get("backtest_equity")
        if eq is not None:
            st.markdown("**Equity Curve**")
            eq_df = eq.reset_index(); eq_df.columns = ["Date", "Portfolio Value"]
            st.line_chart(eq_df.set_index("Date"), use_container_width=True)

        tdf = st.session_state.get("backtest_trades_df")
        if tdf is not None and not tdf.empty:
            with st.expander(f"📋 Trade Log ({r['total_trades']} trades)"):
                display_cols = [c for c in ["direction","entry_timestamp","entry_price",
                                "exit_timestamp","exit_price","pnl","return"] if c in tdf.columns]
                st.dataframe(tdf[display_cols] if display_cols else tdf, use_container_width=True)


# ══════════════════════════════════════════════════════════════════
# TAB 3 — HISTORY
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
                "Run ID":   r.get("run_id", ""), "Period": r.get("period", ""),
                "TF":       r.get("timeframe", "1H"),
                "Regime":   f"{REGIME_EMOJI.get(r.get('regime',''), '❓')} {r.get('regime','')}",
                "Return":   f"{r.get('total_return', 0):.1%}",
                "Sharpe":   f"{r.get('sharpe_ratio', 0):.3f}",
                "Max DD":   f"{r.get('max_drawdown', 0):.1%}",
                "Trades":   r.get("total_trades", 0),
                "Win Rate": f"{r.get('win_rate', 0):.1%}",
                "Beat B&H": "✅" if r.get("beat_bh") else "❌",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.download_button("⬇️ Download History (JSON)",
                           data=json.dumps(mem, indent=2, default=str),
                           file_name="btc_backtest_memory_v8.json", mime="application/json")


# ══════════════════════════════════════════════════════════════════
# TAB 4 — LEARN
# ══════════════════════════════════════════════════════════════════

with tab_learn:
    st.markdown("### 🧠 Learning Report")
    mem = sr.load_memory()
    if not mem:
        st.info("Run at least 2–3 backtests to see learning insights.")
    else:
        st.text(sr.get_learning_summary())
        by_regime = {}
        for r in mem:
            rg = r.get("regime", "UNKNOWN")
            if rg not in by_regime: by_regime[rg] = []
            by_regime[rg].append(r)
        if by_regime:
            st.markdown("**📊 Performance by Regime**")
            regime_rows = []
            for rg, runs in sorted(by_regime.items()):
                regime_rows.append({
                    "Regime":     f"{REGIME_EMOJI.get(rg,'?')} {rg}",
                    "Runs":       len(runs),
                    "Avg Return": f"{sum(r['total_return'] for r in runs)/len(runs):.1%}",
                    "Avg Sharpe": f"{sum(r['sharpe_ratio'] for r in runs)/len(runs):.3f}",
                    "Win Rate":   f"{sum(r['win_rate'] for r in runs)/len(runs):.1%}",
                    "Beat B&H":   f"{sum(1 for r in runs if r.get('beat_bh'))}/{len(runs)}",
                })
            st.dataframe(pd.DataFrame(regime_rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════
# TAB 5 — INTELLIGENCE
# ══════════════════════════════════════════════════════════════════

with tab_intel:
    if INTEL_TAB_AVAILABLE:
        sig_for_intel = st.session_state.signal
        intel_data    = (sig_for_intel or {}).get("intelligence") if sig_for_intel else None
        render_intelligence_tab(sig=sig_for_intel, intel=intel_data)
    else:
        st.markdown("### 🔬 Intelligence Layer")
        st.info(
            "Intelligence tab unavailable — `tab_intelligence.py` not found in "
            "`streamlit_app/`. Make sure it's committed to your repo."
        )
        st.markdown("""
        **What this tab shows when active:**
        - 📡 **Research** — sentiment from Twitter, Reddit, 7 RSS feeds
        - 🎯 **Execution Gate** — confidence breakdown + position sizing
        - ⚗️ **Postmortems** — 5-agent loss autopsy history
        - 🎰 **Polymarket** — BTC prediction market edge analysis

        To enable: commit `intelligence/` and `execution/` folders to your repo,
        and add `feedparser` and `vaderSentiment` to `requirements.txt`.
        """)


with tab_paper:
    render_paper_trading_tab()
