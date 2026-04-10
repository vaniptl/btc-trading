"""
streamlit_app/tab_intelligence.py — Trinity.AI Intelligence Dashboard Tab
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Renders the Intelligence tab with:
  - Research Agent Report (sentiment, narrative, top headlines)
  - Execution Decision Gate (confidence breakdown + approval status)
  - Postmortem History (loss autopsy timeline + lessons learned)
  - Polymarket Intelligence (BTC prediction markets with edge analysis)

Import in app.py:
    from tab_intelligence import render_intelligence_tab
    with tab_intel:
        render_intelligence_tab(sig, intel)

Author: Trinity.AI
"""

import json
import streamlit as st
import pandas as pd
from pathlib import Path


# ── Color/icon helpers ──────────────────────────────────────────────

def _sent_color(label: str) -> str:
    return {"BULLISH": "#00c853", "BEARISH": "#d50000", "NEUTRAL": "#555", "MIXED": "#f9a825"}.get(label, "#555")

def _sev_icon(sev: str) -> str:
    return {"CRITICAL": "🔴", "WARNING": "🟡", "INFO": "⚪"}.get(sev, "⚪")

def _align_icon(a: str) -> str:
    return {"ALIGNED": "✅", "DIVERGENT": "⚠️", "NEUTRAL": "↔️"}.get(a, "↔️")


# ══════════════════════════════════════════════════════════════════
# SECTION 1: RESEARCH AGENTS
# ══════════════════════════════════════════════════════════════════

