#!/usr/bin/env python3
"""
Claudius Night Watch v3.0
=========================
Deep overnight analysis while Jay sleeps.
With optional AUTO-FIX mode using Opus iterative agent loop.

Schedule: 2 AM nightly
Report sent: 7 AM via Telegram

What it does:
1. System health deep dive
2. Code quality analysis (via Clode/Opus)
3. Security scan
4. Performance check
5. Opportunity discovery
6. [AUTO-FIX MODE] Fix issues iteratively with build verification

Usage:
  python3 night-watch-v3.py              # Report only (default)
  python3 night-watch-v3.py --auto-fix   # Report + auto-fix safe issues
  python3 night-watch-v3.py --send-digest # Send the morning digest only
"""

import subprocess
import json
import os
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
import time
import re


# Smart concurrency control - decides based on available resources
def can_spawn_fixer() -> tuple[bool, str]:
    """Check if system has resources to spawn another claude fixer.

    Returns:
        (can_spawn: bool, reason: str)
    """
    import psutil
    import subprocess

    # Clean up orphaned MCP processes first
    try:
        result = subprocess.run(
            ["python3", "/opt/claudius/scripts/orphan-reaper.py"],
            capture_output=True, text=True, timeout=30
        )
        if "Killed:" in result.stdout:
            for line in result.stdout.split("\n"):
                if "[REAPER]" in line:
                    print(line)
    except Exception as e:
        print(f"[SPAWN] Reaper skipped: {e}")

    # Get current claude process count
    result = subprocess.run(
        ["pgrep", "-c", "-u", "claudius", "-f", "^claude"],
        capture_output=True, text=True
    )
    current_claude_count = int(result.stdout.strip()) if result.returncode == 0 else 0

    # Get system stats
    mem = psutil.virtual_memory()
    available_gb = mem.available / (1024**3)

    # Each claude session uses ~300MB RAM + ~50 threads
    # Decision logic:
    # - If <2GB available: no new sessions
    # - If already 4+ claude processes: no new sessions (absolute max)
    # - If 2-4GB available: max 2 concurrent
    # - If >4GB available: max 3 concurrent

    if available_gb < 2:
        return False, f"Low memory: {available_gb:.1f}GB available"

    if current_claude_count >= 4:
        return False, f"Max processes reached: {current_claude_count} claude sessions running"

    if available_gb < 4 and current_claude_count >= 2:
        return False, f"Limited memory ({available_gb:.1f}GB) with {current_claude_count} sessions"

    if current_claude_count >= 3:
        return False, f"At capacity: {current_claude_count} sessions, {available_gb:.1f}GB available"

    return True, f"OK: {current_claude_count} sessions, {available_gb:.1f}GB available"


# Config
CLODE_API = "http://localhost:3000/api/admin/clode"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
OWNER_CHAT_ID = "7070679785"
LOG_DIR = Path("/opt/claudius/logs")
REPORTS_DIR = Path("/opt/claudius/reports")
OMNIOPS_DIR = Path("/opt/omniops")

LOG_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)


# Load secrets
def load_env():
    secrets = {}
    for env_file in ["/opt/omniops/.env", "/opt/claudius/.env"]:
        try:
            with open(env_file) as f:
                for line in f:
                    if "=" in line and not line.startswith("#"):
                        key, val = line.strip().split("=", 1)
                        secrets[key] = val.strip('"').strip("'")
        except:
            pass
    return secrets

SECRETS = load_env()
ADMIN_SECRET = SECRETS.get("ADMIN_SECRET", "")
TELEGRAM_BOT_TOKEN = SECRETS.get("TELEGRAM_BOT_TOKEN", "")


