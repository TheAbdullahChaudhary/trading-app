#!/bin/bash
# Automated trading bot runner with monitoring and auto-restart

LOG_FILE="logs/bot_runner.log"
MAX_DAILY_LOSS=5.0  # Stop if lose more than $5/day
CHECK_INTERVAL=300  # Check every 5 minutes

echo "Starting automated trading bot..." | tee -a $LOG_FILE

while true; do
    # Start bot in background
    python3 main.py > logs/bot_output.log 2>&1 &
    BOT_PID=$!
    echo "[$(date)] Bot started with PID $BOT_PID" | tee -a $LOG_FILE
    
    # Monitor bot health
    while kill -0 $BOT_PID 2>/dev/null; do
        sleep $CHECK_INTERVAL
        
        # Check daily PnL from database
        DAILY_PNL=$(sqlite3 data/trades.db "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE date(opened_at) = date('now')" 2>/dev/null || echo "0")
        
        echo "[$(date)] Daily PnL: $DAILY_PNL" | tee -a $LOG_FILE
        
        # Kill switch: stop if daily loss exceeds limit
        if (( $(echo "$DAILY_PNL < -$MAX_DAILY_LOSS" | bc -l) )); then
            echo "[$(date)] STOP: Daily loss limit hit ($DAILY_PNL)" | tee -a $LOG_FILE
            kill $BOT_PID
            exit 1
        fi
    done
    
    echo "[$(date)] Bot stopped. Restarting in 30s..." | tee -a $LOG_FILE
    sleep 30
done
