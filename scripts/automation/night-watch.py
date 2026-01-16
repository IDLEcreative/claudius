#!/usr/bin/env python3
"""
Claudius Night Watch v2.0
=========================
8 hours of autonomous work while Jay sleeps.

Schedule:
- 2:00 AM: Start night shift
- 2:00-3:00: System deep dive
- 3:00-5:00: Code quality audit with Clode
- 5:00-6:00: Security audit
- 6:00-7:00: Opportunity discovery
- 7:00-8:00: Safe fixes + report generation
- 8:00 AM: Morning digest to Telegram

Philosophy: Be thorough, be proactive, be safe with changes.
"""

import subprocess
import json
import os
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from pathlib import Path
import time
import re

# Configuration
CLAUDIUS_API = "http://localhost:3100/invoke"
CLODE_API = "http://localhost:3000/api/admin/clode"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
OWNER_CHAT_ID = "7070679785"
LOG_DIR = "/opt/claudius/logs"
REPORTS_DIR = "/opt/claudius/reports"
OMNIOPS_DIR = "/opt/omniops"

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
CRON_SECRET = SECRETS.get("CRON_SECRET", "")
ADMIN_SECRET = SECRETS.get("ADMIN_SECRET", "")
TELEGRAM_BOT_TOKEN = SECRETS.get("TELEGRAM_BOT_TOKEN", "")


@dataclass
class Finding:
    """A single finding from analysis"""
    category: str  # system, security, code, performance, opportunity
    severity: str  # critical, high, medium, low, info
    title: str
    description: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    auto_fixable: bool = False
    fixed: bool = False


@dataclass
class NightShiftReport:
    """Comprehensive night shift report"""
    started_at: datetime = field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None

    # Findings by category
    findings: List[Finding] = field(default_factory=list)

    # Metrics
    system_metrics: Dict[str, Any] = field(default_factory=dict)
    code_metrics: Dict[str, Any] = field(default_factory=dict)

    # Actions taken
    fixes_applied: List[str] = field(default_factory=list)
    prs_created: List[str] = field(default_factory=list)

    # Errors during execution
    errors: List[str] = field(default_factory=list)


