# AUTOMATED TRADING BOT - SETUP GUIDE

## ⚠️ CRITICAL WARNING

**Trading bots can lose money.** There is NO guarantee of profit. Only risk what you can afford to lose.

## Current Status
- Win Rate: 27.3% → Target: 40%+
- Settings: OPTIMIZED for better risk/reward
- Mode: Ready for automation

## Quick Start - Automated Trading

### Option 1: Simple Background Run
```bash
cd /home/devops/application/trading-app
nohup ./run_bot.sh &
```

### Option 2: Systemd Service (Recommended)
```bash
# Install service
sudo cp trading-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable trading-bot
sudo systemctl start trading-bot

# Monitor
sudo systemctl status trading-bot
tail -f logs/bot_runner.log
```

### Option 3: Screen Session
```bash
screen -S trading-bot
./run_bot.sh
# Press Ctrl+A then D to detach
# Reattach: screen -r trading-bot
```

## Safety Features Built-In

1. **Daily Loss Limit**: Stops at -$5/day
2. **Max Drawdown**: 5% account limit
3. **Auto-Restart**: Recovers from crashes
4. **Trade Limits**: Max 10 trades/hour
5. **Position Limits**: Max 3 concurrent positions

## Monitoring Dashboard

Access at: http://localhost:5000

**Key Metrics to Watch:**
- Win Rate (target >40%)
- Daily PnL (should trend positive)
- Open positions (max 3)
- Consecutive losses (should be <3)

## Required: API Keys

Create `.env` file:
```bash
MEXC_API_KEY=your_api_key_here
MEXC_API_SECRET=your_api_secret_here
GEMINI_API_KEY=your_gemini_key_here  # Optional
```

## Maintenance Commands

```bash
# Check status
tail -f logs/bot_runner.log

# Stop bot
pkill -f "python main.py"
# OR
sudo systemctl stop trading-bot

# View trades
sqlite3 data/trades.db "SELECT * FROM trades ORDER BY id DESC LIMIT 10"

# Check daily PnL
sqlite3 data/trades.db "SELECT SUM(pnl) FROM trades WHERE date(opened_at) = date('now')"
```

## Performance Expectations

**Realistic Goals:**
- Monthly Return: 5-15% (good month)
- Win Rate: 40-50%
- Max Drawdown: 10-20%
- Losing days: 30-40% of days

**Red Flags (Stop Bot):**
- Win rate <30% after 50 trades
- 5+ consecutive losses
- Daily loss >10%
- Unusual API errors

## Optimization Tips

1. **Start Small**: Test with $100-500 first
2. **Monitor Daily**: Check dashboard 2x/day minimum
3. **Weekly Review**: Analyze which symbols perform best
4. **Adjust Settings**: Disable losing symbols
5. **Compound Slowly**: Don't increase risk too fast

## Troubleshooting

**Bot keeps stopping:**
- Check API keys in `.env`
- Verify MEXC account has funds
- Check logs: `tail -f logs/bot_*.log`

**No trades executing:**
- ADX filter may be too strict (markets choppy)
- Confidence threshold too high
- Check "Signal Scores" in dashboard

**High loss rate:**
- Reduce leverage further (5x)
- Increase MIN_SCORE to 10
- Trade only BTC/ETH (remove commodities)

## Backup Strategy

```bash
# Daily backup
0 0 * * * cp /home/devops/application/trading-app/data/trades.db /home/devops/backups/trades_$(date +\%Y\%m\%d).db
```

## Emergency Stop

```bash
# Kill bot immediately
pkill -9 -f "python main.py"

# Close all positions via dashboard
# Click "🔥 Close All" button

# Or via API
python -c "from bot.mexc_client import MEXCClient; import os; c=MEXCClient(os.getenv('MEXC_API_KEY'), os.getenv('MEXC_API_SECRET')); print(c.close_all_positions())"
```

## Legal Disclaimer

This bot is provided AS-IS. No warranty. Trading involves substantial risk. Past performance does not guarantee future results. You are responsible for all trading decisions and losses.

---

**Ready to start?**
```bash
./run_bot.sh
```

Monitor at: http://localhost:5000
