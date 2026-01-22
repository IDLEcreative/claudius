#!/usr/bin/env python3
"""
Claudius Night Watch v4.0 - BEEFED UP
=====================================
Deep overnight analysis while Jay sleeps.
NO auto-fixes, NO PRs - just thorough reporting.

What's new in v4:
- Multiple Clode calls for different analysis types
- Actual code pattern scanning (N+1, missing memoization, etc)
- LOC violation checks
- Better Telegram formatting
- Estimated 30-60 min runtime (thorough!)

Schedule: 2 AM nightly
Report sent: 7 AM via Telegram
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
import sys

# Config
CLODE_API = "http://localhost:3000/api/admin/clode"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
OWNER_CHAT_ID = "7070679785"
LOG_DIR = Path("/opt/claudius/logs")
REPORTS_DIR = Path("/opt/claudius/reports")
OMNIOPS_DIR = Path("/opt/omniops")

LOG_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)


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
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=str(OMNIOPS_DIR)
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT after {timeout}s]"
    except Exception as e:
        return f"[ERROR: {e}]"


def call_clode(prompt: str, timeout: int = 300, model: str = "sonnet") -> Optional[str]:
    """Call Clode for AI analysis."""
    if not ADMIN_SECRET:
        return None
    try:
        response = requests.post(
            CLODE_API,
            headers={"Authorization": f"Bearer {ADMIN_SECRET}", "Content-Type": "application/json"},
            json={"prompt": prompt, "model": model},
            timeout=timeout
        )
        if response.ok:
            data = response.json()
            return data.get("response") or data.get("result")
    except Exception as e:
        print(f"Clode error: {e}")
    return None


def send_telegram(message: str, parse_mode: str = "Markdown") -> bool:
    """Send message to Telegram with proper chunking."""
    if not TELEGRAM_BOT_TOKEN:
        return False
    try:
        max_len = 4000
        messages = [message[i:i+max_len] for i in range(0, len(message), max_len)]
        for msg in messages:
            resp = requests.post(
                TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN),
                json={"chat_id": OWNER_CHAT_ID, "text": msg, "parse_mode": parse_mode},
                timeout=30
            )
            if not resp.ok and parse_mode == "Markdown":
                # Retry without markdown if it fails
                requests.post(
                    TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN),
                    json={"chat_id": OWNER_CHAT_ID, "text": msg},
                    timeout=30
                )
            time.sleep(0.5)
        return True
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


class NightWatchV4:
    def __init__(self, quick_mode: bool = False):
        self.started = datetime.now()
        self.findings: List[Dict] = []
        self.log_lines: List[str] = []
        self.quick_mode = quick_mode  # For testing
        self.clode_analyses: Dict[str, str] = {}

    def log(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"
        print(line)
        self.log_lines.append(line)

    def add_finding(self, category: str, severity: str, title: str,
                    details: str = "", file_path: str = "", line_num: int = 0):
        self.findings.append({
            "category": category,
            "severity": severity,
            "title": title,
            "details": details,
            "file": file_path,
            "line": line_num
        })

    # ==================== PHASE 1: SYSTEM HEALTH ====================
    def phase1_system(self):
        self.log("=" * 50)
        self.log("PHASE 1: SYSTEM HEALTH")
        self.log("=" * 50)

        # Disk usage
        disk_output = run_cmd("df -h / | tail -1")
        parts = disk_output.split()
        if len(parts) >= 5:
            disk_percent = int(parts[4].replace("%", ""))
            self.log(f"Disk: {disk_percent}%")
            if disk_percent > 80:
                self.add_finding("system", "high", f"Disk usage critical: {disk_percent}%")
            elif disk_percent > 60:
                self.add_finding("system", "medium", f"Disk usage elevated: {disk_percent}%")

        # Memory
        mem_output = run_cmd("free -m | grep Mem")
        parts = mem_output.split()
        if len(parts) >= 3:
            total = int(parts[1])
            used = int(parts[2])
            percent = (used / total) * 100
            self.log(f"Memory: {percent:.0f}%")
            if percent > 85:
                self.add_finding("system", "high", f"Memory critical: {percent:.0f}%")

        # Docker health
        containers = run_cmd("docker ps --format '{{.Names}}: {{.Status}}' 2>/dev/null")
        for line in containers.split("\n"):
            if "unhealthy" in line.lower() or "restarting" in line.lower():
                self.add_finding("system", "critical", f"Container issue: {line}")

        # Container restarts in last 24h
        restarts = run_cmd("docker events --since 24h --until now --filter 'event=restart' 2>&1 | wc -l")
        try:
            restart_count = int(restarts.strip())
            if restart_count > 5:
                self.add_finding("system", "medium", f"{restart_count} container restarts in 24h")
        except:
            pass

        # App errors in logs
        errors = run_cmd("docker logs omniops-app-live --since 24h 2>&1 | grep -iE 'error|exception|fatal' | wc -l")
        try:
            error_count = int(errors.strip())
            self.log(f"App errors (24h): {error_count}")
            if error_count > 50:
                self.add_finding("system", "high", f"{error_count} errors in app logs (24h)")
            elif error_count > 10:
                self.add_finding("system", "medium", f"{error_count} errors in app logs (24h)")
        except:
            pass

    # ==================== PHASE 2: CODE QUALITY (Static) ====================
    def phase2_static_analysis(self):
        self.log("=" * 50)
        self.log("PHASE 2: STATIC CODE ANALYSIS")
        self.log("=" * 50)

        # TypeScript errors
        self.log("Running TypeScript check...")
        ts_output = run_cmd("npx tsc --noEmit 2>&1 | head -50", timeout=180)
        ts_errors = ts_output.count("error TS")
        self.log(f"TypeScript errors: {ts_errors}")
        if ts_errors > 0:
            self.add_finding("code", "medium", f"{ts_errors} TypeScript errors", ts_output[:500])

        # ESLint
        self.log("Running ESLint...")
        lint_output = run_cmd("npm run lint 2>&1 | tail -30", timeout=120)
        if "error" in lint_output.lower() and "0 errors" not in lint_output.lower():
            lint_errors = lint_output.count(" error ")
            self.add_finding("code", "low", f"ESLint errors found", lint_output[:300])

        # LOC violations (300 line limit)
        self.log("Checking LOC compliance...")
        loc_check = run_cmd("""
            find app lib components -name '*.ts' -o -name '*.tsx' 2>/dev/null | \
            xargs wc -l 2>/dev/null | \
            awk '$1 > 300 && !/total$/' | \
            sort -rn | head -15
        """)
        violations = [l for l in loc_check.strip().split("\n") if l.strip()]
        if violations:
            self.log(f"LOC violations: {len(violations)} files over 300 lines")
            for v in violations[:5]:
                parts = v.split()
                if len(parts) >= 2:
                    lines, filepath = parts[0], parts[1]
                    self.add_finding("code", "medium", f"LOC violation: {filepath} ({lines} lines)", file_path=filepath)

        # Check for common anti-patterns
        self.log("Scanning for anti-patterns...")

        # N+1 patterns (sequential awaits in loops)
        n1_pattern = run_cmd("""
            grep -rn 'for.*await\\|forEach.*await\\|map.*await' \
            --include='*.ts' --include='*.tsx' app/ lib/ 2>/dev/null | \
            grep -v node_modules | head -10
        """)
        if n1_pattern.strip():
            count = len(n1_pattern.strip().split("\n"))
            self.add_finding("performance", "high", f"{count} potential N+1 patterns (sequential awaits)", n1_pattern[:500])

        # Missing useMemo/useCallback
        hooks_without_memo = run_cmd("""
            grep -rn 'const.*=.*props\\.' --include='*.tsx' components/ app/ 2>/dev/null | \
            grep -v 'useMemo\\|useCallback\\|node_modules' | wc -l
        """)

        # as any usage
        as_any = run_cmd("grep -rn 'as any' --include='*.ts' --include='*.tsx' app/ lib/ 2>/dev/null | wc -l")
        try:
            any_count = int(as_any.strip())
            if any_count > 100:
                self.add_finding("code", "medium", f"{any_count} 'as any' assertions in codebase")
        except:
            pass

    # ==================== PHASE 3: SECURITY ====================
    def phase3_security(self):
        self.log("=" * 50)
        self.log("PHASE 3: SECURITY AUDIT")
        self.log("=" * 50)

        # npm audit
        self.log("Running npm audit...")
        audit = run_cmd("npm audit --json 2>/dev/null", timeout=120)
        try:
            audit_data = json.loads(audit)
            vulns = audit_data.get("metadata", {}).get("vulnerabilities", {})
            critical = vulns.get("critical", 0)
            high = vulns.get("high", 0)
            moderate = vulns.get("moderate", 0)

            self.log(f"Vulnerabilities: {critical} critical, {high} high, {moderate} moderate")

            if critical > 0:
                self.add_finding("security", "critical", f"{critical} critical npm vulnerabilities")
            if high > 0:
                self.add_finding("security", "high", f"{high} high npm vulnerabilities")
        except:
            pass

        # Hardcoded secrets check
        self.log("Scanning for hardcoded secrets...")
        secrets_patterns = [
            ("API keys", "sk-[a-zA-Z0-9]{20,}"),
            ("Passwords", "password\\s*=\\s*['\"][^'\"]+['\"]"),
            ("Tokens", "token\\s*=\\s*['\"][^'\"]{20,}['\"]"),
        ]

        for name, pattern in secrets_patterns:
            matches = run_cmd(f"grep -rEn '{pattern}' --include='*.ts' --include='*.tsx' app/ lib/ 2>/dev/null | grep -v node_modules | head -5")
            if matches.strip():
                self.add_finding("security", "critical", f"Potential {name} exposed", matches[:200])

        # SSL cert check
        cert_check = run_cmd("""
            echo | openssl s_client -servername omniops.uk -connect omniops.uk:443 2>/dev/null | \
            openssl x509 -noout -dates 2>/dev/null | grep notAfter
        """)
        if cert_check:
            try:
                expiry_str = cert_check.split("=")[1].strip()
                expiry = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
                days_left = (expiry - datetime.now()).days
                self.log(f"SSL cert expires in {days_left} days")
                if days_left < 14:
                    self.add_finding("security", "high", f"SSL expires in {days_left} days")
                elif days_left < 30:
                    self.add_finding("security", "medium", f"SSL expires in {days_left} days")
            except:
                pass

        # Exposed ports check
        exposed = run_cmd("docker-compose-safe ps 2>/dev/null | grep -E '0\\.0\\.0\\.0:[0-9]+' | wc -l")

    # ==================== PHASE 4: CLODE DEEP ANALYSIS ====================
    def phase4_clode_analysis(self):
        self.log("=" * 50)
        self.log("PHASE 4: CLODE DEEP ANALYSIS")
        self.log("=" * 50)

        if self.quick_mode:
            self.log("Quick mode - skipping Clode calls")
            return

        # Get list of largest/most complex files
        complex_files = run_cmd("""
            find app lib components -name '*.ts' -o -name '*.tsx' 2>/dev/null | \
            xargs wc -l 2>/dev/null | sort -rn | head -20
        """)

        # Analysis 1: Critical bugs and security
        self.log("Clode Analysis 1/3: Security & Bugs...")
        security_prompt = f"""Analyze these largest files for SECURITY issues and BUGS only:

