"""
intelligence/postmortem.py — Trinity.AI Loss Autopsy System v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
After every losing trade, 5 specialized agents run in parallel
to diagnose what went wrong. Their findings are saved, aggregated,
and used to update system weights over time.

5 Postmortem Agents:
  P1 — Entry Timing Analyst       (was the entry premature or late?)
  P2 — Regime Mismatch Detector   (were we trading against the macro?)
  P3 — Indicator Conflict Auditor (which indicators gave false signals?)
  P4 — Sentiment Divergence Check (did sentiment warn us before entry?)
  P5 — Risk Management Evaluator  (was SL too tight? position sized wrong?)

Outputs a PostmortemReport saved to JSON. System reads these back to
auto-adjust confidence thresholds and flag underperforming indicators.

Author: Trinity.AI (Inspired by Ray Dalio's "systematic mistake-learning")
"""

import os
import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("postmortem")

POSTMORTEM_DIR  = Path(os.environ.get("POSTMORTEM_DIR",  "data/postmortems"))
SYSTEM_MEMORY   = Path(os.environ.get("SYSTEM_MEMORY",   "data/system_memory.json"))
POSTMORTEM_DIR.mkdir(parents=True, exist_ok=True)
SYSTEM_MEMORY.parent.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════

@dataclass
class TradeRecord:
    """Full trade snapshot needed for postmortem."""
    trade_id:      str
    direction:     str          # "LONG" | "SHORT"
    entry_price:   float
    exit_price:    float
    entry_time:    str
    exit_time:     str
    exit_reason:   str          # "SL_HIT" | "TP_HIT" | "MANUAL" | "SIGNAL_FLIP"
    pnl_usd:       float
    pnl_pct:       float
    # Signal state at entry
    regime:        str
    long_score:    float
    short_score:   float
    confidence:    float        # research confidence at entry
    sentiment:     str          # "BULLISH" | "BEARISH" | "NEUTRAL" | "MIXED"
    h1_structure:  str
    h4_structure:  str
    bear_override: str
    # Category scores at entry
    trend_score:   float = 0.0
    zone_score:    float = 0.0
    mom_score:     float = 0.0
    pos_score:     float = 0.0
    # Active indicators at entry
    active_indicators: list = field(default_factory=list)
    # Market context
    atr_at_entry:  float = 0.0
    rsi_at_entry:  float = 0.0
    vol_ratio:     float = 0.0
    above_vah:     bool  = False


@dataclass
class AgentVerdict:
    agent:      str
    diagnosis:  str
    severity:   str   # "CRITICAL" | "WARNING" | "INFO"
    root_cause: str
    fix:        str   # actionable fix recommendation


@dataclass
class PostmortemReport:
    trade_id:          str
    timestamp:         str
    trade:             dict
    verdicts:          list[dict]   # list of AgentVerdict dicts
    primary_failure:   str          # P1-P5 most responsible
    severity_score:    float        # 0-10
    system_updates:    dict         # what to update in weights/thresholds
    lessons:           list[str]
    pattern_hash:      str          # hash of conditions to detect repeat mistakes
    repeat_count:      int = 0      # how many times this pattern has caused a loss


# ══════════════════════════════════════════════════════════════════
# POSTMORTEM AGENTS
# ══════════════════════════════════════════════════════════════════

