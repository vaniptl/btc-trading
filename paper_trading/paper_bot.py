"""
Addition to paper_trading/paper_bot.py — replace the run_bot() function
and add run_single_check() at the bottom.

The key fix for GitHub Actions: when PAPER_SINGLE_SHOT=true, the bot
runs exactly ONE signal check and exits cleanly. The cron schedule
in the workflow acts as the "loop". This avoids timeout kills.
"""


def run_single_check():
    """
    Single-shot mode for GitHub Actions.
    Run exactly one signal fetch → monitor → open/close → exit.
    Called when env var PAPER_SINGLE_SHOT=true.
    """
    import signal_runner as sr

    log.info("=== Single-shot paper check ===")
    portfolio = PaperPortfolio()
    log.info(f"Account: ${portfolio.state.account_value:,.2f} | open={len(portfolio.open_trades)}")

    # 1. Fetch signal
    try:
        sig = sr.get_signal()
        direction = sig.get("direction", "NONE")
        price     = sig.get("price", 0)
        regime    = sig.get("regime", "?")
        ls        = sig.get("long_score",  0)
        ss        = sig.get("short_score", 0)
        log.info(f"Signal: {direction} @ ${price:,.2f} | regime={regime} | L={ls:.2f} S={ss:.2f}")
    except Exception as e:
        log.error(f"Signal fetch failed: {e}")
        return

    # 2. Monitor + close open trades
    portfolio.monitor_trades(sig)

    # 3. Open new trade if signal is active
    if direction in ("LONG", "SHORT"):
        portfolio.open_trade(sig)

    # 4. Log summary
    stats = portfolio.get_stats()
    log.info(
        f"Done | account=${stats['account_value']:,.2f} | "
        f"return={stats['total_return_pct']:+.1%} | "
        f"WR={stats['win_rate']:.0%} | "
        f"trades={stats['total_trades']} | "
        f"open={stats['open_trades']}"
    )


def run_bot():
    """
    Continuous 24/7 loop for local deployment.
    For GitHub Actions, use PAPER_SINGLE_SHOT=true instead.
    """
    import signal_runner as sr

    log.info("=" * 60)
    log.info("🤖 Trinity.AI Paper Trading Bot — CONTINUOUS MODE")
    log.info(f"   Capital:        ${INIT_CAPITAL:,.0f}")
    log.info(f"   Check interval: {CHECK_INTERVAL}s")
    log.info(f"   Max daily loss: {MAX_DAILY_LOSS:.0%}")
    log.info(f"   Tip: set PAPER_SINGLE_SHOT=true for GitHub Actions")
    log.info("=" * 60)

    portfolio = PaperPortfolio()
    _notify_telegram(
        f"🤖 *Paper Bot Started (continuous)*\n"
        f"Capital: `${portfolio.state.account_value:,.2f}`\n"
        f"Interval: `{CHECK_INTERVAL}s`"
    )

    iteration       = 0
    last_fetch_time = 0

    while portfolio.state.running:
        try:
            now = time.time()
            iteration += 1

            if now - last_fetch_time >= CHECK_INTERVAL:
                log.info(f"[{iteration}] Fetching signal…")
                try:
                    sig = sr.get_signal()
                    last_fetch_time = now
                    direction = sig.get("direction", "NONE")
                    price     = sig.get("price", 0)
                    regime    = sig.get("regime", "?")
                    log.info(f"Signal: {direction} @ ${price:,.2f} | regime={regime}")

                    portfolio.monitor_trades(sig)

                    if direction in ("LONG", "SHORT"):
                        portfolio.open_trade(sig)

                    if iteration % 10 == 0:
                        stats = portfolio.get_stats()
                        log.info(
                            f"📊 account=${stats['account_value']:,.2f} | "
                            f"return={stats['total_return_pct']:+.1%} | "
                            f"WR={stats['win_rate']:.0%}"
                        )

                except Exception as e:
                    log.error(f"Signal fetch error: {e}")
                    time.sleep(30)
                    continue

            elif portfolio.open_trades:
                try:
                    cp = portfolio.broker.get_price()
                    if cp > 0:
                        portfolio.monitor_trades({"direction": portfolio.state.last_signal_dir, "price": cp})
                except Exception as e:
                    log.debug(f"Price check: {e}")

            time.sleep(min(10, CHECK_INTERVAL))

        except KeyboardInterrupt:
            log.info("Shutdown (KeyboardInterrupt)")
            portfolio.state.running = False
            portfolio._save_state()
            break
        except Exception as e:
            log.error(f"Main loop error: {e}", exc_info=True)
            time.sleep(60)

    log.info("🛑 Bot stopped")


if __name__ == "__main__":
    single_shot = os.environ.get("PAPER_SINGLE_SHOT", "").lower() in ("true", "1", "yes")
    if single_shot:
        run_single_check()
    else:
        run_bot()
