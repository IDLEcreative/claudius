#!/bin/bash
# Claudius Watchdog - Prevents resource exhaustion and maintains responsiveness
# Run via cron every minute: * * * * * /opt/claudius/scripts/claudius-watchdog.sh

LOG="/opt/claudius/logs/claudius-watchdog.log"
MAX_CLAUDE_PROCS=6
MAX_SESSION_FILES=20
SESSION_DIR="/opt/claudius/.claude/projects/-opt-omniops"

# Flag file coordination - Night Watch sets this while running auto-fix
NIGHT_WATCH_FLAG="/tmp/night-watch-active"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"
}

# Check if Night Watch is actively running auto-fix
is_night_watch_active() {
    if [ -f "$NIGHT_WATCH_FLAG" ]; then
        # Check if flag is stale (older than 30 minutes = stuck)
        FLAG_AGE=$(( $(date +%s) - $(stat -c %Y "$NIGHT_WATCH_FLAG" 2>/dev/null || echo 0) ))
        if [ "$FLAG_AGE" -gt 1800 ]; then
            log "Night Watch flag is stale (${FLAG_AGE}s old), removing"
            rm -f "$NIGHT_WATCH_FLAG"
            return 1
        fi
        return 0
    fi
    return 1
}

# 1. Kill runaway Claude processes if too many
# BUT respect Night Watch flag - if active, don't kill workers
CLAUDE_COUNT=$(pgrep -u claudius -f "^claude" | wc -l)
if [ "$CLAUDE_COUNT" -gt "$MAX_CLAUDE_PROCS" ]; then
    if is_night_watch_active; then
        log "WARNING: $CLAUDE_COUNT claude processes, but Night Watch is active - not killing"
    else
        log "WARNING: $CLAUDE_COUNT claude processes running (max $MAX_CLAUDE_PROCS)"
        # Kill oldest spawned claudes (not the main Claudius)
        OLDEST=$(pgrep -u claudius -f "^claude" -o)
        MAIN_CLAUDIUS=$(pgrep -u claudius -f "^claude" | head -1)

        for pid in $(pgrep -u claudius -f "^claude" | tail -n +$((MAX_CLAUDE_PROCS))); do
            if [ "$pid" != "$MAIN_CLAUDIUS" ]; then
                log "Killing excess claude process: $pid"
                kill -15 "$pid" 2>/dev/null
            fi
        done
    fi
fi

# 2. Clean up old session files (keep newest 20)
if [ -d "$SESSION_DIR" ]; then
    SESSION_COUNT=$(ls -1 "$SESSION_DIR"/*.jsonl 2>/dev/null | wc -l)
    if [ "$SESSION_COUNT" -gt "$MAX_SESSION_FILES" ]; then
        log "Cleaning up session files: $SESSION_COUNT found, keeping $MAX_SESSION_FILES"
        ls -1t "$SESSION_DIR"/*.jsonl 2>/dev/null | tail -n +$((MAX_SESSION_FILES + 1)) | while read f; do
            rm -f "$f"
            log "Removed old session: $(basename $f)"
        done
    fi
fi

# 3. Kill any tsc processes running longer than 5 minutes
# BUT respect Night Watch flag - if active, extend to 10 minutes
for pid in $(pgrep -f "npx tsc"); do
    RUNTIME=$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')
    if [ -n "$RUNTIME" ]; then
        # If Night Watch is active, allow 10 minutes; otherwise 5 minutes
        if is_night_watch_active; then
            MAX_TSC_TIME=600
        else
            MAX_TSC_TIME=300
        fi

        if [ "$RUNTIME" -gt "$MAX_TSC_TIME" ]; then
            log "Killing stuck tsc process: $pid (running ${RUNTIME}s, limit ${MAX_TSC_TIME}s)"
            kill -9 "$pid" 2>/dev/null
        fi
    fi
done

# 4. Clean up zombie node processes
ZOMBIES=$(ps aux | grep -E "\[node\].*defunct" | wc -l)
if [ "$ZOMBIES" -gt 0 ]; then
    log "Found $ZOMBIES zombie node processes"
    # Zombies need their parent to reap them, just log for now
fi

# 5. Check API responsiveness - restart if unresponsive
if systemctl is-active --quiet claudius-api.service; then
    HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:3100/health 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" != "200" ]; then
        # Check twice to avoid false positives
        sleep 3
        HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:3100/health 2>/dev/null || echo "000")
        if [ "$HTTP_CODE" != "200" ]; then
            log "CRITICAL: Claudius API unresponsive (HTTP $HTTP_CODE), restarting service"
            systemctl restart claudius-api.service
        fi
    fi
fi

# 6. Check memory and warn if low
AVAIL_GB=$(free -g | awk '/Mem:/ {print $7}')
if [ "$AVAIL_GB" -lt 2 ]; then
    log "CRITICAL: Only ${AVAIL_GB}GB RAM available"
    # Kill any non-essential claude spawns
    for pid in $(pgrep -u claudius -f "^claude" | tail -n +2); do
        MAIN_CLAUDIUS=$(pgrep -u claudius -f "^claude" | head -1)
        if [ "$pid" != "$MAIN_CLAUDIUS" ]; then
            log "Emergency kill of claude $pid due to low memory"
            kill -9 "$pid" 2>/dev/null
        fi
    done
fi

exit 0
