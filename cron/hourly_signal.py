"""
cron/hourly_signal.py
Run by GitHub Actions every hour.
If LONG or SHORT signal fires → sends Telegram push notification.
Always logs to Google Sheets if webhook is configured.
"""

import os, sys, asyncio, logging
from pathlib import Path

_core = Path(__file__).parent.parent / "core"
sys.path.insert(0, str(_core))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cron")

import importlib.util
_spec = importlib.util.spec_from_file_location("signal_runner", _core / "signal_runner.py")
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)

try:
    from telegram import Bot
    from telegram.constants import ParseMode
except ImportError:
    log.error("pip install python-telegram-bot"); sys.exit(1)


def score_bar(val, max_val=10.0, width=8):
    filled = int(min(val/max_val, 1.0) * width)
    return "█"*filled + "░"*(width-filled)

def format_alert(sig):
    d    = sig["direction"]
    p    = sig["price"]
    ls   = sig["long_score"]
    ss   = sig["short_score"]
    cats = sig["categories"]
    ep   = sig.get("entry_plan")
    inds = sig.get("indicators", {})

    if   d == "LONG":  icon = "🟢"; rl = sig["reasons_long"]
    elif d == "SHORT": icon = "🔴"; rl = sig["reasons_short"]
    else:              icon = "⚪"; rl = []

    regime  = sig.get("regime","")
    rcfg    = sig.get("regime_config", {})
    h1_str  = sig.get("h1_structure","")
    h4_str  = sig.get("h4_structure","")
    bear_ov = sig.get("bear_override","NONE")
    vp      = sig.get("volume_profile",{})
    ts_     = sig["timestamp"][:16].replace("T"," ")

    lines = [
        f"{icon} *BTC/USD — {d} SIGNAL*",
        f"🕐 `{ts_} UTC`",
        f"💲 Price: `${p:,.2f}`",
        f"",
        f"{rcfg.get('emoji','')} *Regime: {regime}*",
        f"  4H: `{h4_str}` \\| 1H: `{h1_str}`",
    ]
    if bear_ov != "NONE":
        lines.append(f"  ⚡ Override: `{bear_ov}`")
    if vp.get("poc"):
        lines.append(f"  POC: `${vp['poc']:,.0f}` VAH: `${vp['vah']:,.0f}` VAL: `${vp['val']:,.0f}`")

    lines += [
        f"",
        f"Long:  `{ls:5.2f}` `{score_bar(ls)}`",
        f"Short: `{ss:5.2f}` `{score_bar(ss)}`",
        f"",
        f"Trend  L:`{cats['trend']['long']:4.1f}` S:`{cats['trend']['short']:4.1f}`",
        f"Zone   L:`{cats['zone']['long']:4.1f}` S:`{cats['zone']['short']:4.1f}`",
        f"Mom    L:`{cats['momentum']['long']:4.1f}` S:`{cats['momentum']['short']:4.1f}`",
        f"Pos    L:`{cats['positioning']['long']:4.1f}` S:`{cats['positioning']['short']:4.1f}`",
    ]

    if rl:
        reasons_str = ", ".join(rl[:6])
        lines += [f"", f"✅ `{reasons_str}`"]

    if ep:
        lines += [
            f"",
            f"Entry: `${ep['entry']:,.2f}`",
            f"SL:    `${ep['stop_loss']:,.2f}`",
            f"TP:    `${ep['take_profit']:,.2f}`",
            f"R:R    `1:{ep['rr']:.1f}`",
        ]

    lines.append(
        f"\nRSI: `{inds.get('rsi',0):.1f}` "
        f"MFI: `{inds.get('mfi',0):.1f}` "
        f"Stoch: `{inds.get('stoch_k',0):.1f}` "
        f"ATR: `${inds.get('atr',0):,.0f}`"
    )
    return "\n".join(lines)


async def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN","")
    chat_ids  = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_IDS","").split(",") if c.strip()]

    log.info("Fetching signal (v8.5)...")
    try:
        sig = sr.get_signal()
    except Exception as e:
        log.error(f"get_signal failed: {e}"); return

    direction = sig.get("direction","NONE")
    log.info(f"Signal: {direction} | Long={sig.get('long_score',0):.2f} Short={sig.get('short_score',0):.2f}")
    log.info(f"Regime: {sig.get('regime','')} | 4H: {sig.get('h4_structure','')} | 1H: {sig.get('h1_structure','')}")
    log.info(f"Bear override: {sig.get('bear_override','NONE')}")

    # Log to Google Sheets always
    try:
        sig_clean = {k:v for k,v in sig.items() if k != "df"}
        sr.log_signal_to_sheets(sig_clean)
    except Exception as e:
        log.warning(f"Sheets log error: {e}")

    # Send Telegram only if signal fires
    if direction not in ("LONG","SHORT"):
        log.info("No signal — skipping Telegram notification")
        return

    if not bot_token or not chat_ids:
        log.warning("No Telegram credentials — signal fired but no notification sent")
        return

    try:
        text = format_alert(sig)
        bot  = Bot(token=bot_token)
        for chat_id in chat_ids:
            await bot.send_message(
                chat_id=chat_id, text=text,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            log.info(f"Alert sent to {chat_id}: {direction} @ ${sig['price']:,.2f}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
