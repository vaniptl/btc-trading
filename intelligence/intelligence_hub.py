"""
intelligence/intelligence_hub.py — Trinity.AI Intelligence Integration v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Single import point for signal_runner.py and app.py.
Runs research + polymarket + execution gate as one call.
Handles parallel execution, caching, and timeout safety.

Usage in signal_runner.get_signal():
    from intelligence.intelligence_hub import run_intelligence_layer
    intel = run_intelligence_layer(signal, regime, price, ema50, ema200)
    signal["intelligence"] = intel
    signal["execution_decision"] = intel["execution"]

Author: Trinity.AI
"""

import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import asdict
from typing import Optional

log = logging.getLogger("intelligence_hub")


def run_intelligence_layer(
    signal_direction: str,
    tech_score:       float,
    regime:           str,
    btc_price:        float,
    ema50:            float,
    ema200:           float,
    entry_price:      float,
    stop_loss:        float,
    take_profit:      float,
    h1_structure:     str   = "ranging",
    h4_structure:     str   = "ranging",
    account_value:    float = 10000.0,
    run_research:     bool  = True,
    run_polymarket:   bool  = True,
    lookback_hours:   int   = 6,
    timeout_seconds:  int   = 45,
) -> dict:
    """
    Runs Research + Polymarket in parallel (both I/O bound).
    Then runs Execution Gate synchronously.
    Returns full intelligence dict for inclusion in signal payload.

    Total max runtime: timeout_seconds (default 45s)
    If any agent times out, it's skipped gracefully.
    """
    result = {
        "research":     None,
        "polymarket":   None,
        "execution":    None,
        "errors":       [],
    }

    # ── Parallel I/O layer ────────────────────────────────────────
    futures_map = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        if run_research:
            try:
                from intelligence.research_agents import run_research as _run_research
                f = pool.submit(
                    _run_research,
                    market_regime=regime,
                    current_price=btc_price,
                    ema50=ema50,
                    ema200=ema200,
                    signal_dir=signal_direction,
                    lookback_hours=lookback_hours,
                )
                futures_map["research"] = f
            except Exception as e:
                result["errors"].append(f"research_import: {e}")

        if run_polymarket:
            try:
                from intelligence.polymarket import run_polymarket_scan
                f = pool.submit(
                    run_polymarket_scan,
                    btc_price=btc_price,
                    market_regime=regime,
                    signal_dir=signal_direction,
                )
                futures_map["polymarket"] = f
            except Exception as e:
                result["errors"].append(f"polymarket_import: {e}")

        # Collect results with timeout
        for name, future in futures_map.items():
            try:
                r = future.result(timeout=timeout_seconds)
                result[name] = asdict(r) if hasattr(r, "__dataclass_fields__") else r
            except FuturesTimeout:
                result["errors"].append(f"{name}_timeout after {timeout_seconds}s")
                log.warning(f"{name} agent timed out after {timeout_seconds}s")
            except Exception as e:
                result["errors"].append(f"{name}_error: {e}")
                log.warning(f"{name} agent error: {traceback.format_exc()[:200]}")

    # ── Extract research confidence for execution gate ────────────
    research_conf = 50.0   # default
    poly_edge     = 0.0    # default
    pattern_hash  = ""

    if result["research"]:
        research_conf = float(result["research"].get("confidence", 50.0))

    if result["polymarket"]:
        # Get best edge from high-conviction markets
        hc = result["polymarket"].get("high_conviction", [])
        if hc:
            poly_edge = float(hc[0].get("edge_vs_signal", 0.0))

    # Pattern hash for postmortem system
    try:
        import hashlib
        pattern_hash = hashlib.md5(
            f"{regime}_{signal_direction}_{h4_structure}_{h1_structure}".encode()
        ).hexdigest()[:8]
    except: pass

    # ── Execution gate ────────────────────────────────────────────
    try:
        from execution.execution_engine import evaluate_execution, load_risk_state
        # Get historical metrics from memory
        from core.signal_runner import load_memory
        mem = load_memory()
        hist_wr    = 0.55; avg_win = 0.025; avg_loss = 0.012
        if len(mem) >= 5:
            wins_list  = [r.get("win_rate", 0.55) for r in mem[-20:]]
            hist_wr    = sum(wins_list) / len(wins_list)
            wins_usd   = [r.get("avg_win",  0) for r in mem[-20:] if r.get("avg_win")]
            losses_usd = [r.get("avg_loss", 0) for r in mem[-20:] if r.get("avg_loss")]
            if wins_usd and losses_usd and account_value > 0:
                avg_win  = abs(sum(wins_usd)   / len(wins_usd))   / account_value
                avg_loss = abs(sum(losses_usd) / len(losses_usd)) / account_value

        exec_decision = evaluate_execution(
            signal_direction=signal_direction,
            tech_score=tech_score,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            regime=regime,
            h1_structure=h1_structure,
            h4_structure=h4_structure,
            research_conf=research_conf,
            poly_edge=poly_edge,
            historical_wr=hist_wr,
            avg_win_pct=avg_win,
            avg_loss_pct=avg_loss,
            account_value=account_value,
            pattern_hash=pattern_hash,
        )
        result["execution"] = asdict(exec_decision)
    except Exception as e:
        result["errors"].append(f"execution_gate: {e}")
        log.warning(f"Execution gate error: {traceback.format_exc()[:300]}")
        # Fallback: basic decision
        result["execution"] = {
            "approved":  signal_direction != "NONE" and tech_score >= 5.0 and research_conf >= 50.0,
            "direction": signal_direction,
            "combined_conf": research_conf,
            "rejection_reason": "execution_engine_unavailable" if signal_direction == "NONE" else "",
            "notes": result["errors"],
        }

    # Attach summary fields for easy access
    result["confidence"]         = research_conf
    result["poly_edge"]          = poly_edge
    result["execution_approved"] = result["execution"].get("approved", False) if result["execution"] else False
    result["sentiment"]          = (result["research"] or {}).get("narrative_label", "NEUTRAL")
    result["alignment"]          = (result["research"] or {}).get("market_alignment", "NEUTRAL")

    return result


