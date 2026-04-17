"""
paper_trading/paper_bot.py — Trinity.AI Paper Trading Bot v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fully autonomous 24/7 paper trading bot using Trinity.AI signals.
No exchange API key needed — uses Binance public price feed.

Architecture:
  PaperBroker    — simulates order fills using real Binance prices
  PaperPortfolio — tracks virtual positions, P&L, drawdown
  RiskEngine     — confidence-scaled Kelly position sizing
  BotLoop        — 24/7 main loop, checks signals every N minutes

Risk Management (Confidence-Scaled Kelly):
  position_size = min(¼_kelly × score_mult × conf_scale, regime_cap)
  regime_caps: STRONG_BULL=20%, WEAK_BULL=15%, BEAR=12%, RANGING=10%

Circuit Breakers:
  • Daily loss > 5%        → halt trading for rest of day
  • 3 consecutive losses   → 4-hour cooldown
  • Max 2 concurrent trades
  • Max single-trade loss  = 3% of account

Storage: JSON file (paper_trading/trades.json) — no DB needed
Telegram: sends alerts when trades open/close (optional)
Sheets:   logs every trade + P&L to Google Sheets (optional)

Author: Trinity.AI
"""

import os
import sys
import json
import time
import logging
import asyncio
import hashlib
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict, field
from typing import Optional

# ── Path setup ────────────────────────────────────────────────────
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root / "core"))
sys.path.insert(0, str(_root / "intelligence"))
sys.path.insert(0, str(_root / "execution"))

import signal_runner as sr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("paper_trading/bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("paper_bot")

# ── Config ────────────────────────────────────────────────────────
INIT_CAPITAL      = float(os.environ.get("PAPER_CAPITAL",          "10000"))
CHECK_INTERVAL    = int(os.environ.get("PAPER_CHECK_INTERVAL",      "60"))   # seconds
MAX_DAILY_LOSS    = float(os.environ.get("PAPER_MAX_DAILY_LOSS",    "0.05")) # 5%
MAX_TRADE_LOSS    = float(os.environ.get("PAPER_MAX_TRADE_LOSS",    "0.03")) # 3% per trade
MAX_CONCURRENT    = int(os.environ.get("PAPER_MAX_CONCURRENT",      "2"))
COOLDOWN_LOSSES   = int(os.environ.get("PAPER_COOLDOWN_LOSSES",     "3"))    # streak
COOLDOWN_HOURS    = int(os.environ.get("PAPER_COOLDOWN_HOURS",      "4"))
KELLY_FRACTION    = float(os.environ.get("PAPER_KELLY_FRACTION",    "0.25"))
TRADES_FILE       = Path(os.environ.get("PAPER_TRADES_FILE",        "paper_trading/trades.json"))
STATE_FILE        = Path(os.environ.get("PAPER_STATE_FILE",         "paper_trading/state.json"))
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN",            "")
TELEGRAM_CHAT_IDS = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]
SHEETS_URL        = os.environ.get("GOOGLE_SHEETS_WEBHOOK",         "")

TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)

# ── Regime position caps ──────────────────────────────────────────
REGIME_CAPS = {
    "STRONG_BULL": 0.20,
    "WEAK_BULL":   0.15,
    "BEAR":        0.12,
    "RANGING":     0.10,
}


# ══════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════

@dataclass
class PaperTrade:
    trade_id:      str
    direction:     str          # "LONG" | "SHORT"
    entry_price:   float
    stop_loss:     float
    take_profit:   float
    size_usd:      float        # dollar position size
    size_pct:      float        # % of account
    open_time:     str
    regime:        str
    tech_score:    float
    conf:          float        # combined confidence
    sentiment:     str
    # Closed fields
    exit_price:    float = 0.0
    exit_time:     str   = ""
    exit_reason:   str   = ""   # "TP_HIT" | "SL_HIT" | "SIGNAL_FLIP" | "EOD_CLOSE" | "MANUAL"
    pnl_usd:       float = 0.0
    pnl_pct:       float = 0.0
    status:        str   = "OPEN"  # "OPEN" | "CLOSED"
    # Debug info
    reasons:       list  = field(default_factory=list)

