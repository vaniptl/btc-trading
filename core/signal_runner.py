"""
signal_runner.py — Shared core used by Telegram bot, Streamlit, and GitHub Actions.
Fetches live data, computes all indicators, returns structured signal + backtest results.
"""

import os, json, time, requests
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

# ── Config from environment variables (set in GitHub Secrets / Streamlit Secrets) ──
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

MEMORY_FILE  = Path(os.environ.get("MEMORY_FILE", "btc_backtest_memory.json"))
SHEETS_URL   = os.environ.get("GOOGLE_SHEETS_WEBHOOK", "")   # optional

CAT_WEIGHTS = {
    "4h_bias": 3.0, "ema_ribbon": 2.0, "ema200": 1.5, "bos": 2.0, "choch": 1.0,
    "support_resistance": 2.5, "fib618": 2.0, "fib382": 1.5, "order_blocks": 2.5,
    "vwap": 1.0, "rsi": 1.5, "rsi_div": 2.5, "cvd": 2.0, "volume": 0.5,
    "liquidations": 2.0, "funding": 1.5, "oi": 1.5,
}

PERIOD_DAYS = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "3Y": 1095}


# ══════════════════════════════════════════════════════════════════
# DATA FETCHERS
# ══════════════════════════════════════════════════════════════════

def fetch_ohlcv(unit="hour", aggregate=1, days_back=365, limit=2000):
    endpoints = {
        "minute": "https://min-api.cryptocompare.com/data/v2/histominute",
        "hour":   "https://min-api.cryptocompare.com/data/v2/histohour",
        "day":    "https://min-api.cryptocompare.com/data/v2/histoday",
    }
    headers = {}
    if CC_API_KEY and CC_API_KEY != "YOUR_CRYPTOCOMPARE_KEY_HERE":
        headers["authorization"] = f"Apikey {CC_API_KEY}"

    mins_per  = {"minute": aggregate, "hour": 60*aggregate, "day": 1440*aggregate}[unit]
    total     = min(int(days_back * 1440 / mins_per), limit)
    all_data, to_ts, remaining = [], None, total

    while remaining > 0:
        params = {"fsym": "BTC", "tsym": "USD",
                  "limit": min(2000, remaining), "aggregate": aggregate}
        if to_ts: params["toTs"] = to_ts
        try:
            r    = requests.get(endpoints[unit], params=params, headers=headers, timeout=15)
            data = r.json()
            if data.get("Response") != "Success": break
            candles = data["Data"]["Data"]
            if not candles: break
            all_data  = candles + all_data
            to_ts     = candles[0]["time"] - 1
            remaining -= len(candles)
            time.sleep(0.1)
        except: break

    if not all_data: return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("datetime").rename(columns={"volumefrom": "volume"})
    df = df[["open","high","low","close","volume"]].astype(float)
    df = df[df["volume"] > 0].sort_index()
    return df[~df.index.duplicated(keep="last")]

def fetch_liquidations(max_days=30):
    url, all_orders = "https://fapi.binance.com/fapi/v1/allForceOrders", []
    end_ms, day_ms = int(time.time()*1000), 86_400_000
    for _ in range(max_days):
        start_ms = end_ms - day_ms
        try:
            r = requests.get(url, params={"symbol":"BTCUSDT","startTime":start_ms,
                                           "endTime":end_ms,"limit":1000}, timeout=15)
            raw = r.json()
            if isinstance(raw, list): all_orders.extend(raw)
            if len(all_orders) >= 1000: break
        except: break
        end_ms = start_ms; time.sleep(0.08)
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
        r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate",
                         params={"symbol":"BTCUSDT","limit":1000}, timeout=15)
        raw = r.json()
        if not isinstance(raw, list) or not raw: return pd.DataFrame()
        df = pd.DataFrame(raw)
        tc = next((c for c in ["fundingTime","calcTime","time"] if c in df.columns), None)
        if not tc: return pd.DataFrame()
        df["datetime"]     = pd.to_datetime(pd.to_numeric(df[tc], errors="coerce"), unit="ms")
        df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
        return df.dropna(subset=["datetime"]).set_index("datetime").sort_index()[["funding_rate"]].dropna()
    except: return pd.DataFrame()

