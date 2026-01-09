#!/bin/bash
# Claudius Installation Script
# Run this on the Hetzner server to install/update Claudius

set -e

INSTALL_DIR="/opt/claudius"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "🏛️  Installing Claudius Maximus..."
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ Please run as root (sudo ./scripts/install.sh)"
    exit 1
fi

# Check if Claude CLI is installed
if ! command -v claude &> /dev/null; then
    echo "❌ Claude CLI not found. Please install it first:"
    echo "   npm install -g @anthropic-ai/claude-code"
    exit 1
fi

# Create installation directory if needed
if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
    echo "📁 Creating installation directory..."
    mkdir -p "$INSTALL_DIR"
    cp -r "$SCRIPT_DIR"/* "$INSTALL_DIR/"
fi

# Create memory file from template if it doesn't exist
if [ ! -f "$INSTALL_DIR/MEMORY.md" ]; then
    echo "📝 Creating memory file from template..."
    cp "$INSTALL_DIR/MEMORY_TEMPLATE.md" "$INSTALL_DIR/MEMORY.md"
fi

# Create .env from example if it doesn't exist
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo "⚙️  Creating .env from template..."
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo ""
    echo "⚠️  IMPORTANT: Edit /opt/claudius/.env and add your CRON_SECRET!"
    echo ""
fi

# Install systemd service
echo "🔧 Installing systemd service..."
cp "$INSTALL_DIR/systemd/claudius-api.service" /etc/systemd/system/
systemctl daemon-reload

# Configure log rotation
echo "📋 Configuring log rotation..."
cp "$INSTALL_DIR/config/logrotate.conf" /etc/logrotate.d/claudius-api

# Set permissions
echo "🔒 Setting permissions..."
chmod +x "$INSTALL_DIR/api/claudius-api.py"
chmod +x "$INSTALL_DIR/mcp/telegram-progress.py"
chmod +x "$INSTALL_DIR/scripts/"*.sh 2>/dev/null || true

# Enable and start service
echo "🚀 Starting Claudius service..."
systemctl enable claudius-api
systemctl restart claudius-api

# Wait for startup
sleep 2

# Health check
echo ""
echo "🏥 Running health check..."
if curl -s http://localhost:3100/health | grep -q '"status":"ok"'; then
    echo "✅ Claudius is running!"
    echo ""
    curl -s http://localhost:3100/health | python3 -m json.tool
else
    echo "❌ Health check failed. Check logs:"
    echo "   journalctl -u claudius-api -n 50"
fi

echo ""
echo "📖 Quick reference:"
echo "   Status:  systemctl status claudius-api"
echo "   Logs:    journalctl -u claudius-api -f"
echo "   Restart: systemctl restart claudius-api"
echo "   Test:    curl http://localhost:3100/health"
echo ""
echo "🏛️  Claudius Maximus installation complete!"