@dataclass
class BotState:
    account_value:      float = INIT_CAPITAL
    peak_value:         float = INIT_CAPITAL
    daily_start_value:  float = INIT_CAPITAL
    daily_reset_date:   str   = ""
    consecutive_losses: int   = 0
    cooldown_until:     str   = ""
    total_trades:       int   = 0
    winning_trades:     int   = 0
    last_signal_time:   str   = ""
    last_signal_dir:    str   = "NONE"
    running:            bool  = True


# ══════════════════════════════════════════════════════════════════
# PAPER BROKER — simulates fills with real Binance prices
# ══════════════════════════════════════════════════════════════════

class PaperBroker:
    """Simulates order execution using real Binance spot prices."""

    SLIPPAGE = 0.0003   # 0.03% slippage on fills
    FEE      = 0.001    # 0.1% taker fee per side

    def get_price(self) -> float:
        """Fetch live BTC price from Binance public API."""
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"}, timeout=8,
            )
            return float(r.json()["price"])
        except:
            pass
        try:
            r = requests.get(
                "https://min-api.cryptocompare.com/data/price",
                params={"fsym": "BTC", "tsyms": "USD"}, timeout=8,
            )
            return float(r.json()["USD"])
        except:
            return 0.0

    def fill_entry(self, direction: str, size_usd: float, price: float) -> tuple[float, float]:
        """
        Simulate entry fill with slippage + fees.
        Returns (fill_price, actual_cost_usd)
        """
        if direction == "LONG":
            fill = price * (1 + self.SLIPPAGE)
        else:
            fill = price * (1 - self.SLIPPAGE)
        cost = size_usd * (1 + self.FEE)
        return round(fill, 2), round(cost, 2)

    def fill_exit(self, direction: str, price: float) -> float:
        """Simulate exit fill with slippage + fees. Returns net exit price."""
        if direction == "LONG":
            fill = price * (1 - self.SLIPPAGE)
        else:
            fill = price * (1 + self.SLIPPAGE)
        return round(fill * (1 - self.FEE), 2)

    def check_sl_tp(self, trade: PaperTrade, current_price: float) -> Optional[str]:
        """Check if SL or TP has been hit. Returns exit reason or None."""
        if trade.direction == "LONG":
            if current_price <= trade.stop_loss:
                return "SL_HIT"
            if current_price >= trade.take_profit:
                return "TP_HIT"
        elif trade.direction == "SHORT":
            if current_price >= trade.stop_loss:
                return "SL_HIT"
            if current_price <= trade.take_profit:
                return "TP_HIT"
        return None

    def compute_unrealized_pnl(self, trade: PaperTrade, current_price: float) -> float:
        """Calculate current unrealized P&L in USD."""
        if trade.direction == "LONG":
            price_move = (current_price - trade.entry_price) / trade.entry_price
        else:
            price_move = (trade.entry_price - current_price) / trade.entry_price
        return round(trade.size_usd * price_move, 2)


# ══════════════════════════════════════════════════════════════════
# RISK ENGINE — confidence-scaled Kelly
# ══════════════════════════════════════════════════════════════════

