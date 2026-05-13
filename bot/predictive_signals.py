"""
Predictive Signal Engine
Analyzes order book, tick data, and market microstructure
to predict price moves BEFORE they appear on MEXC charts
"""
import numpy as np
import pandas as pd
from collections import deque
import time
import logging

log = logging.getLogger(__name__)


class PredictiveSignals:
    """Generate early signals before price updates"""
    
    def __init__(self):
        self.tick_history = {}  # Store tick-by-tick data
        self.order_flow = {}    # Track buy/sell pressure
        self.last_signals = {}
        
    def analyze_tick_data(self, symbol: str, price: float, volume: float, timestamp: float) -> dict:
        """
        Analyze individual ticks to predict next move
        This runs BEFORE candle closes, giving early signals
        """
        if symbol not in self.tick_history:
            self.tick_history[symbol] = deque(maxlen=100)
            self.order_flow[symbol] = {'buys': 0, 'sells': 0, 'neutral': 0}
        
        # Store tick
        self.tick_history[symbol].append({
            'price': price,
            'volume': volume,
            'time': timestamp
        })
        
        if len(self.tick_history[symbol]) < 10:
            return {'signal': 'HOLD', 'strength': 0, 'reason': 'Insufficient data'}
        
        ticks = list(self.tick_history[symbol])
        
        # 1. Tick direction analysis
        tick_direction = self._analyze_tick_direction(ticks)
        
        # 2. Volume clustering
        volume_signal = self._analyze_volume_clustering(ticks)
        
        # 3. Price acceleration
        acceleration = self._calculate_acceleration(ticks)
        
        # 4. Order flow imbalance
        flow_imbalance = self._calculate_flow_imbalance(symbol, ticks)
        
        # Combine signals
        total_score = 0
        reasons = []
        
        if tick_direction > 0.6:
            total_score += 2
            reasons.append(f"Bullish ticks: {tick_direction:.0%}")
        elif tick_direction < 0.4:
            total_score -= 2
            reasons.append(f"Bearish ticks: {(1-tick_direction):.0%}")
        
        if volume_signal > 1.5:
            total_score += 1
            reasons.append(f"Volume surge: {volume_signal:.1f}x")
        
        if acceleration > 0.0005:
            total_score += 1
            reasons.append("Accelerating up")
        elif acceleration < -0.0005:
            total_score -= 1
            reasons.append("Accelerating down")
        
        if flow_imbalance > 0.3:
            total_score += 1
            reasons.append("Buy pressure")
        elif flow_imbalance < -0.3:
            total_score -= 1
            reasons.append("Sell pressure")
        
        # Generate signal
        signal = 'HOLD'
        strength = 0
        
        if total_score >= 3:
            signal = 'BUY'
            strength = min(total_score * 20, 100)
        elif total_score <= -3:
            signal = 'SELL'
            strength = min(abs(total_score) * 20, 100)
        
        result = {
            'signal': signal,
            'strength': strength,
            'reason': ' | '.join(reasons) if reasons else 'No clear signal',
            'tick_direction': tick_direction,
            'volume_signal': volume_signal,
            'acceleration': acceleration,
            'flow_imbalance': flow_imbalance
        }
        
        self.last_signals[symbol] = result
        return result
    
    def _analyze_tick_direction(self, ticks: list) -> float:
        """Calculate % of upticks vs downticks"""
        directions = []
        for i in range(1, len(ticks)):
            if ticks[i]['price'] > ticks[i-1]['price']:
                directions.append(1)
            elif ticks[i]['price'] < ticks[i-1]['price']:
                directions.append(-1)
            else:
                directions.append(0)
        
        if not directions:
            return 0.5
        
        upticks = sum(1 for d in directions if d == 1)
        return upticks / len(directions)
    
    def _analyze_volume_clustering(self, ticks: list) -> float:
        """Detect volume spikes (institutional activity)"""
        volumes = [t['volume'] for t in ticks]
        avg_vol = np.mean(volumes[:-5])
        recent_vol = np.mean(volumes[-5:])
        
        if avg_vol == 0:
            return 1.0
        
        return recent_vol / avg_vol
    
    def _calculate_acceleration(self, ticks: list) -> float:
        """Calculate price acceleration (2nd derivative)"""
        prices = [t['price'] for t in ticks[-20:]]
        if len(prices) < 3:
            return 0
        
        # First derivative (velocity)
        velocity = np.diff(prices)
        # Second derivative (acceleration)
        acceleration = np.diff(velocity)
        
        return np.mean(acceleration[-5:]) if len(acceleration) > 0 else 0
    
    def _calculate_flow_imbalance(self, symbol: str, ticks: list) -> float:
        """
        Estimate order flow imbalance
        Positive = more buying, Negative = more selling
        """
        # Classify ticks as buy/sell based on price movement and volume
        for i in range(1, len(ticks)):
            price_change = ticks[i]['price'] - ticks[i-1]['price']
            volume = ticks[i]['volume']
            
            if price_change > 0:
                self.order_flow[symbol]['buys'] += volume
            elif price_change < 0:
                self.order_flow[symbol]['sells'] += volume
            else:
                self.order_flow[symbol]['neutral'] += volume
        
        total = self.order_flow[symbol]['buys'] + self.order_flow[symbol]['sells']
        if total == 0:
            return 0
        
        imbalance = (self.order_flow[symbol]['buys'] - self.order_flow[symbol]['sells']) / total
        
        # Decay old data
        self.order_flow[symbol]['buys'] *= 0.95
        self.order_flow[symbol]['sells'] *= 0.95
        self.order_flow[symbol]['neutral'] *= 0.95
        
        return imbalance
    
    def get_early_entry_signal(self, symbol: str, current_price: float, df: pd.DataFrame) -> dict:
        """
        Combine tick analysis with candle data for early entry
        This gives signals BEFORE the candle closes
        """
        if symbol not in self.last_signals:
            return {'action': 'WAIT', 'confidence': 0, 'reason': 'No tick data yet'}
        
        tick_signal = self.last_signals[symbol]
        
        # Check if we have strong tick signal
        if tick_signal['strength'] < 60:
            return {'action': 'WAIT', 'confidence': tick_signal['strength'], 'reason': 'Signal too weak'}
        
        # Validate with candle data
        if len(df) < 20:
            return {'action': 'WAIT', 'confidence': 0, 'reason': 'Insufficient candle data'}
        
        # Check trend alignment
        ema_9 = df['close'].ewm(span=9).mean().iloc[-1]
        ema_21 = df['close'].ewm(span=21).mean().iloc[-1]
        
        trend_aligned = False
        if tick_signal['signal'] == 'BUY' and current_price > ema_9 > ema_21:
            trend_aligned = True
        elif tick_signal['signal'] == 'SELL' and current_price < ema_9 < ema_21:
            trend_aligned = True
        
        if not trend_aligned:
            return {
                'action': 'WAIT',
                'confidence': tick_signal['strength'] * 0.5,
                'reason': 'Tick signal vs trend mismatch'
            }
        
        # Strong signal + trend aligned = EARLY ENTRY
        return {
            'action': tick_signal['signal'],
            'confidence': tick_signal['strength'],
            'reason': f"Early signal: {tick_signal['reason']}",
            'entry_type': 'PREDICTIVE',  # Mark as predictive entry
            'tick_data': tick_signal
        }
