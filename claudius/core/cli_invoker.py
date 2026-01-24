"""
CLI Invoker - Claude CLI subprocess management and response parsing.

Handles:
- Subprocess execution with process group isolation
- Timeout enforcement with proper zombie cleanup
- Token swap retry on account limits
- Session resume retry on CLI failures
- Response parsing (JSON or plain text)
"""

import json
import logging
import os
import signal
import subprocess
import threading
import time

logger = logging.getLogger('claudius.cli_invoker')

# Token swap integration
try:
    from claudius.core.token_swap import check_and_swap_if_limited
    TOKEN_SWAP_AVAILABLE = True
except ImportError:
    TOKEN_SWAP_AVAILABLE = False

# Context builder integration (build_context imported as build_agent_context)
from claudius.core.context_builder import (
    build_context as build_agent_context,
    learn_from_response,
    LEARNING_MEMORY_AVAILABLE,
)

CLAUDIUS_DIR = '/opt/claudius'
MCP_API_CONFIG = f'{CLAUDIUS_DIR}/.mcp-api.json'

# Timeouts
DEFAULT_TIMEOUT = 120
SESSION_RESUME_TIMEOUT = 180
REQUEST_DEADLINE = 180
MAX_INVOKE_RETRIES = 2


def _run_subprocess(cmd: list, input_text: str, cwd: str, env: dict,
                    timeout: float) -> tuple[str, str, int]:
    """Execute subprocess with process group isolation and proper cleanup.

    Returns:
        (stdout, stderr, returncode) tuple

    Raises:
        subprocess.TimeoutExpired: If timeout exceeded
    """
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
        preexec_fn=os.setsid,
    )

    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=timeout)
        return stdout.strip(), stderr.strip(), proc.returncode
    except subprocess.TimeoutExpired:
        # Kill entire process group
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            proc.kill()
        proc.communicate(timeout=5)
        raise


def _parse_success_response(stdout: str, stderr: str, returncode: int,
                            request, duration: float, metrics) -> dict:
    """Parse successful CLI output into response dict."""
    if stdout:
        try:
            json_response = json.loads(stdout)
            response_text = json_response.get('result', stdout)

            logger.info(f'[Claude CLI] Completed in {duration:.1f}s (exit={returncode})')
            metrics.record(duration, success=True, sources_failed=None)

            # Fire-and-forget surprise detection
            if LEARNING_MEMORY_AVAILABLE:
                threading.Thread(
                    target=learn_from_response,
                    args=(request.prompt, response_text[:2000]),
                    daemon=True,
                    name='surprise-detect',
                ).start()

            return {
                'response': response_text,
                'session_id': json_response.get('session_id'),
            }
        except json.JSONDecodeError:
            logger.info(
                f'[Claude CLI] Completed in {duration:.1f}s (exit={returncode}, non-json)'
            )
            metrics.record(duration, success=True, sources_failed=None)
            return {
                'response': stdout,
                'session_id': request.session_id,
            }

    # Empty stdout
    error_msg = 'No response from Claude CLI'
    logger.warning(
        f'[Claude CLI] Empty stdout in {duration:.1f}s. returncode={returncode}, '
        f'stderr={stderr[:200]}'
    )
    metrics.record(duration, success=False, sources_failed=None)
    return {
        'response': error_msg,
        'session_id': request.session_id,
    }


def invoke_claude(request, metrics) -> dict:
    """Full Claude CLI invocation with context building, execution, and retry.

    Args:
        request: AgentRequest with prompt, conversation_history, session_id
        metrics: RequestMetrics instance for recording durations

    Returns:
        dict with 'response' and 'session_id' keys
    """
    start_time = time.time()

    # Build context
    sources_failed, context = None, None
    context, sources_failed = build_agent_context(
        prompt=request.prompt,
        conversation_history=request.conversation_history,
        session_id=request.session_id,
    )

    # Build command
    cmd = [
        'claude', '--print',
        '--output-format', 'json',
        '--permission-mode', 'bypassPermissions',
        '--mcp-config', MCP_API_CONFIG,
        '--strict-mcp-config',
    ]

    if request.session_id:
        cmd.extend(['--resume', request.session_id])

    # Set environment
    env = os.environ.copy()
    env['IS_SANDBOX'] = '1'

    # Calculate effective timeout
    cli_timeout = SESSION_RESUME_TIMEOUT if request.session_id else DEFAULT_TIMEOUT

    # Check if we have a request deadline
    elapsed = time.time() - getattr(request, 'created_at', start_time).timestamp() if hasattr(request, 'created_at') else 0
    effective_timeout = min(max(cli_timeout, 30), REQUEST_DEADLINE - elapsed) if elapsed else cli_timeout

    # Execute
    try:
        stdout, stderr, returncode = _run_subprocess(cmd, context, CLAUDIUS_DIR, env, effective_timeout)
    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        logger.error(
            f'[Claude CLI] Timeout after {duration:.1f}s for request {getattr(request, "id", "?")}'
        )
        metrics.record(duration, success=False, sources_failed=sources_failed)
        hint = ' (session resume may be slow \u2014 try /new to start fresh)' if request.session_id else ''
        return {
            'response': f'Request timed out.{hint}',
            'session_id': request.session_id,
        }
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f'[Claude CLI] Exception after {duration:.1f}s: {e}', exc_info=True)
        metrics.record(duration, success=False, sources_failed=sources_failed)
        return {
            'response': f'Error: {str(e)}',
            'session_id': request.session_id,
        }

    # Check stderr for warnings
    if stderr:
        logger.warning(f'[Claude CLI] stderr: {stderr[:500]}')

    # Check for token swap opportunity on non-zero exit
    retry_depth = getattr(request, '_retry_depth', 0)
    if returncode != 0 and TOKEN_SWAP_AVAILABLE and retry_depth < MAX_INVOKE_RETRIES:
        swapped, swap_msg = check_and_swap_if_limited(stderr + ' ' + stdout)
        if swapped:
            logger.info(f'[TokenSwap] {swap_msg} - Retrying with fresh session...')
            request._retried = True
            request._retry_depth = retry_depth + 1
            # Retry without session to avoid stale state
            request.session_id = None
            return invoke_claude(request, metrics)

    # Non-zero exit with session - retry without session
    if returncode != 0 and request.session_id and not getattr(request, '_retried', False):
        logger.warning(
            f'[Claude CLI] Non-zero exit ({returncode}) with session. Retrying without session.'
        )
        request._retried = True
        request.session_id = None
        return invoke_claude(request, metrics)

    duration = time.time() - start_time
    return _parse_success_response(stdout, stderr, returncode, request, duration, metrics)