class RiskEngine:

    def compute_kelly_size(
        self,
        account_value: float,
        regime:        str,
        tech_score:    float,
        max_score:     float,
        confidence:    float,  # 0-100
        win_rate:      float,  # historical
        avg_win_pct:   float,
        avg_loss_pct:  float,
    ) -> float:
        """
        Returns position size as fraction of account (0.02 to regime_cap).
        Confidence-scaled quarter-Kelly.
        """
        # Full Kelly fraction
        if avg_loss_pct > 0 and win_rate > 0:
            B            = avg_win_pct / avg_loss_pct
            L            = 1.0 - win_rate
            kelly_full   = max(0.0, (win_rate * B - L) / B)
        else:
            kelly_full = 0.08  # conservative default

        quarter_kelly = kelly_full * KELLY_FRACTION

        # Scale by signal score (0.8x to 1.4x)
        score_mult = 0.8 + (min(tech_score, max_score) / max_score) * 0.6

        # Scale by confidence (confidence 40% = 0, 100% = 1)
        conf_scale = max(0.0, min(1.0, (confidence - 40.0) / 60.0))

        sized = quarter_kelly * score_mult * conf_scale

        # Apply regime cap
        cap   = REGIME_CAPS.get(regime, 0.10)
        final = max(0.02, min(cap, sized))

        return round(final, 4)

    def max_sl_distance(self, account_value: float, size_usd: float) -> float:
        """
        Maximum stop-loss distance as % of entry price,
        such that loss never exceeds MAX_TRADE_LOSS * account_value.
        """
        max_loss_usd = account_value * MAX_TRADE_LOSS
        return max_loss_usd / size_usd if size_usd > 0 else 0.05

    def check_circuit_breakers(self, state: BotState) -> tuple[bool, str]:
        """Returns (can_trade, reason_if_not)."""
        now = datetime.now(timezone.utc)

        # Daily loss limit
        daily_loss_pct = (state.daily_start_value - state.account_value) / state.daily_start_value
        if daily_loss_pct >= MAX_DAILY_LOSS:
            return False, f"DAILY_LOSS_LIMIT: -{daily_loss_pct:.1%} (limit -{MAX_DAILY_LOSS:.0%})"

        # Cooldown after consecutive losses
        if state.cooldown_until:
            try:
                cooldown_end = datetime.fromisoformat(state.cooldown_until)
                if cooldown_end.tzinfo is None:
                    cooldown_end = cooldown_end.replace(tzinfo=timezone.utc)
                if now < cooldown_end:
                    remaining = int((cooldown_end - now).total_seconds() / 60)
                    return False, f"COOLDOWN: {remaining}min remaining after {state.consecutive_losses} losses"
            except:
                pass

        return True, ""


# ══════════════════════════════════════════════════════════════════
# PORTFOLIO — tracks positions and account value
# ══════════════════════════════════════════════════════════════════