def fetch_oi():
    try:
        r = requests.get("https://fapi.binance.com/futures/data/openInterestHist",
                         params={"symbol":"BTCUSDT","period":"1h","limit":500}, timeout=15)
        raw = r.json()
        if not isinstance(raw, list) or not raw: return pd.DataFrame()
        df = pd.DataFrame(raw)
        tc = next((c for c in df.columns if "time" in c.lower()), None)
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
# INDICATORS
# ══════════════════════════════════════════════════════════════════

def _ema(s, p): return s.ewm(span=p, adjust=False).mean()
def _rsi(c, p=14):
    d = c.diff()
    ag = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - (100 / (1 + ag / al.replace(0, np.nan)))
def _atr(df, p=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(span=p, adjust=False).mean()

def compute_indicators(df, df_4h, liq_df, fund_df, oi_df):
    df = df.copy()
    df["ema8"]   = _ema(df["close"], 8)
    df["ema21"]  = _ema(df["close"], 21)
    df["ema55"]  = _ema(df["close"], 55)
    df["ema200"] = _ema(df["close"], 200)
    df["rsi"]    = _rsi(df["close"])
    df["atr"]    = _atr(df)

    tp = (df["high"]+df["low"]+df["close"])/3
    df["vwap"] = (tp*df["volume"]).cumsum()/df["volume"].cumsum()

    hl = (df["high"]-df["low"]).replace(0,np.nan)
    df["cvd"] = (df["volume"]*((df["close"]-df["low"])/hl-(df["high"]-df["close"])/hl)).cumsum()

    vol_ma = df["volume"].rolling(20).mean()
    vol_std = df["volume"].rolling(20).std()
    df["vol_confirm"] = df["volume"] > vol_ma
    df["vol_spike"]   = df["volume"] > (vol_ma + 2*vol_std)
    df["vol_ratio"]   = df["volume"] / vol_ma

    h_v, l_v, c_v = df["high"].values, df["low"].values, df["close"].values
    ph, pl = [], []
    for i in range(3, len(df)-3):
        if h_v[i] == max(h_v[i-3:i+4]): ph.append(h_v[i])
        if l_v[i] == min(l_v[i-3:i+4]): pl.append(l_v[i])
    cur = c_v[-1]
    df["near_support"]    = pd.Series(False, index=df.index)
    df["near_resistance"] = pd.Series(False, index=df.index)
    for lvl in [v for v in pl if v < cur]:
        df["near_support"] |= (df["close"]-lvl).abs()/lvl < 0.005
    for lvl in [v for v in ph if v > cur]:
        df["near_resistance"] |= (df["close"]-lvl).abs()/lvl < 0.005

    r100 = df.tail(100)
    sh, sl = r100["high"].max(), r100["low"].min()
    diff   = sh - sl
    down   = r100["high"].idxmax() < r100["low"].idxmin()
    fib382 = sl+diff*0.382 if down else sh-diff*0.382
    fib618 = sl+diff*0.618 if down else sh-diff*0.618
    df["near_fib618"] = (df["close"]-fib618).abs()/df["close"] < 0.008
    df["near_fib382"] = (df["close"]-fib382).abs()/df["close"] < 0.008

    opens, closes = df["open"].values, df["close"].values
    atr_v = _atr(df).values
    df["in_bullish_ob"] = pd.Series(False, index=df.index)
    df["in_bearish_ob"] = pd.Series(False, index=df.index)
    for i in range(2, len(df)-2):
        a = atr_v[i]
        if np.isnan(a) or a == 0: continue
        if closes[i] < opens[i] and closes[i+2]-closes[i] > 2.0*a:
            df["in_bullish_ob"] |= (df["close"]>=min(opens[i],closes[i]))&(df["close"]<=max(opens[i],closes[i]))
        if closes[i] > opens[i] and closes[i]-closes[i+2] > 2.0*a:
            df["in_bearish_ob"] |= (df["close"]>=min(opens[i],closes[i]))&(df["close"]<=max(opens[i],closes[i]))

    rsi_v, c_vals = df["rsi"].values, df["close"].values
    rsi_bull, rsi_bear = [False]*len(df), [False]*len(df)
    for i in range(55, len(df)):
        p = i-5
        if np.isnan(rsi_v[i]) or np.isnan(rsi_v[p]): continue
        if c_vals[i] < c_vals[p] and rsi_v[i] > rsi_v[p] and rsi_v[i] < 40: rsi_bull[i] = True
        if c_vals[i] > c_vals[p] and rsi_v[i] < rsi_v[p] and rsi_v[i] > 60: rsi_bear[i] = True
    df["rsi_bull_div"], df["rsi_bear_div"] = rsi_bull, rsi_bear

    bos_up, bos_down, choch = [False]*len(df), [False]*len(df), [False]*len(df)
    trend = "neutral"
    hv2, lv2 = df["high"].values, df["low"].values
    for i in range(5, len(df)-5):
        if hv2[i] == max(hv2[i-5:i+6]):
            if trend == "bullish":   bos_up[i]  = True
            elif trend == "bearish": choch[i]   = True
            trend = "bullish"
        if lv2[i] == min(lv2[i-5:i+6]):
            if trend == "bearish":   bos_down[i] = True
            elif trend == "bullish": choch[i]    = True
            trend = "bearish"
    df["bos_up"], df["bos_down"], df["choch"] = bos_up, bos_down, choch

    if not df_4h.empty:
        e4     = _ema(df_4h["close"], 21)
        e4_200 = _ema(df_4h["close"], 200)
        bias   = (df_4h["close"] > e4) & (e4 > e4_200)
        df["bias_bullish_4h"] = bias.resample("1h").ffill().reindex(df.index, method="ffill").fillna(False)
        df["bias_bearish_4h"] = (~bias).resample("1h").ffill().reindex(df.index, method="ffill").fillna(False)
    else:
        df["bias_bullish_4h"] = False; df["bias_bearish_4h"] = False

    df["major_long_liq"] = False; df["major_short_liq"] = False
    if not liq_df.empty and "long_liq_usd" in liq_df.columns:
        lh = liq_df[["long_liq_usd","short_liq_usd"]].resample("1h").sum()
        df["major_long_liq"]  = (lh["long_liq_usd"]  > lh["long_liq_usd"].quantile(0.90)).reindex(df.index, fill_value=False)
        df["major_short_liq"] = (lh["short_liq_usd"] > lh["short_liq_usd"].quantile(0.90)).reindex(df.index, fill_value=False)

    df["funding_extreme_long"] = False; df["funding_extreme_short"] = False
    if not fund_df.empty:
        fr = fund_df["funding_rate"].resample("1h").ffill().reindex(df.index, method="ffill")
        df["funding_extreme_long"]  = fr >  0.0005
        df["funding_extreme_short"] = fr < -0.0001

    df["oi_confirm_long"] = False; df["oi_confirm_short"] = False
    if not oi_df.empty:
        oi_s  = oi_df["oi"].resample("1h").last().reindex(df.index, method="ffill")
        oi_ch = oi_s.pct_change()
        df["oi_confirm_long"]  = (df["close"].pct_change() > 0) & (oi_ch > 0.005)
        df["oi_confirm_short"] = (df["close"].pct_change() < 0) & (oi_ch > 0.005)

    return df.dropna(subset=["ema200","rsi","atr"])

def compute_scores(df):
    w  = CAT_WEIGHTS
    tl = pd.Series(0.0, index=df.index); ts = pd.Series(0.0, index=df.index)
    zl = pd.Series(0.0, index=df.index); zs = pd.Series(0.0, index=df.index)
    ml = pd.Series(0.0, index=df.index); ms = pd.Series(0.0, index=df.index)
    pl = pd.Series(0.0, index=df.index); ps = pd.Series(0.0, index=df.index)

    tl += df["bias_bullish_4h"].astype(float)*w["4h_bias"];   ts += df["bias_bearish_4h"].astype(float)*w["4h_bias"]
    bull_ema = (df["ema8"]>df["ema21"])&(df["ema21"]>df["ema55"])
    bear_ema = (df["ema8"]<df["ema21"])&(df["ema21"]<df["ema55"])
    tl += bull_ema.astype(float)*w["ema_ribbon"];              ts += bear_ema.astype(float)*w["ema_ribbon"]
    tl += (df["close"]>df["ema200"]).astype(float)*w["ema200"];ts += (df["close"]<df["ema200"]).astype(float)*w["ema200"]
    tl += df["bos_up"].astype(float)*w["bos"];                 ts += df["bos_down"].astype(float)*w["bos"]
    tl += df["choch"].astype(float)*w["choch"];                ts += df["choch"].astype(float)*w["choch"]

    zl += df["near_support"].astype(float)*w["support_resistance"];    zs += df["near_resistance"].astype(float)*w["support_resistance"]
    zl += df["near_fib618"].astype(float)*w["fib618"];                 zs += df["near_fib618"].astype(float)*w["fib618"]
    zl += df["near_fib382"].astype(float)*w["fib382"];                 zs += df["near_fib382"].astype(float)*w["fib382"]
    zl += df["in_bullish_ob"].astype(float)*w["order_blocks"];         zs += df["in_bearish_ob"].astype(float)*w["order_blocks"]
    zl += (df["close"]<df["vwap"]).astype(float)*w["vwap"];            zs += (df["close"]>df["vwap"]).astype(float)*w["vwap"]

    ml += (df["rsi"]<40).astype(float)*w["rsi"];              ms += (df["rsi"]>60).astype(float)*w["rsi"]
    ml += df["rsi_bull_div"].astype(float)*w["rsi_div"];       ms += df["rsi_bear_div"].astype(float)*w["rsi_div"]
    cvd_up = df["cvd"].diff()>0; cvd_dn = df["cvd"].diff()<0
    ml += cvd_up.astype(float)*w["cvd"];                       ms += cvd_dn.astype(float)*w["cvd"]
    ml += df["vol_confirm"].astype(float)*w["volume"];         ms += df["vol_confirm"].astype(float)*w["volume"]

    pl += df["major_long_liq"].astype(float)*w["liquidations"];        ps += df["major_short_liq"].astype(float)*w["liquidations"]
    pl += df["funding_extreme_short"].astype(float)*w["funding"];      ps += df["funding_extreme_long"].astype(float)*w["funding"]
    pl += df["oi_confirm_long"].astype(float)*w["oi"];                 ps += df["oi_confirm_short"].astype(float)*w["oi"]

    df["long_score"]  = tl+zl+ml+pl
    df["short_score"] = ts+zs+ms+ps
    df["trend_l"],  df["trend_s"]  = tl, ts
    df["zone_l"],   df["zone_s"]   = zl, zs
    df["mom_l"],    df["mom_s"]    = ml, ms
    df["pos_l"],    df["pos_s"]    = pl, ps

    def gate(tcat, zcat, mcat, pcat):
        cats_active = (tcat>0).astype(int)+(zcat>0).astype(int)+(mcat>0).astype(int)+(pcat>0).astype(int)
        score_ok    = (tcat+zcat+mcat+pcat) >= MIN_SCORE
        cats_ok     = cats_active >= MIN_CATS
        trend_ok    = (tcat>0) if TREND_REQ else pd.Series(True, index=tcat.index)
        return score_ok & cats_ok & trend_ok

    df["long_signal"]  = gate(tl, zl, ml, pl)
    df["short_signal"] = gate(ts, zs, ms, ps) & ENABLE_SHORT
    return df


# ══════════════════════════════════════════════════════════════════
# MAIN SIGNAL FUNCTION (called by all components)
# ══════════════════════════════════════════════════════════════════

def get_signal(bars=300):
    """Returns a rich signal dict. bars=300 gives ~12 days of 1H context."""
    days_needed = max(bars // 24 + 5, 15)
    df_1h  = fetch_ohlcv("hour", 1,  days_back=days_needed)
    df_4h  = fetch_ohlcv("hour", 4,  days_back=days_needed*4)
    liq_df = fetch_liquidations(max_days=3)
    fund_df= fetch_funding()
    oi_df  = fetch_oi()
    price  = get_live_price()

    if df_1h.empty:
        return {"error": "Could not fetch OHLCV data. Check API key."}

    df = compute_indicators(df_1h, df_4h, liq_df, fund_df, oi_df)
    df = compute_scores(df)

    row = df.iloc[-1]
    ls  = float(row["long_score"]);    ss  = float(row["short_score"])
    tl  = float(row["trend_l"]);       ts  = float(row["trend_s"])
    zl  = float(row["zone_l"]);        zs  = float(row["zone_s"])
    ml  = float(row["mom_l"]);         ms_ = float(row["mom_s"])
    posl= float(row["pos_l"]);         poss= float(row["pos_s"])

    lsig = bool(row["long_signal"])
    ssig = bool(row["short_signal"])

    if lsig:   direction = "LONG"
    elif ssig: direction = "SHORT"
    else:      direction = "NONE"

    atr_v = float(row["atr"])
    rsi_v = float(row["rsi"])

    entry_plan = None
    if lsig:
        entry_plan = {
            "entry":       round(price, 2),
            "stop_loss":   round(price - atr_v*ATR_SL, 2),
            "take_profit": round(price + atr_v*ATR_TP, 2),
            "rr":          round(ATR_TP/ATR_SL, 1),
        }
    elif ssig:
        entry_plan = {
            "entry":       round(price, 2),
            "stop_loss":   round(price + atr_v*ATR_SL, 2),
            "take_profit": round(price - atr_v*ATR_TP, 2),
            "rr":          round(ATR_TP/ATR_SL, 1),
        }

    # Per-indicator active reasons
    reasons_long, reasons_short = [], []
    indicator_map = [
        ("4H Bias Bullish",    bool(row.get("bias_bullish_4h")),    False),
        ("4H Bias Bearish",    False,                                bool(row.get("bias_bearish_4h"))),
        ("EMA Ribbon Bull",    bool((row["ema8"]>row["ema21"])and(row["ema21"]>row["ema55"])), False),
        ("EMA Ribbon Bear",    False, bool((row["ema8"]<row["ema21"])and(row["ema21"]<row["ema55"]))),
        ("Above EMA200",       bool(row["close"]>row["ema200"]),     False),
        ("Below EMA200",       False,                                bool(row["close"]<row["ema200"])),
        ("BOS Up",             bool(row.get("bos_up")),              False),
        ("BOS Down",           False,                                bool(row.get("bos_down"))),
        ("CHoCH",              bool(row.get("choch")),               bool(row.get("choch"))),
        ("Near Support",       bool(row.get("near_support")),        False),
        ("Near Resistance",    False,                                bool(row.get("near_resistance"))),
        ("Fib 0.618",          bool(row.get("near_fib618")),         bool(row.get("near_fib618"))),
        ("Fib 0.382",          bool(row.get("near_fib382")),         bool(row.get("near_fib382"))),
        ("Bullish OB",         bool(row.get("in_bullish_ob")),       False),
        ("Bearish OB",         False,                                bool(row.get("in_bearish_ob"))),
        ("Below VWAP",         bool(row["close"]<row["vwap"]),       False),
        ("Above VWAP",         False,                                bool(row["close"]>row["vwap"])),
        (f"RSI Oversold {rsi_v:.0f}", bool(rsi_v<40), False),
        (f"RSI Overbought {rsi_v:.0f}", False, bool(rsi_v>60)),
        ("RSI Bull Div",       bool(row.get("rsi_bull_div")),        False),
        ("RSI Bear Div",       False,                                bool(row.get("rsi_bear_div"))),
        ("CVD Rising",         bool(row.get("cvd",0)>0),             False),
        ("CVD Falling",        False,                                bool(row.get("cvd",0)<0)),
        ("Vol Confirm",        bool(row.get("vol_confirm")),         bool(row.get("vol_confirm"))),
        ("Long Liq Cascade",   bool(row.get("major_long_liq")),      False),
        ("Short Liq Cascade",  False,                                bool(row.get("major_short_liq"))),
        ("Funding Short",      bool(row.get("funding_extreme_short")),False),
        ("Funding Long",       False,                                bool(row.get("funding_extreme_long"))),
        ("OI Long Confirm",    bool(row.get("oi_confirm_long")),      False),
        ("OI Short Confirm",   False,                                bool(row.get("oi_confirm_short"))),
    ]
    for name, for_long, for_short in indicator_map:
        if for_long:  reasons_long.append(name)
        if for_short: reasons_short.append(name)

    return {
        "timestamp":     datetime.utcnow().isoformat(),
        "price":         round(price, 2),
        "direction":     direction,
        "long_score":    round(ls, 2),
        "short_score":   round(ss, 2),
        "categories": {
            "trend":       {"long": round(tl,2), "short": round(ts,2)},
            "zone":        {"long": round(zl,2), "short": round(zs,2)},
            "momentum":    {"long": round(ml,2), "short": round(ms_,2)},
            "positioning": {"long": round(posl,2),"short": round(poss,2)},
        },
        "reasons_long":  reasons_long,
        "reasons_short": reasons_short,
        "entry_plan":    entry_plan,
        "indicators": {
            "rsi":      round(rsi_v, 2),
            "atr":      round(atr_v, 2),
            "ema200":   round(float(row["ema200"]), 2),
            "vwap":     round(float(row["vwap"]), 2),
            "vol_ratio":round(float(row.get("vol_ratio",1)), 2),
        },
        "df": df,   # returned for streamlit charts — stripped before sending to Telegram
    }


# ══════════════════════════════════════════════════════════════════
# MEMORY — Save / Load / Learn
# ══════════════════════════════════════════════════════════════════

def load_memory():
    if MEMORY_FILE.exists():
        try:
            with open(MEMORY_FILE) as f:
                return json.load(f)
        except: pass
    return []

def save_to_memory(result: dict):
    mem = load_memory()
    mem.append(result)
    with open(MEMORY_FILE, "w") as f:
        json.dump(mem, f, indent=2, default=str)
    return len(mem)

def get_learning_summary():
    mem = load_memory()
    if not mem: return "No backtest history yet."
    ranked = sorted(mem, key=lambda x: x.get("sharpe_ratio", 0), reverse=True)
    best   = ranked[0]
    lines  = [
        f"📚 *Learning Report* — {len(mem)} runs",
        f"",
        f"🥇 *Best Run:* `{best['run_id']}`",
        f"   Period: {best['period']} | Return: {best['total_return']:.1%}",
        f"   Sharpe: {best['sharpe_ratio']:.3f} | WR: {best['win_rate']:.1%}",
        f"   Config: min\\_score={best['config']['min_score']} | SL={best['config']['atr_sl_mult']}x | TP={best['config']['atr_tp_mult']}x",
        f"",
        f"📊 *All Runs (by Sharpe):*",
    ]
    for i, r in enumerate(ranked[:10]):
        bh = "✅" if r.get("beat_bh") else "❌"
        lines.append(f"  {i+1}. {r['period']} | {r['total_return']:+.1%} | SR={r['sharpe_ratio']:.2f} {bh}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# GOOGLE SHEETS LOGGER (optional)
# ══════════════════════════════════════════════════════════════════

def log_trade_to_sheets(trade: dict):
    """POST a trade row to Google Sheets via Apps Script webhook."""
    if not SHEETS_URL: return
    try:
        requests.post(SHEETS_URL, json=trade, timeout=10)
    except: pass

def log_signal_to_sheets(signal: dict):
    if not SHEETS_URL: return
    row = {
        "timestamp":    signal["timestamp"],
        "price":        signal["price"],
        "direction":    signal["direction"],
        "long_score":   signal["long_score"],
        "short_score":  signal["short_score"],
        "reasons":      ", ".join(signal.get("reasons_long" if signal["direction"]=="LONG" else "reasons_short", [])),
    }
    log_trade_to_sheets(row)