def p1_entry_timing(trade: TradeRecord) -> AgentVerdict:
    """
    P1 — Entry Timing Analyst
    Was entry premature (entered before confirmation)?
    Was entry late (entered at exhaustion)?
    """
    issues = []
    severity = "INFO"
    root_cause = ""
    fix = ""

    rsi = trade.rsi_at_entry
    vol = trade.vol_ratio
    atr = trade.atr_at_entry

    if trade.direction == "LONG":
        # Premature: entered while RSI still falling, no volume confirm
        if rsi > 60 and trade.pnl_pct < 0:
            issues.append(f"Entered LONG with RSI={rsi:.1f} — overbought zone, not a pullback entry")
            severity = "CRITICAL"
            root_cause = "CHASING — entered at momentum exhaustion, not at value"
            fix = "Wait for RSI < 45 on 1H before entering longs. Only enter on pullbacks, not breakouts."

        elif vol < 0.8 and trade.pnl_pct < 0:
            issues.append(f"Low volume at entry (vol_ratio={vol:.2f}) — move had no institutional backing")
            severity = "WARNING"
            root_cause = "FAKEOUT — low conviction move, retail-driven"
            fix = "Require vol_ratio > 1.2 for long entries. Volume must confirm the move."

        if trade.exit_reason == "SL_HIT":
            if atr > 0 and (trade.entry_price - trade.exit_price) / atr < 1.0:
                issues.append("SL was too tight (<1x ATR) — price needed more room to breathe")
                severity = "WARNING"
                root_cause = "TIGHT_STOP"
                fix = "Use minimum 1.5x ATR for stop loss in current regime."

    elif trade.direction == "SHORT":
        if rsi < 40 and trade.pnl_pct < 0:
            issues.append(f"Entered SHORT with RSI={rsi:.1f} — oversold, not ideal short entry")
            severity = "CRITICAL"
            root_cause = "CHASING_SHORT — shorted into oversold conditions"
            fix = "Short entries require RSI > 55 on 1H. Never short extreme oversold conditions."

    if not issues:
        issues.append("Entry timing appears acceptable — timing was not primary failure mode")
        fix = "No timing adjustment needed"
        root_cause = "NOT_PRIMARY_FAILURE"

    return AgentVerdict(
        agent="P1_ENTRY_TIMING",
        diagnosis=" | ".join(issues),
        severity=severity,
        root_cause=root_cause,
        fix=fix,
    )


def p2_regime_mismatch(trade: TradeRecord) -> AgentVerdict:
    """
    P2 — Regime Mismatch Detector
    Were we trading against the macro regime?
    """
    severity = "INFO"
    root_cause = ""
    fix = ""
    diagnosis = ""

    regime = trade.regime
    direction = trade.direction
    override = trade.bear_override

    if regime == "BEAR" and direction == "LONG" and override == "NONE":
        severity = "CRITICAL"
        diagnosis = f"LONG in BEAR regime with NO override — fighting the macro trend"
        root_cause = "REGIME_CONFLICT — system placed a long in a bear market without structure confirmation"
        fix = "In BEAR regime, longs require explicit Level 2/3 override. Raise BEAR long threshold to 9.0+ pts."

    elif regime == "BEAR" and direction == "LONG" and "LEVEL1" in override:
        severity = "WARNING"
        diagnosis = f"LONG in BEAR using Level 1 override (weakest signal) — 4H was still bear-aligned"
        root_cause = "WEAK_OVERRIDE — Level 1 (1H ranging only) is insufficient for reliable MR longs"
        fix = "Disable Level 1 override longs. Only allow Level 2 and Level 3 mean reversion longs in bear market."

    elif regime == "RANGING" and abs(trade.pnl_pct) > 0.02:
        severity = "WARNING"
        diagnosis = f"Ranging regime — used tight TP but trade moved against significantly"
        root_cause = "RANGING_OVERSTAY — held position too long in ranging market"
        fix = "In RANGING regime, use tighter TP (1.5x ATR max). Scale out at 50% at 1.0x ATR."

    elif regime == "STRONG_BULL" and direction == "SHORT":
        severity = "CRITICAL"
        diagnosis = "SHORT in STRONG_BULL regime — directly against macro trend"
        root_cause = "SHORTING_BULL — this should be impossible under current regime config"
        fix = "Confirm STRONG_BULL config has enable_shorts=False. Check regime detection logic."

    elif regime in ("WEAK_BULL", "STRONG_BULL") and direction == "LONG":
        severity = "INFO"
        diagnosis = f"LONG in {regime} — regime was correct, failure elsewhere"
        root_cause = "NOT_REGIME_FAILURE"
        fix = "Regime was aligned. Investigate P1/P3/P4."

    else:
        diagnosis = f"Regime={regime}, direction={direction} — minor mismatch or acceptable setup"
        root_cause = "MINOR_OR_NONE"
        fix = "No major regime change needed"

    return AgentVerdict(
        agent="P2_REGIME_MISMATCH",
        diagnosis=diagnosis, severity=severity,
        root_cause=root_cause, fix=fix,
    )


