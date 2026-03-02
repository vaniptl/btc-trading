"""
telegram_bot.py — BTC Strategy Telegram Bot
Commands:
  /signal    — Live signal with full indicator breakdown
  /backtest  — Run backtest (default 1Y), reply with period e.g. /backtest 3Y
  /learn     — Show learning report from all past runs
  /status    — System health check
  /help      — Command list

Deploy free on Railway or run locally.
Set env vars: TELEGRAM_BOT_TOKEN, CRYPTOCOMPARE_API_KEY
"""

import os, sys, json, logging, traceback
from pathlib import Path

# Add core to path
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("btc_bot")

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                               ContextTypes, MessageHandler, filters)
    from telegram.constants import ParseMode
except ImportError:
    print("Run: pip install python-telegram-bot")
    sys.exit(1)

import signal_runner as sr

BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_IDS = os.environ.get("ALLOWED_CHAT_IDS", "").split(",")  # leave blank = allow all


# ══════════════════════════════════════════════════════════════════
# AUTH GUARD
# ══════════════════════════════════════════════════════════════════

def is_allowed(update: Update) -> bool:
    if not ALLOWED_IDS or ALLOWED_IDS == [""]: return True
    return str(update.effective_chat.id) in ALLOWED_IDS


# ══════════════════════════════════════════════════════════════════
# FORMATTERS
# ══════════════════════════════════════════════════════════════════

def score_bar(val, max_val=10.0, width=8):
    filled = int(min(val/max_val, 1.0) * width)
    return "█"*filled + "░"*(width-filled)

def format_signal_message(sig: dict) -> str:
    if "error" in sig:
        return f"❌ Error: {sig['error']}"

    d    = sig["direction"]
    p    = sig["price"]
    ls   = sig["long_score"]
    ss   = sig["short_score"]
    cats = sig["categories"]

    if   d == "LONG":  icon = "🟢"; rl = sig["reasons_long"]
    elif d == "SHORT": icon = "🔴"; rl = sig["reasons_short"]
    else:              icon = "⚪"; rl = []

    ep   = sig.get("entry_plan")
    inds = sig.get("indicators", {})
    ts   = sig["timestamp"][:16].replace("T"," ")

    lines = [
        f"{icon} *BTC/USD SIGNAL — {d}*",
        f"🕐 `{ts} UTC`",
        f"💲 Price: `${p:,.2f}`",
        f"",
        f"📊 *Scores*",
        f"  Long:  `{ls:5.2f}` `{score_bar(ls)}`",
        f"  Short: `{ss:5.2f}` `{score_bar(ss)}`",
        f"",
        f"📂 *Categories*",
        f"  📈 Trend      L:`{cats['trend']['long']:4.1f}`  S:`{cats['trend']['short']:4.1f}`",
        f"  📍 Zone       L:`{cats['zone']['long']:4.1f}`  S:`{cats['zone']['short']:4.1f}`",
        f"  ⚡ Momentum   L:`{cats['momentum']['long']:4.1f}`  S:`{cats['momentum']['short']:4.1f}`",
        f"  🎯 Positioning L:`{cats['positioning']['long']:4.1f}`  S:`{cats['positioning']['short']:4.1f}`",
        f"",
        f"🔬 *Indicators*",
        f"  RSI: `{inds.get('rsi',0):.1f}` | Vol Ratio: `{inds.get('vol_ratio',0):.2f}x`",
        f"  EMA200: `${inds.get('ema200',0):,.0f}` | VWAP: `${inds.get('vwap',0):,.0f}`",
        f"  ATR: `${inds.get('atr',0):,.0f}`",
    ]

    if rl:
        lines += [f"", f"✅ *Active Reasons ({len(rl)})*"]
        for r in rl:
            lines.append(f"  • {r}")

    if ep:
        lines += [
            f"",
            f"📋 *{d} Entry Plan*",
            f"  Entry:       `${ep['entry']:>10,.2f}`",
            f"  Stop Loss:   `${ep['stop_loss']:>10,.2f}`",
            f"  Take Profit: `${ep['take_profit']:>10,.2f}`",
            f"  R:R Ratio:   `1:{ep['rr']:.1f}`",
        ]

    return "\n".join(lines)

