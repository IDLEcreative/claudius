#!/usr/bin/env python3
"""
Async Agent Spawner for Claudius
================================
Non-blocking agent spawning with Claude Code Tasks for coordination.

Key features:
1. Spawns worker agents without blocking HTTP responses
2. Uses CLAUDE_CODE_TASK_LIST_ID for multi-agent shared task lists
3. Resource-gated: checks memory before spawning
4. Telegram notifications on completion

Usage:
    from lib.async_agent_spawner import spawn_agent, check_agent_status

    task_id = await spawn_agent("Fix the LOC violations in entity-graph.ts")
    status = await check_agent_status(task_id)

For coordinated multi-agent work, see lib/coordinated_agents.py
"""

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import psutil


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
    task_list_id: Optional[str] = None


# In-memory store
_task_store: Dict[str, AgentTask] = {}
_task_queue: asyncio.Queue = None
_worker_running = False

# Resource limits
MAX_CONCURRENT_AGENTS = 2
MIN_FREE_MEMORY_GB = 4.0
TASK_DIR = Path("/opt/claudius/state/agent-tasks")


def _check_resources() -> tuple[bool, str]:
    """Check if system has resources to spawn another agent."""
    mem = psutil.virtual_memory()
    available_gb = mem.available / (1024**3)

    if available_gb < MIN_FREE_MEMORY_GB:
        return False, f"Low memory: {available_gb:.1f}GB (need {MIN_FREE_MEMORY_GB}GB)"

    running_count = sum(1 for t in _task_store.values() if t.status == AgentStatus.RUNNING)
    if running_count >= MAX_CONCURRENT_AGENTS:
        return False, f"Max concurrent agents: {running_count}/{MAX_CONCURRENT_AGENTS}"

    return True, f"OK: {running_count} running, {available_gb:.1f}GB free"


async def _run_agent_subprocess(task: AgentTask) -> str:
    """Run agent via Claude CLI subprocess with optional shared task list."""
    working_dir = task.working_dir or "/opt/claudius"

    full_prompt = f"""Read /opt/claudius/CLAUDE.md for project rules.

Task: {task.prompt}

After completing, summarize what you did."""

    env = {
        **os.environ,
        "CLAUDECODE": "1",
        "CLAUDE_CODE_ENTRYPOINT": "spawned-agent",
    }

    if task.task_list_id:
        env["CLAUDE_CODE_TASK_LIST_ID"] = task.task_list_id

    proc = await asyncio.create_subprocess_exec(
        "claude",
        "--print",
        "--dangerously-skip-permissions",
        "-p", full_prompt,
        cwd=working_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env
    )

    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr.decode()[:500]
        raise RuntimeError(f"Agent exited with code {proc.returncode}: {err_msg}")

    return stdout.decode()


async def _notify_completion(task: AgentTask):
    """Send Telegram notification when agent completes."""
    try:
        from lib.telegram_sender import send_telegram

        icon = "✅" if task.status == AgentStatus.COMPLETED else "❌"
        duration = ""
        if task.started_at and task.completed_at:
            elapsed = (task.completed_at - task.started_at).total_seconds()
            duration = f" ({elapsed:.0f}s)"

        summary = (task.result or task.error or "No output")[:200]
        msg = f"{icon} Agent `{task.task_id}` {task.status.value}{duration}\n\n_{summary}_"
        send_telegram(msg)
    except Exception as e:
        print(f"[SPAWNER] Telegram notify failed: {e}")


