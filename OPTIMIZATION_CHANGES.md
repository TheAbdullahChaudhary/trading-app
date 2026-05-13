# Trading Bot Optimization - Profitability Improvements

## Problem Analysis
- **Win Rate**: 27.3% (need >40% minimum)
- **Total PnL**: -4.17 USDT (losing money)
- **Root Causes**:
  1. Stops too tight (0.8 ATR) - getting stopped out prematurely
  2. Poor risk/reward ratio (1:1 instead of 1:2+)
  3. Over-trading (50 trades/hour, 1-second checks)
  4. Too aggressive leverage (50x)
  5. Low entry threshold (score 8/12)

## Changes Made

### 1. Risk Management (config.yaml)
```yaml
BEFORE → AFTER
- risk_per_trade_pct: 2.0 → 1.0 (more conservative)
- max_open_positions: 10 → 3 (focus on quality)
- max_daily_drawdown_pct: 10.0 → 5.0 (tighter control)
- sl_atr_multiplier: 0.8 → 1.5 (wider stops, less noise)
- tp_atr_multiplier: 0.8 → 3.0 (1:2 risk/reward minimum)
- max_trades_per_hour: 50 → 10 (reduce overtrading)
- cooldown_seconds: 10 → 120 (2min between trades)
```

### 2. Leverage Reduction
```yaml
BEFORE → AFTER
- leverage: 50 → 10 (all symbols)
```
**Impact**: Reduces risk of liquidation, more sustainable trading

### 3. Signal Quality (bot/strategy.py)
```python
BEFORE → AFTER
- MIN_SCORE: 8 → 9 (more selective entries)
- MIN_ADX: 18 → 22 (stronger trend requirement)
- BASE_COOLDOWN: 60s → 120s (less frequent trades)
- LOSS_COOLDOWN: 300s → 600s (longer pause after losses)
```

### 4. Dynamic Stop Loss/Take Profit
```python
BEFORE → AFTER
Base case:
- SL: 1.5 ATR → 1.5 ATR (kept)
- TP: 3.0 ATR → 3.5 ATR (better R:R)

Strong trend (ADX > 35):
- SL: 2.0 ATR → 2.0 ATR (kept)
- TP: 4.0 ATR → 5.0 ATR (ride winners longer)

Weak trend (ADX < 25):
- SL: 1.2 ATR → 1.8 ATR (much wider, avoid noise)
- TP: 2.5 ATR → 3.5 ATR (better R:R)
```

### 5. AI Confidence Threshold
```yaml
BEFORE → AFTER
- min_confidence: 0.50 → 0.65 (higher quality signals)
- signal_check_interval: 1s → 5s (less frantic)
- retrain_interval_hours: 2 → 4 (more stable models)
```

## Expected Improvements

### Win Rate Target: 40-50%
- Wider stops reduce premature exits
- Higher score threshold = better setups
- Stronger ADX filter = trending markets only

### Risk/Reward: 1:2.3 minimum
- TP now 3.5x ATR vs SL 1.5x ATR
- Strong trends: 1:2.5 ratio
- Should recover losses faster with fewer wins

### Reduced Overtrading
- 10 trades/hour max (was 50)
- 2-minute cooldown per symbol
- 10-minute pause after 2 consecutive losses
- Only 3 positions max (was 10)

### Lower Risk Exposure
- 1% risk per trade (was 2%)
- 10x leverage (was 50x)
- 5% daily drawdown limit (was 10%)

## Action Required

**RESTART THE BOT** for changes to take effect:
1. Stop the current bot
2. Restart: `python main.py --dry-run`
3. Monitor for at least 20 trades to evaluate

## Monitoring Metrics

Track these to validate improvements:
- **Win Rate**: Target >40% (was 27.3%)
- **Avg Win/Loss Ratio**: Target >2.0
- **Max Consecutive Losses**: Should see <3 in a row
- **Daily PnL**: Should trend positive over 3+ days
- **Trades per Hour**: Should be 5-10 (was spiking to 50)

## Additional Recommendations

1. **Focus on 2-3 symbols** - Remove underperformers (SILVER, OIL showing losses)
2. **Trade only during high liquidity** - Add time-of-day filters
3. **Backtest these settings** - Validate on historical data
4. **Consider 5-minute timeframe** - Less noise than 1-minute
5. **Add trailing stops** - Lock in profits on strong moves

## Rollback Instructions

If performance worsens, restore original settings:
```bash
git checkout config.yaml bot/strategy.py
```