class PaperPortfolio:

    def __init__(self):
        self.broker  = PaperBroker()
        self.risk    = RiskEngine()
        self.state   = self._load_state()
        self.trades  = self._load_trades()

    # ── Persistence ───────────────────────────────────────────────

    def _load_state(self) -> BotState:
        if STATE_FILE.exists():
            try:
                d = json.loads(STATE_FILE.read_text())
                return BotState(**d)
            except: pass
        return BotState()

    def _save_state(self):
        STATE_FILE.write_text(json.dumps(asdict(self.state), indent=2, default=str))

    def _load_trades(self) -> list[PaperTrade]:
        if TRADES_FILE.exists():
            try:
                raw = json.loads(TRADES_FILE.read_text())
                return [PaperTrade(**t) for t in raw]
            except: pass
        return []

    def _save_trades(self):
        TRADES_FILE.write_text(json.dumps([asdict(t) for t in self.trades], indent=2, default=str))

    # ── Daily reset ───────────────────────────────────────────────

    def _maybe_reset_daily(self):
        today = datetime.now(timezone.utc).date().isoformat()
        if self.state.daily_reset_date != today:
            self.state.daily_reset_date = today
            self.state.daily_start_value = self.state.account_value
            log.info(f"Daily reset | account={self.state.account_value:,.2f}")
            self._save_state()

    # ── Open positions ────────────────────────────────────────────

    @property
    def open_trades(self) -> list[PaperTrade]:
        return [t for t in self.trades if t.status == "OPEN"]

    # ── Open a new trade ─────────────────────────────────────────

    def open_trade(self, signal: dict) -> Optional[PaperTrade]:
        """Evaluate signal through risk engine and open paper trade if approved."""
        self._maybe_reset_daily()

        direction  = signal.get("direction", "NONE")
        if direction == "NONE":
            return None

        # Circuit breakers
        can_trade, reason = self.risk.check_circuit_breakers(self.state)
        if not can_trade:
            log.warning(f"Trade blocked: {reason}")
            return None

        # Max concurrent positions
        if len(self.open_trades) >= MAX_CONCURRENT:
            log.info(f"Max concurrent trades reached ({MAX_CONCURRENT})")
            return None

        # Don't open same direction if already open
        existing_dirs = {t.direction for t in self.open_trades}
        if direction in existing_dirs:
            log.info(f"Already have open {direction} trade")
            return None

        # Extract signal data
        tech_score = signal.get("long_score" if direction == "LONG" else "short_score", 0.0)
        regime     = signal.get("regime", "RANGING")
        ep         = signal.get("entry_plan", {}) or {}
        price      = signal.get("price", 0.0)
        confidence = float((signal.get("execution_decision") or {}).get("combined_conf", 50.0))
        sentiment  = signal.get("sentiment", "NEUTRAL")
        reasons    = signal.get("reasons_long" if direction == "LONG" else "reasons_short", [])

        if not ep or price <= 0:
            return None

        # Load historical metrics for Kelly
        mem      = sr.load_memory()
        win_rate = 0.55; avg_win = 0.025; avg_loss = 0.012
        if len(mem) >= 5:
            recent    = mem[-20:]
            win_rate  = sum(r.get("win_rate", 0.55) for r in recent) / len(recent)
            wins_usd  = [r.get("avg_win",  0) for r in recent if r.get("avg_win")]
            loss_usd  = [r.get("avg_loss", 0) for r in recent if r.get("avg_loss")]
            if wins_usd and self.state.account_value > 0:
                avg_win  = abs(sum(wins_usd) / len(wins_usd)) / self.state.account_value
            if loss_usd and self.state.account_value > 0:
                avg_loss = abs(sum(loss_usd) / len(loss_usd)) / self.state.account_value

        max_scores = {"STRONG_BULL": 15, "WEAK_BULL": 15, "BEAR": 18, "RANGING": 16}
        size_pct   = self.risk.compute_kelly_size(
            self.state.account_value, regime,
            tech_score, max_scores.get(regime, 15),
            confidence, win_rate, avg_win, avg_loss,
        )
        size_usd   = round(self.state.account_value * size_pct, 2)

        # Validate SL/TP from entry plan
        sl = ep.get("stop_loss", 0)
        tp = ep.get("take_profit", 0)
        if sl <= 0 or tp <= 0:
            return None

        # Check SL doesn't exceed max allowed loss
        sl_dist_pct = abs(price - sl) / price
        max_sl      = self.risk.max_sl_distance(self.state.account_value, size_usd)
        if sl_dist_pct > max_sl:
            # Tighten the SL to stay within risk limits
            if direction == "LONG":
                sl = round(price * (1 - max_sl), 2)
            else:
                sl = round(price * (1 + max_sl), 2)
            log.info(f"SL tightened to {sl:,.2f} (max loss {MAX_TRADE_LOSS:.0%})")

        # Fill entry
        fill_price, actual_cost = self.broker.fill_entry(direction, size_usd, price)

        trade_id = hashlib.md5(
            f"{direction}{fill_price}{datetime.now(timezone.utc).isoformat()}".encode()
        ).hexdigest()[:8]

        trade = PaperTrade(
            trade_id    = trade_id,
            direction   = direction,
            entry_price = fill_price,
            stop_loss   = sl,
            take_profit = tp,
            size_usd    = actual_cost,
            size_pct    = size_pct,
            open_time   = datetime.now(timezone.utc).isoformat(),
            regime      = regime,
            tech_score  = round(tech_score, 2),
            conf        = round(confidence, 1),
            sentiment   = sentiment,
            reasons     = reasons[:6],
        )

        self.trades.append(trade)
        self._save_trades()
        self.state.last_signal_dir  = direction
        self.state.last_signal_time = trade.open_time
        self._save_state()

        log.info(
            f"📈 OPENED {direction} @ ${fill_price:,.2f} | "
            f"size=${actual_cost:,.0f} ({size_pct:.1%}) | "
            f"SL=${sl:,.2f} TP=${tp:,.2f} | "
            f"score={tech_score:.1f} conf={confidence:.0f}%"
        )
        _notify_telegram(
            f"📈 *Paper {direction} OPENED*\n"
            f"Entry: `${fill_price:,.2f}` | Size: `${actual_cost:,.0f}` ({size_pct:.1%})\n"
            f"SL: `${sl:,.2f}` | TP: `${tp:,.2f}` | Regime: `{regime}`\n"
            f"Score: `{tech_score:.1f}` | Confidence: `{confidence:.0f}%`"
        )
        _log_sheets({"type": "OPEN", **asdict(trade)})

        return trade

    # ── Monitor and close existing trades ────────────────────────

    def monitor_trades(self, current_signal: dict):
        """Check all open trades against current price and signal direction."""
        if not self.open_trades:
            return

        current_price = self.broker.get_price()
        if current_price <= 0:
            return

        current_direction = current_signal.get("direction", "NONE")

        for trade in list(self.open_trades):
            exit_reason = self.broker.check_sl_tp(trade, current_price)

            # Signal flip — new signal in opposite direction → close this trade
            if exit_reason is None:
                if trade.direction == "LONG"  and current_direction == "SHORT":
                    exit_reason = "SIGNAL_FLIP"
                elif trade.direction == "SHORT" and current_direction == "LONG":
                    exit_reason = "SIGNAL_FLIP"

            if exit_reason:
                self._close_trade(trade, current_price, exit_reason)

    def _close_trade(self, trade: PaperTrade, price: float, reason: str):
        """Close a trade, update portfolio, trigger postmortem if loss."""
        fill = self.broker.fill_exit(trade.direction, price)

        if trade.direction == "LONG":
            pnl_pct = (fill - trade.entry_price) / trade.entry_price
        else:
            pnl_pct = (trade.entry_price - fill) / trade.entry_price

        pnl_usd = round(trade.size_usd * pnl_pct, 2)

        trade.exit_price  = fill
        trade.exit_time   = datetime.now(timezone.utc).isoformat()
        trade.exit_reason = reason
        trade.pnl_usd     = pnl_usd
        trade.pnl_pct     = round(pnl_pct, 4)
        trade.status      = "CLOSED"

        # Update account
        self.state.account_value = round(self.state.account_value + pnl_usd, 2)
        self.state.peak_value    = max(self.state.peak_value, self.state.account_value)
        self.state.total_trades += 1

        # Track consecutive losses / wins
        if pnl_usd > 0:
            self.state.winning_trades     += 1
            self.state.consecutive_losses  = 0
        else:
            self.state.consecutive_losses += 1
            if self.state.consecutive_losses >= COOLDOWN_LOSSES:
                cooldown = datetime.now(timezone.utc) + timedelta(hours=COOLDOWN_HOURS)
                self.state.cooldown_until = cooldown.isoformat()
                log.warning(
                    f"⚠️  {COOLDOWN_LOSSES} consecutive losses — "
                    f"cooldown until {self.state.cooldown_until}"
                )

        self._save_trades()
        self._save_state()

        win_rate = self.state.winning_trades / max(self.state.total_trades, 1)
        pnl_icon = "✅" if pnl_usd >= 0 else "❌"

        log.info(
            f"{pnl_icon} CLOSED {trade.direction} @ ${fill:,.2f} | "
            f"PnL=${pnl_usd:+,.2f} ({pnl_pct:+.1%}) | "
            f"reason={reason} | "
            f"account=${self.state.account_value:,.2f} | "
            f"WR={win_rate:.0%}"
        )
        _notify_telegram(
            f"{pnl_icon} *Paper {trade.direction} CLOSED*\n"
            f"Exit: `${fill:,.2f}` | PnL: `${pnl_usd:+,.2f}` ({pnl_pct:+.1%})\n"
            f"Reason: `{reason}` | Account: `${self.state.account_value:,.2f}`\n"
            f"Win rate: `{win_rate:.0%}` ({self.state.winning_trades}/{self.state.total_trades})"
        )
        _log_sheets({"type": "CLOSE", **asdict(trade)})

        # Trigger postmortem for losses
        if pnl_usd < 0:
            try:
                from intelligence.intelligence_hub import trigger_postmortem_if_loss
                pm_trade = {
                    "trade_id":     trade.trade_id,
                    "direction":    trade.direction,
                    "entry_price":  trade.entry_price,
                    "exit_price":   trade.exit_price,
                    "exit_reason":  reason,
                    "pnl":          pnl_usd,
                    "pnl_pct":      pnl_pct,
                    "regime":       trade.regime,
                    "tech_score":   trade.tech_score,
                    "combined_conf":trade.conf,
                    "sentiment":    trade.sentiment,
                    "timestamp":    trade.open_time,
                    "exit_time":    trade.exit_time,
                    "h1_structure": "ranging",
                    "h4_structure": "ranging",
                    "bear_override":"NONE",
                }
                pm = trigger_postmortem_if_loss(pm_trade)
                if pm:
                    log.info(f"⚗️  Postmortem: severity={pm.get('severity_score',0):.1f}/10 primary={pm.get('primary_failure','?')}")
            except Exception as e:
                log.debug(f"Postmortem unavailable: {e}")

    # ── Stats snapshot ────────────────────────────────────────────

    def get_stats(self) -> dict:
        closed   = [t for t in self.trades if t.status == "CLOSED"]
        open_t   = self.open_trades
        total_pnl = sum(t.pnl_usd for t in closed)
        win_rate  = self.state.winning_trades / max(self.state.total_trades, 1)
        drawdown  = (self.state.peak_value - self.state.account_value) / max(self.state.peak_value, 1)
        daily_ret = (self.state.account_value - self.state.daily_start_value) / max(self.state.daily_start_value, 1)

        return {
            "account_value":      round(self.state.account_value, 2),
            "total_return_pct":   round((self.state.account_value - INIT_CAPITAL) / INIT_CAPITAL, 4),
            "total_pnl":          round(total_pnl, 2),
            "daily_return_pct":   round(daily_ret, 4),
            "max_drawdown":       round(drawdown, 4),
            "total_trades":       self.state.total_trades,
            "win_rate":           round(win_rate, 4),
            "winning_trades":     self.state.winning_trades,
            "open_trades":        len(open_t),
            "consecutive_losses": self.state.consecutive_losses,
            "cooldown_until":     self.state.cooldown_until,
            "open_positions":     [
                {
                    "id":        t.trade_id,
                    "direction": t.direction,
                    "entry":     t.entry_price,
                    "sl":        t.stop_loss,
                    "tp":        t.take_profit,
                    "size_usd":  t.size_usd,
                    "open_time": t.open_time,
                    "regime":    t.regime,
                }
                for t in open_t
            ],
        }


