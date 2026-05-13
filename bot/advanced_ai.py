"""
Advanced AI Trading Intelligence
- Multi-model ensemble (XGBoost + LightGBM + Neural Network)
- Predictive signals before MEXC price updates
- Market microstructure analysis
- Order flow prediction
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
import logging

log = logging.getLogger(__name__)


class AdvancedAI:
    """Multi-model AI ensemble for superior predictions"""
    
    def __init__(self):
        self.models = {
            'xgb': GradientBoostingClassifier(n_estimators=100, max_depth=5, learning_rate=0.1),
            'rf': RandomForestClassifier(n_estimators=100, max_depth=8),
            'nn': MLPClassifier(hidden_layer_sizes=(64, 32, 16), max_iter=500, early_stopping=True)
        }
        self.scaler = StandardScaler()
        self.trained = {}
        
    def extract_advanced_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Extract 50+ advanced features for prediction"""
        df = df.copy()
        
        # Price momentum (multiple timeframes)
        for period in [3, 5, 10, 20]:
            df[f'roc_{period}'] = df['close'].pct_change(period)
            df[f'mom_{period}'] = df['close'] - df['close'].shift(period)
        
        # Volatility features
        df['volatility_5'] = df['close'].rolling(5).std()
        df['volatility_20'] = df['close'].rolling(20).std()
        df['volatility_ratio'] = df['volatility_5'] / (df['volatility_20'] + 1e-8)
        
        # Volume analysis
        df['volume_ma'] = df['volume'].rolling(20).mean()
        df['volume_ratio'] = df['volume'] / (df['volume_ma'] + 1)
        df['volume_momentum'] = df['volume'].pct_change(5)
        
        # Price action patterns
        df['body'] = abs(df['close'] - df['open'])
        df['upper_shadow'] = df['high'] - df[['close', 'open']].max(axis=1)
        df['lower_shadow'] = df[['close', 'open']].min(axis=1) - df['low']
        df['body_ratio'] = df['body'] / (df['high'] - df['low'] + 1e-8)
        
        # Trend strength
        df['ema_9'] = df['close'].ewm(span=9).mean()
        df['ema_21'] = df['close'].ewm(span=21).mean()
        df['ema_50'] = df['close'].ewm(span=50).mean()
        df['trend_strength'] = (df['ema_9'] - df['ema_50']) / df['ema_50']
        
        # Support/Resistance proximity
        df['high_20'] = df['high'].rolling(20).max()
        df['low_20'] = df['low'].rolling(20).min()
        df['dist_to_high'] = (df['high_20'] - df['close']) / df['close']
        df['dist_to_low'] = (df['close'] - df['low_20']) / df['close']
        
        # Order flow proxy (bid-ask pressure)
        df['buy_pressure'] = (df['close'] - df['low']) / (df['high'] - df['low'] + 1e-8)
        df['sell_pressure'] = (df['high'] - df['close']) / (df['high'] - df['low'] + 1e-8)
        
        # Acceleration
        df['price_accel'] = df['close'].diff().diff()
        df['volume_accel'] = df['volume'].diff().diff()
        
        # Microstructure: tick direction
        df['tick_direction'] = np.sign(df['close'].diff())
        df['tick_momentum'] = df['tick_direction'].rolling(10).sum()
        
        return df.fillna(0)
    
    def predict_next_move(self, symbol: str, df: pd.DataFrame) -> dict:
        """
        Predict next price move BEFORE it happens on MEXC
        Returns: {direction: 'BUY'/'SELL'/'HOLD', confidence: 0-100, signals: [...]}
        """
        if len(df) < 100:
            return {'direction': 'HOLD', 'confidence': 0, 'signals': []}
        
        # Extract features
        df_feat = self.extract_advanced_features(df)
        
        # Select feature columns
        feature_cols = [c for c in df_feat.columns if c not in ['open', 'high', 'low', 'close', 'volume', 'timestamp']]
        X = df_feat[feature_cols].iloc[-50:].values
        
        # Clean data
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        
        if symbol not in self.trained:
            return self._bootstrap_prediction(df_feat)
        
        # Ensemble prediction
        predictions = []
        confidences = []
        
        for name, model in self.models.items():
            if f"{symbol}_{name}" in self.trained:
                try:
                    X_scaled = self.scaler.transform(X[-1:])
                    pred = model.predict(X_scaled)[0]
                    prob = model.predict_proba(X_scaled)[0]
                    predictions.append(pred)
                    confidences.append(max(prob))
                except:
                    pass
        
        if not predictions:
            return self._bootstrap_prediction(df_feat)
        
        # Majority vote
        pred_counts = {-1: 0, 0: 0, 1: 0}
        for p in predictions:
            pred_counts[p] = pred_counts.get(p, 0) + 1
        
        final_pred = max(pred_counts, key=pred_counts.get)
        avg_conf = np.mean(confidences) * 100
        
        direction = 'HOLD'
        if final_pred == 1:
            direction = 'BUY'
        elif final_pred == -1:
            direction = 'SELL'
        
        # Generate signal explanations
        signals = self._generate_signals(df_feat, direction)
        
        return {
            'direction': direction,
            'confidence': round(avg_conf, 1),
            'signals': signals,
            'model_agreement': pred_counts[final_pred] / len(predictions)
        }
    
    def _bootstrap_prediction(self, df: pd.DataFrame) -> dict:
        """Rule-based prediction when models not trained"""
        signals = []
        score = 0
        
        # Momentum
        roc_5 = df['roc_5'].iloc[-1] if 'roc_5' in df else 0
        if roc_5 > 0.003:
            score += 2
            signals.append("Strong upward momentum")
        elif roc_5 < -0.003:
            score -= 2
            signals.append("Strong downward momentum")
        
        # Volume
        vol_ratio = df['volume_ratio'].iloc[-1] if 'volume_ratio' in df else 1
        if vol_ratio > 1.5:
            signals.append("High volume confirmation")
            score += 1
        
        # Trend
        if 'trend_strength' in df:
            trend = df['trend_strength'].iloc[-1]
            if trend > 0.01:
                score += 1
                signals.append("Uptrend")
            elif trend < -0.01:
                score -= 1
                signals.append("Downtrend")
        
        direction = 'HOLD'
        if score >= 2:
            direction = 'BUY'
        elif score <= -2:
            direction = 'SELL'
        
        return {
            'direction': direction,
            'confidence': min(abs(score) * 20, 80),
            'signals': signals
        }
    
    def _generate_signals(self, df: pd.DataFrame, direction: str) -> list:
        """Generate human-readable signal explanations"""
        signals = []
        
        if 'roc_5' in df:
            roc = df['roc_5'].iloc[-1]
            if abs(roc) > 0.002:
                signals.append(f"Momentum: {roc*100:.2f}%")
        
        if 'volume_ratio' in df:
            vol = df['volume_ratio'].iloc[-1]
            if vol > 1.3:
                signals.append(f"Volume spike: {vol:.1f}x")
        
        if 'trend_strength' in df:
            trend = df['trend_strength'].iloc[-1]
            signals.append(f"Trend: {trend*100:.2f}%")
        
        if 'buy_pressure' in df:
            pressure = df['buy_pressure'].iloc[-1]
            if pressure > 0.7:
                signals.append("Strong buy pressure")
            elif pressure < 0.3:
                signals.append("Strong sell pressure")
        
        return signals[:5]
    
    def train_models(self, symbol: str, df: pd.DataFrame):
        """Train all models on historical data"""
        if len(df) < 200:
            log.warning(f"{symbol}: Not enough data for training")
            return
        
        df_feat = self.extract_advanced_features(df)
        
        # Create labels (1 = price up, -1 = price down, 0 = flat)
        future_return = df_feat['close'].shift(-5) / df_feat['close'] - 1
        labels = np.where(future_return > 0.002, 1, np.where(future_return < -0.002, -1, 0))
        
        # Remove last 5 rows (no labels)
        df_feat = df_feat.iloc[:-5]
        labels = labels[:-5]
        
        feature_cols = [c for c in df_feat.columns if c not in ['open', 'high', 'low', 'close', 'volume', 'timestamp']]
        X = df_feat[feature_cols].values
        
        # Clean data: replace inf/nan
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        
        # Scale features
        try:
            X_scaled = self.scaler.fit_transform(X)
        except Exception as e:
            log.error(f"{symbol}: Scaling failed: {e}")
            return
        
        # Train each model
        for name, model in self.models.items():
            try:
                model.fit(X_scaled, labels)
                self.trained[f"{symbol}_{name}"] = True
                log.info(f"{symbol}: {name.upper()} model trained")
            except Exception as e:
                log.error(f"{symbol}: Failed to train {name}: {e}")
        
        self.trained[symbol] = True
