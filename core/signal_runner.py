"""
signal_runner.py — BTC Strategy Core Engine v8.5
Regime-Aware + 5-Signal Voting + 1H+4H Local Structure Override
Shared by: Telegram bot, Streamlit dashboard, GitHub Actions cron
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
        "description": "WEAK BULL — Selective longs | Reversal setups", "emoji": "📈",
    },
    "BEAR": {
        "enable_longs": True, "enable_shorts": True,
        "min_score_long": 8.0, "min_score_short": 4.0,
        "atr_sl_mult": 2.5, "atr_tp_mult": 5.0,
        "weights_override": {
            "4h_bias": 3.5, "ema200": 4.0, "vah_bias": 3.5, "bos": 3.0,
            "rsi_hidden_div": 3.0, "rsi_div": 2.0, "cvd_div": 3.5, "real_buying": 1.0,
            "bull_flag": 0.5, "mfi": 2.5, "stochastic": 2.0, "volume_profile": 3.5,
            "avwap_anchor": 3.0, "liquidations": 2.5,
        },
        "description": "BEAR — Shorts primary | MR longs at extremes", "emoji": "🐻",
    },
    "RANGING": {
        "enable_longs": True, "enable_shorts": True,
        "min_score_long": 6.0, "min_score_short": 6.0,
        "atr_sl_mult": 1.5, "atr_tp_mult": 2.5,
        "weights_override": {
            "support_resistance": 4.0, "volume_profile": 4.0, "rsi_div": 3.5,
            "mfi": 3.0, "stochastic": 3.0, "cvd_div": 3.0, "avwap_anchor": 3.0,
            "bull_flag": 0.5, "bos": 0.5, "rsi_hidden_div": 1.0,
            "4h_bias": 1.5, "ema_ribbon": 1.0, "ema200": 2.0, "vah_bias": 1.5,
        },
        "description": "RANGING — Fade edges | Mean reversion to POC", "emoji": "↔️",
    },
}

PERIOD_DAYS = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "3Y": 1095}
BINANCE_SPOT_URL  = "https://api.binance.com/api/v3/klines"
BINANCE_FAPI_URL  = "https://fapi.binance.com/fapi/v1"
BINANCE_FDATA_URL = "https://fapi.binance.com/futures/data"
INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


# ══════════════════════════════════════════════════════════════════
# DATA FETCHERS
# ══════════════════════════════════════════════════════════════════

def fetch_ohlcv_binance(symbol="BTCUSDT", interval="1h", days_back=365):
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - int(days_back * 86_400_000)
    ms_step  = INTERVAL_MS.get(interval, 3_600_000)
    all_data, cur = [], start_ms
    while cur < end_ms:
        try:
            r = requests.get(BINANCE_SPOT_URL, params={
                "symbol": symbol, "interval": interval,
                "startTime": cur, "endTime": end_ms, "limit": 1000,
            }, timeout=20)
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

def fetch_ohlcv(unit="hour", aggregate=1, days_back=365):
    """Binance primary, CryptoCompare fallback"""
    interval_map = {("hour",1): "1h", ("hour",4): "4h", ("day",1): "1d"}
    interval = interval_map.get((unit, aggregate), "1h")
    df = fetch_ohlcv_binance("BTCUSDT", interval, days_back)
    if not df.empty: return df
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
            if len(all_orders) >= 1000: break
        except: break
        end_ms = start_ms
        time.sleep(0.08)
    if not all_orders: return pd.DataFrame()
    df = pd.DataFrame(all_orders[:1000])
    df["datetime"] = pd.to_datetime(df["time"], unit="ms")
    df = df.set_index("datetime").sort_index()
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["qty"]   = pd.to_numeric(df["executedQty"], errors="coerce")
    df["liq_usd"]       = df["price"] * df["qty"]
    df["long_liq_usd"]  = df.apply(lambda r: r["liq_usd"] if r["side"]=="SELL" else 0.0, axis=1)
    df["short_liq_usd"] = df.apply(lambda r: r["liq_usd"] if r["side"]=="BUY"  else 0.0, axis=1)
    return df

def fetch_funding():
    try:
        r = requests.get(f"{BINANCE_FAPI_URL}/fundingRate",
                         params={"symbol":"BTCUSDT","limit":1000}, timeout=15)
        raw = r.json()
        if not isinstance(raw, list) or not raw: return pd.DataFrame()
        df  = pd.DataFrame(raw)
        tc  = next((c for c in ["fundingTime","calcTime","time"] if c in df.columns), None)
        if not tc: return pd.DataFrame()
        df["datetime"]     = pd.to_datetime(pd.to_numeric(df[tc], errors="coerce"), unit="ms")
        df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
        return df.dropna(subset=["datetime"]).set_index("datetime").sort_index()[["funding_rate"]].dropna()
    except: return pd.DataFrame()

def fetch_oi():
    try:
        r = requests.get(f"{BINANCE_FDATA_URL}/openInterestHist",
                         params={"symbol":"BTCUSDT","period":"1h","limit":500}, timeout=15)
        raw = r.json()
        if not isinstance(raw, list) or not raw: return pd.DataFrame()
        df  = pd.DataFrame(raw)
        tc  = next((c for c in df.columns if "time" in c.lower()), None)
        if not tc: return pd.DataFrame()
        df["datetime"] = pd.to_datetime(pd.to_numeric(df[tc], errors="coerce"), unit="ms")
        df["oi"]       = pd.to_numeric(df["sumOpenInterest"], errors="coerce")
        return df.dropna(subset=["datetime"]).set_index("datetime").sort_index()[["oi"]].dropna()
    except: return pd.DataFrame()

def get_live_price():
    try:
        r = requests.get("https://min-api.cryptocompare.com/data/price",
                         params={"fsym":"BTC","tsyms":"USD"}, timeout=5)
        return float(r.json().get("USD", 0))
    except: return 0.0


# ══════════════════════════════════════════════════════════════════
# MATH HELPERS
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
# NEW v8.5 INDICATOR HELPERS
# ══════════════════════════════════════════════════════════════════

def compute_volume_profile(df, bins=50, lookback=200):
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
    if anchor_pos >= len(df) or anchor_pos < 0:
        return pd.Series(np.nan, index=df.index)
    sl = df.iloc[anchor_pos:].copy()
    tp = (sl["high"] + sl["low"] + sl["close"]) / 3
    av = (tp * sl["volume"]).cumsum() / sl["volume"].cumsum()
    result = pd.Series(np.nan, index=df.index)
    result.iloc[anchor_pos:] = av.values
    return result

def detect_bull_flag(df, lookback=20, flag_bars=8):
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

    # EMA50 + EMA200 computed on 1D, mapped to 1H via ffill
    if not df_1d.empty and len(df_1d) >= 50:
        d_ema50  = _ema(df_1d["close"], 50)
        d_ema200 = _ema(df_1d["close"], 200)
        df["ema50"]  = d_ema50.reindex(df.index, method="ffill")
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
        df.attrs["d_ema200_slope"] = 0.0; df.attrs["d_ema50_slope"] = 0.0
        df.attrs["d_ema50_price"]  = float(df["ema50"].iloc[-1])
        df.attrs["d_ema200_price"] = float(df["ema200"].iloc[-1])

    # Standard VWAP
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
    df["real_buying"]  = cr & pu & df["vol_confirm"]
    df["real_selling"] = cf & pd_ & df["vol_confirm"]

    # MFI + divergence
    df["mfi"] = _mfi(df)
    lb = 10
    p_ll = df["close"] < df["close"].shift(lb); p_hh = df["close"] > df["close"].shift(lb)
    m_hl = df["mfi"]   > df["mfi"].shift(lb);   m_lh = df["mfi"]   < df["mfi"].shift(lb)
    df["mfi_bull_div"]    = p_ll & m_hl & (df["mfi"] < 40)
    df["mfi_bear_div"]    = p_hh & m_lh & (df["mfi"] > 60)
    df["mfi_hidden_bull"] = (~p_ll) & (~m_hl) & (df["mfi"] < 50)
    df["mfi_hidden_bear"] = (~p_hh) & (~m_lh) & (df["mfi"] > 50)
    df["mfi_oversold"]    = df["mfi"] < 20
    df["mfi_overbought"]  = df["mfi"] > 80

    # Volume Profile (POC / VAH / VAL) — BH's primary framework
    poc, vah, val = compute_volume_profile(df)
    df["poc"] = poc; df["vah"] = vah; df["val"] = val
    safe_close = df["close"].replace(0, np.nan)
    df["near_poc"]  = (df["close"] - poc).abs() / safe_close < 0.008
    df["near_vah"]  = (df["close"] - vah).abs() / safe_close < 0.008
    df["near_val"]  = (df["close"] - val).abs() / safe_close < 0.008
    df["above_vah"] = df["close"] > vah
    df["below_vah"] = df["close"] < vah
    df.attrs.update({"vp_poc": round(poc,2), "vp_vah": round(vah,2), "vp_val": round(val,2)})

    # Anchored VWAP from recent swing high/low
    lb_av  = min(30, len(df)-1)
    sh_idx = df["high"].iloc[-lb_av:].idxmax()
    sl_idx = df["low"].iloc[-lb_av:].idxmin()
    sh_pos = df.index.get_loc(sh_idx); sl_pos = df.index.get_loc(sl_idx)
    df["avwap_high"] = anchored_vwap(df, sh_pos)
    df["avwap_low"]  = anchored_vwap(df, sl_pos)
    df["avwap_bull_conf"] = df["avwap_low"].notna() & ((df["close"]-df["avwap_low"]).abs()/safe_close < 0.01)
    df["avwap_bear_conf"] = df["avwap_high"].notna() & ((df["close"]-df["avwap_high"]).abs()/safe_close < 0.01)

    # Stochastic
    ll_k = df["low"].rolling(14).min(); hh_k = df["high"].rolling(14).max()
    stoch_k = 100 * (df["close"]-ll_k) / (hh_k-ll_k+1e-9); stoch_d = stoch_k.rolling(3).mean()
    df["stoch_k"] = stoch_k; df["stoch_d"] = stoch_d
    df["stoch_overbought"] = stoch_k > 80;  df["stoch_oversold"]   = stoch_k < 20
    df["stoch_bull_cross"] = (stoch_k > stoch_d) & (stoch_k.shift(1) <= stoch_d.shift(1))
    df["stoch_bear_cross"] = (stoch_k < stoch_d) & (stoch_k.shift(1) >= stoch_d.shift(1))

    # Bull Flag
    df["bull_flag"] = detect_bull_flag(df)

    # S/R (1H pivots)
    h_v, l_v, c_v2 = df["high"].values, df["low"].values, df["close"].values
    sw = 3; ph2 = []; pl2 = []
    for i in range(sw, len(df)-sw):
        if h_v[i] == max(h_v[i-sw:i+sw+1]): ph2.append(h_v[i])
        if l_v[i] == min(l_v[i-sw:i+sw+1]): pl2.append(l_v[i])
    cur2 = c_v2[-1]
    df["near_support"]    = pd.Series(False, index=df.index)
    df["near_resistance"] = pd.Series(False, index=df.index)
    for lvl in [v for v in pl2 if v < cur2]:
        df["near_support"]    |= (df["close"]-lvl).abs()/lvl < 0.005
    for lvl in [v for v in ph2 if v > cur2]:
        df["near_resistance"] |= (df["close"]-lvl).abs()/lvl < 0.005

    # Fibonacci retracements + extensions
    r100 = df.tail(100); sh2 = r100["high"].max(); sl2 = r100["low"].min(); diff2 = sh2-sl2
    down2 = r100["high"].idxmax() < r100["low"].idxmin()
    if down2:
        fib382,fib50,fib618 = sl2+diff2*0.382, sl2+diff2*0.500, sl2+diff2*0.618
        fib100,fib1618      = sl2+diff2*1.000, sl2+diff2*1.618
    else:
        fib382,fib50,fib618 = sh2-diff2*0.382, sh2-diff2*0.500, sh2-diff2*0.618
        fib100,fib1618      = sh2-diff2*1.000, sh2-diff2*1.618
    df["near_fib382"]  = (df["close"]-fib382).abs()/safe_close < 0.008
    df["near_fib50"]   = (df["close"]-fib50).abs()/safe_close  < 0.008
    df["near_fib618"]  = (df["close"]-fib618).abs()/safe_close < 0.008
    df["near_fib100"]  = (df["close"]-fib100).abs()/safe_close < 0.008
    df["near_fib1618"] = (df["close"]-fib1618).abs()/safe_close< 0.008
    df.attrs.update({
        "fib382_price": round(float(fib382),2), "fib50_price": round(float(fib50),2),
        "fib618_price": round(float(fib618),2), "fib100_price": round(float(fib100),2),
        "fib1618_price": round(float(fib1618),2),
    })

    # Order Blocks
    opens2, closes2 = df["open"].values, df["close"].values
    atr_v2 = _atr(df).values
    df["in_bullish_ob"] = pd.Series(False, index=df.index)
    df["in_bearish_ob"] = pd.Series(False, index=df.index)
    for i in range(2, len(df)-2):
        a = atr_v2[i]
        if np.isnan(a) or a == 0: continue
        if closes2[i] < opens2[i] and closes2[i+2]-closes2[i] > 2.0*a:
            df["in_bullish_ob"] |= (df["close"]>=min(opens2[i],closes2[i]))&(df["close"]<=max(opens2[i],closes2[i]))
        if closes2[i] > opens2[i] and closes2[i]-closes2[i+2] > 2.0*a:
            df["in_bearish_ob"] |= (df["close"]>=min(opens2[i],closes2[i]))&(df["close"]<=max(opens2[i],closes2[i]))

    # RSI divergence + hidden + trend zones
    rsi_v2 = df["rsi"].values; cv2 = df["close"].values
    rb=[False]*len(df); rr=[False]*len(df); rhb=[False]*len(df); rhr=[False]*len(df)
    for i in range(55, len(df)):
        p = i-5
        if np.isnan(rsi_v2[i]) or np.isnan(rsi_v2[p]): continue
        if cv2[i]<cv2[p] and rsi_v2[i]>rsi_v2[p] and rsi_v2[i]<40:        rb[i]=True
        if cv2[i]>cv2[p] and rsi_v2[i]<rsi_v2[p] and rsi_v2[i]>60:        rr[i]=True
        if cv2[i]>cv2[p] and rsi_v2[i]<rsi_v2[p] and 30<rsi_v2[i]<55:     rhb[i]=True
        if cv2[i]<cv2[p] and rsi_v2[i]>rsi_v2[p] and 45<rsi_v2[i]<70:     rhr[i]=True
    df["rsi_bull_div"]        = rb;  df["rsi_bear_div"]        = rr
    df["rsi_hidden_bull_div"] = rhb; df["rsi_hidden_bear_div"] = rhr
    is_up = df["close"] > df["ema200"]
    df["rsi_trend_bull_zone"] = is_up  & (df["rsi"]>=30) & (df["rsi"]<=50)
    df["rsi_trend_bear_zone"] = (~is_up) & (df["rsi"]>=50) & (df["rsi"]<=70)

    # BOS / CHoCH
    bu=[False]*len(df); bd=[False]*len(df); ch=[False]*len(df)
    tr2 = "neutral"; hv3, lv3 = df["high"].values, df["low"].values
    for i in range(5, len(df)-5):
        if hv3[i] == max(hv3[i-5:i+6]):
            if tr2 == "bullish":   bu[i] = True
            elif tr2 == "bearish": ch[i] = True
            tr2 = "bullish"
        if lv3[i] == min(lv3[i-5:i+6]):
            if tr2 == "bearish":   bd[i] = True
            elif tr2 == "bullish": ch[i] = True
            tr2 = "bearish"
    df["bos_up"] = bu; df["bos_down"] = bd; df["choch"] = ch

    # 4H Bias cross-checked with 1D EMA200
    if not df_4h.empty:
        e4_21 = _ema(df_4h["close"], 21); e4_55 = _ema(df_4h["close"], 55)
        e4_bull = (df_4h["close"] > e4_21) & (e4_21 > e4_55)
        e4_bear = (df_4h["close"] < e4_21) & (e4_21 < e4_55)
        e4_bull_1h = e4_bull.resample("1h").ffill().reindex(df.index, method="ffill").fillna(False)
        e4_bear_1h = e4_bear.resample("1h").ffill().reindex(df.index, method="ffill").fillna(False)
        above_d200 = df["close"] > df["ema200"]; below_d200 = ~above_d200
        df["bias_bullish_4h"]      = e4_bull_1h & above_d200
        df["bias_bearish_4h"]      = e4_bear_1h & below_d200
        df["bias_bullish_4h_weak"] = e4_bull_1h
        df["bias_bearish_4h_weak"] = e4_bear_1h
    else:
        for col in ["bias_bullish_4h","bias_bearish_4h","bias_bullish_4h_weak","bias_bearish_4h_weak"]:
            df[col] = False

    # Liquidations
    df["major_long_liq"] = False; df["major_short_liq"] = False
    if not liq_df.empty and "long_liq_usd" in liq_df.columns:
        lh = liq_df[["long_liq_usd","short_liq_usd"]].resample("1h").sum()
        df["major_long_liq"]  = (lh["long_liq_usd"]  > lh["long_liq_usd"].quantile(0.90)).reindex(df.index, fill_value=False)
        df["major_short_liq"] = (lh["short_liq_usd"] > lh["short_liq_usd"].quantile(0.90)).reindex(df.index, fill_value=False)

    # Funding
    df["funding_extreme_long"] = False; df["funding_extreme_short"] = False
    if not funding_df.empty:
        fr = funding_df["funding_rate"].resample("1h").ffill().reindex(df.index, method="ffill")
        df["funding_extreme_long"]  = fr >  0.0005
        df["funding_extreme_short"] = fr < -0.0001

    # OI
    df["oi_confirm_long"] = False; df["oi_confirm_short"] = False
    if not oi_df.empty:
        oi_s  = oi_df["oi"].resample("1h").last().reindex(df.index, method="ffill")
        oi_ch = oi_s.pct_change()
        df["oi_confirm_long"]  = (df["close"].pct_change() > 0) & (oi_ch > 0.005)
        df["oi_confirm_short"] = (df["close"].pct_change() < 0) & (oi_ch > 0.005)

    return df.dropna(subset=["ema200","rsi","atr"])


# ══════════════════════════════════════════════════════════════════
# REGIME DETECTION ENGINE v8.5
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
    if len(df) < lookback_bars + 10: return "ranging"
    recent = df.tail(lookback_bars)
    e8  = float(recent["ema8"].iloc[-1])
    e21 = float(recent["ema21"].iloc[-1])
    e55 = float(recent["ema55"].iloc[-1])
    bull_aligned = e8 > e21 > e55; bear_aligned = e8 < e21 < e55
    atr_now  = float(recent["atr"].iloc[-1]); atr_mean = float(recent["atr"].mean())
    atr_exp  = atr_now > atr_mean*1.2; atr_con = atr_now < atr_mean*0.85
    pr_pct   = (recent["high"].max()-recent["low"].min())/recent["close"].mean()
    tight    = pr_pct < (0.05 if tf_label=="4H" else 0.03)
    rsi_osc  = (float(recent["rsi"].max())-float(recent["rsi"].min()))>25 and 35<float(recent["rsi"].mean())<65
    e8s = recent["ema8"]; e21s = recent["ema21"]
    cross_up = len(e8s)>=4 and float(e8s.iloc[-1])>float(e21s.iloc[-1]) and float(e8s.iloc[-3])<float(e21s.iloc[-3])
    highs = recent["high"].values; lows = recent["low"].values
    ph, pl = [], []
    for i in range(2, len(highs)-2):
        if highs[i]==max(highs[i-2:i+3]): ph.append(highs[i])
        if lows[i] ==min(lows[i-2:i+3]):  pl.append(lows[i])
    hh_hl = len(ph)>=2 and len(pl)>=2 and ph[-1]>ph[-2] and pl[-1]>pl[-2]
    lh_ll = len(ph)>=2 and len(pl)>=2 and ph[-1]<ph[-2] and pl[-1]<pl[-2]
    if bull_aligned and atr_exp and hh_hl:  return "trending_bull"
    if bear_aligned and atr_exp and lh_ll:  return "trending_bear"
    if cross_up and not bear_aligned:        return "recovering"
    if bull_aligned and atr_exp:             return "trending_bull"
    if bear_aligned and atr_exp:             return "trending_bear"
    if not bull_aligned and not bear_aligned or tight or atr_con or rsi_osc: return "ranging"
    if bull_aligned: return "trending_bull"
    if bear_aligned: return "trending_bear"
    return "ranging"

def detect_regime(df, df_4h_ext=None, df_1d_ext=None):
    price     = float(df["close"].iloc[-1])
    ema50_1d  = float(df.attrs.get("d_ema50_price",  df["ema50"].iloc[-1]))
    ema200_1d = float(df.attrs.get("d_ema200_price", df["ema200"].iloc[-1]))
    slope_1d  = float(df.attrs.get("d_ema200_slope", 0.0))
    above_vah = bool(df["above_vah"].iloc[-1])
    vah       = float(df["vah"].iloc[-1]); val = float(df["val"].iloc[-1])
    reasons   = []
    bull_votes = 0; bear_votes = 0; ranging_votes = 0

    # S1: 1D EMA50/200 (weight 2)
    d_golden = ema50_1d > ema200_1d
    if d_golden:
        bull_votes += 2; reasons.append(f"[S1] ✅ GOLDEN CROSS EMA50>${ema50_1d:,.0f} > EMA200${ema200_1d:,.0f} → +2 bull")
    else:
        bear_votes += 2; reasons.append(f"[S1] ❌ DEATH CROSS EMA50${ema50_1d:,.0f} < EMA200${ema200_1d:,.0f} → +2 bear")
    if slope_1d > 0.003:   bull_votes += 1;   reasons.append(f"[S1+] EMA200 rising {slope_1d*100:+.2f}% → +1 bull")
    elif slope_1d < -0.003: bear_votes += 1;  reasons.append(f"[S1+] EMA200 falling {slope_1d*100:+.2f}% → +1 bear")
    else:                   ranging_votes += 1; reasons.append(f"[S1+] EMA200 flat → +1 ranging")

    # S2: Price vs 1D EMAs
    if price>ema50_1d and price>ema200_1d:
        bull_votes += 1; reasons.append("[S2] ✅ Price above both 1D EMAs → +1 bull")
    elif price<ema50_1d and price<ema200_1d:
        bear_votes += 1; reasons.append("[S2] ❌ Price below both 1D EMAs → +1 bear")
    else:
        ranging_votes += 1; reasons.append("[S2] ↔️ Price between 1D EMAs → +1 ranging")

    # S3: 4H EMA21/55
    if df_4h_ext is not None and not df_4h_ext.empty and len(df_4h_ext) >= 55:
        h4_e21 = float(df_4h_ext["close"].ewm(span=21,adjust=False).mean().iloc[-1])
        h4_e55 = float(df_4h_ext["close"].ewm(span=55,adjust=False).mean().iloc[-1])
        if h4_e21 > h4_e55:
            bull_votes += 1; reasons.append(f"[S3] ✅ 4H EMA21>${h4_e21:,.0f} > EMA55${h4_e55:,.0f} → +1 bull")
        else:
            bear_votes += 1; reasons.append(f"[S3] ❌ 4H EMA21 < EMA55 → +1 bear")
    else:
        h4b = bool(df["bias_bullish_4h_weak"].iloc[-1]) if "bias_bullish_4h_weak" in df.columns else d_golden
        if h4b: bull_votes += 1; reasons.append("[S3] ✅ 4H bias bullish (fallback) → +1 bull")
        else:   bear_votes += 1; reasons.append("[S3] ❌ 4H bias bearish (fallback) → +1 bear")

    # S4: Price structure on 1D
    structure = price_structure(df_1d_ext if df_1d_ext is not None else df, lookback=30)
    if structure == "bull":
        bull_votes += 1; reasons.append("[S4] ✅ 1D: HH+HL uptrend → +1 bull")
    elif structure == "bear":
        bear_votes += 1; reasons.append("[S4] ❌ 1D: LH+LL downtrend → +1 bear")
    else:
        ranging_votes += 1; reasons.append(f"[S4] ↔️ 1D structure: {structure} → +1 ranging")

    # S5: ADX
    adx = adx_strength(df_1d_ext if df_1d_ext is not None else df)
    if adx > 25:
        if bull_votes > bear_votes:
            bull_votes += 1; reasons.append(f"[S5] ✅ ADX={adx:.1f} strong → confirms bull → +1")
        elif bear_votes > bull_votes:
            bear_votes += 1; reasons.append(f"[S5] ✅ ADX={adx:.1f} strong → confirms bear → +1")
        else:
            reasons.append(f"[S5] ADX={adx:.1f} strong — tied, no extra vote")
    elif adx < 20:
        ranging_votes += 2; reasons.append(f"[S5] ↔️ ADX={adx:.1f} no trend → +2 ranging")
    else:
        ranging_votes += 1; reasons.append(f"[S5] ↔️ ADX={adx:.1f} weak → +1 ranging")

    # VP context
    if above_vah:               reasons.append(f"[VP] Above VAH${vah:,.0f} — bullish zone ✅")
    elif val<=price<=vah:       reasons.append(f"[VP] Inside VA ${val:,.0f}-${vah:,.0f} — neutral ↔️")
    else:                       reasons.append(f"[VP] Below VAL${val:,.0f} — bearish zone ❌")
    reasons.append(f"[SCORE] Bull={bull_votes} Bear={bear_votes} Ranging={ranging_votes}")

    # Classify
    if ranging_votes >= 4 or (ranging_votes >= 3 and adx < 22):   regime = "RANGING"
    elif bull_votes >= 5 and above_vah:                             regime = "STRONG_BULL"
    elif bull_votes >= 4:                                           regime = "STRONG_BULL"
    elif bull_votes >= 2 and bull_votes>bear_votes and ranging_votes<3:
        regime = "STRONG_BULL" if (bull_votes>=3 and price>ema50_1d and above_vah) else "WEAK_BULL"
    elif bear_votes >= 4:                                           regime = "BEAR"
    elif bear_votes >= 2 and bear_votes>bull_votes and ranging_votes<3: regime = "BEAR"
    elif ranging_votes >= 2:                                        regime = "RANGING"
    else:                                                           regime = "WEAK_BULL" if d_golden else "BEAR"
    return regime, reasons

def get_regime_config(regime):
    cfg = REGIME_CONFIGS.get(regime, REGIME_CONFIGS["RANGING"])
    w   = dict(CAT_WEIGHTS); w.update(cfg.get("weights_override", {}))
    return {
        "weights": w, "enable_longs": cfg["enable_longs"], "enable_shorts": cfg["enable_shorts"],
        "min_score_long": cfg["min_score_long"], "min_score_short": cfg["min_score_short"],
        "atr_sl_mult": cfg["atr_sl_mult"], "atr_tp_mult": cfg["atr_tp_mult"],
        "description": cfg["description"], "emoji": cfg["emoji"],
    }


# ══════════════════════════════════════════════════════════════════
# SCORING ENGINE v8.5
# ══════════════════════════════════════════════════════════════════

def compute_scores_regime(df, regime, min_cats=2, trend_req=True, h1_structure=None, h4_structure=None):
    rcfg = get_regime_config(regime); w = rcfg["weights"]
    min_sl = rcfg["min_score_long"]; min_ss = rcfg["min_score_short"]
    enl = rcfg["enable_longs"];     ens = rcfg["enable_shorts"]

    tl=pd.Series(0.0,index=df.index); ts=pd.Series(0.0,index=df.index)
    zl=pd.Series(0.0,index=df.index); zs=pd.Series(0.0,index=df.index)
    ml=pd.Series(0.0,index=df.index); ms=pd.Series(0.0,index=df.index)
    pl=pd.Series(0.0,index=df.index); ps=pd.Series(0.0,index=df.index)

    # TREND
    tl+=df["bias_bullish_4h"].astype(float)*w.get("4h_bias",3.0)
    ts+=df["bias_bearish_4h"].astype(float)*w.get("4h_bias",3.0)
    be=(df["ema8"]>df["ema21"])&(df["ema21"]>df["ema55"]); bb=(df["ema8"]<df["ema21"])&(df["ema21"]<df["ema55"])
    tl+=be.astype(float)*w.get("ema_ribbon",2.0); ts+=bb.astype(float)*w.get("ema_ribbon",2.0)
    tl+=(df["close"]>df["ema50"]).astype(float)*w.get("ema50",2.5)
    ts+=(df["close"]<df["ema50"]).astype(float)*w.get("ema50",2.5)
    tl+=(df["close"]>df["ema200"]).astype(float)*w.get("ema200",3.0)
    ts+=(df["close"]<df["ema200"]).astype(float)*w.get("ema200",3.0)
    tl+=df["bos_up"].astype(float)*w.get("bos",2.5);   ts+=df["bos_down"].astype(float)*w.get("bos",2.5)
    tl+=df["above_vah"].astype(float)*w.get("vah_bias",2.0)
    ts+=df["below_vah"].astype(float)*w.get("vah_bias",2.0)

    # ZONE
    zl+=df["near_support"].astype(float)*w.get("support_resistance",2.5)
    zs+=df["near_resistance"].astype(float)*w.get("support_resistance",2.5)
    zl+=df["near_fib618"].astype(float)*w.get("fib618",2.0); zs+=df["near_fib618"].astype(float)*w.get("fib618",2.0)
    zl+=df["near_fib50"].astype(float)*w.get("fib50",1.5);   zs+=df["near_fib50"].astype(float)*w.get("fib50",1.5)
    zl+=df["near_fib382"].astype(float)*w.get("fib382",1.5); zs+=df["near_fib382"].astype(float)*w.get("fib382",1.5)
    zl+=df["in_bullish_ob"].astype(float)*w.get("order_blocks",2.5)
    zs+=df["in_bearish_ob"].astype(float)*w.get("order_blocks",2.5)
    zl+=(df["close"]<df["vwap"]).astype(float)*w.get("vwap",1.0)
    zs+=(df["close"]>df["vwap"]).astype(float)*w.get("vwap",1.0)
    zl+=df["near_poc"].astype(float)*w.get("volume_profile",3.0)
    zl+=df["near_val"].astype(float)*w.get("volume_profile",3.0)
    zs+=df["near_vah"].astype(float)*w.get("volume_profile",3.0)
    zl+=df["avwap_bull_conf"].astype(float)*w.get("avwap_anchor",2.5)
    zs+=df["avwap_bear_conf"].astype(float)*w.get("avwap_anchor",2.5)
    zs+=df["near_fib100"].astype(float)*w.get("fib_ext",1.5)
    zs+=df["near_fib1618"].astype(float)*w.get("fib_ext",1.5)

    # MOMENTUM
    ml+=(df["rsi"]<40).astype(float)*w.get("rsi",2.0); ms+=(df["rsi"]>60).astype(float)*w.get("rsi",2.0)
    ml+=df["rsi_trend_bull_zone"].astype(float)*(w.get("rsi",2.0)*0.5)
    ms+=df["rsi_trend_bear_zone"].astype(float)*(w.get("rsi",2.0)*0.5)
    ml+=df["rsi_bull_div"].astype(float)*w.get("rsi_div",2.5);         ms+=df["rsi_bear_div"].astype(float)*w.get("rsi_div",2.5)
    ml+=df["rsi_hidden_bull_div"].astype(float)*w.get("rsi_hidden_div",2.0)
    ms+=df["rsi_hidden_bear_div"].astype(float)*w.get("rsi_hidden_div",2.0)
    cu=df["cvd"].diff()>0; cd=df["cvd"].diff()<0
    ml+=cu.astype(float)*w.get("cvd",2.0); ms+=cd.astype(float)*w.get("cvd",2.0)
    ml+=df["cvd_bull_div"].astype(float)*w.get("cvd_div",2.5); ms+=df["cvd_bear_div"].astype(float)*w.get("cvd_div",2.5)
    ml+=df["real_buying"].astype(float)*w.get("real_buying",2.0); ms+=df["real_selling"].astype(float)*w.get("real_buying",2.0)
    ml+=df["vol_confirm"].astype(float)*w.get("volume",0.5); ms+=df["vol_confirm"].astype(float)*w.get("volume",0.5)
    ml+=df["mfi_bull_div"].astype(float)*w.get("mfi",2.0);    ms+=df["mfi_bear_div"].astype(float)*w.get("mfi",2.0)
    ml+=df["mfi_hidden_bull"].astype(float)*w.get("mfi",2.0); ms+=df["mfi_hidden_bear"].astype(float)*w.get("mfi",2.0)
    ml+=df["mfi_oversold"].astype(float)*w.get("mfi",2.0)*0.5; ms+=df["mfi_overbought"].astype(float)*w.get("mfi",2.0)*0.5
    ml+=df["stoch_oversold"].astype(float)*w.get("stochastic",1.5);   ms+=df["stoch_overbought"].astype(float)*w.get("stochastic",1.5)
    ml+=df["stoch_bull_cross"].astype(float)*w.get("stochastic",1.5); ms+=df["stoch_bear_cross"].astype(float)*w.get("stochastic",1.5)
    ml+=df["bull_flag"].astype(float)*w.get("bull_flag",2.0)

    # POSITIONING
    pl+=df["choch"].astype(float)*w.get("choch",1.0);             ps+=df["choch"].astype(float)*w.get("choch",1.0)
    pl+=df["major_long_liq"].astype(float)*w.get("liquidations",2.0)
    ps+=df["major_short_liq"].astype(float)*w.get("liquidations",2.0)
    pl+=df["funding_extreme_short"].astype(float)*w.get("funding",1.5)
    ps+=df["funding_extreme_long"].astype(float)*w.get("funding",1.5)
    pl+=df["oi_confirm_long"].astype(float)*w.get("oi",1.5)
    ps+=df["oi_confirm_short"].astype(float)*w.get("oi",1.5)

    df["long_score"]  = tl+zl+ml+pl
    df["short_score"] = ts+zs+ms+ps

    def cat_gate(tl_, zl_, ml_, pl_, min_score, enable):
        if not enable: return pd.Series(False, index=tl_.index)
        cats = (tl_>0).astype(int)+(zl_>0).astype(int)+(ml_>0).astype(int)+(pl_>0).astype(int)
        s_ok = (tl_+zl_+ml_+pl_) >= min_score
        c_ok = cats >= min_cats
        t_ok = (tl_>0) if trend_req else pd.Series(True,index=tl_.index)
        return s_ok & c_ok & t_ok

    long_sig  = cat_gate(tl, zl, ml, pl, min_sl, enl)
    short_sig = cat_gate(ts, zs, ms, ps, min_ss, ens)

    # Bear + local ranging overrides
    h1r = h1_structure in ("ranging","recovering"); h4r = h4_structure in ("ranging","recovering")
    mr_mod  = (df["rsi"]<40)|df["stoch_oversold"]|df["mfi_oversold"]
    mr_str  = (df["rsi"]<35)|(df["stoch_oversold"]&(df["rsi"]<40))
    at_sup  = df["near_val"]|df["near_support"]|df["in_bullish_ob"]|df["near_fib618"]|df["near_poc"]
    rev1    = df["rsi_bull_div"]|df["mfi_bull_div"]|df["stoch_bull_cross"]|df["cvd_bull_div"]
    rev2    = rev1 & (df["rsi_bull_div"]|df["mfi_bull_div"])
    no_tr   = ~(df["bos_up"]|df["bull_flag"]|df["bias_bullish_4h"])
    str_res = (df["near_resistance"]|df["near_vah"]|df["near_fib618"])&(df["cvd_bear_div"]|df["rsi_bear_div"])

    if regime=="BEAR" and h4r and h1r:
        short_sig = (df["rsi"]>65) & str_res & short_sig
        long_sig  = mr_mod & at_sup & rev1 & no_tr & ((zl+ml)>=4.0)
        df.attrs["bear_override"] = "LEVEL3_BOTH_RANGING"
    elif regime=="BEAR" and h4r:
        short_sig = (df["rsi"]>65) & str_res & short_sig
        long_sig  = mr_mod & at_sup & rev2 & no_tr & ((zl+ml)>=4.5)
        df.attrs["bear_override"] = "LEVEL2_4H_RANGING"
    elif regime=="BEAR" and h1r:
        h1ba = (df["ema8"]<df["ema21"])&(df["ema21"]<df["ema55"])
        short_sig = (h1ba|((df["rsi"]>68)&str_res)) & short_sig
        long_sig  = mr_str & at_sup & rev2 & no_tr & ((zl+ml)>=5.0)
        df.attrs["bear_override"] = "LEVEL1_1H_ONLY"
    else:
        df.attrs["bear_override"] = "NONE"

    df.attrs["h1_structure"] = h1_structure or "unknown"
    df.attrs["h4_structure"] = h4_structure or "unknown"
    df["long_signal"]  = long_sig;  df["short_signal"] = short_sig
    df["trend_l"]=tl;  df["trend_s"]=ts
    df["zone_l"] =zl;  df["zone_s"] =zs
    df["mom_l"]  =ml;  df["mom_s"]  =ms
    df["pos_l"]  =pl;  df["pos_s"]  =ps
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
    # detect_local_structure needs ema8/21/55, rsi, atr computed on that timeframe
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
    df     = compute_scores_regime(df, regime, MIN_CATS, TREND_REQ, h1_structure=h1_str, h4_structure=h4_str)

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
    sl_mult  = rcfg["atr_sl_mult"]; tp_mult = rcfg["atr_tp_mult"]

    entry_plan = None
    if lsig:
        entry_plan = {"entry": round(price,2), "stop_loss": round(price-atr_v*sl_mult,2),
                      "take_profit": round(price+atr_v*tp_mult,2), "rr": round(tp_mult/sl_mult,1)}
    elif ssig:
        entry_plan = {"entry": round(price,2), "stop_loss": round(price+atr_v*sl_mult,2),
                      "take_profit": round(price-atr_v*tp_mult,2), "rr": round(tp_mult/sl_mult,1)}

    indicator_map = [
        ("4H Bias Bullish",    bool(row.get("bias_bullish_4h")),         False),
        ("4H Bias Bearish",    False,                                     bool(row.get("bias_bearish_4h"))),
        ("EMA Ribbon Bull",    bool((row["ema8"]>row["ema21"])and(row["ema21"]>row["ema55"])), False),
        ("EMA Ribbon Bear",    False, bool((row["ema8"]<row["ema21"])and(row["ema21"]<row["ema55"]))),
        ("Above EMA50",        bool(price>ema50_v),                      False),
        ("Below EMA50",        False,                                     bool(price<ema50_v)),
        ("Above EMA200",       bool(price>ema200_v),                     False),
        ("Below EMA200",       False,                                     bool(price<ema200_v)),
        ("BOS Up",             bool(row.get("bos_up")),                  False),
        ("BOS Down",           False,                                     bool(row.get("bos_down"))),
        ("Above VAH",          bool(row.get("above_vah")),               False),
        ("Below VAH",          False,                                     bool(row.get("below_vah"))),
        ("Near POC",           bool(row.get("near_poc")),                bool(row.get("near_poc"))),
        ("Near VAL",           bool(row.get("near_val")),                False),
        ("Near VAH",           False,                                     bool(row.get("near_vah"))),
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
        ("Below VWAP",         bool(row["close"]<row["vwap"]),           False),
        ("Above VWAP",         False,                                     bool(row["close"]>row["vwap"])),
        (f"RSI {rsi_v:.0f}",   bool(rsi_v<40 or (is_up and 30<=rsi_v<=50)), bool(rsi_v>60 or (not is_up and 50<=rsi_v<=70))),
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
        "regime_config":  {k:v for k,v in rcfg.items() if k!="weights"},
        "h1_structure":   h1_str,
        "h4_structure":   h4_str,
        "bear_override":  df.attrs.get("bear_override","NONE"),
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
            "rsi": round(rsi_v,2), "mfi": round(mfi_v,2), "stoch_k": round(stoch_v,2),
            "atr": round(atr_v,2), "ema50": round(ema50_v,2), "ema200": round(ema200_v,2),
            "vwap": round(float(row["vwap"]),2), "vol_ratio": round(float(row.get("vol_ratio",1)),2),
            "above_vah": bool(row.get("above_vah")), "bull_flag": bool(row.get("bull_flag")),
            "real_buying": bool(row.get("real_buying")), "real_selling": bool(row.get("real_selling")),
        },
        "df": df,
    }


# ══════════════════════════════════════════════════════════════════
# MEMORY
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
    if not mem: return "📚 No backtest history yet\\. Run /backtest first\\."
    ranked = sorted(mem, key=lambda x: x.get("sharpe_ratio",0), reverse=True)
    best   = ranked[0]
    by_regime = {}
    for r in mem:
        reg = r.get("regime","UNKNOWN")
        if reg not in by_regime: by_regime[reg]=[]
        by_regime[reg].append(r)
    lines = [
        f"📚 *Learning Report* — {len(mem)} runs", "",
        f"🥇 *Best:* `{best['run_id']}`",
        f"   {best['period']} \\| {best.get('timeframe','1H')} \\| Return: {best['total_return']:.1%}",
        f"   Sharpe: {best['sharpe_ratio']:.3f} \\| WR: {best['win_rate']:.1%}",
        f"   min\\_score={best['config']['min_score']} SL={best['config']['atr_sl_mult']}x TP={best['config']['atr_tp_mult']}x",
        "", "📊 *By Regime:*",
    ]
    em_map = {"STRONG_BULL":"🚀","WEAK_BULL":"📈","BEAR":"🐻","RANGING":"↔️","UNKNOWN":"❓"}
    for reg, runs in sorted(by_regime.items()):
        avg_r = sum(r["total_return"] for r in runs)/len(runs)
        avg_s = sum(r["sharpe_ratio"] for r in runs)/len(runs)
        lines.append(f"  {em_map.get(reg,'❓')} {reg}: {avg_r:+.1%} \\| SR={avg_s:.2f} \\| {len(runs)} runs")
    lines += ["", "*All runs \\(Sharpe ranked\\):*"]
    for i, r in enumerate(ranked[:10]):
        bh = "✅" if r.get("beat_bh") else "❌"
        lines.append(f"  {i+1}\\. {r['period']} {r.get('timeframe','1H')} \\| {r['total_return']:+.1%} \\| SR={r['sharpe_ratio']:.2f} {bh}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# GOOGLE SHEETS LOGGER
# ══════════════════════════════════════════════════════════════════

def log_to_sheets(data: dict):
    if not SHEETS_URL: return
    try: requests.post(SHEETS_URL, json=data, timeout=10)
    except: pass

def log_signal_to_sheets(signal: dict):
    log_to_sheets({
        "timestamp":   signal["timestamp"],   "price":       signal["price"],
        "direction":   signal["direction"],   "regime":      signal.get("regime",""),
        "long_score":  signal["long_score"],  "short_score": signal["short_score"],
        "vp_poc":      signal.get("volume_profile",{}).get("poc",""),
        "vp_vah":      signal.get("volume_profile",{}).get("vah",""),
        "h1_structure":signal.get("h1_structure",""),
        "h4_structure":signal.get("h4_structure",""),
        "reasons":     ", ".join(signal.get(
            "reasons_long" if signal["direction"]=="LONG" else "reasons_short",[])),
    })
