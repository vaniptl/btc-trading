"""
signal_runner.py — BTC Strategy Core Engine v8.5
Regime-Aware + 5-Signal Voting + 1H+4H Local Structure Override
Shared by: Telegram bot, Streamlit dashboard, GitHub Actions cron

NEW in v8.5 vs v7:
  ✅ Volume Profile (POC / VAH / VAL) — BH's primary decision tool
  ✅ Anchored VWAP from swing highs/lows
  ✅ Money Flow Index (MFI) + divergences + hidden divergences
  ✅ Stochastic Oscillator (14,3) — overbought/oversold + cross signals
  ✅ Bull Flag detector — 5%+ impulse + shallow pullback
  ✅ Fib 0.5 + extensions (1.0, 1.618) as zone/target levels
  ✅ RSI Hidden Divergence
  ✅ CVD divergence
  ✅ 5-signal Regime Voting (S1–S5 with weighted votes)
  ✅ Bear + Local Structure Override (3 levels: LEVEL1/2/3)
  ✅ VAH bias switch in trend scoring
  ✅ Real Buying / Real Selling confirmation
"""

import os, json, time, requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Config from environment variables ─────────────────────────────
CC_API_KEY   = os.environ.get("CRYPTOCOMPARE_API_KEY", "")
INIT_CASH    = float(os.environ.get("INIT_CASH", "10000"))
FEES         = float(os.environ.get("FEES", "0.001"))
SLIPPAGE     = float(os.environ.get("SLIPPAGE", "0.0005"))
MIN_SCORE    = float(os.environ.get("MIN_SCORE", "5.0"))
MIN_CATS     = int(os.environ.get("MIN_CATEGORIES", "2"))
TREND_REQ    = os.environ.get("TREND_REQUIRED", "true").lower() == "true"
ATR_SL       = float(os.environ.get("ATR_SL_MULT", "2.0"))
ATR_TP       = float(os.environ.get("ATR_TP_MULT", "4.0"))
ENABLE_SHORT = os.environ.get("ENABLE_SHORTS", "true").lower() == "true"
MEMORY_FILE  = Path(os.environ.get("MEMORY_FILE", "btc_backtest_memory_v8.json"))
SHEETS_URL   = os.environ.get("GOOGLE_SHEETS_WEBHOOK", "")

PERIOD_DAYS = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "3Y": 1095}

# Base weights — v8.5 (regimes scale these via weights_override)
CAT_WEIGHTS = {
    "4h_bias": 3.0, "ema200": 3.0, "ema50": 2.5, "ema_ribbon": 2.0, "bos": 2.5,
    "vah_bias": 2.0, "volume_profile": 3.0, "support_resistance": 2.5,
    "avwap_anchor": 2.5, "fib618": 2.0, "fib50": 1.5, "fib382": 1.5, "fib_ext": 1.5,
    "order_blocks": 2.5, "rsi": 2.0, "rsi_div": 2.5, "rsi_hidden_div": 2.0,
    "mfi": 2.0, "stochastic": 1.5, "cvd": 2.0, "cvd_div": 2.5, "real_buying": 2.0,
    "bull_flag": 2.0, "choch": 1.0, "vwap": 1.0, "liquidations": 2.0,
    "funding": 1.5, "oi": 1.5, "volume": 0.5,
}

REGIME_CONFIGS = {
    "STRONG_BULL": {
        "enable_longs": True, "enable_shorts": False,
        "min_score_long": 4.5, "min_score_short": 99.0,
        "atr_sl_mult": 1.5, "atr_tp_mult": 6.0,
        "weights_override": {
            "4h_bias": 4.0, "ema200": 4.0, "ema50": 3.5, "vah_bias": 3.0, "bos": 3.5,
            "rsi_hidden_div": 3.5, "rsi_div": 1.5, "bull_flag": 3.5, "cvd_div": 2.0,
            "volume_profile": 3.5, "avwap_anchor": 3.0, "stochastic": 0.5, "mfi": 2.5,
        },
        "description": "STRONG BULL — Longs only | Wide targets", "emoji": "🚀",
    },
    "WEAK_BULL": {
        "enable_longs": True, "enable_shorts": False,
        "min_score_long": 5.5, "min_score_short": 99.0,
        "atr_sl_mult": 2.0, "atr_tp_mult": 3.5,
        "weights_override": {
            "4h_bias": 3.0, "ema200": 3.5, "volume_profile": 3.5, "support_resistance": 3.0,
            "rsi_div": 3.5, "mfi": 3.0, "cvd_div": 3.0, "rsi_hidden_div": 2.0,
            "bull_flag": 2.0, "stochastic": 2.0, "vah_bias": 2.5, "order_blocks": 3.0,
        },
        "description": "WEAK BULL — Selective longs | Pullbacks only", "emoji": "📈",
    },
    "BEAR": {
        "enable_longs": False, "enable_shorts": True,
        "min_score_long": 8.0, "min_score_short": 4.0,
        "atr_sl_mult": 2.5, "atr_tp_mult": 5.0,
        "weights_override": {
            "4h_bias": 4.0, "ema200": 4.0, "bos": 3.0, "support_resistance": 3.5,
            "rsi_div": 4.0, "mfi": 3.0, "cvd_div": 3.5, "order_blocks": 3.0,
            "vah_bias": 3.5, "volume_profile": 3.5, "avwap_anchor": 3.0,
            "stochastic": 2.5, "bull_flag": 0.5, "rsi_hidden_div": 1.5,
        },
        "description": "BEAR — Shorts primary | Hard gate for longs", "emoji": "🐻",
    },
    "RANGING": {
        "enable_longs": True, "enable_shorts": True,
        "min_score_long": 6.0, "min_score_short": 6.0,
        "atr_sl_mult": 1.5, "atr_tp_mult": 2.5,
        "weights_override": {
            "support_resistance": 4.0, "volume_profile": 4.0, "order_blocks": 3.5,
            "vah_bias": 3.0, "fib618": 3.0, "fib50": 2.5, "avwap_anchor": 3.5,
            "rsi_div": 3.5, "mfi": 3.0, "stochastic": 3.0, "cvd_div": 3.0,
            "4h_bias": 1.5, "ema200": 2.0, "bos": 1.5, "bull_flag": 1.5,
        },
        "description": "RANGING — Mean reversion | Both sides | Tight R:R", "emoji": "↔️",
    },
}


# ══════════════════════════════════════════════════════════════════
# BINANCE + CRYPTOCOMPARE DATA FETCHERS
# ══════════════════════════════════════════════════════════════════

BINANCE_SPOT_URL = "https://data-api.binance.vision/api/v3/klines"
BINANCE_FAPI_URL  = "https://fapi.binance.com/fapi/v1"
BINANCE_FDATA_URL = "https://fapi.binance.com/futures/data"

# Add this constant at the top of signal_runner.py
BINANCE_DATA_URL = "https://data-api.binance.vision/api/v3/klines"

