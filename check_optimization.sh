#!/bin/bash
# Quick optimization verification script

echo "=========================================="
echo "   TRADING BOT OPTIMIZATION CHECK"
echo "=========================================="
echo ""

# Check config
echo "📋 Configuration:"
grep -A 2 "symbols:" config.yaml | grep "name:" | wc -l | xargs echo "  Active Symbols:"
grep "min_confidence:" config.yaml | tail -1
grep "max_open_positions:" config.yaml | tail -1
grep "MIN_SCORE" bot/strategy.py | grep "=" | head -1
grep "MIN_ADX" bot/strategy.py | grep "=" | head -1
echo ""

# Check recent performance
echo "📊 Last 50 Trades Performance:"
sqlite3 data/trades.db "
SELECT 
  COUNT(*) as total_trades,
  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
  ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) || '%' as win_rate,
  ROUND(SUM(pnl), 2) || ' USDT' as total_pnl,
  ROUND(AVG(CASE WHEN pnl > 0 THEN pnl END), 2) || ' USDT' as avg_win,
  ROUND(AVG(CASE WHEN pnl < 0 THEN pnl END), 2) || ' USDT' as avg_loss
FROM (SELECT * FROM trades WHERE status='closed' ORDER BY id DESC LIMIT 50)
" -header -column

echo ""
echo "📈 Performance by Symbol (Last 50):"
sqlite3 data/trades.db "
SELECT 
  symbol,
  COUNT(*) as trades,
  ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) || '%' as win_rate,
  ROUND(SUM(pnl), 2) || ' USDT' as pnl
FROM (SELECT * FROM trades WHERE status='closed' ORDER BY id DESC LIMIT 50)
GROUP BY symbol
ORDER BY win_rate DESC
" -header -column

echo ""
echo "🎯 Optimization Status:"
if grep -q "# - name: \"USOIL_USDT\"" config.yaml; then
  echo "  ✅ USOIL disabled (high volatility)"
else
  echo "  ⚠️  USOIL still enabled - consider disabling"
fi

if grep -q "min_confidence: 0.7" config.yaml; then
  echo "  ✅ High confidence threshold (70%+)"
else
  echo "  ⚠️  Confidence threshold may be too low"
fi

if grep -q "max_open_positions: 1" config.yaml; then
  echo "  ✅ Single position focus"
else
  echo "  ℹ️  Multiple positions allowed"
fi

echo ""
echo "🔍 Recent Rejections (why no trades):"
tail -100 logs/bot_*.log | grep "HOLD" | tail -5

echo ""
echo "=========================================="
echo "Run: tail -f logs/bot_*.log  (to monitor)"
echo "Dashboard: http://localhost:5000"
echo "=========================================="
