#!/bin/bash
# Fix Docker container - Add missing dependencies

echo "🔧 Fixing Docker container..."

# Stop the container
docker stop fcaabe02c001

# Pull latest code
cd /data/servers/app/trading-app
git pull origin main

# Rebuild Docker image
docker build -t server-logs .

# Remove old container
docker rm fcaabe02c001

# Start new container
docker run -d \
  --name SERVER-LOGS \
  --restart unless-stopped \
  -v /data/servers/app/trading-app:/app \
  -p 5000:5000 \
  server-logs

echo "✅ Container rebuilt and started!"
echo ""
echo "Check logs:"
echo "  docker logs -f SERVER-LOGS"
