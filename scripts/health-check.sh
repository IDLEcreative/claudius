#!/bin/bash
# Claudius Health Check Script

echo "🏛️  Claudius Health Check"
echo ""

# Service status
echo "📊 Service Status:"
systemctl is-active claudius-api && echo "✅ Service: Running" || echo "❌ Service: Not running"

# API health
echo ""
echo "🏥 API Health:"
HEALTH=$(curl -s http://localhost:3100/health 2>/dev/null)
if [ -n "$HEALTH" ]; then
    echo "$HEALTH" | python3 -m json.tool 2>/dev/null || echo "$HEALTH"
else
    echo "❌ API not responding"
fi

# Recent logs
echo ""
echo "📋 Recent Logs (last 10 lines):"
journalctl -u claudius-api -n 10 --no-pager

# Resource usage
echo ""
echo "💻 Resource Usage:"
echo "Memory: $(free -h | awk '/^Mem:/ {print $3 "/" $2}')"
echo "Disk: $(df -h / | awk 'NR==2 {print $3 "/" $2 " (" $5 " used)"}')"
