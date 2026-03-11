# ₿ BTC Strategy v8.5 — Regime-Aware Multi-TF Framework

**5-Signal Regime Voting · Volume Profile · Local Structure Override · MFI · Stochastic · Bull Flag**

Live Streamlit dashboard → deployed via GitHub

---

## 🆕 What's New in v8.5

| Feature | Description |
|---|---|
| **5-Signal Regime Voting** | S1 (1D EMA cross) + S2 (price vs EMAs) + S3 (4H bias) + S4 (HH/HL structure) + S5 (ADX) |
| **Volume Profile** | POC / VAH / VAL computed on 200-bar lookback. Above VAH = bullish, below = bearish |
| **Anchored VWAP** | Anchored from recent swing high and swing low — confluence signals |
| **MFI + Divergences** | Money Flow Index with bull/bear/hidden divergences |
| **Stochastic (14,3)** | Overbought/oversold + golden/death cross signals |
| **Bull Flag Detector** | 5%+ impulse + shallow pullback not breaking Fib 0.382 |
| **RSI + CVD Hidden Divs** | Hidden bull/bear divergences on both RSI and CVD |
| **Fib Extensions** | 1.0 and 1.618 as short entry/target zones |
| **Fib 0.5 Level** | Added to zone scoring |
| **Bear + Local Override** | 3 levels of override when 1D=BEAR but 4H/1H are ranging |

---

## 🧠 How the System Works

### Layer 1 — Macro Regime (1D + 4H)
5-signal voting classifies into: **🚀 STRONG_BULL** · **📈 WEAK_BULL** · **🐻 BEAR** · **↔️ RANGING**

Each regime auto-adjusts: direction filter, score threshold, SL/TP multipliers, indicator weights.

### Layer 2 — Local Structure Override (Bear Market)
When 1D = BEAR, checks 4H and 1H independently:
- **Level 3** (both ranging): MR longs allowed, shorts only at extreme resistance
- **Level 2** (4H ranging): selective MR longs, strict resistance shorts
- **Level 1** (1H ranging only): very tight MR longs, 1H bear-aligned shorts only
- **None** (both trending bear): full BEAR mode

### Layer 3 — Execution Scoring (4 categories)
Trend · Zone · Momentum · Positioning — all 4 must contribute for a signal.

---

## 📁 File Structure

```
core/
  signal_runner.py       ← ALL strategy logic (indicators, regime, scoring, signal, memory)
streamlit_app/
  app.py                 ← Streamlit dashboard (Signal · Backtest · History · Learn tabs)
cron/
  hourly_signal.py       ← GitHub Actions hourly cron
telegram_bot/
  bot.py                 ← Telegram bot (optional)
.github/workflows/
  hourly_signal.yml      ← Hourly cron workflow
requirements.txt
```

---

## 🚀 Deployment

### Streamlit Cloud (via GitHub)
1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io) → Connect repo
3. Set **Main file path**: `streamlit_app/app.py`
4. Add secrets in Streamlit Cloud settings:
   ```toml
   CRYPTOCOMPARE_API_KEY = "your_key"
   TELEGRAM_BOT_TOKEN   = "optional"
   TELEGRAM_CHAT_IDS    = "optional"
   GOOGLE_SHEETS_WEBHOOK = "optional"
   ```

### GitHub Actions (hourly signal cron)
Add secrets in GitHub repo → Settings → Secrets:
- `CRYPTOCOMPARE_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_IDS` (comma-separated)
- `GOOGLE_SHEETS_WEBHOOK` (optional)

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|---|---|---|
| `CRYPTOCOMPARE_API_KEY` | — | CryptoCompare API key (Binance used as primary fallback) |
| `MIN_SCORE` | `5.0` | Minimum signal score (regime overrides per direction) |
| `MIN_CATEGORIES` | `2` | Categories that must contribute (max 4) |
| `TREND_REQUIRED` | `true` | Require trend category contribution |
| `ATR_SL_MULT` | `2.0` | ATR multiplier for stop loss (regime overrides) |
| `ATR_TP_MULT` | `4.0` | ATR multiplier for take profit (regime overrides) |
| `ENABLE_SHORTS` | `true` | Allow short signals |
| `INIT_CASH` | `10000` | Starting capital for backtest |
| `FEES` | `0.001` | Exchange fee per trade |
| `SLIPPAGE` | `0.0005` | Slippage per trade |

---

## 📊 Regime Configs

| Regime | Score L | Score S | SL | TP | Direction |
|---|---|---|---|---|---|
| 🚀 STRONG_BULL | 4.5 | off | 1.5x | 6.0x | Longs only |
| 📈 WEAK_BULL | 5.5 | off | 2.0x | 3.5x | Longs only |
| 🐻 BEAR | 8.0 | 4.0 | 2.5x | 5.0x | Shorts primary |
| ↔️ RANGING | 6.0 | 6.0 | 1.5x | 2.5x | Both sides |
