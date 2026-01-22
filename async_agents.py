#!/usr/bin/env python3
"""
Async Agent Spawner for Claudius
Uses Claude Agent SDK to run agents without blocking the main process.
"""

import asyncio
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable, Any
from claude_agent_sdk import query, ClaudeAgentOptions

# Ensure sandbox mode for bypassPermissions
os.environ["IS_SANDBOX"] = "1"

# Store for tracking running agents
_running_agents: dict[str, asyncio.Task] = {}
_agent_results: dict[str, dict] = {}


async def run_agent(
    agent_id: str,
    prompt: str,
    model: str = "sonnet",
    cwd: str = "/opt/omniops",
    max_turns: int = 50,
    on_complete: Optional[Callable[[str, dict], Any]] = None
) -> None:
    """Run an agent asynchronously.

    Args:
        agent_id: Unique identifier for this agent run
        prompt: The task for the agent
        model: Model to use (haiku, sonnet, opus)
        cwd: Working directory
        max_turns: Maximum conversation turns
        on_complete: Optional callback when done
    """
    result = {
        "agent_id": agent_id,
        "started": datetime.now().isoformat(),
        "completed": None,
        "success": False,
        "output": "",
        "error": None
    }

    try:
        options = ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            model=model,
            cwd=cwd,
            max_turns=max_turns
        )

        output_parts = []
        async for msg in query(prompt=prompt, options=options):
            if hasattr(msg, 'content') and msg.content:
                # Handle TextBlock content
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if hasattr(block, 'text'):
                            output_parts.append(block.text)
                else:
                    output_parts.append(str(msg.content))

        result["output"] = "\n".join(output_parts)
        result["success"] = True
        result["completed"] = datetime.now().isoformat()

    except asyncio.CancelledError:
        result["error"] = "Cancelled"
        result["completed"] = datetime.now().isoformat()
    except Exception as e:
        result["error"] = str(e)
        result["completed"] = datetime.now().isoformat()

    _agent_results[agent_id] = result

    if on_complete:
        try:
            on_complete(agent_id, result)
        except Exception as e:
            print(f"[AGENT] Callback error: {e}")


def spawn_agent(
    prompt: str,
    agent_id: Optional[str] = None,
    model: str = "sonnet",
    cwd: str = "/opt/omniops",
    max_turns: int = 50,
    on_complete: Optional[Callable[[str, dict], Any]] = None
) -> str:
    """Spawn an agent in the background (non-blocking).

    Returns immediately with agent_id. Use get_agent_status() to check progress.

    Args:
        prompt: The task for the agent
        agent_id: Optional ID (auto-generated if not provided)
        model: Model to use
        cwd: Working directory
        max_turns: Maximum turns
        on_complete: Callback when done

    Returns:
        agent_id: The ID to track this agent
    """
    if agent_id is None:
        agent_id = f"agent-{datetime.now().strftime('%H%M%S')}"

    # Get or create event loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # Create task
    task = asyncio.create_task(
        run_agent(agent_id, prompt, model, cwd, max_turns, on_complete)
    )
    _running_agents[agent_id] = task

    return agent_id


def get_agent_status(agent_id: str) -> dict:
    """Get the status of an agent.

    Returns:
        dict with 'running', 'result' (if complete)
    """
    if agent_id in _agent_results:
        return {"running": False, "result": _agent_results[agent_id]}
    elif agent_id in _running_agents:
        task = _running_agents[agent_id]
        return {"running": not task.done(), "result": None}
    else:
        return {"running": False, "result": None, "error": "Unknown agent"}


def cancel_agent(agent_id: str) -> bool:
    """Cancel a running agent."""
    if agent_id in _running_agents:
        task = _running_agents[agent_id]
        if not task.done():
            task.cancel()
            return True
    return False


def list_agents() -> dict:
    """List all agents and their status."""
    return {
        "running": [aid for aid, task in _running_agents.items() if not task.done()],
        "completed": list(_agent_results.keys())
    }


async def run_agent_and_wait(
    prompt: str,
    model: str = "sonnet",
    cwd: str = "/opt/omniops",
    max_turns: int = 50,
    timeout: int = 600
) -> dict:
    """Run an agent and wait for completion (with timeout).

    This is async but waits - use when you need the result.
    """
    agent_id = f"agent-{datetime.now().strftime('%H%M%S')}"

    try:
        await asyncio.wait_for(
            run_agent(agent_id, prompt, model, cwd, max_turns),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        cancel_agent(agent_id)
        return {"success": False, "error": f"Timeout after {timeout}s"}

    return _agent_results.get(agent_id, {"success": False, "error": "No result"})


# Convenience function for simple fire-and-forget
def fire_and_forget(prompt: str, model: str = "sonnet") -> str:
    """Spawn an agent and immediately return. Truly non-blocking."""
    import threading

    agent_id = f"agent-{datetime.now().strftime('%H%M%S')}"

    def run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_agent(agent_id, prompt, model))
        finally:
            loop.close()

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()

    return agent_id


if __name__ == "__main__":
    # Test
    import sys

    async def main():
        print("Testing async agent...")
        result = await run_agent_and_wait(
            prompt="What is 2+2? Just say the number.",
            model="haiku",
            max_turns=1,
            timeout=30
        )
        print(f"Result: {result}")

    asyncio.run(main())
