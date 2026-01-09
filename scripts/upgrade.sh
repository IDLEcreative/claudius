#!/bin/bash
# Claudius Upgrade Script
# Pulls latest changes and restarts the service

set -e

INSTALL_DIR="/opt/claudius"

echo "🏛️  Upgrading Claudius Maximus..."
echo ""

cd "$INSTALL_DIR"

# Check for uncommitted changes
if [ -n "$(git status --porcelain)" ]; then
    echo "⚠️  Warning: You have uncommitted changes"
    git status --short
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Pull latest
echo "📥 Pulling latest changes..."
git fetch origin main
git pull origin main

# Update systemd service if changed
if git diff HEAD~1 --name-only | grep -q "systemd/claudius-api.service"; then
    echo "🔧 Updating systemd service..."
    cp "$INSTALL_DIR/systemd/claudius-api.service" /etc/systemd/system/
    systemctl daemon-reload
fi

# Restart service
echo "🔄 Restarting Claudius..."
systemctl restart claudius-api

# Wait and verify
sleep 2
if curl -s http://localhost:3100/health | grep -q '"status":"ok"'; then
    echo "✅ Upgrade complete! Claudius is running."
else
    echo "❌ Health check failed. Rolling back..."
    git reset --hard HEAD~1
    systemctl restart claudius-api
    echo "⚠️  Rolled back to previous version"
fi
