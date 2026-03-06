"""
signal_runner.py — BTC Strategy v6 Backend
All indicator logic, data fetching, scoring engine for Streamlit app.
v6 new: EMA50, Fib 0.5, RSI hidden div, CVD absorption, real buying pressure,
        S/R for 1H/4H/1D, Binance primary data source, daily trade history.
"""

import os, json, time, requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from pathlib import Path

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════

CRYPTOCOMPARE_API_KEY = os.environ.get("CRYPTOCOMPARE_API_KEY", "")

INIT_CASH    = 10_000.0
FEES         = 0.001
SLIPPAGE     = 0.0005
ATR_SL       = 2.0
ATR_TP       = 4.0
MIN_SCORE    = 5.0
MIN_CATS     = 2
TREND_REQ    = True
ENABLE_SHORT = True

PERIOD_DAYS = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "3Y": 1095}
MEMORY_FILE = Path(__file__).parent / "btc_backtest_memory.json"

BINANCE_SPOT_URL  = "https://api.binance.com/api/v3/klines"
BINANCE_FAPI_URL  = "https://fapi.binance.com/fapi/v1"
BINANCE_FDATA_URL = "https://fapi.binance.com/futures/data"

INTERVAL_MS = {
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}

CAT_WEIGHTS = {
    # TIER 5 — Core trend
    "4h_bias":            3.0,
    "ema200":             3.0,
    "ema50":              2.5,
    "ema_ribbon":         2.0,
    "bos":                2.5,
    # TIER 4 — Key levels
    "support_resistance": 2.5,
    "fib618":             2.0,
    "fib50":              1.5,
    "fib382":             1.5,
    "order_blocks":       2.5,
    # TIER 3 — Momentum
    "rsi":                2.0,
    "rsi_div":            2.5,
    "rsi_hidden_div":     2.0,
    "cvd":                2.0,
    "cvd_div":            2.5,
    "real_buying":        2.0,
    # TIER 2 — Positioning
    "choch":              1.0,
    "vwap":               1.0,
    "liquidations":       2.0,
    "funding":            1.5,
    "oi":                 1.5,
    # TIER 1 — Supplementary
    "volume":             0.5,
}


# ══════════════════════════════════════════════════════════════════
# DATA FETCHERS
# ══════════════════════════════════════════════════════════════════

def fetch_ohlcv_binance(symbol="BTCUSDT", interval="1h", days_back=365):
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - int(days_back * 86_400_000)
    ms_step  = INTERVAL_MS.get(interval, 3_600_000)
    all_data, cur_start = [], start_ms

    while cur_start < end_ms:
        try:
            r = requests.get(BINANCE_SPOT_URL, params={
                "symbol": symbol, "interval": interval,
                "startTime": cur_start, "endTime": end_ms, "limit": 1000
            }, timeout=20)
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            all_data.extend(batch)
            cur_start = batch[-1][0] + ms_step
            if len(batch) < 1000:
                break
            time.sleep(0.12)
        except Exception:
            break

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "num_trades", "tbv", "tbq", "ignore"
    ])
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_localize(None)
    df = df.set_index("datetime")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[["open", "high", "low", "close", "volume"]]
    df = df[df["volume"] > 0].sort_index()
    return df[~df.index.duplicated(keep="last")]


def fetch_ohlcv_cc(symbol="BTC", currency="USD", unit="hour",
                   aggregate=1, days_back=365):
    endpoints = {
        "hour": "https://min-api.cryptocompare.com/data/v2/histohour",
        "day":  "https://min-api.cryptocompare.com/data/v2/histoday",
    }
    headers = {}
    if CRYPTOCOMPARE_API_KEY:
        headers["authorization"] = f"Apikey {CRYPTOCOMPARE_API_KEY}"
    mins_per   = {"hour": 60 * aggregate, "day": 1440 * aggregate}[unit]
    total_need = int(days_back * 1440 / mins_per)
    all_data, to_ts, fetched = [], None, 0

    while fetched < total_need:
        params = {"fsym": symbol, "tsym": currency,
                  "limit": min(2000, total_need - fetched), "aggregate": aggregate}
        if to_ts:
            params["toTs"] = to_ts
        try:
            resp = requests.get(endpoints[unit], params=params,
                                headers=headers, timeout=15)
            data = resp.json()
            if data.get("Response") != "Success":
                break
            candles = data["Data"]["Data"]
            if not candles:
                break
            all_data = candles + all_data
            to_ts    = candles[0]["time"] - 1
            fetched += len(candles)
            time.sleep(0.15)
        except Exception:
            break

    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("datetime").rename(columns={"volumefrom": "volume"})
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[df["volume"] > 0].sort_index()
    return df[~df.index.duplicated(keep="last")]


def fetch_ohlcv(unit="hour", aggregate=1, days_back=365):
    """Smart wrapper: Binance → CryptoCompare fallback."""
    interval_map = {("hour", 1): "1h", ("hour", 4): "4h", ("day", 1): "1d"}
    interval = interval_map.get((unit, aggregate), "1h")
    df = fetch_ohlcv_binance("BTCUSDT", interval, days_back)
    if df.empty:
        df = fetch_ohlcv_cc("BTC", "USD", unit, aggregate, days_back)
    return df