def fetch_ohlcv_binance(symbol="BTCUSDT", interval="1h", days_back=365):
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - int(days_back * 86_400_000)
    ms_step  = INTERVAL_MS.get(interval, 3_600_000)
    all_data, cur = [], start_ms
    
    while cur < end_ms:
        try:
            r = requests.get(
                BINANCE_DATA_URL,   # <-- use the no-auth mirror
                params={
                    "symbol": symbol, "interval": interval,
                    "startTime": cur, "endTime": end_ms, "limit": 1000,
                },
                timeout=20,
            )
            r.raise_for_status()
            batch = r.json()
            if not batch: break
            all_data.extend(batch)
            cur = batch[-1][0] + ms_step
            if len(batch) < 1000: break
            time.sleep(0.12)
        except Exception as e:
            print(f"Binance error: {e}"); break
    
    if not all_data: return pd.DataFrame()
    df = pd.DataFrame(all_data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qv","nt","tbv","tbq","ig"])
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_localize(None)
    df = df.set_index("datetime")
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[["open","high","low","close","volume"]]
    return df[df["volume"] > 0].sort_index()[~df.index.duplicated(keep="last")]
def fetch_ohlcv_cc(unit="hour", aggregate=1, days_back=365):
    eps = {
        "hour": "https://min-api.cryptocompare.com/data/v2/histohour",
        "day":  "https://min-api.cryptocompare.com/data/v2/histoday",
    }
    headers = {}
    if CC_API_KEY and CC_API_KEY not in ("", "YOUR_CRYPTOCOMPARE_KEY_HERE"):
        headers["authorization"] = f"Apikey {CC_API_KEY}"
    mins_per   = {"hour": 60*aggregate, "day": 1440*aggregate}[unit]
    total_need = int(days_back * 1440 / mins_per)
    all_data, to_ts, fetched = [], None, 0
    while fetched < total_need:
        params = {"fsym": "BTC", "tsym": "USD",
                  "limit": min(2000, total_need-fetched), "aggregate": aggregate}
        if to_ts: params["toTs"] = to_ts
        try:
            resp = requests.get(eps[unit], params=params, headers=headers, timeout=15)
            data = resp.json()
            if data.get("Response") != "Success": break
            candles = data["Data"]["Data"]
            if not candles: break
            all_data = candles + all_data
            to_ts    = candles[0]["time"] - 1
            fetched += len(candles)
            time.sleep(0.15)
        except Exception as e:
            print(f"CC error: {e}"); break
    if not all_data: return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("datetime").rename(columns={"volumefrom": "volume"})
    df = df[["open","high","low","close","volume"]].astype(float)
    df = df[df["volume"] > 0].sort_index()
    return df[~df.index.duplicated(keep="last")]

# In signal_runner.py — replace the entire fetch_ohlcv() function with this:

def fetch_ohlcv(unit="hour", aggregate=1, days_back=365):
    """Binance public API (no key) — primary. CC fallback only if Binance fails."""
    interval_map = {("hour", 1): "1h", ("hour", 4): "4h", ("day", 1): "1d"}
    interval = interval_map.get((unit, aggregate), "1h")
    df = fetch_ohlcv_binance("BTCUSDT", interval, days_back)
    if not df.empty:
        return df
    print(f"[WARN] Binance failed for {interval}, trying CC fallback...")
    return fetch_ohlcv_cc(unit, aggregate, days_back)
  
def fetch_liquidations(max_days=30):
    url, all_orders = f"{BINANCE_FAPI_URL}/allForceOrders", []
    end_ms, day_ms = int(time.time()*1000), 86_400_000
    for _ in range(max_days):
        start_ms = end_ms - day_ms
        try:
            r = requests.get(url, params={
                "symbol": "BTCUSDT", "startTime": start_ms,
                "endTime": end_ms, "limit": 1000,
            }, timeout=15)
            raw = r.json()
            if isinstance(raw, list): all_orders.extend(raw)
        except: pass
        end_ms = start_ms
        time.sleep(0.1)
    if not all_orders: return pd.DataFrame()
    df = pd.DataFrame(all_orders)
    df["datetime"] = pd.to_datetime(pd.to_numeric(df["time"], errors="coerce"), unit="ms")
    df = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
    df["price"]  = pd.to_numeric(df["price"],          errors="coerce")
    df["qty"]    = pd.to_numeric(df["origQty"],        errors="coerce")
    df["side"]   = df["side"].astype(str)
    df["value"]  = df["price"] * df["qty"]
    return df[["side","price","qty","value"]].dropna()

def fetch_funding():
    try:
        r   = requests.get(f"{BINANCE_FAPI_URL}/fundingRate",
                           params={"symbol":"BTCUSDT","limit":500}, timeout=15)
        raw = r.json()
        if not isinstance(raw, list) or not raw: return pd.DataFrame()
        df  = pd.DataFrame(raw)
        tc  = next((c for c in df.columns if "time" in c.lower()), None)
        if not tc: return pd.DataFrame()
        df["datetime"]     = pd.to_datetime(pd.to_numeric(df[tc], errors="coerce"), unit="ms")
        df                 = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
        df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
        return df[["funding_rate"]].dropna()
    except: return pd.DataFrame()

def fetch_oi():
    try:
        r   = requests.get(f"{BINANCE_FDATA_URL}/openInterestHist",
                           params={"symbol":"BTCUSDT","period":"1h","limit":500}, timeout=15)
        raw = r.json()
        if not isinstance(raw, list) or not raw: return pd.DataFrame()
        df  = pd.DataFrame(raw)
        tc  = next((c for c in df.columns if "time" in c.lower()), None)
        if not tc: return pd.DataFrame()
        df["datetime"] = pd.to_datetime(pd.to_numeric(df[tc], errors="coerce"), unit="ms")
        df             = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
        df["oi"]       = pd.to_numeric(df["sumOpenInterest"], errors="coerce")
        return df[["oi"]].dropna()
    except: return pd.DataFrame()

def get_live_price():
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price",
                         params={"symbol":"BTCUSDT"}, timeout=10)
        return float(r.json()["price"])
    except:
        try:
            r = requests.get("https://min-api.cryptocompare.com/data/price",
                             params={"fsym":"BTC","tsyms":"USD"}, timeout=10)
            return float(r.json()["USD"])
        except:
            return 0.0


# ══════════════════════════════════════════════════════════════════
# BASE INDICATOR HELPERS
# ══════════════════════════════════════════════════════════════════

def _ema(s, p): return s.ewm(span=p, adjust=False).mean()

def _rsi(close, p=14):
    d  = close.diff()
    ag = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - (100 / (1 + ag / al.replace(0, np.nan)))

def _atr(df, p=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=p, adjust=False).mean()

def _mfi(df, period=14):
    tp     = (df["high"] + df["low"] + df["close"]) / 3
    raw_mf = tp * df["volume"]
    pos_mf = raw_mf.where(tp > tp.shift(1), 0.0)
    neg_mf = raw_mf.where(tp < tp.shift(1), 0.0)
    mfr    = pos_mf.rolling(period).sum() / neg_mf.rolling(period).sum().replace(0, np.nan)
    return 100 - (100 / (1 + mfr))


# ══════════════════════════════════════════════════════════════════
# v8.5 NEW INDICATOR HELPERS
# ══════════════════════════════════════════════════════════════════

def compute_volume_profile(df, bins=50, lookback=200):
    """POC / VAH / VAL — BH's primary decision tool. Above VAH = bullish."""
    df_r = df.tail(lookback).copy()
    pmin, pmax = df_r["low"].min(), df_r["high"].max()
    if pmax <= pmin:
        mid = (pmax + pmin) / 2
        return float(mid), float(pmax), float(pmin)
    price_bins  = np.linspace(pmin, pmax, bins+1)
    vol_profile = np.zeros(bins)
    for i in range(len(df_r)):
        lo  = df_r["low"].iloc[i]; hi = df_r["high"].iloc[i]; vol = df_r["volume"].iloc[i]
        cr  = hi - lo
        if cr < 1e-9: continue
        for b in range(bins):
            ov = max(0.0, min(hi, price_bins[b+1]) - max(lo, price_bins[b]))
            vol_profile[b] += vol * ov / cr
    poc_idx   = int(np.argmax(vol_profile))
    poc_price = float((price_bins[poc_idx] + price_bins[poc_idx+1]) / 2)
    total_vol  = vol_profile.sum()
    sorted_idx = list(np.argsort(vol_profile)[::-1])
    va_bins, va_vol = [], 0.0
    for idx in sorted_idx:
        va_bins.append(idx); va_vol += vol_profile[idx]
        if va_vol >= total_vol * 0.70: break
    vah = float(price_bins[max(va_bins)+1])
    val = float(price_bins[min(va_bins)])
    return poc_price, vah, val

def anchored_vwap(df, anchor_pos):
    """Anchored VWAP from a specific bar — BH anchors at swing highs/lows."""
    if anchor_pos >= len(df) or anchor_pos < 0:
        return pd.Series(np.nan, index=df.index)
    sl = df.iloc[anchor_pos:].copy()
    tp = (sl["high"] + sl["low"] + sl["close"]) / 3
    av = (tp * sl["volume"]).cumsum() / sl["volume"].cumsum()
    result = pd.Series(np.nan, index=df.index)
    result.iloc[anchor_pos:] = av.values
    return result

def detect_bull_flag(df, lookback=20, flag_bars=8):
    """Bull flag: strong impulse (>=5%) + controlled pullback not breaking Fib 0.382."""
    flags = pd.Series(False, index=df.index)
    for i in range(lookback + flag_bars, len(df)):
        imp_w  = df.iloc[i-lookback-flag_bars : i-flag_bars]
        flag_w = df.iloc[i-flag_bars : i]
        imp_lo = imp_w["low"].min(); imp_hi = imp_w["high"].max()
        move   = (imp_hi - imp_lo) / max(imp_lo, 1e-9)
        if move < 0.05: continue
        fib382_lvl = imp_hi - (imp_hi - imp_lo) * 0.382
        if flag_w["low"].min() < fib382_lvl: continue
        if df["close"].iloc[i] > flag_w["high"].max() * 0.99:
            flags.iloc[i] = True
    return flags