def _render_research(research: dict):
    if not research:
        st.info("Research agents not yet run. Click 🔄 Refresh on Signal tab.")
        return

    sent_label = research.get("narrative_label", "NEUTRAL")
    sent_comp  = research.get("overall_sentiment", 0.0)
    alignment  = research.get("market_alignment", "NEUTRAL")
    confidence = research.get("confidence", 0.0)
    bull_pct   = research.get("bull_pct", 0.0)
    bear_pct   = research.get("bear_pct", 0.0)

    # Sentiment card
    sc = _sent_color(sent_label)
    st.markdown(f"""
    <div style="background:{sc};border-radius:12px;padding:14px 18px;margin-bottom:10px">
      <b style="color:white;font-size:1.15em">
        {{"BULLISH":"🟢","BEARISH":"🔴","NEUTRAL":"⚪","MIXED":"🟡"}}.get("{sent_label}","⚪") 
        Sentiment: {sent_label} ({sent_comp:+.2f})
      </b>
    </div>
    """.replace('{{"BULLISH":"🟢","BEARISH":"🔴","NEUTRAL":"⚪","MIXED":"🟡"}}.get("{sent_label}","⚪")',
                {"BULLISH":"🟢","BEARISH":"🔴","NEUTRAL":"⚪","MIXED":"🟡"}.get(sent_label,"⚪")),
    unsafe_allow_html=True)

    # Stats row
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Posts Scraped",  research.get("total_posts", 0))
    c2.metric("🐦 Twitter",     research.get("twitter_posts", 0))
    c3.metric("🤖 Reddit",      research.get("reddit_posts", 0))
    c4.metric("📰 RSS",         research.get("rss_articles", 0))

    # Sentiment distribution
    st.markdown("**Sentiment Distribution**")
    sent_df = pd.DataFrame({
        "Sentiment": ["🟢 Bullish", "🔴 Bearish", "⚪ Neutral"],
        "Percentage": [bull_pct * 100, bear_pct * 100, research.get("neutral_pct", 0) * 100],
    })
    st.bar_chart(sent_df.set_index("Sentiment"), use_container_width=True, height=150)

    # Alignment note
    st.markdown(f"**{_align_icon(alignment)} Market Alignment: {alignment}**")
    st.caption(research.get("alignment_note", ""))

    # Confidence
    conf_color = "#00c853" if confidence >= 65 else ("#f9a825" if confidence >= 45 else "#d50000")
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:12px;margin:8px 0">
      <span style="font-size:0.9em;color:#aaa">Research Confidence</span>
      <div style="flex:1;background:#1e1e1e;border-radius:6px;height:10px">
        <div style="width:{confidence}%;background:{conf_color};height:100%;border-radius:6px"></div>
      </div>
      <b style="color:{conf_color}">{confidence:.0f}%</b>
    </div>
    """, unsafe_allow_html=True)

    # Keywords
    kw = research.get("top_keywords", [])
    if kw:
        st.markdown("**Top Keywords**")
        kw_html = "".join(
            f'<span style="background:#1e3a2f;color:#00c853;border-radius:20px;padding:2px 10px;margin:2px;font-size:0.75em">{k}</span>'
            for k in kw[:10]
        )
        st.markdown(kw_html, unsafe_allow_html=True)

    # Headlines
    headlines = research.get("top_headlines", [])
    if headlines:
        st.markdown("**📰 Top Headlines**")
        for h in headlines[:5]:
            source = h.get("source", "")
            title  = h.get("title", "")
            url    = h.get("url", "")
            ts_    = h.get("ts", "")[:16] if h.get("ts") else ""
            if url:
                st.markdown(f"• [{source}] [{title[:75]}]({url}) `{ts_}`")
            else:
                st.caption(f"• [{source}] {title[:80]} `{ts_}`")


# ══════════════════════════════════════════════════════════════════
# SECTION 2: EXECUTION DECISION GATE
# ══════════════════════════════════════════════════════════════════

def _render_execution(execution: dict):
    if not execution:
        st.info("Execution gate not evaluated yet.")
        return

    approved  = execution.get("approved", False)
    direction = execution.get("direction", "NONE")
    conf      = execution.get("combined_conf", 0.0)
    reason    = execution.get("rejection_reason", "")
    tech      = execution.get("tech_score", 0.0)
    research  = execution.get("research_conf", 0.0)
    poly_edge = execution.get("poly_edge", 0.0)
    notes     = execution.get("notes", [])

    # Decision banner
    if approved:
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#00c853,#1b5e20);border-radius:12px;padding:16px;text-align:center;color:white">
          <b style="font-size:1.3em">✅ EXECUTION APPROVED — {direction}</b><br>
          <span style="font-size:0.9em">Combined Confidence: {conf:.0f}%</span>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#424242,#212121);border-radius:12px;padding:16px;text-align:center;color:#ccc">
          <b style="font-size:1.3em">❌ EXECUTION BLOCKED</b><br>
          <span style="font-size:0.85em;color:#f44336">{reason}</span>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("")

    # Confidence breakdown
    st.markdown("**Confidence Breakdown**")
    conf_data = {
        "Source": ["⚡ Technical Signal", "📡 Research/Sentiment", "🎰 Polymarket Edge", "🎯 Combined"],
        "Score":  [
            f"{tech:.1f} pts",
            f"{research:.0f}%",
            f"{poly_edge:+.1%}" if abs(poly_edge) > 0.01 else "N/A",
            f"{conf:.0f}%",
        ],
        "Weight": ["45%", "35%", "20%", "100%"],
    }
    st.dataframe(pd.DataFrame(conf_data), use_container_width=True, hide_index=True)

    if approved:
        # Position details
        pos_pct = execution.get("position_pct", 0)
        pos_usd = execution.get("position_usd", 0)
        entry   = execution.get("entry_price", 0)
        sl      = execution.get("stop_loss", 0)
        tp      = execution.get("take_profit", 0)
        rr      = execution.get("rr_ratio", 0)

        st.markdown("**Position Details**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Size",         f"{pos_pct:.1%} = ${pos_usd:,.0f}")
        c2.metric("Entry",        f"${entry:,.2f}")
        c3.metric("Stop Loss",    f"${sl:,.2f}")
        c4.metric("Take Profit",  f"${tp:,.2f}")
        st.info(f"R:R Ratio: 1:{rr:.1f} | Kelly-adjusted position sizing")

    if notes:
        with st.expander("📝 Decision Notes"):
            for note in notes:
                st.caption(f"• {note}")


# ══════════════════════════════════════════════════════════════════
# SECTION 3: POSTMORTEM HISTORY
# ══════════════════════════════════════════════════════════════════

def _render_postmortems():
    postmortem_dir = Path("data/postmortems")
    system_memory  = Path("data/system_memory.json")

    # System recommendations from accumulated postmortems
    if system_memory.exists():
        try:
            mem = json.loads(system_memory.read_text())
            patterns = mem.get("loss_patterns", {})
            non_note = {k: v for k, v in mem.items() if k != "loss_patterns" and not k.startswith("note")}

            if non_note:
                st.markdown("**🧬 System Memory Updates (from postmortems)**")
                rec_rows = []
                for key, data in non_note.items():
                    count = data.get("count", 0)
                    val   = data.get("value", "")
                    conf  = "🔴 HIGH" if count >= 5 else ("🟡 MEDIUM" if count >= 3 else "⚪ LOW")
                    rec_rows.append({
                        "Update":    key.replace("_", " ").title(),
                        "Value":     str(val),
                        "Triggered": count,
                        "Confidence": conf,
                    })
                st.dataframe(pd.DataFrame(rec_rows), use_container_width=True, hide_index=True)

            # Pattern history
            if patterns:
                st.markdown("**🔁 Loss Pattern Registry**")
                pat_rows = []
                for ph, data in list(patterns.items())[:10]:
                    count = data.get("count", 0)
                    avg_sev = sum(data.get("severity", [0])) / len(data.get("severity", [1]))
                    blocked = "🚫 BLOCKED" if count >= 3 else ("⚠️ WATCH" if count >= 2 else "📋 LOG")
                    pat_rows.append({
                        "Pattern Hash": ph,
                        "Loss Count": count,
                        "Avg Severity": f"{avg_sev:.1f}/10",
                        "Status": blocked,
                    })
                st.dataframe(pd.DataFrame(pat_rows), use_container_width=True, hide_index=True)
        except Exception as e:
            st.caption(f"System memory read error: {e}")

    # Postmortem files
    if not postmortem_dir.exists():
        st.info("No postmortems yet — they run automatically after losing trades.")
        return

    pm_files = sorted(postmortem_dir.glob("pm_*.json"), reverse=True)
    if not pm_files:
        st.info("No postmortems on file. System is watching for losing trades.")
        return

    st.markdown(f"**{len(pm_files)} Postmortem(s) on file**")

    for f in pm_files[:5]:
        try:
            pm = json.loads(f.read_text())
            trade  = pm.get("trade", {})
            sev    = pm.get("severity_score", 0)
            sev_ic = "🔴" if sev >= 7 else ("🟡" if sev >= 4 else "🟢")
            primary = pm.get("primary_failure", "")
            repeat  = pm.get("repeat_count", 0)
            rep_str = f" ⚠️ REPEAT x{repeat}" if repeat > 0 else ""

            with st.expander(
                f"{sev_ic} {pm.get('trade_id','')} — "
                f"Severity {sev:.0f}/10 | {primary}{rep_str} | "
                f"PnL: {trade.get('pnl_pct', 0):.1%}"
            ):
                # Agent verdicts
                st.markdown("**5-Agent Verdicts:**")
                for v in pm.get("verdicts", []):
                    icon = _sev_icon(v.get("severity", "INFO"))
                    st.markdown(
                        f"{icon} **{v.get('agent','')}**: `{v.get('root_cause','')}` — "
                        f"{v.get('diagnosis','')[:100]}"
                    )

                # Lessons
                lessons = pm.get("lessons", [])
                if lessons:
                    st.markdown("**📋 Lessons & Fixes:**")
                    for l in lessons[:4]:
                        st.caption(f"• {l[:120]}")

                # System updates
                updates = pm.get("system_updates", {})
                if updates:
                    st.markdown("**⚙️ System Updates Applied:**")
                    for k, v in updates.items():
                        if not k.startswith("note"):
                            st.code(f"{k} = {v}", language="python")
        except Exception as e:
            st.caption(f"Error reading {f.name}: {e}")


# ══════════════════════════════════════════════════════════════════
# SECTION 4: POLYMARKET INTELLIGENCE
# ══════════════════════════════════════════════════════════════════

def _render_polymarket(polymarket: dict):
    if not polymarket:
        st.info("Polymarket scan not yet run.")
        return

    total   = polymarket.get("total_scanned", 0)
    rel     = polymarket.get("relevant_count", 0)
    summary = polymarket.get("edge_summary", "")

    st.markdown(f"**Scanned {total} markets — {rel} BTC-relevant**")

    if summary:
        st.info(f"💡 {summary}")

    # High conviction table
    hc = polymarket.get("high_conviction", [])
    if hc:
        st.markdown("**🎯 High/Medium Conviction Bets**")
        table = []
        for m in hc:
            conv_ic = "🟢" if m.get("conviction") == "HIGH" else "🟡"
            table.append({
                "": conv_ic,
                "Question":    m.get("question", "")[:60],
                "YES %":       f"{m.get('yes_price', 0):.0%}",
                "Liquidity":   f"${m.get('liquidity', 0):,.0f}",
                "Our Edge":    f"{m.get('edge_vs_signal', 0):+.1%}",
                "Bet":         m.get("recommended_bet", "SKIP"),
                "Ends":        m.get("end_date", ""),
                "Hist. Acc":   f"{m.get('historical_accuracy', 0):.0f}%",
            })
        st.dataframe(pd.DataFrame(table), use_container_width=True, hide_index=True)

        # Context for top market
        top = hc[0]
        st.markdown(f"**Context: _{top.get('question', '')}_ **")
        st.caption(top.get("context_note", ""))
        st.caption(f"Category: {top.get('relevance', '')} | Volume 24h: ${top.get('volume_24h', 0):,.0f}")

    # Macro context
    macro = polymarket.get("macro_context", [])
    if macro:
        st.markdown("**📊 Macro Context Markets** _(watch for BTC impact)_")
        macro_rows = []
        for m in macro:
            macro_rows.append({
                "Question": m.get("question", "")[:65],
                "YES %":    f"{m.get('yes_price', 0):.0%}",
                "Liquidity":f"${m.get('liquidity', 0):,.0f}",
                "Ends":     m.get("end_date", ""),
            })
        st.dataframe(pd.DataFrame(macro_rows), use_container_width=True, hide_index=True)

    if not hc and not macro:
        st.caption("No high-liquidity BTC-relevant markets found at this time.")


# ══════════════════════════════════════════════════════════════════
# MASTER RENDER FUNCTION
# ══════════════════════════════════════════════════════════════════

def render_intelligence_tab(sig: dict = None, intel: dict = None):
    """
    Main entry point. Call from app.py inside the Intelligence tab.

    sig   — the full signal dict from sr.get_signal()
    intel — the intelligence layer dict from run_intelligence_layer()
            (can be None if not yet fetched, tab will show placeholders)
    """
    st.markdown("## 🧠 Intelligence Layer")
    st.caption("Research Agents · Execution Gate · Loss Autopsy · Polymarket Intel")

    if intel is None and sig:
        intel = sig.get("intelligence", {})

    # Errors banner
    if intel and intel.get("errors"):
        with st.expander(f"⚠️ {len(intel['errors'])} agent error(s)"):
            for e in intel["errors"]:
                st.caption(f"• {e}")

    # ── Sub-tabs ────────────────────────────────────────────────
    it1, it2, it3, it4 = st.tabs([
        "📡 Research",
        "🎯 Execution Gate",
        "⚗️ Postmortems",
        "🎰 Polymarket",
    ])

    with it1:
        research = (intel or {}).get("research")
        if not research and sig:
            research = sig.get("research")
        st.markdown("### Research Agents Report")
        st.caption("Twitter · Reddit · RSS Feeds → Sentiment Synthesis → Narrative vs Market")
        _render_research(research)

    with it2:
        execution = (intel or {}).get("execution")
        if not execution and sig:
            execution = sig.get("execution_decision")
        st.markdown("### Execution Decision Gate")
        st.caption("All signals must pass confidence threshold + risk gates before execution")
        _render_execution(execution)

    with it3:
        st.markdown("### Loss Autopsy System")
        st.caption("5 agents run after every losing trade · Findings accumulate in system memory")
        _render_postmortems()

    with it4:
        poly = (intel or {}).get("polymarket")
        if not poly and sig:
            poly = sig.get("polymarket")
        st.markdown("### Polymarket Intelligence")
        st.caption("Active BTC prediction markets · Historical accuracy · Our edge vs crowd")
        _render_polymarket(poly)