def fetch_liquidations(symbol="BTCUSDT", limit=1000, max_days=30):
    url, all_orders = f"{BINANCE_FAPI_URL}/allForceOrders", []
    end_ms, day_ms  = int(time.time() * 1000), 86_400_000
    for _ in range(max_days):
        start_ms = end_ms - day_ms
        try:
            r   = requests.get(url, params={"symbol": symbol, "startTime": start_ms,
                               "endTime": end_ms, "limit": 1000}, timeout=15)
            raw = r.json()
            if isinstance(raw, list):
                all_orders.extend(raw)
            if len(all_orders) >= limit:
                break
        except Exception:
            break
        end_ms = start_ms
        time.sleep(0.08)
    if not all_orders:
        return pd.DataFrame()
    df = pd.DataFrame(all_orders[:limit])
    df["datetime"]      = pd.to_datetime(df["time"], unit="ms")
    df                  = df.set_index("datetime").sort_index()
    df["price"]         = pd.to_numeric(df["price"],       errors="coerce")
    df["qty"]           = pd.to_numeric(df["executedQty"], errors="coerce")
    df["liq_usd"]       = df["price"] * df["qty"]
    df["long_liq_usd"]  = df.apply(lambda r: r["liq_usd"] if r["side"] == "SELL" else 0.0, axis=1)
    df["short_liq_usd"] = df.apply(lambda r: r["liq_usd"] if r["side"] == "BUY"  else 0.0, axis=1)
    return df


def fetch_funding(symbol="BTCUSDT", limit=1000):
    try:
        r   = requests.get(f"{BINANCE_FAPI_URL}/fundingRate",
                           params={"symbol": symbol, "limit": limit}, timeout=15)
        raw = r.json()
        if not isinstance(raw, list) or not raw:
            return pd.DataFrame()
        df = pd.DataFrame(raw)
        tc = next((c for c in ["fundingTime", "calcTime", "time"] if c in df.columns), None)
        if not tc:
            return pd.DataFrame()
        df["datetime"]     = pd.to_datetime(pd.to_numeric(df[tc], errors="coerce"), unit="ms")
        df                 = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
        df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
        return df[["funding_rate"]].dropna()
    except Exception:
        return pd.DataFrame()


def fetch_oi(symbol="BTCUSDT", period="1h", limit=500):
    try:
        r   = requests.get(f"{BINANCE_FDATA_URL}/openInterestHist",
                           params={"symbol": symbol, "period": period, "limit": limit}, timeout=15)
        raw = r.json()
        if not isinstance(raw, list) or not raw:
            return pd.DataFrame()
        df = pd.DataFrame(raw)
        tc = next((c for c in df.columns if "time" in c.lower()), None)
        if not tc:
            return pd.DataFrame()
        df["datetime"] = pd.to_datetime(pd.to_numeric(df[tc], errors="coerce"), unit="ms")
        df             = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
        df["oi"]       = pd.to_numeric(df["sumOpenInterest"], errors="coerce")
        return df[["oi"]].dropna()
    except Exception:
        return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════
# INDICATOR HELPERS
# ══════════════════════════════════════════════════════════════════

def _ema(s, p):
    return s.ewm(span=p, adjust=False).mean()

def _rsi(close, p=14):
    d  = close.diff()
    ag = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - (100 / (1 + ag / al.replace(0, np.nan)))

def _atr(df, p=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=p, adjust=False).mean()


# ══════════════════════════════════════════════════════════════════
# S/R + FIBONACCI
# ══════════════════════════════════════════════════════════════════

def find_sr_levels(df_tf, swing=5, label="1H"):
    if df_tf is None or df_tf.empty:
        return {"levels": [], "nearest_support": None, "nearest_resistance": None, "label": label}

    h_v = df_tf["high"].values
    l_v = df_tf["low"].values
    cur = float(df_tf["close"].iloc[-1])
    s   = swing

    ph, pl = [], []
    for i in range(s, len(df_tf) - s):
        if h_v[i] == max(h_v[i-s:i+s+1]): ph.append(round(float(h_v[i]), 2))
        if l_v[i] == min(l_v[i-s:i+s+1]): pl.append(round(float(l_v[i]), 2))

    def dedup(vals, tol=0.003):
        vals = sorted(set(vals))
        out  = []
        for v in vals:
            if not out or abs(v - out[-1]) / out[-1] > tol:
                out.append(v)
        return out

    supports    = [v for v in dedup(pl) if v < cur]
    resistances = [v for v in dedup(ph) if v > cur]

    levels  = [{"price": v, "type": "support",    "timeframe": label} for v in supports[-5:]]
    levels += [{"price": v, "type": "resistance", "timeframe": label} for v in resistances[:5]]

    return {
        "levels":             levels,
        "nearest_support":    supports[-1]    if supports    else None,
        "nearest_resistance": resistances[0]  if resistances else None,
        "label":              label,
    }


def compute_fibonacci(df_tf, lookback=100, label="1H"):
    if df_tf is None or df_tf.empty:
        return {"label": label, "fib382": 0, "fib50": 0, "fib618": 0,
                "anchor_high": 0, "anchor_low": 0, "trend": "unknown"}
    r    = df_tf.tail(lookback)
    sh   = float(r["high"].max())
    sl   = float(r["low"].min())
    diff = sh - sl
    down = r["high"].idxmax() < r["low"].idxmin()

    if down:
        f382, f50, f618 = sl + diff*0.382, sl + diff*0.500, sl + diff*0.618
    else:
        f382, f50, f618 = sh - diff*0.382, sh - diff*0.500, sh - diff*0.618

    return {
        "label":       label,
        "anchor_high": round(sh,   2),
        "anchor_low":  round(sl,   2),
        "fib382":      round(f382, 2),
        "fib50":       round(f50,  2),
        "fib618":      round(f618, 2),
        "trend":       "down" if down else "up",
    }


# ══════════════════════════════════════════════════════════════════
# INDICATOR ENGINE
# ══════════════════════════════════════════════════════════════════

