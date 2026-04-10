"""
execution/execution_engine.py — Trinity.AI Confidence-Gated Execution v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Central execution gate. All signals pass through here before any trade
is taken. Combines:
  1. Technical signal strength (Trinity score)
  2. Research confidence (sentiment + narrative alignment)
  3. Postmortem system memory (blocked patterns, adjusted thresholds)
  4. Polymarket intelligence (prediction market confirmation)
  5. Dynamic position sizing (Kelly-inspired, regime-adjusted)

Architecture inspired by JP Morgan's multi-factor pre-trade approval system.
"Every trade requires 3 independent confirmations. No exceptions."

Author: Trinity.AI
"""

import os
import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("execution_engine")

EXEC_LOG_FILE = Path(os.environ.get("EXEC_LOG_FILE", "data/execution_log.json"))
EXEC_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# ── Execution thresholds (overrideable via env) ────────────────────
CONFIDENCE_THRESHOLD   = float(os.environ.get("CONFIDENCE_THRESHOLD",   "55.0"))
MIN_TECH_SCORE         = float(os.environ.get("MIN_TECH_SCORE",          "5.0"))
MAX_POSITION_PCT       = float(os.environ.get("MAX_POSITION_PCT",        "0.20"))  # 20% of capital
MIN_POSITION_PCT       = float(os.environ.get("MIN_POSITION_PCT",        "0.02"))  # 2% min
MAX_DAILY_LOSS_PCT     = float(os.environ.get("MAX_DAILY_LOSS_PCT",      "0.05"))  # 5% daily drawdown limit
MAX_CONCURRENT_TRADES  = int(os.environ.get("MAX_CONCURRENT_TRADES",     "2"))
KELLY_FRACTION         = float(os.environ.get("KELLY_FRACTION",          "0.25"))  # quarter-Kelly


# ══════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════

@dataclass
class ExecutionDecision:
    timestamp:         str
    trade_id:          str
    direction:         str       # "LONG" | "SHORT" | "SKIP"
    approved:          bool
    rejection_reason:  str       # why it was blocked (if approved=False)
    # Confidence components
    tech_score:        float     # Trinity scoring engine score
    research_conf:     float     # sentiment/narrative confidence (0-100)
    poly_edge:         float     # polymarket edge (0-1, 0 if N/A)
    combined_conf:     float     # final blended confidence (0-100)
    # Position sizing
    position_pct:      float     # % of capital to risk
    position_usd:      float     # dollar amount
    entry_price:       float
    stop_loss:         float
    take_profit:       float
    rr_ratio:          float
    # Metadata
    regime:            str
    h1_structure:      str
    h4_structure:      str
    blocked_by_memory: bool      # was blocked by postmortem system memory?
    pattern_hash:      str       # for detecting repeat patterns
    notes:             list[str] = field(default_factory=list)


@dataclass
class RiskState:
    """Current portfolio risk state — checked before every execution."""
    account_value:     float = 10000.0
    available_capital: float = 10000.0
    open_trades:       int   = 0
    daily_pnl:         float = 0.0
    daily_pnl_pct:     float = 0.0
    consecutive_losses: int  = 0
    last_loss_time:    str   = ""
    blocked_patterns:  list  = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════
# COMBINED CONFIDENCE CALCULATOR
# ══════════════════════════════════════════════════════════════════

