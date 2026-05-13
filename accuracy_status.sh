#!/bin/bash
# Real-time bot status with high accuracy mode info

clear
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         HIGH ACCURACY TRADING BOT - LIVE STATUS              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Check if running
if pgrep -f "main.py --dry-run" > /dev/null; then
    PID=$(pgrep -f "main.py --dry-run")
    echo "✅ Status: RUNNING (PID: $PID)"
else
    echo "❌ Status: STOPPED"
    echo ""
    echo "Start with: cd /home/devops/application/trading-app && python3 main.py --dry-run &"
    exit 1
fi

echo "💰 Balance: \$100 USDT (Demo)"
echo "📊 Mode: HIGH ACCURACY SNIPER"
echo "🌐 Dashboard: http://192.168.104.156:5000"
echo ""

echo "═══════════════════════════════════════════════════════════════"
echo "🎯 ACCURACY SETTINGS"
echo "═══════════════════════════════════════════════════════════════"
echo "  Entry Score:     ≥10/12 (MAXIMUM selectivity)"
echo "  ADX Threshold:   ≥25 (Strong trends only)"
echo "  AI Confidence:   ≥70%"
echo "  Momentum:        ≥0.2% required"
echo "  Volume:          >110% average"
echo "  Risk/Trade:      0.8% (\$0.80)"
echo "  Max Positions:   2"
echo "  Stop Loss:       2.0-2.5 ATR"
echo "  Take Profit:     4.5-6.0 ATR"
echo "  Risk/Reward:     1:2.25+"
echo ""

echo "═══════════════════════════════════════════════════════════════"
echo "📈 RECENT ACTIVITY (Last 10 evaluations)"
echo "═══════════════════════════════════════════════════════════════"
tail -20 /home/devops/application/trading-app/logs/bot_20260512.log | grep "Evaluating\|BUY\|SELL\|Choppy\|Score" | tail -10

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "📊 PERFORMANCE"
echo "═══════════════════════════════════════════════════════════════"
if [ -f data/trades.db ]; then
    TOTAL=$(sqlite3 data/trades.db 'SELECT COUNT(*) FROM trades' 2>/dev/null || echo '0')
    OPEN=$(sqlite3 data/trades.db "SELECT COUNT(*) FROM trades WHERE status='open'" 2>/dev/null || echo '0')
    PNL=$(sqlite3 data/trades.db "SELECT COALESCE(ROUND(SUM(pnl),2), 0) FROM trades" 2>/dev/null || echo '0')
    TODAY_PNL=$(sqlite3 data/trades.db "SELECT COALESCE(ROUND(SUM(pnl),2), 0) FROM trades WHERE date(opened_at) = date('now')" 2>/dev/null || echo '0')
    
    echo "  Total Trades:    $TOTAL"
    echo "  Open Positions:  $OPEN / 2"
    echo "  Total PnL:       $PNL USDT"
    echo "  Today's PnL:     $TODAY_PNL USDT"
    
    if [ "$TOTAL" -gt 0 ]; then
        WINS=$(sqlite3 data/trades.db "SELECT COUNT(*) FROM trades WHERE pnl > 0" 2>/dev/null || echo '0')
        WIN_RATE=$(echo "scale=1; $WINS * 100 / $TOTAL" | bc 2>/dev/null || echo '0')
        echo "  Win Rate:        ${WIN_RATE}%"
    fi
else
    echo "  No trades yet - waiting for perfect setup"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "⚠️  REMEMBER"
echo "═══════════════════════════════════════════════════════════════"
echo "  • This is SNIPER mode - may wait hours for trade"
echo "  • Target: 1-3 trades per day"
echo "  • Expected win rate: 55-65%"
echo "  • No trades = Protecting capital (GOOD!)"
echo "  • Click '▶ Start' on dashboard if not started"
echo ""
echo "Commands:"
echo "  Watch logs:  tail -f logs/bot_20260512.log"
echo "  Stop bot:    pkill -f 'main.py'"
echo "  This status: ./accuracy_status.sh"
echo ""