def compute_indicators(df_1h, df_4h, liq_df, fund_df, oi_df, df_1d=None):
    df = df_1h.copy()

    df["ema8"]   = _ema(df["close"], 8)
    df["ema21"]  = _ema(df["close"], 21)
    df["ema50"]  = _ema(df["close"], 50)
    df["ema55"]  = _ema(df["close"], 55)
    df["ema200"] = _ema(df["close"], 200)
    df["rsi"]    = _rsi(df["close"])
    df["atr"]    = _atr(df)

    df["ema50_above_200"] = df["ema50"] > df["ema200"]
    df["golden_cross"]    = (df["ema50"] > df["ema200"]) & (df["ema50"].shift(1) <= df["ema200"].shift(1))
    df["death_cross"]     = (df["ema50"] < df["ema200"]) & (df["ema50"].shift(1) >= df["ema200"].shift(1))

    tp         = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (tp * df["volume"]).cumsum() / df["volume"].cumsum()

    hl         = (df["high"] - df["low"]).replace(0, np.nan)
    df["cvd"]  = (df["volume"] * ((df["close"]-df["low"])/hl - (df["high"]-df["close"])/hl)).cumsum()

    cvd_v  = df["cvd"].values
    c_vals = df["close"].values
    cvd_bull, cvd_bear = [False]*len(df), [False]*len(df)
    for i in range(20, len(df)):
        p = i - 10
        if np.isnan(cvd_v[i]) or np.isnan(cvd_v[p]):
            continue
        if c_vals[i] < c_vals[p] and cvd_v[i] > cvd_v[p]:
            cvd_bull[i] = True
        if c_vals[i] > c_vals[p] and cvd_v[i] < cvd_v[p]:
            cvd_bear[i] = True
    df["cvd_bull_div"] = cvd_bull
    df["cvd_bear_div"] = cvd_bear

    vol_ma             = df["volume"].rolling(20).mean()
    vol_std            = df["volume"].rolling(20).std()
    df["vol_confirm"]  = df["volume"] > vol_ma
    df["vol_spike"]    = df["volume"] > (vol_ma + 2*vol_std)
    df["vol_ratio"]    = df["volume"] / vol_ma
    cvd_rising         = df["cvd"].diff() > 0
    cvd_falling        = df["cvd"].diff() < 0
    price_up           = df["close"].pct_change() > 0
    price_down         = df["close"].pct_change() < 0
    df["real_buying"]  = cvd_rising  & price_up   & df["vol_confirm"]
    df["real_selling"] = cvd_falling & price_down & df["vol_confirm"]

    h_v, l_v = df["high"].values, df["low"].values
    cur = float(df["close"].iloc[-1])
    s, ph, pl = 3, [], []
    for i in range(s, len(df)-s):
        if h_v[i] == max(h_v[i-s:i+s+1]): ph.append(h_v[i])
        if l_v[i] == min(l_v[i-s:i+s+1]): pl.append(l_v[i])
    df["near_support"]    = pd.Series(False, index=df.index)
    df["near_resistance"] = pd.Series(False, index=df.index)
    for lvl in [v for v in pl if v < cur]:
        df["near_support"]    |= (df["close"]-lvl).abs()/lvl < 0.005
    for lvl in [v for v in ph if v > cur]:
        df["near_resistance"] |= (df["close"]-lvl).abs()/lvl < 0.005

    r100  = df.tail(100)
    sh    = float(r100["high"].max())
    sl    = float(r100["low"].min())
    diff  = sh - sl
    down  = r100["high"].idxmax() < r100["low"].idxmin()
    fib382 = sl + diff*0.382 if down else sh - diff*0.382
    fib50  = sl + diff*0.500 if down else sh - diff*0.500
    fib618 = sl + diff*0.618 if down else sh - diff*0.618
    df["near_fib382"] = (df["close"]-fib382).abs()/df["close"] < 0.008
    df["near_fib50"]  = (df["close"]-fib50).abs()/df["close"]  < 0.008
    df["near_fib618"] = (df["close"]-fib618).abs()/df["close"] < 0.008
    df.attrs["fib382_price"] = round(fib382, 2)
    df.attrs["fib50_price"]  = round(fib50,  2)
    df.attrs["fib618_price"] = round(fib618, 2)
    df.attrs["fib_high"]     = round(sh, 2)
    df.attrs["fib_low"]      = round(sl, 2)

    opens, closes = df["open"].values, df["close"].values
    atr_v  = _atr(df).values
    df["in_bullish_ob"] = pd.Series(False, index=df.index)
    df["in_bearish_ob"] = pd.Series(False, index=df.index)
    for i in range(2, len(df)-2):
        a = atr_v[i]
        if np.isnan(a) or a == 0:
            continue
        if closes[i] < opens[i] and closes[i+2] - closes[i] > 2.0*a:
            df["in_bullish_ob"] |= ((df["close"] >= min(opens[i],closes[i])) &
                                    (df["close"] <= max(opens[i],closes[i])))
        if closes[i] > opens[i] and closes[i] - closes[i+2] > 2.0*a:
            df["in_bearish_ob"] |= ((df["close"] >= min(opens[i],closes[i])) &
                                    (df["close"] <= max(opens[i],closes[i])))

    rsi_v  = df["rsi"].values
    rsi_bull, rsi_bear = [False]*len(df), [False]*len(df)
    rsi_hbull, rsi_hbear = [False]*len(df), [False]*len(df)
    for i in range(55, len(df)):
        p = i - 5
        if np.isnan(rsi_v[i]) or np.isnan(rsi_v[p]):
            continue
        if c_vals[i] < c_vals[p] and rsi_v[i] > rsi_v[p] and rsi_v[i] < 40:
            rsi_bull[i] = True
        if c_vals[i] > c_vals[p] and rsi_v[i] < rsi_v[p] and rsi_v[i] > 60:
            rsi_bear[i] = True
        if c_vals[i] > c_vals[p] and rsi_v[i] < rsi_v[p] and 30 < rsi_v[i] < 55:
            rsi_hbull[i] = True
        if c_vals[i] < c_vals[p] and rsi_v[i] > rsi_v[p] and 45 < rsi_v[i] < 70:
            rsi_hbear[i] = True
    df["rsi_bull_div"]        = rsi_bull
    df["rsi_bear_div"]        = rsi_bear
    df["rsi_hidden_bull_div"] = rsi_hbull
    df["rsi_hidden_bear_div"] = rsi_hbear

    is_up  = df["close"] > df["ema200"]
    is_dn  = df["close"] < df["ema200"]
    df["rsi_trend_bull_zone"] = is_up & (df["rsi"] >= 30) & (df["rsi"] <= 50)
    df["rsi_trend_bear_zone"] = is_dn & (df["rsi"] >= 50) & (df["rsi"] <= 70)

    bos_up, bos_dn, choch = [False]*len(df), [False]*len(df), [False]*len(df)
    trend = "neutral"
    h_v2, l_v2 = df["high"].values, df["low"].values
    for i in range(5, len(df)-5):
        if h_v2[i] == max(h_v2[i-5:i+6]):
            if trend == "bullish":   bos_up[i]  = True
            elif trend == "bearish": choch[i]   = True
            trend = "bullish"
        if l_v2[i] == min(l_v2[i-5:i+6]):
            if trend == "bearish":   bos_dn[i]  = True
            elif trend == "bullish": choch[i]   = True
            trend = "bearish"
    df["bos_up"]   = bos_up
    df["bos_down"] = bos_dn
    df["choch"]    = choch

    if not df_4h.empty:
        e4     = _ema(df_4h["close"], 21)
        e4_200 = _ema(df_4h["close"], 200)
        bias   = (df_4h["close"] > e4) & (e4 > e4_200)
        df["bias_bullish_4h"] = bias.resample("1h").ffill().reindex(df.index, method="ffill").fillna(False)
        df["bias_bearish_4h"] = (~bias).resample("1h").ffill().reindex(df.index, method="ffill").fillna(False)
    else:
        df["bias_bullish_4h"] = False
        df["bias_bearish_4h"] = False

    df["major_long_liq"]  = False
    df["major_short_liq"] = False
    if not liq_df.empty and "long_liq_usd" in liq_df.columns:
        lh     = liq_df[["long_liq_usd", "short_liq_usd"]].resample("1h").sum()
        lt_thr = lh["long_liq_usd"].quantile(0.90)
        st_thr = lh["short_liq_usd"].quantile(0.90)
        df["major_long_liq"]  = (lh["long_liq_usd"]  > lt_thr).reindex(df.index, fill_value=False)
        df["major_short_liq"] = (lh["short_liq_usd"] > st_thr).reindex(df.index, fill_value=False)

    df["funding_extreme_long"]  = False
    df["funding_extreme_short"] = False
    if not fund_df.empty:
        fr = fund_df["funding_rate"].resample("1h").ffill().reindex(df.index, method="ffill")
        df["funding_extreme_long"]  = fr >  0.0005
        df["funding_extreme_short"] = fr < -0.0001

    df["oi_confirm_long"]  = False
    df["oi_confirm_short"] = False
    if not oi_df.empty:
        oi_s  = oi_df["oi"].resample("1h").last().reindex(df.index, method="ffill")
        oi_ch = oi_s.pct_change()
        df["oi_confirm_long"]  = (df["close"].pct_change() > 0) & (oi_ch > 0.005)
        df["oi_confirm_short"] = (df["close"].pct_change() < 0) & (oi_ch > 0.005)

    return df.dropna(subset=["ema200", "rsi", "atr"])


