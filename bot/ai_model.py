"""LightGBM-based AI signal generator with feature importance and incremental retraining."""
import os
import threading
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from bot.indicators import FEATURE_COLS, compute_indicators
from bot.logger import get_logger

log = get_logger("ai_model")

try:
    import lightgbm as lgb
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import LabelEncoder
    import joblib
    HAS_LGB = True
except ImportError:
    HAS_LGB = False
    log.warning("LightGBM not found – rule-based mode only")


class AIModel:
    SIGNAL_MAP = {0: "BUY", 1: "HOLD", 2: "SELL"}
    MODEL_DIR = "models"

    def __init__(self, min_confidence: float = 0.60, lookback: int = 300):
        self.min_confidence = min_confidence
        self.lookback = lookback
        self._models: dict = {}
        self._feature_importance: dict = {}  # sym -> list of (feature, importance)
        self._lock = threading.Lock()
        os.makedirs(self.MODEL_DIR, exist_ok=True)

    # ─────────────────── LABEL ────────────────────

    def _make_labels(self, df: pd.DataFrame, threshold: float = 0.003) -> pd.Series:
        """
        Forward-return label:
          BUY  (0) if next close > current * (1 + threshold)
          SELL (2) if next close < current * (1 - threshold)
          HOLD (1) otherwise
        """
        fwd = df["close"].pct_change(1).shift(-1)
        labels = pd.Series(1, index=df.index)  # default HOLD
        labels[fwd >  threshold] = 0  # BUY
        labels[fwd < -threshold] = 2  # SELL
        return labels

    # ─────────────────── TRAIN ────────────────────

    def train(self, symbol: str, df: pd.DataFrame) -> bool:
        if not HAS_LGB:
            return False
        try:
            df_ind = compute_indicators(df)
            if df_ind is None or len(df_ind) < 100:
                log.warning(f"Not enough data to train {symbol}")
                return False

            df_ind = df_ind.tail(self.lookback)
            labels = self._make_labels(df_ind)
            df_ind = df_ind.loc[labels.index]
            X = df_ind[FEATURE_COLS].values
            y = labels.values[:-1]  # drop last (no forward return)
            X = X[:-1]

            X_train, X_val, y_train, y_val = train_test_split(
                X, y, test_size=0.15, shuffle=False
            )
            train_data = lgb.Dataset(X_train, label=y_train)
            val_data   = lgb.Dataset(X_val, label=y_val, reference=train_data)

            params = {
                "objective": "multiclass",
                "num_class": 3,
                "learning_rate": 0.05,
                "num_leaves": 31,
                "n_estimators": 200,
                "feature_fraction": 0.8,
                "bagging_fraction": 0.8,
                "bagging_freq": 5,
                "verbose": -1,
            }
            model = lgb.train(
                params,
                train_data,
                num_boost_round=200,
                valid_sets=[val_data],
                callbacks=[lgb.early_stopping(20, verbose=False),
                           lgb.log_evaluation(-1)]
            )
            with self._lock:
                self._models[symbol] = model
                # Store feature importance
                imp = model.feature_importance(importance_type="gain")
                pairs = sorted(zip(FEATURE_COLS, imp.tolist()),
                               key=lambda x: x[1], reverse=True)
                self._feature_importance[symbol] = pairs
                log.debug(f"{symbol} top features: {pairs[:3]}")

            # Persist
            path = os.path.join(self.MODEL_DIR, f"{symbol}.lgb")
            model.save_model(path)
            log.info(f"✓ Model trained & saved for {symbol} ({len(X_train)} samples)")
            return True
        except Exception as e:
            log.error(f"Training failed for {symbol}: {e}")
            return False

    def load_model(self, symbol: str) -> bool:
        if not HAS_LGB:
            return False
        path = os.path.join(self.MODEL_DIR, f"{symbol}.lgb")
        if os.path.exists(path):
            try:
                model = lgb.Booster(model_file=path)
                with self._lock:
                    self._models[symbol] = model
                log.info(f"Loaded saved model for {symbol}")
                return True
            except Exception as e:
                log.warning(f"Could not load model for {symbol}: {e}")
        return False

    def retrain_on_close(self, symbol: str, df: pd.DataFrame) -> bool:
        """
        Lightweight incremental retrain called after each trade closes.
        Uses the most recent `lookback` candles so the model stays fresh.
        Runs in a daemon thread to avoid blocking the main loop.
        """
        def _do():
            log.info(f"🔄 Incremental retrain for {symbol} ({len(df)} rows)...")
            self.train(symbol, df)
        t = threading.Thread(target=_do, daemon=True)
        t.start()
        return True

    def get_feature_importance(self, symbol: str) -> list:
        """Returns list of (feature_name, importance_score) sorted by importance."""
        with self._lock:
            return self._feature_importance.get(symbol, [])

    # ─────────────────── PREDICT ──────────────────

    def predict(self, symbol: str, df: pd.DataFrame) -> Tuple[str, float]:
        """
        Returns (signal, confidence): signal in ['BUY','SELL','HOLD']
        Falls back to rule-based if no model available.
        """
        df_ind = compute_indicators(df)
        if df_ind is None or df_ind.empty:
            return "HOLD", 0.0

        latest = df_ind.iloc[-1]

        with self._lock:
            model = self._models.get(symbol)

        if model and HAS_LGB:
            try:
                X = latest[FEATURE_COLS].values.reshape(1, -1)
                probs = model.predict(X)[0]
                pred  = int(np.argmax(probs))
                conf  = float(probs[pred])
                signal = self.SIGNAL_MAP[pred]
                log.debug(f"{symbol} AI: {signal} conf={conf:.2f} probs={probs}")
                return signal, conf
            except Exception as e:
                log.warning(f"Prediction error {symbol}: {e}")

        # ── Rule-based fallback ──
        return self._rule_based(latest)

    def _rule_based(self, row: pd.Series) -> Tuple[str, float]:
        """Simple rule-based signal using RSI + MACD + EMA."""
        rsi       = row.get("rsi", 50)
        macd_diff = row.get("macd_diff", 0)
        ema_cross = row.get("ema_cross", 0)
        bb_pos    = (row.get("close", 0) - row.get("bb_lower", 0)) / (
                     row.get("bb_upper", 1) - row.get("bb_lower", 0) + 1e-9)

        score = 0
        if rsi < 40:        score += 1
        if rsi > 60:        score -= 1
        if macd_diff > 0:   score += 1
        if macd_diff < 0:   score -= 1
        if ema_cross > 0:   score += 1
        if ema_cross < 0:   score -= 1
        if bb_pos < 0.2:    score += 1
        if bb_pos > 0.8:    score -= 1

        if score >= 2:
            return "BUY",  min(0.5 + score * 0.05, 0.90)
        elif score <= -2:
            return "SELL", min(0.5 + abs(score) * 0.05, 0.90)
        return "HOLD", 0.0