def p3_indicator_conflict(trade: TradeRecord) -> AgentVerdict:
    """
    P3 — Indicator Conflict Auditor
    Which categories were weak? Did we fire with insufficient confluence?
    """
    diagnosis = []
    severity = "INFO"
    root_cause = ""
    fix = ""

    t = trade.trend_score
    z = trade.zone_score
    m = trade.mom_score
    p = trade.pos_score
    total = t + z + m + p

    # Identify weakest category
    scores = {"trend": t, "zone": z, "momentum": m, "positioning": p}
    weakest = min(scores, key=scores.get)
    weakest_val = scores[weakest]
    active = trade.active_indicators

    if weakest_val == 0.0:
        diagnosis.append(f"Category '{weakest}' scored ZERO — fired without 4-category confluence")
        severity = "CRITICAL"
        root_cause = f"MISSING_CATEGORY — {weakest} had no contributing indicators"
        fix = f"Require minimum 0.5 pts in ALL 4 categories before firing. Current gap: {weakest}=0"

    elif weakest_val < 1.0:
        diagnosis.append(f"Category '{weakest}' scored only {weakest_val:.1f} — very weak contribution")
        severity = "WARNING"
        root_cause = f"WEAK_CATEGORY — {weakest} barely contributed"
        fix = f"Raise min per-category threshold to 1.0 pts. Adjust: MIN_CATEGORY_SCORE = 1.0"

    # Check if key trend indicators were missing
    trend_indicators = ["4H Bias Bullish", "4H Bias Bearish", "EMA Ribbon Bull", "EMA Ribbon Bear",
                        "Above EMA200", "Below EMA200", "BOS Up", "BOS Down"]
    trend_hits = [i for i in active if i in trend_indicators]
    if len(trend_hits) < 2 and trade.direction in ("LONG", "SHORT"):
        diagnosis.append(f"Only {len(trend_hits)} trend indicators active — trend category not robust")
        if severity == "INFO": severity = "WARNING"
        root_cause = root_cause or "WEAK_TREND_CONFIRMATION"
        fix = fix or "Require ≥2 trend indicators active simultaneously. Increase 4h_bias weight by 0.5."

    # Check zone quality
    zone_indicators = ["Near POC", "Near VAL", "Near VAH", "Near Support", "Near Resistance",
                       "Fib 0.618", "Fib 0.500", "AVWAP Bull", "AVWAP Bear", "Bullish OB", "Bearish OB"]
    zone_hits = [i for i in active if i in zone_indicators]
    if len(zone_hits) < 1:
        diagnosis.append("No zone indicator was active — entry had no price level justification")
        severity = "CRITICAL"
        root_cause = root_cause or "NO_ZONE_CONFLUENCE"
        fix = fix or "Zone score must be >0 before entry. Add zone check to cat_gate function."

    if not diagnosis:
        diagnosis.append(f"Indicators were sufficient (T:{t:.1f} Z:{z:.1f} M:{m:.1f} P:{p:.1f}) — not indicator failure")
        root_cause = "NOT_INDICATOR_FAILURE"
        fix = "No indicator changes needed. Investigate timing/regime."

    return AgentVerdict(
        agent="P3_INDICATOR_CONFLICT",
        diagnosis=" | ".join(diagnosis), severity=severity,
        root_cause=root_cause, fix=fix,
    )


