"""
Tool Registry for Claudius

A lightweight Python implementation of the tool registry pattern
for managing infrastructure tools in the Claudius bare metal agent.

Usage:
    from lib import ToolRegistry, ToolMetadata

    registry = ToolRegistry()

    @registry.tool(
        name="check_disk_space",
        description="Check disk space on the server",
        category="monitoring",
        dangerous=False
    )
    async def check_disk_space(context: dict) -> dict:
        # Implementation
        return {"usage_percent": 45.2, "free_gb": 120.5}

    # Execute tool
    result = await registry.execute("check_disk_space", {}, context)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypedDict, Awaitable
import time
import asyncio
from functools import wraps

# ============================================================================
# Type Definitions
# ============================================================================


class ToolCategory(str, Enum):
    """Categories for organizing tools by function."""
    MONITORING = "monitoring"
    DOCKER = "docker"
    DEPLOYMENT = "deployment"
    MAINTENANCE = "maintenance"
    SECURITY = "security"
    INFRASTRUCTURE = "infrastructure"


class ToolComplexity(str, Enum):
    """Complexity levels affecting timeouts and execution strategy."""
    SIMPLE = "simple"        # < 5 seconds
    MODERATE = "moderate"    # 5-30 seconds
    COMPLEX = "complex"      # 30+ seconds


@dataclass
class ToolMetadata:
    """Rich metadata describing a tool's characteristics."""
    name: str
    description: str
    category: ToolCategory
    complexity: ToolComplexity = ToolComplexity.MODERATE
    estimated_duration_ms: int = 5000
    rate_limit_per_minute: Optional[int] = None
    requires_auth: bool = False
    dangerous: bool = False
    version: str = "1.0.0"
    tags: List[str] = field(default_factory=list)


class ToolExecutionResult(TypedDict, total=False):
    """Result of a tool execution."""
    success: bool
    data: Any
    error: Optional[str]
    execution_time_ms: int
    tool_name: str


@dataclass
class RegisteredTool:
    """A fully registered tool with metadata and handler."""
    metadata: ToolMetadata
    handler: Callable[..., Awaitable[Any]]
    parameter_schema: Optional[Dict[str, Any]] = None
    return_schema: Optional[Dict[str, Any]] = None


# ============================================================================
# Tool Registry Class
# ============================================================================