# ══════════════════════════════════════════════════════════════════
# MAIN INDICATOR ENGINE v8.5
# ══════════════════════════════════════════════════════════════════

def compute_indicators(df, df_4h, df_1d, liq_df, funding_df, oi_df):
    df = df.copy()

    # 1H EMA ribbon + RSI + ATR
    df["ema8"]  = _ema(df["close"], 8)
    df["ema21"] = _ema(df["close"], 21)
    df["ema55"] = _ema(df["close"], 55)
    df["rsi"]   = _rsi(df["close"])
    df["atr"]   = _atr(df)

    # EMA50 + EMA200 from 1D (proper warmup), reindexed to 1H via ffill
    if not df_1d.empty and len(df_1d) >= 50:
        d_ema50  = _ema(df_1d["close"], 50)
        d_ema200 = _ema(df_1d["close"], 200)
        df["ema50"]  = d_ema50.reindex(df.index,  method="ffill")
        df["ema200"] = d_ema200.reindex(df.index, method="ffill")
        if len(d_ema200) >= 21:
            df.attrs["d_ema200_slope"] = float((d_ema200.iloc[-1] - d_ema200.iloc[-21]) / d_ema200.iloc[-21])
            df.attrs["d_ema50_slope"]  = float((d_ema50.iloc[-1]  - d_ema50.iloc[-21])  / d_ema50.iloc[-21])
        else:
            df.attrs["d_ema200_slope"] = 0.0; df.attrs["d_ema50_slope"] = 0.0
        df.attrs["d_ema50_price"]  = round(float(d_ema50.iloc[-1]),  2)
        df.attrs["d_ema200_price"] = round(float(d_ema200.iloc[-1]), 2)
    else:
        df["ema50"]  = _ema(df["close"], 50)
        df["ema200"] = _ema(df["close"], 200)
        df.attrs.update({"d_ema200_slope":0.0,"d_ema50_slope":0.0,
                         "d_ema50_price":float(df["ema50"].iloc[-1]),
                         "d_ema200_price":float(df["ema200"].iloc[-1])})

    # Standard VWAP (session-based cumsum)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (tp * df["volume"]).cumsum() / df["volume"].cumsum()

    # CVD + divergence
    hl = (df["high"] - df["low"]).replace(0, np.nan)
    df["cvd"] = (df["volume"] * ((df["close"]-df["low"])/hl - (df["high"]-df["close"])/hl)).cumsum()
    cvd_v = df["cvd"].values; c_v = df["close"].values
    cbl   = [False]*len(df); cbr = [False]*len(df)
    for i in range(20, len(df)):
        p = i - 10
        if np.isnan(cvd_v[i]) or np.isnan(cvd_v[p]): continue
        if c_v[i] < c_v[p] and cvd_v[i] > cvd_v[p]: cbl[i] = True
        if c_v[i] > c_v[p] and cvd_v[i] < cvd_v[p]: cbr[i] = True
    df["cvd_bull_div"] = cbl; df["cvd_bear_div"] = cbr

    # Real Buying / Selling
    vol_ma = df["volume"].rolling(20).mean(); vol_std = df["volume"].rolling(20).std()
    df["vol_confirm"] = df["volume"] > vol_ma
    df["vol_spike"]   = df["volume"] > (vol_ma + 2*vol_std)
    df["vol_ratio"]   = df["volume"] / vol_ma
    cr = df["cvd"].diff() > 0; cf = df["cvd"].diff() < 0
    pu = df["close"].pct_change() > 0; pd_ = df["close"].pct_change() < 0
    df["real_buying"]  = cr & pu  & df["vol_confirm"]
    df["real_selling"] = cf & pd_ & df["vol_confirm"]

    # ── NEW: MFI + divergences ────────────────────────────────────
    df["mfi"] = _mfi(df)
    lb = 10
    price_ll = df["close"] < df["close"].shift(lb); price_hh = df["close"] > df["close"].shift(lb)
    mfi_hl   = df["mfi"]   > df["mfi"].shift(lb);   mfi_lh   = df["mfi"]   < df["mfi"].shift(lb)
    df["mfi_bull_div"]    = price_ll & mfi_hl  & (df["mfi"] < 40)
    df["mfi_bear_div"]    = price_hh & mfi_lh  & (df["mfi"] > 60)
    df["mfi_hidden_bull"] = (~price_ll) & (~mfi_hl) & (df["mfi"] < 50)
    df["mfi_hidden_bear"] = (~price_hh) & (~mfi_lh) & (df["mfi"] > 50)
    df["mfi_oversold"]    = df["mfi"] < 20
    df["mfi_overbought"]  = df["mfi"] > 80

    # ── NEW: Volume Profile (POC / VAH / VAL) ────────────────────
    poc, vah, val = compute_volume_profile(df)
    df["poc"] = poc; df["vah"] = vah; df["val"] = val
    safe_close = df["close"].replace(0, np.nan)
    df["near_poc"]  = (df["close"] - poc).abs() / safe_close < 0.008
    df["near_vah"]  = (df["close"] - vah).abs() / safe_close < 0.008
    df["near_val"]  = (df["close"] - val).abs() / safe_close < 0.008
    df["above_vah"] = df["close"] > vah
    df["below_vah"] = df["close"] < vah

    # ── NEW: Anchored VWAP ────────────────────────────────────────
    lb_av  = min(30, len(df)-1)
    sh_idx = df["high"].iloc[-lb_av:].idxmax()
    sl_idx = df["low"].iloc[-lb_av:].idxmin()
    sh_pos = df.index.get_loc(sh_idx)
    sl_pos = df.index.get_loc(sl_idx)
    df["avwap_high"] = anchored_vwap(df, sh_pos)
    df["avwap_low"]  = anchored_vwap(df, sl_pos)
    df["avwap_bull_conf"] = df["avwap_low"].notna()  & ((df["close"]-df["avwap_low"]).abs()/safe_close < 0.01)
    df["avwap_bear_conf"] = df["avwap_high"].notna() & ((df["close"]-df["avwap_high"]).abs()/safe_close < 0.01)

    # ── NEW: Stochastic Oscillator ────────────────────────────────
    ll_k = df["low"].rolling(14).min(); hh_k = df["high"].rolling(14).max()
    stoch_k = 100 * (df["close"] - ll_k) / (hh_k - ll_k + 1e-9)
    stoch_d = stoch_k.rolling(3).mean()
    df["stoch_k"] = stoch_k; df["stoch_d"] = stoch_d
    df["stoch_overbought"] = stoch_k > 80
    df["stoch_oversold"]   = stoch_k < 20
    df["stoch_bull_cross"] = (stoch_k > stoch_d) & (stoch_k.shift(1) <= stoch_d.shift(1))
    df["stoch_bear_cross"] = (stoch_k < stoch_d) & (stoch_k.shift(1) >= stoch_d.shift(1))

    # ── NEW: Bull Flag ────────────────────────────────────────────
    df["bull_flag"] = detect_bull_flag(df)

    # S/R levels
    h_v, l_v, c_v2 = df["high"].values, df["low"].values, df["close"].values
    sw, ph2, pl2 = 3, [], []
    for i in range(sw, len(df)-sw):
        if h_v[i] == max(h_v[i-sw:i+sw+1]): ph2.append(h_v[i])
        if l_v[i] == min(l_v[i-sw:i+sw+1]): pl2.append(l_v[i])
    cur2 = c_v2[-1]
    df["near_support"]    = pd.Series(False, index=df.index)
    df["near_resistance"] = pd.Series(False, index=df.index)
    for lvl in [v for v in pl2 if v < cur2]:
        df["near_support"]    |= (df["close"] - lvl).abs() / lvl < 0.005
    for lvl in [v for v in ph2 if v > cur2]:
        df["near_resistance"] |= (df["close"] - lvl).abs() / lvl < 0.005

    # Fibonacci retracements + NEW extensions
    r100 = df.tail(100); sh2 = r100["high"].max(); sl2 = r100["low"].min(); diff2 = sh2 - sl2
    down2 = r100["high"].idxmax() < r100["low"].idxmin()
    if down2:
        fib382,fib50,fib618 = sl2+diff2*0.382, sl2+diff2*0.500, sl2+diff2*0.618
        fib100,fib1618      = sl2+diff2*1.000, sl2+diff2*1.618
    else:
        fib382,fib50,fib618 = sh2-diff2*0.382, sh2-diff2*0.500, sh2-diff2*0.618
        fib100,fib1618      = sh2-diff2*1.000, sh2-diff2*1.618
    df["near_fib382"]  = (df["close"] - fib382).abs() / safe_close < 0.008
    df["near_fib50"]   = (df["close"] - fib50).abs()  / safe_close < 0.008
    df["near_fib618"]  = (df["close"] - fib618).abs() / safe_close < 0.008
    df["near_fib100"]  = (df["close"] - fib100).abs() / safe_close < 0.008
    df["near_fib1618"] = (df["close"] - fib1618).abs()/ safe_close < 0.008

    # Order Blocks
    ob_window = 10
    body_up = (df["close"] - df["open"]).abs()
    df["in_bullish_ob"] = pd.Series(False, index=df.index)
    df["in_bearish_ob"] = pd.Series(False, index=df.index)
    for i in range(ob_window, len(df)):
        w = df.iloc[i-ob_window:i]
        bear_idx = w["close"].idxmin()
        bull_idx = w["close"].idxmax()
        bear_ob_high = float(df.loc[bear_idx, "high"]); bear_ob_low = float(df.loc[bear_idx, "low"])
        bull_ob_high = float(df.loc[bull_idx, "high"]); bull_ob_low = float(df.loc[bull_idx, "low"])
        cur_c = float(df["close"].iloc[i])
        if bear_ob_low <= cur_c <= bear_ob_high: df["in_bullish_ob"].iloc[i] = True
        if bull_ob_low <= cur_c <= bull_ob_high: df["in_bearish_ob"].iloc[i] = True

    # RSI divergences
    rsi_v2 = df["rsi"].values; close_v = df["close"].values
    rbd, rbb, rhb, rhbr = [False]*len(df), [False]*len(df), [False]*len(df), [False]*len(df)
    for i in range(14, len(df)):
        p = i - 10
        if np.isnan(rsi_v2[i]) or np.isnan(rsi_v2[p]): continue
        if close_v[i] < close_v[p] and rsi_v2[i] > rsi_v2[p] and rsi_v2[i] < 50: rbd[i] = True
        if close_v[i] > close_v[p] and rsi_v2[i] < rsi_v2[p] and rsi_v2[i] > 50: rbb[i] = True
        if close_v[i] > close_v[p] and rsi_v2[i] > rsi_v2[p] and rsi_v2[i] < 50: rhb[i] = True
        if close_v[i] < close_v[p] and rsi_v2[i] < rsi_v2[p] and rsi_v2[i] > 50: rhbr[i] = True
    df["rsi_bull_div"]       = rbd
    df["rsi_bear_div"]       = rbb
    df["rsi_hidden_bull_div"]= rhb
    df["rsi_hidden_bear_div"]= rhbr
    df["rsi_trend_bull_zone"] = (df["rsi"] >= 40) & (df["rsi"] <= 60)
    df["rsi_trend_bear_zone"] = df["rsi_trend_bull_zone"]

    # 4H bias
    if not df_4h.empty and len(df_4h) >= 21:
        e8_4h  = _ema(df_4h["close"], 8)
        e21_4h = _ema(df_4h["close"], 21)
        e55_4h = _ema(df_4h["close"], 55)
        bull_4h = (e8_4h > e21_4h) & (e21_4h > e55_4h)
        bear_4h = (e8_4h < e21_4h) & (e21_4h < e55_4h)
        df["bias_bullish_4h"] = bull_4h.reindex(df.index, method="ffill").fillna(False)
        df["bias_bearish_4h"] = bear_4h.reindex(df.index, method="ffill").fillna(False)
    else:
        df["bias_bullish_4h"] = df["close"] > df["ema55"]
        df["bias_bearish_4h"] = df["close"] < df["ema55"]

    # BOS (Break of Structure)
    highs_v = df["high"].values; lows_v = df["low"].values; close_vv = df["close"].values
    bos_up = [False]*len(df); bos_dn = [False]*len(df)
    for i in range(20, len(df)):
        prev_high = max(highs_v[max(0,i-20):i])
        prev_low  = min(lows_v[max(0,i-20):i])
        if close_vv[i] > prev_high: bos_up[i] = True
        if close_vv[i] < prev_low:  bos_dn[i] = True
    df["bos_up"] = bos_up; df["bos_down"] = bos_dn

    # CHoCH
    df["choch"] = df["bos_up"] | df["bos_down"]

    # Liquidations
    df["major_long_liq"]  = pd.Series(False, index=df.index)
    df["major_short_liq"] = pd.Series(False, index=df.index)
    if not liq_df.empty and "side" in liq_df.columns and "value" in liq_df.columns:
        liq_df2 = liq_df.reindex(df.index, method="nearest", tolerance="1h")
        if not liq_df2.empty:
            long_liq  = liq_df[liq_df["side"] == "SELL"]["value"]
            short_liq = liq_df[liq_df["side"] == "BUY"]["value"]
            thr = liq_df["value"].quantile(0.90) if len(liq_df) > 10 else 1e6
            ll_ts = long_liq[long_liq > thr].index
            sl_ts = short_liq[short_liq > thr].index
            for ts in ll_ts:
                mask = (df.index >= ts - timedelta(hours=1)) & (df.index <= ts + timedelta(hours=1))
                df.loc[mask, "major_long_liq"] = True
            for ts in sl_ts:
                mask = (df.index >= ts - timedelta(hours=1)) & (df.index <= ts + timedelta(hours=1))
                df.loc[mask, "major_short_liq"] = True

    # Funding rate extremes
    df["funding_extreme_long"]  = pd.Series(False, index=df.index)
    df["funding_extreme_short"] = pd.Series(False, index=df.index)
    if not funding_df.empty and "funding_rate" in funding_df.columns:
        fr_s = funding_df["funding_rate"].reindex(df.index, method="ffill")
        df["funding_extreme_long"]  = fr_s < -0.0005
        df["funding_extreme_short"] = fr_s > 0.0005

    # Open Interest confirmation
    df["oi_confirm_long"]  = pd.Series(False, index=df.index)
    df["oi_confirm_short"] = pd.Series(False, index=df.index)
    if not oi_df.empty and "oi" in oi_df.columns:
        oi_s  = oi_df["oi"].reindex(df.index, method="ffill")
        oi_ch = oi_s.pct_change()
        df["oi_confirm_long"]  = (df["close"].pct_change() > 0) & (oi_ch > 0.005)
        df["oi_confirm_short"] = (df["close"].pct_change() < 0) & (oi_ch > 0.005)

    return df.dropna(subset=["ema200","rsi","atr"])