# ══════════════════════════════════════════════════════════════════
# SCORING ENGINE
# ══════════════════════════════════════════════════════════════════

def compute_scores(df, w=None, min_score=None, min_cats=None,
                   trend_req=None, enable_shorts=None):
    if w           is None: w           = CAT_WEIGHTS
    if min_score   is None: min_score   = MIN_SCORE
    if min_cats    is None: min_cats    = MIN_CATS
    if trend_req   is None: trend_req   = TREND_REQ
    if enable_shorts is None: enable_shorts = ENABLE_SHORT

    z = pd.Series(0.0, index=df.index)

    tl  = z.copy(); ts = z.copy()
    tl += df["bias_bullish_4h"].astype(float)                              * w["4h_bias"]
    ts += df["bias_bearish_4h"].astype(float)                              * w["4h_bias"]
    bull_ema = (df["ema8"]>df["ema21"]) & (df["ema21"]>df["ema55"])
    bear_ema = (df["ema8"]<df["ema21"]) & (df["ema21"]<df["ema55"])
    tl += bull_ema.astype(float)                                           * w["ema_ribbon"]
    ts += bear_ema.astype(float)                                           * w["ema_ribbon"]
    tl += (df["close"] > df["ema50"]).astype(float)                        * w["ema50"]
    ts += (df["close"] < df["ema50"]).astype(float)                        * w["ema50"]
    tl += (df["close"] > df["ema200"]).astype(float)                       * w["ema200"]
    ts += (df["close"] < df["ema200"]).astype(float)                       * w["ema200"]
    tl += df["bos_up"].astype(float)                                       * w["bos"]
    ts += df["bos_down"].astype(float)                                     * w["bos"]

    zl  = z.copy(); zs = z.copy()
    zl += df["near_support"].astype(float)                                 * w["support_resistance"]
    zs += df["near_resistance"].astype(float)                              * w["support_resistance"]
    zl += df["near_fib618"].astype(float)                                  * w["fib618"]
    zs += df["near_fib618"].astype(float)                                  * w["fib618"]
    zl += df["near_fib50"].astype(float)                                   * w.get("fib50", 1.5)
    zs += df["near_fib50"].astype(float)                                   * w.get("fib50", 1.5)
    zl += df["near_fib382"].astype(float)                                  * w["fib382"]
    zs += df["near_fib382"].astype(float)                                  * w["fib382"]
    zl += df["in_bullish_ob"].astype(float)                                * w["order_blocks"]
    zs += df["in_bearish_ob"].astype(float)                                * w["order_blocks"]
    zl += (df["close"] < df["vwap"]).astype(float)                         * w["vwap"]
    zs += (df["close"] > df["vwap"]).astype(float)                         * w["vwap"]

    ml  = z.copy(); ms = z.copy()
    ml += (df["rsi"] < 40).astype(float)                                   * w["rsi"]
    ms += (df["rsi"] > 60).astype(float)                                   * w["rsi"]
    ml += df["rsi_trend_bull_zone"].astype(float)                          * (w["rsi"] * 0.5)
    ms += df["rsi_trend_bear_zone"].astype(float)                          * (w["rsi"] * 0.5)
    ml += df["rsi_bull_div"].astype(float)                                 * w["rsi_div"]
    ms += df["rsi_bear_div"].astype(float)                                 * w["rsi_div"]
    ml += df["rsi_hidden_bull_div"].astype(float)                          * w.get("rsi_hidden_div", 2.0)
    ms += df["rsi_hidden_bear_div"].astype(float)                          * w.get("rsi_hidden_div", 2.0)
    cvd_up   = df["cvd"].diff() > 0
    cvd_down = df["cvd"].diff() < 0
    ml += cvd_up.astype(float)                                             * w["cvd"]
    ms += cvd_down.astype(float)                                           * w["cvd"]
    ml += df["cvd_bull_div"].astype(float)                                 * w.get("cvd_div", 2.5)
    ms += df["cvd_bear_div"].astype(float)                                 * w.get("cvd_div", 2.5)
    ml += df["real_buying"].astype(float)                                  * w.get("real_buying", 2.0)
    ms += df["real_selling"].astype(float)                                 * w.get("real_buying", 2.0)
    ml += df["vol_confirm"].astype(float)                                  * w["volume"]
    ms += df["vol_confirm"].astype(float)                                  * w["volume"]

    pl  = z.copy(); ps = z.copy()
    pl += df["choch"].astype(float)                                        * w["choch"]
    ps += df["choch"].astype(float)                                        * w["choch"]
    pl += df["major_long_liq"].astype(float)                               * w["liquidations"]
    ps += df["major_short_liq"].astype(float)                              * w["liquidations"]
    pl += df["funding_extreme_short"].astype(float)                        * w["funding"]
    ps += df["funding_extreme_long"].astype(float)                         * w["funding"]
    pl += df["oi_confirm_long"].astype(float)                              * w["oi"]
    ps += df["oi_confirm_short"].astype(float)                             * w["oi"]

    df["long_score"]  = tl + zl + ml + pl
    df["short_score"] = ts + zs + ms + ps

    def gate(t, z, m, p):
        cats_active = ((t > 0).astype(int) + (z > 0).astype(int) +
                       (m > 0).astype(int) + (p > 0).astype(int))
        score_ok = (t + z + m + p) >= min_score
        cats_ok  = cats_active >= min_cats
        t_ok     = (t > 0) if trend_req else pd.Series(True, index=t.index)
        return score_ok & cats_ok & t_ok

    df["long_signal"]  = gate(tl, zl, ml, pl)
    df["short_signal"] = gate(ts, zs, ms, ps) & enable_shorts
    df["trend_l"] = tl;  df["trend_s"] = ts
    df["zone_l"]  = zl;  df["zone_s"]  = zs
    df["mom_l"]   = ml;  df["mom_s"]   = ms
    df["pos_l"]   = pl;  df["pos_s"]   = ps
    return df