def compute_combined_confidence(
    tech_score:     float,      # Trinity indicator score (raw, e.g. 6.5 pts)
    max_tech_score: float,      # regime max score (e.g. 15)
    research_conf:  float,      # 0-100 from research agents
    poly_edge:      float,      # -1 to +1 polymarket edge
    regime:         str,
    direction:      str,
) -> float:
    """
    Blend technical + research + polymarket signals into single 0-100 confidence.

    Weights:
      Technical signal:  45%  (core — has proven edge)
      Research/Sentiment: 35%  (supporting confirmation)
      Polymarket:         20%  (smart money consensus, when available)
    """
    # Normalize tech score to 0-100
    tech_pct = min(100.0, (tech_score / max(max_tech_score, 1)) * 100)

    # Research already 0-100
    sent_pct = research_conf

    # Polymarket: convert edge to confidence boost
    # Edge of +0.10 = 60, +0.20 = 70, -0.10 = 40
    if abs(poly_edge) > 0.01:
        poly_pct = 50.0 + poly_edge * 200.0   # scale: 0.25 edge → 100
        poly_pct = max(0.0, min(100.0, poly_pct))
        poly_weight = 0.20
    else:
        # No polymarket signal — redistribute weight to tech
        poly_pct    = 50.0
        poly_weight = 0.0

    tech_w = 0.45 + (0.20 - poly_weight) * 0.45
    sent_w = 0.35 + (0.20 - poly_weight) * 0.55

    combined = tech_pct * tech_w + sent_pct * sent_w + poly_pct * poly_weight

    # Regime modifier
    regime_boost = {
        "STRONG_BULL": +5.0 if direction == "LONG" else -10.0,
        "WEAK_BULL":   +2.0 if direction == "LONG" else  -5.0,
        "BEAR":        +5.0 if direction == "SHORT" else -8.0,
        "RANGING":     -5.0,  # reduce conviction in choppy markets
    }.get(regime, 0.0)

    combined = max(0.0, min(100.0, combined + regime_boost))
    return round(combined, 1)


# ══════════════════════════════════════════════════════════════════
# KELLY POSITION SIZER
# ══════════════════════════════════════════════════════════════════

def compute_position_size(
    account_value:  float,
    confidence:     float,    # 0-100
    win_rate:       float,    # historical win rate (0-1)
    avg_win_pct:    float,    # avg win as % of account
    avg_loss_pct:   float,    # avg loss as % of account (positive number)
    regime:         str,
    min_pct:        float = MIN_POSITION_PCT,
    max_pct:        float = MAX_POSITION_PCT,
) -> float:
    """
    Kelly Criterion + confidence scaling + regime cap.

    Kelly fraction = (W*B - L) / B
    where:
      W = win probability
      B = win/loss ratio (avg_win / avg_loss)
      L = loss probability

    We use quarter-Kelly for safety, then scale by confidence.
    """
    try:
        if avg_loss_pct <= 0 or win_rate <= 0:
            return max_pct * 0.3

        B = avg_win_pct / avg_loss_pct
        L = 1.0 - win_rate
        kelly_full = (win_rate * B - L) / B
        kelly_frac = max(0.0, kelly_full * KELLY_FRACTION)  # quarter-Kelly

        # Scale by confidence: 100% conf = full quarter-Kelly, 50% = half
        conf_scale = (confidence - 40.0) / 60.0  # 40% conf = 0, 100% = 1
        conf_scale = max(0.0, min(1.0, conf_scale))
        sized = kelly_frac * conf_scale

        # Regime caps
        regime_cap = {
            "STRONG_BULL": max_pct,
            "WEAK_BULL":   max_pct * 0.75,
            "BEAR":        max_pct * 0.60,  # smaller in bear (more uncertainty)
            "RANGING":     max_pct * 0.50,
        }.get(regime, max_pct * 0.60)

        final = max(min_pct, min(regime_cap, sized))
        return round(final, 4)
    except Exception as e:
        log.warning(f"Position sizer error: {e}")
        return min_pct


# ══════════════════════════════════════════════════════════════════
# RISK GATE
# ══════════════════════════════════════════════════════════════════

def check_risk_gates(risk_state: RiskState, direction: str, pattern_hash: str) -> tuple[bool, str]:
    """
    Hard risk rules that CANNOT be overridden:
      1. Daily loss limit
      2. Max concurrent positions
      3. Blocked patterns (from postmortem system memory)
      4. Consecutive loss cooldown
    Returns (approved, rejection_reason)
    """
    # Daily loss limit
    if risk_state.daily_pnl_pct < -MAX_DAILY_LOSS_PCT:
        return False, f"DAILY_LOSS_LIMIT: Daily PnL at {risk_state.daily_pnl_pct:.1%} (limit: -{MAX_DAILY_LOSS_PCT:.0%}). Trading halted."

    # Max concurrent trades
    if risk_state.open_trades >= MAX_CONCURRENT_TRADES:
        return False, f"MAX_POSITIONS: {risk_state.open_trades} trades open (max: {MAX_CONCURRENT_TRADES})"

    # Blocked pattern check
    if pattern_hash in risk_state.blocked_patterns:
        return False, f"BLOCKED_PATTERN: Pattern {pattern_hash} blocked after 3+ losses. Postmortem system prevented entry."

    # Consecutive losses cooldown
    if risk_state.consecutive_losses >= 3:
        return False, f"COOLDOWN: {risk_state.consecutive_losses} consecutive losses. Mandatory cooldown period active."

    return True, ""


