"""
Claudius Library Module

Core utilities and abstractions for the Claudius bare metal agent.
"""

from .tool_registry import (
    ToolCategory,
    ToolComplexity,
    ToolMetadata,
    ToolRegistry,
    RegisteredTool,
    ToolExecutionResult,
    global_registry,
    get_global_registry,
)

__all__ = [
    "ToolCategory",
    "ToolComplexity",
    "ToolMetadata",
    "ToolRegistry",
    "RegisteredTool",
    "ToolExecutionResult",
    "global_registry",
    "get_global_registry",
]
