"""
AgentPool - Claude CLI invocation with concurrency control and queueing.

Extracted from claudius-api-with-telegram.py to provide:
- Centralized Claude CLI invocation
- Request queueing (instead of immediate rejection)
- Proper timeout handling
- Resource checking before spawn
"""

import json
import logging
import os
import subprocess
import threading
import uuid
from datetime import datetime
from queue import Queue, Empty
from typing import Optional, Callable
import psutil

logger = logging.getLogger("claudius.agent")

# Learning Memory integration
try:
    import sys
    sys.path.insert(0, "/opt/claudius")
    from learning_memory import recall_memories, format_memories_for_context, detect_and_store_surprise
    LEARNING_MEMORY_AVAILABLE = True
    logger.info("[LearningMemory] Integration loaded successfully")
except ImportError as e:
    LEARNING_MEMORY_AVAILABLE = False
    logger.warning(f"[LearningMemory] Not available: {e}")

# Lazy import to avoid circular dependency
_unified_memory = None

def _get_memory():
    global _unified_memory
    if _unified_memory is None:
        try:
            from claudius.memory.unified import get_unified_memory
            _unified_memory = get_unified_memory()
        except ImportError:
            logger.warning("UnifiedMemory not available, using basic context")
            _unified_memory = False
    return _unified_memory if _unified_memory else None

# Configuration
CLAUDIUS_DIR = "/opt/claudius"
CLAUDE_MD = f"{CLAUDIUS_DIR}/CLAUDE.md"
MEMORY_MD = f"{CLAUDIUS_DIR}/MEMORY.md"

# Concurrency settings
MAX_CONCURRENT_SESSIONS = 4  # Each session spawns 5+ MCP processes
DEFAULT_TIMEOUT = 600  # 10 minutes
QUEUE_TIMEOUT = 300  # 5 minutes to wait for slot


class AgentRequest:
    """Represents a queued agent request."""

    def __init__(self, prompt: str, conversation_history: list = None,
                 session_id: str = None, callback: Callable = None):
        self.id = str(uuid.uuid4())[:8]
        self.prompt = prompt
        self.conversation_history = conversation_history or []
        self.session_id = session_id
        self.callback = callback
        self.created_at = datetime.now()
        self.result = None
        self.error = None
        self.completed = threading.Event()


