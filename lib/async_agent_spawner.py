#!/usr/bin/env python3
"""
Async Agent Spawner for Claudius
================================
Non-blocking agent spawning using the Claude Agent SDK.
Uses Max plan tokens (not API credits) via OAuth auth.

The key insight: The SDK spawns `claude` as a subprocess, but we can
make that non-blocking by using asyncio + background tasks.

This allows Claudius to:
1. Spawn worker agents without blocking HTTP responses
2. Use Max subscription tokens (inherited from ~/.claude/.credentials.json)
3. Get notified when workers complete
4. Handle multiple concurrent workers safely

Usage:
    from lib.async_agent_spawner import spawn_agent, check_agent_status, get_agent_result

    # Fire and forget
    task_id = await spawn_agent("Fix the LOC violations in entity-graph.ts")

    # Later, check status
    status = await check_agent_status(task_id)

    # Get result when done
    result = await get_agent_result(task_id)
"""

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import psutil

# Import Claude SDK (uses bundled CLI with Max auth)
try:
    from claude_agent_sdk import query, ClaudeAgentOptions
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    print("[SPAWNER] Warning: claude-agent-sdk not available, falling back to subprocess")


class AgentStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentTask:
    """Represents a spawned agent task."""
    task_id: str
    prompt: str
    status: AgentStatus = AgentStatus.QUEUED
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    callback: Optional[Callable] = None
    working_dir: Optional[str] = None


# In-memory task store (could be Redis for persistence)
_task_store: Dict[str, AgentTask] = {}
_task_queue: asyncio.Queue = None
_worker_running = False

# Resource limits
MAX_CONCURRENT_AGENTS = 2  # Conservative to prevent resource exhaustion
MIN_FREE_MEMORY_GB = 4.0
TASK_DIR = Path("/opt/claudius/agent-tasks")


def _check_resources() -> tuple[bool, str]:
    """Check if system has resources to spawn another agent."""
    mem = psutil.virtual_memory()
    available_gb = mem.available / (1024**3)

    if available_gb < MIN_FREE_MEMORY_GB:
        return False, f"Low memory: {available_gb:.1f}GB (need {MIN_FREE_MEMORY_GB}GB)"

    # Count running agents
    running_count = sum(1 for t in _task_store.values() if t.status == AgentStatus.RUNNING)
    if running_count >= MAX_CONCURRENT_AGENTS:
        return False, f"Max concurrent agents: {running_count}/{MAX_CONCURRENT_AGENTS}"

    return True, f"OK: {running_count} running, {available_gb:.1f}GB free"


async def _run_agent_sdk(task: AgentTask) -> str:
    """Run agent using Claude SDK (async, non-blocking)."""
    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Task"],
        permission_mode='acceptEdits',  # Auto-accept file edits
        max_turns=50,  # Reasonable limit for most fixes
        cwd=task.working_dir or "/opt/omniops"
    )

    result_parts = []
    last_assistant_message = ""

    try:
        async for message in query(prompt=task.prompt, options=options):
            # Parse different message types
            msg_type = getattr(message, 'type', None) or getattr(message, 'subtype', None)

            if msg_type == 'assistant':
                # This is Claude's text response
                content = getattr(message, 'message', None)
                if content and hasattr(content, 'content'):
                    for block in content.content:
                        if hasattr(block, 'text'):
                            last_assistant_message = block.text
                            result_parts.append(block.text)

            elif msg_type == 'result':
                # Tool execution result
                data = getattr(message, 'data', {})
                if data.get('result'):
                    result_parts.append(f"[Tool Result]\n{data['result'][:500]}")

    except GeneratorExit:
        # Normal cleanup - SDK throws this on completion
        pass
    except Exception as e:
        if "cancel scope" not in str(e):  # Ignore cleanup errors
            raise

    return last_assistant_message or "\n".join(result_parts[-5:]) if result_parts else "Task completed"


async def _run_agent_subprocess(task: AgentTask) -> str:
    """Fallback: Run agent via subprocess (still async via asyncio.create_subprocess_exec)."""
    working_dir = task.working_dir or "/opt/omniops"

    # Build the prompt with project context
    full_prompt = f"""You are working in the Omniops codebase.
Read CLAUDE.md first for project rules.

Task: {task.prompt}

After completing, commit any changes with a descriptive message."""

    # Use asyncio subprocess for non-blocking
    proc = await asyncio.create_subprocess_exec(
        "claude",
        "--print",  # Non-interactive
        "--dangerously-skip-permissions",  # For automated use
        "-p", full_prompt,
        cwd=working_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            **os.environ,
            "CLAUDECODE": "1",
            "CLAUDE_CODE_ENTRYPOINT": "spawned-agent"
        }
    )

    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"Agent exited with code {proc.returncode}: {stderr.decode()}")

    return stdout.decode()