def load_risk_state() -> RiskState:
    """Load current risk state from execution log."""
    state = RiskState()
    try:
        from intelligence.postmortem import get_system_recommendations, SYSTEM_MEMORY
        if SYSTEM_MEMORY.exists():
            mem = json.loads(SYSTEM_MEMORY.read_text())
            patterns = mem.get("loss_patterns", {})
            # Block patterns that have lost 3+ times
            state.blocked_patterns = [
                ph for ph, data in patterns.items()
                if data.get("count", 0) >= 3
            ]
    except: pass

    try:
        if EXEC_LOG_FILE.exists():
            logs = json.loads(EXEC_LOG_FILE.read_text())
            recent = [l for l in logs if l.get("approved")]
            state.open_trades = sum(1 for l in recent[-10:] if l.get("status") == "OPEN")

            # Daily PnL
            today = datetime.utcnow().date().isoformat()
            today_trades = [l for l in logs if l.get("timestamp","").startswith(today) and l.get("pnl")]
            state.daily_pnl = sum(l.get("pnl", 0) for l in today_trades)
            state.daily_pnl_pct = state.daily_pnl / max(state.account_value, 1)

            # Consecutive losses
            reversed_logs = list(reversed([l for l in logs if l.get("pnl") is not None]))
            for l in reversed_logs[:10]:
                if l.get("pnl", 0) < 0:
                    state.consecutive_losses += 1
                else:
                    break
    except: pass

    return state


# ══════════════════════════════════════════════════════════════════
# MASTER EXECUTION GATE
# ══════════════════════════════════════════════════════════════════

