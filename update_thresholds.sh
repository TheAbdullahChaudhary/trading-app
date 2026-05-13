#!/bin/bash
# Quick update - Lower thresholds for testing

echo "🔄 Updating bot for more trades..."

cd /data/servers/app/trading-app
git pull origin main

docker restart SERVER-LOGS

echo "✅ Bot updated! Thresholds lowered:"
echo "  - Score: 7/12 (was 10/12)"
echo "  - ADX: 20 (was 25)"
echo "  - Confidence: 55% (was 60%)"
echo "  - Cooldown: 1min (was 3min)"
echo "  - Momentum: 0.1% (was 0.2%)"
echo ""
echo "Bot will trade MORE frequently now!"
echo ""
echo "Watch: docker logs -f SERVER-LOGS"
