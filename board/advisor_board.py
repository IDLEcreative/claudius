#!/usr/bin/env python3
"""
Board of Advisors for Claudius

A simplified Python implementation of the Board of Advisors deliberation system.
Provides routing decisions for requests between Claudius (infrastructure) and Clode (codebase).

Usage:
    from board.advisor_board import AdvisorBoard

    board = AdvisorBoard()
    result = board.deliberate("Check disk space and restart if needed")
    # result = {"decision": "claudius", "confidence": 0.9, "reasoning": "..."}
"""

import os
import re
import json
import urllib.request
import urllib.error
from typing import Literal, Optional
from dataclasses import dataclass


RoutingDecision = Literal["claudius", "clode", "escalate"]


@dataclass
class DeliberationResult:
    decision: RoutingDecision
    confidence: float
    reasoning: str
    summary: str


# Keywords for quick routing (no API call needed)
CLAUDIUS_KEYWORDS = [
    r"\bdocker\b", r"\bcontainer\b", r"\bkubernetes\b", r"\bk8s\b",
    r"\bdisk\b", r"\bmemory\b", r"\bcpu\b", r"\bnetwork\b", r"\bport\b",
    r"\bserver\b", r"\binfrastructure\b", r"\bdeploy\b", r"\brollback\b",
    r"\bsystemd\b", r"\bjournalctl\b", r"\bservice\b",
    r"\bssl\b", r"\bcert\b", r"\bcaddy\b", r"\bnginx\b",
    r"\brestart\b", r"\bstop\b", r"\bstart\b", r"\blogs\b",
    r"\bprune\b", r"\bcleanup\b", r"\bhealth\b",
]

CLODE_KEYWORDS = [
    r"\btest\b", r"\btests\b", r"\btesting\b", r"\bjest\b", r"\bplaywright\b",
    r"\bbuild\b", r"\bcompile\b", r"\btypescript\b", r"\btsc\b",
    r"\bnpm\b", r"\bnode\b", r"\bpnpm\b", r"\byarn\b",
    r"\bcode\b", r"\bfunction\b", r"\bcomponent\b", r"\bendpoint\b",
    r"\brefactor\b", r"\blint\b", r"\beslint\b", r"\bformat\b",
    r"\breview\b", r"\bdebug\b", r"\bbug\b", r"\berror\b",
    r"\.ts\b", r"\.tsx\b", r"\.js\b", r"\.json\b",
    r"\blib/", r"\bapp/", r"\bcomponents/", r"\bsrc/",
    r"\bsupabase\b", r"\bdatabase\b", r"\bschema\b", r"\bmigration\b",
]

ESCALATION_KEYWORDS = [
    r"\bdelete\b.*\bproduction\b", r"\bprod\b.*\bdelete\b",
    r"\bdrop\b.*\btable\b", r"\btruncate\b",
    r"\brm\s+-rf\b", r"\bforce\b.*\bpush\b",
    r"\brollback\b.*\bdata\b", r"\brevert\b.*\buser\b",
]