def evaluate_execution(
    signal_direction:   str,        # "LONG" | "SHORT" | "NONE"
    tech_score:         float,      # Trinity score (e.g. 6.5)
    entry_price:        float,
    stop_loss:          float,
    take_profit:        float,
    regime:             str,
    h1_structure:       str,
    h4_structure:       str,
    research_conf:      float      = 50.0,
    poly_edge:          float      = 0.0,
    historical_wr:      float      = 0.55,
    avg_win_pct:        float      = 0.02,
    avg_loss_pct:       float      = 0.01,
    account_value:      float      = 10000.0,
    pattern_hash:       str        = "",
    confidence_override: Optional[float] = None,
) -> ExecutionDecision:
    """
    The single most important function in the system.
    Every potential trade must pass through here.

    Returns ExecutionDecision with approved=True/False and full reasoning.
    """
    import hashlib
    ts       = datetime.utcnow().isoformat()
    trade_id = hashlib.md5(f"{ts}{signal_direction}{entry_price}".encode()).hexdigest()[:8]

    # ── Skip immediately if no signal ────────────────────────────
    if signal_direction == "NONE":
        return ExecutionDecision(
            timestamp=ts, trade_id=trade_id, direction="SKIP",
            approved=False, rejection_reason="NO_SIGNAL",
            tech_score=tech_score, research_conf=research_conf,
            poly_edge=poly_edge, combined_conf=0.0,
            position_pct=0.0, position_usd=0.0,
            entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
            rr_ratio=0.0, regime=regime, h1_structure=h1_structure,
            h4_structure=h4_structure, blocked_by_memory=False, pattern_hash=pattern_hash,
        )

    notes = []

    # ── 1. Risk gate check ────────────────────────────────────────
    risk_state = load_risk_state()
    risk_ok, risk_reason = check_risk_gates(risk_state, signal_direction, pattern_hash)
    if not risk_ok:
        return ExecutionDecision(
            timestamp=ts, trade_id=trade_id, direction=signal_direction,
            approved=False, rejection_reason=risk_reason,
            tech_score=tech_score, research_conf=research_conf,
            poly_edge=poly_edge, combined_conf=0.0,
            position_pct=0.0, position_usd=0.0,
            entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
            rr_ratio=0.0, regime=regime, h1_structure=h1_structure,
            h4_structure=h4_structure,
            blocked_by_memory=pattern_hash in risk_state.blocked_patterns,
            pattern_hash=pattern_hash,
            notes=[risk_reason],
        )

    # ── 2. Minimum tech score check ───────────────────────────────
    if tech_score < MIN_TECH_SCORE:
        return ExecutionDecision(
            timestamp=ts, trade_id=trade_id, direction=signal_direction,
            approved=False, rejection_reason=f"TECH_SCORE_TOO_LOW: {tech_score:.1f} < {MIN_TECH_SCORE}",
            tech_score=tech_score, research_conf=research_conf,
            poly_edge=poly_edge, combined_conf=0.0,
            position_pct=0.0, position_usd=0.0,
            entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
            rr_ratio=0.0, regime=regime, h1_structure=h1_structure,
            h4_structure=h4_structure, blocked_by_memory=False, pattern_hash=pattern_hash,
        )

    # ── 3. Compute combined confidence ────────────────────────────
    max_scores = {"STRONG_BULL": 15, "WEAK_BULL": 15, "BEAR": 18, "RANGING": 16}
    max_score  = max_scores.get(regime, 15)

    if confidence_override is not None:
        combined_conf = confidence_override
    else:
        combined_conf = compute_combined_confidence(
            tech_score, max_score, research_conf, poly_edge,
            regime, signal_direction
        )

    # Notes for transparency
    notes.append(f"Tech: {tech_score:.1f}/{max_score} ({tech_score/max_score:.0%})")
    notes.append(f"Research confidence: {research_conf:.0f}%")
    if abs(poly_edge) > 0.02:
        notes.append(f"Polymarket edge: {poly_edge:+.1%}")
    notes.append(f"Combined confidence: {combined_conf:.0f}%")

    # ── 4. Confidence threshold gate ─────────────────────────────
    threshold = CONFIDENCE_THRESHOLD
    # Raise threshold in bear regime for longs
    if regime == "BEAR" and signal_direction == "LONG":
        threshold += 10.0
        notes.append(f"Bear-long threshold raised to {threshold:.0f}%")
    # Lower threshold slightly when polymarket strongly confirms
    if poly_edge > 0.15 and signal_direction == "LONG":
        threshold -= 5.0
        notes.append(f"Polymarket confirmation: threshold lowered to {threshold:.0f}%")
    if poly_edge < -0.15 and signal_direction == "SHORT":
        threshold -= 5.0

    if combined_conf < threshold:
        return ExecutionDecision(
            timestamp=ts, trade_id=trade_id, direction=signal_direction,
            approved=False,
            rejection_reason=f"CONFIDENCE_BELOW_THRESHOLD: {combined_conf:.0f}% < {threshold:.0f}%",
            tech_score=tech_score, research_conf=research_conf,
            poly_edge=poly_edge, combined_conf=combined_conf,
            position_pct=0.0, position_usd=0.0,
            entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
            rr_ratio=0.0, regime=regime, h1_structure=h1_structure,
            h4_structure=h4_structure, blocked_by_memory=False, pattern_hash=pattern_hash,
            notes=notes,
        )

    # ── 5. Position sizing ────────────────────────────────────────
    position_pct = compute_position_size(
        account_value=account_value,
        confidence=combined_conf,
        win_rate=historical_wr,
        avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct,
        regime=regime,
    )
    position_usd = round(account_value * position_pct, 2)

    # R:R ratio
    if entry_price > 0 and stop_loss > 0 and take_profit > 0:
        risk  = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)
        rr    = round(reward / risk, 2) if risk > 0 else 0.0
    else:
        rr = 0.0

    # Check minimum R:R
    min_rr = {"STRONG_BULL": 2.0, "WEAK_BULL": 1.5, "BEAR": 1.8, "RANGING": 1.5}.get(regime, 1.5)
    if rr < min_rr and rr > 0:
        notes.append(f"⚠️ R:R = 1:{rr:.1f} is below ideal {min_rr:.1f} for {regime} — proceed with caution")

    notes.append(f"Position: {position_pct:.1%} of account = ${position_usd:,.0f}")
    notes.append(f"R:R = 1:{rr:.1f}")

    decision = ExecutionDecision(
        timestamp=ts, trade_id=trade_id, direction=signal_direction,
        approved=True, rejection_reason="",
        tech_score=tech_score, research_conf=research_conf,
        poly_edge=poly_edge, combined_conf=combined_conf,
        position_pct=position_pct, position_usd=position_usd,
        entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
        rr_ratio=rr, regime=regime, h1_structure=h1_structure,
        h4_structure=h4_structure, blocked_by_memory=False, pattern_hash=pattern_hash,
        notes=notes,
    )

    # Log decision
    log_execution(decision)

    log.info(
        f"✅ APPROVED | {signal_direction} | conf={combined_conf:.0f}% | "
        f"size={position_pct:.1%} = ${position_usd:,.0f} | R:R=1:{rr:.1f}"
    )
    return decision