class AgentPool:
    """Pool of Claude CLI agents with concurrency control."""

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_SESSIONS):
        self.max_concurrent = max_concurrent
        self.semaphore = threading.Semaphore(max_concurrent)
        self.active_sessions = 0
        self.session_lock = threading.Lock()
        self.active_details = {}  # request_id -> details
        self.request_queue = Queue()
        self._shutdown = False

        # Start queue processor thread
        self.processor_thread = threading.Thread(
            target=self._process_queue,
            daemon=True,
            name="AgentQueueProcessor"
        )
        self.processor_thread.start()

    def _check_resources(self) -> tuple[bool, str]:
        """Check if system has resources to spawn a new agent."""
        try:
            # Check available memory
            mem = psutil.virtual_memory()
            available_mb = mem.available / (1024 * 1024)
            if available_mb < 500:
                return False, f"Low memory: {available_mb:.0f}MB"

            # Check Claude process count
            claude_procs = len([
                p for p in psutil.process_iter(['cmdline'])
                if 'claude' in str(p.info.get('cmdline', [])).lower()
            ])
            if claude_procs > 10:
                return False, f"Too many Claude processes: {claude_procs}"

            # Check CPU load
            load_avg = os.getloadavg()[0]
            cpu_count = os.cpu_count() or 1
            if load_avg > cpu_count * 2:
                return False, f"High load: {load_avg:.1f}"

            return True, "OK"

        except Exception as e:
            logger.warning(f"Resource check failed: {e}")
            return True, "Check failed (allowing)"

    def _invoke_claude(self, request: AgentRequest) -> dict:
        """Invoke Claude CLI with the given request."""
        try:
            # Recall relevant memories from Learning Memory system
            learning_memory_context = ""
            if LEARNING_MEMORY_AVAILABLE:
                try:
                    memories = recall_memories(request.prompt, agent='claudius', max_results=5)
                    learning_memory_context = format_memories_for_context(memories, max_chars=1500)
                    if learning_memory_context:
                        logger.info(f"[LearningMemory] Injecting {len(memories)} memories into context")
                except Exception as e:
                    logger.debug(f"[LearningMemory] Recall failed (non-blocking): {e}")

            # Build command
            cmd = [
                "claude", "--print",
                "--output-format", "json",
                "--permission-mode", "bypassPermissions"
            ]

            if request.session_id:
                cmd.extend(["--resume", request.session_id])

            # Try to use UnifiedMemory for smarter context building
            memory = _get_memory()
            if memory and not request.session_id:
                # Use UnifiedMemory for consolidated context (core facts + recent history + semantic memories)
                unified_context = memory.build_context(chat_id=0, current_query=request.prompt)
                context = f"""You are Claudius, the bare metal server Claude (Emperor/Overseer).

Read your instructions from {CLAUDE_MD}.

{unified_context}
{learning_memory_context}

Current user request: {request.prompt}

IMPORTANT: Use the context above to provide relevant responses."""
            else:
                # Fallback: format history manually
                history_context = ""
                if request.conversation_history and not request.session_id:
                    history_context = "\n--- CONVERSATION HISTORY ---\n"
                    for msg in request.conversation_history:
                        role = msg.get("role", "?").upper()
                        content = msg.get("content", "")
                        history_context += f"\n{role}: {content}\n"
                    history_context += "\n--- END HISTORY ---\n"

                context = f"""You are Claudius, the bare metal server Claude (Emperor/Overseer).

Read your instructions from {CLAUDE_MD}.
Read your memory from {MEMORY_MD} for context from previous sessions.
{learning_memory_context}
{history_context}
Current user request: {request.prompt}

IMPORTANT: If the user refers to something from the conversation history above, use that context."""

            # Set environment
            env = os.environ.copy()
            env["IS_SANDBOX"] = "1"

            # Execute
            result = subprocess.run(
                cmd,
                input=context,
                capture_output=True,
                text=True,
                timeout=DEFAULT_TIMEOUT,
                cwd=CLAUDIUS_DIR,
                env=env
            )

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            # If session resume returned empty output, retry without session
            if not stdout and request.session_id and not getattr(request, "_session_cleared", False):
                logger.warning(f"[Agent] Empty response with session resume — retrying fresh (no session)")
                request._session_cleared = True
                request.session_id = None
                return self._invoke_claude(request)

            # Parse response
            if stdout:
                try:
                    json_response = json.loads(stdout)
                    response_text = json_response.get("result", stdout)

                    # Learn from response via Learning Memory System
                    if LEARNING_MEMORY_AVAILABLE:
                        try:
                            detect_and_store_surprise(
                                prompt=request.prompt,
                                response=response_text[:2000],
                                auto_store=True
                            )
                        except Exception as e:
                            logger.debug(f"[LearningMemory] Surprise detection failed: {e}")

                    return {
                        "response": response_text,
                        "session_id": json_response.get("session_id")
                    }
                except json.JSONDecodeError:
                    return {"response": stdout, "session_id": request.session_id}

            return {
                "response": stderr or "No response",
                "session_id": request.session_id
            }

        except subprocess.TimeoutExpired:
            return {
                "response": "Request timed out. Try a simpler task.",
                "session_id": request.session_id
            }
        except Exception as e:
            return {
                "response": f"Error: {str(e)}",
                "session_id": request.session_id
            }

    def _process_queue(self):
        """Background thread that processes queued requests."""
        while not self._shutdown:
            try:
                request = self.request_queue.get(timeout=1)
            except Empty:
                continue

            # Wait for slot
            acquired = self.semaphore.acquire(timeout=QUEUE_TIMEOUT)
            if not acquired:
                request.error = "Queue timeout"
                request.result = {
                    "response": "Request queue timeout. Please try again.",
                    "session_id": request.session_id
                }
                request.completed.set()
                if request.callback:
                    request.callback(request)
                continue

            # Check resources
            resources_ok, resource_msg = self._check_resources()
            if not resources_ok:
                self.semaphore.release()
                request.error = resource_msg
                request.result = {
                    "response": f"Server resources low: {resource_msg}",
                    "session_id": request.session_id
                }
                request.completed.set()
                if request.callback:
                    request.callback(request)
                continue

            # Track session
            with self.session_lock:
                self.active_sessions += 1
                self.active_details[request.id] = {
                    "started_at": datetime.now().isoformat(),
                    "prompt_preview": request.prompt[:100]
                }
                logger.info(f"Agent started ({self.active_sessions}/{self.max_concurrent}) - {request.id}")

            try:
                request.result = self._invoke_claude(request)
            except Exception as e:
                request.error = str(e)
                request.result = {"response": f"Error: {e}", "session_id": request.session_id}
            finally:
                with self.session_lock:
                    self.active_sessions -= 1
                    self.active_details.pop(request.id, None)
                    logger.info(f"Agent ended ({self.active_sessions}/{self.max_concurrent}) - {request.id}")
                self.semaphore.release()

            request.completed.set()
            if request.callback:
                request.callback(request)

    def invoke(self, prompt: str, conversation_history: list = None,
               session_id: str = None, wait: bool = True,
               timeout: float = None) -> dict:
        """Submit an agent request.

        Args:
            prompt: The user's message
            conversation_history: List of previous messages
            session_id: Claude CLI session ID to resume
            wait: If True, block until complete. If False, return immediately.
            timeout: How long to wait (if wait=True)

        Returns:
            dict with 'response' and 'session_id' keys
        """
        request = AgentRequest(prompt, conversation_history, session_id)
        self.request_queue.put(request)

        if wait:
            wait_timeout = timeout or (DEFAULT_TIMEOUT + QUEUE_TIMEOUT)
            if request.completed.wait(timeout=wait_timeout):
                return request.result
            else:
                return {
                    "response": "Request timed out waiting for completion.",
                    "session_id": session_id
                }
        else:
            return {"queued": True, "request_id": request.id}

    def get_status(self) -> dict:
        """Get current pool status."""
        with self.session_lock:
            return {
                "active_sessions": self.active_sessions,
                "max_sessions": self.max_concurrent,
                "queue_size": self.request_queue.qsize(),
                "sessions": list(self.active_details.values())
            }

    def shutdown(self):
        """Shutdown the pool."""
        self._shutdown = True
        self.processor_thread.join(timeout=5)


# Singleton instance
_agent_pool: Optional[AgentPool] = None


def get_agent_pool() -> AgentPool:
    """Get the singleton AgentPool instance."""
    global _agent_pool
    if _agent_pool is None:
        _agent_pool = AgentPool()
    return _agent_pool


def invoke_claudius(prompt: str, conversation_history: list = None, session_id: str = None) -> dict:
    """Backwards-compatible wrapper for AgentPool.invoke().

    This function provides the same interface as the original invoke_claudius
    for easy migration.
    """
    pool = get_agent_pool()
    return pool.invoke(prompt, conversation_history, session_id)
