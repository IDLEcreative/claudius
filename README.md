# Claudius Maximus

**Bare Metal Infrastructure Agent** - The Emperor/Overseer of your Hetzner production server.

Claudius runs directly on the host machine (outside Docker) with full access to:
- Docker commands (start, stop, restart, logs, build)
- System resources (disk, memory, CPU, network)
- Server processes (systemd, journalctl)
- File system operations
- Git operations

## Quick Start

### Installation

```bash
# Clone to server
git clone https://github.com/IDLEcreative/claudius.git /opt/claudius
cd /opt/claudius

# Configure environment
cp .env.example .env
# Edit .env with your secrets

# Run installation script
./scripts/install.sh
```

### Manual Installation

```bash
# 1. Copy files to /opt/claudius
git clone https://github.com/IDLEcreative/claudius.git /opt/claudius

# 2. Configure environment
cp /opt/claudius/.env.example /opt/claudius/.env
nano /opt/claudius/.env  # Add your CRON_SECRET

# 3. Create memory file from template
cp /opt/claudius/MEMORY_TEMPLATE.md /opt/claudius/MEMORY.md

# 4. Install systemd service
sudo cp /opt/claudius/systemd/claudius-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable claudius-api
sudo systemctl start claudius-api

# 5. Configure log rotation
sudo cp /opt/claudius/config/logrotate.conf /etc/logrotate.d/claudius-api

# 6. Verify
curl http://localhost:3100/health
```

## API Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/health` | GET | No | Health check |
| `/invoke` | POST | Bearer | Execute Claudius prompt |
| `/memory` | GET | Bearer | View memory file |

### Example: Invoke Claudius

```bash
curl -X POST http://localhost:3100/invoke \
  -H "Authorization: Bearer $CRON_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Check disk space and clean up if needed"}'
```

### Request Body

```json
{
  "prompt": "Your infrastructure task",
  "model": "opus",       // Optional: opus, sonnet, haiku
  "timeout": 120,        // Optional: 30-300 seconds
  "session_id": "uuid"   // Optional: for session continuity
}
```

### Response

```json
{
  "success": true,
  "response": "Disk usage is at 45%...",
  "model": "opus",
  "session_id": "abc-123",
  "cost_usd": 0.02,
  "duration_ms": 3400
}
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `CRON_SECRET` | Yes | Authentication token |
| `ADMIN_SECRET` | No | For delegating to Clode |
| `CLODE_API_URL` | No | Clode API endpoint (default: http://localhost:3000/api/admin/claude) |
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot for notifications |
| `TELEGRAM_OWNER_CHAT_ID` | No | Telegram chat ID (default: 7070679785) |
| `FAL_KEY` | No | fal.ai API key for TTS |
| `CLAUDIUS_DIR` | No | Installation directory (default: /opt/claudius) |

## Architecture

```
                    ┌─────────────────────────────────────┐
                    │       HETZNER SERVER (77.42.19.161)  │
                    │                                      │
┌─────────┐        │  ┌──────────────────────────────┐   │
│ Claude  │        │  │     CLAUDIUS (BARE METAL)     │   │
│  Code   │───────►│  │     Port 3100                │   │
│ (Local) │        │  │  /opt/claudius/               │   │
└─────────┘        │  │  - claudius-api.py           │   │
                    │  │  - CLAUDE.md                 │   │
                    │  │  - MEMORY.md                 │   │
                    │  └──────────────┬───────────────┘   │
                    │                 │                    │
                    │                 │ Auto-delegation    │
                    │                 ▼                    │
                    │  ┌──────────────────────────────┐   │
                    │  │     DOCKER CONTAINER          │   │
                    │  │     (omniops-app)            │   │
                    │  │  - Clode (codebase agent)    │   │
                    │  │  - /api/admin/claude         │   │
                    │  └──────────────────────────────┘   │
                    └─────────────────────────────────────┘
```

## Auto-Delegation

Claudius automatically detects codebase tasks and delegates to Clode:

**Delegated to Clode:**
- Code review, debugging, refactoring
- Tests, builds, TypeScript
- Database queries, migrations

**Handled by Claudius:**
- Docker operations
- Disk/memory/CPU monitoring
- Server health checks
- Deployments and rollbacks

## Telegram Notifications

Claudius can send real-time progress updates via Telegram:

1. Create a bot via @BotFather
2. Get your chat ID
3. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_OWNER_CHAT_ID` in `.env`

Notifications include:
- Task progress (started, in progress, completed, failed)
- System alerts (disk > 85%, memory > 90%, container crashed)
- Deployment status

## MCP Server

For Claude Code integration, use the MCP server at `mcp/server.py`:

```json
{
  "mcpServers": {
    "claudius": {
      "command": "python3",
      "args": ["/opt/claudius/mcp/server.py"],
      "env": {
        "CLAUDIUS_URL": "http://77.42.19.161:3100",
        "CRON_SECRET": "your_secret"
      }
    }
  }
}
```

## Logs

```bash
# View live logs
journalctl -u claudius-api -f

# View log file
tail -f /var/log/claudius-api.log
```

## Upgrading

```bash
cd /opt/claudius
git pull origin main
sudo systemctl restart claudius-api
```

## License

Private repository - IDLEcreative