# ══════════════════════════════════════════════════════════════════
# REGIME DETECTION ENGINE v8.5 — 5-signal voting
# ══════════════════════════════════════════════════════════════════

def price_structure(df_daily, lookback=20):
    if df_daily is None or len(df_daily) < lookback: return "neutral"
    d = df_daily.tail(lookback)
    highs, lows = d["high"].values, d["low"].values
    ph, pl = [], []
    for i in range(2, len(highs)-2):
        if highs[i] == max(highs[i-2:i+3]): ph.append(highs[i])
        if lows[i]  == min(lows[i-2:i+3]):  pl.append(lows[i])
    if len(ph) >= 2 and len(pl) >= 2:
        hh = ph[-1]>ph[-2]; hl = pl[-1]>pl[-2]
        lh = ph[-1]<ph[-2]; ll = pl[-1]<pl[-2]
        if hh and hl: return "bull"
        if lh and ll: return "bear"
        if hh and ll: return "neutral"
        if lh and hl: return "ranging"
    return "neutral"

def adx_strength(df_daily, period=14):
    if df_daily is None or len(df_daily) < period*2: return 25.0
    h = df_daily["high"]; l = df_daily["low"]; c = df_daily["close"]
    up = h.diff(); dn = -l.diff()
    pdm = up.where((up>dn)&(up>0), 0.0); ndm = dn.where((dn>up)&(dn>0), 0.0)
    tr  = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    atr14 = tr.ewm(span=period, adjust=False).mean()
    pdi = 100*pdm.ewm(span=period,adjust=False).mean()/atr14.replace(0,np.nan)
    ndi = 100*ndm.ewm(span=period,adjust=False).mean()/atr14.replace(0,np.nan)
    dx  = 100*(pdi-ndi).abs()/(pdi+ndi).replace(0,np.nan)
    adx = dx.ewm(span=period,adjust=False).mean()
    return float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 25.0

