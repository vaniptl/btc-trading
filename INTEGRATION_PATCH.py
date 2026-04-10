"""
INTEGRATION PATCH for core/signal_runner.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Apply these changes to your existing signal_runner.py.
Changes are minimal — just 3 additions:
  1. Import intelligence hub (top of file)
  2. Call run_intelligence_layer() inside get_signal() after scores computed
  3. Add log_trade_to_sheets() + trigger_postmortem() to close_trade() flow

DO NOT replace signal_runner.py — this is an ADDITIVE patch.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ════════════════════════════════════════════════════════════════
# PATCH 1: Add to TOP of signal_runner.py (after existing imports)
# ════════════════════════════════════════════════════════════════

PATCH_1_IMPORTS = '''
# ── Intelligence Layer (Trinity.AI v2) ──────────────────────────
import sys
from pathlib import Path as _Path
# Allow intelligence/ and execution/ dirs to be found
for _p in ["intelligence", "execution", ".."]:
    _candidate = _Path(__file__).parent.parent / _p
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

try:
    from intelligence.intelligence_hub import run_intelligence_layer, trigger_postmortem_if_loss
    INTELLIGENCE_AVAILABLE = True
except ImportError as _e:
    INTELLIGENCE_AVAILABLE = False
    import logging as _log_imp
    _log_imp.getLogger("signal_runner").warning(f"Intelligence layer unavailable: {_e}")
'''


# ════════════════════════════════════════════════════════════════
# PATCH 2: Add INSIDE get_signal() — after "entry_plan = None..." block,
#          before the final return statement.
# Replace this section in your get_signal():
#
#   entry_plan = None
#   if lsig:
#       ...
#   elif ssig:
#       ...
#
# ADD THIS BLOCK right before `return { ... }`:
# ════════════════════════════════════════════════════════════════

PATCH_2_GET_SIGNAL = '''
    # ── Intelligence Layer: Research + Execution Gate + Polymarket ──
    intel = {}
    if INTELLIGENCE_AVAILABLE:
        try:
            ep = entry_plan or {}
            intel = run_intelligence_layer(
                signal_direction = direction,
                tech_score       = ls if lsig else ss,
                regime           = regime,
                btc_price        = price,
                ema50            = ema50_v,
                ema200           = ema200_v,
                entry_price      = ep.get("entry",      price),
                stop_loss        = ep.get("stop_loss",   price - atr_v * sl_mult),
                take_profit      = ep.get("take_profit", price + atr_v * tp_mult),
                h1_structure     = h1_str,
                h4_structure     = h4_str,
                account_value    = INIT_CASH,
                run_research     = True,
                run_polymarket   = True,
                timeout_seconds  = 40,
            )
            # If execution gate blocks the signal, override direction
            exec_decision = intel.get("execution", {})
            if exec_decision and not exec_decision.get("approved", True):
                # Log the block but keep direction for display — just flag it
                intel["execution_blocked"] = True
                intel["block_reason"]      = exec_decision.get("rejection_reason", "")
        except Exception as _ie:
            intel = {"errors": [str(_ie)]}
'''


PATCH_2_RETURN_ADDITIONS = '''
        # Add to the existing return dict:
        "intelligence":        intel,
        "research":            intel.get("research"),
        "execution_decision":  intel.get("execution"),
        "polymarket":          intel.get("polymarket"),
        "research_confidence": intel.get("confidence", 50.0),
        "execution_approved":  intel.get("execution_approved", True),
        "sentiment":           intel.get("sentiment", "NEUTRAL"),
        "market_alignment":    intel.get("alignment", "NEUTRAL"),
'''


# ════════════════════════════════════════════════════════════════
# PATCH 3: Add NEW function to signal_runner.py for trade lifecycle
# ════════════════════════════════════════════════════════════════

PATCH_3_TRADE_LIFECYCLE = '''
# ── Add these functions to signal_runner.py ──────────────────────

def close_and_postmortem(trade_record: dict) -> dict:
    """
    Call when a trade closes.
    If trade is a loss → automatically triggers 5-agent postmortem.
    Always logs to execution engine and Google Sheets.

    trade_record should contain:
        trade_id, direction, entry_price, exit_price,
        exit_reason, pnl_usd, pnl_pct, regime,
        long_score/short_score, confidence, sentiment,
        h1_structure, h4_structure, bear_override,
        rsi_at_entry, atr, vol_ratio
    """
    result = {"trade": trade_record, "postmortem": None}

    # Log to sheets
    log_to_sheets({
        "type":        "TRADE_CLOSE",
        "timestamp":   datetime.utcnow().isoformat(),
        "trade_id":    trade_record.get("trade_id", ""),
        "direction":   trade_record.get("direction", ""),
        "pnl_pct":     trade_record.get("pnl_pct", 0),
        "pnl_usd":     trade_record.get("pnl_usd", 0),
        "exit_reason": trade_record.get("exit_reason", ""),
        "regime":      trade_record.get("regime", ""),
    })

    # Trigger postmortem if loss
    if trade_record.get("pnl_pct", 0) < 0:
        if INTELLIGENCE_AVAILABLE:
            try:
                pm = trigger_postmortem_if_loss(trade_record)
                result["postmortem"] = pm
                if pm:
                    # Log postmortem to sheets
                    log_to_sheets({
                        "type":           "POSTMORTEM",
                        "timestamp":      datetime.utcnow().isoformat(),
                        "trade_id":       trade_record.get("trade_id"),
                        "severity_score": pm.get("severity_score", 0),
                        "primary_failure":pm.get("primary_failure", ""),
                        "lessons":        "; ".join(pm.get("lessons", [])[:3]),
                    })
            except Exception as e:
                result["postmortem_error"] = str(e)

    return result


def log_trade_to_sheets(trade: dict):
    """Log individual trade to Google Sheets."""
    log_to_sheets({
        "type":      "TRADE",
        "timestamp": datetime.utcnow().isoformat(),
        **{k: v for k, v in trade.items() if not isinstance(v, (dict, list))},
    })
'''


# ════════════════════════════════════════════════════════════════
# PATCH 4: app.py — Add Intelligence Tab
# ════════════════════════════════════════════════════════════════

PATCH_4_APP_PY = '''
# In streamlit_app/app.py:

# 1. Add import at top:
from tab_intelligence import render_intelligence_tab

# 2. Add tab to tab definition:
tab_signal, tab_backtest, tab_history, tab_learn, tab_intel = st.tabs(
    ["📡 Signal", "📊 Backtest", "📋 History", "🧠 Learn", "🔬 Intelligence"]
)

# 3. Add tab content at bottom:
with tab_intel:
    render_intelligence_tab(
        sig=st.session_state.signal,
        intel=(st.session_state.signal or {}).get("intelligence"),
    )

# 4. In the Signal tab, after displaying the signal card,
#    add the execution approval badge:
exec_decision = sig.get("execution_decision", {}) if sig else {}
if exec_decision:
    if exec_decision.get("approved"):
        st.success(
            f"✅ Execution Approved — Confidence {exec_decision.get('combined_conf',0):.0f}% | "
            f"Position: {exec_decision.get('position_pct',0):.1%} = ${exec_decision.get('position_usd',0):,.0f}"
        )
    else:
        st.warning(
            f"❌ Execution Blocked: {exec_decision.get('rejection_reason','')}"
        )
'''


# ════════════════════════════════════════════════════════════════
# PATCH 5: Telegram bot — Add intelligence to /signal command
# ════════════════════════════════════════════════════════════════

PATCH_5_BOT_PY = '''
# In telegram_bot/bot.py format_signal_message(), ADD after the entry plan block:

# Intelligence section
intel = sig.get("intelligence", {})
if intel:
    research = intel.get("research", {})
    exec_dec = intel.get("execution", {})
    poly     = intel.get("polymarket", {})

    lines += [f"", f"🧠 *Intelligence Layer*"]

    # Research
    if research:
        sent = research.get("narrative_label", "NEUTRAL")
        conf = research.get("confidence", 0)
        sent_ic = {"BULLISH":"🟢","BEARISH":"🔴","NEUTRAL":"⚪","MIXED":"🟡"}.get(sent,"⚪")
        lines.append(f"  {sent_ic} Sentiment: `{sent}` ({research.get("overall_sentiment",0):+.2f})")
        lines.append(f"  Confidence: `{conf:.0f}%` | Posts: {research.get("total_posts",0)}")
        align = research.get("market_alignment","NEUTRAL")
        align_ic = {"ALIGNED":"✅","DIVERGENT":"⚠️","NEUTRAL":"↔️"}.get(align,"↔️")
        lines.append(f"  {align_ic} Alignment: `{align}`")

    # Execution gate
    if exec_dec:
        if exec_dec.get("approved"):
            lines.append(f"  ✅ Execution: APPROVED ({exec_dec.get("combined_conf",0):.0f}% conf)")
            lines.append(f"     Position: `{exec_dec.get("position_pct",0):.1%}` = `${exec_dec.get("position_usd",0):,.0f}`")
        else:
            lines.append(f"  ❌ Execution: BLOCKED — `{exec_dec.get("rejection_reason","")[:60]}`")

    # Polymarket
    if poly and poly.get("high_conviction"):
        best = poly["high_conviction"][0]
        lines.append(f"  🎰 Poly: {best.get("question","")[:50]} → YES:{best.get("yes_price",0):.0%} Edge:{best.get("edge_vs_signal",0):+.1%}")

# Also add /postmortem command:
async def cmd_postmortem(update, ctx):
    from pathlib import Path
    import json
    pm_dir = Path("data/postmortems")
    files  = sorted(pm_dir.glob("pm_*.json"), reverse=True)[:3]
    if not files:
        await update.message.reply_text("No postmortems on file yet.")
        return
    lines = ["⚗️ *Recent Postmortems*", ""]
    for f in files:
        pm = json.loads(f.read_text())
        sev = pm.get("severity_score", 0)
        lines.append(f"🔴 {pm.get("trade_id","")} | Sev:{sev:.0f}/10 | {pm.get("primary_failure","")}")
        for lesson in pm.get("lessons",[])[:2]:
            lines.append(f"  • {lesson[:80]}")
        lines.append("")
    await update.message.reply_text("\\n".join(lines), parse_mode="MarkdownV2")

# Register: app.add_handler(CommandHandler("postmortem", cmd_postmortem))
'''

if __name__ == "__main__":
    print("=" * 60)
    print("TRINITY.AI INTELLIGENCE LAYER — INTEGRATION GUIDE")
    print("=" * 60)
    print("\n📁 New files created:")
    new_files = [
        "intelligence/research_agents.py  — 5 parallel research agents",
        "intelligence/postmortem.py       — 5-agent loss autopsy system",
        "intelligence/polymarket.py       — Polymarket scanner + edge calc",
        "intelligence/intelligence_hub.py — Integration orchestrator",
        "execution/execution_engine.py    — Confidence-gated execution",
        "streamlit_app/tab_intelligence.py — Dashboard Intelligence tab",
        "INTEGRATION_PATCH.py             — This file (apply patches)",
    ]
    for f in new_files: print(f"  ✅ {f}")

    print("\n📋 Apply patches to existing files:")
    print("  1. core/signal_runner.py    → PATCH_1 (imports) + PATCH_2 (in get_signal)")
    print("  2. streamlit_app/app.py     → PATCH_4 (add tab + execution badge)")
    print("  3. telegram_bot/bot.py      → PATCH_5 (add intel to /signal)")

    print("\n🌍 Required env vars (add to .env / Streamlit secrets):")
    env_vars = [
        "REDDIT_CLIENT_ID      — Reddit API (free at reddit.com/prefs/apps)",
        "REDDIT_CLIENT_SECRET  — Reddit API secret",
        "TWITTER_BEARER_TOKEN  — Twitter API v2 (optional, falls back to CryptoPanic)",
        "CONFIDENCE_THRESHOLD  — Min confidence to execute (default: 55)",
        "MAX_DAILY_LOSS_PCT    — Daily loss circuit breaker (default: 0.05 = 5%)",
        "MAX_CONCURRENT_TRADES — Max open positions (default: 2)",
        "POSTMORTEM_DIR        — Path for postmortem JSON files (default: data/postmortems)",
        "SYSTEM_MEMORY         — Path for accumulated lessons (default: data/system_memory.json)",
    ]
    for v in env_vars: print(f"  {v}")

    print("\n⚡ Quick test:")
    print("  python -c \"from intelligence.research_agents import run_research; r = run_research(); print(r.confidence)\"")