def p4_sentiment_divergence(trade: TradeRecord) -> AgentVerdict:
    """
    P4 — Sentiment Divergence Check
    Did research agents warn us before entry and we ignored it?
    """
    severity = "INFO"
    diagnosis = ""
    root_cause = ""
    fix = ""

    sent = trade.sentiment
    conf = trade.confidence
    direction = trade.direction

    if conf < 40.0:
        severity = "CRITICAL"
        diagnosis = f"Research confidence was {conf:.0f}% — BELOW threshold but trade executed anyway"
        root_cause = "LOW_CONFIDENCE_IGNORED — system traded despite insufficient research confirmation"
        fix = f"Enforce confidence threshold: do NOT execute if research_confidence < 50. Current was {conf:.0f}%."

    elif direction == "LONG" and sent == "BEARISH" and conf < 60:
        severity = "WARNING"
        diagnosis = f"Sentiment was BEARISH at entry for LONG trade (confidence {conf:.0f}%)"
        root_cause = "SENTIMENT_HEADWIND — entered long into bearish social sentiment"
        fix = "For LONG entries, require sentiment != BEARISH, OR confidence > 70 (contrarian confirmation)."

    elif direction == "SHORT" and sent == "BULLISH" and conf < 60:
        severity = "WARNING"
        diagnosis = f"Sentiment was BULLISH at entry for SHORT trade (confidence {conf:.0f}%)"
        root_cause = "SENTIMENT_HEADWIND — shorted into bullish sentiment"
        fix = "For SHORT entries, require sentiment != BULLISH, OR confidence > 70."

    elif conf >= 70:
        diagnosis = f"Research confidence was high ({conf:.0f}%) — sentiment did not cause this loss"
        root_cause = "NOT_SENTIMENT_FAILURE"
        fix = "No sentiment threshold changes needed."

    else:
        diagnosis = f"Sentiment={sent}, confidence={conf:.0f}% — moderate signal, not primary failure"
        root_cause = "MINOR_SENTIMENT_CONFLICT"
        fix = "Consider slight confidence threshold increase (+5 pts) to filter borderline cases."

    return AgentVerdict(
        agent="P4_SENTIMENT_DIVERGENCE",
        diagnosis=diagnosis, severity=severity,
        root_cause=root_cause, fix=fix,
    )


def p5_risk_management(trade: TradeRecord) -> AgentVerdict:
    """
    P5 — Risk Management Evaluator
    Was the position sized correctly? Was SL placed optimally?
    Was R:R acceptable before entry?
    """
    severity = "INFO"
    diagnosis = []
    root_cause = ""
    fix = ""

    pnl = trade.pnl_pct
    direction = trade.direction
    regime = trade.regime

    # Check R:R based on regime config expectations
    regime_rr = {
        "STRONG_BULL": 4.0,
        "WEAK_BULL":   1.75,
        "BEAR":        2.0,
        "RANGING":     1.67,
    }
    expected_rr = regime_rr.get(regime, 2.0)

    if pnl < -0.03:  # > 3% loss
        diagnosis.append(f"Large loss ({pnl:.1%}) — exceeded expected max loss for {regime} regime")
        severity = "WARNING"
        root_cause = "OVERSIZED_LOSS"
        fix = f"For {regime}, max acceptable loss per trade = 2%. Use tighter ATR multiplier or smaller position."

    if pnl < -0.05:  # > 5% loss
        severity = "CRITICAL"
        root_cause = "STOP_TOO_WIDE_OR_MOVED"
        fix = "HARD RULE: Never allow single trade loss > 3% of portfolio. Reduce position size by 40%."

    # Check if loss was due to SL hit vs signal flip
    if trade.exit_reason == "SL_HIT":
        diagnosis.append("SL was hit — price moved adversely before signal reversed")
    elif trade.exit_reason == "SIGNAL_FLIP":
        diagnosis.append("Exit was signal flip — entered on false breakout, system caught it quickly")
        if abs(pnl) < 0.01:
            severity = "INFO"
            fix = "Fast signal-flip exit is actually good risk management. No change needed."

    if not diagnosis:
        diagnosis.append(f"Risk management appears within norms (PnL: {pnl:.1%})")
        root_cause = root_cause or "NOT_RISK_FAILURE"
        fix = fix or "No position sizing changes needed."

    return AgentVerdict(
        agent="P5_RISK_MANAGEMENT",
        diagnosis=" | ".join(diagnosis), severity=severity,
        root_cause=root_cause, fix=fix,
    )


# ══════════════════════════════════════════════════════════════════
# SYSTEM MEMORY UPDATER
# ══════════════════════════════════════════════════════════════════

def _compute_pattern_hash(trade: TradeRecord) -> str:
    """Create a fingerprint for this loss pattern to detect repeats."""
    import hashlib
    pattern = f"{trade.regime}_{trade.direction}_{trade.h4_structure}_{trade.h1_structure}_{trade.bear_override}_{int(trade.rsi_at_entry/10)*10}"
    return hashlib.md5(pattern.encode()).hexdigest()[:8]