# ══════════════════════════════════════════════════════════════════
# LIVE SIGNAL
# ══════════════════════════════════════════════════════════════════

def get_signal():
    try:
        try:
            rp = requests.get("https://api.binance.com/api/v3/ticker/price",
                              params={"symbol": "BTCUSDT"}, timeout=5)
            price = float(rp.json()["price"])
        except Exception:
            price = 0.0

        df_1h  = fetch_ohlcv("hour", 1, days_back=10)
        df_4h  = fetch_ohlcv("hour", 4, days_back=40)
        df_1d  = fetch_ohlcv("day",  1, days_back=60)
        liq    = fetch_liquidations(max_days=3)
        fund   = fetch_funding()
        oi     = fetch_oi()

        if df_1h.empty:
            return {"error": "No OHLCV data available"}

        df = compute_indicators(df_1h, df_4h, liq, fund, oi, df_1d)
        df = compute_scores(df)

        row = df.iloc[-1]
        if price == 0.0:
            price = float(row["close"])

        ls   = float(row["long_score"])
        ss   = float(row["short_score"])
        lsig = bool(row["long_signal"])
        ssig = bool(row["short_signal"])
        atr  = float(row["atr"])

        direction = "LONG" if lsig else ("SHORT" if ssig else "NONE")

        sr_1h = find_sr_levels(df_1h, swing=5, label="1H")
        sr_4h = find_sr_levels(df_4h, swing=3, label="4H")
        sr_1d = find_sr_levels(df_1d, swing=3, label="1D") if not df_1d.empty else {}

        fib_1h = compute_fibonacci(df_1h, lookback=100, label="1H")
        fib_4h = compute_fibonacci(df_4h, lookback=60,  label="4H")
        fib_1d = compute_fibonacci(df_1d, lookback=30,  label="1D") if not df_1d.empty else {}

        entry_plan = None
        if lsig:
            entry_plan = {
                "entry":       round(price, 2),
                "stop_loss":   round(price - atr * ATR_SL, 2),
                "take_profit": round(price + atr * ATR_TP, 2),
                "rr":          round(ATR_TP / ATR_SL, 1),
            }
        elif ssig:
            entry_plan = {
                "entry":       round(price, 2),
                "stop_loss":   round(price + atr * ATR_SL, 2),
                "take_profit": round(price - atr * ATR_TP, 2),
                "rr":          round(ATR_TP / ATR_SL, 1),
            }

        ema50_v  = float(row["ema50"])
        ema200_v = float(row["ema200"])
        is_up    = price > ema200_v

        reasons_long, reasons_short = [], []
        indicator_checks = [
            ("4H Bias Bullish",     bool(row.get("bias_bullish_4h")),                True,  False),
            ("4H Bias Bearish",     bool(row.get("bias_bearish_4h")),                False, True),
            ("EMA Ribbon Bull",     bool((row["ema8"]>row["ema21"])&(row["ema21"]>row["ema55"])), True, False),
            ("EMA Ribbon Bear",     bool((row["ema8"]<row["ema21"])&(row["ema21"]<row["ema55"])), False, True),
            ("Above EMA50",         bool(price > ema50_v),                           True,  False),
            ("Below EMA50",         bool(price < ema50_v),                           False, True),
            ("Above EMA200",        bool(price > ema200_v),                          True,  False),
            ("Below EMA200",        bool(price < ema200_v),                          False, True),
            ("Golden Cross",        bool(row.get("golden_cross", False)),            True,  False),
            ("Death Cross",         bool(row.get("death_cross",  False)),            False, True),
            ("BOS Up",              bool(row.get("bos_up")),                         True,  False),
            ("BOS Down",            bool(row.get("bos_down")),                       False, True),
            ("Near Support",        bool(row.get("near_support")),                   True,  False),
            ("Near Resistance",     bool(row.get("near_resistance")),                False, True),
            ("Fib 0.618",           bool(row.get("near_fib618")),                    True,  True),
            ("Fib 0.500",           bool(row.get("near_fib50")),                     True,  True),
            ("Fib 0.382",           bool(row.get("near_fib382")),                    True,  True),
            ("Bullish OB",          bool(row.get("in_bullish_ob")),                  True,  False),
            ("Bearish OB",          bool(row.get("in_bearish_ob")),                  False, True),
            ("Below VWAP",          bool(price < row.get("vwap", price+1)),          True,  False),
            ("Above VWAP",          bool(price > row.get("vwap", price-1)),          False, True),
            ("RSI Oversold",        bool(row["rsi"] < 40),                           True,  False),
            ("RSI Overbought",      bool(row["rsi"] > 60),                           False, True),
            ("RSI Bull Zone",       bool(row.get("rsi_trend_bull_zone")),            True,  False),
            ("RSI Bear Zone",       bool(row.get("rsi_trend_bear_zone")),            False, True),
            ("RSI Bull Div",        bool(row.get("rsi_bull_div")),                   True,  False),
            ("RSI Bear Div",        bool(row.get("rsi_bear_div")),                   False, True),
            ("RSI Hidden Bull Div", bool(row.get("rsi_hidden_bull_div")),            True,  False),
            ("RSI Hidden Bear Div", bool(row.get("rsi_hidden_bear_div")),            False, True),
            ("CVD Bull Div",        bool(row.get("cvd_bull_div")),                   True,  False),
            ("CVD Bear Div",        bool(row.get("cvd_bear_div")),                   False, True),
            ("Real Buying",         bool(row.get("real_buying")),                    True,  False),
            ("Real Selling",        bool(row.get("real_selling")),                   False, True),
            ("Vol Confirm",         bool(row.get("vol_confirm")),                    True,  True),
            ("Long Liq",            bool(row.get("major_long_liq")),                 True,  False),
            ("Short Liq",           bool(row.get("major_short_liq")),                False, True),
            ("Funding Short Ext",   bool(row.get("funding_extreme_short")),          True,  False),
            ("Funding Long Ext",    bool(row.get("funding_extreme_long")),           False, True),
            ("OI Long",             bool(row.get("oi_confirm_long")),                True,  False),
            ("OI Short",            bool(row.get("oi_confirm_short")),               False, True),
        ]
        for name, active, is_long_ind, is_short_ind in indicator_checks:
            if active:
                if is_long_ind:  reasons_long.append(name)
                if is_short_ind: reasons_short.append(name)

        rsi_v = float(row["rsi"])
        if is_up and 30 <= rsi_v <= 50:
            rsi_zone = "Bull Buy Zone (30-50)"
        elif not is_up and 50 <= rsi_v <= 70:
            rsi_zone = "Bear Sell Zone (50-70)"
        elif rsi_v < 30:
            rsi_zone = "Oversold"
        elif rsi_v > 70:
            rsi_zone = "Overbought"
        else:
            rsi_zone = "Neutral"

        return {
            "direction":    direction,
            "price":        price,
            "timestamp":    datetime.utcnow().isoformat(),
            "long_score":   round(ls, 2),
            "short_score":  round(ss, 2),
            "long_signal":  lsig,
            "short_signal": ssig,
            "entry_plan":   entry_plan,
            "reasons_long":  reasons_long,
            "reasons_short": reasons_short,
            "categories": {
                "trend":       {"long": round(float(row["trend_l"]),2), "short": round(float(row["trend_s"]),2)},
                "zone":        {"long": round(float(row["zone_l"]),2),  "short": round(float(row["zone_s"]),2)},
                "momentum":    {"long": round(float(row["mom_l"]),2),   "short": round(float(row["mom_s"]),2)},
                "positioning": {"long": round(float(row["pos_l"]),2),   "short": round(float(row["pos_s"]),2)},
            },
            "indicators": {
                "rsi":          round(rsi_v, 1),
                "rsi_zone":     rsi_zone,
                "atr":          round(atr, 0),
                "vol_ratio":    round(float(row.get("vol_ratio", 1)), 2),
                "vwap":         round(float(row.get("vwap", 0)), 2),
                "ema50":        round(ema50_v, 2),
                "ema200":       round(ema200_v, 2),
                "ema200_cross": "Golden" if ema50_v > ema200_v else "Death",
                "real_buying":  bool(row.get("real_buying")),
                "real_selling": bool(row.get("real_selling")),
                "cvd_bull_div": bool(row.get("cvd_bull_div")),
                "cvd_bear_div": bool(row.get("cvd_bear_div")),
            },
            "sr": {
                "1H": sr_1h,
                "4H": sr_4h,
                "1D": sr_1d,
            },
            "fibonacci": {
                "1H": fib_1h,
                "4H": fib_4h,
                "1D": fib_1d,
            },
            "df": df,
        }
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════
# GOOGLE SHEETS LOGGING
# ══════════════════════════════════════════════════════════════════