def format_backtest_message(result: dict) -> str:
    if not result: return "❌ Backtest failed — check logs"
    ret_icon = "🟢" if result["total_return"] > 0 else "🔴"
    bh_icon  = "✅" if result.get("beat_bh") else "❌"
    lines = [
        f"📊 *Backtest Result — {result['period']}*",
        f"📅 {result['start']} → {result['end']}",
        f"",
        f"{ret_icon} Return:        `{result['total_return']:>8.2%}`",
        f"{bh_icon} Beat Buy&Hold: `{result.get('beat_bh', False)}`  \\(B&H: {result.get('buy_and_hold',0):.1%}\\)",
        f"📈 Sharpe:        `{result['sharpe_ratio']:>8.3f}`",
        f"📉 Max Drawdown:  `{result['max_drawdown']:>8.2%}`",
        f"🔢 Total Trades:  `{result['total_trades']:>8}`",
        f"🎯 Win Rate:      `{result['win_rate']:>8.1%}`",
        f"💰 Profit Factor: `{result['profit_factor']:>8.3f}`",
        f"💵 Avg Win:       `${result['avg_win']:>9,.2f}`",
        f"💸 Avg Loss:      `${result['avg_loss']:>9,.2f}`",
        f"🏦 Final Value:   `${result['final_value']:>9,.2f}`",
        f"",
        f"💾 Saved to memory \\(run #{result.get('run_num',1)}\\)",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    text = (
        "🤖 *BTC Strategy Bot*\n\n"
        "Your 13-indicator strategy in your pocket\\.\n\n"
        "Commands:\n"
        "/signal — Live signal \\+ reasons\n"
        "/backtest `1M`|`3M`|`6M`|`1Y`|`3Y` — Run backtest\n"
        "/learn — Learning report \\(all past runs\\)\n"
        "/status — System health\n"
        "/help — This menu"
    )
    keyboard = [
        [InlineKeyboardButton("📡 Live Signal", callback_data="signal"),
         InlineKeyboardButton("📊 Backtest 1Y", callback_data="bt_1Y")],
        [InlineKeyboardButton("🧠 Learn", callback_data="learn"),
         InlineKeyboardButton("❤️ Status", callback_data="status")],
    ]
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2,
                                    reply_markup=InlineKeyboardMarkup(keyboard))

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)

async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    msg = await update.message.reply_text("⏳ Fetching live signal...")
    try:
        sig = sr.get_signal()
        # Remove df before sending (not JSON-serializable over Telegram)
        sig_clean = {k:v for k,v in sig.items() if k != "df"}
        sr.log_signal_to_sheets(sig_clean)
        text = format_signal_message(sig)
        keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="signal")]]
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN_V2,
                            reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        log.error(traceback.format_exc())
        await msg.edit_text(f"❌ Error: {e}")

async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    period = "1Y"
    if ctx.args and ctx.args[0].upper() in sr.PERIOD_DAYS:
        period = ctx.args[0].upper()

    msg = await update.message.reply_text(
        f"⏳ Running {period} backtest... this takes 2–5 minutes.")
    try:
        result = await _run_backtest_async(period, ctx)
        text   = format_backtest_message(result)
        keyboard = [
            [InlineKeyboardButton("📡 Signal Now", callback_data="signal"),
             InlineKeyboardButton("🧠 Learn", callback_data="learn")],
        ]
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN_V2,
                            reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        log.error(traceback.format_exc())
        await msg.edit_text(f"❌ Backtest error: {e}")

async def _run_backtest_async(period: str, ctx):
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run_backtest_sync, period)

def _run_backtest_sync(period: str):
    try:
        import vectorbt as vbt
    except ImportError:
        return {"error": "vectorbt not installed"}

    days = sr.PERIOD_DAYS[period]
    df_1h  = sr.fetch_ohlcv("hour", 1,  days_back=days)
    df_4h  = sr.fetch_ohlcv("hour", 4,  days_back=days)
    liq_df = sr.fetch_liquidations(max_days=min(days,30))
    fund_df= sr.fetch_funding()
    oi_df  = sr.fetch_oi()

    if df_1h.empty: return {"error": "No OHLCV data"}

    df = sr.compute_indicators(df_1h, df_4h, liq_df, fund_df, oi_df)
    df = sr.compute_scores(df)

    n_long  = int(df["long_signal"].sum())
    n_short = int(df["short_signal"].sum())
    if n_long + n_short == 0:
        return {"error": f"No signals — try lowering MIN_SCORE (currently {sr.MIN_SCORE})"}

    sl_stop = df["atr"] * sr.ATR_SL / df["close"]
    tp_stop = df["atr"] * sr.ATR_TP / df["close"]

    pf = vbt.Portfolio.from_signals(
        close=df["close"], high=df["high"], low=df["low"],
        entries=df["long_signal"], short_entries=df["short_signal"],
        sl_stop=sl_stop, tp_stop=tp_stop,
        init_cash=sr.INIT_CASH, fees=sr.FEES, slippage=sr.SLIPPAGE, freq="1h"
    )

    try:
        t = pf.trades.records_readable
        wins   = len(t[t["PnL"]>0]); losses = len(t[t["PnL"]<0])
        wr     = wins/len(t) if len(t)>0 else 0
        aw     = float(t[t["PnL"]>0]["PnL"].mean()) if wins>0 else 0
        al     = float(t[t["PnL"]<0]["PnL"].mean()) if losses>0 else 0
        pfr    = abs(aw/al) if al!=0 else 0
        trades = t.astype(str).to_dict(orient="records")
    except:
        wr=aw=al=pfr=0; trades=[]

    from datetime import datetime as dt
    bh = float(df["close"].iloc[-1]/df["close"].iloc[0])-1

    result = {
        "run_id":        dt.utcnow().strftime("%Y%m%d_%H%M%S"),
        "run_ts":        dt.utcnow().isoformat(),
        "period":        period,
        "start":         str(df.index[0].date()),
        "end":           str(df.index[-1].date()),
        "total_return":  round(float(pf.total_return()),4),
        "buy_and_hold":  round(bh,4),
        "beat_bh":       bool(pf.total_return()>bh),
        "sharpe_ratio":  round(float(pf.sharpe_ratio() or 0),3),
        "sortino_ratio": round(float(pf.sortino_ratio() or 0),3),
        "max_drawdown":  round(float(pf.max_drawdown()),4),
        "total_trades":  int(pf.trades.count()),
        "win_rate":      round(wr,4),
        "avg_win":       round(aw,2),
        "avg_loss":      round(al,2),
        "profit_factor": round(pfr,3),
        "final_value":   round(float(pf.final_value()),2),
        "init_cash":     sr.INIT_CASH,
        "trades":        trades,
        "config": {
            "min_score": sr.MIN_SCORE, "min_categories": sr.MIN_CATS,
            "trend_required": sr.TREND_REQ, "atr_sl_mult": sr.ATR_SL,
            "atr_tp_mult": sr.ATR_TP, "enable_shorts": sr.ENABLE_SHORT,
            "cat_weights": sr.CAT_WEIGHTS,
        },
    }

    # Log each trade to Google Sheets
    for trade in trades:
        sr.log_trade_to_sheets({**trade, "period": period, "run_id": result["run_id"]})

    run_num = sr.save_to_memory(result)
    result["run_num"] = run_num
    return result