def detect_local_structure(df, lookback_bars=48, tf_label="1H"):
    """
    Detect whether the local TF is trending or ranging.
    Used for Bear + Local Override (Layer 2 of v8.5).
    """
    if len(df) < lookback_bars + 10: return "ranging"
    recent = df.tail(lookback_bars)
    e8  = float(recent["ema8"].iloc[-1])
    e21 = float(recent["ema21"].iloc[-1])
    e55 = float(recent["ema55"].iloc[-1])
    bull_aligned = e8 > e21 > e55
    bear_aligned = e8 < e21 < e55
    atr_now  = float(recent["atr"].iloc[-1])
    atr_mean = float(recent["atr"].mean())
    atr_exp  = atr_now > atr_mean * 1.2
    atr_con  = atr_now < atr_mean * 0.85
    pr_pct   = (recent["high"].max() - recent["low"].min()) / recent["close"].mean()
    tight    = pr_pct < (0.05 if tf_label == "4H" else 0.03)
    rsi_osc  = (float(recent["rsi"].max()) - float(recent["rsi"].min())) > 25 and \
               35 < float(recent["rsi"].mean()) < 65
    e8s = recent["ema8"]; e21s = recent["ema21"]
    cross_up = (len(e8s) >= 4 and float(e8s.iloc[-1]) > float(e21s.iloc[-1]) and
                float(e8s.iloc[-3]) < float(e21s.iloc[-3]))
    cross_dn = (len(e8s) >= 4 and float(e8s.iloc[-1]) < float(e21s.iloc[-1]) and
                float(e8s.iloc[-3]) > float(e21s.iloc[-3]))
    if bear_aligned and atr_exp and not tight: return "bear"
    if bull_aligned and atr_exp and not tight: return "bull"
    if tight and rsi_osc and atr_con:          return "ranging"
    if cross_up and not atr_con:               return "recovering"
    if cross_dn and not atr_con:               return "bear"
    if tight or (atr_con and rsi_osc):         return "ranging"
    return "ranging"

def detect_regime(df, df_4h_ext=None, df_1d_ext=None):
    """
    5-signal voting system (v8.5):
      S1: 1D EMA50/200 cross + slope (weight 2 + slope bonus)
      S2: Price vs 1D EMAs (weight 1)
      S3: 4H EMA21/55 bias (weight 1)
      S4: HH/HL price structure on 1D (weight 1)
      S5: ADX trend strength (weight 1–2)
    Returns: regime string + list of reasons
    """
    bull_votes = 0; bear_votes = 0; ranging_votes = 0
    reasons = []

    # S1: 1D EMA50 / EMA200 cross (golden/death cross) — weight 2
    d_ema200_slope = df.attrs.get("d_ema200_slope", 0.0)
    d_ema50_slope  = df.attrs.get("d_ema50_slope",  0.0)
    d_ema50_price  = df.attrs.get("d_ema50_price",  0.0)
    d_ema200_price = df.attrs.get("d_ema200_price", 0.0)
    if d_ema50_price > 0 and d_ema200_price > 0:
        if d_ema50_price > d_ema200_price:
            bull_votes += 2
            reasons.append(f"[S1] ✅ 1D EMA50 > EMA200 (golden cross) → +2 bull")
            if d_ema200_slope > 0.002:
                bull_votes += 1
                reasons.append(f"[S1+] ✅ 1D EMA200 sloping UP ({d_ema200_slope:.3%}) → +1 bull")
        else:
            bear_votes += 2
            reasons.append(f"[S1] ❌ 1D EMA50 < EMA200 (death cross) → +2 bear")
            if d_ema200_slope < -0.002:
                bear_votes += 1
                reasons.append(f"[S1+] ❌ 1D EMA200 sloping DOWN ({d_ema200_slope:.3%}) → +1 bear")
    else:
        # Fallback: use 1H EMA200 slope
        if len(df) >= 21:
            slope = float((df["ema200"].iloc[-1] - df["ema200"].iloc[-21]) / df["ema200"].iloc[-21])
            if slope > 0.002:   bull_votes += 2; reasons.append(f"[S1] ✅ EMA200 rising ({slope:.3%}) → +2 bull")
            elif slope < -0.002: bear_votes += 2; reasons.append(f"[S1] ❌ EMA200 falling ({slope:.3%}) → +2 bear")
            else:               ranging_votes += 2; reasons.append(f"[S1] ↔️ EMA200 flat ({slope:.3%}) → +2 ranging")

    # S2: Price vs 1D EMA50 / EMA200
    price_now  = float(df["close"].iloc[-1])
    ema50_now  = float(df["ema50"].iloc[-1])
    ema200_now = float(df["ema200"].iloc[-1])
    d_above_50  = price_now > d_ema50_price  if d_ema50_price  > 0 else price_now > ema50_now
    d_above_200 = price_now > d_ema200_price if d_ema200_price > 0 else price_now > ema200_now
    if d_above_50 and d_above_200:
        bull_votes += 1
        reasons.append(f"[S2] ✅ Price above both 1D EMA50 & EMA200 → +1 bull")
    elif not d_above_50 and not d_above_200:
        bear_votes += 1
        reasons.append(f"[S2] ❌ Price below both 1D EMA50 & EMA200 → +1 bear")
    else:
        ranging_votes += 1
        reasons.append(f"[S2] ↔️ Price between 1D EMA50 & EMA200 → +1 ranging")

    # S3: 4H EMA21/55 bias
    if df_4h_ext is not None and not df_4h_ext.empty and len(df_4h_ext) >= 55:
        e21_4h = _ema(df_4h_ext["close"], 21); e55_4h = _ema(df_4h_ext["close"], 55)
        if float(e21_4h.iloc[-1]) > float(e55_4h.iloc[-1]):
            bull_votes += 1; reasons.append(f"[S3] ✅ 4H EMA21 > EMA55 (4H uptrend) → +1 bull")
        else:
            bear_votes += 1; reasons.append(f"[S3] ❌ 4H EMA21 < EMA55 (4H downtrend) → +1 bear")
    else:
        # Fallback from df attrs
        h4_golden = bool(df["bias_bullish_4h"].iloc[-1]) if "bias_bullish_4h" in df.columns else d_above_50
        if h4_golden: bull_votes += 1; reasons.append(f"[S3] ✅ 4H bias bullish (fallback) → +1 bull")
        else:          bear_votes += 1; reasons.append(f"[S3] ❌ 4H bias bearish (fallback) → +1 bear")

    # S4: HH/HL price structure on 1D
    structure = price_structure(df_1d_ext if df_1d_ext is not None else df, lookback=30)
    if structure == "bull":
        bull_votes += 1; reasons.append(f"[S4] ✅ 1D structure: HH+HL → +1 bull")
    elif structure == "bear":
        bear_votes += 1; reasons.append(f"[S4] ❌ 1D structure: LH+LL → +1 bear")
    else:
        ranging_votes += 1; reasons.append(f"[S4] ↔️ 1D structure: mixed/ranging → +1 ranging")

    # S5: ADX trend strength
    adx = adx_strength(df_1d_ext if df_1d_ext is not None else df, period=14)
    if adx > 25:
        if bull_votes > bear_votes:
            bull_votes += 1; reasons.append(f"[S5] ✅ ADX={adx:.1f} (strong trend) → confirms bull → +1 bull")
        elif bear_votes > bull_votes:
            bear_votes += 1; reasons.append(f"[S5] ✅ ADX={adx:.1f} (strong trend) → confirms bear → +1 bear")
        else:
            reasons.append(f"[S5] ➡️ ADX={adx:.1f} (strong trend) → votes tied, no extra")
    elif adx < 20:
        ranging_votes += 2; reasons.append(f"[S5] ↔️ ADX={adx:.1f} (no trend) → +2 ranging")
    else:
        ranging_votes += 1; reasons.append(f"[S5] ↔️ ADX={adx:.1f} (weak trend) → +1 ranging")

    # VP context
    poc_v = float(df.get("poc", pd.Series([0])).iloc[-1]) if "poc" in df.columns else 0
    vah_v = float(df.get("vah", pd.Series([0])).iloc[-1]) if "vah" in df.columns else 0
    if vah_v > 0:
        if price_now > vah_v:
            reasons.append(f"[VP] ✅ Price ${price_now:,.0f} > VAH ${vah_v:,.0f} (bullish context)")
        else:
            reasons.append(f"[VP] ❌ Price ${price_now:,.0f} < VAH ${vah_v:,.0f} (bearish context)")

    reasons.append(f"📊 Votes → Bull: {bull_votes} | Bear: {bear_votes} | Ranging: {ranging_votes}")

    # Classify
    if bull_votes >= 4:
        regime = "STRONG_BULL"
    elif bull_votes >= 2 and bull_votes > bear_votes:
        regime = "WEAK_BULL"
    elif bear_votes >= 2 and bear_votes > bull_votes:
        regime = "BEAR"
    else:
        regime = "RANGING"

    reasons.append(f"🏷️  Regime → {regime}")
    return regime, reasons