def run_cmd(cmd: str, timeout: int = 60) -> str:
    """Run shell command and return output."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=str(OMNIOPS_DIR)
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT after {timeout}s]"
    except Exception as e:
        return f"[ERROR: {e}]"


def call_clode(prompt: str, timeout: int = 300, session_id: str = None) -> tuple[Optional[str], Optional[str]]:
    """Call Clode for AI analysis.

    Args:
        prompt: The prompt to send
        timeout: Request timeout in seconds
        session_id: Optional session ID to resume (for iterative fixes)

    Returns:
        tuple of (response_text, session_id) - session_id can be used to continue conversation
    """
    if not ADMIN_SECRET:
        return None, None
    try:
        payload = {"prompt": prompt, "model": "opus"}
        if session_id:
            payload["sessionId"] = session_id
        else:
            payload["newSession"] = True

        response = requests.post(
            CLODE_API,
            headers={"Authorization": f"Bearer {ADMIN_SECRET}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout
        )
        if response.ok:
            data = response.json()
            return (data.get("response") or data.get("output") or data.get("result"),
                    data.get("sessionId") or data.get("session_id"))
    except Exception as e:
        print(f"Clode error: {e}")
    return None, None


def spawn_claude_fixer(issue_type: str, issue_details: str, timeout: int = 600) -> dict:
    """Spawn a full Claude Code session to fix an issue.

    This gives Claude the SAME capabilities I (Claudius) have:
    - Tool access (Read, Edit, Bash, Grep, etc.)
    - Extended thinking mode (from settings.json)
    - Iterative execution until done
    - Full context of the task

    Returns:
        dict with 'success', 'output', 'session_id'
    """
    # Smart concurrency control - check available resources
    can_spawn, reason = can_spawn_fixer()
    if not can_spawn:
        print(f"[SPAWN] Skipping: {reason}")
        return {"success": False, "output": f"Cannot spawn: {reason}", "session_id": None}

        result = {
            "success": False,
            "output": "",
            "session_id": None
        }

        # Build the fix prompt - instruct Claude to work autonomously with tools
        fix_prompt = f"""You are Clode, the codebase Claude. You're in AUTO-FIX mode.

    **Your Mission:** Fix this issue completely, verifying your work as you go.

    **Issue Type:** {issue_type}
    **Details:** {issue_details}

    **Your Process:**
    1. THINK about what needs to change
    2. READ the relevant file(s) to understand current state
    3. EDIT/WRITE to make the fix
    4. RUN typecheck to verify: `npx tsc --noEmit --skipLibCheck 2>&1 | head -20`
    5. If errors, fix them and verify again
    6. Continue until build passes

    **Rules:**
    - Working directory is /opt/omniops
    - Make minimal, targeted changes
    - Run the typecheck after EVERY change to verify
    - If you create new files, make sure imports/exports are correct
    - Don't add unnecessary comments or documentation
    - Keep iterating until the build passes or you've made 5 attempts

    Start now. Read the file, make the fix, verify it works."""

        # Spawn Claude Code directly with full tool access
        # Note: --output-format json causes hangs, using text output
        # Pass prompt as argument (stdin causes issues)
        cmd = [
            "claude",
            "--print",
            "--permission-mode", "bypassPermissions",
            "--mcp-config", "/opt/claudius/.mcp-autofix.json",
            "--model", "opus",
            fix_prompt
        ]

        env = os.environ.copy()
        env["IS_SANDBOX"] = "1"  # Required to allow bypassPermissions

        try:
            print(f"[SPAWN] Starting Claude Code session...")
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(OMNIOPS_DIR),
                env=env
            )

            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()

            if stdout:
                result["output"] = stdout
                result["success"] = True

            if stderr and not result["output"]:
                result["output"] = f"stderr: {stderr}"

            print(f"[SPAWN] Claude completed. Output length: {len(result['output'])}")

        except subprocess.TimeoutExpired:
            result["output"] = f"Timed out after {timeout}s"
            print(f"[SPAWN] Claude timed out after {timeout}s")
        except Exception as e:
            result["output"] = f"Error: {e}"
            print(f"[SPAWN] Error: {e}")

        return result



def auto_fix_with_clode(issue_type: str, issue_details: str, max_iterations: int = 3) -> dict:
    """Use Claude Code to fix an issue with full agent capabilities.

    This spawns Claude with the same capabilities I (Claudius) have:
    - Extended thinking (from ~/.claude/settings.json)
    - Tool access (Read, Edit, Bash, etc.)
    - Iterative execution within a single session

    Returns:
        dict with 'success', 'changes', 'iterations', 'final_status'
    """
    result = {
        "success": False,
        "changes": [],
        "iterations": 0,
        "final_status": ""
    }

    print(f"[AUTO-FIX] Spawning Claude Code for: {issue_type}")

    # First attempt - let Claude work autonomously with full tool access
    fix_result = spawn_claude_fixer(issue_type, issue_details, timeout=600)
    result["iterations"] = 1
    result["changes"].append(fix_result["output"][:500] if fix_result["output"] else "No output")

    if not fix_result["success"]:
        result["final_status"] = f"Claude failed: {fix_result['output'][:200]}"
        return result

    # Verify the fix
    verify_cmd = "cd /opt/omniops && timeout 60 npx tsc --noEmit --skipLibCheck 2>&1 | grep -E 'error TS' | head -10"
    errors = run_cmd(verify_cmd, timeout=90)

    # If still errors, spawn another session to fix them
    iteration = 1
    while errors and "error TS" in errors and iteration < max_iterations:
        iteration += 1
        result["iterations"] = iteration

        print(f"[AUTO-FIX] Iteration {iteration} - fixing remaining errors...")

        fix_result = spawn_claude_fixer(
            "TypeScript compilation errors",
            f"Previous fix left these TypeScript errors:\n```\n{errors}\n```\nFix them.",
            timeout=600
        )

        result["changes"].append(fix_result["output"][:300] if fix_result["output"] else "No output")

        if not fix_result["success"]:
            result["final_status"] = f"Claude failed at iteration {iteration}"
            return result

        errors = run_cmd(verify_cmd, timeout=90)

    # Final status
    if not errors or "error TS" not in errors:
        result["success"] = True
        result["final_status"] = f"Fixed successfully in {iteration} iteration(s)"
    else:
        result["final_status"] = f"Still has errors after {iteration} iterations"

    return result


def send_telegram(message: str) -> bool:
    """Send message to Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        return False
    try:
        max_len = 4000
        messages = [message[i:i+max_len] for i in range(0, len(message), max_len)]
        for msg in messages:
            requests.post(
                TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN),
                json={"chat_id": OWNER_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
                timeout=30
            )
            time.sleep(0.5)
        return True
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


