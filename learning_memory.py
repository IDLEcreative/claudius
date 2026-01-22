#!/usr/bin/env python3
"""
Learning Memory Integration for Claudius

Provides recall_memories() and store_memory() functions that interface
with the Learning Memory API running at localhost:3300.

This enables Claudius to:
1. Recall relevant memories before responding (activation)
2. Store new learnings after responding (storage)
"""

import os
import json
import logging
import requests
from typing import List, Dict, Optional
from functools import lru_cache

logger = logging.getLogger('claudius')

LEARNING_MEMORY_URL = 'http://localhost:3300'
LEARNING_MEMORY_ENV = '/opt/learning-memory/.env'

@lru_cache(maxsize=1)
def get_learning_memory_secret() -> Optional[str]:
    """Get API secret from learning memory .env file (cached)"""
    try:
        with open(LEARNING_MEMORY_ENV, 'r') as f:
            for line in f:
                if line.startswith('API_SECRET='):
                    return line.strip().split('=', 1)[1]
    except FileNotFoundError:
        logger.debug(f'[LearningMemory] {LEARNING_MEMORY_ENV} not found')
    except Exception as e:
        logger.debug(f'[LearningMemory] Error reading secret: {e}')
    return None


def _get_headers() -> Dict[str, str]:
    """Get authorization headers for Learning Memory API"""
    secret = get_learning_memory_secret()
    if not secret:
        return {}
    return {
        'Authorization': f'Bearer {secret}',
        'Content-Type': 'application/json'
    }


def recall_memories(
    query: str,
    agent: str = 'claudius',
    max_results: int = 5,
    threshold: float = 0.3
) -> List[Dict]:
    """
    Recall relevant memories before responding.
    
    Uses spreading activation to find contextually relevant memories
    based on the query.
    
    Args:
        query: The user prompt to find relevant memories for
        agent: Agent identifier for logging
        max_results: Maximum memories to return
        threshold: Minimum activation threshold
    
    Returns:
        List of memory dicts with 'content', 'activation', 'memory_type'
    """
    secret = get_learning_memory_secret()
    if not secret:
        logger.debug('[LearningMemory] No API secret configured, skipping recall')
        return []
    
    try:
        response = requests.post(
            f'{LEARNING_MEMORY_URL}/activate',
            headers=_get_headers(),
            json={
                'query': query,
                'agent': agent,
                'max_results': max_results,
                'threshold': threshold
            },
            timeout=5  # Fast timeout to not block response
        )
        
        if response.ok:
            data = response.json()
            memories = data.get('memories', [])
            
            if memories:
                logger.info(f'[LearningMemory] Recalled {len(memories)} memories')
                for m in memories[:3]:  # Log top 3
                    logger.debug(f'  - {m.get("content", "")[:50]}... (activation: {m.get("activation", 0):.2f})')
            
            return memories
        else:
            logger.warning(f'[LearningMemory] Recall failed: {response.status_code}')
            
    except requests.exceptions.Timeout:
        logger.debug('[LearningMemory] Recall timed out (5s)')
    except requests.exceptions.ConnectionError:
        logger.debug('[LearningMemory] Service not reachable')
    except Exception as e:
        logger.debug(f'[LearningMemory] Recall error: {e}')
    
    return []


def store_memory(
    content: str,
    memory_type: str = 'episodic',
    summary: str = None,
    concepts: List[str] = None,
    base_salience: float = 0.5
) -> Optional[Dict]:
    """
    Store a new memory after significant learning.
    
    Only call this for truly useful/surprising information, not for
    routine operations.
    
    Args:
        content: The memory content to store
        memory_type: Type of memory ('episodic', 'semantic', 'procedural')
        summary: Optional short summary
        concepts: Optional list of concept tags
        base_salience: Initial importance (0-1)
    
    Returns:
        Response dict with 'id' if successful, None otherwise
    """
    secret = get_learning_memory_secret()
    if not secret:
        logger.debug('[LearningMemory] No API secret configured, skipping store')
        return None
    
    try:
        response = requests.post(
            f'{LEARNING_MEMORY_URL}/store',
            headers=_get_headers(),
            json={
                'content': content,
                'summary': summary,
                'memory_type': memory_type,
                'source_agent': 'claudius',
                'base_salience': base_salience,
                'concepts': concepts or []
            },
            timeout=10  # Longer timeout for embedding generation
        )
        
        if response.ok:
            data = response.json()
            logger.info(f'[LearningMemory] Stored memory: {data.get("id", "unknown")[:8]}')
            return data
        else:
            logger.warning(f'[LearningMemory] Store failed: {response.status_code}')
            
    except requests.exceptions.Timeout:
        logger.debug('[LearningMemory] Store timed out (10s)')
    except requests.exceptions.ConnectionError:
        logger.debug('[LearningMemory] Service not reachable')
    except Exception as e:
        logger.debug(f'[LearningMemory] Store error: {e}')
    
    return None