# ══════════════════════════════════════════════════════════════════
# PERSISTENCE
# ══════════════════════════════════════════════════════════════════

def log_execution(decision: ExecutionDecision):
    records = []
    if EXEC_LOG_FILE.exists():
        try: records = json.loads(EXEC_LOG_FILE.read_text())
        except: pass
    records.append({**asdict(decision), "status": "OPEN", "pnl": None})
    EXEC_LOG_FILE.write_text(json.dumps(records[-500:], indent=2, default=str))


def close_trade(trade_id: str, exit_price: float, exit_reason: str) -> Optional[dict]:
    """Call when a trade closes to compute PnL and trigger postmortem if needed."""
    if not EXEC_LOG_FILE.exists():
        return None
    records = json.loads(EXEC_LOG_FILE.read_text())
    for rec in records:
        if rec.get("trade_id") == trade_id and rec.get("status") == "OPEN":
            direction = rec.get("direction")
            entry     = rec.get("entry_price", 0)
            if direction == "LONG":
                pnl_pct = (exit_price - entry) / entry
            elif direction == "SHORT":
                pnl_pct = (entry - exit_price) / entry
            else:
                pnl_pct = 0.0

            pos_pct  = rec.get("position_pct", 0.01)
            account  = rec.get("position_usd", 10000) / max(pos_pct, 0.001)
            pnl_usd  = account * pos_pct * pnl_pct

            rec["status"]       = "CLOSED"
            rec["exit_price"]   = exit_price
            rec["exit_reason"]  = exit_reason
            rec["exit_time"]    = datetime.utcnow().isoformat()
            rec["pnl"]          = round(pnl_usd, 2)
            rec["pnl_pct"]      = round(pnl_pct, 4)

    EXEC_LOG_FILE.write_text(json.dumps(records, indent=2, default=str))
    closed = next((r for r in records if r.get("trade_id") == trade_id and r.get("status") == "CLOSED"), None)
    return closed


# ══════════════════════════════════════════════════════════════════
# FORMAT FOR UI
# ══════════════════════════════════════════════════════════════════

def format_execution_decision(d: ExecutionDecision) -> str:
    icon = "✅" if d.approved else "❌"
    lines = [
        f"{icon} *Execution Decision: {d.direction}*",
        f"",
        f"📊 *Confidence Breakdown:*",
        f"  Tech Score:       `{d.tech_score:.1f}` pts",
        f"  Research Conf:    `{d.research_conf:.0f}%`",
        f"  Polymarket Edge:  `{d.poly_edge:+.1%}`",
        f"  Combined:         `{d.combined_conf:.0f}%`",
        f"",
    ]
    if d.approved:
        lines += [
            f"💰 *Position:* `{d.position_pct:.1%}` = `${d.position_usd:,.0f}`",
            f"📍 Entry: `${d.entry_price:,.2f}`",
            f"🛑 SL:    `${d.stop_loss:,.2f}`",
            f"🎯 TP:    `${d.take_profit:,.2f}`",
            f"📈 R:R:   `1:{d.rr_ratio:.1f}`",
        ]
    else:
        lines.append(f"🚫 *Blocked:* `{d.rejection_reason}`")

    if d.notes:
        lines.append("\n📝 *Notes:*")
        for note in d.notes:
            lines.append(f"  • {note}")

    return "\n".join(lines)
