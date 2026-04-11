"""
intelligence/intelligence_hub.py — Trinity.AI Intelligence Integration v1.1
Fixed: load_memory import path (was 'from core.signal_runner' — breaks on Streamlit Cloud)
"""

import sys
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger("intelligence_hub")

# ── Ensure core/ and intelligence/ are on the path regardless of CWD ──
for _candidate in [
    Path(__file__).parent.parent / "core",
    Path(__file__).parent,
    Path(__file__).parent.parent / "execution",
]:
    _s = str(_candidate)
    if _candidate.exists() and _s not in sys.path:
        sys.path.insert(0, _s)


def _load_memory_safe() -> list:
    """Load backtest memory without crashing if path is wrong."""
    try:
        # Try importing signal_runner from wherever it lives
        import importlib.util as _ilu
        from pathlib import Path as _P
        _sr_path = _P(__file__).parent.parent / "core" / "signal_runner.py"
        if _sr_path.exists():
            _spec = _ilu.spec_from_file_location("signal_runner", _sr_path)
            _sr   = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_sr)
            return _sr.load_memory()
    except Exception as e:
        log.debug(f"load_memory fallback: {e}")

    # Direct JSON fallback
    try:
        import json, os
        mem_file = os.environ.get("MEMORY_FILE", "btc_backtest_memory_v8.json")
        with open(mem_file) as f:
            return json.load(f)
    except:
        return []


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
    Runs Research + Polymarket in parallel, then Execution Gate synchronously.
    All failures are caught gracefully — signal still works if intel layer is down.
    """
    result = {
        "research":   None,
        "polymarket": None,
        "execution":  None,
        "errors":     [],
    }

    # ── Parallel I/O: research + polymarket ───────────────────────
    futures_map = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        if run_research:
            try:
                from intelligence.research_agents import run_research as _run_research
                futures_map["research"] = pool.submit(
                    _run_research,
                    market_regime=regime,
                    current_price=btc_price,
                    ema50=ema50,
                    ema200=ema200,
                    signal_dir=signal_direction,
                    lookback_hours=lookback_hours,
                )
            except Exception as e:
                result["errors"].append(f"research_import: {e}")

        if run_polymarket:
            try:
                from intelligence.polymarket import run_polymarket_scan
                futures_map["polymarket"] = pool.submit(
                    run_polymarket_scan,
                    btc_price=btc_price,
                    market_regime=regime,
                    signal_dir=signal_direction,
                )
            except Exception as e:
                result["errors"].append(f"polymarket_import: {e}")

        for name, future in futures_map.items():
            try:
                r = future.result(timeout=timeout_seconds)
                result[name] = asdict(r) if hasattr(r, "__dataclass_fields__") else r
            except FuturesTimeout:
                result["errors"].append(f"{name}_timeout after {timeout_seconds}s")
                log.warning(f"{name} timed out")
            except Exception as e:
                result["errors"].append(f"{name}_error: {e}")
                log.warning(f"{name} error: {traceback.format_exc()[:200]}")

    # ── Extract confidence + polymarket edge ─────────────────────
    research_conf = 50.0
    poly_edge     = 0.0
    pattern_hash  = ""

    if result["research"]:
        research_conf = float(result["research"].get("confidence", 50.0))

    if result["polymarket"]:
        hc = result["polymarket"].get("high_conviction", [])
        if hc:
            poly_edge = float(hc[0].get("edge_vs_signal", 0.0))

    try:
        import hashlib
        pattern_hash = hashlib.md5(
            f"{regime}_{signal_direction}_{h4_structure}_{h1_structure}".encode()
        ).hexdigest()[:8]
    except:
        pass

    # ── Execution gate ────────────────────────────────────────────
    try:
        from execution.execution_engine import evaluate_execution

        mem         = _load_memory_safe()
        hist_wr     = 0.55
        avg_win     = 0.025
        avg_loss    = 0.012

        if len(mem) >= 5:
            recent      = mem[-20:]
            hist_wr     = sum(r.get("win_rate", 0.55) for r in recent) / len(recent)
            wins_usd    = [r.get("avg_win",  0) for r in recent if r.get("avg_win")]
            losses_usd  = [r.get("avg_loss", 0) for r in recent if r.get("avg_loss")]
            if wins_usd and losses_usd and account_value > 0:
                avg_win  = abs(sum(wins_usd)  / len(wins_usd))  / account_value
                avg_loss = abs(sum(losses_usd)/ len(losses_usd))/ account_value

        exec_decision = evaluate_execution(
            signal_direction = signal_direction,
            tech_score       = tech_score,
            entry_price      = entry_price,
            stop_loss        = stop_loss,
            take_profit      = take_profit,
            regime           = regime,
            h1_structure     = h1_structure,
            h4_structure     = h4_structure,
            research_conf    = research_conf,
            poly_edge        = poly_edge,
            historical_wr    = hist_wr,
            avg_win_pct      = avg_win,
            avg_loss_pct     = avg_loss,
            account_value    = account_value,
            pattern_hash     = pattern_hash,
        )
        result["execution"] = asdict(exec_decision)

    except Exception as e:
        result["errors"].append(f"execution_gate: {e}")
        log.warning(f"Execution gate error: {traceback.format_exc()[:300]}")
        # Graceful fallback
        result["execution"] = {
            "approved":         signal_direction != "NONE" and tech_score >= 5.0,
            "direction":        signal_direction,
            "combined_conf":    research_conf,
            "rejection_reason": "" if signal_direction != "NONE" else "NO_SIGNAL",
            "notes":            result["errors"],
            "position_pct":     0.05,
            "position_usd":     account_value * 0.05,
            "rr_ratio":         2.0,
        }

    # ── Summary fields ────────────────────────────────────────────
    result["confidence"]         = research_conf
    result["poly_edge"]          = poly_edge
    result["execution_approved"] = (result["execution"] or {}).get("approved", False)
    result["sentiment"]          = (result["research"]  or {}).get("narrative_label",  "NEUTRAL")
    result["alignment"]          = (result["research"]  or {}).get("market_alignment", "NEUTRAL")

    return result


def trigger_postmortem_if_loss(closed_trade: dict) -> Optional[dict]:
    """Auto-trigger 5-agent postmortem when a trade closes with a loss."""
    if not closed_trade or closed_trade.get("pnl", 0) >= 0:
        return None
    try:
        from intelligence.postmortem import run_postmortem, TradeRecord
        t = TradeRecord(
            trade_id      = closed_trade.get("trade_id",      "unknown"),
            direction     = closed_trade.get("direction",     "LONG"),
            entry_price   = float(closed_trade.get("entry_price",  0)),
            exit_price    = float(closed_trade.get("exit_price",   0)),
            entry_time    = closed_trade.get("timestamp",     ""),
            exit_time     = closed_trade.get("exit_time",     ""),
            exit_reason   = closed_trade.get("exit_reason",   "UNKNOWN"),
            pnl_usd       = float(closed_trade.get("pnl",        0)),
            pnl_pct       = float(closed_trade.get("pnl_pct",    0)),
            regime        = closed_trade.get("regime",        "UNKNOWN"),
            long_score    = float(closed_trade.get("tech_score",  0)),
            short_score   = 0.0,
            confidence    = float(closed_trade.get("combined_conf", 50)),
            sentiment     = closed_trade.get("sentiment",    "NEUTRAL"),
            h1_structure  = closed_trade.get("h1_structure", ""),
            h4_structure  = closed_trade.get("h4_structure", ""),
            bear_override = closed_trade.get("bear_override","NONE"),
            rsi_at_entry  = float(closed_trade.get("rsi_at_entry", 50)),
            atr_at_entry  = float(closed_trade.get("atr",          0)),
            vol_ratio     = float(closed_trade.get("vol_ratio",    1)),
        )
        report = run_postmortem(t)
        return asdict(report)
    except Exception as e:
        log.error(f"Postmortem trigger error: {e}")
        return None