class NightShift:
    """The comprehensive autonomous night shift worker"""

    def __init__(self):
        self.report = NightShiftReport()
        self.log(f"🌙 Night shift starting at {self.report.started_at}")
        os.makedirs(LOG_DIR, exist_ok=True)
        os.makedirs(REPORTS_DIR, exist_ok=True)

    def log(self, msg: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {msg}")
        with open(f"{LOG_DIR}/night-watch.log", "a") as f:
            f.write(f"[{timestamp}] {msg}\n")

    def add_finding(self, category: str, severity: str, title: str,
                    description: str, file_path: str = None,
                    line_number: int = None, auto_fixable: bool = False):
        self.report.findings.append(Finding(
            category=category,
            severity=severity,
            title=title,
            description=description,
            file_path=file_path,
            line_number=line_number,
            auto_fixable=auto_fixable
        ))

    def run_command(self, cmd: str, timeout: int = 120) -> Tuple[int, str, str]:
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except Exception as e:
            return -1, "", str(e)

    def call_claudius(self, prompt: str, timeout: int = 600) -> Optional[str]:
        """Call myself for complex analysis - longer timeout for thorough work"""
        if not CRON_SECRET:
            return None
        try:
            response = requests.post(
                CLAUDIUS_API,
                headers={"Authorization": f"Bearer {CRON_SECRET}", "Content-Type": "application/json"},
                json={"prompt": prompt, "model": "opus"},
                timeout=timeout
            )
            if response.ok:
                data = response.json()
                return data.get("response") or data.get("result")
        except Exception as e:
            self.report.errors.append(f"Claudius API: {e}")
        return None

    def call_clode(self, prompt: str, timeout: int = 600) -> Optional[str]:
        """Call Clode for code analysis - longer timeout for thorough work"""
        if not ADMIN_SECRET:
            return None
        try:
            response = requests.post(
                CLODE_API,
                headers={"Authorization": f"Bearer {ADMIN_SECRET}", "Content-Type": "application/json"},
                json={"prompt": prompt, "model": "sonnet"},
                timeout=timeout
            )
            if response.ok:
                data = response.json()
                return data.get("response") or data.get("result")
        except Exception as e:
            self.report.errors.append(f"Clode API: {e}")
        return None

    def send_telegram(self, message: str) -> bool:
        if not TELEGRAM_BOT_TOKEN:
            return False
        try:
            # Split long messages
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
            self.log(f"Telegram error: {e}")
            return False

    # ==================== PHASE 1: SYSTEM DEEP DIVE ====================

    def phase1_system_analysis(self):
        """Deep system analysis - 1 hour"""
        self.log("=" * 50)
        self.log("PHASE 1: SYSTEM DEEP DIVE")
        self.log("=" * 50)

        # Basic health
        self._check_disk_usage()
        self._check_memory_usage()
        self._check_docker_health()
        self._check_docker_images()
        self._check_logs_for_errors()
        self._check_database_health()
        self._check_api_response_times()

    def _check_disk_usage(self):
        self.log("Checking disk usage...")
        code, out, _ = self.run_command("df -h / | tail -1")
        if code == 0:
            parts = out.split()
            if len(parts) >= 5:
                usage = int(parts[4].replace('%', ''))
                self.report.system_metrics["disk_percent"] = usage
                self.report.system_metrics["disk_used"] = parts[2]
                self.report.system_metrics["disk_total"] = parts[1]

                if usage > 90:
                    self.add_finding("system", "critical", "Disk usage critical",
                                   f"Root disk is {usage}% full. Immediate cleanup required.")
                elif usage > 80:
                    self.add_finding("system", "high", "Disk usage high",
                                   f"Root disk is {usage}% full. Consider cleanup soon.")

        # Check for large files
        code, out, _ = self.run_command("find /opt -type f -size +100M 2>/dev/null | head -10")
        if code == 0 and out.strip():
            large_files = out.strip().split('\n')
            self.add_finding("system", "info", "Large files found",
                           f"Found {len(large_files)} files >100MB in /opt")

    def _check_memory_usage(self):
        self.log("Checking memory usage...")
        code, out, _ = self.run_command("free -m | grep Mem")
        if code == 0:
            parts = out.split()
            if len(parts) >= 3:
                total = int(parts[1])
                used = int(parts[2])
                percent = int((used / total) * 100)
                self.report.system_metrics["memory_percent"] = percent
                self.report.system_metrics["memory_used_mb"] = used
                self.report.system_metrics["memory_total_mb"] = total

                if percent > 95:
                    self.add_finding("system", "critical", "Memory critical",
                                   f"Memory at {percent}% ({used}MB/{total}MB)")
                elif percent > 85:
                    self.add_finding("system", "high", "Memory high",
                                   f"Memory at {percent}% ({used}MB/{total}MB)")

    def _check_docker_health(self):
        self.log("Checking Docker containers...")
        code, out, _ = self.run_command("docker ps --format '{{.Names}}|{{.Status}}|{{.Image}}'")
        if code == 0:
            containers = []
            for line in out.strip().split('\n'):
                if '|' in line:
                    name, status, image = line.split('|')
                    containers.append({"name": name, "status": status, "image": image})

                    if "unhealthy" in status.lower():
                        self.add_finding("system", "critical", f"Container unhealthy: {name}",
                                       f"Status: {status}")
                    elif "restarting" in status.lower():
                        self.add_finding("system", "high", f"Container restarting: {name}",
                                       f"Container is in restart loop")

            self.report.system_metrics["containers"] = containers

    def _check_docker_images(self):
        self.log("Checking Docker images for cleanup opportunities...")
        code, out, _ = self.run_command("docker images --format '{{.Repository}}:{{.Tag}}|{{.Size}}|{{.CreatedSince}}'")
        if code == 0:
            # Check for dangling images
            code2, out2, _ = self.run_command("docker images -f 'dangling=true' -q | wc -l")
            if code2 == 0 and int(out2.strip()) > 0:
                self.add_finding("system", "low", "Dangling Docker images",
                               f"{out2.strip()} dangling images can be cleaned up with 'docker image prune'",
                               auto_fixable=True)

    def _check_logs_for_errors(self):
        self.log("Analyzing logs for errors...")

        # OmniOps container logs
        code, out, _ = self.run_command(
            "docker logs omniops-app-green --since 24h 2>&1 | grep -iE 'error|exception|failed|fatal' | wc -l"
        )
        if code == 0 and out.strip().isdigit():
            errors = int(out.strip())
            if errors > 50:
                self.add_finding("system", "high", "High error rate in OmniOps",
                               f"{errors} errors in last 24h. Review logs for patterns.")
            elif errors > 10:
                self.add_finding("system", "medium", "Errors in OmniOps logs",
                               f"{errors} errors in last 24h")
            self.report.system_metrics["omniops_errors_24h"] = errors

    def _check_database_health(self):
        self.log("Checking database health...")
        # This would need actual DB access - placeholder for now
        pass

    def _check_api_response_times(self):
        self.log("Checking API response times...")
        # Quick health check timing
        start = time.time()
        code, _, _ = self.run_command("curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/api/health")
        elapsed = (time.time() - start) * 1000

        self.report.system_metrics["api_response_ms"] = round(elapsed, 2)
        if elapsed > 1000:
            self.add_finding("performance", "medium", "Slow API response",
                           f"Health check took {elapsed:.0f}ms")

    # ==================== PHASE 2: CODE QUALITY AUDIT ====================

    def phase2_code_quality(self):
        """Deep code quality audit with Clode - 2 hours"""
        self.log("=" * 50)
        self.log("PHASE 2: CODE QUALITY AUDIT")
        self.log("=" * 50)

        # Run ESLint
        self._run_eslint()

        # Run TypeScript strict check
        self._run_typescript_check()

        # Ask Clode for deep analysis
        self._clode_deep_analysis()

        # Check for common issues
        self._check_console_logs()
        self._check_todo_comments()
        self._check_dead_code()

    def _run_eslint(self):
        self.log("Running ESLint...")
        code, out, err = self.run_command(
            f"cd {OMNIOPS_DIR} && npm run lint 2>&1 | tail -50",
            timeout=300
        )

        if "error" in (out + err).lower():
            # Count errors
            error_count = len(re.findall(r'\d+:\d+\s+error', out + err))
            warning_count = len(re.findall(r'\d+:\d+\s+warning', out + err))

            self.report.code_metrics["eslint_errors"] = error_count
            self.report.code_metrics["eslint_warnings"] = warning_count

            if error_count > 0:
                self.add_finding("code", "medium", f"ESLint: {error_count} errors",
                               f"Plus {warning_count} warnings. Run 'npm run lint' for details.")

    def _run_typescript_check(self):
        self.log("Running TypeScript check...")
        code, out, err = self.run_command(
            f"cd {OMNIOPS_DIR} && npx tsc --noEmit 2>&1 | grep -E 'error TS' | wc -l",
            timeout=300
        )

        if code == 0 and out.strip().isdigit():
            ts_errors = int(out.strip())
            self.report.code_metrics["typescript_errors"] = ts_errors

            if ts_errors > 0:
                self.add_finding("code", "medium", f"TypeScript: {ts_errors} errors",
                               "Run 'npx tsc --noEmit' for details")

    def _clode_deep_analysis(self):
        self.log("Asking Clode for deep code analysis...")

        prompt = """You are doing an overnight code quality audit of the OmniOps codebase.

Analyze thoroughly and find:

1. **Bugs & Logic Errors**
   - Potential null/undefined errors
   - Race conditions
   - Incorrect error handling
   - Edge cases not handled

2. **Tech Debt**
   - Complex functions that need refactoring (cyclomatic complexity > 10)
   - Duplicate code patterns
   - Outdated patterns or deprecated APIs
   - Inconsistent code style

3. **Performance Issues**
   - N+1 query patterns
   - Unnecessary re-renders in React
   - Large bundle imports that could be split
   - Missing memoization

4. **Missing Error Handling**
   - Async functions without try/catch
   - API calls without error handling
   - Unvalidated user input

5. **Testing Gaps**
   - Critical paths without tests
   - Functions with complex logic but no tests

For each finding, provide:
- File path
- Line number (if applicable)
- Severity (critical/high/medium/low)
- Brief description
- Whether it's auto-fixable

Format as JSON array:
[{"file": "path", "line": 123, "severity": "high", "title": "...", "description": "...", "auto_fixable": false}]

Be thorough. This is a comprehensive overnight audit."""

        result = self.call_clode(prompt)
        if result:
            # Try to parse JSON findings
            try:
                # Find JSON array in response
                match = re.search(r'\[[\s\S]*\]', result)
                if match:
                    findings = json.loads(match.group())
                    for f in findings:
                        self.add_finding(
                            "code",
                            f.get("severity", "medium"),
                            f.get("title", "Code issue"),
                            f.get("description", ""),
                            file_path=f.get("file"),
                            line_number=f.get("line"),
                            auto_fixable=f.get("auto_fixable", False)
                        )
                    self.log(f"Clode found {len(findings)} code issues")
            except json.JSONDecodeError:
                # Store raw analysis
                self.report.code_metrics["clode_analysis"] = result[:2000]

    def _check_console_logs(self):
        self.log("Checking for console.logs in production code...")
        code, out, _ = self.run_command(
            f"grep -r 'console\\.log' {OMNIOPS_DIR}/app {OMNIOPS_DIR}/lib --include='*.ts' --include='*.tsx' 2>/dev/null | grep -v node_modules | grep -v '.test.' | wc -l"
        )
        if code == 0 and out.strip().isdigit():
            count = int(out.strip())
            if count > 0:
                self.add_finding("code", "low", f"{count} console.logs in production",
                               "Consider removing or using proper logging",
                               auto_fixable=True)

    def _check_todo_comments(self):
        self.log("Checking for TODO/FIXME comments...")
        code, out, _ = self.run_command(
            f"grep -rn 'TODO\\|FIXME\\|HACK\\|XXX' {OMNIOPS_DIR}/app {OMNIOPS_DIR}/lib --include='*.ts' --include='*.tsx' 2>/dev/null | grep -v node_modules | wc -l"
        )
        if code == 0 and out.strip().isdigit():
            count = int(out.strip())
            self.report.code_metrics["todo_count"] = count
            if count > 20:
                self.add_finding("code", "info", f"{count} TODO/FIXME comments",
                               "Tech debt markers in codebase")

    def _check_dead_code(self):
        self.log("Checking for potentially dead code...")
        # Check for unused exports would require more sophisticated analysis
        pass

    # ==================== PHASE 3: SECURITY AUDIT ====================

    def phase3_security(self):
        """Security audit - 1 hour"""
        self.log("=" * 50)
        self.log("PHASE 3: SECURITY AUDIT")
        self.log("=" * 50)

        self._npm_audit()
        self._check_secrets_in_code()
        self._check_ssl_certs()
        self._check_exposed_ports()
        self._check_file_permissions()

    def _npm_audit(self):
        self.log("Running npm audit...")
        code, out, _ = self.run_command(
            f"cd {OMNIOPS_DIR} && npm audit --json 2>/dev/null",
            timeout=120
        )

        if code == 0:
            try:
                audit = json.loads(out)
                vulns = audit.get("metadata", {}).get("vulnerabilities", {})

                critical = vulns.get("critical", 0)
                high = vulns.get("high", 0)
                moderate = vulns.get("moderate", 0)

                self.report.code_metrics["npm_vulnerabilities"] = vulns

                if critical > 0:
                    self.add_finding("security", "critical",
                                   f"npm audit: {critical} critical vulnerabilities",
                                   "Run 'npm audit' for details and 'npm audit fix' to resolve")
                if high > 0:
                    self.add_finding("security", "high",
                                   f"npm audit: {high} high severity vulnerabilities",
                                   "Review and update affected packages")
            except json.JSONDecodeError:
                pass

    def _check_secrets_in_code(self):
        self.log("Scanning for potential secrets in code...")
        patterns = [
            r'["\']sk-[a-zA-Z0-9]{20,}["\']',  # OpenAI keys
            r'["\']AKIA[A-Z0-9]{16}["\']',      # AWS keys
            r'password\s*=\s*["\'][^"\']+["\']', # Passwords
        ]

        for pattern in patterns:
            code, out, _ = self.run_command(
                f"grep -rE '{pattern}' {OMNIOPS_DIR}/app {OMNIOPS_DIR}/lib --include='*.ts' --include='*.tsx' 2>/dev/null | grep -v node_modules | head -5"
            )
            if code == 0 and out.strip():
                self.add_finding("security", "critical", "Potential secret in code",
                               f"Pattern matched: {pattern[:30]}... Check the code!")

    def _check_ssl_certs(self):
        self.log("Checking SSL certificates...")
        # Would need domain list - placeholder
        pass

    def _check_exposed_ports(self):
        self.log("Checking exposed ports...")
        code, out, _ = self.run_command(
            "ss -tuln | grep LISTEN | grep -v '127.0.0.1' | grep -v '::1'"
        )
        if code == 0 and out.strip():
            ports = len(out.strip().split('\n'))
            self.add_finding("security", "info", f"{ports} ports exposed externally",
                           "Review if all are necessary")

    def _check_file_permissions(self):
        self.log("Checking file permissions...")
        code, out, _ = self.run_command(
            "find /opt/claudius -type f -perm /o+w 2>/dev/null | head -5"
        )
        if code == 0 and out.strip():
            self.add_finding("security", "medium", "World-writable files found",
                           "Some files in /opt/claudius are world-writable")

    # ==================== PHASE 4: OPPORTUNITIES ====================

    def phase4_opportunities(self):
        """Find opportunities for improvement - 1 hour"""
        self.log("=" * 50)
        self.log("PHASE 4: OPPORTUNITY DISCOVERY")
        self.log("=" * 50)

        self._check_outdated_packages()
        self._check_bundle_size()
        self._check_caching_opportunities()

    def _check_outdated_packages(self):
        self.log("Checking for outdated packages...")
        code, out, _ = self.run_command(
            f"cd {OMNIOPS_DIR} && npm outdated --json 2>/dev/null",
            timeout=120
        )

        if code == 0 and out.strip():
            try:
                outdated = json.loads(out)
                major_updates = []
                for pkg, info in outdated.items():
                    current = info.get("current", "")
                    latest = info.get("latest", "")
                    if current and latest:
                        if current.split('.')[0] != latest.split('.')[0]:
                            major_updates.append(f"{pkg}: {current} → {latest}")

                if major_updates:
                    self.add_finding("opportunity", "info",
                                   f"{len(major_updates)} major package updates available",
                                   "\n".join(major_updates[:10]))
            except json.JSONDecodeError:
                pass

    def _check_bundle_size(self):
        self.log("Checking bundle size...")
        code, out, _ = self.run_command(
            f"du -sh {OMNIOPS_DIR}/.next 2>/dev/null | cut -f1"
        )
        if code == 0 and out.strip():
            self.report.code_metrics["bundle_size"] = out.strip()

    def _check_caching_opportunities(self):
        self.log("Looking for caching opportunities...")
        # Would need API analysis - placeholder
        pass

    # ==================== PHASE 5: SAFE FIXES ====================

    def phase5_safe_fixes(self):
        """Apply safe, non-breaking fixes - 1 hour"""
        self.log("=" * 50)
        self.log("PHASE 5: APPLYING SAFE FIXES")
        self.log("=" * 50)

        # Find auto-fixable issues
        fixable = [f for f in self.report.findings if f.auto_fixable and not f.fixed]

        if not fixable:
            self.log("No auto-fixable issues found")
            return

        # Clean up Docker (always safe)
        self._cleanup_docker()

        # For code fixes, ask Clode but be conservative
        # Disabled for now until we build confidence
        # self._apply_code_fixes(fixable)

    def _cleanup_docker(self):
        self.log("Cleaning up Docker...")
        code, out, _ = self.run_command("docker image prune -f 2>&1")
        if code == 0:
            self.report.fixes_applied.append("Docker: Pruned dangling images")

    # ==================== REPORT GENERATION ====================

    def generate_report(self) -> str:
        """Generate comprehensive morning report"""
        self.report.ended_at = datetime.now()
        duration = self.report.ended_at - self.report.started_at

        # Count findings by severity
        by_severity = {}
        by_category = {}
        for f in self.report.findings:
            by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
            by_category[f.category] = by_category.get(f.category, 0) + 1

        report = f"""☀️ **MORNING INTELLIGENCE REPORT**
{datetime.now().strftime('%A, %d %B %Y')}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

*Night shift duration: {duration.seconds // 3600}h {(duration.seconds % 3600) // 60}m*

"""
        # Executive Summary
        critical = by_severity.get("critical", 0)
        high = by_severity.get("high", 0)

        report += "**📊 EXECUTIVE SUMMARY**\n"
        if critical > 0:
            report += f"🔴 {critical} CRITICAL issues need immediate attention\n"
        if high > 0:
            report += f"🟠 {high} HIGH priority issues\n"
        report += f"📋 {len(self.report.findings)} total findings\n"
        report += f"🔧 {len(self.report.fixes_applied)} fixes applied overnight\n\n"

        # System Health
        m = self.report.system_metrics
        report += f"""**💻 SYSTEM HEALTH**
• Disk: {m.get('disk_percent', '?')}% ({m.get('disk_used', '?')}/{m.get('disk_total', '?')})
• Memory: {m.get('memory_percent', '?')}% ({m.get('memory_used_mb', '?')}MB/{m.get('memory_total_mb', '?')}MB)
• Containers: {len(m.get('containers', []))} running
• API Response: {m.get('api_response_ms', '?')}ms
• OmniOps Errors (24h): {m.get('omniops_errors_24h', '?')}

"""

        # Critical & High Priority Issues
        critical_findings = [f for f in self.report.findings if f.severity in ["critical", "high"]]
        if critical_findings:
            report += "**🚨 PRIORITY ISSUES**\n"
            for f in critical_findings[:10]:
                emoji = "🔴" if f.severity == "critical" else "🟠"
                report += f"{emoji} *{f.title}*\n   {f.description}\n"
                if f.file_path:
                    report += f"   📁 {f.file_path}"
                    if f.line_number:
                        report += f":{f.line_number}"
                    report += "\n"
            report += "\n"

        # Code Quality Summary
        cm = self.report.code_metrics
        if cm:
            report += "**📝 CODE QUALITY**\n"
            if "eslint_errors" in cm:
                report += f"• ESLint: {cm['eslint_errors']} errors, {cm.get('eslint_warnings', 0)} warnings\n"
            if "typescript_errors" in cm:
                report += f"• TypeScript: {cm['typescript_errors']} errors\n"
            if "todo_count" in cm:
                report += f"• TODO/FIXME comments: {cm['todo_count']}\n"
            if "npm_vulnerabilities" in cm:
                v = cm["npm_vulnerabilities"]
                report += f"• npm vulnerabilities: {v.get('critical', 0)} critical, {v.get('high', 0)} high\n"
            report += "\n"

        # Fixes Applied
        if self.report.fixes_applied:
            report += "**✅ FIXES APPLIED OVERNIGHT**\n"
            for fix in self.report.fixes_applied:
                report += f"• {fix}\n"
            report += "\n"

        # Medium/Low findings summary
        other = [f for f in self.report.findings if f.severity in ["medium", "low", "info"]]
        if other:
            report += f"**📋 OTHER FINDINGS**\n"
            report += f"• {by_severity.get('medium', 0)} medium priority\n"
            report += f"• {by_severity.get('low', 0)} low priority\n"
            report += f"• {by_severity.get('info', 0)} informational\n"
            report += f"_Full report: /opt/claudius/reports/night-watch-{datetime.now().strftime('%Y%m%d')}.json_\n\n"

        # Errors during execution
        if self.report.errors:
            report += "**⚠️ NIGHT SHIFT ERRORS**\n"
            for err in self.report.errors[:5]:
                report += f"• {err}\n"
            report += "\n"

        report += "_Your autonomous night shift, signing off._ 🌙"

        return report

    def save_full_report(self):
        """Save detailed JSON report"""
        report_data = {
            "started_at": self.report.started_at.isoformat(),
            "ended_at": self.report.ended_at.isoformat() if self.report.ended_at else None,
            "system_metrics": self.report.system_metrics,
            "code_metrics": self.report.code_metrics,
            "findings": [
                {
                    "category": f.category,
                    "severity": f.severity,
                    "title": f.title,
                    "description": f.description,
                    "file_path": f.file_path,
                    "line_number": f.line_number,
                    "auto_fixable": f.auto_fixable,
                    "fixed": f.fixed
                }
                for f in self.report.findings
            ],
            "fixes_applied": self.report.fixes_applied,
            "errors": self.report.errors
        }

        report_file = f"{REPORTS_DIR}/night-watch-{datetime.now().strftime('%Y%m%d')}.json"
        with open(report_file, "w") as f:
            json.dump(report_data, f, indent=2)
        self.log(f"Full report saved to {report_file}")

    # ==================== MAIN EXECUTION ====================

    def run(self):
        """Execute the full night shift"""
        try:
            self.phase1_system_analysis()
            self.phase2_code_quality()
            self.phase3_security()
            self.phase4_opportunities()
            self.phase5_safe_fixes()

            # Generate and save reports
            digest = self.generate_report()
            self.save_full_report()

            # Save markdown digest
            digest_file = f"{REPORTS_DIR}/night-watch-{datetime.now().strftime('%Y%m%d')}.md"
            with open(digest_file, "w") as f:
                f.write(digest)

            self.log("Night shift complete!")
            return digest

        except Exception as e:
            self.log(f"Night shift error: {e}")
            self.report.errors.append(str(e))
            return self.generate_report()


def send_digest():
    """Send the morning digest to Telegram"""
    today = datetime.now().strftime('%Y%m%d')
    digest_file = f"{REPORTS_DIR}/night-watch-{today}.md"

    if not os.path.exists(digest_file):
        print(f"No digest found for {today}")
        return

    with open(digest_file) as f:
        digest = f.read()

    secrets = load_env()
    token = secrets.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return

    # Split if too long
    max_len = 4000
    messages = [digest[i:i+max_len] for i in range(0, len(digest), max_len)]

    for msg in messages:
        try:
            requests.post(
                TELEGRAM_API.format(token=token),
                json={"chat_id": OWNER_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
                timeout=30
            )
            time.sleep(0.5)
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--send-digest":
        send_digest()
    elif len(sys.argv) > 1 and sys.argv[1] == "--test":
        # Quick test run
        shift = NightShift()
        digest = shift.run()
        print("\n" + "=" * 50)
        print("DIGEST PREVIEW:")
        print("=" * 50)
        print(digest)
    else:
        shift = NightShift()
        shift.run()