async def cmd_learn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    msg = await update.message.reply_text("⏳ Analysing past runs...")
    text = sr.get_learning_summary()
    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN_V2)

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    from datetime import datetime as dt
    import platform
    mem = sr.load_memory()
    last_sig = "Never"
    try:
        price = sr.get_live_price()
        price_str = f"${price:,.2f}"
    except:
        price_str = "Error"
    text = (
        f"❤️ *System Status*\n\n"
        f"🕐 `{dt.utcnow().strftime('%Y\\-%m\\-%d %H:%M:%S')} UTC`\n"
        f"💲 BTC Price: `{price_str}`\n"
        f"💾 Backtest runs: `{len(mem)}`\n"
        f"📁 Memory file: `{sr.MEMORY_FILE}`\n"
        f"🔑 CC API Key: `{'✅ Set' if sr.CC_API_KEY else '❌ Missing'}`\n"
        f"📊 Google Sheets: `{'✅ Connected' if sr.SHEETS_URL else '⚪ Not set'}`\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


# ══════════════════════════════════════════════════════════════════
# INLINE BUTTON CALLBACKS
# ══════════════════════════════════════════════════════════════════

async def button_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "signal":
        await query.edit_message_text("⏳ Refreshing signal...")
        try:
            sig  = sr.get_signal()
            text = format_signal_message(sig)
            keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="signal")]]
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN_V2,
                                          reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            await query.edit_message_text(f"❌ {e}")

    elif data.startswith("bt_"):
        period = data[3:]
        await query.edit_message_text(f"⏳ Running {period} backtest...")
        try:
            result = await _run_backtest_async(period, ctx)
            text   = format_backtest_message(result)
            keyboard = [[InlineKeyboardButton("📡 Signal Now", callback_data="signal"),
                         InlineKeyboardButton("🧠 Learn", callback_data="learn")]]
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN_V2,
                                          reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            await query.edit_message_text(f"❌ {e}")

    elif data == "learn":
        text = sr.get_learning_summary()
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    elif data == "status":
        fake_update = type("U", (), {"message": query.message, "effective_chat": query.message.chat})()
        await cmd_status(fake_update, ctx)


# ══════════════════════════════════════════════════════════════════
# PUSH NOTIFICATION SENDER (called by GitHub Actions scheduler)
# ══════════════════════════════════════════════════════════════════

async def send_alert_if_signal(app, chat_ids: list):
    """Called by the hourly cron. Only sends a message if signal != NONE."""
    try:
        sig  = sr.get_signal()
        sig_clean = {k:v for k,v in sig.items() if k != "df"}
        sr.log_signal_to_sheets(sig_clean)
        if sig.get("direction") in ("LONG","SHORT"):
            text = format_signal_message(sig)
            for chat_id in chat_ids:
                await app.bot.send_message(chat_id=chat_id, text=text,
                                           parse_mode=ParseMode.MARKDOWN_V2)
            log.info(f"Alert sent: {sig['direction']} at ${sig['price']}")
        else:
            log.info(f"No signal. Long={sig['long_score']:.2f} Short={sig['short_score']:.2f}")
    except Exception as e:
        log.error(f"Alert error: {e}")


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("signal",   cmd_signal))
    app.add_handler(CommandHandler("backtest", cmd_backtest))
    app.add_handler(CommandHandler("learn",    cmd_learn))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CallbackQueryHandler(button_callback))

    log.info("🤖 Bot started — polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
