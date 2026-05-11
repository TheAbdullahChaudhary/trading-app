"""Unit tests for indicators module."""
import pandas as pd
import numpy as np
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from bot.indicators import compute_indicators, FEATURE_COLS


def make_df(n=200):
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        "open":   close * 0.999,
        "high":   close * 1.002,
        "low":    close * 0.997,
        "close":  close,
        "volume": np.random.randint(1000, 5000, n).astype(float),
    })
    return df


def test_indicators_run():
    df = make_df(200)
    result = compute_indicators(df)
    assert result is not None
    assert len(result) > 0


def test_all_features_present():
    df = make_df(200)
    result = compute_indicators(df)
    for col in FEATURE_COLS:
        assert col in result.columns, f"Missing feature: {col}"


def test_rsi_range():
    df = make_df(200)
    result = compute_indicators(df)
    assert result["rsi"].between(0, 100).all(), "RSI out of range"


def test_short_data_returns_none():
    df = make_df(30)
    result = compute_indicators(df)
    assert result is None


def test_no_nans():
    df = make_df(200)
    result = compute_indicators(df)
    for col in FEATURE_COLS:
        assert not result[col].isnull().any(), f"NaN found in {col}"