def trigger_postmortem_if_loss(closed_trade: dict) -> Optional[dict]:
    """
    Call after trade_close() when pnl < 0.
    Builds TradeRecord from closed trade data and runs 5-agent postmortem.
    Returns PostmortemReport dict or None on error.
    """
    if not closed_trade or closed_trade.get("pnl", 0) >= 0:
        return None

    try:
        from intelligence.postmortem import run_postmortem, TradeRecord
        t = TradeRecord(
            trade_id=closed_trade.get("trade_id", "unknown"),
            direction=closed_trade.get("direction", "LONG"),
            entry_price=float(closed_trade.get("entry_price", 0)),
            exit_price=float(closed_trade.get("exit_price", 0)),
            entry_time=closed_trade.get("timestamp", ""),
            exit_time=closed_trade.get("exit_time", ""),
            exit_reason=closed_trade.get("exit_reason", "UNKNOWN"),
            pnl_usd=float(closed_trade.get("pnl", 0)),
            pnl_pct=float(closed_trade.get("pnl_pct", 0)),
            regime=closed_trade.get("regime", "UNKNOWN"),
            long_score=float(closed_trade.get("tech_score", 0)),
            short_score=0.0,
            confidence=float(closed_trade.get("combined_conf", 50)),
            sentiment=closed_trade.get("sentiment", "NEUTRAL"),
            h1_structure=closed_trade.get("h1_structure", ""),
            h4_structure=closed_trade.get("h4_structure", ""),
            bear_override=closed_trade.get("bear_override", "NONE"),
            rsi_at_entry=float(closed_trade.get("rsi_at_entry", 50)),
            atr_at_entry=float(closed_trade.get("atr", 0)),
            vol_ratio=float(closed_trade.get("vol_ratio", 1)),
        )
        report = run_postmortem(t)
        return asdict(report)
    except Exception as e:
        log.error(f"Postmortem trigger error: {e}")
        return None