def get_regime_config(regime):
    base = dict(REGIME_CONFIGS.get(regime, REGIME_CONFIGS["WEAK_BULL"]))
    w = dict(CAT_WEIGHTS)
    w.update(base.get("weights_override", {}))
    base["weights"] = w
    return base


# ══════════════════════════════════════════════════════════════════
# SCORING ENGINE v8.5 — Regime-aware + Bear Local Override
# ══════════════════════════════════════════════════════════════════

def compute_scores_regime(df, regime, min_cats, trend_req,
                           h1_structure=None, h4_structure=None):
    rcfg    = get_regime_config(regime)
    w       = rcfg["weights"]
    enl     = rcfg["enable_longs"]
    ens     = rcfg["enable_shorts"]
    min_sl  = rcfg["min_score_long"]
    min_ss  = rcfg["min_score_short"]

    tl = pd.Series(0.0, index=df.index); ts = pd.Series(0.0, index=df.index)
    zl = pd.Series(0.0, index=df.index); zs = pd.Series(0.0, index=df.index)
    ml = pd.Series(0.0, index=df.index); ms = pd.Series(0.0, index=df.index)
    pl = pd.Series(0.0, index=df.index); ps = pd.Series(0.0, index=df.index)

    # TREND
    tl += df["bias_bullish_4h"].astype(float) * w.get("4h_bias", 3.0)
    ts += df["bias_bearish_4h"].astype(float) * w.get("4h_bias", 3.0)
    bull_e = (df["ema8"]>df["ema21"]) & (df["ema21"]>df["ema55"])
    bear_e = (df["ema8"]<df["ema21"]) & (df["ema21"]<df["ema55"])
    tl += bull_e.astype(float) * w.get("ema_ribbon", 2.0)
    ts += bear_e.astype(float) * w.get("ema_ribbon", 2.0)
    tl += (df["close"] > df["ema50"]).astype(float)  * w.get("ema50",  2.5)
    ts += (df["close"] < df["ema50"]).astype(float)  * w.get("ema50",  2.5)
    tl += (df["close"] > df["ema200"]).astype(float) * w.get("ema200", 3.0)
    ts += (df["close"] < df["ema200"]).astype(float) * w.get("ema200", 3.0)
    tl += df["bos_up"].astype(float)   * w.get("bos", 2.5)
    ts += df["bos_down"].astype(float) * w.get("bos", 2.5)
    # NEW: VAH bias
    tl += df["above_vah"].astype(float) * w.get("vah_bias", 2.0)
    ts += df["below_vah"].astype(float) * w.get("vah_bias", 2.0)

    # ZONE
    zl += df["near_support"].astype(float)    * w.get("support_resistance", 2.5)
    zs += df["near_resistance"].astype(float) * w.get("support_resistance", 2.5)
    zl += df["near_fib618"].astype(float) * w.get("fib618", 2.0)
    zs += df["near_fib618"].astype(float) * w.get("fib618", 2.0)
    zl += df["near_fib50"].astype(float)  * w.get("fib50",  1.5)
    zs += df["near_fib50"].astype(float)  * w.get("fib50",  1.5)
    zl += df["near_fib382"].astype(float) * w.get("fib382", 1.5)
    zs += df["near_fib382"].astype(float) * w.get("fib382", 1.5)
    zl += df["in_bullish_ob"].astype(float) * w.get("order_blocks", 2.5)
    zs += df["in_bearish_ob"].astype(float) * w.get("order_blocks", 2.5)
    zl += (df["close"] < df["vwap"]).astype(float) * w.get("vwap", 1.0)
    zs += (df["close"] > df["vwap"]).astype(float) * w.get("vwap", 1.0)
    # NEW: Volume Profile zones
    zl += df["near_poc"].astype(float) * w.get("volume_profile", 3.0)
    zl += df["near_val"].astype(float) * w.get("volume_profile", 3.0)
    zs += df["near_vah"].astype(float) * w.get("volume_profile", 3.0)
    # NEW: Anchored VWAP
    zl += df["avwap_bull_conf"].astype(float) * w.get("avwap_anchor", 2.5)
    zs += df["avwap_bear_conf"].astype(float) * w.get("avwap_anchor", 2.5)
    # NEW: Fib extensions as short zones
    zs += df["near_fib100"].astype(float)  * w.get("fib_ext", 1.5)
    zs += df["near_fib1618"].astype(float) * w.get("fib_ext", 1.5)

    # MOMENTUM
    ml += (df["rsi"] < 40).astype(float) * w.get("rsi", 2.0)
    ms += (df["rsi"] > 60).astype(float) * w.get("rsi", 2.0)
    ml += df["rsi_trend_bull_zone"].astype(float) * (w.get("rsi", 2.0) * 0.5)
    ms += df["rsi_trend_bear_zone"].astype(float) * (w.get("rsi", 2.0) * 0.5)
    ml += df["rsi_bull_div"].astype(float)        * w.get("rsi_div", 2.5)
    ms += df["rsi_bear_div"].astype(float)        * w.get("rsi_div", 2.5)
    ml += df["rsi_hidden_bull_div"].astype(float) * w.get("rsi_hidden_div", 2.0)
    ms += df["rsi_hidden_bear_div"].astype(float) * w.get("rsi_hidden_div", 2.0)
    cu = df["cvd"].diff() > 0; cd = df["cvd"].diff() < 0
    ml += cu.astype(float) * w.get("cvd", 2.0); ms += cd.astype(float) * w.get("cvd", 2.0)
    ml += df["cvd_bull_div"].astype(float) * w.get("cvd_div", 2.5)
    ms += df["cvd_bear_div"].astype(float) * w.get("cvd_div", 2.5)
    ml += df["real_buying"].astype(float)  * w.get("real_buying", 2.0)
    ms += df["real_selling"].astype(float) * w.get("real_buying", 2.0)
    ml += df["vol_confirm"].astype(float)  * w.get("volume", 0.5)
    ms += df["vol_confirm"].astype(float)  * w.get("volume", 0.5)
    # NEW: MFI
    ml += df["mfi_bull_div"].astype(float)    * w.get("mfi", 2.0)
    ms += df["mfi_bear_div"].astype(float)    * w.get("mfi", 2.0)
    ml += df["mfi_hidden_bull"].astype(float) * w.get("mfi", 2.0)
    ms += df["mfi_hidden_bear"].astype(float) * w.get("mfi", 2.0)
    ml += df["mfi_oversold"].astype(float)    * w.get("mfi", 2.0) * 0.5
    ms += df["mfi_overbought"].astype(float)  * w.get("mfi", 2.0) * 0.5
    # NEW: Stochastic
    ml += df["stoch_oversold"].astype(float)   * w.get("stochastic", 1.5)
    ms += df["stoch_overbought"].astype(float) * w.get("stochastic", 1.5)
    ml += df["stoch_bull_cross"].astype(float) * w.get("stochastic", 1.5)
    ms += df["stoch_bear_cross"].astype(float) * w.get("stochastic", 1.5)
    # NEW: Bull Flag
    ml += df["bull_flag"].astype(float) * w.get("bull_flag", 2.0)

    # POSITIONING
    pl += df["choch"].astype(float)               * w.get("choch",        1.0)
    ps += df["choch"].astype(float)               * w.get("choch",        1.0)
    pl += df["major_long_liq"].astype(float)      * w.get("liquidations", 2.0)
    ps += df["major_short_liq"].astype(float)     * w.get("liquidations", 2.0)
    pl += df["funding_extreme_short"].astype(float)* w.get("funding",     1.5)
    ps += df["funding_extreme_long"].astype(float) * w.get("funding",     1.5)
    pl += df["oi_confirm_long"].astype(float)      * w.get("oi",          1.5)
    ps += df["oi_confirm_short"].astype(float)     * w.get("oi",          1.5)

    df["long_score"]  = tl + zl + ml + pl
    df["short_score"] = ts + zs + ms + ps

    def cat_gate(tl_, zl_, ml_, pl_, min_score, enable):
        if not enable: return pd.Series(False, index=tl_.index)
        cats = (tl_>0).astype(int)+(zl_>0).astype(int)+(ml_>0).astype(int)+(pl_>0).astype(int)
        s_ok = (tl_+zl_+ml_+pl_) >= min_score
        c_ok = cats >= min_cats
        t_ok = (tl_>0) if trend_req else pd.Series(True, index=tl_.index)
        return s_ok & c_ok & t_ok

    long_sig  = cat_gate(tl, zl, ml, pl, min_sl, enl)
    short_sig = cat_gate(ts, zs, ms, ps, min_ss, ens)

    # ── Bear + Local Ranging Override (Layer 2 of v8.5) ──────────
    h1r = h1_structure in ("ranging", "recovering")
    h4r = h4_structure in ("ranging", "recovering")

    mr_mod  = (df["rsi"] < 40) | df["stoch_oversold"] | df["mfi_oversold"]
    mr_str  = (df["rsi"] < 35) | (df["stoch_oversold"] & (df["rsi"] < 40))
    at_sup  = df["near_val"] | df["near_support"] | df["in_bullish_ob"] | df["near_fib618"] | df["near_poc"]
    rev1    = df["rsi_bull_div"]|df["mfi_bull_div"]|df["stoch_bull_cross"]|df["cvd_bull_div"]
    rev2    = rev1 & (df["rsi_bull_div"] | df["mfi_bull_div"])
    no_tr   = ~(df["bos_up"] | df["bull_flag"] | df["bias_bullish_4h"])
    str_res = (df["near_resistance"]|df["near_vah"]|df["near_fib618"]) & \
              (df["cvd_bear_div"] | df["rsi_bear_div"])

    if regime == "BEAR" and h4r and h1r:
        # Level 3: both TFs ranging → MR longs allowed, shorts only at extreme resistance
        short_sig = (df["rsi"] > 65) & str_res & short_sig
        long_sig  = mr_mod & at_sup & rev1 & no_tr & ((zl + ml) >= 4.0)
        df.attrs["bear_override"] = "LEVEL3_BOTH_RANGING"
    elif regime == "BEAR" and h4r:
        # Level 2: 4H ranging, 1H still bear → selective MR longs
        short_sig = (df["rsi"] > 65) & str_res & short_sig
        long_sig  = mr_mod & at_sup & rev2 & no_tr & ((zl + ml) >= 4.5)
        df.attrs["bear_override"] = "LEVEL2_4H_RANGING"
    elif regime == "BEAR" and h1r:
        # Level 1: 1H ranging only, 4H still bear → very strict MR longs
        h1ba = (df["ema8"] < df["ema21"]) & (df["ema21"] < df["ema55"])
        short_sig = (h1ba | ((df["rsi"] > 68) & str_res)) & short_sig
        long_sig  = mr_str & at_sup & rev2 & no_tr & ((zl + ml) >= 5.0)
        df.attrs["bear_override"] = "LEVEL1_1H_ONLY"
    else:
        df.attrs["bear_override"] = "NONE"

    df.attrs["h1_structure"] = h1_structure or "unknown"
    df.attrs["h4_structure"] = h4_structure or "unknown"
    df["long_signal"]  = long_sig
    df["short_signal"] = short_sig
    df["trend_l"] = tl; df["trend_s"] = ts
    df["zone_l"]  = zl; df["zone_s"]  = zs
    df["mom_l"]   = ml; df["mom_s"]   = ms
    df["pos_l"]   = pl; df["pos_s"]   = ps
    return df


