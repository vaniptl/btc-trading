"""
streamlit_app/app.py — Trinity.AI BTC Strategy v8.5
FIXES:
  ✅ Recommendation panel always shown (BUY/SELL/HOLD even with NO SIGNAL)
  ✅ Score progress bars show % toward threshold
  ✅ Intelligence tab with Research/Execution/Polymarket
  ✅ Backtest history saved to session_state (survives page refresh)
  ✅ History tab shows backtest runs (not signal history)
  ✅ All 6 tabs: Signal · Backtest · History · Learn · Intelligence · Paper Trading
  ✅ Diagnostic panel when data fetch fails
"""

import os, sys, json, time
from pathlib import Path
from datetime import datetime, timezone, timedelta

_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root / "core"))
sys.path.insert(0, str(_root / "streamlit_app"))
sys.path.insert(0, str(_root / "intelligence"))
sys.path.insert(0, str(_root / "execution"))

import streamlit as st
import pandas as pd
import numpy as np

st.set_page_config(
    page_title="BTC Strategy v8.5",
    page_icon="₿",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  .block-container { padding: 0.8rem 0.5rem; max-width: 100%; }
  .signal-card { border-radius: 16px; padding: 20px; margin: 8px 0;
    text-align: center; font-size: 1.4em; font-weight: bold; }
  .long-card  { background: linear-gradient(135deg,#00c853,#1b5e20); color:white; }
  .short-card { background: linear-gradient(135deg,#d50000,#b71c1c); color:white; }
  .none-card  { background: linear-gradient(135deg,#424242,#212121); color:#aaa; }
  .rec-card   { border-radius:12px; padding:14px 18px; margin:8px 0; border:2px solid; }
  .rec-buy    { border-color:#00c853; background:#0a1f10; }
  .rec-sell   { border-color:#d50000; background:#1f0a0a; }
  .rec-hold   { border-color:#f9a825; background:#1f1a09; }
  .rec-wait   { border-color:#555; background:#1a1a1a; }
  .regime-card { border-radius:12px; padding:12px 16px; margin:8px 0; }
  .chip-active  { display:inline-block; background:#00c853; color:#000;
    border-radius:20px; padding:2px 10px; font-size:0.75em; margin:2px; font-weight:600; }
  .chip-inactive{ display:inline-block; background:#2a2a2a; color:#555;
    border-radius:20px; padding:2px 10px; font-size:0.75em; margin:2px; }
  .chip-bear    { display:inline-block; background:#d50000; color:#fff;
    border-radius:20px; padding:2px 10px; font-size:0.75em; margin:2px; font-weight:600; }
  .score-bar-wrap { background:#1e1e1e; border-radius:8px; height:12px; margin:4px 0; overflow:hidden; }
  header { visibility:hidden; } .stDeployButton { display:none; }
  .stTabs [data-baseweb="tab"] { font-size:0.82em; padding:6px 10px; }
</style>
""", unsafe_allow_html=True)

import signal_runner as sr

# ── Constants ─────────────────────────────────────────────────────
REGIME_COLORS = {
    "STRONG_BULL":"#1b5e20","WEAK_BULL":"#2e7d32","BEAR":"#7f0000","RANGING":"#1a237e"
}
REGIME_EMOJI = {"STRONG_BULL":"🚀","WEAK_BULL":"📈","BEAR":"🐻","RANGING":"↔️","UNKNOWN":"❓"}
REC_COLORS = {
    "STRONG_BUY":"#00c853","BUY":"#43a047","HOLD":"#f9a825",
    "SELL":"#ef5350","STRONG_SELL":"#b71c1c","WAIT":"#78909c"
}
REC_ICONS = {
    "STRONG_BUY":"🚀 STRONG BUY","BUY":"📈 BUY",
    "HOLD":"⏸ HOLD","SELL":"📉 SELL",
    "STRONG_SELL":"🔴 STRONG SELL","WAIT":"⏳ WAIT"
}

# ── Session state ─────────────────────────────────────────────────
for k,v in [("signal",None),("last_fetch",0),("backtest_runs",[]),("diag",None)]:
    if k not in st.session_state: st.session_state[k]=v

def pct_bar(pct, color):
    w=min(100,max(0,pct*100))
    return f'<div class="score-bar-wrap"><div style="width:{w}%;background:{color};height:100%;border-radius:8px;"></div></div>'

def chip(label, active, bear=False):
    if active and bear: return f'<span class="chip-bear">{label}</span>'
    if active: return f'<span class="chip-active">{label}</span>'
    return f'<span class="chip-inactive">{label}</span>'


# ══════════════════════════════════════════════════════════════════
# HEADER + TABS
# ══════════════════════════════════════════════════════════════════

st.markdown("## ₿ BTC Strategy v8.5")
st.caption(f"Regime-Aware · 5-Signal Voting · Volume Profile | {datetime.utcnow().strftime('%H:%M UTC')}")

tab_signal, tab_backtest, tab_history, tab_learn, tab_intel, tab_paper = st.tabs(
    ["📡 Signal","📊 Backtest","📋 History","🧠 Learn","🔬 Intelligence","📄 Paper Trading"])


# ══════════════════════════════════════════════════════════════════
# TAB 1 — LIVE SIGNAL
# ══════════════════════════════════════════════════════════════════

with tab_signal:
    col1, col2 = st.columns([3,1])
    with col1: st.markdown("### Live Signal")
    with col2: refresh = st.button("🔄 Refresh", use_container_width=True)

    now = time.time()
    if refresh or (now - st.session_state.last_fetch > 300) or st.session_state.signal is None:
        with st.spinner("Fetching live data (1H + 4H + 1D)..."):
            sig = sr.get_signal()
            st.session_state.signal     = sig
            st.session_state.last_fetch = now

    sig = st.session_state.signal

    if sig and "error" not in sig:
        d       = sig["direction"]
        price   = sig["price"]
        ls      = sig["long_score"]
        ss      = sig["short_score"]
        cats    = sig["categories"]
        ep      = sig.get("entry_plan")
        inds    = sig.get("indicators", {})
        rl_     = sig.get("reasons_long", [])
        rs_     = sig.get("reasons_short", [])
        rec     = sig.get("recommendation", {})

        # ── Signal card ───────────────────────────────────────────
        if   d=="LONG":  card_cls="long-card";  label="🟢 LONG"
        elif d=="SHORT": card_cls="short-card";  label="🔴 SHORT"
        else:            card_cls="none-card";   label="⚪ NO SIGNAL"
        ts_ = sig["timestamp"][:16].replace("T"," ")

        st.markdown(f"""
        <div class="signal-card {card_cls}">
          {label}<br>
          <span style="font-size:1.8em">${price:,.2f}</span><br>
          <span style="font-size:0.6em;font-weight:normal">{ts_} UTC</span>
        </div>""", unsafe_allow_html=True)

        # ── RECOMMENDATION — always shown ─────────────────────────
        action = rec.get("action","WAIT")
        rc_color = REC_COLORS.get(action,"#78909c")
        rc_icon  = REC_ICONS.get(action,"⏳ WAIT")
        rc_class = {"STRONG_BUY":"rec-buy","BUY":"rec-buy","HOLD":"rec-hold",
                    "SELL":"rec-sell","STRONG_SELL":"rec-sell","WAIT":"rec-wait"}.get(action,"rec-wait")
        rec_reasons = rec.get("reasons",[])
        min_l = rec.get("min_long_threshold", sr.MIN_SCORE)
        min_s = rec.get("min_short_threshold", sr.MIN_SCORE)
        pct_l = rec.get("pct_to_long_signal", 0)
        pct_s = rec.get("pct_to_short_signal", 0)

        st.markdown(f"""
        <div class="rec-card {rc_class}">
          <b style="font-size:1.25em;color:{rc_color}">{rc_icon}</b><br>
          <span style="font-size:0.88em;color:#ccc">
            {"  ·  ".join(rec_reasons[:3]) if rec_reasons else "Analysing market conditions..."}
          </span>
        </div>""", unsafe_allow_html=True)

        # Score progress toward threshold
        sc1, sc2 = st.columns(2)
        with sc1:
            st.markdown(f"**Long: {ls:.2f} / {min_l}** &nbsp;({pct_l:.0f}%)")
            st.markdown(pct_bar(pct_l/100, "#00c853"), unsafe_allow_html=True)
        with sc2:
            st.markdown(f"**Short: {ss:.2f} / {min_s}** &nbsp;({pct_s:.0f}%)")
            st.markdown(pct_bar(pct_s/100, "#d50000"), unsafe_allow_html=True)

        # ── Regime ────────────────────────────────────────────────
        regime   = sig.get("regime","")
        rcfg_d   = sig.get("regime_config",{})
        vp       = sig.get("volume_profile",{})
        h1_str   = sig.get("h1_structure","")
        h4_str   = sig.get("h4_structure","")
        bear_ov  = sig.get("bear_override","NONE")
        rcolor   = REGIME_COLORS.get(regime,"#333")
        remoji   = REGIME_EMOJI.get(regime,"")

        st.markdown(f"""
        <div class="regime-card" style="background:{rcolor};">
          <b style="color:white;font-size:1.1em">{remoji} {regime}</b><br>
          <span style="color:#ddd;font-size:0.82em">{rcfg_d.get("description","")}</span><br>
          <span style="color:#bbb;font-size:0.78em">
            4H: <b>{h4_str}</b> | 1H: <b>{h1_str}</b> |
            SL: <b>{rcfg_d.get("atr_sl_mult","?")}×ATR</b> |
            TP: <b>{rcfg_d.get("atr_tp_mult","?")}×ATR</b> |
            Gate L:<b>{rcfg_d.get("min_score_long","?")}</b> S:<b>{rcfg_d.get("min_score_short","?")}</b>
          </span>
        </div>""", unsafe_allow_html=True)

        if bear_ov != "NONE":
            st.warning(f"⚡ Bear Override: **{bear_ov}** — MR longs active at support")

        # ── Volume Profile ────────────────────────────────────────
        st.markdown("**📦 Volume Profile**")
        v1,v2,v3=st.columns(3)
        v1.metric("POC",f"${vp.get('poc',0):,.0f}")
        v2.metric("VAH",f"${vp.get('vah',0):,.0f}")
        v3.metric("VAL",f"${vp.get('val',0):,.0f}")
        above_vah=inds.get("above_vah",False)
        st.markdown(f"**{'🟢 Above VAH — BH Bullish Zone' if above_vah else '🔴 Below VAH — BH Bearish Zone'}**")

        # ── Category breakdown ────────────────────────────────────
        st.markdown("**Category Breakdown**")
        st.dataframe(pd.DataFrame({
            "Category":["📈 Trend","📍 Zone","⚡ Momentum","🎯 Positioning"],
            "Long": [cats["trend"]["long"], cats["zone"]["long"], cats["momentum"]["long"], cats["positioning"]["long"]],
            "Short":[cats["trend"]["short"],cats["zone"]["short"],cats["momentum"]["short"],cats["positioning"]["short"]],
        }), use_container_width=True, hide_index=True)

        # ── Active indicators ─────────────────────────────────────
        active_reasons = rl_ if d=="LONG" else rs_
        if active_reasons:
            st.markdown(f"**Active Signals ({len(active_reasons)})**")
            chips_html=" ".join(chip(r,True,bear=d=="SHORT") for r in active_reasons)
            st.markdown(chips_html, unsafe_allow_html=True)

        # ── All indicator status ──────────────────────────────────
        with st.expander("🔬 All Indicators"):
            all_inds=[
                ("4H Bias Bull",    "4H Bullish" in rl_),
                ("4H Bias Bear",    "4H Bearish" in rs_),
                ("EMA Ribbon Bull", "EMA Ribbon Bull" in rl_),
                ("Above EMA200",    price>inds.get("ema200",0)),
                ("Above VAH",       above_vah),
                ("Near POC",        "Near POC" in rl_+rs_),
                ("Near VAL",        "Near VAL" in rl_),
                ("AVWAP Bull",      "AVWAP Bull" in rl_),
                ("Near Support",    "Near Support" in rl_),
                ("Near Resistance", "Near Resistance" in rs_),
                ("Fib 0.618",       "Fib 0.618" in rl_+rs_),
                ("RSI Bull Div",    "RSI Bull Div" in rl_),
                ("RSI Hidden Bull", "RSI Hidden Bull" in rl_),
                ("MFI Bull",        any(x in str(rl_) for x in ["MFI"])),
                ("Stoch Bull",      any("Stoch" in r for r in rl_)),
                ("Bull Flag",       inds.get("bull_flag",False)),
                ("CVD Bull Div",    "CVD Bull Div" in rl_),
                ("Real Buying",     inds.get("real_buying",False)),
                ("Bullish OB",      "Bullish OB" in rl_),
                ("Funding Short",   "Funding Short" in rl_),
            ]
            html="".join(chip(n,a)+" " for n,a in all_inds)
            st.markdown(html, unsafe_allow_html=True)
            st.markdown("---")
            c1,c2,c3,c4,c5=st.columns(5)
            c1.metric("RSI",    f"{inds.get('rsi',0):.1f}")
            c2.metric("MFI",    f"{inds.get('mfi',0):.1f}")
            c3.metric("Stoch",  f"{inds.get('stoch_k',0):.1f}")
            c4.metric("Vol×",   f"{inds.get('vol_ratio',0):.2f}")
            c5.metric("ATR",    f"${inds.get('atr',0):,.0f}")
            st.markdown(
                f"EMA50: **${inds.get('ema50',0):,.0f}** | "
                f"EMA200: **${inds.get('ema200',0):,.0f}** | "
                f"VWAP: **${inds.get('vwap',0):,.0f}** | "
                f"Bull Flag: **{'🚩' if inds.get('bull_flag') else 'No'}**"
            )

        # ── Regime reasoning ──────────────────────────────────────
        with st.expander("🧠 Regime Detection Reasoning"):
            for line in sig.get("regime_reasons",[]):
                st.caption(line)

        # ── Entry plan ────────────────────────────────────────────
        if ep:
            st.markdown(f"**{d} Entry Plan** *(regime-adjusted)*")
            e1,e2,e3=st.columns(3)
            e1.metric("Entry",       f"${ep['entry']:,.0f}")
            e2.metric("Stop Loss",   f"${ep['stop_loss']:,.0f}")
            e3.metric("Take Profit", f"${ep['take_profit']:,.0f}")
            st.info(f"R:R = 1:{ep['rr']:.1f}  |  SL {rcfg_d.get('atr_sl_mult','?')}×ATR  |  TP {rcfg_d.get('atr_tp_mult','?')}×ATR")

        # ── Price chart ───────────────────────────────────────────
        with st.expander("📈 Price Chart (1H)"):
            df_chart=sig.get("df")
            if df_chart is not None and not df_chart.empty:
                tail=df_chart.tail(100)
                st.line_chart(pd.DataFrame({
                    "Close":tail["close"],"EMA21":tail["ema21"],"EMA55":tail["ema55"],"EMA200":tail["ema200"]
                }), use_container_width=True)
                st.markdown("**RSI**")
                st.line_chart(pd.DataFrame({"RSI":tail["rsi"]}),use_container_width=True,height=120)
                st.markdown("**MFI**")
                st.line_chart(pd.DataFrame({"MFI":tail["mfi"]}),use_container_width=True,height=120)

    elif sig and "error" in sig:
        st.error(f"❌ {sig['error']}")
        if st.button("🔍 Run Diagnostics"):
            with st.spinner("Testing API connections..."):
                diag = sr._diagnose_fetch()
                st.session_state.diag = diag

        if st.session_state.diag:
            diag = st.session_state.diag
            st.markdown("### 🔍 Connection Status")
            c1,c2,c3=st.columns(3)
            c1.markdown(f"**Binance (data-api)**\n{diag.get('binance_data_api','?')}")
            c2.markdown(f"**Binance (main)**\n{diag.get('binance_main','?')}")
            c3.markdown(f"**CryptoCompare**\n{diag.get('cryptocompare','?')}")
            if not diag.get("api_key_set"):
                st.warning(
                    "**CRYPTOCOMPARE_API_KEY not set.**\n\n"
                    "Streamlit Cloud → App Settings → Secrets:\n"
                    "```toml\nCRYPTOCOMPARE_API_KEY = \"your_key_here\"\n```\n"
                    "Free key: https://www.cryptocompare.com/cryptopian/api-keys"
                )
    else:
        st.info("Click 🔄 Refresh to fetch live signal")


# ══════════════════════════════════════════════════════════════════
# TAB 2 — BACKTEST
# ══════════════════════════════════════════════════════════════════

with tab_backtest:
    st.markdown("### Run Backtest")

    period = st.select_slider("Period",options=["1M","3M","6M","1Y"],value="3M")
    tf_col1,tf_col2=st.columns(2)
    with tf_col1:
        tf_sel=st.radio("Timeframe",["1H — Hourly (more signals)","1D — Daily (swing)"],index=0,label_visibility="collapsed")
    with tf_col2:
        st.caption("**1H** = more signals, shorter holds\n\n**1D** = fewer signals, bigger moves")
    backtest_tf="1D" if tf_sel.startswith("1D") else "1H"

    st.markdown("**Parameters** *(regime auto-adjusts SL/TP)*")
    p1,p2,p3=st.columns(3)
    min_score=p1.number_input("Min Score",value=float(sr.MIN_SCORE),step=0.5,min_value=1.0,max_value=15.0)
    atr_sl   =p2.number_input("ATR SL",  value=float(sr.ATR_SL),  step=0.5,min_value=0.5,max_value=5.0)
    atr_tp   =p3.number_input("ATR TP",  value=float(sr.ATR_TP),  step=0.5,min_value=1.0,max_value=10.0)

    run_btn=st.button(f"🚀 Run Backtest [{period} · {backtest_tf}]",use_container_width=True,type="primary")

    if run_btn:
        with st.spinner(f"Running {period} {backtest_tf} backtest..."):
            try:
                import vectorbt as vbt
                days=sr.PERIOD_DAYS[period]
                df_1h=sr.fetch_ohlcv("hour",1,days_back=days+30)
                df_4h=sr.fetch_ohlcv("hour",4,days_back=days+60)
                df_1d=sr.fetch_ohlcv("day", 1,days_back=days+250)
                if df_1h.empty:
                    st.error("No data — check API key"); st.stop()
                df=sr.compute_indicators(df_1h,df_4h,df_1d,pd.DataFrame(),pd.DataFrame(),pd.DataFrame())
                regime,_=sr.detect_regime(df,df_4h_ext=df_4h,df_1d_ext=df_1d)
                if not df_4h.empty:
                    d4=df_4h.copy()
                    for col,n in [("ema8",8),("ema21",21),("ema55",55)]:
                        d4[col]=sr._ema(d4["close"],n)
                    d4["rsi"]=sr._rsi(d4["close"]); d4["atr"]=sr._atr(d4)
                    h4s=sr.detect_local_structure(d4,30,"4H")
                else: h4s="ranging"
                h1s=sr.detect_local_structure(df,48,"1H")
                df=sr.compute_scores_regime(df,regime,sr.MIN_CATS,sr.TREND_REQ,h1_structure=h1s,h4_structure=h4s)
                rcfg=sr.get_regime_config(regime)
                cutoff=pd.Timestamp(datetime.now(timezone.utc)-timedelta(days=days)).tz_localize(None)
                df_bt=df[df.index>=cutoff].copy()
                if backtest_tf=="1D":
                    df_bt=df_bt.resample("1D").agg({"open":"first","high":"max","low":"min","close":"last",
                        "volume":"sum","atr":"last","long_signal":"any","short_signal":"any"}).dropna(subset=["close"])
                n_sig=int(df_bt["long_signal"].sum())+int(df_bt["short_signal"].sum())
                if n_sig==0:
                    st.warning(f"No signals — try Min Score < {min_score}"); st.stop()
                sl_stop=df_bt["atr"]*atr_sl/df_bt["close"]
                tp_stop=df_bt["atr"]*atr_tp/df_bt["close"]
                vbt_freq="1D" if backtest_tf=="1D" else "1h"
                pf=vbt.Portfolio.from_signals(
                    close=df_bt["close"],high=df_bt["high"],low=df_bt["low"],
                    entries=df_bt["long_signal"],short_entries=df_bt["short_signal"],
                    sl_stop=sl_stop,tp_stop=tp_stop,
                    init_cash=sr.INIT_CASH,fees=sr.FEES,slippage=sr.SLIPPAGE,freq=vbt_freq)
                try:
                    t=pf.trades.records_readable
                    wins=len(t[t["PnL"]>0]); losses=len(t[t["PnL"]<0])
                    wr=wins/len(t) if len(t)>0 else 0
                    aw=float(t[t["PnL"]>0]["PnL"].mean()) if wins>0 else 0
                    al=float(t[t["PnL"]<0]["PnL"].mean()) if losses>0 else 0
                    pfr=abs(aw/al) if al!=0 else 0
                    trades_df=t
                except:
                    wr=aw=al=pfr=0; trades_df=pd.DataFrame()
                bh=float(df_bt["close"].iloc[-1]/df_bt["close"].iloc[0])-1
                result={
                    "run_id":f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{period}_{backtest_tf}",
                    "run_ts":datetime.utcnow().isoformat(),
                    "period":period,"timeframe":backtest_tf,
                    "regime":regime,"h4_structure":h4s,"h1_structure":h1s,
                    "start":str(df_bt.index[0].date()),"end":str(df_bt.index[-1].date()),
                    "total_return":round(float(pf.total_return()),4),
                    "buy_and_hold":round(bh,4),"beat_bh":bool(pf.total_return()>bh),
                    "sharpe_ratio":round(float(pf.sharpe_ratio() or 0),3),
                    "sortino_ratio":round(float(pf.sortino_ratio() or 0),3),
                    "max_drawdown":round(float(pf.max_drawdown()),4),
                    "total_trades":int(pf.trades.count()),
                    "win_rate":round(wr,4),"avg_win":round(aw,2),"avg_loss":round(al,2),
                    "profit_factor":round(pfr,3),"final_value":round(float(pf.final_value()),2),
                    "init_cash":sr.INIT_CASH,
                    "config":{"min_score":min_score,"atr_sl_mult":atr_sl,"atr_tp_mult":atr_tp,
                              "min_categories":sr.MIN_CATS,"trend_required":sr.TREND_REQ,
                              "enable_shorts":sr.ENABLE_SHORT},
                }
                # Save to session_state (persists in browser) AND try file
                runs=st.session_state.backtest_runs
                runs.append(result)
                st.session_state.backtest_runs=runs
                sr.save_to_memory(result)
                # Store equity + trades for display
                st.session_state["last_equity"]=pf.value()
                st.session_state["last_trades"]=trades_df
                st.session_state["last_result"]=result
                st.success(f"✅ Backtest complete! {n_sig} signals | {int(pf.trades.count())} trades")
            except ImportError:
                st.error("vectorbt not installed — add `vectorbt>=0.26.1` to requirements.txt")
            except Exception as e:
                st.error(f"Backtest error: {e}")

    r=st.session_state.get("last_result")
    if r:
        rem=REGIME_EMOJI.get(r.get("regime",""),"❓")
        st.markdown(f"#### {r['period']} · {r.get('timeframe','1H')} &nbsp; `{r['start']} → {r['end']}`")
        st.caption(f"{rem} {r.get('regime','')} | 4H: {r.get('h4_structure','')} | 1H: {r.get('h1_structure','')}")
        c1,c2,c3,c4=st.columns(4)
        c1.metric("Return",f"{r['total_return']:.1%}",delta=f"B&H: {r['buy_and_hold']:.1%}")
        c2.metric("Sharpe",f"{r['sharpe_ratio']:.3f}")
        c3.metric("Max DD", f"{r['max_drawdown']:.1%}")
        c4.metric("Trades", r["total_trades"])
        c1,c2,c3,c4=st.columns(4)
        c1.metric("Win Rate",     f"{r['win_rate']:.1%}")
        c2.metric("Profit Factor",f"{r['profit_factor']:.3f}")
        c3.metric("Avg Win",      f"${r['avg_win']:,.0f}")
        c4.metric("Avg Loss",     f"${r['avg_loss']:,.0f}")
        st.metric("Final Value",f"${r['final_value']:,.2f}",delta=f"Started ${r['init_cash']:,.0f}")
        eq=st.session_state.get("last_equity")
        if eq is not None:
            eq_df=eq.reset_index(); eq_df.columns=["Date","Portfolio Value"]
            st.line_chart(eq_df.set_index("Date"),use_container_width=True)
        tdf=st.session_state.get("last_trades")
        if tdf is not None and not tdf.empty:
            with st.expander(f"📋 Trade Log ({r['total_trades']} trades)"):
                dc=[c for c in ["Direction","Entry Timestamp","Entry Price","Exit Timestamp","Exit Price","PnL","Return"] if c in tdf.columns]
                st.dataframe(tdf[dc] if dc else tdf,use_container_width=True)


# ══════════════════════════════════════════════════════════════════
# TAB 3 — HISTORY (backtest runs)
# ══════════════════════════════════════════════════════════════════

with tab_history:
    st.markdown("### 📋 Backtest History")
    # Merge session runs + file runs
    file_mem=sr.load_memory()
    session_runs=st.session_state.backtest_runs
    # Combine, deduplicate by run_id
    all_ids=set()
    all_runs=[]
    for r in file_mem+session_runs:
        rid=r.get("run_id","")
        if rid not in all_ids:
            all_ids.add(rid); all_runs.append(r)
    all_runs=sorted(all_runs,key=lambda x:x.get("run_ts",""),reverse=True)

    if not all_runs:
        st.info("No backtest runs yet. Go to Backtest tab and run one.")
    else:
        rows=[]
        for r in all_runs[:50]:
            rows.append({
                "Run ID":   r.get("run_id",""),
                "Period":   r.get("period",""),
                "TF":       r.get("timeframe","1H"),
                "Regime":   f"{REGIME_EMOJI.get(r.get('regime',''),'❓')} {r.get('regime','')}",
                "Return":   f"{r.get('total_return',0):.1%}",
                "Sharpe":   f"{r.get('sharpe_ratio',0):.3f}",
                "Max DD":   f"{r.get('max_drawdown',0):.1%}",
                "Trades":   r.get("total_trades",0),
                "Win Rate": f"{r.get('win_rate',0):.1%}",
                "Beat B&H": "✅" if r.get("beat_bh") else "❌",
            })
        st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
        st.download_button("⬇️ Download History (JSON)",
            data=json.dumps(all_runs,indent=2,default=str),
            file_name="btc_backtest_memory_v8.json",mime="application/json")

        returns=[r.get("total_return",0) for r in all_runs]
        if len(returns)>1:
            st.markdown("**Return Distribution**")
            st.bar_chart(pd.DataFrame({"Return":returns}))


# ══════════════════════════════════════════════════════════════════
# TAB 4 — LEARN
# ══════════════════════════════════════════════════════════════════

with tab_learn:
    st.markdown("### 🧠 Learning Report")
    file_mem=sr.load_memory()
    session_runs=st.session_state.backtest_runs
    all_ids=set(); all_runs=[]
    for r in file_mem+session_runs:
        rid=r.get("run_id","")
        if rid not in all_ids: all_ids.add(rid); all_runs.append(r)

    if not all_runs:
        st.info("Run at least 2-3 backtests to see learning insights.")
    else:
        ranked=sorted(all_runs,key=lambda x:x.get("sharpe_ratio",0),reverse=True)
        best=ranked[0]; worst=ranked[-1]
        st.success(f"🥇 Best: {best['period']} {best.get('timeframe','1H')} · {REGIME_EMOJI.get(best.get('regime',''),'')}{best.get('regime','')} · Return {best['total_return']:.1%} · Sharpe {best.get('sharpe_ratio',0):.3f}")
        st.error(f"🔴 Worst: {worst['period']} {worst.get('timeframe','1H')} · Return {worst['total_return']:.1%}")

        profitable=[r for r in all_runs if r["total_return"]>0]
        beat_bh=[r for r in all_runs if r.get("beat_bh")]
        c1,c2,c3=st.columns(3)
        c1.metric("Total Runs",len(all_runs))
        c2.metric("Profitable",f"{len(profitable)}/{len(all_runs)}")
        c3.metric("Beat B&H",f"{len(beat_bh)}/{len(all_runs)}")

        # By regime
        by_regime={}
        for r in all_runs:
            reg=r.get("regime","UNKNOWN")
            if reg not in by_regime: by_regime[reg]=[]
            by_regime[reg].append(r)
        if by_regime:
            st.markdown("**📊 Performance by Regime**")
            st.dataframe(pd.DataFrame([{
                "Regime":f"{REGIME_EMOJI.get(reg,'❓')} {reg}","Runs":len(runs),
                "Avg Return":f"{sum(r['total_return'] for r in runs)/len(runs):.1%}",
                "Avg Sharpe":f"{sum(r.get('sharpe_ratio',0) for r in runs)/len(runs):.3f}",
                "Win Rate":f"{sum(r.get('win_rate',0) for r in runs)/len(runs):.1%}",
                "Beat B&H":f"{sum(1 for r in runs if r.get('beat_bh'))}/{len(runs)}",
            } for reg,runs in sorted(by_regime.items())]),use_container_width=True,hide_index=True)

        # By timeframe
        by_tf={}
        for r in all_runs:
            tf=r.get("timeframe","1H")
            if tf not in by_tf: by_tf[tf]=[]
            by_tf[tf].append(r)
        if len(by_tf)>1:
            st.markdown("**⏱ By Timeframe**")
            st.dataframe(pd.DataFrame([{
                "TF":tf,"Runs":len(runs),
                "Avg Return":f"{sum(r['total_return'] for r in runs)/len(runs):.1%}",
                "Avg Sharpe":f"{sum(r.get('sharpe_ratio',0) for r in runs)/len(runs):.3f}",
            } for tf,runs in sorted(by_tf.items())]),use_container_width=True,hide_index=True)

        # Best config
        st.markdown("**💡 Best Config Recommendation**")
        bc=best.get("config",{})
        col1,col2=st.columns(2)
        col1.code(f"MIN_SCORE      = {bc.get('min_score','?')}\n"
                  f"MIN_CATEGORIES = {bc.get('min_categories','?')}\n"
                  f"ATR_SL_MULT    = {bc.get('atr_sl_mult','?')}\n"
                  f"ATR_TP_MULT    = {bc.get('atr_tp_mult','?')}\n"
                  f"TIMEFRAME      = {best.get('timeframe','1H')}")
        col2.metric("Expected Return",f"{best['total_return']:.1%}")
        col2.metric("Expected Sharpe",f"{best.get('sharpe_ratio',0):.3f}")
        col2.metric("Best Period",f"{best['period']} {best.get('timeframe','1H')}")


# ══════════════════════════════════════════════════════════════════
# TAB 5 — INTELLIGENCE
# ══════════════════════════════════════════════════════════════════

with tab_intel:
    st.markdown("## 🔬 Intelligence Layer")
    st.caption("Research Agents · Execution Gate · Loss Autopsy · Polymarket Intel")

    try:
        from tab_intelligence import render_intelligence_tab
        sig_cur=st.session_state.get("signal",{}) or {}
        intel_data=sig_cur.get("intelligence",{})
        render_intelligence_tab(sig=sig_cur, intel=intel_data)
    except ImportError:
        st.info("tab_intelligence.py not found in streamlit_app/ folder.")
        # Inline basic intelligence display using current signal
        sig_cur=st.session_state.get("signal",{}) or {}
        intel=sig_cur.get("intelligence",{})
        research=intel.get("research") or sig_cur.get("research",{})
        execution=intel.get("execution") or sig_cur.get("execution_decision",{})
        polymarket=intel.get("polymarket") or sig_cur.get("polymarket",{})

        it1,it2,it3,it4=st.tabs(["📡 Research","🎯 Execution Gate","⚗️ Postmortems","🎰 Polymarket"])

        with it1:
            st.markdown("### Research Agents Report")
            st.caption("Twitter · Reddit · RSS Feeds → Sentiment → Narrative vs Market")
            if not sig_cur:
                st.info("Research agents not yet run. Click 🔄 Refresh on Signal tab.")
            elif not research:
                st.warning("Research layer not available. Ensure `feedparser` and `vaderSentiment` are in requirements.txt and `REDDIT_CLIENT_ID` is set.")
                st.markdown("**Install dependencies:**")
                st.code("feedparser>=6.0.10\nvaderSentiment>=3.3.2", language="toml")
            else:
                sent=research.get("narrative_label","NEUTRAL")
                comp=research.get("overall_sentiment",0)
                conf=research.get("confidence",0)
                align=research.get("market_alignment","NEUTRAL")
                sc={"BULLISH":"#00c853","BEARISH":"#d50000","NEUTRAL":"#555","MIXED":"#f9a825"}.get(sent,"#555")
                ai={"ALIGNED":"✅","DIVERGENT":"⚠️","NEUTRAL":"↔️"}.get(align,"↔️")
                st.markdown(f'<div style="background:{sc};border-radius:10px;padding:12px;color:white"><b>{sent} ({comp:+.2f})</b></div>',unsafe_allow_html=True)
                c1,c2,c3,c4=st.columns(4)
                c1.metric("Posts",research.get("total_posts",0))
                c2.metric("Confidence",f"{conf:.0f}%")
                c3.metric("Alignment",f"{ai} {align}")
                c4.metric("Bull%",f"{research.get('bull_pct',0):.0%}")
                st.caption(research.get("alignment_note",""))
                kws=research.get("top_keywords",[])
                if kws:
                    st.markdown("**Keywords:** " + " · ".join(f"`{k}`" for k in kws[:8]))
                headlines=research.get("top_headlines",[])
                if headlines:
                    st.markdown("**Top Headlines:**")
                    for h in headlines[:4]:
                        url=h.get("url",""); title=h.get("title",""); src=h.get("source","")
                        if url: st.markdown(f"• [{src}] [{title[:75]}]({url})")
                        else: st.caption(f"• [{src}] {title[:80]}")

        with it2:
            st.markdown("### Execution Decision Gate")
            st.caption("All signals must pass confidence threshold + risk gates before execution")
            if not execution:
                st.info("Execution gate not evaluated yet.")
            else:
                approved=execution.get("approved",False)
                direction_e=execution.get("direction","NONE")
                conf_e=execution.get("combined_conf",0)
                reason=execution.get("rejection_reason","")
                if approved:
                    st.success(f"✅ APPROVED — {direction_e} | Confidence {conf_e:.0f}%")
                    pos_pct=execution.get("position_pct",0); pos_usd=execution.get("position_usd",0)
                    c1,c2,c3,c4=st.columns(4)
                    c1.metric("Position",f"{pos_pct:.1%} = ${pos_usd:,.0f}")
                    c2.metric("Entry",f"${execution.get('entry_price',0):,.2f}")
                    c3.metric("SL",   f"${execution.get('stop_loss',0):,.2f}")
                    c4.metric("TP",   f"${execution.get('take_profit',0):,.2f}")
                    st.info(f"R:R = 1:{execution.get('rr_ratio',0):.1f}")
                else:
                    st.warning(f"❌ BLOCKED — {reason}")
                    st.markdown("**Confidence Breakdown:**")
                    st.dataframe(pd.DataFrame({
                        "Source":["Technical","Research","Polymarket","Combined"],
                        "Score":[f"{execution.get('tech_score',0):.1f}pts",
                                 f"{execution.get('research_conf',0):.0f}%",
                                 f"{execution.get('poly_edge',0):+.1%}",
                                 f"{conf_e:.0f}%"],
                        "Weight":["45%","35%","20%","—"],
                    }),use_container_width=True,hide_index=True)
                    st.caption("Threshold: 55% combined confidence required to execute")
                notes=execution.get("notes",[])
                if notes:
                    with st.expander("📝 Decision Notes"):
                        for n in notes: st.caption(f"• {n}")

        with it3:
            st.markdown("### Loss Autopsy System")
            st.caption("5 agents analyse every losing trade · Lessons accumulate in system memory")
            pm_dir=Path("data/postmortems")
            if pm_dir.exists():
                files=sorted(pm_dir.glob("pm_*.json"),reverse=True)[:5]
                if files:
                    for f in files:
                        try:
                            pm=json.loads(f.read_text())
                            sev=pm.get("severity_score",0)
                            si="🔴" if sev>=7 else ("🟡" if sev>=4 else "🟢")
                            with st.expander(f"{si} {pm.get('trade_id','')} | Sev {sev:.0f}/10 | PnL: {pm.get('trade',{}).get('pnl_pct',0):.1%}"):
                                for v in pm.get("verdicts",[]):
                                    icon={"CRITICAL":"🔴","WARNING":"🟡","INFO":"⚪"}.get(v.get("severity","INFO"),"⚪")
                                    st.markdown(f"{icon} **{v.get('agent','')}**: `{v.get('root_cause','')}` — {v.get('diagnosis','')[:100]}")
                                for l in pm.get("lessons",[])[:3]: st.caption(f"• {l[:120]}")
                        except: pass
                else:
                    st.info("No postmortems yet — they run automatically after losing trades.")
            else:
                st.info("No postmortems yet. They are created automatically after each losing trade via the paper trading bot.")

        with it4:
            st.markdown("### Polymarket Intelligence")
            st.caption("Active BTC prediction markets · Our edge vs crowd odds")
            if not polymarket:
                st.warning("Polymarket scan returned no data.")
                st.markdown("This could mean:\n- Polymarket API is temporarily rate-limited\n- No high-liquidity BTC markets found\n- Intelligence layer not fully initialised")
                st.markdown("**Retry:** Click 🔄 Refresh on Signal tab to re-run the scan.")
            else:
                total=polymarket.get("total_scanned",0); rel=polymarket.get("relevant_count",0)
                st.markdown(f"Scanned **{total}** markets — **{rel}** BTC-relevant")
                summary=polymarket.get("edge_summary","")
                if summary: st.info(f"💡 {summary}")
                hc=polymarket.get("high_conviction",[])
                if hc:
                    st.markdown("**🎯 High/Medium Conviction:**")
                    rows=[]
                    for m in hc:
                        ci="🟢" if m.get("conviction")=="HIGH" else "🟡"
                        rows.append({"":ci,"Question":m.get("question","")[:55],
                                     "YES%":f"{m.get('yes_price',0):.0%}",
                                     "Liquidity":f"${m.get('liquidity',0):,.0f}",
                                     "Edge":f"{m.get('edge_vs_signal',0):+.1%}",
                                     "Bet":m.get("recommended_bet","SKIP"),"Ends":m.get("end_date","")})
                    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
                else:
                    st.info("No high-conviction markets at this time.")


# ══════════════════════════════════════════════════════════════════
# TAB 6 — PAPER TRADING
# ══════════════════════════════════════════════════════════════════

with tab_paper:
    try:
        from tab_paper_trading import render_paper_trading_tab
        render_paper_trading_tab()
    except ImportError:
        st.markdown("## 📄 Paper Trading")
        st.warning("tab_paper_trading.py not found.")
        st.markdown("### How to Start Paper Trading Bot")
        st.code("python paper_trading/paper_bot.py", language="bash")
        st.markdown("**Add to requirements.txt:**")
        st.code("requests\npandas\nnumpy", language="text")
        st.markdown("**Environment variables (Streamlit Secrets):**")
        st.code("""PAPER_CAPITAL = "10000"
PAPER_CHECK_INTERVAL = "60"
PAPER_MAX_DAILY_LOSS = "0.05"
PAPER_MAX_CONCURRENT = "2"
PAPER_KELLY_FRACTION = "0.25"
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_CHAT_IDS = "..."
""", language="toml")