def log_signal_to_sheets(signal: dict):
    """
    Appends the current signal to a Google Sheet via the Sheets API.

    Required GitHub Secrets / env vars:
        GOOGLE_SHEET_ID       — the spreadsheet ID from the URL
        GOOGLE_CREDENTIALS    — service account JSON (as a string)

    Sheet columns (auto-created on first write):
        Timestamp | Direction | Price | Long Score | Short Score |
        Trend L | Trend S | Zone L | Zone S | Mom L | Mom S |
        Pos L | Pos S | RSI | RSI Zone | Vol Ratio | Entry |
        Stop Loss | Take Profit | Reasons Long | Reasons Short
    """
    sheet_id    = os.environ.get("GOOGLE_SHEET_ID", "")
    creds_json  = os.environ.get("GOOGLE_CREDENTIALS", "")

    # ── If secrets not set, log a warning and skip gracefully ────
    if not sheet_id or not creds_json:
        print("⚠️  log_signal_to_sheets: GOOGLE_SHEET_ID or GOOGLE_CREDENTIALS "
              "not set — skipping Sheets logging.")
        return

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds_dict = json.loads(creds_json)
        creds      = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc         = gspread.authorize(creds)
        sh         = gc.open_by_key(sheet_id)

        # Use first sheet, or create "Signals" tab if missing
        try:
            ws = sh.worksheet("Signals")
        except Exception:
            ws = sh.add_worksheet(title="Signals", rows=10000, cols=25)
            # Write header row
            ws.append_row([
                "Timestamp", "Direction", "Price",
                "Long Score", "Short Score",
                "Trend L", "Trend S", "Zone L", "Zone S",
                "Mom L", "Mom S", "Pos L", "Pos S",
                "RSI", "RSI Zone", "Vol Ratio",
                "Entry", "Stop Loss", "Take Profit",
                "Reasons Long", "Reasons Short",
            ])

        cats  = signal.get("categories", {})
        inds  = signal.get("indicators", {})
        ep    = signal.get("entry_plan") or {}

        row = [
            signal.get("timestamp", datetime.utcnow().isoformat()),
            signal.get("direction", "NONE"),
            signal.get("price", 0),
            signal.get("long_score", 0),
            signal.get("short_score", 0),
            cats.get("trend",       {}).get("long",  0),
            cats.get("trend",       {}).get("short", 0),
            cats.get("zone",        {}).get("long",  0),
            cats.get("zone",        {}).get("short", 0),
            cats.get("momentum",    {}).get("long",  0),
            cats.get("momentum",    {}).get("short", 0),
            cats.get("positioning", {}).get("long",  0),
            cats.get("positioning", {}).get("short", 0),
            inds.get("rsi",       0),
            inds.get("rsi_zone",  ""),
            inds.get("vol_ratio", 0),
            ep.get("entry",       ""),
            ep.get("stop_loss",   ""),
            ep.get("take_profit", ""),
            ", ".join(signal.get("reasons_long",  [])),
            ", ".join(signal.get("reasons_short", [])),
        ]

        ws.append_row(row, value_input_option="USER_ENTERED")
        print(f"✅ Signal logged to Google Sheets — {signal.get('direction')} @ ${signal.get('price')}")

    except ImportError:
        print("⚠️  log_signal_to_sheets: 'gspread' not installed. "
              "Add 'gspread google-auth' to requirements.txt")
    except Exception as e:
        print(f"⚠️  log_signal_to_sheets error: {e}")


