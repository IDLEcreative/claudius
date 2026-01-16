#!/usr/bin/env python3
"""
Orphan Process Reaper for Claudius

Finds and kills orphaned MCP server processes that were left behind
when their parent claude CLI process died or timed out.

Run periodically via cron or systemd timer:
  */5 * * * * /opt/claudius/scripts/orphan-reaper.py

Can also be called from can_spawn_fixer() before spawning new sessions.
"""

import subprocess
import os
import time
from datetime import datetime

# MCP server patterns to look for
MCP_PATTERNS = [
    "context7-mcp",
    "mcp-server-supabase",
    "workspace-mcp",
    "chrome-devtools-mcp",
    "mcp-server-playwright",
    "stripe/mcp",
    "tsx mcp/servers",
]

# Max age in seconds before killing orphan (5 minutes)
MAX_ORPHAN_AGE_SECONDS = 300

def get_claude_pids():
    """Get PIDs of all running claude CLI processes."""
    result = subprocess.run(
        ["pgrep", "-u", "claudius", "-f", "^claude"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        return set(result.stdout.strip().split('\n'))
    return set()

def get_mcp_processes():
    """Get all MCP server processes with their parent PIDs."""
    mcp_procs = []

    for pattern in MCP_PATTERNS:
        result = subprocess.run(
            ["pgrep", "-u", "claudius", "-f", pattern],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            for pid in result.stdout.strip().split('\n'):
                if pid:
                    mcp_procs.append(pid)

    return mcp_procs

def get_process_info(pid):
    """Get parent PID and start time of a process."""
    try:
        # Get parent PID
        with open(f"/proc/{pid}/stat", "r") as f:
            stat = f.read().split()
            ppid = stat[3]

        # Get start time (in clock ticks since boot)
        starttime = int(stat[21])

        # Get system boot time
        with open("/proc/uptime", "r") as f:
            uptime = float(f.read().split()[0])

        # Calculate process age
        clk_tck = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
        process_start_seconds = starttime / clk_tck
        current_uptime = uptime
        age_seconds = current_uptime - process_start_seconds

        return {"ppid": ppid, "age_seconds": age_seconds}
    except (FileNotFoundError, IndexError, ValueError):
        return None

def is_orphan(mcp_pid, claude_pids):
    """Check if an MCP process is orphaned (no parent claude process)."""
    info = get_process_info(mcp_pid)
    if not info:
        return False, 0

    ppid = info["ppid"]
    age = info["age_seconds"]

    # Check if parent is a claude process or init (1)
    # If parent is init (1), it's definitely orphaned
    if ppid == "1":
        return True, age

    # Check if parent is in our claude PIDs
    # Walk up the process tree to find if any ancestor is claude
    current_pid = ppid
    for _ in range(10):  # Max 10 levels up
        if current_pid in claude_pids:
            return False, age
        if current_pid == "1":
            return True, age

        parent_info = get_process_info(current_pid)
        if not parent_info:
            break
        current_pid = parent_info["ppid"]

    # If we can't find claude in ancestry, check age
    # Young processes might still be starting up
    return age > 60, age  # Consider orphan if >60s old with no claude parent

def kill_orphans(dry_run=False):
    """Find and kill orphaned MCP processes."""
    claude_pids = get_claude_pids()
    mcp_pids = get_mcp_processes()

    killed = []
    skipped = []

    for pid in mcp_pids:
        orphan, age = is_orphan(pid, claude_pids)

        if orphan and age > MAX_ORPHAN_AGE_SECONDS:
            if dry_run:
                print(f"[DRY RUN] Would kill orphan PID {pid} (age: {age:.0f}s)")
            else:
                try:
                    os.kill(int(pid), 9)  # SIGKILL
                    killed.append(pid)
                    print(f"[REAPER] Killed orphan PID {pid} (age: {age:.0f}s)")
                except ProcessLookupError:
                    pass
                except PermissionError:
                    print(f"[REAPER] Permission denied killing PID {pid}")
        elif orphan:
            skipped.append((pid, age))

    return killed, skipped

def cleanup_before_spawn():
    """Quick cleanup to call before spawning new claude sessions."""
    killed, _ = kill_orphans(dry_run=False)
    if killed:
        print(f"[REAPER] Cleaned up {len(killed)} orphan processes before spawn")
    return len(killed)

if __name__ == "__main__":
    import sys

    dry_run = "--dry-run" in sys.argv

    print(f"[REAPER] Starting orphan scan at {datetime.now().isoformat()}")
    print(f"[REAPER] Claude PIDs: {get_claude_pids()}")

    killed, skipped = kill_orphans(dry_run=dry_run)

    print(f"[REAPER] Killed: {len(killed)}, Skipped (too young): {len(skipped)}")

    if skipped:
        for pid, age in skipped:
            print(f"  - PID {pid}: {age:.0f}s old (threshold: {MAX_ORPHAN_AGE_SECONDS}s)")