# ══════════════════════════════════════════════════════════════════
# MAIN SIGNAL FUNCTION
# ══════════════════════════════════════════════════════════════════

def get_signal():
    """Fetch + compute all v8.5 indicators + regime + signal. Returns rich dict."""
    df_1h  = fetch_ohlcv("hour", 1, days_back=40)
    df_4h  = fetch_ohlcv("hour", 4, days_back=90)
    df_1d  = fetch_ohlcv("day",  1, days_back=260)
    liq_df = fetch_liquidations(max_days=3)
    fund_df= fetch_funding()
    oi_df  = fetch_oi()
    price  = get_live_price()

    if df_1h.empty:
        return {"error": "Could not fetch OHLCV data"}

    df = compute_indicators(df_1h, df_4h, df_1d, liq_df, fund_df, oi_df)
    regime, regime_reasons = detect_regime(df, df_4h_ext=df_4h, df_1d_ext=df_1d)
    rcfg = get_regime_config(regime)

    if not df_4h.empty:
        df_4h_ind = df_4h.copy()
        df_4h_ind["ema8"]  = _ema(df_4h_ind["close"], 8)
        df_4h_ind["ema21"] = _ema(df_4h_ind["close"], 21)
        df_4h_ind["ema55"] = _ema(df_4h_ind["close"], 55)
        df_4h_ind["rsi"]   = _rsi(df_4h_ind["close"])
        df_4h_ind["atr"]   = _atr(df_4h_ind)
        h4_str = detect_local_structure(df_4h_ind, lookback_bars=30, tf_label="4H")
    else:
        h4_str = "ranging"
    h1_str = detect_local_structure(df, lookback_bars=48, tf_label="1H")
    df     = compute_scores_regime(df, regime, MIN_CATS, TREND_REQ,
                                   h1_structure=h1_str, h4_structure=h4_str)

    row  = df.iloc[-1]
    ls   = float(row["long_score"]);  ss   = float(row["short_score"])
    lsig = bool(row["long_signal"]);  ssig = bool(row["short_signal"])
    direction = "LONG" if lsig else ("SHORT" if ssig else "NONE")

    atr_v    = float(row["atr"])
    rsi_v    = float(row["rsi"])
    mfi_v    = float(row.get("mfi", 50))
    stoch_v  = float(row.get("stoch_k", 50))
    poc_v    = float(row.get("poc", 0))
    vah_v    = float(row.get("vah", 0))
    val_v    = float(row.get("val", 0))
    ema50_v  = float(row.get("ema50",  0))
    ema200_v = float(row.get("ema200", 0))
    is_up    = price > ema200_v
    sl_mult  = rcfg["atr_sl_mult"]
    tp_mult  = rcfg["atr_tp_mult"]

    entry_plan = None
    if direction in ("LONG", "SHORT"):
        entry   = price
        sl      = entry - atr_v * sl_mult if direction == "LONG" else entry + atr_v * sl_mult
        tp      = entry + atr_v * tp_mult if direction == "LONG" else entry - atr_v * tp_mult
        rr      = abs(tp - entry) / max(abs(sl - entry), 1)
        entry_plan = {"entry": round(entry,2), "stop_loss": round(sl,2),
                      "take_profit": round(tp,2), "rr": round(rr,1)}

    indicator_map = [
        ("4H Bullish",         bool(row.get("bias_bullish_4h")),         False),
        ("4H Bearish",         False,                                     bool(row.get("bias_bearish_4h"))),
        ("EMA Ribbon Bull",    bool(row.get("ema8",0)>row.get("ema21",0)>row.get("ema55",0)), False),
        ("EMA Ribbon Bear",    False, bool(row.get("ema8",0)<row.get("ema21",0)<row.get("ema55",0))),
        ("Above EMA50",        bool(row["close"]>row.get("ema50", row["close"])), False),
        ("Below EMA50",        False, bool(row["close"]<row.get("ema50", row["close"]))),
        ("Above EMA200",       bool(row["close"]>row.get("ema200",row["close"])), False),
        ("Below EMA200",       False, bool(row["close"]<row.get("ema200",row["close"]))),
        ("BOS Up",             bool(row.get("bos_up")),                  False),
        ("BOS Down",           False,                                     bool(row.get("bos_down"))),
        ("Above VAH",          bool(row.get("above_vah")),               False),
        ("Below VAH",          False,                                     bool(row.get("below_vah"))),
        ("Near POC",           bool(row.get("near_poc")),                bool(row.get("near_poc"))),
        ("Near VAL",           bool(row.get("near_val")),                False),
        ("Near VAH Short",     False,                                     bool(row.get("near_vah"))),
        ("AVWAP Bull",         bool(row.get("avwap_bull_conf")),         False),
        ("AVWAP Bear",         False,                                     bool(row.get("avwap_bear_conf"))),
        ("Near Support",       bool(row.get("near_support")),            False),
        ("Near Resistance",    False,                                     bool(row.get("near_resistance"))),
        ("Fib 0.618",          bool(row.get("near_fib618")),             bool(row.get("near_fib618"))),
        ("Fib 0.500",          bool(row.get("near_fib50")),              bool(row.get("near_fib50"))),
        ("Fib 0.382",          bool(row.get("near_fib382")),             bool(row.get("near_fib382"))),
        ("Fib 1.618",          False,                                     bool(row.get("near_fib1618"))),
        ("Bullish OB",         bool(row.get("in_bullish_ob")),           False),
        ("Bearish OB",         False,                                     bool(row.get("in_bearish_ob"))),
        ("Below VWAP",         bool(row["close"] < row["vwap"]),         False),
        ("Above VWAP",         False,                                     bool(row["close"] > row["vwap"])),
        (f"RSI {rsi_v:.0f}",   bool(rsi_v<40 or (is_up and 30<=rsi_v<=50)),
                                bool(rsi_v>60 or (not is_up and 50<=rsi_v<=70))),
        ("RSI Bull Div",       bool(row.get("rsi_bull_div")),            False),
        ("RSI Bear Div",       False,                                     bool(row.get("rsi_bear_div"))),
        ("RSI Hidden Bull",    bool(row.get("rsi_hidden_bull_div")),     False),
        ("RSI Hidden Bear",    False,                                     bool(row.get("rsi_hidden_bear_div"))),
        (f"MFI {mfi_v:.0f}",   bool(row.get("mfi_bull_div") or row.get("mfi_hidden_bull") or row.get("mfi_oversold")),
                                bool(row.get("mfi_bear_div") or row.get("mfi_hidden_bear") or row.get("mfi_overbought"))),
        (f"Stoch {stoch_v:.0f}",bool(row.get("stoch_oversold") or row.get("stoch_bull_cross")),
                                 bool(row.get("stoch_overbought") or row.get("stoch_bear_cross"))),
        ("Bull Flag",          bool(row.get("bull_flag")),               False),
        ("CVD Bull Div",       bool(row.get("cvd_bull_div")),            False),
        ("CVD Bear Div",       False,                                     bool(row.get("cvd_bear_div"))),
        ("Real Buying",        bool(row.get("real_buying")),             False),
        ("Real Selling",       False,                                     bool(row.get("real_selling"))),
        ("Vol Confirm",        bool(row.get("vol_confirm")),             bool(row.get("vol_confirm"))),
        ("Long Liq",           bool(row.get("major_long_liq")),         False),
        ("Short Liq",          False,                                     bool(row.get("major_short_liq"))),
        ("Funding Short",      bool(row.get("funding_extreme_short")),   False),
        ("Funding Long",       False,                                     bool(row.get("funding_extreme_long"))),
        ("OI Long",            bool(row.get("oi_confirm_long")),         False),
        ("OI Short",           False,                                     bool(row.get("oi_confirm_short"))),
    ]
    reasons_long  = [n for n,fl,_ in indicator_map if fl]
    reasons_short = [n for n,_,fs in indicator_map if fs]

    return {
        "timestamp":      datetime.utcnow().isoformat(),
        "price":          round(price, 2),
        "direction":      direction,
        "regime":         regime,
        "regime_reasons": regime_reasons,
        "regime_config":  {k:v for k,v in rcfg.items() if k != "weights"},
        "h1_structure":   h1_str,
        "h4_structure":   h4_str,
        "bear_override":  df.attrs.get("bear_override", "NONE"),
        "long_score":     round(ls, 2),
        "short_score":    round(ss, 2),
        "categories": {
            "trend":       {"long": round(float(row["trend_l"]),2), "short": round(float(row["trend_s"]),2)},
            "zone":        {"long": round(float(row["zone_l"]), 2), "short": round(float(row["zone_s"]), 2)},
            "momentum":    {"long": round(float(row["mom_l"]),  2), "short": round(float(row["mom_s"]),  2)},
            "positioning": {"long": round(float(row["pos_l"]),  2), "short": round(float(row["pos_s"]),  2)},
        },
        "volume_profile": {"poc": round(poc_v,2), "vah": round(vah_v,2), "val": round(val_v,2)},
        "reasons_long":   reasons_long,
        "reasons_short":  reasons_short,
        "entry_plan":     entry_plan,
        "indicators": {
            "rsi":         round(rsi_v,2),
            "mfi":         round(mfi_v,2),
            "stoch_k":     round(stoch_v,2),
            "atr":         round(atr_v,2),
            "ema50":       round(ema50_v,2),
            "ema200":      round(ema200_v,2),
            "vwap":        round(float(row["vwap"]),2),
            "vol_ratio":   round(float(row.get("vol_ratio",1)),2),
            "above_vah":   bool(row.get("above_vah")),
            "bull_flag":   bool(row.get("bull_flag")),
            "real_buying": bool(row.get("real_buying")),
            "real_selling":bool(row.get("real_selling")),
        },
        "df": df,
    }