# ══════════════════════════════════════════════════════════════════
# DAILY TRADE HISTORY
# ══════════════════════════════════════════════════════════════════

def build_daily_trade_history(pf, init_cash=None):
    if init_cash is None:
        init_cash = INIT_CASH

    empty = ({}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame())

    try:
        raw = pf.trades.records_readable.copy()
    except Exception:
        return empty

    if raw.empty:
        return empty

    raw.columns = [c.lower().strip().replace(" ", "_") for c in raw.columns]
    cols = set(raw.columns)

    def fc(*candidates):
        for c in candidates:
            if c in cols:
                return c
        return None

    def get_col(col_name, default=None):
        if col_name and col_name in cols:
            return raw[col_name]
        n = len(raw)
        if default is None:
            return pd.Series([np.nan] * n, index=raw.index)
        return pd.Series([default] * n, index=raw.index)

    entry_time_col  = fc("entry_time",  "entry_idx",  "open_idx",   "open_time",  "start_time")
    exit_time_col   = fc("exit_time",   "exit_idx",   "close_idx",  "close_time", "end_time")
    entry_price_col = fc("entry_price", "avg_entry_price", "open_price",  "entry_bar_close")
    exit_price_col  = fc("exit_price",  "avg_exit_price",  "close_price", "exit_bar_close")
    pnl_col         = fc("pnl", "p&l", "realized_pnl", "profit_and_loss", "net_pnl")
    dir_col         = fc("direction", "side", "type", "trade_type", "trade_side")
    exit_type_col   = fc("exit_type", "close_reason", "exit_reason", "status", "close_type")
    ret_col         = fc("return", "return_(%)", "pnl_pct", "return_pct", "ret", "pct_return")

    td = pd.DataFrame(index=raw.index)
    td["entry_time"]  = pd.to_datetime(get_col(entry_time_col), errors="coerce")
    td["exit_time"]   = pd.to_datetime(get_col(exit_time_col),  errors="coerce")
    td["direction"]   = get_col(dir_col, "Long").astype(str).str.upper()
    td["entry_price"] = pd.to_numeric(get_col(entry_price_col), errors="coerce")
    td["exit_price"]  = pd.to_numeric(get_col(exit_price_col),  errors="coerce")
    td["pnl"]         = pd.to_numeric(get_col(pnl_col, 0),      errors="coerce").fillna(0)
    td["exit_reason"] = get_col(exit_type_col, "Unknown").astype(str)

    if ret_col:
        raw_ret = pd.to_numeric(raw[ret_col], errors="coerce")
        td["pnl_pct"] = raw_ret * 100 if raw_ret.abs().median() < 5 else raw_ret
    else:
        td["pnl_pct"] = td["pnl"] / init_cash * 100

    td["win"]        = td["pnl"] > 0
    td["entry_date"] = td["entry_time"].dt.date
    td["exit_date"]  = td["exit_time"].dt.date
    td = td.sort_values("exit_time").reset_index(drop=True)
    td["balance"]    = init_cash + td["pnl"].cumsum()

    daily = td.groupby("exit_date").agg(
        trades   =("pnl", "count"),
        wins     =("win", "sum"),
        net_pnl  =("pnl", "sum"),
        best_pnl =("pnl", "max"),
        worst_pnl=("pnl", "min"),
    ).reset_index()
    daily["wr"]          = (daily["wins"] / daily["trades"]).round(3)
    daily["cum_pnl"]     = daily["net_pnl"].cumsum()
    daily["balance_eod"] = init_cash + daily["cum_pnl"]

    td["exit_week"] = td["exit_time"].dt.to_period("W")
    weekly = td.groupby("exit_week").agg(
        trades  =("pnl", "count"),
        wins    =("win", "sum"),
        net_pnl =("pnl", "sum"),
    ).reset_index()
    weekly["wr"]       = (weekly["wins"] / weekly["trades"]).round(3)
    weekly["cum_pnl"]  = weekly["net_pnl"].cumsum()
    weekly["week_str"] = weekly["exit_week"].astype(str)

    td["exit_month"] = td["exit_time"].dt.to_period("M")
    monthly = td.groupby("exit_month").agg(
        trades  =("pnl", "count"),
        wins    =("win", "sum"),
        net_pnl =("pnl", "sum"),
    ).reset_index()
    monthly["wr"]        = (monthly["wins"] / monthly["trades"]).round(3)
    monthly["cum_pnl"]   = monthly["net_pnl"].cumsum()
    monthly["ret_pct"]   = (monthly["net_pnl"] / init_cash * 100).round(2)
    monthly["month_str"] = monthly["exit_month"].astype(str)

    summary = {
        "total_trades":  len(td),
        "wins":          int(td["win"].sum()),
        "losses":        len(td) - int(td["win"].sum()),
        "win_rate":      round(float(td["win"].mean()), 3),
        "total_pnl":     round(float(td["pnl"].sum()), 2),
        "final_balance": round(float(td["balance"].iloc[-1]), 2) if len(td) > 0 else init_cash,
        "best_trade":    round(float(td["pnl"].max()), 2) if len(td) > 0 else 0,
        "worst_trade":   round(float(td["pnl"].min()), 2) if len(td) > 0 else 0,
        "best_date":     str(td.loc[td["pnl"].idxmax(), "exit_date"]) if len(td) > 0 else "",
        "worst_date":    str(td.loc[td["pnl"].idxmin(), "exit_date"]) if len(td) > 0 else "",
    }

    return summary, td, daily, weekly, monthly


# ══════════════════════════════════════════════════════════════════
# MEMORY
# ══════════════════════════════════════════════════════════════════

def save_to_memory(result: dict):
    mem = load_memory()
    mem.append(result)
    with open(MEMORY_FILE, "w") as f:
        json.dump(mem, f, indent=2, default=str)


def load_memory():
    if not MEMORY_FILE.exists():
        return []
    try:
        with open(MEMORY_FILE) as f:
            return json.load(f)
    except Exception:
        return []