def generate_system_updates(verdicts: list[AgentVerdict], trade: TradeRecord) -> dict:
    """
    Translate postmortem findings into concrete system parameter updates.
    These are accumulated over time and reviewed weekly.
    """
    updates = {}
    critical = [v for v in verdicts if v.severity == "CRITICAL"]

    for v in critical:
        if v.root_cause == "REGIME_CONFLICT":
            updates["bear_long_min_score_increase"] = +1.0
            updates["note_regime"] = "Bear long threshold raised by 1.0 due to regime conflict loss"

        elif v.root_cause == "LOW_CONFIDENCE_IGNORED":
            updates["confidence_threshold_increase"] = +5.0
            updates["note_confidence"] = f"Confidence threshold raised. Trade fired at {trade.confidence:.0f}%"

        elif v.root_cause in ("CHASING", "CHASING_SHORT"):
            updates["rsi_entry_filter"] = "TIGHTER"
            updates["note_rsi"] = "RSI entry filter tightened after chasing loss"

        elif v.root_cause == "MISSING_CATEGORY":
            updates["enforce_min_per_category"] = True
            updates["note_categories"] = "All 4 categories must have >0 score before entry"

        elif v.root_cause == "NO_ZONE_CONFLUENCE":
            updates["zone_minimum"] = 0.5
            updates["note_zone"] = "Zone score must be >0 (no zone = no entry)"

        elif v.root_cause == "WEAK_OVERRIDE":
            updates["disable_bear_level1_override"] = True
            updates["note_override"] = "Level 1 bear override disabled after repeated losses"

    return updates


# ══════════════════════════════════════════════════════════════════
# MASTER POSTMORTEM RUNNER
# ══════════════════════════════════════════════════════════════════

def run_postmortem(trade: TradeRecord) -> PostmortemReport:
    """
    Runs all 5 postmortem agents in parallel and saves the report.
    Call this immediately after a losing trade closes.
    """
    from concurrent.futures import ThreadPoolExecutor
    ts = datetime.utcnow().isoformat()
    log.info(f"⚗️  Postmortem started | trade={trade.trade_id} pnl={trade.pnl_pct:.1%}")

    # Run 5 agents in parallel
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(p1_entry_timing,      trade): "P1",
            pool.submit(p2_regime_mismatch,   trade): "P2",
            pool.submit(p3_indicator_conflict, trade): "P3",
            pool.submit(p4_sentiment_divergence, trade): "P4",
            pool.submit(p5_risk_management,   trade): "P5",
        }
        verdicts = []
        for f in futures:
            try:
                verdicts.append(f.result())
            except Exception as e:
                log.error(f"Postmortem agent error: {e}")

    # Sort by severity
    sev_order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    verdicts.sort(key=lambda v: sev_order.get(v.severity, 3))

    # Primary failure = most severe agent
    primary = verdicts[0].agent if verdicts else "UNKNOWN"

    # Severity score (0-10)
    crit_count = sum(1 for v in verdicts if v.severity == "CRITICAL")
    warn_count = sum(1 for v in verdicts if v.severity == "WARNING")
    severity_score = min(10.0, crit_count * 3.0 + warn_count * 1.5)

    # System updates
    system_updates = generate_system_updates(verdicts, trade)

    # Lessons
    lessons = [f"[{v.agent}] {v.fix}" for v in verdicts if v.severity in ("CRITICAL", "WARNING")]

    # Pattern detection
    pattern_hash = _compute_pattern_hash(trade)
    repeat_count = 0

    # Check existing postmortems for this pattern
    try:
        existing = load_all_postmortems()
        repeat_count = sum(1 for pm in existing if pm.get("pattern_hash") == pattern_hash)
        if repeat_count > 0:
            lessons.insert(0, f"⚠️  REPEAT PATTERN: This exact setup has failed {repeat_count} time(s) before!")
            if repeat_count >= 3:
                system_updates["block_pattern"] = pattern_hash
                system_updates["note_pattern"] = f"Pattern {pattern_hash} blocked after {repeat_count} losses"
    except Exception as e:
        log.debug(f"Pattern check error: {e}")

    report = PostmortemReport(
        trade_id=trade.trade_id,
        timestamp=ts,
        trade=asdict(trade),
        verdicts=[asdict(v) for v in verdicts],
        primary_failure=primary,
        severity_score=severity_score,
        system_updates=system_updates,
        lessons=lessons,
        pattern_hash=pattern_hash,
        repeat_count=repeat_count,
    )

    # Save report
    save_postmortem(report)
    # Update system memory
    update_system_memory(report)

    log.info(
        f"✅ Postmortem complete | severity={severity_score:.1f}/10 | "
        f"primary={primary} | repeats={repeat_count} | "
        f"updates={list(system_updates.keys())}"
    )
    return report