{complex_files}

Focus on:
1. Auth bypass possibilities
2. Injection vulnerabilities (SQL, XSS, command)
3. Race conditions
4. Null/undefined crashes
5. Logic errors

For each issue found, provide:
- File path
- Line number (if possible)
- Severity: critical/high/medium
- Brief description

Be specific. No false positives. Only report real issues."""

        security_result = call_clode(security_prompt, timeout=300)
        if security_result:
            self.clode_analyses["security"] = security_result
            # Parse and add findings
            self._parse_clode_response(security_result, "security")

        time.sleep(2)  # Brief pause between calls

        # Analysis 2: Performance
        self.log("Clode Analysis 2/3: Performance...")
        perf_prompt = f"""Analyze these files for PERFORMANCE issues only:

{complex_files}

Focus on:
1. N+1 query patterns
2. Missing memoization (useMemo, useCallback)
3. Expensive computations in render
4. Large bundle imports
5. Sequential awaits that should be parallel

For each issue, provide:
- File path
- Severity: high/medium/low
- Brief fix suggestion

Be specific and actionable."""

        perf_result = call_clode(perf_prompt, timeout=300)
        if perf_result:
            self.clode_analyses["performance"] = perf_result
            self._parse_clode_response(perf_result, "performance")

        time.sleep(2)

        # Analysis 3: Tech debt & code quality
        self.log("Clode Analysis 3/3: Tech Debt...")
        debt_prompt = f"""Analyze for TECH DEBT and CODE QUALITY issues:

{complex_files}

Focus on:
1. Functions over 50 lines (need splitting)
2. Duplicate code patterns
3. TODO/FIXME that are blocking
4. Deprecated API usage
5. Missing error handling

For each issue:
- File path
- Severity
- Brief description

No style nitpicks. Focus on maintainability blockers."""

        debt_result = call_clode(debt_prompt, timeout=300)
        if debt_result:
            self.clode_analyses["tech_debt"] = debt_result
            self._parse_clode_response(debt_result, "code")

    def _parse_clode_response(self, response: str, category: str):
        """Extract structured findings from Clode response."""
        # Look for severity indicators
        lines = response.split("\n")
        for i, line in enumerate(lines):
            line_lower = line.lower()

            # Skip empty lines
            if not line.strip():
                continue

            # Detect severity
            severity = None
            if "critical" in line_lower:
                severity = "critical"
            elif "high" in line_lower:
                severity = "high"
            elif "medium" in line_lower:
                severity = "medium"
            elif "low" in line_lower:
                severity = "low"

            # If we found a severity marker, extract the finding
            if severity and (":" in line or "-" in line):
                # Clean up the line
                title = line.strip()
                # Remove markdown formatting
                title = re.sub(r'\*+', '', title)
                title = re.sub(r'^[-•*]\s*', '', title)

                # Extract file path if present
                file_match = re.search(r'[`"]?([a-zA-Z0-9/_-]+\.(ts|tsx))[`"]?', line)
                file_path = file_match.group(1) if file_match else ""

                if len(title) > 10 and len(title) < 200:
                    self.add_finding(category, severity, title[:150], file_path=file_path)

    # ==================== PHASE 5: OPPORTUNITY SCAN ====================
    def phase5_opportunities(self):
        self.log("=" * 50)
        self.log("PHASE 5: OPPORTUNITIES")
        self.log("=" * 50)

        # Outdated packages
        self.log("Checking for outdated packages...")
        outdated = run_cmd("npm outdated --json 2>/dev/null", timeout=60)
        try:
            data = json.loads(outdated) if outdated.strip() else {}
            major_updates = []
            for pkg, info in data.items():
                current = info.get("current", "0").split(".")[0]
                latest = info.get("latest", "0").split(".")[0]
                if current != latest:
                    major_updates.append(f"{pkg}: {info.get('current')} -> {info.get('latest')}")

            if major_updates:
                self.add_finding("opportunity", "low",
                               f"{len(major_updates)} packages have major updates",
                               "\n".join(major_updates[:10]))
        except:
            pass

        # TODO count
        todo_count = run_cmd("""
            grep -rn 'TODO\\|FIXME' --include='*.ts' --include='*.tsx' \
            app/ lib/ components/ 2>/dev/null | wc -l
        """)
        try:
            count = int(todo_count.strip())
            self.log(f"TODO/FIXME count: {count}")
            if count > 100:
                self.add_finding("opportunity", "info", f"{count} TODO/FIXME markers in codebase")
        except:
            pass

        # Test coverage gaps (files without tests)
        self.log("Checking test coverage...")
        lib_files = run_cmd("find lib -name '*.ts' | wc -l")
        test_files = run_cmd("find __tests__ -name '*.test.ts' | wc -l")
        try:
            lib_count = int(lib_files.strip())
            test_count = int(test_files.strip())
            ratio = test_count / max(lib_count, 1)
            self.log(f"Test ratio: {test_count} tests / {lib_count} lib files = {ratio:.2f}")
        except:
            pass

    # ==================== REPORT GENERATION ====================
    def generate_report(self) -> str:
        duration = datetime.now() - self.started

        lines = [
            "Night Watch Report",
            f"{self.started.strftime('%A, %d %B %Y')}",
            f"Analysis time: {duration.seconds // 60}m {duration.seconds % 60}s",
            "",
        ]

        # Count by severity
        critical = [f for f in self.findings if f["severity"] == "critical"]
        high = [f for f in self.findings if f["severity"] == "high"]
        medium = [f for f in self.findings if f["severity"] == "medium"]
        low = [f for f in self.findings if f["severity"] in ["low", "info"]]

        # Summary line
        lines.append(f"Found: {len(critical)} critical, {len(high)} high, {len(medium)} medium, {len(low)} low")
        lines.append("")

        if critical:
            lines.append("CRITICAL")
            for f in critical:
                lines.append(f"  - {f['title']}")
                if f.get("file"):
                    lines.append(f"    File: {f['file']}")
            lines.append("")

        if high:
            lines.append("HIGH")
            for f in high:
                lines.append(f"  - {f['title']}")
            lines.append("")

        if medium:
            lines.append("MEDIUM")
            for f in medium[:10]:  # Limit
                lines.append(f"  - {f['title']}")
            if len(medium) > 10:
                lines.append(f"  ... and {len(medium) - 10} more")
            lines.append("")

        if low:
            lines.append("LOW/INFO")
            for f in low[:5]:
                lines.append(f"  - {f['title']}")
            lines.append("")

        if not self.findings:
            lines.append("All clear! No issues found.")
            lines.append("")

        # Add Clode analysis summaries
        if self.clode_analyses:
            lines.append("-" * 30)
            lines.append("CLODE ANALYSIS HIGHLIGHTS")
            lines.append("")

            for analysis_type, content in self.clode_analyses.items():
                # Get first ~500 chars of each analysis
                summary = content[:600].strip()
                if len(content) > 600:
                    summary += "..."
                lines.append(f"{analysis_type.upper()}:")
                lines.append(summary)
                lines.append("")

        lines.append("-" * 30)
        lines.append("Full report: /opt/claudius/reports/")

        return "\n".join(lines)

    def save_reports(self):
        date_str = self.started.strftime("%Y%m%d")

        # Save markdown report
        md_report = self.generate_report()
        md_file = REPORTS_DIR / f"night-watch-{date_str}.md"
        with open(md_file, "w") as f:
            f.write(md_report)

        # Save JSON with full details
        json_file = REPORTS_DIR / f"night-watch-{date_str}.json"
        with open(json_file, "w") as f:
            json.dump({
                "version": "4.0",
                "started_at": self.started.isoformat(),
                "ended_at": datetime.now().isoformat(),
                "duration_seconds": (datetime.now() - self.started).seconds,
                "findings": self.findings,
                "clode_analyses": self.clode_analyses,
                "log": self.log_lines
            }, f, indent=2)

        self.log(f"Reports saved to {REPORTS_DIR}")
        return md_report

    def run(self):
        self.log("Night Watch v4.0 starting...")
        self.log(f"Quick mode: {self.quick_mode}")

        try:
            self.phase1_system()
            self.phase2_static_analysis()
            self.phase3_security()
            self.phase4_clode_analysis()
            self.phase5_opportunities()
        except Exception as e:
            self.log(f"ERROR during analysis: {e}")
            self.add_finding("system", "critical", f"Night watch error: {e}")

        report = self.save_reports()
        self.log("Night Watch v4.0 complete!")

        return report


def send_digest():
    """Send this morning's report."""
    today = datetime.now().strftime("%Y%m%d")
    report_file = REPORTS_DIR / f"night-watch-{today}.md"

    if not report_file.exists():
        # Try yesterday (in case running before midnight)
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        report_file = REPORTS_DIR / f"night-watch-{yesterday}.md"

    if not report_file.exists():
        print(f"No report found for today or yesterday")
        return

    with open(report_file) as f:
        report = f.read()

    # Format nicely for Telegram
    tg_report = report.replace("**", "*")  # Bold

    if send_telegram(tg_report):
        print("Sent digest successfully")
    else:
        print("Failed to send digest")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--send-digest":
            send_digest()
        elif sys.argv[1] == "--quick":
            # Quick test mode (no Clode calls)
            watch = NightWatchV4(quick_mode=True)
            report = watch.run()
            print("\n" + report)
        else:
            print("Usage: night-watch-v4.py [--send-digest|--quick]")
    else:
        watch = NightWatchV4()
        report = watch.run()
        print("\n" + "=" * 50)
        print(report)