def format_memories_for_context(memories: List[Dict], max_chars: int = 2000) -> str:
    """
    Format recalled memories for injection into Claude's context.
    
    Args:
        memories: List of memory dicts from recall_memories()
        max_chars: Maximum characters to include
    
    Returns:
        Formatted string for context injection
    """
    if not memories:
        return ''
    
    lines = ['\n### Relevant Memories from Past Experience:\n']
    total_chars = len(lines[0])
    
    for m in memories:
        activation = m.get('activation', 0)
        content = m.get('content', '')
        memory_type = m.get('memory_type', 'episodic')
        
        # Format: [type] content (confidence: X.XX)
        line = f'- [{memory_type}] {content[:200]} (confidence: {activation:.2f})\n'
        
        if total_chars + len(line) > max_chars:
            break
        
        lines.append(line)
        total_chars += len(line)
    
    if len(lines) == 1:
        return ''  # No memories added
    
    lines.append('\n---\n')
    return ''.join(lines)


def detect_and_store_surprise(
    prompt: str,
    response: str,
    surprise_keywords: List[str] = None,
    auto_store: bool = True
) -> Optional[Dict]:
    """
    Detect if response contains surprising/valuable information worth storing.
    
    Heuristic detection based on:
    - Surprise keywords (unexpected, actually, turns out, etc.)
    - Contradiction patterns (expected X but found Y)
    - Problem resolution patterns (fixed, solved, resolved)
    
    Args:
        prompt: The original prompt
        response: The generated response
        surprise_keywords: Custom keywords to detect (uses defaults if None)
        auto_store: Whether to automatically store if surprise detected
    
    Returns:
        Stored memory dict if surprise detected and stored, None otherwise
    """
    if surprise_keywords is None:
        surprise_keywords = [
            'unexpected', 'surprisingly', 'actually', 'turns out',
            'contrary to', 'contradicts', 'discovered', 'realized',
            'fixed', 'solved', 'resolved', 'root cause', 'workaround',
            'important note', 'critical', 'key insight', 'learned that'
        ]
    
    response_lower = response.lower()
    score = 0.0
    detected_keywords = []
    
    # Check for surprise keywords (+0.15 each)
    for keyword in surprise_keywords:
        if keyword in response_lower:
            score += 0.15
            detected_keywords.append(keyword)
    
    # Check for contradiction patterns (+0.25 each)
    contradiction_patterns = [
        ('expected', 'but'),
        ('thought', 'actually'),
        ('assumed', 'however'),
    ]
    for pattern in contradiction_patterns:
        if all(p in response_lower for p in pattern):
            score += 0.25
    
    # Cap at 1.0
    score = min(1.0, score)
    
    # Only store if above threshold (0.5)
    if score >= 0.5 and auto_store:
        # Determine memory type from content
        if any(w in response_lower for w in ['fixed', 'solved', 'step', 'run', 'command']):
            memory_type = 'procedural'
        elif any(w in response_lower for w in ['is', 'always', 'never', 'typically']):
            memory_type = 'semantic'
        else:
            memory_type = 'episodic'
        
        # Create summary from first 100 chars
        summary = response[:100].strip()
        if len(response) > 100:
            summary += '...'
        
        # Extract concepts from detected keywords
        concepts = detected_keywords[:5] if detected_keywords else None
        
        logger.info(f'[LearningMemory] Surprise detected (score: {score:.2f}), storing memory')
        return store_memory(
            content=response[:2000],  # Limit content size
            memory_type=memory_type,
            summary=summary,
            concepts=concepts,
            base_salience=score
        )
    
    return None


def health_check() -> bool:
    """Check if Learning Memory API is accessible"""
    try:
        response = requests.get(
            f'{LEARNING_MEMORY_URL}/health',
            timeout=2
        )
        return response.ok
    except:
        return False


# Quick test when run directly
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    
    print('Testing Learning Memory Integration...')
    print(f'API URL: {LEARNING_MEMORY_URL}')
    print(f'Secret configured: {bool(get_learning_memory_secret())}')
    print(f'Health check: {health_check()}')
    
    # Test recall
    print('\nTesting recall...')
    memories = recall_memories('How do I restart Docker containers?')
    print(f'Recalled {len(memories)} memories')
    for m in memories:
        print(f'  - {m.get("content", "")[:60]}...')
    
    # Test store
    print('\nTesting store...')
    result = store_memory(
        content='Test memory from learning_memory.py integration test',
        memory_type='episodic',
        concepts=['test', 'integration']
    )
    print(f'Store result: {result}')