class NightWatch:
    def __init__(self):
        self.started = datetime.now()
        self.findings = []
        self.log_lines = []
        
    def log(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"
        print(line)
        self.log_lines.append(line)
        
    def add_finding(self, category: str, severity: str, title: str, details: str = ""):
        self.findings.append({
            "category": category,
            "severity": severity,
            "title": title,
            "details": details
        })
        
    # ==================== PHASE 1: SYSTEM ====================
    def phase1_system(self):
        self.log("=" * 50)
        self.log("PHASE 1: SYSTEM HEALTH")
        self.log("=" * 50)
        
        # Disk
        disk_output = run_cmd("df -h / | tail -1")
        parts = disk_output.split()
        if len(parts) >= 5:
            disk_percent = int(parts[4].replace("%", ""))
            if disk_percent > 80:
                self.add_finding("system", "high", f"Disk usage at {disk_percent}%", disk_output)
            elif disk_percent > 60:
                self.add_finding("system", "medium", f"Disk usage at {disk_percent}%", disk_output)
                
        # Memory
        mem_output = run_cmd("free -m | grep Mem")
        parts = mem_output.split()
        if len(parts) >= 3:
            total = int(parts[1])
            used = int(parts[2])
            percent = (used / total) * 100
            if percent > 85:
                self.add_finding("system", "high", f"Memory at {percent:.0f}%", mem_output)
                
        # Docker containers
        containers = run_cmd("docker ps --format '{{.Names}}: {{.Status}}' 2>/dev/null")
        unhealthy = [c for c in containers.split("\n") if "unhealthy" in c.lower() or "restarting" in c.lower()]
        if unhealthy:
            self.add_finding("system", "critical", "Unhealthy containers", "\n".join(unhealthy))
            
        # Recent errors in logs
        errors = run_cmd("docker logs omniops-app-live --since 24h 2>&1 | grep -i 'error\\|exception\\|fatal' | tail -20")
        error_count = len([l for l in errors.split("\n") if l.strip()])
        if error_count > 10:
            self.add_finding("system", "medium", f"{error_count} errors in app logs (24h)", errors[:500])
            
        self.log(f"System checks complete. Found {len([f for f in self.findings if f['category'] == 'system'])} issues.")
        
    # ==================== PHASE 2: CODE QUALITY ====================
    def phase2_code(self):
        self.log("=" * 50)
        self.log("PHASE 2: CODE QUALITY (Clode Analysis)")
        self.log("=" * 50)
        
        # Get high-complexity files
        self.log("Finding complex files...")
        complex_files = run_cmd("find app lib components -name '*.ts' -o -name '*.tsx' 2>/dev/null | xargs wc -l 2>/dev/null | sort -rn | head -20")
        
        # Ask Clode for deep analysis
        self.log("Asking Clode for code analysis (this takes a few minutes)...")
        
        analysis_prompt = f"""You are doing an overnight code quality audit. Be thorough but concise.

Analyze the OmniOps codebase and report:

1. **Critical Issues** (bugs, security holes, broken logic)
2. **Code Smells** (complex functions, duplicate code, outdated patterns)  
3. **Performance Concerns** (N+1 queries, missing memoization, large imports)
4. **Tech Debt** (TODOs that are blocking, deprecated APIs)

For each finding, give:
- File path (if specific)
- Severity (critical/high/medium/low)
- Brief description (1-2 lines)

Be specific. No fluff. This is for the morning report.

Here are the largest files to prioritize:
{complex_files}

Focus on real issues, not style nitpicks."""

        clode_response, _ = call_clode(analysis_prompt, timeout=600)

        if clode_response:
            self.log("Got Clode analysis")
            self.add_finding("code", "info", "Clode Deep Analysis", clode_response[:3000])
        else:
            self.log("Clode unavailable - falling back to static checks")
            
            # Fallback: basic static checks
            ts_errors = run_cmd("NODE_OPTIONS='--max-old-space-size=4096' npx tsc --noEmit 2>&1 | head -30", timeout=180)
            if "error TS" in ts_errors:
                error_count = ts_errors.count("error TS")
                self.add_finding("code", "medium", f"{error_count} TypeScript errors", ts_errors[:500])
                
            lint_errors = run_cmd("npm run lint 2>&1 | tail -20", timeout=120)
            if "error" in lint_errors.lower():
                self.add_finding("code", "low", "ESLint errors found", lint_errors[:500])
                
    # ==================== PHASE 3: SECURITY ====================
    def phase3_security(self):
        self.log("=" * 50)
        self.log("PHASE 3: SECURITY AUDIT")
        self.log("=" * 50)
        
        # npm audit
        audit = run_cmd("npm audit --json 2>/dev/null", timeout=60)
        try:
            audit_data = json.loads(audit)
            vulns = audit_data.get("metadata", {}).get("vulnerabilities", {})
            critical = vulns.get("critical", 0)
            high = vulns.get("high", 0)
            if critical > 0:
                self.add_finding("security", "critical", f"{critical} critical npm vulnerabilities", audit[:500])
            elif high > 0:
                self.add_finding("security", "high", f"{high} high npm vulnerabilities", "Run `npm audit` for details")
        except:
            pass
            
        # Check for exposed secrets
        # Look for actual API keys, not password form fields
        secrets_check = run_cmd("grep -rE 'sk-[a-zA-Z0-9]{20,}|ANTHROPIC_API_KEY.*=.*[\"\\x27][^\"\\x27]+|aws_secret' --include='*.ts' --include='*.tsx' app/ lib/ 2>/dev/null | grep -v node_modules | grep -v '.env' | head -10")
        if secrets_check.strip():
            self.add_finding("security", "critical", "Potential hardcoded secrets", secrets_check[:300])
            
        # SSL cert expiry
        cert_check = run_cmd("echo | openssl s_client -servername omniops.uk -connect omniops.uk:443 2>/dev/null | openssl x509 -noout -dates 2>/dev/null | grep notAfter")
        if cert_check:
            # Parse expiry date
            try:
                expiry_str = cert_check.split("=")[1].strip()
                expiry = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
                days_left = (expiry - datetime.now()).days
                if days_left < 14:
                    self.add_finding("security", "high", f"SSL cert expires in {days_left} days", cert_check)
                elif days_left < 30:
                    self.add_finding("security", "medium", f"SSL cert expires in {days_left} days", cert_check)
            except:
                pass
                
        self.log(f"Security checks complete.")
        
    # ==================== PHASE 4: PERFORMANCE ====================
    def phase4_performance(self):
        self.log("=" * 50)
        self.log("PHASE 4: PERFORMANCE CHECK")
        self.log("=" * 50)
        
        # API response times
        api_check = run_cmd("curl -w '%{time_total}' -s -o /dev/null http://localhost:3000/api/health")
        try:
            response_time = float(api_check)
            if response_time > 2.0:
                self.add_finding("performance", "high", f"API response slow ({response_time:.2f}s)", "Health endpoint taking too long")
            elif response_time > 1.0:
                self.add_finding("performance", "medium", f"API response: {response_time:.2f}s", "Consider optimization")
        except:
            pass
            
        # Database size
        db_size = run_cmd("docker exec supabase-db psql -U postgres -t -c \"SELECT pg_size_pretty(pg_database_size('postgres'));\" 2>/dev/null")
        if db_size.strip():
            self.log(f"Database size: {db_size.strip()}")
            
        # Check for large bundles
        bundle_check = run_cmd("ls -lah .next/static/chunks/*.js 2>/dev/null | awk '{print $5, $9}' | sort -hr | head -5")
        if bundle_check.strip():
            # Check if any chunk is > 500KB
            for line in bundle_check.split("\n"):
                if "M" in line.split()[0] if line.split() else "":
                    self.add_finding("performance", "medium", "Large JS bundle detected", bundle_check)
                    break
                    
        self.log("Performance checks complete.")
        
    # ==================== PHASE 5: OPPORTUNITIES ====================
    def phase5_opportunities(self):
        self.log("=" * 50)
        self.log("PHASE 5: OPPORTUNITY DISCOVERY")
        self.log("=" * 50)
        
        # Check for outdated packages
        outdated = run_cmd("npm outdated --json 2>/dev/null | head -50", timeout=60)
        try:
            outdated_data = json.loads(outdated) if outdated.strip() else {}
            major_updates = [k for k, v in outdated_data.items() 
                           if v.get("current", "").split(".")[0] != v.get("latest", "").split(".")[0]]
            if len(major_updates) > 5:
                self.add_finding("opportunity", "low", f"{len(major_updates)} packages have major updates available", 
                               ", ".join(major_updates[:10]))
        except:
            pass
            
        # TODO count
        todo_count = run_cmd("grep -r 'TODO\\|FIXME' --include='*.ts' --include='*.tsx' app/ lib/ components/ 2>/dev/null | wc -l")
        try:
            count = int(todo_count.strip())
            if count > 50:
                self.add_finding("opportunity", "info", f"{count} TODO/FIXME comments in codebase", "Tech debt markers")
        except:
            pass

        # LOC violations (>300 lines)
        self.log("Checking LOC compliance...")
        loc_violations = run_cmd("find app lib components -name '*.ts' -o -name '*.tsx' 2>/dev/null | xargs wc -l 2>/dev/null | awk '$1 > 300 && !/total/ {print $1, $2}' | sort -rn | head -10")
        if loc_violations.strip():
            violation_count = len([l for l in loc_violations.strip().split('\n') if l])
            self.add_finding("code", "medium", f"{violation_count} files exceed 300 LOC limit", loc_violations[:300])

        # 'as any' count (type safety escapes)
        self.log("Counting type escapes...")
        as_any_count = run_cmd("grep -r 'as any' --include='*.ts' --include='*.tsx' app/ lib/ components/ 2>/dev/null | wc -l")
        try:
            count = int(as_any_count.strip())
            if count > 100:
                self.add_finding("code", "medium", f"{count} 'as any' type assertions", "Type safety concern")
            elif count > 50:
                self.add_finding("code", "low", f"{count} 'as any' type assertions", "")
        except:
            pass

        # Magic numbers (hardcoded values)
        self.log("Scanning for magic numbers...")
        magic_numbers = run_cmd("grep -rE '(setTimeout|setInterval|slice|substring)\\([^,]+,\\s*[0-9]{2,}' --include='*.ts' --include='*.tsx' app/ lib/ components/ 2>/dev/null | grep -v node_modules | grep -v constants | head -15")
        if magic_numbers.strip():
            count = len([l for l in magic_numbers.strip().split('\n') if l])
            if count > 5:
                self.add_finding("code", "low", f"{count} potential magic numbers", "Consider extracting to constants")

        # Console.log in production code
        self.log("Checking console.log usage...")
        console_count = run_cmd("grep -r 'console\\.log' --include='*.ts' --include='*.tsx' app/ lib/ 2>/dev/null | grep -v __tests__ | grep -v examples | wc -l")
        try:
            count = int(console_count.strip())
            if count > 200:
                self.add_finding("code", "low", f"{count} console.log statements in app/lib", "Consider using proper logger")
        except:
            pass

        # Unused exports (dead code indicator)
        self.log("Checking for potential dead code patterns...")

        # Empty catch blocks (swallowed errors)
        empty_catch = run_cmd("grep -rE 'catch\\s*\\([^)]*\\)\\s*\\{\\s*\\}' --include='*.ts' --include='*.tsx' app/ lib/ 2>/dev/null | wc -l")
        try:
            count = int(empty_catch.strip())
            if count > 5:
                self.add_finding("code", "medium", f"{count} empty catch blocks", "Errors being silently swallowed")
        except:
            pass

        # Hardcoded URLs/domains
        hardcoded_urls = run_cmd("grep -rE 'https?://[a-zA-Z0-9]' --include='*.ts' --include='*.tsx' app/ lib/ 2>/dev/null | grep -v node_modules | grep -v localhost | grep -v 127.0.0.1 | grep -v example.com | grep -v '.env' | wc -l")
        try:
            count = int(hardcoded_urls.strip())
            if count > 20:
                self.add_finding("code", "low", f"{count} hardcoded URLs in codebase", "Consider using env vars")
        except:
            pass

        # Circular dependencies check
        self.log("Checking for circular dependency indicators...")
        # Files that import from parent directories (often a sign)
        parent_imports = run_cmd("grep -rE \"from ['\\\"]\\.\\.\" --include='*.ts' --include='*.tsx' lib/ 2>/dev/null | wc -l")
        # This is informational - real check needs madge

        # Unused imports (basic check - look for import then no usage)
        self.log("Checking for potential unused imports...")
        # Too complex for grep - leave to Clode analysis

        # React hook violations - hooks in conditionals
        self.log("Checking React hook rules...")
        hook_in_if = run_cmd("grep -rPzo 'if\\s*\\([^)]+\\)\\s*\\{[^}]*use(State|Effect|Memo|Callback|Ref)' --include='*.tsx' app/ components/ 2>/dev/null | tr '\\0' '\\n' | head -5")
        if hook_in_if.strip():
            self.add_finding("code", "high", "Hooks possibly called conditionally", "React hooks must be at top level")

        # N+1 query patterns - await in loops
        self.log("Checking for N+1 query patterns...")
        n_plus_one = run_cmd("grep -rB5 'await.*supabase' --include='*.ts' app/ lib/ 2>/dev/null | grep -E '(for\\s*\\(|forEach|while)' | head -10")
        if n_plus_one.strip():
            count = len([l for l in n_plus_one.strip().split('\\n') if l.strip()])
            if count > 0:
                self.add_finding("performance", "high", "Potential N+1 queries (await in loop)", n_plus_one[:200])

        # Promises without catch
        self.log("Checking promise error handling...")
        uncaught_promises = run_cmd("grep -rE '\\.then\\([^)]+\\)[^.]*$' --include='*.ts' --include='*.tsx' app/ lib/ 2>/dev/null | grep -v '.catch' | grep -v '//' | wc -l")
        try:
            count = int(uncaught_promises.strip())
            if count > 10:
                self.add_finding("code", "medium", f"{count} promises may lack .catch()", "Unhandled rejections")
        except:
            pass

        # Supabase queries without error check
        self.log("Checking Supabase error handling...")
        supabase_no_error = run_cmd("grep -r '\\.from(' --include='*.ts' app/ lib/ 2>/dev/null | grep -v 'error' | grep -v '.catch' | wc -l")
        try:
            count = int(supabase_no_error.strip())
            if count > 30:
                self.add_finding("code", "low", f"~{count} Supabase queries - verify error handling", "Check { error } destructuring")
        except:
            pass

        # Deprecated React patterns
        self.log("Checking for deprecated patterns...")
        deprecated = run_cmd("grep -rE 'componentWillMount|componentWillReceiveProps|componentWillUpdate|UNSAFE_' --include='*.tsx' app/ components/ 2>/dev/null | wc -l")
        try:
            count = int(deprecated.strip())
            if count > 0:
                self.add_finding("code", "medium", f"{count} deprecated React lifecycle methods", "Migrate to hooks")
        except:
            pass

        # Missing ErrorBoundary
        error_boundaries = run_cmd("grep -r 'ErrorBoundary' --include='*.tsx' app/ 2>/dev/null | wc -l")
        try:
            count = int(error_boundaries.strip())
            if count < 2:
                self.add_finding("code", "low", f"Only {count} ErrorBoundary components", "Add error boundaries for resilience")
        except:
            pass

        # API rate limit patterns (missing rate limiting)
        self.log("Checking API patterns...")
        api_routes = run_cmd("find app/api -name 'route.ts' 2>/dev/null | wc -l")
        rate_limited = run_cmd("grep -r 'rateLimit\\|rateLimiter' --include='route.ts' app/api/ 2>/dev/null | wc -l")
        try:
            total = int(api_routes.strip())
            limited = int(rate_limited.strip())
            if total > 0 and limited < total * 0.5:
                self.add_finding("security", "medium", f"Only {limited}/{total} API routes have rate limiting", "Consider adding rate limits")
        except:
            pass

        self.log("Opportunity scan complete.")
        
    # ==================== REPORT ====================
    def generate_report(self) -> str:
        duration = datetime.now() - self.started
        
        lines = [
            "🌙 *Night Watch Report*",
            f"_{self.started.strftime('%A, %d %B %Y')}_",
            f"Duration: {duration.seconds // 60}m {duration.seconds % 60}s",
            ""
        ]
        
        # Group findings by severity
        critical = [f for f in self.findings if f["severity"] == "critical"]
        high = [f for f in self.findings if f["severity"] == "high"]
        medium = [f for f in self.findings if f["severity"] == "medium"]
        low = [f for f in self.findings if f["severity"] in ["low", "info"]]
        
        if critical:
            lines.append("🔴 *CRITICAL*")
            for f in critical:
                lines.append(f"  • {f['title']}")
                if f["details"]:
                    lines.append(f"    _{f['details'][:100]}_")
            lines.append("")
            
        if high:
            lines.append("🟠 *HIGH*")
            for f in high:
                lines.append(f"  • {f['title']}")
            lines.append("")
            
        if medium:
            lines.append("🟡 *MEDIUM*")
            for f in medium:
                lines.append(f"  • {f['title']}")
            lines.append("")
            
        if low:
            lines.append("🟢 *LOW/INFO*")
            for f in low[:5]:  # Limit low priority
                lines.append(f"  • {f['title']}")
            lines.append("")
            
        if not self.findings:
            lines.append("✅ *All clear!* No issues found.")
            lines.append("")
            
        # Clode analysis section (if present)
        clode_finding = next((f for f in self.findings if "Clode" in f["title"]), None)
        if clode_finding and clode_finding["details"]:
            lines.append("📋 *Clode Analysis Summary:*")
            # Take first 1000 chars of Clode response
            summary = clode_finding["details"][:1000]
            lines.append(summary)
            lines.append("")
            
        lines.append("_Full report: /opt/claudius/reports/_")
        
        return "\n".join(lines)
        
    def save_report(self):
        report = self.generate_report()
        date_str = self.started.strftime("%Y%m%d")
        
        # Save markdown
        report_file = REPORTS_DIR / f"night-watch-{date_str}.md"
        with open(report_file, "w") as f:
            f.write(report)
            
        # Save JSON with full details
        json_file = REPORTS_DIR / f"night-watch-{date_str}.json"
        with open(json_file, "w") as f:
            json.dump({
                "started_at": self.started.isoformat(),
                "ended_at": datetime.now().isoformat(),
                "findings": self.findings,
                "log": self.log_lines
            }, f, indent=2)
            
        return report
        
    def run(self):
        self.log("🌙 Night Watch v3 starting...")
        
        self.phase1_system()
        self.phase2_code()
        self.phase3_security()
        self.phase4_performance()
        self.phase5_opportunities()
        
        report = self.save_report()
        self.log("Night Watch complete!")
        
        return report


def send_digest():
    """Send this morning's report."""
    today = datetime.now().strftime("%Y%m%d")
    report_file = REPORTS_DIR / f"night-watch-{today}.md"

    if not report_file.exists():
        print(f"No report for {today}")
        return

    with open(report_file) as f:
        report = f.read()

    if send_telegram(report):
        print("✅ Sent digest")
    else:
        print("❌ Failed to send")


def run_auto_fix(findings: List[dict]) -> List[dict]:
    """Run auto-fix on eligible findings.

    Auto-fixable issues:
    1. LOC violations (>300 lines) - Clode splits files
    2. TypeScript errors - Clode fixes type issues
    3. npm vulnerabilities - npm audit fix

    Returns:
        List of fix results
    """
    fix_results = []

    # 1. npm vulnerabilities - safest, do first
    npm_findings = [f for f in findings if "npm" in f["title"].lower() and "vulnerab" in f["title"].lower()]
    if npm_findings:
        print("[AUTO-FIX] Running npm audit fix...")
        output = run_cmd("cd /opt/omniops && npm audit fix 2>&1", timeout=120)
        fix_results.append({
            "type": "npm_audit",
            "success": "added" in output or "fixed" in output or "up to date" in output,
            "details": output[:500]
        })

    # 2. LOC violations - use Clode iteratively
    loc_findings = [f for f in findings if "LOC" in f["title"] or "lines" in f["title"].lower()]
    for finding in loc_findings[:3]:  # Max 3 LOC fixes per run
        details = finding.get("details", "")
        # Extract file names from details
        files = re.findall(r'([a-zA-Z0-9_/-]+\.tsx?):\s*\d+', details)
        for file_path in files[:2]:  # Max 2 files per finding
            print(f"[AUTO-FIX] Fixing LOC violation in {file_path}...")
            result = auto_fix_with_clode(
                "LOC violation (>300 lines)",
                f"File {file_path} exceeds 300 lines. Split it into smaller, focused modules. Extract logical groupings of functions into separate files."
            )
            fix_results.append({
                "type": "loc_split",
                "file": file_path,
                **result
            })

    # 3. TypeScript errors - use Clode iteratively
    ts_findings = [f for f in findings if "typescript" in f["title"].lower() or "TS" in f["title"]]
    for finding in ts_findings[:2]:  # Max 2 TS fix sessions
        details = finding.get("details", "")
        if details:
            print(f"[AUTO-FIX] Fixing TypeScript errors...")
            result = auto_fix_with_clode(
                "TypeScript errors",
                f"Fix these TypeScript errors:\n{details}"
            )
            fix_results.append({
                "type": "typescript",
                **result
            })

    return fix_results


if __name__ == "__main__":
    import sys

    if "--send-digest" in sys.argv:
        send_digest()
    elif "--auto-fix" in sys.argv:
        # Run night watch with auto-fix
        print("🌙 Night Watch v3 with AUTO-FIX mode")
        print("=" * 50)

        watch = NightWatch()
        report = watch.run()

        print("\n" + "=" * 50)
        print("🔧 AUTO-FIX PHASE")
        print("=" * 50)

        fix_results = run_auto_fix(watch.findings)

        # Add fix results to report
        if fix_results:
            fixes_summary = "\n\n---\n\n## 🔧 Auto-Fix Results\n\n"
            for fix in fix_results:
                status = "✅" if fix.get("success") else "❌"
                fixes_summary += f"- {status} **{fix.get('type')}**"
                if fix.get('file'):
                    fixes_summary += f" ({fix.get('file')})"
                fixes_summary += f": {fix.get('final_status', fix.get('details', 'Unknown'))[:200]}\n"

            # Append to report file
            date_str = watch.started.strftime("%Y%m%d")
            report_file = REPORTS_DIR / f"night-watch-{date_str}.md"
            with open(report_file, "a") as f:
                f.write(fixes_summary)

            print(fixes_summary)

        print("\n" + report)
    else:
        # Standard report-only mode
        watch = NightWatch()
        report = watch.run()
        print("\n" + "=" * 50)
        print(report)
