"""
streamlit_app/app.py — Trinity.AI BTC Strategy v8.5
Fixed:
  • NameError: duplicate st.tabs() call removed (line 110 had old 5-tab unpack)
  • &nbsp; rendering raw — EMA line now uses proper markdown
  • RSI / MFI / Stoch K now show gauge charts (not just numbers)
  • Paper Trading tab wired in (6th tab)
  • 1H chart removed from Signal tab
"""

import os, sys, json, time
from pathlib import Path
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

# ── Path setup ────────────────────────────────────────────────────
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root / "core"))
sys.path.insert(0, str(_root / "streamlit_app"))
sys.path.insert(0, str(_root / "intelligence"))

st.set_page_config(
    page_title="BTC Strategy v8.5",
    page_icon="₿",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Minimal styling ───────────────────────────────────────────────
st.markdown("""
<style>
  .block-container { padding-top: 1rem; padding-bottom: 1rem; }
  .stMetric label { font-size: 0.78rem; color: #888; }
  .stMetric [data-testid="stMetricValue"] { font-size: 1.4rem; }
  div[data-testid="stHorizontalBlock"] { gap: 6px; }
  .signal-box {
    border-radius: 12px; padding: 24px; text-align: center;
    margin-bottom: 12px; background: #1a1a1a;
  }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════
# SAFE IMPORTS
# ══════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner=False)
def _import_runner():
    try:
        import signal_runner as sr
        return sr
    except Exception as e:
        return None

sr = _import_runner()

def _load_memory_safe():
    paths = [
        _root / "data" / "system_memory.json",
        Path("data/system_memory.json"),
        Path("system_memory.json"),
    ]
    for p in paths:
        if p.exists():
            try: return json.loads(p.read_text())
            except: pass
    return []

try:
    from tab_intelligence   import render_intelligence_tab
    INTEL_OK = True
except Exception:
    INTEL_OK = False

try:
    from tab_paper_trading  import render_paper_trading_tab
    PAPER_OK = True
except Exception:
    PAPER_OK = False


# ══════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════

st.markdown("# ₿ BTC Strategy v8.5")
st.caption("Regime-Aware · 5-Signal Voting · Volume Profile · " +
           time.strftime("%H:%M UTC", time.gmtime()))


# ══════════════════════════════════════════════════════════════════
# TABS — exactly 6, unpacked once
# ══════════════════════════════════════════════════════════════════

tab_signal, tab_backtest, tab_history, tab_learn, tab_intel, tab_paper = st.tabs([
    "📡 Signal",
    "📊 Backtest",
    "📋 History",
    "🧠 Learn",
    "🔬 Intelligence",
    "📄 Paper Trading",
])


# ══════════════════════════════════════════════════════════════════
# HELPERS — GAUGE CHARTS
# ══════════════════════════════════════════════════════════════════

def _gauge(value: float, label: str, lo: float = 0, hi: float = 100,
           oversold: float = 30, overbought: float = 70,
           invert: bool = False, unit: str = "") -> go.Figure:
    """
    Compact semicircle gauge for RSI / MFI / Stoch K.
    Red zone < oversold, Green zone > overbought, Yellow = neutral.
    """
    # colour based on value
    if value <= oversold:
        bar_color = "#26a69a"   # teal = oversold (buy signal potential)
    elif value >= overbought:
        bar_color = "#ef5350"   # red = overbought (sell signal potential)
    else:
        bar_color = "#ffa726"   # amber = neutral

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        number={"suffix": unit, "font": {"size": 28, "color": "#fff"}},
        title={"text": label, "font": {"size": 13, "color": "#aaa"}},
        gauge={
            "axis": {
                "range": [lo, hi],
                "tickwidth": 1,
                "tickcolor": "#444",
                "tickfont": {"size": 9, "color": "#666"},
                "nticks": 5,
            },
            "bar": {"color": bar_color, "thickness": 0.25},
            "bgcolor": "#1e1e1e",
            "borderwidth": 0,
            "steps": [
                {"range": [lo, oversold],   "color": "#1b3a3a"},  # oversold zone
                {"range": [oversold, overbought], "color": "#1e1e1e"},
                {"range": [overbought, hi], "color": "#3a1b1b"},  # overbought zone
            ],
            "threshold": {
                "line": {"color": "#fff", "width": 2},
                "thickness": 0.75,
                "value": value,
            },
        },
    ))
    fig.update_layout(
        height=160,
        margin=dict(l=10, r=10, t=30, b=5),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#fff"},
    )
    return fig


def _mini_bar(value: float, label: str, max_val: float = 100,
              color: str = "#26a69a") -> go.Figure:
    """Tiny horizontal bar for ATR / Vol Ratio."""
    fig = go.Figure(go.Bar(
        x=[value], y=[""],
        orientation="h",
        marker_color=color,
        text=[f"{value:.2f}"],
        textposition="outside",
        textfont={"color": "#ccc", "size": 13},
    ))
    fig.update_layout(
        height=70,
        margin=dict(l=0, r=40, t=24, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(range=[0, max_val * 1.2], showgrid=False,
                   showticklabels=False, zeroline=False),
        yaxis=dict(showticklabels=False, showgrid=False),
        title={"text": label, "font": {"size": 12, "color": "#aaa"}, "y": 0.98},
    )
    return fig


# ══════════════════════════════════════════════════════════════════
# TAB 1 — SIGNAL
# ══════════════════════════════════════════════════════════════════

with tab_signal:
    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        do_refresh = st.button("🔄 Refresh", use_container_width=True)

    # ── Fetch signal ──────────────────────────────────────────────
    if sr is None:
        st.error("signal_runner.py could not be imported. Check deployment logs.")
        st.stop()

    @st.cache_data(ttl=300, show_spinner="Fetching signal…")
    def _fetch():
        return sr.get_signal()

    if do_refresh:
        st.cache_data.clear()

    try:
        sig = _fetch()
    except Exception as e:
        st.error(f"Signal fetch failed: {e}")
        sig = {}

    direction  = sig.get("direction", "NO SIGNAL")
    price      = sig.get("price", 0)
    regime     = sig.get("regime", "UNKNOWN")
    regime_cfg = sig.get("regime_config", {}) or {}
    ls         = float(sig.get("long_score",  0) or 0)
    ss         = float(sig.get("short_score", 0) or 0)
    ind        = sig.get("indicators",  {}) or {}
    ep         = sig.get("entry_plan",  {}) or {}
    override   = sig.get("bear_override",    "NONE")
    sentiment  = sig.get("sentiment",   "NEUTRAL")
    alignment  = sig.get("alignment",   "NEUTRAL")
    confidence = sig.get("research_confidence", 0)
    timestamp  = sig.get("timestamp",   "")
    reasons_l  = sig.get("reasons_long",  []) or []
    reasons_s  = sig.get("reasons_short", []) or []
    exec_dec   = sig.get("execution_decision", {}) or {}

    # ── Signal hero card ──────────────────────────────────────────
    if direction == "LONG":
        hero_bg = "linear-gradient(135deg,#1b5e20,#2e7d32)"
        hero_icon = "📈 LONG"
    elif direction == "SHORT":
        hero_bg = "linear-gradient(135deg,#7f0000,#c62828)"
        hero_icon = "📉 SHORT"
    else:
        hero_bg = "linear-gradient(135deg,#212121,#37474f)"
        hero_icon = "⬜ NO SIGNAL"

    ts_display = timestamp[:16].replace("T", " ") + " UTC" if timestamp else time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    st.markdown(f"""
    <div style="background:{hero_bg};border-radius:14px;padding:28px 20px;
                text-align:center;margin-bottom:12px">
      <div style="font-size:2.2em;font-weight:700;color:#fff;letter-spacing:0.04em">
        {hero_icon}
      </div>
      <div style="font-size:2.8em;font-weight:300;color:#fff;margin:8px 0">
        ${price:,.2f}
      </div>
      <div style="font-size:0.85em;color:rgba(255,255,255,0.6)">{ts_display}</div>
    </div>
    """, unsafe_allow_html=True)

    # ── Regime badge ──────────────────────────────────────────────
    regime_colors = {
        "STRONG_BULL": "#1b5e20", "WEAK_BULL": "#33691e",
        "BEAR": "#7f0000",        "RANGING":   "#e65100",
    }
    rc = regime_colors.get(regime, "#37474f")
    desc = regime_cfg.get("description", f"{regime}")
    sl_m = regime_cfg.get("atr_sl_mult", "?")
    tp_m = regime_cfg.get("atr_tp_mult", "?")
    min_l = regime_cfg.get("min_score_long", "?")
    min_s = regime_cfg.get("min_score_short", "?")

    st.markdown(f"""
    <div style="background:{rc};border-radius:10px;padding:14px 18px;margin-bottom:10px">
      <b style="font-size:1.1em;color:#fff">🐻 {regime}</b><br>
      <span style="color:rgba(255,255,255,0.8);font-size:0.88em">
        {desc} &nbsp;|&nbsp; SL: {sl_m}× ATR &nbsp;|&nbsp; TP: {tp_m}× ATR
        &nbsp;|&nbsp; Min L/S: {min_l} / {min_s}
      </span>
    </div>
    """, unsafe_allow_html=True)

    # ── Bear override ─────────────────────────────────────────────
    if override and override != "NONE":
        st.markdown(f"""
        <div style="background:#fff3cd;border-radius:8px;padding:10px 14px;
                    margin-bottom:10px;color:#856404">
          ⚡ Bear Override Active: <b>{override}</b>
        </div>""", unsafe_allow_html=True)

    # ── Sentiment row ─────────────────────────────────────────────
    sent_icon = "🟡" if sentiment == "NEUTRAL" else ("🟢" if sentiment == "BULLISH" else "🔴")
    st.markdown(
        f"{sent_icon} Sentiment: **{sentiment}** &nbsp;|&nbsp; "
        f"↔ Alignment: **{alignment}** &nbsp;|&nbsp; "
        f"Research confidence: **{confidence}%**"
    )

    # ── Score bars ────────────────────────────────────────────────
    st.divider()
    sc1, sc2 = st.columns(2)
    with sc1:
        st.markdown(f"**Long Score: {ls:.2f}**")
        st.progress(min(ls / 15, 1.0))
    with sc2:
        st.markdown(f"**Short Score: {ss:.2f}**")
        st.progress(min(ss / 15, 1.0))

    # ── GAUGE CHARTS — RSI / MFI / Stoch K ───────────────────────
    st.markdown("### Momentum Gauges")
    rsi_v   = float(ind.get("rsi",    50) or 50)
    mfi_v   = float(ind.get("mfi",    50) or 50)
    stoch_v = float(ind.get("stoch_k",50) or 50)
    atr_v   = float(ind.get("atr",     0) or 0)
    vol_r   = float(ind.get("vol_ratio",1) or 1)

    gc1, gc2, gc3 = st.columns(3)
    with gc1:
        st.plotly_chart(_gauge(rsi_v,   "RSI",    oversold=35, overbought=65), use_container_width=True, key="gauge_rsi")
    with gc2:
        st.plotly_chart(_gauge(mfi_v,   "MFI",    oversold=25, overbought=75), use_container_width=True, key="gauge_mfi")
    with gc3:
        st.plotly_chart(_gauge(stoch_v, "Stoch K",oversold=20, overbought=80), use_container_width=True, key="gauge_stoch")

    # ── ATR + Vol Ratio mini bars ─────────────────────────────────
    ab1, ab2 = st.columns(2)
    with ab1:
        st.plotly_chart(_mini_bar(atr_v, f"ATR  (${atr_v:,.0f})", max_val=max(atr_v*2, 500), color="#5c6bc0"), use_container_width=True, key="bar_atr")
    with ab2:
        col = "#26a69a" if vol_r >= 1 else "#ef5350"
        st.plotly_chart(_mini_bar(vol_r, f"Vol Ratio  ({vol_r:.2f}×)", max_val=3.0, color=col), use_container_width=True, key="bar_vol")

    # ── EMA / VWAP / Bull Flag text row ──────────────────────────
    ema50  = ind.get("ema50",  0) or 0
    ema200 = ind.get("ema200", 0) or 0
    vwap   = ind.get("vwap",   0) or 0
    bull_f = ind.get("bull_flag", False)

    ema50_color  = "#ef5350" if price < ema50  else "#26a69a"
    ema200_color = "#ef5350" if price < ema200 else "#26a69a"

    st.markdown(
        f"EMA50: "
        f'<span style="color:{ema50_color};font-weight:600">${ema50:,.0f}</span>'
        f" &nbsp;&nbsp; EMA200: "
        f'<span style="color:{ema200_color};font-weight:600">${ema200:,.0f}</span>'
        f" &nbsp;|&nbsp; VWAP: **${vwap:,.0f}**"
        f" &nbsp;|&nbsp; Bull Flag: **{'Yes ✅' if bull_f else 'No'}**",
        unsafe_allow_html=True,
    )

    # ── Entry plan ────────────────────────────────────────────────
    if ep:
        st.divider()
        st.markdown("### 🎯 Entry Plan")
        e1, e2, e3, e4 = st.columns(4)
        e1.metric("Entry",       f"${ep.get('entry', price):,.2f}")
        e2.metric("Stop Loss",   f"${ep.get('stop_loss', 0):,.2f}")
        e3.metric("Take Profit", f"${ep.get('take_profit', 0):,.2f}")
        rr = ep.get("risk_reward", 0) or 0
        e4.metric("R:R",         f"1 : {rr:.1f}")

    # ── Reasons ───────────────────────────────────────────────────
    reasons = reasons_l if direction == "LONG" else reasons_s
    if reasons:
        st.divider()
        st.markdown("### ✅ Signal Reasons")
        for r in reasons:
            st.markdown(f"- {r}")

    # ── Execution decision (from intelligence) ────────────────────
    if exec_dec:
        st.divider()
        st.markdown("### 🔬 Execution Gate")
        approved = exec_dec.get("approved", False)
        if approved:
            st.success(f"✅ Execution APPROVED — confidence {exec_dec.get('combined_conf', 0):.0f}%")
        else:
            st.warning(
                f"⛔ Execution BLOCKED — {exec_dec.get('block_reason', 'conditions not met')}\n\n"
                f"Confidence: {exec_dec.get('combined_conf', 0):.0f}% "
                f"(threshold: {exec_dec.get('threshold', 55)}%)"
            )

    # ── Volume Profile ────────────────────────────────────────────
    poc = float(ind.get("poc", 0) or 0)
    vah = float(ind.get("vah", 0) or 0)
    val = float(ind.get("val", 0) or 0)
    if poc > 0:
        st.divider()
        st.markdown("### 📊 Volume Profile")
        vp1, vp2, vp3 = st.columns(3)
        vp1.metric("POC", f"${poc:,.0f}", delta=f"{(price-poc)/poc:+.1%} from price" if poc else None)
        vp2.metric("VAH", f"${vah:,.0f}")
        vp3.metric("VAL", f"${val:,.0f}")


# ══════════════════════════════════════════════════════════════════
# TAB 2 — BACKTEST
# ══════════════════════════════════════════════════════════════════

with tab_backtest:
    st.markdown("## 📊 Backtest")

    if sr is None:
        st.error("signal_runner unavailable")
    else:
        with st.form("bt_form"):
            c1, c2, c3, c4 = st.columns(4)
            period  = c1.selectbox("Period",    ["1M","3M","6M","1Y"], index=1)
            tf      = c2.selectbox("Timeframe", ["1H","4H","1D"],      index=0)
            capital = c3.number_input("Capital $", value=10000, step=1000)
            submitted = c4.form_submit_button("▶ Run Backtest", use_container_width=True)

        if submitted:
            with st.spinner("Running backtest…"):
                try:
                    result = sr.run_backtest(period=period, tf=tf, capital=capital)
                    if result:
                        r1, r2, r3, r4, r5 = st.columns(5)
                        r1.metric("Return",       f"{result.get('total_return',0):.1%}")
                        r2.metric("Sharpe",       f"{result.get('sharpe',0):.3f}")
                        r3.metric("Max DD",       f"{result.get('max_dd',0):.1%}")
                        r4.metric("Trades",       result.get("num_trades", 0))
                        r5.metric("Win Rate",     f"{result.get('win_rate',0):.1%}")

                        eq = result.get("equity_curve", [])
                        if eq:
                            st.line_chart(pd.DataFrame({"Equity": eq}))

                        history = result.get("run_history", [])
                        if history:
                            st.dataframe(pd.DataFrame(history), use_container_width=True)
                except Exception as e:
                    st.error(f"Backtest error: {e}")


# ══════════════════════════════════════════════════════════════════
# TAB 3 — HISTORY
# ══════════════════════════════════════════════════════════════════

with tab_history:
    st.markdown("## 📋 Signal History")
    mem = _load_memory_safe()
    if mem:
        df = pd.DataFrame(mem[-50:])
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No signal history yet. History populates as signals fire.")


# ══════════════════════════════════════════════════════════════════
# TAB 4 — LEARN
# ══════════════════════════════════════════════════════════════════

with tab_learn:
    st.markdown("## 🧠 Learn — How Trinity.AI Works")

    with st.expander("Layer 1 — Regime Detection", expanded=True):
        st.markdown("""
        **5-signal daily voting engine** classifies market into one of 4 regimes:
        - 🚀 **STRONG_BULL** — EMA golden cross + price > EMA200 + strong volume
        - 📈 **WEAK_BULL** — Trend intact but momentum weakening
        - 🐻 **BEAR** — Price < EMA200, shorts primary, hard gate for longs
        - ↔️ **RANGING** — Both sides enabled with mean-reversion logic
        """)

    with st.expander("Layer 2 — Local Structure Override"):
        st.markdown("""
        In BEAR regime, 4H and 1H timeframe structure is checked:
        - **LEVEL3** (both ranging): selective MR longs allowed when RSI<48 + at support
        - **LEVEL2** (4H ranging): moderate MR longs with reversal confirmation
        - **LEVEL1** (1H only): very tight MR longs, strict entry conditions
        """)

    with st.expander("Layer 3 — Execution Scoring (~29 indicators)"):
        st.markdown("""
        Four scoring categories:
        - **Trend**: EMA8/21/55/200, 4H bias, BOS/CHoCH
        - **Zone**: Volume Profile POC/VAH/VAL, Order Blocks, Fib levels, AVWAP
        - **Momentum**: RSI, MFI, Stochastic, CVD divergences
        - **Positioning**: Liquidations, Funding, OI
        """)

    with st.expander("Layer 4 — Intelligence Layer"):
        st.markdown("""
        - **Research agents**: 5 parallel agents scraping news/Reddit/Twitter with VADER sentiment
        - **Execution gate**: combines technical confidence + sentiment alignment
        - **Postmortem**: triggered on every loss, accumulates system memory
        - **Polymarket**: BTC prediction market edge analysis
        """)

    with st.expander("Risk Management — Confidence-Scaled Kelly"):
        st.markdown("""
        ```
        position_size = min(¼_kelly × score_mult × conf_scale, regime_cap)
        ```
        - **¼ Kelly**: mathematically optimal fraction, never ruins account
        - **score_mult**: 0.8× to 1.4× based on technical score (better signal = bigger bet)
        - **conf_scale**: scales by research confidence (40%=0, 100%=1)
        - **Regime caps**: STRONG_BULL 20% / WEAK_BULL 15% / BEAR 12% / RANGING 10%
        - **Circuit breakers**: daily loss 5%, 3 consecutive losses → 4h cooldown
        """)


# ══════════════════════════════════════════════════════════════════
# TAB 5 — INTELLIGENCE
# ══════════════════════════════════════════════════════════════════

with tab_intel:
    if INTEL_OK:
        render_intelligence_tab()
    else:
        st.markdown("## 🔬 Intelligence")
        st.warning("Intelligence layer not available. Check that `tab_intelligence.py` is in `streamlit_app/`.")

        # Explain execution gate logic even without the full tab
        st.markdown("### Why Execution Gate Shows BLOCKED")
        st.markdown("""
        The execution gate combines **technical score** + **research confidence** before approving a trade:

        ```
        combined_conf = (tech_confidence × 0.6) + (sentiment_confidence × 0.4)
        threshold     = 55%
        ```

        **Why it's currently blocking:**
        1. Research agents returned `NEUTRAL` sentiment (no strong bullish/bearish narrative found)
        2. `alignment = NEUTRAL` means technical signal + sentiment don't agree
        3. `research_confidence = 45%` is below the 55% threshold

        This is **correct behaviour** — the gate exists to prevent trading when signal quality is low.

        **To unblock:** Wait for a regime where the technical signal is strong AND sentiment aligns.
        The gate will auto-approve when `combined_conf ≥ 55%`.
        """)


# ══════════════════════════════════════════════════════════════════
# TAB 6 — PAPER TRADING
# ══════════════════════════════════════════════════════════════════

with tab_paper:
    if PAPER_OK:
        render_paper_trading_tab()
    else:
        st.markdown("## 📄 Paper Trading")
        st.warning("Paper trading tab not loaded. Check that `tab_paper_trading.py` is in `streamlit_app/`.")

        st.markdown("### How to Start the Paper Trading Bot")
        st.code("""
# Option 1 — Run locally (24/7)
python paper_trading/paper_bot.py

# Option 2 — GitHub Actions (recommended for cloud)
# 1. Push paper_trading_bot.yml to .github/workflows/
# 2. Go to Actions tab → Enable workflow
# 3. Click "Run workflow" to start

# Option 3 — Streamlit Cloud background process
# Add to requirements.txt: schedule
# The bot runs as a background thread from app.py
        """, language="bash")

        st.markdown("### Environment Variables (add to Streamlit Cloud secrets)")
        st.code("""
PAPER_CAPITAL = "10000"
PAPER_CHECK_INTERVAL = "60"
PAPER_MAX_DAILY_LOSS = "0.05"
PAPER_MAX_CONCURRENT = "2"
PAPER_COOLDOWN_LOSSES = "3"
PAPER_COOLDOWN_HOURS = "4"
PAPER_KELLY_FRACTION = "0.25"
TELEGRAM_BOT_TOKEN = "your_token"
TELEGRAM_CHAT_IDS = "your_chat_id"
        """, language="toml")
