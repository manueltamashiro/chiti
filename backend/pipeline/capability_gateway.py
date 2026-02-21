"""
Capability Gateway - Unified registry and validation for tools and skills

This module provides the foundation for both simple tools and agent skills,
ensuring they share the same security infrastructure.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
import asyncio
import logging

from .models import (
    CapabilityMetadata,
    CapabilityType,
    ActionTier,
    OutputBlock,
    ToolResult,
    ProcessedResult,
)

logger = logging.getLogger(__name__)


class Capability(ABC):
    """
    Abstract base class for all capabilities (tools and skills).

    Both simple tools and multi-step agent skills extend this class,
    ensuring they share the same metadata structure and security checks.
    """

    def __init__(self):
        self._metadata: Optional[CapabilityMetadata] = None

    @property
    @abstractmethod
    def metadata(self) -> CapabilityMetadata:
        """
        Return the capability metadata.

        This must be implemented by all tools and skills to declare:
        - Name and type
        - Tier (for confirmation requirements)
        - Allowed paths, networks, databases
        - OAuth requirements
        - Tools used (for skills)
        """
        pass

    @abstractmethod
    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        """
        Execute the capability with the given parameters.

        Args:
            params: Execution parameters (validated against schema)

        Returns:
            OutputBlock: Typed output for frontend rendering

        Raises:
            ValueError: If parameters are invalid
            PermissionError: If operation is not allowed
            RuntimeError: If execution fails
        """
        pass

    @abstractmethod
    def get_parameter_schema(self) -> Dict[str, Any]:
        """
        Return JSON schema for parameters.

        This is used for validation and for presenting to the LLM.

        Returns:
            Dict following JSON Schema format
        """
        pass

    async def validate_params(self, params: Dict[str, Any]) -> None:
        """
        Validate parameters against schema.

        Raises:
            ValueError: If parameters are invalid
        """
        schema = self.get_parameter_schema()
        # Basic validation - can be enhanced with jsonschema library
        required = schema.get("required", [])
        for field in required:
            if field not in params:
                raise ValueError(f"Missing required parameter: {field}")

        # Type validation for basic types
        properties = schema.get("properties", {})
        for key, value in params.items():
            if key in properties:
                expected_type = properties[key].get("type")
                if expected_type and not self._check_type(value, expected_type):
                    raise ValueError(
                        f"Parameter '{key}' expected type {expected_type}, got {type(value).__name__}"
                    )

    def _check_type(self, value: Any, expected_type: str) -> bool:
        """Basic type checking"""
        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        expected_python_type = type_map.get(expected_type)
        if expected_python_type:
            return isinstance(value, expected_python_type)
        return True


class ToolBase(Capability):
    """
    Base class for simple single-operation tools.

    Simple tools perform a single operation (read file, list directory, etc.)
    and return immediately. For multi-step workflows, use SkillBase instead.

    Example:
        class ReadFileTool(ToolBase):
            @property
            def metadata(self):
                return CapabilityMetadata(
                    name="read_file",
                    type=CapabilityType.TOOL,
                    tier=ActionTier.TIER_1,
                    description="Read a file's contents",
                    allowed_paths=["local://~/Documents/**", "local://~/projects/**"],
                )

            async def execute(self, params: dict) -> OutputBlock:
                path = params["path"]
                content = await self._read_file(path)
                return OutputBlock(type="text", content=content)

            def get_parameter_schema(self):
                return {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to read"}
                    },
                    "required": ["path"]
                }
    """

    pass  # ToolBase inherits all Capability interface


class SkillBase(Capability):
    """
    Base class for multi-step agent skills using LangGraph.

    Skills are tools that:
    - Use LangGraph for multi-step orchestration
    - May call other tools internally
    - Can have branching logic based on results
    - Stream intermediate outputs
    - Can be cancelled mid-execution

    Example:
        class ResearchSkill(SkillBase):
            @property
            def metadata(self):
                return CapabilityMetadata(
                    name="research",
                    type=CapabilityType.SKILL,
                    tier=ActionTier.TIER_2,
                    description="Deep research using web + file analysis",
                    tools_used=["web_search", "read_file", "write_file"],
                )

            def get_parameter_schema(self):
                return {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "depth": {"type": "string", "enum": ["quick", "standard", "deep"]}
                    },
                    "required": ["topic"]
                }
    """

    def __init__(self):
        super().__init__()
        self._cancelled = False
        self._progress_callbacks: List[callable] = []

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        """
        Execute the skill with progress streaming and cancellation support.

        This default implementation can be overridden, but most skills
        should override _build_graph() instead and use the default execute().
        """
        from .graph_builder import build_skill_graph
        from .models import SkillState

        # Validate parameters
        await self.validate_params(params)

        # Initialize skill state
        state = SkillState(
            input_params=params,
            steps_completed=[],
            tool_results={},
            final_output=None,
            errors=[],
        )

        # Build the LangGraph
        graph = self._build_graph()

        # Execute with progress streaming
        try:
            result = await self._execute_with_progress(graph, state)
            return result
        except Exception as e:
            logger.error(f"Skill {self.metadata.name} failed: {e}")
            raise

    @abstractmethod
    def _build_graph(self):
        """
        Build and return the LangGraph for this skill.

        Returns:
            StateGraph: LangGraph state graph

        Example:
            def _build_graph(self):
                return build_skill_graph(
                    name="my_skill",
                    nodes={
                        "step1": self._step1,
                        "step2": self._step2,
                    },
                    edges=[("step1", "step2")],
                    entry_point="step1"
                )
        """
        pass

    async def _execute_with_progress(self, graph, initial_state):
        """Execute graph with progress updates and cancellation checking"""
        from .models import SkillState

        state = initial_state

        # Execute the graph
        # Note: This is a simplified version - actual LangGraph integration
        # will use graph.ainvoke() or similar
        try:
            # For now, return a simple implementation
            # Real implementation will stream intermediate steps
            result_state = await graph.ainvoke(state)

            if result_state.final_output:
                return result_state.final_output
            else:
                # Fallback if no final_output set
                return OutputBlock(
                    type="text",
                    content=f"Skill {self.metadata.name} completed steps: {result_state.steps_completed}",
                    metadata={"steps": result_state.steps_completed}
                )
        except Exception as e:
            raise RuntimeError(f"Graph execution failed: {e}")

    def cancel(self):
        """Cancel skill execution"""
        self._cancelled = True
        logger.info(f"Skill {self.metadata.name} marked for cancellation")

    def is_cancelled(self) -> bool:
        """Check if skill was cancelled"""
        return self._cancelled

    def add_progress_callback(self, callback: callable):
        """Add a callback for progress updates"""
        self._progress_callbacks.append(callback)

    async def _notify_progress(self, step: str, data: Any = None):
        """Notify progress callbacks"""
        for callback in self._progress_callbacks:
            if asyncio.iscoroutinefunction(callback):
                await callback(step, data)
            else:
                callback(step, data)


class CapabilityRegistry:
    """
    Singleton registry for all capabilities (tools and skills).

    Provides:
    - Registration of tools and skills
    - Validation of metadata
    - Lookup by name, type, or tier
    - Ensures no name conflicts
    - Validates skill dependencies
    """

    _instance: Optional["CapabilityRegistry"] = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if CapabilityRegistry._initialized:
            return

        self._capabilities: Dict[str, Capability] = {}
        self._metadata: Dict[str, CapabilityMetadata] = {}
        CapabilityRegistry._initialized = True
        logger.info("CapabilityRegistry initialized")

    def register_tool(self, tool: ToolBase) -> None:
        """
        Register a simple tool.

        Args:
            tool: ToolBase instance to register

        Raises:
            ValueError: If tool name already exists or metadata is invalid
        """
        self._register_capability(tool)

    def register_skill(self, skill: SkillBase) -> None:
        """
        Register an agent skill.

        Args:
            skill: SkillBase instance to register

        Raises:
            ValueError: If skill name already exists, metadata is invalid,
                       or skill references unregistered tools
        """
        metadata = skill.metadata

        # Validate that tools used by this skill are registered
        if metadata.tools_used:
            unregistered_tools = [
                tool_name
                for tool_name in metadata.tools_used
                if tool_name not in self._capabilities
            ]
            if unregistered_tools:
                raise ValueError(
                    f"Skill '{metadata.name}' references unregistered tools: {unregistered_tools}. "
                    f"Register tools first, then skill."
                )

        self._register_capability(skill)
        logger.info(f"Registered skill: {metadata.name} (uses tools: {metadata.tools_used})")

    def _register_capability(self, capability: Capability) -> None:
        """Internal registration with validation"""
        metadata = capability.metadata

        # Validate metadata completeness
        if not metadata.name:
            raise ValueError("Capability must have a name")

        if not metadata.description:
            raise ValueError(f"Capability '{metadata.name}' must have a description")

        if not isinstance(metadata.tier, ActionTier):
            raise ValueError(f"Capability '{metadata.name}' must have a valid ActionTier")

        # Check for name conflicts
        if metadata.name in self._capabilities:
            raise ValueError(f"Capability '{metadata.name}' already registered")

        # Validate parameter schema
        try:
            schema = capability.get_parameter_schema()
            if not isinstance(schema, dict):
                raise ValueError("Parameter schema must be a dictionary")
        except Exception as e:
            raise ValueError(f"Invalid parameter schema for '{metadata.name}': {e}")

        # Register
        self._capabilities[metadata.name] = capability
        self._metadata[metadata.name] = metadata

        log_msg = f"Registered {metadata.type.value}: {metadata.name} (tier {metadata.tier.value})"
        logger.info(log_msg)

    def get_capability(self, name: str) -> Optional[Capability]:
        """Get capability by name"""
        return self._capabilities.get(name)

    def get_metadata(self, name: str) -> Optional[CapabilityMetadata]:
        """Get capability metadata by name"""
        return self._metadata.get(name)

    def list_capabilities(
        self,
        type: Optional[CapabilityType] = None,
        tier: Optional[ActionTier] = None,
    ) -> List[str]:
        """
        List capability names with optional filters.

        Args:
            type: Filter by capability type (tool/skill)
            tier: Filter by action tier

        Returns:
            List of capability names
        """
        names = list(self._capabilities.keys())

        if type:
            names = [
                name for name in names
                if self._metadata[name].type == type
            ]

        if tier:
            names = [
                name for name in names
                if self._metadata[name].tier == tier
            ]

        return names

    def find_by_tier(self, tier: ActionTier) -> List[str]:
        """Find all capabilities with a specific tier"""
        return self.list_capabilities(tier=tier)

    def get_all_for_llm(self) -> List[Dict[str, Any]]:
        """
        Get all capabilities formatted for LLM tool selection.

        Returns a list of tool descriptions in the format expected
        by Claude/Anthropic API.
        """
        tools = []
        for name, metadata in self._metadata.items():
            capability = self._capabilities[name]

            tool_def = {
                "name": name,
                "description": metadata.description,
                "input_schema": capability.get_parameter_schema(),
                "tier": metadata.tier.value,
                "type": metadata.type.value,
            }

            # Add metadata about permissions
            if metadata.allowed_paths:
                tool_def["allowed_paths"] = metadata.allowed_paths
            if metadata.requires_network:
                tool_def["requires_network"] = True
            if metadata.requires_oauth:
                tool_def["requires_oauth"] = metadata.requires_oauth

            tools.append(tool_def)

        return tools

    def validate_skill_tier(self, skill: SkillBase) -> ActionTier:
        """
        Validate and auto-calculate skill tier based on tools used.

        A skill's tier should be the highest tier of any tool it uses.
        This method validates that and returns the correct tier.

        Args:
            skill: Skill to validate

        Returns:
            The calculated tier (highest of tools used)

        Raises:
            ValueError: If skill tier is lower than tools used
        """
        metadata = skill.metadata

        if not metadata.tools_used:
            return metadata.tier

        # Find highest tier among tools used
        max_tier = metadata.tier
        for tool_name in metadata.tools_used:
            tool_meta = self.get_metadata(tool_name)
            if tool_meta:
                # Compare tiers (TIER_3 > TIER_2 > TIER_1)
                tier_order = [ActionTier.TIER_1, ActionTier.TIER_2, ActionTier.TIER_3]
                tool_tier_index = tier_order.index(tool_meta.tier)
                current_tier_index = tier_order.index(max_tier)

                if tool_tier_index > current_tier_index:
                    max_tier = tool_meta.tier

        # Validate declared tier matches calculated tier
        if max_tier != metadata.tier:
            raise ValueError(
                f"Skill '{metadata.name}' declares tier {metadata.tier.value} "
                f"but uses tools that require tier {max_tier.value}"
            )

        return max_tier


# Global singleton instance
capability_registry = CapabilityRegistry()
