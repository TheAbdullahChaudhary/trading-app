"""
Scalping-optimized indicators.
Uses fast EMAs, VWAP, momentum, tick-pressure, and spread width.
All computed on 1-minute candles for real-time scalping decisions.
"""
from typing import Optional
import numpy as np
import pandas as pd

try:
    import ta
    HAS_TA = True
except ImportError:
    HAS_TA = False

from bot.logger import get_logger
log = get_logger("indicators")


def compute_indicators(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    Computes scalping-optimised indicators.
    Needs columns: open, high, low, close, volume
    Requires at least 60 rows.
    """
    if df is None or len(df) < 60:
        return None

    df = df.copy()
    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]
    open_ = df["open"]

    # ── Fast Trend EMAs ──────────────────────────────────────
    df["ema_5"]   = close.ewm(span=5,  adjust=False).mean()
    df["ema_9"]   = close.ewm(span=9,  adjust=False).mean()
    df["ema_21"]  = close.ewm(span=21, adjust=False).mean()
    df["ema_50"]  = close.ewm(span=50, adjust=False).mean()
    df["ema_200"] = close.ewm(span=200, adjust=False).mean()

    # ── MACD (fast settings for scalping) ───────────────────
    ema_fast = close.ewm(span=8,  adjust=False).mean()
    ema_slow = close.ewm(span=21, adjust=False).mean()
    df["macd"]        = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=5, adjust=False).mean()
    df["macd_diff"]   = df["macd"] - df["macd_signal"]

    # ── RSI (fast 7-period) ──────────────────────────────────
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(7).mean()
    loss  = (-delta.clip(upper=0)).rolling(7).mean()
    rs    = gain / (loss + 1e-9)
    df["rsi"] = 100 - (100 / (1 + rs))

    # ── RSI slope (momentum of momentum) ────────────────────
    df["rsi_slope"] = df["rsi"].diff(3)

    # ── Stochastic (5,3) ultra-fast ──────────────────────────
    low_min  = low.rolling(5).min()
    high_max = high.rolling(5).max()
    df["stoch_k"] = ((close - low_min) / (high_max - low_min + 1e-9)) * 100
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()

    # ── ATR (7-period, tighter for scalping) ─────────────────
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(7).mean()

    # ── Bollinger Bands (10,2) ───────────────────────────────
    sma10       = close.rolling(10).mean()
    std10       = close.rolling(10).std()
    df["bb_upper"] = sma10 + 2.0 * std10
    df["bb_mid"]   = sma10
    df["bb_lower"] = sma10 - 2.0 * std10
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / (sma10 + 1e-9)
    df["bb_pct"]   = (close - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"] + 1e-9)

    # ── VWAP (rolling 20 periods proxy) ──────────────────────
    typical = (high + low + close) / 3
    df["vwap"] = (typical * vol).rolling(20).sum() / (vol.rolling(20).sum() + 1e-9)
    df["price_vs_vwap"] = (close - df["vwap"]) / (df["atr"] + 1e-9)

    # ── Candle Body & Wick Analysis ──────────────────────────
    df["body"]       = (close - open_).abs()
    df["upper_wick"] = high - close.clip(lower=open_)
    df["lower_wick"] = close.clip(upper=open_) - low
    df["body_pct"]   = df["body"] / (high - low + 1e-9)
    df["candle_dir"] = np.sign(close - open_)   # +1 bullish, -1 bearish

    # ── Volume Spike ─────────────────────────────────────────
    df["vol_ma"]    = vol.rolling(20).mean()
    df["vol_ratio"] = vol / (df["vol_ma"] + 1e-9)

    # ── Momentum (Rate of Change) ─────────────────────────────
    df["roc_3"]  = close.pct_change(3)
    df["roc_5"]  = close.pct_change(5)

    # ── Price vs EMAs ────────────────────────────────────────
    df["price_vs_ema9"]   = (close - df["ema_9"])  / (df["atr"] + 1e-9)
    df["price_vs_ema21"]  = (close - df["ema_21"]) / (df["atr"] + 1e-9)
    df["ema_cross_5_9"]   = df["ema_5"]  - df["ema_9"]
    df["ema_cross_9_21"]  = df["ema_9"]  - df["ema_21"]

    # ── OBV (On-balance volume) ──────────────────────────────
    df["obv"] = (np.sign(close.diff()) * vol).fillna(0).cumsum()
    df["obv_slope"] = df["obv"].diff(5)

    df.dropna(inplace=True)
    return df


# Features used by AI model
FEATURE_COLS = [
    "rsi", "rsi_slope",
    "macd_diff", "macd",
    "stoch_k", "stoch_d",
    "bb_pct", "bb_width",
    "atr",
    "price_vs_vwap",
    "price_vs_ema9", "price_vs_ema21",
    "ema_cross_5_9", "ema_cross_9_21",
    "body_pct", "candle_dir",
    "vol_ratio",
    "roc_3", "roc_5",
    "obv_slope",
]