class ToolRegistry:
    """
    Centralized registry for managing Claudius infrastructure tools.

    Example:
        registry = ToolRegistry()

        @registry.tool(
            name="restart_container",
            description="Restart a Docker container",
            category="docker",
            dangerous=True
        )
        async def restart_container(params: dict, context: dict) -> dict:
            container_name = params.get("container")
            # ... implementation
            return {"status": "restarted", "container": container_name}

        # Later, execute the tool
        result = await registry.execute("restart_container", {"container": "app"}, context)
    """

    def __init__(self) -> None:
        self._tools: Dict[str, RegisteredTool] = {}

    # ========================================================================
    # Registration
    # ========================================================================

    def register(
        self,
        metadata: ToolMetadata,
        handler: Callable[..., Awaitable[Any]],
        parameter_schema: Optional[Dict[str, Any]] = None,
        return_schema: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register a new tool in the registry."""
        if metadata.name in self._tools:
            raise ValueError(f"Tool '{metadata.name}' is already registered")

        self._tools[metadata.name] = RegisteredTool(
            metadata=metadata,
            handler=handler,
            parameter_schema=parameter_schema,
            return_schema=return_schema,
        )

    def tool(
        self,
        name: str,
        description: str,
        category: ToolCategory,
        complexity: ToolComplexity = ToolComplexity.MODERATE,
        estimated_duration_ms: int = 5000,
        dangerous: bool = False,
        tags: Optional[List[str]] = None,
    ) -> Callable:
        """
        Decorator for registering a tool.

        Example:
            @registry.tool(
                name="check_health",
                description="Check system health",
                category=ToolCategory.MONITORING
            )
            async def check_health(params: dict, context: dict) -> dict:
                return {"status": "healthy"}
        """
        def decorator(func: Callable[..., Awaitable[Any]]) -> Callable:
            metadata = ToolMetadata(
                name=name,
                description=description,
                category=category,
                complexity=complexity,
                estimated_duration_ms=estimated_duration_ms,
                dangerous=dangerous,
                tags=tags or [],
            )
            self.register(metadata, func)

            @wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                return await func(*args, **kwargs)

            return wrapper

        return decorator

    def unregister(self, name: str) -> bool:
        """Unregister a tool from the registry."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    # ========================================================================
    # Retrieval
    # ========================================================================

    def get(self, name: str) -> Optional[RegisteredTool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def list(
        self,
        category: Optional[ToolCategory] = None,
        exclude_dangerous: bool = False,
    ) -> List[ToolMetadata]:
        """List all tool metadata, optionally filtered."""
        tools = list(self._tools.values())

        if category:
            tools = [t for t in tools if t.metadata.category == category]

        if exclude_dangerous:
            tools = [t for t in tools if not t.metadata.dangerous]

        return [t.metadata for t in tools]

    @property
    def size(self) -> int:
        """Number of registered tools."""
        return len(self._tools)

    # ========================================================================
    # Execution
    # ========================================================================

    async def execute(
        self,
        name: str,
        params: Dict[str, Any],
        context: Dict[str, Any],
        timeout_ms: Optional[int] = None,
    ) -> ToolExecutionResult:
        """
        Execute a tool with the given parameters.

        Args:
            name: Tool name
            params: Parameters to pass to the tool
            context: Execution context (org_id, etc.)
            timeout_ms: Optional timeout override

        Returns:
            ToolExecutionResult with success/failure status
        """
        start_time = time.time()
        tool = self._tools.get(name)

        if not tool:
            return ToolExecutionResult(
                success=False,
                error=f"Tool '{name}' not found",
                execution_time_ms=0,
                tool_name=name,
            )

        # Determine timeout
        timeout = (timeout_ms or tool.metadata.estimated_duration_ms * 2) / 1000

        try:
            # Execute with timeout
            result = await asyncio.wait_for(
                tool.handler(params, context),
                timeout=timeout
            )

            execution_time = int((time.time() - start_time) * 1000)

            return ToolExecutionResult(
                success=True,
                data=result,
                execution_time_ms=execution_time,
                tool_name=name,
            )

        except asyncio.TimeoutError:
            execution_time = int((time.time() - start_time) * 1000)
            return ToolExecutionResult(
                success=False,
                error=f"Tool '{name}' timed out after {timeout}s",
                execution_time_ms=execution_time,
                tool_name=name,
            )

        except Exception as e:
            execution_time = int((time.time() - start_time) * 1000)
            return ToolExecutionResult(
                success=False,
                error=str(e),
                execution_time_ms=execution_time,
                tool_name=name,
            )

    # ========================================================================
    # Format Conversion (for AI providers)
    # ========================================================================

    def to_anthropic_format(
        self,
        category: Optional[ToolCategory] = None,
        exclude_dangerous: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Convert tools to Anthropic tool_use format.

        Returns:
            List of tool definitions in Anthropic format
        """
        tools = list(self._tools.values())

        if category:
            tools = [t for t in tools if t.metadata.category == category]

        if exclude_dangerous:
            tools = [t for t in tools if not t.metadata.dangerous]

        return [
            {
                "name": t.metadata.name,
                "description": t.metadata.description,
                "input_schema": t.parameter_schema or {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            }
            for t in tools
        ]

    def to_openai_format(
        self,
        category: Optional[ToolCategory] = None,
        exclude_dangerous: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Convert tools to OpenAI function calling format.

        Returns:
            List of tool definitions in OpenAI format
        """
        tools = list(self._tools.values())

        if category:
            tools = [t for t in tools if t.metadata.category == category]

        if exclude_dangerous:
            tools = [t for t in tools if not t.metadata.dangerous]

        return [
            {
                "type": "function",
                "function": {
                    "name": t.metadata.name,
                    "description": t.metadata.description,
                    "parameters": t.parameter_schema or {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            }
            for t in tools
        ]


# ============================================================================
# Global Registry Instance
# ============================================================================

global_registry = ToolRegistry()


def get_global_registry() -> ToolRegistry:
    """Get the global tool registry instance."""
    return global_registry
