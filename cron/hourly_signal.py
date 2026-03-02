"""
cron/hourly_signal.py
Run by GitHub Actions every hour. Checks for LONG/SHORT signal.
If signal fires → sends Telegram push notification.
Always logs to Google Sheets if webhook is set.
"""

import os, sys, asyncio, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cron")

import signal_runner as sr

try:
    from telegram import Bot
    from telegram.constants import ParseMode
except ImportError:
    log.error("pip install python-telegram-bot")
    sys.exit(1)


def score_bar(val, max_val=10.0, width=8):
    filled = int(min(val/max_val, 1.0) * width)
    return "█"*filled + "░"*(width-filled)

def format_telegram_alert(sig):
    d   = sig["direction"]
    p   = sig["price"]
    ls  = sig["long_score"]
    ss  = sig["short_score"]
    cats= sig["categories"]
    ep  = sig.get("entry_plan")
    inds= sig.get("indicators", {})

    if   d == "LONG":  icon = "🟢"; rl = sig["reasons_long"]
    elif d == "SHORT": icon = "🔴"; rl = sig["reasons_short"]
    else:              icon = "⚪"; rl = []

    lines = [
        f"{icon} *BTC/USD — {d} SIGNAL FIRED*",
        f"💲 Price: `${p:,.2f}`",
        f"",
        f"Long:  `{ls:5.2f}` `{score_bar(ls)}`",
        f"Short: `{ss:5.2f}` `{score_bar(ss)}`",
        f"",
        f"Trend      L:`{cats['trend']['long']:4.1f}` S:`{cats['trend']['short']:4.1f}`",
        f"Zone       L:`{cats['zone']['long']:4.1f}` S:`{cats['zone']['short']:4.1f}`",
        f"Momentum   L:`{cats['momentum']['long']:4.1f}` S:`{cats['momentum']['short']:4.1f}`",
        f"Positioning L:`{cats['positioning']['long']:4.1f}` S:`{cats['positioning']['short']:4.1f}`",
    ]

    if rl:
        lines += [f"", f"✅ Reasons: {', '.join(rl[:5])}"]

    if ep:
        lines += [
            f"",
            f"Entry: `${ep['entry']:,.2f}`",
            f"SL:    `${ep['stop_loss']:,.2f}`",
            f"TP:    `${ep['take_profit']:,.2f}`",
            f"R:R    1:{ep['rr']:.1f}",
        ]

    lines.append(f"\nRSI: `{inds.get('rsi',0):.1f}` | ATR: `${inds.get('atr',0):,.0f}`")
    return "\n".join(lines)


async def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_ids  = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_IDS","").split(",") if c.strip()]

    if not bot_token:
        log.warning("TELEGRAM_BOT_TOKEN not set — will check signal but won't send alerts")

    log.info("Fetching live signal...")
    sig = sr.get_signal()

    if "error" in sig:
        log.error(f"Signal error: {sig['error']}")
        return

    direction = sig["direction"]
    log.info(f"Signal: {direction} | Long={sig['long_score']:.2f} | Short={sig['short_score']:.2f} | Price=${sig['price']:,.2f}")

    # Always log to sheets
    sig_clean = {k:v for k,v in sig.items() if k != "df"}
    sr.log_signal_to_sheets(sig_clean)

    # Send Telegram alert only when signal fires
    if direction in ("LONG", "SHORT") and bot_token and chat_ids:
        bot  = Bot(token=bot_token)
        text = format_telegram_alert(sig)
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN_V2)
                log.info(f"Alert sent to {chat_id}")
            except Exception as e:
                log.error(f"Failed to send to {chat_id}: {e}")
    elif direction == "NONE":
        log.info("No signal — no alert sent")
    else:
        log.info("Signal fired but no Telegram config — skipping alert")


if __name__ == "__main__":
    asyncio.run(main())