# ══════════════════════════════════════════════════════════════════
# PERSISTENCE
# ══════════════════════════════════════════════════════════════════

def save_postmortem(report: PostmortemReport):
    fname = POSTMORTEM_DIR / f"pm_{report.trade_id}_{report.timestamp[:10]}.json"
    fname.write_text(json.dumps(asdict(report), indent=2, default=str))
    log.info(f"Postmortem saved: {fname}")

def load_all_postmortems() -> list[dict]:
    results = []
    for f in sorted(POSTMORTEM_DIR.glob("pm_*.json")):
        try:
            results.append(json.loads(f.read_text()))
        except: pass
    return results

def update_system_memory(report: PostmortemReport):
    """Accumulate system update recommendations in system_memory.json."""
    memory = {}
    if SYSTEM_MEMORY.exists():
        try: memory = json.loads(SYSTEM_MEMORY.read_text())
        except: pass

    # Track update counts
    for key, val in report.system_updates.items():
        if key.startswith("note_"): continue
        if key not in memory:
            memory[key] = {"count": 0, "value": val, "first_seen": report.timestamp, "last_seen": report.timestamp}
        else:
            memory[key]["count"] += 1
            memory[key]["last_seen"] = report.timestamp
            if isinstance(val, (int, float)) and isinstance(memory[key]["value"], (int, float)):
                memory[key]["value"] = round(memory[key]["value"] + val, 3)

    # Track loss pattern history
    if "loss_patterns" not in memory:
        memory["loss_patterns"] = {}
    ph = report.pattern_hash
    if ph not in memory["loss_patterns"]:
        memory["loss_patterns"][ph] = {"count": 1, "severity": [], "primary": []}
    else:
        memory["loss_patterns"][ph]["count"] += 1
    memory["loss_patterns"][ph]["severity"].append(report.severity_score)
    memory["loss_patterns"][ph]["primary"].append(report.primary_failure)

    SYSTEM_MEMORY.write_text(json.dumps(memory, indent=2, default=str))

def get_system_recommendations() -> dict:
    """Read accumulated system memory and return actionable recommendations."""
    if not SYSTEM_MEMORY.exists():
        return {}
    memory = json.loads(SYSTEM_MEMORY.read_text())
    recs = {}

    for key, data in memory.items():
        if key == "loss_patterns": continue
        count = data.get("count", 0)
        if count >= 3:  # triggered 3+ times = recommend applying
            recs[key] = {
                "recommendation": f"Apply: {key}={data['value']}",
                "triggered_count": count,
                "confidence": "HIGH" if count >= 5 else "MEDIUM",
            }
    return recs

def format_postmortem_telegram(report: PostmortemReport) -> str:
    """Format postmortem for Telegram notification."""
    sev = report.severity_score
    sev_icon = "🔴" if sev >= 7 else ("🟡" if sev >= 4 else "🟢")

    lines = [
        f"⚗️ *Trade Postmortem — {report.trade_id}*",
        f"{sev_icon} Severity: `{sev:.1f}/10.0`",
        f"",
        f"💸 PnL: `{report.trade['pnl_pct']:.1%}` | Regime: `{report.trade['regime']}`",
        f"",
        f"🔬 *Agent Verdicts:*",
    ]
    for v in report.verdicts:
        icon = "🔴" if v["severity"] == "CRITICAL" else ("🟡" if v["severity"] == "WARNING" else "⚪")
        lines.append(f"  {icon} {v['agent']}: {v['root_cause']}")

    lines.append("")
    lines.append(f"🎯 *Primary Failure:* `{report.primary_failure}`")

    if report.repeat_count > 0:
        lines.append(f"⚠️ *REPEAT PATTERN — failed {report.repeat_count}x before*")

    lines.append("")
    lines.append("📋 *Fixes Applied:*")
    for lesson in report.lessons[:4]:
        lines.append(f"  • {lesson[:80]}")

    return "\n".join(lines)