# ══════════════════════════════════════════════════════════════════
# MEMORY & LEARNING
# ══════════════════════════════════════════════════════════════════

def load_memory():
    if MEMORY_FILE.exists():
        try:
            with open(MEMORY_FILE) as f: return json.load(f)
        except: pass
    return []

def save_to_memory(result: dict):
    mem = load_memory(); mem.append(result)
    with open(MEMORY_FILE, "w") as f: json.dump(mem, f, indent=2, default=str)
    return len(mem)

def get_learning_summary():
    mem = load_memory()
    if not mem: return "📚 No backtest history yet. Run /backtest first."
    last5    = mem[-5:]
    avg_ret  = sum(r.get("total_return",0)  for r in last5) / len(last5)
    avg_sha  = sum(r.get("sharpe_ratio",0)  for r in last5) / len(last5)
    avg_wr   = sum(r.get("win_rate",0)      for r in last5) / len(last5)
    best_run = max(mem, key=lambda r: r.get("sharpe_ratio", 0))
    lines    = [
        f"🧠 Learning Report ({len(mem)} total runs)",
        f"",
        f"Last 5 avg → Return: {avg_ret:.1%}  Sharpe: {avg_sha:.3f}  WR: {avg_wr:.1%}",
        f"",
        f"🏆 Best run: {best_run.get('period','')} {best_run.get('timeframe','')} "
        f"| Sharpe {best_run.get('sharpe_ratio',0):.3f} "
        f"| Return {best_run.get('total_return',0):.1%}",
        f"   Regime: {best_run.get('regime','')} | "
        f"Score: {best_run.get('config',{}).get('min_score','')}",
    ]
    regime_perf = {}
    for r in mem:
        rg = r.get("regime","UNKNOWN")
        if rg not in regime_perf: regime_perf[rg] = []
        regime_perf[rg].append(r.get("sharpe_ratio",0))
    lines.append(f"")
    lines.append(f"📊 Avg Sharpe by Regime:")
    for rg, vals in sorted(regime_perf.items(), key=lambda x: -sum(x[1])/max(1,len(x[1]))):
        lines.append(f"   {rg:<14} → {sum(vals)/len(vals):.3f}  ({len(vals)} runs)")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# GOOGLE SHEETS LOGGING
# ══════════════════════════════════════════════════════════════════

def log_signal_to_sheets(sig: dict):
    if not SHEETS_URL: return
    try:
        payload = {
            "timestamp": sig.get("timestamp",""),
            "direction": sig.get("direction",""),
            "price":     sig.get("price",0),
            "regime":    sig.get("regime",""),
            "long_score":sig.get("long_score",0),
            "short_score":sig.get("short_score",0),
        }
        requests.post(SHEETS_URL, json=payload, timeout=10)
    except: pass

def log_trade_to_sheets(trade: dict):
    if not SHEETS_URL: return
    try: requests.post(SHEETS_URL, json=trade, timeout=10)
    except: pass
