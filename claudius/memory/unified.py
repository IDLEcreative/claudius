"""
UnifiedMemory Facade - Single interface to all memory systems.

Claudius has three memory systems:
1. Engram - Semantic search via HTTP API
2. Supabase - Conversation history (structured)
3. File - MEMORY.md for core facts

This facade provides a single interface to query all three and returns
a consolidated, context-efficient response.
"""

import json
import logging
import urllib.request
import urllib.error
import os
from typing import Optional
from datetime import datetime, timedelta

from claudius.core.circuit_breaker import engram_breaker

logger = logging.getLogger('claudius.memory')

ENGRAM_API_URL = os.environ.get('ENGRAM_API_URL', 'http://localhost:3201/engram')
ENGRAM_API_KEY = os.environ.get('ADMIN_SECRET', '')
SUPABASE_URL = os.environ.get('NEXT_PUBLIC_SUPABASE_URL')
SUPABASE_ANON_KEY = os.environ.get('NEXT_PUBLIC_SUPABASE_ANON_KEY')
MEMORY_MD_PATH = '/opt/claudius/MEMORY.md'
MAX_HISTORY_MESSAGES = 10
MAX_SEMANTIC_MEMORIES = 5
MAX_CONTEXT_CHARS = 8000


class UnifiedMemory:
    """Unified interface to all Claudius memory systems."""

    def __init__(self):
        self._supabase_available = None

    def _check_engram(self) -> bool:
        """Check if Engram API is available via circuit breaker state."""
        return engram_breaker.state != 'open'

    def _check_supabase(self) -> bool:
        """Check if Supabase is configured."""
        if self._supabase_available is None:
            self._supabase_available = bool(SUPABASE_URL and SUPABASE_ANON_KEY)
        return self._supabase_available

    def get_core_facts(self) -> str:
        """Get core facts from MEMORY.md (owner profile, preferences, config)."""
        if not os.path.exists(MEMORY_MD_PATH):
            return ''
        try:
            with open(MEMORY_MD_PATH, 'r') as f:
                content = f.read()

            # Only return content before "Recent Sessions" section
            sections = content.split('## Recent Sessions')
            return sections[0].strip()[:2000]
        except Exception as e:
            logger.error(f'Failed to read MEMORY.md: {e}')
            return ''

    def get_recent_history(self, chat_id: int = 0,
                           limit: int = MAX_HISTORY_MESSAGES) -> list:
        """Get recent conversation history from Supabase."""
        if not self._check_supabase():
            return []

        try:
            cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
            endpoint = (
                f'claudius_messages?chat_id=eq.{chat_id}'
                f'&created_at=gte.{cutoff}'
                f'&order=created_at.desc&limit={limit}'
            )
            url = f'{SUPABASE_URL}/rest/v1/{endpoint}'

            headers = {
                'apikey': SUPABASE_ANON_KEY,
                'Authorization': f'Bearer {SUPABASE_ANON_KEY}',
            }

            req = urllib.request.Request(url, headers=headers, method='GET')
            response = urllib.request.urlopen(req, timeout=5)
            result = json.loads(response.read().decode('utf-8'))

            if isinstance(result, list):
                return list(reversed(result))
            return []
        except Exception as e:
            logger.error(f'Failed to get Supabase history: {e}')
            return []

    def _raw_search_semantic(self, query: str, limit: int = MAX_SEMANTIC_MEMORIES) -> list:
        """Internal: Execute Engram search (called through circuit breaker)."""
        data = json.dumps({'query': query, 'limit': limit}).encode('utf-8')

        headers = {
            'Content-Type': 'application/json',
        }
        if ENGRAM_API_KEY:
            headers['Authorization'] = f'Bearer {ENGRAM_API_KEY}'

        req = urllib.request.Request(
            f'{ENGRAM_API_URL}/search',
            data=data,
            headers=headers,
            method='POST',
        )
        response = urllib.request.urlopen(req, timeout=5)
        result = json.loads(response.read().decode('utf-8'))
        return result.get('memories', [])

    def search_semantic(self, query: str, limit: int = MAX_SEMANTIC_MEMORIES) -> list:
        """Search Engram for semantically relevant memories via circuit breaker."""
        if not self._check_engram():
            return []

        return engram_breaker.call(self._raw_search_semantic, query, limit, fallback=[])

    def build_context(self, chat_id: int = 0, current_query: str = '') -> str:
        """Build optimized context from all memory systems.

        Returns a consolidated string with:
        1. Core facts (owner profile, preferences)
        2. Recent conversation history (last N messages)
        3. Semantically relevant memories (if query warrants)

        Stays within MAX_CONTEXT_CHARS to prevent context window waste.
        """
        parts = []
        total_chars = 0

        # 1. Core facts
        core_facts = self.get_core_facts()
        if core_facts:
            parts.append('=== OWNER PROFILE & PREFERENCES ===\n' + core_facts)
            total_chars += len(core_facts)

        # 2. Recent history
        if total_chars < MAX_CONTEXT_CHARS:
            history = self.get_recent_history(chat_id)
            if history:
                history_text = '\n=== RECENT CONVERSATION ===\n'
                for msg in history:
                    role = msg.get('role', '?').upper()
                    content = msg.get('content', '')
                    if content is None:
                        content = ''
                    line = f'{role}: {content[:500]}\n'
                    if total_chars + len(line) > MAX_CONTEXT_CHARS:
                        break
                    history_text += line
                    total_chars += len(line)
                parts.append(history_text)

        # 3. Semantic memories (if room remains and query warrants)
        if total_chars < MAX_CONTEXT_CHARS and len(current_query) > 10:
            memories = self.search_semantic(current_query)
            if memories:
                mem_text = '\n=== RELEVANT MEMORIES ===\n'
                for mem in memories:
                    content = mem.get('content', '')
                    if content:
                        line = f'- {content[:300]}\n'
                        if total_chars + len(line) > MAX_CONTEXT_CHARS:
                            break
                        mem_text += line
                        total_chars += len(line)
                parts.append(mem_text)

        return '\n'.join(parts)

    def save_memory(self, content: str, memory_type: str = 'episodic') -> bool:
        """Save a memory to Engram."""
        if not self._check_engram():
            return False

        try:
            data = json.dumps({
                'content': content,
                'memoryType': memory_type,
                'timestamp': datetime.utcnow().isoformat(),
            }).encode('utf-8')

            headers = {
                'Content-Type': 'application/json',
            }
            if ENGRAM_API_KEY:
                headers['Authorization'] = f'Bearer {ENGRAM_API_KEY}'

            req = urllib.request.Request(
                f'{ENGRAM_API_URL}/store',
                data=data,
                headers=headers,
                method='POST',
            )
            response = urllib.request.urlopen(req, timeout=5)
            return response.getcode() == 200
        except Exception as e:
            logger.error(f'Failed to save memory: {e}')
            return False


_unified_memory: Optional[UnifiedMemory] = None


def get_unified_memory() -> UnifiedMemory:
    """Get the singleton UnifiedMemory instance."""
    global _unified_memory
    if _unified_memory is None:
        _unified_memory = UnifiedMemory()
    return _unified_memory
