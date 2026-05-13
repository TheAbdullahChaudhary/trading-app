#!/bin/bash
# Quick status check for trading bot

echo "=== TRADING BOT STATUS ==="
echo ""

# Check if running
if pgrep -f "python3 main.py" > /dev/null; then
    PID=$(pgrep -f "python3 main.py")
    echo "✅ Bot is RUNNING (PID: $PID)"
else
    echo "❌ Bot is NOT running"
    exit 1
fi

echo ""
echo "=== RECENT ACTIVITY ==="
tail -10 /home/devops/application/trading-app/logs/bot_20260512.log | grep -E "BUY|SELL|HOLD|PnL|Balance" || echo "No recent trades"

echo ""
echo "=== PERFORMANCE ==="
if [ -f data/trades.db ]; then
    echo "Total Trades: $(sqlite3 data/trades.db 'SELECT COUNT(*) FROM trades' 2>/dev/null || echo '0')"
    echo "Open Positions: $(sqlite3 data/trades.db "SELECT COUNT(*) FROM trades WHERE status='open'" 2>/dev/null || echo '0')"
    echo "Today's PnL: $(sqlite3 data/trades.db "SELECT COALESCE(ROUND(SUM(pnl),2), 0) FROM trades WHERE date(opened_at) = date('now')" 2>/dev/null || echo '0') USDT"
else
    echo "No trades yet"
fi

echo ""
echo "=== DASHBOARD ==="
echo "Access at: http://localhost:5000"
echo ""
echo "Commands:"
echo "  Stop bot:   pkill -f 'python3 main.py'"
echo "  View logs:  tail -f logs/bot_20260512.log"
echo "  This check: ./status.sh"