async def _worker_loop():
    """Background worker that processes the task queue."""
    global _worker_running, _task_queue

    if _task_queue is None:
        _task_queue = asyncio.Queue()

    _worker_running = True

    while _worker_running:
        try:
            try:
                task_id = await asyncio.wait_for(_task_queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue

            task = _task_store.get(task_id)
            if not task or task.status == AgentStatus.CANCELLED:
                continue

            can_run, reason = _check_resources()
            if not can_run:
                print(f"[SPAWNER] Waiting for resources: {reason}")
                await asyncio.sleep(30)
                await _task_queue.put(task_id)
                continue

            task.status = AgentStatus.RUNNING
            task.started_at = datetime.now()
            print(f"[SPAWNER] Starting agent {task_id}: {task.prompt[:80]}...")

            try:
                task.result = await _run_agent_subprocess(task)
                task.status = AgentStatus.COMPLETED
            except Exception as e:
                task.status = AgentStatus.FAILED
                task.error = str(e)
            finally:
                task.completed_at = datetime.now()

            if task.callback:
                try:
                    await task.callback(task)
                except Exception as e:
                    print(f"[SPAWNER] Callback error for {task_id}: {e}")

            await _notify_completion(task)
            _persist_task(task)

        except Exception as e:
            print(f"[SPAWNER] Worker error: {e}")
            await asyncio.sleep(1)


def _persist_task(task: AgentTask):
    """Save task result to disk for later retrieval."""
    TASK_DIR.mkdir(parents=True, exist_ok=True)

    task_data = {
        "task_id": task.task_id,
        "prompt": task.prompt[:500],
        "status": task.status.value,
        "result": task.result[:10000] if task.result else None,
        "error": task.error,
        "task_list_id": task.task_list_id,
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
    task_list_id: Optional[str] = None,
) -> str:
    """
    Spawn a single agent asynchronously. Returns immediately with task_id.

    Args:
        prompt: The task for the agent to perform
        callback: Optional async function called when task completes
        working_dir: Working directory (default: /opt/claudius)
        task_list_id: Optional shared task list ID for multi-agent coordination

    Returns:
        task_id: Short UUID to track the task
    """
    global _task_queue, _worker_running

    if _task_queue is None:
        _task_queue = asyncio.Queue()

    if not _worker_running:
        asyncio.create_task(_worker_loop())

    task_id = str(uuid.uuid4())[:8]
    task = AgentTask(
        task_id=task_id,
        prompt=prompt,
        callback=callback,
        working_dir=working_dir,
        task_list_id=task_list_id,
    )

    _task_store[task_id] = task
    await _task_queue.put(task_id)
    return task_id


async def check_agent_status(task_id: str) -> Dict[str, Any]:
    """Check status of a spawned agent task."""
    task = _task_store.get(task_id)

    if not task:
        task_file = TASK_DIR / f"{task_id}.json"
        if task_file.exists():
            with open(task_file) as f:
                return json.load(f)
        return {"error": "Task not found", "task_id": task_id}

    return {
        "task_id": task.task_id,
        "status": task.status.value,
        "task_list_id": task.task_list_id,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "has_result": task.result is not None,
        "has_error": task.error is not None,
    }


async def get_agent_result(task_id: str) -> Optional[str]:
    """Get the result of a completed agent task."""
    task = _task_store.get(task_id)
    if task:
        return task.result

    task_file = TASK_DIR / f"{task_id}.json"
    if task_file.exists():
        with open(task_file) as f:
            return json.load(f).get("result")
    return None


async def cancel_agent(task_id: str) -> bool:
    """Cancel a queued or running agent task."""
    task = _task_store.get(task_id)
    if not task:
        return False

    if task.status in (AgentStatus.QUEUED, AgentStatus.RUNNING):
        task.status = AgentStatus.CANCELLED
        return True
    return False


async def _wait_for_agents(agent_ids: list, timeout: float = 600):
    """Wait for a group of agents to complete (with timeout)."""
    start = datetime.now()
    while True:
        all_done = all(
            _task_store.get(aid, AgentTask(task_id="", prompt="")).status
            in (AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.CANCELLED)
            for aid in agent_ids
        )
        if all_done:
            return

        elapsed = (datetime.now() - start).total_seconds()
        if elapsed > timeout:
            print(f"[SPAWNER] Timeout waiting for agents: {agent_ids}")
            return

        await asyncio.sleep(2)


def get_queue_status() -> Dict[str, Any]:
    """Get current queue and worker status."""
    statuses = {s: 0 for s in AgentStatus}
    for t in _task_store.values():
        statuses[t.status] += 1

    can_spawn, reason = _check_resources()

    return {
        "worker_running": _worker_running,
        "queue_size": _task_queue.qsize() if _task_queue else 0,
        "queued": statuses[AgentStatus.QUEUED],
        "running": statuses[AgentStatus.RUNNING],
        "completed": statuses[AgentStatus.COMPLETED],
        "failed": statuses[AgentStatus.FAILED],
        "can_spawn": can_spawn,
        "resource_status": reason,
    }


if __name__ == "__main__":
    import sys

    async def _cli():
        if len(sys.argv) < 2 or sys.argv[1] == "--help":
            print("Usage: python3 -m lib.async_agent_spawner [--status [id] | prompt...]")
            return
        if sys.argv[1] == "--status":
            tid = sys.argv[2] if len(sys.argv) > 2 else None
            data = await check_agent_status(tid) if tid else get_queue_status()
            print(json.dumps(data, indent=2))
        else:
            tid = await spawn_agent(" ".join(sys.argv[1:]))
            print(f"Spawned: {tid}")
            while (await check_agent_status(tid)).get("status") not in ("completed", "failed"):
                await asyncio.sleep(2)
            print(await get_agent_result(tid) or "No result")

    asyncio.run(_cli())
