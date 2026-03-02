"""
Strategy Engine
Wraps all v4 notebook logic into clean callable methods.
This is where your Cell 3-13 code lives as a proper module.
"""

import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime, timedelta
from typing import Optional


# ══════════════════════════════════════════════════════════════════
# DEFAULT CONFIG (mirrors Cell 2)
# ══════════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "cryptocompare_api_key": "YOUR_KEY_HERE",
    "symbol":           "BTC",
    "currency":         "USD",
    "backtest_period":  "3Y",
    "init_cash":        10_000.0,
    "fees":             0.001,
    "slippage":         0.0005,
    "min_score":        5.0,
    "min_categories":   2,
    "trend_required":   True,
    "atr_sl_mult":      2.0,
    "atr_tp_mult":      4.0,
    "enable_shorts":    True,
    "wfo_splits":       3,
    "wfo_is_ratio":     0.70,
    "cat_weights": {
        "4h_bias":              3.0,
        "ema_ribbon":           2.0,
        "ema200":               1.5,
        "bos":                  2.0,
        "choch":                1.0,
        "support_resistance":   2.5,
        "fib618":               2.0,
        "fib382":               1.5,
        "order_blocks":         2.5,
        "vwap":                 1.0,
        "rsi":                  1.5,
        "rsi_div":              2.5,
        "cvd":                  2.0,
        "volume":               0.5,
        "liquidations":         2.0,
        "funding":              1.5,
        "oi":                   1.5,
    }
}

PERIOD_DAYS = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "3Y": 1095}