async def _worker_loop():
    """Background worker that processes the task queue."""
    global _worker_running, _task_queue

    if _task_queue is None:
        _task_queue = asyncio.Queue()

    _worker_running = True

    while _worker_running:
        try:
            # Wait for task with timeout (allows clean shutdown)
            try:
                task_id = await asyncio.wait_for(_task_queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue

            task = _task_store.get(task_id)
            if not task or task.status == AgentStatus.CANCELLED:
                continue

            # Check resources before starting
            can_run, reason = _check_resources()
            if not can_run:
                # Re-queue with delay
                await asyncio.sleep(30)
                await _task_queue.put(task_id)
                continue

            # Start the task
            task.status = AgentStatus.RUNNING
            task.started_at = datetime.now()

            try:
                # Run via SDK if available, else subprocess
                if SDK_AVAILABLE:
                    task.result = await _run_agent_sdk(task)
                else:
                    task.result = await _run_agent_subprocess(task)

                task.status = AgentStatus.COMPLETED
            except Exception as e:
                task.status = AgentStatus.FAILED
                task.error = str(e)
            finally:
                task.completed_at = datetime.now()

            # Invoke callback if provided
            if task.callback:
                try:
                    await task.callback(task)
                except Exception as e:
                    print(f"[SPAWNER] Callback error for {task_id}: {e}")

            # Persist result to disk
            _persist_task(task)

        except Exception as e:
            print(f"[SPAWNER] Worker error: {e}")
            await asyncio.sleep(1)


def _persist_task(task: AgentTask):
    """Save task result to disk for later retrieval."""
    TASK_DIR.mkdir(exist_ok=True)

    task_data = {
        "task_id": task.task_id,
        "prompt": task.prompt[:500],  # Truncate for storage
        "status": task.status.value,
        "result": task.result[:10000] if task.result else None,  # Truncate
        "error": task.error,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }

    task_file = TASK_DIR / f"{task.task_id}.json"
    with open(task_file, "w") as f:
        json.dump(task_data, f, indent=2)


async def spawn_agent(
    prompt: str,
    callback: Optional[Callable] = None,
    working_dir: Optional[str] = None,
    priority: bool = False
) -> str:
    """
    Spawn an agent asynchronously. Returns immediately with task_id.

    Args:
        prompt: The task for the agent to perform
        callback: Optional async function called when task completes
        working_dir: Working directory for the agent (default: /opt/omniops)
        priority: If True, task is added to front of queue

    Returns:
        task_id: UUID string to track the task
    """
    global _task_queue, _worker_running

    # Initialize queue and worker if needed
    if _task_queue is None:
        _task_queue = asyncio.Queue()

    if not _worker_running:
        asyncio.create_task(_worker_loop())

    # Create task
    task_id = str(uuid.uuid4())[:8]
    task = AgentTask(
        task_id=task_id,
        prompt=prompt,
        callback=callback,
        working_dir=working_dir
    )

    _task_store[task_id] = task

    # Queue it
    if priority:
        # For priority, we'd need a priority queue - for now just add
        await _task_queue.put(task_id)
    else:
        await _task_queue.put(task_id)

    return task_id


async def check_agent_status(task_id: str) -> Dict[str, Any]:
    """Check status of a spawned agent task."""
    task = _task_store.get(task_id)

    if not task:
        # Try loading from disk
        task_file = TASK_DIR / f"{task_id}.json"
        if task_file.exists():
            with open(task_file) as f:
                return json.load(f)
        return {"error": "Task not found", "task_id": task_id}

    return {
        "task_id": task.task_id,
        "status": task.status.value,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "has_result": task.result is not None,
        "has_error": task.error is not None
    }


async def get_agent_result(task_id: str) -> Optional[str]:
    """Get the result of a completed agent task."""
    task = _task_store.get(task_id)

    if task:
        return task.result

    # Try loading from disk
    task_file = TASK_DIR / f"{task_id}.json"
    if task_file.exists():
        with open(task_file) as f:
            data = json.load(f)
            return data.get("result")

    return None


async def cancel_agent(task_id: str) -> bool:
    """Cancel a queued or running agent task."""
    task = _task_store.get(task_id)

    if not task:
        return False

    if task.status in [AgentStatus.QUEUED, AgentStatus.RUNNING]:
        task.status = AgentStatus.CANCELLED
        return True

    return False


def get_queue_status() -> Dict[str, Any]:
    """Get current queue and worker status."""
    queued = sum(1 for t in _task_store.values() if t.status == AgentStatus.QUEUED)
    running = sum(1 for t in _task_store.values() if t.status == AgentStatus.RUNNING)
    completed = sum(1 for t in _task_store.values() if t.status == AgentStatus.COMPLETED)
    failed = sum(1 for t in _task_store.values() if t.status == AgentStatus.FAILED)

    can_spawn, reason = _check_resources()

    return {
        "worker_running": _worker_running,
        "queue_size": _task_queue.qsize() if _task_queue else 0,
        "queued": queued,
        "running": running,
        "completed": completed,
        "failed": failed,
        "can_spawn": can_spawn,
        "resource_status": reason
    }


# CLI interface for testing
if __name__ == "__main__":
    import sys

    async def main():
        if len(sys.argv) < 2:
            print("Usage: python3 async_agent_spawner.py 'prompt'")
            print("       python3 async_agent_spawner.py --status task_id")
            return

        if sys.argv[1] == "--status":
            task_id = sys.argv[2] if len(sys.argv) > 2 else None
            if task_id:
                status = await check_agent_status(task_id)
                print(json.dumps(status, indent=2))
            else:
                status = get_queue_status()
                print(json.dumps(status, indent=2))
        else:
            prompt = " ".join(sys.argv[1:])

            # Simple callback that prints completion
            async def on_complete(task):
                print(f"\n[DONE] Task {task.task_id}: {task.status.value}")
                if task.error:
                    print(f"Error: {task.error}")

            task_id = await spawn_agent(prompt, callback=on_complete)
            print(f"Spawned agent: {task_id}")
            print("Waiting for completion...")

            # Wait for completion
            while True:
                status = await check_agent_status(task_id)
                if status.get("status") in ["completed", "failed", "cancelled"]:
                    break
                await asyncio.sleep(2)

            result = await get_agent_result(task_id)
            if result:
                print(f"\nResult:\n{result[:1000]}...")

    asyncio.run(main())