# ══════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════

def _notify_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        return
    import re
    def escape(s):
        return re.sub(r'([_\[\]()~`>#+=|{}.!\\-])', r'\\\1', s)
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
        except Exception as e:
            log.debug(f"Telegram error: {e}")

def _log_sheets(data: dict):
    if not SHEETS_URL:
        return
    try:
        requests.post(SHEETS_URL, json={
            k: v for k, v in data.items()
            if not isinstance(v, list)
        }, timeout=10)
    except: pass


# ══════════════════════════════════════════════════════════════════
# MAIN BOT LOOP — 24/7
# ══════════════════════════════════════════════════════════════════

def run_bot():
    """
    Main 24/7 loop. Runs continuously, checking signals every CHECK_INTERVAL seconds.
    Handles graceful shutdown on KeyboardInterrupt.
    """
    log.info("=" * 60)
    log.info("🤖 Trinity.AI Paper Trading Bot v1.0 starting")
    log.info(f"   Capital:       ${INIT_CAPITAL:,.0f}")
    log.info(f"   Check interval: {CHECK_INTERVAL}s")
    log.info(f"   Max daily loss: {MAX_DAILY_LOSS:.0%}")
    log.info(f"   Cooldown:       {COOLDOWN_LOSSES} losses → {COOLDOWN_HOURS}h pause")
    log.info(f"   Max concurrent: {MAX_CONCURRENT}")
    log.info("=" * 60)

    portfolio = PaperPortfolio()
    log.info(
        f"Portfolio loaded | account=${portfolio.state.account_value:,.2f} | "
        f"open={len(portfolio.open_trades)} | total={portfolio.state.total_trades}"
    )

    _notify_telegram(
        f"🤖 *Paper Trading Bot Started*\n"
        f"Capital: `${portfolio.state.account_value:,.2f}`\n"
        f"Open trades: `{len(portfolio.open_trades)}`\n"
        f"Check interval: `{CHECK_INTERVAL}s`"
    )

    iteration = 0
    last_signal_fetch = 0

    while portfolio.state.running:
        try:
            now = time.time()
            iteration += 1

            # ── 1. Fetch signal (rate-limited) ────────────────────
            if now - last_signal_fetch >= CHECK_INTERVAL:
                log.info(f"[{iteration}] Fetching signal...")
                try:
                    signal = sr.get_signal()
                    last_signal_fetch = now

                    direction  = signal.get("direction", "NONE")
                    price      = signal.get("price", 0)
                    regime     = signal.get("regime", "?")
                    ls         = signal.get("long_score", 0)
                    ss         = signal.get("short_score", 0)

                    log.info(
                        f"Signal: {direction} @ ${price:,.2f} | "
                        f"regime={regime} | L={ls:.2f} S={ss:.2f}"
                    )

                    # ── 2. Monitor existing trades ─────────────────
                    portfolio.monitor_trades(signal)

                    # ── 3. Open new trade if signal fired ─────────
                    if direction in ("LONG", "SHORT"):
                        portfolio.open_trade(signal)

                    # ── 4. Log stats every 10 iterations ──────────
                    if iteration % 10 == 0:
                        stats = portfolio.get_stats()
                        log.info(
                            f"📊 Stats | account=${stats['account_value']:,.2f} | "
                            f"return={stats['total_return_pct']:+.1%} | "
                            f"WR={stats['win_rate']:.0%} | "
                            f"trades={stats['total_trades']} | "
                            f"DD={stats['max_drawdown']:.1%}"
                        )

                except Exception as e:
                    log.error(f"Signal fetch error: {e}", exc_info=True)
                    time.sleep(30)
                    continue

            # ── 5. Check open trades against live price ───────────
            # (runs every loop iteration, between signal fetches)
            elif portfolio.open_trades:
                try:
                    current_price = portfolio.broker.get_price()
                    if current_price > 0:
                        # Create minimal signal-like dict for direction check
                        mini_sig = {
                            "direction": portfolio.state.last_signal_dir,
                            "price": current_price,
                        }
                        portfolio.monitor_trades(mini_sig)
                except Exception as e:
                    log.debug(f"Price check error: {e}")

            # ── Sleep until next check ────────────────────────────
            time.sleep(min(10, CHECK_INTERVAL))

        except KeyboardInterrupt:
            log.info("Shutdown requested (KeyboardInterrupt)")
            portfolio.state.running = False
            portfolio._save_state()
            break

        except Exception as e:
            log.error(f"Unexpected error in main loop: {e}", exc_info=True)
            time.sleep(60)

    log.info("🛑 Paper trading bot stopped")
    stats = portfolio.get_stats()
    _notify_telegram(
        f"🛑 *Paper Bot Stopped*\n"
        f"Final account: `${stats['account_value']:,.2f}`\n"
        f"Total return: `{stats['total_return_pct']:+.1%}`\n"
        f"Win rate: `{stats['win_rate']:.0%}` ({stats['winning_trades']}/{stats['total_trades']})"
    )


if __name__ == "__main__":
    run_bot()
