"""
Coordinated Agent Sessions for Claudius
=========================================
Multi-agent coordination using shared task lists and dependency ordering.

Agents in a coordinated session share a CLAUDE_CODE_TASK_LIST_ID,
enabling them to see each other's progress and coordinate work.

Usage:
    from lib.coordinated_agents import spawn_coordinated, check_session_status

    session = await spawn_coordinated([
        {"prompt": "Fix type errors in src/", "depends_on": []},
        {"prompt": "Fix lint warnings", "depends_on": []},
        {"prompt": "Run full test suite", "depends_on": [0, 1]},
    ])
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from lib.async_agent_spawner import (
    AgentStatus, AgentTask, spawn_agent, check_agent_status,
    _task_store, _wait_for_agents,
)

TASKS_BASE = Path.home() / ".claude" / "tasks"


@dataclass
class CoordinatedSession:
    """A group of agents sharing a task list."""
    session_id: str
    task_list_id: str
    agent_ids: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)


_session_store: Dict[str, CoordinatedSession] = {}


def _create_task_list_id() -> str:
    """Generate a unique task list ID for coordinated agents."""
    return f"claudius-{uuid.uuid4().hex[:12]}"


def _build_dependency_groups(tasks: List[Dict]) -> List[List[int]]:
    """
    Topological sort of tasks into dependency groups.
    Tasks in the same group can run in parallel.
    """
    n = len(tasks)
    in_degree = [0] * n
    dependents = [[] for _ in range(n)]

    for i, t in enumerate(tasks):
        for dep in t.get("depends_on", []):
            if dep < n:
                in_degree[i] += 1
                dependents[dep].append(i)

    groups = []
    ready = [i for i in range(n) if in_degree[i] == 0]

    while ready:
        groups.append(list(ready))
        next_ready = []
        for idx in ready:
            for dep_idx in dependents[idx]:
                in_degree[dep_idx] -= 1
                if in_degree[dep_idx] == 0:
                    next_ready.append(dep_idx)
        ready = next_ready

    # Circular dependencies get added as final group
    remaining = [i for i in range(n) if in_degree[i] > 0]
    if remaining:
        groups.append(remaining)

    return groups


async def spawn_coordinated(
    tasks: List[Dict[str, Any]],
    working_dir: Optional[str] = None,
    callback: Optional[Callable] = None,
) -> CoordinatedSession:
    """
    Spawn multiple agents that share a task list for coordination.

    Each task dict should have:
        - prompt (str): What the agent should do
        - depends_on (list[int], optional): Indices of tasks this depends on

    Agents with dependencies wait until predecessors complete before starting.
    """
    task_list_id = _create_task_list_id()
    session_id = str(uuid.uuid4())[:8]

    session = CoordinatedSession(
        session_id=session_id,
        task_list_id=task_list_id,
    )

    # Create task list directory and write manifest
    list_dir = TASKS_BASE / task_list_id
    list_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "session_id": session_id,
        "task_list_id": task_list_id,
        "created_at": datetime.now().isoformat(),
        "tasks": [],
    }

    for i, task_def in enumerate(tasks):
        prompt = task_def["prompt"]
        deps = task_def.get("depends_on", [])

        if deps:
            dep_prompts = [tasks[d]["prompt"][:60] for d in deps if d < len(tasks)]
            prompt += f"\n\nNOTE: This task depends on completion of: {dep_prompts}"

        manifest["tasks"].append({
            "index": i,
            "prompt": prompt[:200],
            "depends_on": deps,
            "status": "pending",
        })

    with open(list_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    # Spawn agents respecting dependency order
    groups = _build_dependency_groups(tasks)
    agent_ids = []

    for group in groups:
        group_agent_ids = []
        for idx in group:
            task_def = tasks[idx]
            agent_id = await spawn_agent(
                prompt=task_def["prompt"],
                working_dir=working_dir,
                task_list_id=task_list_id,
                callback=callback,
            )
            group_agent_ids.append(agent_id)
            agent_ids.append(agent_id)

        # Wait for group before spawning dependents
        if group != groups[-1]:
            await _wait_for_agents(group_agent_ids)

    session.agent_ids = agent_ids
    _session_store[session_id] = session
    return session


async def check_session_status(session_id: str) -> Dict[str, Any]:
    """Check status of an entire coordinated session."""
    session = _session_store.get(session_id)
    if not session:
        return {"error": "Session not found", "session_id": session_id}

    agent_statuses = []
    for aid in session.agent_ids:
        agent_statuses.append(await check_agent_status(aid))

    all_done = all(
        s.get("status") in ("completed", "failed", "cancelled")
        for s in agent_statuses
    )
    any_failed = any(s.get("status") == "failed" for s in agent_statuses)

    return {
        "session_id": session_id,
        "task_list_id": session.task_list_id,
        "status": "failed" if any_failed else ("completed" if all_done else "running"),
        "agents": agent_statuses,
    }