class AdvisorBoard:
    """
    Board of Advisors for routing decisions.

    Can operate in two modes:
    - Quick mode: Keyword-based routing (instant, no API cost)
    - Full mode: AI-powered deliberation (slower, ~$0.08 per call)
    """

    def __init__(
        self,
        anthropic_api_key: Optional[str] = None,
        telegram_bot_token: Optional[str] = None,
        telegram_chat_id: str = "7070679785"
    ):
        self.api_key = anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.telegram_token = telegram_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = telegram_chat_id or os.environ.get("TELEGRAM_OWNER_CHAT_ID", "7070679785")

    def quick_check(self, prompt: str) -> DeliberationResult:
        """
        Fast keyword-based routing (no API call).
        Use for simple, clear-cut requests.
        """
        prompt_lower = prompt.lower()

        # Check for escalation triggers first (risky operations)
        for pattern in ESCALATION_KEYWORDS:
            if re.search(pattern, prompt_lower, re.IGNORECASE):
                return DeliberationResult(
                    decision="escalate",
                    confidence=0.95,
                    reasoning=f"Detected risky operation pattern: {pattern}",
                    summary="[HIGH 95%] → ESCALATE (risky operation detected)"
                )

        # Count keyword matches
        claudius_score = sum(1 for p in CLAUDIUS_KEYWORDS if re.search(p, prompt_lower))
        clode_score = sum(1 for p in CLODE_KEYWORDS if re.search(p, prompt_lower))

        # Determine decision
        if claudius_score > clode_score:
            confidence = min(0.5 + (claudius_score * 0.1), 0.95)
            return DeliberationResult(
                decision="claudius",
                confidence=confidence,
                reasoning=f"Infrastructure keywords detected ({claudius_score} matches)",
                summary=f"[MED {int(confidence*100)}%] → CLAUDIUS"
            )
        elif clode_score > claudius_score:
            confidence = min(0.5 + (clode_score * 0.1), 0.95)
            return DeliberationResult(
                decision="clode",
                confidence=confidence,
                reasoning=f"Codebase keywords detected ({clode_score} matches)",
                summary=f"[MED {int(confidence*100)}%] → CLODE"
            )
        else:
            # Ambiguous - default to claudius for safety
            return DeliberationResult(
                decision="claudius",
                confidence=0.5,
                reasoning="No clear keyword matches, defaulting to Claudius",
                summary="[LOW 50%] → CLAUDIUS (ambiguous)"
            )

    def deliberate(self, prompt: str, context: Optional[str] = None) -> DeliberationResult:
        """
        Full AI-powered deliberation using Claude API.
        More accurate but slower and costs ~$0.08.

        Falls back to quick_check if API unavailable.
        """
        if not self.api_key:
            return self.quick_check(prompt)

        system_prompt = """You are a routing advisor for an AI agent system.
Your job is to determine which agent should handle a user request:

- CLAUDIUS: Infrastructure agent (bare metal server). Handles: Docker, deployments,
  server health, disk/memory/CPU, SSL/certificates, system services, logs, networking.

- CLODE: Codebase agent (inside Docker). Handles: Code review, testing, TypeScript/JS,
  database queries, refactoring, debugging, linting, builds, npm/node operations.

- ESCALATE: For risky operations that need human approval: production data deletion,
  force pushes, database drops, destructive operations without backup.

Analyze the request and respond with EXACTLY this JSON format:
{
  "decision": "claudius" | "clode" | "escalate",
  "confidence": 0.0 to 1.0,
  "reasoning": "One sentence explanation"
}"""

        user_content = f"Request: {prompt}"
        if context:
            user_content += f"\n\nAdditional context: {context}"

        try:
            request_body = json.dumps({
                "model": "claude-3-5-haiku-latest",
                "max_tokens": 200,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_content}]
            }).encode("utf-8")

            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=request_body,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01"
                }
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
                content = result.get("content", [{}])[0].get("text", "")

                # Parse JSON response
                parsed = json.loads(content)
                decision = parsed.get("decision", "claudius")
                confidence = float(parsed.get("confidence", 0.5))
                reasoning = parsed.get("reasoning", "AI deliberation")

                level = "HIGH" if confidence >= 0.8 else "MED" if confidence >= 0.6 else "LOW"

                return DeliberationResult(
                    decision=decision,
                    confidence=confidence,
                    reasoning=reasoning,
                    summary=f"[{level} {int(confidence*100)}%] → {decision.upper()}"
                )

        except Exception as e:
            # Fall back to quick check on any error
            print(f"[Board] API error, falling back to quick check: {e}")
            return self.quick_check(prompt)

    def notify_escalation(self, prompt: str, result: DeliberationResult) -> bool:
        """Send escalation notification via Telegram."""
        if not self.telegram_token:
            return False

        message = f"""⚠️ <b>ESCALATION REQUIRED</b>

<b>Request:</b> {prompt[:200]}...

<b>Reason:</b> {result.reasoning}

<b>Confidence:</b> {int(result.confidence * 100)}%

Reply with:
• /approve - Route to Claudius
• /deny - Reject request
• /clode - Route to Clode"""

        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = json.dumps({
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML"
            }).encode("utf-8")

            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status == 200
        except Exception as e:
            print(f"[Board] Failed to send escalation: {e}")
            return False


# Module-level convenience functions
_default_board: Optional[AdvisorBoard] = None

def get_board() -> AdvisorBoard:
    global _default_board
    if _default_board is None:
        _default_board = AdvisorBoard()
    return _default_board

def quick_check(prompt: str) -> DeliberationResult:
    return get_board().quick_check(prompt)

def deliberate(prompt: str, context: Optional[str] = None) -> DeliberationResult:
    return get_board().deliberate(prompt, context)
