#!/bin/bash
# Setup script for Garmin health integration
# Run this on the Hetzner server after deploying

set -e

echo "=== Garmin Health Integration Setup ==="
echo ""

# Check if running as root or with sudo
if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo: sudo ./scripts/setup-garmin.sh"
    exit 1
fi

# Install garminconnect library
echo "[1/4] Installing garminconnect library..."
pip3 install garminconnect
echo "✓ garminconnect installed"

# Check for credentials
echo ""
echo "[2/4] Checking credentials..."

if [ -z "$GARMIN_EMAIL" ]; then
    read -p "Enter your Garmin email: " GARMIN_EMAIL
fi

if [ -z "$GARMIN_PASSWORD" ]; then
    read -s -p "Enter your Garmin password: " GARMIN_PASSWORD
    echo ""
fi

# Add to environment file for persistence
ENV_FILE="/opt/claudius/.env"
echo ""
echo "[3/4] Saving credentials..."

# Create or update .env file
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

# Remove old entries if they exist
grep -v "^GARMIN_EMAIL=" "$ENV_FILE" > "$ENV_FILE.tmp" 2>/dev/null || true
grep -v "^GARMIN_PASSWORD=" "$ENV_FILE.tmp" > "$ENV_FILE" 2>/dev/null || true
rm -f "$ENV_FILE.tmp"

# Add new entries
echo "GARMIN_EMAIL=\"$GARMIN_EMAIL\"" >> "$ENV_FILE"
echo "GARMIN_PASSWORD=\"$GARMIN_PASSWORD\"" >> "$ENV_FILE"

echo "✓ Credentials saved to $ENV_FILE"

# Setup cron job
echo ""
echo "[4/4] Setting up cron job (every 15 minutes)..."

CRON_CMD="*/15 * * * * cd /opt/claudius && source .env && python3 -c 'from health import sync_health_data; sync_health_data()' >> /var/log/garmin-sync.log 2>&1"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "garmin-sync"; then
    echo "Cron job already exists, updating..."
    crontab -l 2>/dev/null | grep -v "garmin-sync" | crontab -
fi

# Add the cron job
(crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -

echo "✓ Cron job installed"

# Create log file
touch /var/log/garmin-sync.log
chmod 644 /var/log/garmin-sync.log

# Test the connection
echo ""
echo "=== Testing Garmin Connection ==="
cd /opt/claudius
source .env
export GARMIN_EMAIL GARMIN_PASSWORD

python3 << 'PYEOF'
import sys
sys.path.insert(0, '/opt/claudius')

try:
    from health import get_garmin_auth
    auth = get_garmin_auth()
    auth.login()
    status = auth.get_auth_status()
    print(f"✓ Login successful!")
    print(f"  Email: {status.get('email', 'N/A')}")
    print(f"  Session saved: {status.get('has_session', False)}")
except Exception as e:
    print(f"✗ Login failed: {e}")
    print("  Check your credentials and try again")
    sys.exit(1)
PYEOF

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Restart claudius-api to pick up changes:"
echo "   systemctl restart claudius-api"
echo ""
echo "2. Do initial sync (backfill 7 days):"
echo "   cd /opt/claudius && source .env && python3 -c 'from health import backfill; print(backfill(7))'"
echo ""
echo "3. Check sync logs:"
echo "   tail -f /var/log/garmin-sync.log"
