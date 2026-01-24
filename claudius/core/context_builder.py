"""
Context Builder - Assembles Claude CLI input from memory sources.

Handles:
- Parallel pre-processing of memory sources
- Context string assembly with degradation signaling
- Timeout enforcement per source
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Optional

logger = logging.getLogger('claudius.context_builder')

CLAUDIUS_DIR = '/opt/claudius'
CLAUDE_MD = CLAUDIUS_DIR + '/CLAUDE.md'
MEMORY_MD = CLAUDIUS_DIR + '/MEMORY.md'
PREPROCESS_TIMEOUT = 6

try:
    from learning_memory import recall_memories, format_memories_for_context, detect_and_store_surprise
    LEARNING_MEMORY_AVAILABLE = True
except ImportError:
    LEARNING_MEMORY_AVAILABLE = False

_unified_memory = None
_unified_memory_last_attempt = 0.0


def _get_memory():
    """Get UnifiedMemory instance, retrying every 60s on failure."""
    global _unified_memory, _unified_memory_last_attempt
    if _unified_memory is not None:
        return _unified_memory

    now = time.time()
    if now - _unified_memory_last_attempt < 60:
        return None

    _unified_memory_last_attempt = now
    try:
        from claudius.memory.unified import get_unified_memory
        _unified_memory = get_unified_memory()
        logger.info('[UnifiedMemory] Initialized successfully')
    except Exception as e:
        logger.warning(f'UnifiedMemory not available: {e}')
    return _unified_memory


def _fetch_learning_memory(prompt: str) -> tuple[str, bool]:
    """Fetch learning memory context. Returns (context_str, success)."""
    if not LEARNING_MEMORY_AVAILABLE:
        return '', True

    memories = recall_memories(prompt, agent='claudius', max_results=5)
    context = format_memories_for_context(memories, max_chars=1500)
    if context:
        logger.info(f'[LearningMemory] Injecting {len(memories)} memories into context')
    return context, True


def _fetch_unified_memory(prompt: str) -> tuple[str, bool]:
    """Fetch unified memory context. Returns (context_str, success)."""
    memory = _get_memory()
    if not memory:
        return '', False

    return memory.build_context(chat_id=0, current_query=prompt), True


def build_context(prompt: str, conversation_history: list = None,
                  session_id: str = None) -> tuple:
    """Build full context for Claude CLI invocation.

    Runs memory sources in parallel with PREPROCESS_TIMEOUT cap.

    Returns:
        (context_string, sources_failed_list)
    """
    sources_failed = []
    learning_memory_context = ''
    unified_context = ''

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='preprocess') as executor:
        lm_future = executor.submit(_fetch_learning_memory, prompt)
        um_future = executor.submit(_fetch_unified_memory, prompt)

        # Collect learning memory result
        try:
            lm_result, lm_ok = lm_future.result(timeout=PREPROCESS_TIMEOUT)
            learning_memory_context = lm_result
            if not lm_ok:
                sources_failed.append('learning_memory')
        except (FuturesTimeout, Exception) as e:
            logger.warning(f'[Preprocess] Learning Memory failed: {e}')
            sources_failed.append('learning_memory')

        # Collect unified memory result
        try:
            um_result, um_ok = um_future.result(timeout=PREPROCESS_TIMEOUT)
            unified_context = um_result
            if not um_ok:
                sources_failed.append('unified_memory')
        except (FuturesTimeout, Exception) as e:
            logger.warning(f'[Preprocess] Unified Memory failed: {e}')
            sources_failed.append('unified_memory')

    # Build degradation note if sources failed
    degradation_note = ''
    if sources_failed:
        failed_list = ', '.join(sources_failed)
        degradation_note = (
            f'\n[SYSTEM NOTE: The following memory sources were unavailable: {failed_list}. '
            f"You may be missing context from previous conversations. Acknowledge if the user "
            f"seems to reference something you don't have context for.]\n"
        )
        logger.info(f'[Degradation] Sources unavailable: {failed_list}')

    # Assemble context
    if unified_context:
        context = (
            f'You are Claudius, the bare metal server Claude (Emperor/Overseer).\n\n'
            f'Read your instructions from {CLAUDE_MD}.\n\n'
            f'{unified_context}'
            f'{learning_memory_context}'
            f'{degradation_note}'
            f'\nCurrent user request: {prompt}'
            f'\n\nIMPORTANT: Use the context above to provide relevant responses.'
        )
    else:
        # Fallback: format history manually
        history_context = ''
        if conversation_history and not session_id:
            history_context = '\n--- CONVERSATION HISTORY ---\n'
            for msg in conversation_history:
                role = msg.get('role', '?').upper()
                content = msg.get('content', '')
                history_context += f'\n{role}: {content}\n'
            history_context += '\n--- END HISTORY ---\n'

        context = (
            f'You are Claudius, the bare metal server Claude (Emperor/Overseer).\n\n'
            f'Read your instructions from {CLAUDE_MD}.\n'
            f'Read your memory from {MEMORY_MD} for context from previous sessions.\n'
            f'{learning_memory_context}'
            f'{degradation_note}'
            f'{history_context}'
            f'\nCurrent user request: {prompt}'
            f'\n\nIMPORTANT: If the user refers to something from the conversation history above, use that context.'
        )

    return context, sources_failed


def learn_from_response(prompt: str, response: str):
    """Detect surprise in response and store as memory (fire-and-forget)."""
    if not LEARNING_MEMORY_AVAILABLE:
        return
    try:
        detect_and_store_surprise(prompt=prompt, response=response, auto_store=True)
    except Exception as e:
        logger.debug(f'[LearningMemory] Surprise detection failed: {e}')
