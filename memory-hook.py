#!/usr/bin/env python3
"""
Claudius Memory Hook - Post-Response Surprise Detection

This script implements the Titans-inspired surprise detection for Claudius.
Call it after each response to automatically detect and save surprising information.

The Titans insight: "An event that violates expectations is more memorable."
- Higher surprise = stronger memory encoding
- Recent surprises build momentum (compounding effect)

Usage:
    memory-hook.py --response "My response text" --context "Conversation context"
    memory-hook.py --file /path/to/response.txt --context "Context"

    # Or via stdin:
    echo "Response text" | memory-hook.py --context "Context"

Environment:
    ENGRAM_API_KEY - API key for Engram (defaults to key from CLAUDE.md)
    ENGRAM_URL - Engram API URL (defaults to http://localhost:3201)
"""

import argparse
import json
import sys
import os
import urllib.request
import urllib.error

# Configuration
ENGRAM_URL = os.environ.get("ENGRAM_URL", "http://localhost:3201")
ENGRAM_API_KEY = os.environ.get(
    "ENGRAM_API_KEY",
    "45f50959c089a02dab0397052a2bb9ddc95e7184997ee422cca7b242c2d20293"
)

def send_to_engram(response: str, context: str, source_agent: str = "claudius") -> dict:
    """
    Send response to Engram surprise detection endpoint.

    Returns the surprise detection result with:
    - surpriseScore: Final score (0-1) including momentum
    - rawScore: Base score before momentum
    - momentumBoost: How much past surprises added
    - wasSaved: Whether it was saved to memory
    - reason: Explanation of the decision
    """
    url = f"{ENGRAM_URL}/engram/surprise"

    payload = {
        "response": response,
        "context": context,
        "sourceAgent": source_agent,
        "autoSave": True  # Automatically save if surprising
    }

    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {ENGRAM_API_KEY}",
        "Content-Type": "application/json"
    }

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def get_momentum_state(source_agent: str = "claudius") -> dict:
    """Get current momentum state for debugging."""
    url = f"{ENGRAM_URL}/engram/momentum/{source_agent}"
    headers = {"Authorization": f"Bearer {ENGRAM_API_KEY}"}

    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def format_result(result: dict, verbose: bool = False) -> str:
    """Format the result for display."""
    if "error" in result:
        return f"Error: {result['error']}"

    score = result.get("surpriseScore") or 0
    raw = result.get("rawScore") or 0
    momentum = result.get("momentumBoost") or 0
    saved = result.get("wasSaved", False)
    reason = result.get("reason", "")

    # Emoji indicator based on score
    if score >= 0.8:
        indicator = "!!"  # Very surprising
    elif score >= 0.7:
        indicator = "!"   # Surprising enough to save
    elif score >= 0.5:
        indicator = "~"   # Moderately interesting
    else:
        indicator = "."   # Routine

    if verbose:
        lines = [
            f"Surprise: {score:.2f} {indicator}",
            f"  Raw score: {raw:.2f}",
            f"  Momentum boost: +{momentum:.2f}",
            f"  Saved: {'Yes' if saved else 'No'}",
            f"  Reason: {reason}"
        ]
        if result.get("contradictions"):
            lines.append(f"  Contradictions: {result['contradictions']}")
        return "\n".join(lines)
    else:
        status = "SAVED" if saved else "not saved"
        return f"Surprise: {score:.2f} ({status})"


def main():
    parser = argparse.ArgumentParser(
        description="Claudius memory hook - Titans-inspired surprise detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        "--response", "-r",
        help="Response text to analyze"
    )
    parser.add_argument(
        "--file", "-f",
        help="Read response from file"
    )
    parser.add_argument(
        "--context", "-c",
        required=True,
        help="Conversation context (what prompted this response)"
    )
    parser.add_argument(
        "--agent", "-a",
        default="claudius",
        help="Source agent name (default: claudius)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress output (just return exit code)"
    )
    parser.add_argument(
        "--momentum",
        action="store_true",
        help="Show current momentum state"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON response"
    )

    args = parser.parse_args()

    # Show momentum state if requested
    if args.momentum:
        state = get_momentum_state(args.agent)
        if args.json:
            print(json.dumps(state, indent=2))
        else:
            print(f"Agent: {args.agent}")
            print(f"Recent surprises: {state.get('history', [])}")
            print(f"Current boost: {state.get('currentBoost', 0):.2f}")
        return 0

    # Get response text
    response = None

    if args.response:
        response = args.response
    elif args.file:
        with open(args.file, "r") as f:
            response = f.read()
    elif not sys.stdin.isatty():
        response = sys.stdin.read()

    if not response:
        print("Error: No response provided. Use --response, --file, or pipe to stdin.")
        return 1

    # Send to Engram
    result = send_to_engram(response.strip(), args.context, args.agent)

    # Output result
    if args.json:
        print(json.dumps(result, indent=2))
    elif not args.quiet:
        print(format_result(result, args.verbose))

    # Exit code: 0 if saved, 1 if error, 2 if not saved
    if "error" in result:
        return 1
    elif result.get("wasSaved"):
        return 0
    else:
        return 2


if __name__ == "__main__":
    sys.exit(main())