class StrategyEngine:

    def __init__(self):
        self.config     = dict(DEFAULT_CONFIG)
        self._df_cache  = {}          # cache fetched OHLCV data
        self._cache_ts  = {}          # when each tf was last fetched
        self._liq_cache = None
        self._fr_cache  = None
        self._oi_cache  = None
        self._deriv_ts  = 0           # timestamp of last derivative fetch

    def get_config(self) -> dict:
        return self.config

    def set_config(self, config: dict):
        self.config.update(config)

    # ══════════════════════════════════════════════════════════════
    # DATA FETCHERS (same logic as Cell 3, cleaned up)
    # ══════════════════════════════════════════════════════════════

    def _fetch_ohlcv(self, unit="hour", aggregate=1, limit=2000, days_back=365) -> pd.DataFrame:
        cache_key = f"{unit}_{aggregate}"
        now = time.time()
        # Return cached data if less than 5 min old
        if cache_key in self._df_cache and now - self._cache_ts.get(cache_key, 0) < 300:
            return self._df_cache[cache_key]

        endpoints = {
            "minute": "https://min-api.cryptocompare.com/data/v2/histominute",
            "hour":   "https://min-api.cryptocompare.com/data/v2/histohour",
            "day":    "https://min-api.cryptocompare.com/data/v2/histoday",
        }
        headers = {}
        key = self.config.get("cryptocompare_api_key", "")
        if key and key != "YOUR_KEY_HERE":
            headers["authorization"] = f"Apikey {key}"

        mins_per  = {"minute": aggregate, "hour": 60*aggregate, "day": 1440*aggregate}[unit]
        total     = min(int(days_back * 1440 / mins_per), limit)
        all_data, to_ts, remaining = [], None, total

        while remaining > 0:
            params = {"fsym": self.config["symbol"], "tsym": self.config["currency"],
                      "limit": min(2000, remaining), "aggregate": aggregate}
            if to_ts:
                params["toTs"] = to_ts
            try:
                resp    = requests.get(endpoints[unit], params=params, headers=headers, timeout=15)
                data    = resp.json()
                if data.get("Response") != "Success":
                    break
                candles = data["Data"]["Data"]
                if not candles: break
                all_data  = candles + all_data
                to_ts     = candles[0]["time"] - 1
                remaining -= len(candles)
                time.sleep(0.1)
            except Exception:
                break

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data)
        df["datetime"] = pd.to_datetime(df["time"], unit="s")
        df = df.set_index("datetime").rename(columns={"volumefrom": "volume"})
        df = df[["open","high","low","close","volume"]].astype(float)
        df = df[df["volume"] > 0].sort_index()
        df = df[~df.index.duplicated(keep="last")]

        self._df_cache[cache_key] = df
        self._cache_ts[cache_key] = now
        return df

    def _fetch_liquidations(self, symbol="BTCUSDT", limit=1000, max_days=30) -> pd.DataFrame:
        now = time.time()
        if self._liq_cache is not None and now - self._deriv_ts < 3600:
            return self._liq_cache

        url        = "https://fapi.binance.com/fapi/v1/allForceOrders"
        all_orders = []
        end_ms     = int(now * 1000)
        day_ms     = 24 * 60 * 60 * 1000

        for day in range(max_days):
            start_ms = end_ms - day_ms
            try:
                resp = requests.get(url, params={"symbol": symbol, "startTime": start_ms,
                                                  "endTime": end_ms, "limit": 1000}, timeout=15)
                raw  = resp.json()
                if isinstance(raw, list):
                    all_orders.extend(raw)
                if len(all_orders) >= limit:
                    break
            except:
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
        df["long_liq_usd"]  = df.apply(lambda r: r["liq_usd"] if r["side"]=="SELL" else 0.0, axis=1)
        df["short_liq_usd"] = df.apply(lambda r: r["liq_usd"] if r["side"]=="BUY"  else 0.0, axis=1)
        df["total_liq_usd"] = df["liq_usd"]
        self._liq_cache = df
        return df

    def _fetch_funding(self, symbol="BTCUSDT", limit=1000) -> pd.DataFrame:
        now = time.time()
        if self._fr_cache is not None and now - self._deriv_ts < 3600:
            return self._fr_cache
        try:
            resp = requests.get("https://fapi.binance.com/fapi/v1/fundingRate",
                                params={"symbol": symbol, "limit": limit}, timeout=15)
            raw  = resp.json()
            if not isinstance(raw, list) or len(raw) == 0:
                return pd.DataFrame()
            df = pd.DataFrame(raw)
            time_col = next((c for c in ["fundingTime","calcTime","time"] if c in df.columns), None)
            if not time_col: return pd.DataFrame()
            df["datetime"]     = pd.to_datetime(pd.to_numeric(df[time_col], errors="coerce"), unit="ms")
            df                 = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
            df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
            self._fr_cache = df[["funding_rate"]].dropna()
            return self._fr_cache
        except:
            return pd.DataFrame()

    def _fetch_oi(self, symbol="BTCUSDT", period="1h", limit=500) -> pd.DataFrame:
        now = time.time()
        if self._oi_cache is not None and now - self._deriv_ts < 3600:
            return self._oi_cache
        try:
            resp = requests.get("https://fapi.binance.com/futures/data/openInterestHist",
                                params={"symbol": symbol, "period": period, "limit": limit}, timeout=15)
            raw  = resp.json()
            if not isinstance(raw, list) or len(raw) == 0:
                return pd.DataFrame()
            df     = pd.DataFrame(raw)
            ts_col = next((c for c in df.columns if "time" in c.lower()), None)
            if not ts_col: return pd.DataFrame()
            df["datetime"] = pd.to_datetime(pd.to_numeric(df[ts_col], errors="coerce"), unit="ms")
            df             = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
            df["oi"]       = pd.to_numeric(df["sumOpenInterest"],      errors="coerce")
            df["oi_usd"]   = pd.to_numeric(df["sumOpenInterestValue"], errors="coerce")
            self._oi_cache = df[["oi","oi_usd"]].dropna()
            self._deriv_ts = now
            return self._oi_cache
        except:
            return pd.DataFrame()

    # ══════════════════════════════════════════════════════════════
    # INDICATOR CALCULATIONS (same as Cell 5/6)
    # ══════════════════════════════════════════════════════════════

    def _ema(self, s, p):           return s.ewm(span=p, adjust=False).mean()
    def _rsi(self, c, p=14):
        d  = c.diff()
        ag = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
        al = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
        return 100 - (100 / (1 + ag / al.replace(0, np.nan)))
    def _atr(self, df, p=14):
        h, l, c = df["high"], df["low"], df["close"]
        tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
        return tr.ewm(span=p, adjust=False).mean()

    def _compute_all_indicators(self, df, df_4h, liq_df, funding_df, oi_df) -> pd.DataFrame:
        df = df.copy()

        # EMA ribbon
        df["ema8"]   = self._ema(df["close"], 8)
        df["ema21"]  = self._ema(df["close"], 21)
        df["ema55"]  = self._ema(df["close"], 55)
        df["ema200"] = self._ema(df["close"], 200)
        df["rsi"]    = self._rsi(df["close"])
        df["atr"]    = self._atr(df)

        # VWAP
        tp = (df["high"] + df["low"] + df["close"]) / 3
        df["vwap"] = (tp * df["volume"]).cumsum() / df["volume"].cumsum()

        # CVD
        hl = (df["high"] - df["low"]).replace(0, np.nan)
        df["cvd"] = (df["volume"] * ((df["close"]-df["low"])/hl - (df["high"]-df["close"])/hl)).cumsum()

        # Volume
        vol_ma = df["volume"].rolling(20).mean()
        vol_std = df["volume"].rolling(20).std()
        df["vol_confirm"] = df["volume"] > vol_ma
        df["vol_spike"]   = df["volume"] > (vol_ma + 2*vol_std)
        df["vol_ratio"]   = df["volume"] / vol_ma

        # S/R
        h, l, c = df["high"].values, df["low"].values, df["close"].values
        s, ph, pl = 3, [], []
        for i in range(s, len(df)-s):
            if h[i] == max(h[i-s:i+s+1]): ph.append(h[i])
            if l[i] == min(l[i-s:i+s+1]): pl.append(l[i])
        cur = c[-1]
        df["near_support"]    = pd.Series(False, index=df.index)
        df["near_resistance"] = pd.Series(False, index=df.index)
        for lvl in [v for v in pl if v < cur]:
            df["near_support"] |= (df["close"]-lvl).abs()/lvl < 0.005
        for lvl in [v for v in ph if v > cur]:
            df["near_resistance"] |= (df["close"]-lvl).abs()/lvl < 0.005

        # Fibonacci
        r = df.tail(100)
        sh, sl = r["high"].max(), r["low"].min()
        diff = sh - sl
        down = r["high"].idxmax() < r["low"].idxmin()
        fib382 = sl + diff*0.382 if down else sh - diff*0.382
        fib618 = sl + diff*0.618 if down else sh - diff*0.618
        df["near_fib618"] = (df["close"]-fib618).abs()/df["close"] < 0.008
        df["near_fib382"] = (df["close"]-fib382).abs()/df["close"] < 0.008

        # Order blocks
        opens, closes, highs, lows = df["open"].values, df["close"].values, df["high"].values, df["low"].values
        atr_v = self._atr(df).values
        df["in_bullish_ob"] = pd.Series(False, index=df.index)
        df["in_bearish_ob"] = pd.Series(False, index=df.index)
        for i in range(2, len(df)-2):
            a = atr_v[i]
            if np.isnan(a) or a == 0: continue
            if closes[i] < opens[i] and closes[i+2] - closes[i] > 2.0*a:
                m = (opens[i]+closes[i])/2
                df["in_bullish_ob"] |= (df["close"] >= min(opens[i],closes[i])) & (df["close"] <= max(opens[i],closes[i]))
            if closes[i] > opens[i] and closes[i] - closes[i+2] > 2.0*a:
                df["in_bearish_ob"] |= (df["close"] >= min(opens[i],closes[i])) & (df["close"] <= max(opens[i],closes[i]))

        # RSI divergence
        rsi_v = df["rsi"].values
        df["rsi_bull_div"] = pd.Series(False, index=df.index)
        df["rsi_bear_div"] = pd.Series(False, index=df.index)
        for i in range(55, len(df)):
            p = i - 5
            if np.isnan(rsi_v[i]) or np.isnan(rsi_v[p]): continue
            if c[i] < c[p] and rsi_v[i] > rsi_v[p] and rsi_v[i] < 40: df.loc[df.index[i], "rsi_bull_div"] = True
            if c[i] > c[p] and rsi_v[i] < rsi_v[p] and rsi_v[i] > 60: df.loc[df.index[i], "rsi_bear_div"] = True

        # Market structure
        df["bos_up"] = pd.Series(False, index=df.index)
        df["bos_down"] = pd.Series(False, index=df.index)
        df["choch"] = pd.Series(False, index=df.index)
        trend, lsh, lsl = "neutral", h[0], l[0]
        idx = df.index
        for i in range(5, len(df)-5):
            if h[i] == max(h[i-5:i+6]):
                if trend == "bullish":  df.loc[idx[i], "bos_up"]  = True
                elif trend == "bearish":df.loc[idx[i], "choch"]   = True
                trend = "bullish"; lsh = h[i]
            if l[i] == min(l[i-5:i+6]):
                if trend == "bearish":  df.loc[idx[i], "bos_down"] = True
                elif trend == "bullish":df.loc[idx[i], "choch"]    = True
                trend = "bearish"; lsl = l[i]

        # 4H trend bias
        e4 = self._ema(df_4h["close"], 21) if not df_4h.empty else None
        e4_200 = self._ema(df_4h["close"], 200) if not df_4h.empty else None
        if e4 is not None:
            bias = (df_4h["close"] > e4) & (e4 > e4_200)
            df["bias_bullish_4h"] = bias.resample("1h").ffill().reindex(df.index, method="ffill").fillna(False)
            df["bias_bearish_4h"] = (~bias).resample("1h").ffill().reindex(df.index, method="ffill").fillna(False)
        else:
            df["bias_bullish_4h"] = False
            df["bias_bearish_4h"] = False

        # Liquidations
        df["major_long_liq"]  = pd.Series(False, index=df.index)
        df["major_short_liq"] = pd.Series(False, index=df.index)
        if not liq_df.empty and "long_liq_usd" in liq_df.columns:
            lh = liq_df[["long_liq_usd","short_liq_usd"]].resample("1h").sum()
            lt = lh["long_liq_usd"].quantile(0.90)
            st = lh["short_liq_usd"].quantile(0.90)
            df["major_long_liq"]  = (lh["long_liq_usd"]  > lt).reindex(df.index, fill_value=False)
            df["major_short_liq"] = (lh["short_liq_usd"] > st).reindex(df.index, fill_value=False)

        # Funding rate
        df["funding_extreme_long"]  = pd.Series(False, index=df.index)
        df["funding_extreme_short"] = pd.Series(False, index=df.index)
        if not funding_df.empty:
            fr  = funding_df["funding_rate"].resample("1h").ffill().reindex(df.index, method="ffill")
            df["funding_extreme_long"]  = fr >  0.0005
            df["funding_extreme_short"] = fr < -0.0001

        # OI
        df["oi_confirm_long"]  = pd.Series(False, index=df.index)
        df["oi_confirm_short"] = pd.Series(False, index=df.index)
        if not oi_df.empty:
            oi  = oi_df["oi"].resample("1h").last().reindex(df.index, method="ffill")
            oic = oi.pct_change()
            df["oi_confirm_long"]  = (df["close"].pct_change() > 0) & (oic > 0.005)
            df["oi_confirm_short"] = (df["close"].pct_change() < 0) & (oic > 0.005)

        return df.dropna(subset=["ema200","rsi","atr"])

    # ══════════════════════════════════════════════════════════════
    # SCORING (same as Cell 7 / Cell 12 recompute_tiered)
    # ══════════════════════════════════════════════════════════════

    def _score(self, df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
        w  = cfg["cat_weights"]
        ms = cfg["min_score"]
        mc = cfg["min_categories"]
        tr = cfg["trend_required"]
        sh = cfg["enable_shorts"]

        trend_l = pd.Series(0.0, index=df.index)
        trend_s = pd.Series(0.0, index=df.index)
        zone_l  = pd.Series(0.0, index=df.index)
        zone_s  = pd.Series(0.0, index=df.index)
        mom_l   = pd.Series(0.0, index=df.index)
        mom_s   = pd.Series(0.0, index=df.index)
        pos_l   = pd.Series(0.0, index=df.index)
        pos_s   = pd.Series(0.0, index=df.index)

        trend_l += df["bias_bullish_4h"].astype(float) * w.get("4h_bias", 0)
        trend_s += df["bias_bearish_4h"].astype(float) * w.get("4h_bias", 0)
        bull_ema = (df["ema8"]>df["ema21"]) & (df["ema21"]>df["ema55"])
        bear_ema = (df["ema8"]<df["ema21"]) & (df["ema21"]<df["ema55"])
        trend_l += bull_ema.astype(float) * w.get("ema_ribbon", 0)
        trend_s += bear_ema.astype(float) * w.get("ema_ribbon", 0)
        trend_l += (df["close"] > df["ema200"]).astype(float) * w.get("ema200", 0)
        trend_s += (df["close"] < df["ema200"]).astype(float) * w.get("ema200", 0)
        trend_l += df["bos_up"].astype(float)   * w.get("bos", 0)
        trend_s += df["bos_down"].astype(float) * w.get("bos", 0)
        trend_l += df["choch"].astype(float) * w.get("choch", 0)
        trend_s += df["choch"].astype(float) * w.get("choch", 0)

        zone_l += df["near_support"].astype(float)    * w.get("support_resistance", 0)
        zone_s += df["near_resistance"].astype(float) * w.get("support_resistance", 0)
        zone_l += df["near_fib618"].astype(float) * w.get("fib618", 0)
        zone_s += df["near_fib618"].astype(float) * w.get("fib618", 0)
        zone_l += df["near_fib382"].astype(float) * w.get("fib382", 0)
        zone_s += df["near_fib382"].astype(float) * w.get("fib382", 0)
        zone_l += df["in_bullish_ob"].astype(float) * w.get("order_blocks", 0)
        zone_s += df["in_bearish_ob"].astype(float) * w.get("order_blocks", 0)
        zone_l += (df["close"] < df["vwap"]).astype(float) * w.get("vwap", 0)
        zone_s += (df["close"] > df["vwap"]).astype(float) * w.get("vwap", 0)

        mom_l += (df["rsi"] < 40).astype(float) * w.get("rsi", 0)
        mom_s += (df["rsi"] > 60).astype(float) * w.get("rsi", 0)
        mom_l += df["rsi_bull_div"].astype(float) * w.get("rsi_div", 0)
        mom_s += df["rsi_bear_div"].astype(float) * w.get("rsi_div", 0)
        cvd_b = (df["close"].pct_change(3) < 0) & (df["cvd"].diff(3) > 0)
        cvd_s = (df["close"].pct_change(3) > 0) & (df["cvd"].diff(3) < 0)
        mom_l += cvd_b.astype(float) * w.get("cvd", 0)
        mom_s += cvd_s.astype(float) * w.get("cvd", 0)
        mom_l += df["vol_confirm"].astype(float) * w.get("volume", 0)
        mom_s += df["vol_confirm"].astype(float) * w.get("volume", 0)

        pos_l += df["major_long_liq"].astype(float)        * w.get("liquidations", 0)
        pos_s += df["major_short_liq"].astype(float)       * w.get("liquidations", 0)
        pos_l += df["funding_extreme_short"].astype(float) * w.get("funding", 0)
        pos_s += df["funding_extreme_long"].astype(float)  * w.get("funding", 0)
        pos_l += df["oi_confirm_long"].astype(float)        * w.get("oi", 0)
        pos_s += df["oi_confirm_short"].astype(float)       * w.get("oi", 0)

        ls = trend_l + zone_l + mom_l + pos_l
        ss = trend_s + zone_s + mom_s + pos_s

        cnt_l = (trend_l>0).astype(int)+(zone_l>0).astype(int)+(mom_l>0).astype(int)+(pos_l>0).astype(int)
        cnt_s = (trend_s>0).astype(int)+(zone_s>0).astype(int)+(mom_s>0).astype(int)+(pos_s>0).astype(int)
        gate_l = cnt_l >= mc
        gate_s = cnt_s >= mc
        if tr:
            gate_l = gate_l & (trend_l > 0)
            gate_s = gate_s & (trend_s > 0)

        df = df.copy()
        df["long_score"]  = ls;  df["short_score"] = ss
        df["score_trend_l"] = trend_l; df["score_trend_s"] = trend_s
        df["score_zone_l"]  = zone_l;  df["score_zone_s"]  = zone_s
        df["score_mom_l"]   = mom_l;   df["score_mom_s"]   = mom_s
        df["score_pos_l"]   = pos_l;   df["score_pos_s"]   = pos_s
        df["long_signal"]   = gate_l & (ls >= ms)
        df["short_signal"]  = (gate_s & (ss >= ms)) if sh else pd.Series(False, index=df.index)
        both = df["long_signal"] & df["short_signal"]
        df.loc[both,"long_signal"]  = df.loc[both,"long_score"] > df.loc[both,"short_score"]
        df.loc[both,"short_signal"] = ~df.loc[both,"long_signal"]
        return df

    # ══════════════════════════════════════════════════════════════
    # PUBLIC METHODS — called by API endpoints
    # ══════════════════════════════════════════════════════════════

    def _load_data(self, cfg: dict):
        """Load all data needed for scoring/backtest."""
        days = PERIOD_DAYS.get(cfg.get("backtest_period","3Y"), 1095)
        df     = self._fetch_ohlcv("hour",   1, 2000, days)
        df_4h  = self._fetch_ohlcv("hour",   4, 2000, days)
        liq    = self._fetch_liquidations()
        fr     = self._fetch_funding()
        oi     = self._fetch_oi()
        df     = self._compute_all_indicators(df, df_4h, liq, fr, oi)
        return df, liq, fr, oi

    def get_current_signal(self) -> dict:
        """Returns current bar signal with full breakdown for the Live Signal page."""
        cfg = self.config
        df, liq, fr, oi = self._load_data(cfg)
        df  = self._score(df, cfg)
        row = df.iloc[-1]

        # Build explanation list
        explanations = []
        cats = {
            "Trend":       (row.get("score_trend_l",0), row.get("score_trend_s",0)),
            "Zone":        (row.get("score_zone_l",0),  row.get("score_zone_s",0)),
            "Momentum":    (row.get("score_mom_l",0),   row.get("score_mom_s",0)),
            "Positioning": (row.get("score_pos_l",0),   row.get("score_pos_s",0)),
        }

        active_reasons_long  = []
        active_reasons_short = []
        if row.get("bias_bullish_4h"):   active_reasons_long.append("4H bias bullish")
        if row.get("bias_bearish_4h"):   active_reasons_short.append("4H bias bearish")
        if row.get("ema8",0) > row.get("ema21",0) > row.get("ema55",0): active_reasons_long.append("EMA ribbon aligned bull")
        if row.get("ema8",0) < row.get("ema21",0) < row.get("ema55",0): active_reasons_short.append("EMA ribbon aligned bear")
        if row.get("close",0) > row.get("ema200",0): active_reasons_long.append("Price above EMA200")
        if row.get("close",0) < row.get("ema200",0): active_reasons_short.append("Price below EMA200")
        if row.get("bos_up"):    active_reasons_long.append("Bullish BOS (market structure break)")
        if row.get("bos_down"):  active_reasons_short.append("Bearish BOS (market structure break)")
        if row.get("choch"):
            active_reasons_long.append("CHoCH detected")
            active_reasons_short.append("CHoCH detected")
        if row.get("near_support"):    active_reasons_long.append("Price near key support level")
        if row.get("near_resistance"): active_reasons_short.append("Price near key resistance level")
        if row.get("near_fib618"):
            active_reasons_long.append("At 0.618 Fibonacci level")
            active_reasons_short.append("At 0.618 Fibonacci level")
        if row.get("near_fib382"):     active_reasons_long.append("At 0.382 Fibonacci level")
        if row.get("in_bullish_ob"):   active_reasons_long.append("Inside bullish order block")
        if row.get("in_bearish_ob"):   active_reasons_short.append("Inside bearish order block")
        if row.get("close",0) < row.get("vwap",0): active_reasons_long.append("Price below VWAP (value zone)")
        if row.get("close",0) > row.get("vwap",0): active_reasons_short.append("Price above VWAP")
        if row.get("rsi",50) < 40:     active_reasons_long.append(f"RSI oversold ({row.get('rsi',0):.1f})")
        if row.get("rsi",50) > 60:     active_reasons_short.append(f"RSI overbought ({row.get('rsi',0):.1f})")
        if row.get("rsi_bull_div"):    active_reasons_long.append("RSI bullish divergence")
        if row.get("rsi_bear_div"):    active_reasons_short.append("RSI bearish divergence")
        if row.get("major_long_liq"):  active_reasons_long.append("Major long liquidation cascade (reversal signal)")
        if row.get("major_short_liq"): active_reasons_short.append("Major short liquidation cascade")
        if row.get("funding_extreme_short"): active_reasons_long.append("Funding rate negative (crowded shorts)")
        if row.get("funding_extreme_long"):  active_reasons_short.append("Funding rate elevated (crowded longs)")
        if row.get("oi_confirm_long"):  active_reasons_long.append("OI rising with price (institutional buying)")
        if row.get("oi_confirm_short"): active_reasons_short.append("OI rising as price falls (institutional selling)")

        direction = "none"
        if   row.get("long_signal"):  direction = "long"
        elif row.get("short_signal"): direction = "short"

        # Suggested entry, SL, TP
        atr   = float(row.get("atr", 0))
        price = float(row.get("close", 0))
        sl_dist = atr * cfg["atr_sl_mult"]
        tp_dist = atr * cfg["atr_tp_mult"]

        entry_info = None
        if direction == "long":
            entry_info = {
                "entry": round(price, 2),
                "stop_loss": round(price - sl_dist, 2),
                "take_profit": round(price + tp_dist, 2),
                "risk_usd": round(sl_dist / price * cfg["init_cash"] * 0.015, 2),
                "rr_ratio": round(tp_dist / sl_dist, 2),
            }
        elif direction == "short":
            entry_info = {
                "entry": round(price, 2),
                "stop_loss": round(price + sl_dist, 2),
                "take_profit": round(price - tp_dist, 2),
                "risk_usd": round(sl_dist / price * cfg["init_cash"] * 0.015, 2),
                "rr_ratio": round(tp_dist / sl_dist, 2),
            }

        raw = {
            "timestamp":   datetime.utcnow().isoformat(),
            "price":       price,
            "direction":   direction,
            "long_score":  round(float(row.get("long_score",  0)), 2),
            "short_score": round(float(row.get("short_score", 0)), 2),
            "min_score":   cfg["min_score"],
            "categories": {
                "trend":       {"long": round(float(cats["Trend"][0]),2),       "short": round(float(cats["Trend"][1]),2),       "active": bool(cats["Trend"][0] > 0 or cats["Trend"][1] > 0)},
                "zone":        {"long": round(float(cats["Zone"][0]),2),        "short": round(float(cats["Zone"][1]),2),        "active": bool(cats["Zone"][0] > 0 or cats["Zone"][1] > 0)},
                "momentum":    {"long": round(float(cats["Momentum"][0]),2),    "short": round(float(cats["Momentum"][1]),2),    "active": bool(cats["Momentum"][0] > 0 or cats["Momentum"][1] > 0)},
                "positioning": {"long": round(float(cats["Positioning"][0]),2), "short": round(float(cats["Positioning"][1]),2), "active": bool(cats["Positioning"][0] > 0 or cats["Positioning"][1] > 0)},
            },
            "reasons_long":  active_reasons_long,
            "reasons_short": active_reasons_short,
            "entry_info":    entry_info,
            "indicators": {
                "rsi":     round(float(row.get("rsi",  50)), 2),
                "atr":     round(atr, 2),
                "ema8":    round(float(row.get("ema8",  0)), 2),
                "ema21":   round(float(row.get("ema21", 0)), 2),
                "ema55":   round(float(row.get("ema55", 0)), 2),
                "ema200":  round(float(row.get("ema200",0)), 2),
                "vwap":    round(float(row.get("vwap",  0)), 2),
                "vol_ratio": round(float(row.get("vol_ratio", 1)), 2),
            }
        }
        # Sanitise all numpy types so json.dumps never fails
        return _sanitise(raw)

    def get_live_tick(self) -> dict:
        """Lightweight tick for WebSocket — just price + score."""
        try:
            resp  = requests.get(
                "https://min-api.cryptocompare.com/data/price",
                params={"fsym":"BTC","tsyms":"USD"},
                timeout=5
            )
            price = float(resp.json().get("USD", 0))
        except:
            price = 0.0

        # Return last known scores without re-running full indicator stack
        cached = self._df_cache.get("hour_1")
        ls = ss = 0.0
        if cached is not None and not cached.empty:
            try:
                df = self._score(cached.tail(500), self.config)
                ls = float(df["long_score"].iloc[-1])
                ss = float(df["short_score"].iloc[-1])
            except:
                pass

        signal = "none"
        if   ls >= self.config["min_score"] and ls > ss: signal = "long"
        elif ss >= self.config["min_score"] and ss > ls: signal = "short"

        return {
            "price":      round(price, 2),
            "long_score": round(ls, 2),
            "short_score":round(ss, 2),
            "signal":     signal,
        }

    def run_backtest(self, cfg_override: dict = None) -> dict:
        """Run full backtest, return results dict for the API."""
        import logging
        log = logging.getLogger("btc_dashboard")

        try:
            import vectorbt as vbt
        except ImportError:
            return {"error": "vectorbt not installed. Run: pip install vectorbt"}

        cfg = {**self.config, **(cfg_override or {})}

        log.info("Loading data...")
        df, liq, fr, oi = self._load_data(cfg)

        if df.empty:
            return {"error": "No OHLCV data returned — check your CryptoCompare API key in strategy_engine.py"}

        log.info(f"Data loaded: {len(df)} candles from {df.index[0]} to {df.index[-1]}")
        log.info("Scoring signals...")
        df = self._score(df, cfg)

        long_count  = int(df["long_signal"].sum())
        short_count = int(df["short_signal"].sum())
        log.info(f"Signals: {long_count} long, {short_count} short")

        if long_count + short_count == 0:
            return {"error": f"No signals generated — try lowering min_score (currently {cfg['min_score']}) or min_categories (currently {cfg['min_categories']})"}

        sl_stop = df["atr"] * cfg["atr_sl_mult"] / df["close"]
        tp_stop = df["atr"] * cfg["atr_tp_mult"] / df["close"]

        log.info("Running vectorbt portfolio...")
        pf = vbt.Portfolio.from_signals(
            close=df["close"], high=df["high"], low=df["low"],
            entries=df["long_signal"], short_entries=df["short_signal"],
            sl_stop=sl_stop, tp_stop=tp_stop,
            init_cash=cfg["init_cash"], fees=cfg["fees"],
            slippage=cfg["slippage"], freq="1h"
        )

        # ── Trades ──────────────────────────────────────────────────
        trades = []
        wr = aw = al = pf_ratio = 0
        try:
            # vectorbt API differs between versions — try both
            try:
                trades_df = pf.trades.records_readable
            except AttributeError:
                trades_df = pf.trades.to_pd()

            # Normalise column names (vectorbt changed them across versions)
            col_map = {}
            for c in trades_df.columns:
                cl = c.lower()
                if "entry" in cl and "time" in cl and "Entry Timestamp" not in trades_df.columns:
                    col_map[c] = "Entry Timestamp"
                elif "exit" in cl and "time" in cl and "Exit Timestamp" not in trades_df.columns:
                    col_map[c] = "Exit Timestamp"
                elif "entry" in cl and "price" in cl and "Entry Price" not in trades_df.columns:
                    col_map[c] = "Entry Price"
                elif "exit" in cl and "price" in cl and "Exit Price" not in trades_df.columns:
                    col_map[c] = "Exit Price"
                elif c.lower() in ["pnl","profit"] and "PnL" not in trades_df.columns:
                    col_map[c] = "PnL"
                elif "return" in cl and "Return" not in trades_df.columns:
                    col_map[c] = "Return"
                elif "direction" in cl and "Direction" not in trades_df.columns:
                    col_map[c] = "Direction"
            if col_map:
                trades_df = trades_df.rename(columns=col_map)

            # Ensure Direction column exists
            if "Direction" not in trades_df.columns:
                if "Size" in trades_df.columns:
                    trades_df["Direction"] = trades_df["Size"].apply(lambda x: "Long" if x > 0 else "Short")
                else:
                    trades_df["Direction"] = "Long"

            # Convert timestamps to strings
            for tc in ["Entry Timestamp", "Exit Timestamp"]:
                if tc in trades_df.columns:
                    trades_df[tc] = trades_df[tc].astype(str)

            pnl_col = "PnL" if "PnL" in trades_df.columns else None
            if pnl_col:
                wins   = len(trades_df[trades_df[pnl_col] > 0])
                losses = len(trades_df[trades_df[pnl_col] < 0])
                wr     = wins / len(trades_df) if len(trades_df) > 0 else 0
                aw     = float(trades_df[trades_df[pnl_col] > 0][pnl_col].mean()) if wins   > 0 else 0
                al     = float(trades_df[trades_df[pnl_col] < 0][pnl_col].mean()) if losses > 0 else 0
                pf_ratio = abs(aw / al) if al != 0 else 0

            trades = trades_df.to_dict(orient="records")
            log.info(f"Trades parsed: {len(trades)}, WR={wr:.1%}")
        except Exception as te:
            log.warning(f"Trade parsing failed (non-fatal): {te}")
            trades = []

        # ── Equity curve ────────────────────────────────────────────
        equity = pf.value().reset_index()
        equity.columns = ["timestamp", "value"]
        equity["timestamp"] = equity["timestamp"].astype(str)
        # Downsample to max 500 points for the chart
        if len(equity) > 500:
            step = len(equity) // 500
            equity = equity.iloc[::step].reset_index(drop=True)

        # ── Monthly returns ─────────────────────────────────────────
        try:
            # "ME" is month-end in pandas >= 2.2, "M" works on older versions
            try:
                monthly = pf.returns().resample("ME").apply(lambda x: float((1+x).prod()-1))
            except Exception:
                monthly = pf.returns().resample("M").apply(lambda x: float((1+x).prod()-1))
            monthly_list = [{"month": str(k.date()), "return": round(v, 4)} for k, v in monthly.items()]
        except Exception as me:
            log.warning(f"Monthly returns failed (non-fatal): {me}")
            monthly_list = []

        result = {
            "status":        "ok",
            "period":        cfg["backtest_period"],
            "start":         str(df.index[0].date()),
            "end":           str(df.index[-1].date()),
            "total_return":  round(float(pf.total_return()), 4),
            "sharpe_ratio":  round(float(pf.sharpe_ratio()  or 0), 3),
            "sortino_ratio": round(float(pf.sortino_ratio() or 0), 3),
            "max_drawdown":  round(float(pf.max_drawdown()), 4),
            "total_trades":  int(pf.trades.count()),
            "win_rate":      round(wr, 4),
            "avg_win":       round(aw, 2),
            "avg_loss":      round(al, 2),
            "profit_factor": round(pf_ratio, 3),
            "final_value":   round(float(pf.final_value()), 2),
            "init_cash":     cfg["init_cash"],
            "trades":        trades,
            "equity_curve":  equity.to_dict(orient="records"),
            "monthly":       monthly_list,
            "config":        cfg,
        }
        log.info(f"Backtest result: return={result['total_return']:.2%} sharpe={result['sharpe_ratio']}")
        return result

    def run_wfo(self, cfg_override: dict = None) -> dict:
        """Run walk-forward optimization."""
        try:
            import vectorbt as vbt
        except ImportError:
            return {"error": "vectorbt not installed"}

        cfg    = {**self.config, **(cfg_override or {})}
        df, *_ = self._load_data(cfg)

        param_grid = {"min_score": [4.0,5.0,6.0,7.0,8.0], "atr_sl_mult": [1.5,2.0,2.5], "atr_tp_mult": [3.0,4.0,5.0]}
        n_splits   = cfg.get("wfo_splits", 3)
        is_ratio   = cfg.get("wfo_is_ratio", 0.70)
        n, w       = len(df), len(df) // (n_splits + 1)
        splits     = []

        for sp in range(n_splits):
            start  = sp * w
            end    = min(start + w * (n_splits + 1 - sp) // n_splits, n)
            is_end = start + int((end - start) * is_ratio)
            dis    = df.iloc[start:is_end].copy()
            dos    = df.iloc[is_end:end].copy()
            if len(dis) < 500 or len(dos) < 200: continue

            best_sr, best_p = -np.inf, {"min_score": 5.0, "atr_sl_mult": 2.0, "atr_tp_mult": 4.0}
            for ms in param_grid["min_score"]:
                for sl in param_grid["atr_sl_mult"]:
                    for tp in param_grid["atr_tp_mult"]:
                        try:
                            c2 = {**cfg, "min_score": ms, "atr_sl_mult": sl, "atr_tp_mult": tp}
                            ds = self._score(dis, c2)
                            pfi = vbt.Portfolio.from_signals(
                                close=ds["close"], high=ds["high"], low=ds["low"],
                                entries=ds["long_signal"], short_entries=ds["short_signal"],
                                sl_stop=ds["atr"]*sl/ds["close"], tp_stop=ds["atr"]*tp/ds["close"],
                                init_cash=cfg["init_cash"], fees=cfg["fees"], slippage=cfg["slippage"], freq="1h"
                            )
                            sr = float(pfi.sharpe_ratio() or -999)
                            if sr > best_sr and pfi.trades.count() >= 5:
                                best_sr = sr; best_p = {"min_score":ms,"atr_sl_mult":sl,"atr_tp_mult":tp}
                        except: pass

            try:
                def rpf(d, p):
                    c2 = {**cfg, **p}
                    ds = self._score(d, c2)
                    return vbt.Portfolio.from_signals(
                        close=ds["close"], high=ds["high"], low=ds["low"],
                        entries=ds["long_signal"], short_entries=ds["short_signal"],
                        sl_stop=ds["atr"]*p["atr_sl_mult"]/ds["close"],
                        tp_stop=ds["atr"]*p["atr_tp_mult"]/ds["close"],
                        init_cash=cfg["init_cash"], fees=cfg["fees"], slippage=cfg["slippage"], freq="1h"
                    )
                pis = rpf(dis, best_p); pos = rpf(dos, best_p)
                isr  = float(pis.sharpe_ratio() or 0)
                oosr = float(pos.sharpe_ratio() or 0)
                wfe  = oosr / isr if isr > 0 else 0
                splits.append({
                    "split": sp+1,
                    "is_start":   str(dis.index[0].date()),
                    "is_end":     str(dis.index[-1].date()),
                    "oos_start":  str(dos.index[0].date()),
                    "oos_end":    str(dos.index[-1].date()),
                    "is_return":  round(pis.total_return(), 4),
                    "oos_return": round(pos.total_return(), 4),
                    "is_sharpe":  round(isr, 3),
                    "oos_sharpe": round(oosr, 3),
                    "oos_max_dd": round(float(pos.max_drawdown()), 4),
                    "wf_efficiency": round(wfe, 4),
                    "best_params": best_p,
                    "is_trades":  pis.trades.count(),
                    "oos_trades": pos.trades.count(),
                })
            except: pass

        avg_wfe = float(np.mean([s["wf_efficiency"] for s in splits])) if splits else 0
        return {
            "splits":           splits,
            "avg_oos_return":   round(float(np.mean([s["oos_return"] for s in splits])),4) if splits else 0,
            "avg_oos_sharpe":   round(float(np.mean([s["oos_sharpe"] for s in splits])),3) if splits else 0,
            "avg_wf_efficiency":round(avg_wfe, 4),
            "verdict": "ROBUST" if avg_wfe >= 0.70 else ("MODERATE" if avg_wfe >= 0.40 else "OVERFIT"),
        }

    def run_ablation(self, cfg_override: dict = None) -> dict:
        """Run ablation study — disable each indicator one at a time."""
        try:
            import vectorbt as vbt
        except ImportError:
            return {"error": "vectorbt not installed"}

        cfg = {**self.config, **(cfg_override or {})}
        df, *_ = self._load_data(cfg)
        df_scored = self._score(df, cfg)

        def _run_pf(d):
            return vbt.Portfolio.from_signals(
                close=d["close"], high=d["high"], low=d["low"],
                entries=d["long_signal"], short_entries=d["short_signal"],
                sl_stop=d["atr"]*cfg["atr_sl_mult"]/d["close"],
                tp_stop=d["atr"]*cfg["atr_tp_mult"]/d["close"],
                init_cash=cfg["init_cash"], fees=cfg["fees"], slippage=cfg["slippage"], freq="1h"
            )

        base_pf = _run_pf(df_scored)
        base_sr = float(base_pf.sharpe_ratio() or 0)
        results = []

        for ind in cfg["cat_weights"]:
            if cfg["cat_weights"][ind] == 0: continue
            w2 = {**cfg["cat_weights"], ind: 0.0}
            c2 = {**cfg, "cat_weights": w2}
            try:
                ds  = self._score(df, c2)
                pft = _run_pf(ds)
                sr  = float(pft.sharpe_ratio() or 0)
                results.append({
                    "indicator":      ind,
                    "weight":         cfg["cat_weights"][ind],
                    "sharpe_without": round(sr, 4),
                    "delta_sharpe":   round(base_sr - sr, 4),
                    "ret_without":    round(pft.total_return(), 4),
                    "trades_without": int(pft.trades.count()),
                    "contribution":   "valuable" if base_sr-sr > 0.1 else ("harmful" if base_sr-sr < -0.1 else "neutral"),
                })
            except: pass

        results.sort(key=lambda x: x["delta_sharpe"], reverse=True)
        return {
            "baseline_sharpe": round(base_sr, 4),
            "baseline_trades": int(base_pf.trades.count()),
            "indicators":      results,
        }
