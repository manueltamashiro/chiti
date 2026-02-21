"""
LangGraph helpers for building agent skills

This module provides utilities to construct LangGraph StateGraph
instances for multi-step agent skills.
"""

from typing import Dict, Callable, Any, List, Tuple, Optional
from dataclasses import dataclass
import logging

try:
    from langgraph.graph import StateGraph, END
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    StateGraph = None
    END = None

from backend.pipeline.models import SkillState, OutputBlock

logger = logging.getLogger(__name__)


def build_skill_graph(
    name: str,
    nodes: Dict[str, callable],
    edges: List[Tuple[str, str]],
    entry_point: str,
) -> "StateGraph":
    """
    Build a LangGraph StateGraph for a skill.

    Args:
        name: Name of the skill/graph
        nodes: Dictionary of node names to async functions
        edges: List of (from_node, to_node) tuples defining edges
        entry_point: Name of the entry node

    Returns:
        StateGraph: LangGraph state graph

    Example:
        graph = build_skill_graph(
            name="research",
            nodes={
                "search": search_node,
                "analyze": analyze_node,
                "report": report_node,
            },
            edges=[
                ("search", "analyze"),
                ("analyze", "report"),
            ],
            entry_point="search"
        )
    """
    if not LANGGRAPH_AVAILABLE:
        logger.warning("LangGraph not available, returning mock graph")
        return _MockGraph(name, nodes, edges, entry_point)

    # Create state graph with SkillState
    graph = StateGraph(SkillState)

    # Add nodes
    for node_name, node_func in nodes.items():
        graph.add_node(node_name, node_func)

    # Set entry point
    graph.set_entry_point(entry_point)

    # Add edges
    for from_node, to_node in edges:
        if to_node == "__end__":
            graph.add_edge(from_node, END)
        else:
            graph.add_edge(from_node, to_node)

    # Compile the graph
    compiled = graph.compile()

    logger.info(f"Built skill graph: {name} with {len(nodes)} nodes")
    return compiled


async def _wrap_node_function(node_func: callable, state: SkillState) -> SkillState:
    """
    Wrap a node function to handle errors and logging.

    Args:
        node_func: Async function that takes SkillState and returns SkillState
        state: Current skill state

    Returns:
        Updated SkillState
    """
    step_name = node_func.__name__

    try:
        logger.info(f"Executing skill step: {step_name}")
        state.current_step = step_name

        # Execute the node
        result = await node_func(state)

        # Track completion
        if step_name not in result.steps_completed:
            result.steps_completed.append(step_name)

        return result

    except Exception as e:
        logger.error(f"Skill step '{step_name}' failed: {e}")
        state.errors.append(f"{step_name}: {str(e)}")
        raise


class _MockGraph:
    """
    Mock graph implementation when LangGraph is not available.

    This allows skills to be tested without LangGraph installed.
    """

    def __init__(self, name: str, nodes: Dict[str, callable], edges: List[Tuple[str, str]], entry_point: str):
        self.name = name
        self.nodes = nodes
        self.edges = edges
        self.entry_point = entry_point
        self._build_execution_order()

    def _build_execution_order(self):
        """Build linear execution order from edges"""
        order = []
        current = self.entry_point

        # Simple linear traversal
        edge_map = {frm: to for frm, to in self.edges}
        visited = set()

        while current and current not in visited:
            order.append(current)
            visited.add(current)
            current = edge_map.get(current)

        self.execution_order = order

    async def ainvoke(self, state: SkillState) -> SkillState:
        """Execute graph in order (mock implementation)"""
        logger.info(f"Executing mock graph: {self.name}")

        for node_name in self.execution_order:
            if node_name in self.nodes:
                state = await _wrap_node_function(self.nodes[node_name], state)
            else:
                logger.warning(f"Node '{node_name}' not found in graph")

        return state


# Helper functions for common skill patterns


async def call_tool_through_gateway(tool_name: str, params: Dict[str, Any]) -> Any:
    """
    Call a tool through the capability gateway.

    This is a convenience function for skills that need to call other tools.

    Args:
        tool_name: Name of the tool to call
        params: Parameters for the tool

    Returns:
        OutputBlock from tool execution

    Raises:
        ValueError: If tool is not registered
        RuntimeError: If tool execution fails
    """
    from backend.pipeline.capability_gateway import capability_registry

    capability = capability_registry.get_capability(tool_name)
    if not capability:
        raise ValueError(f"Tool '{tool_name}' not registered")

    try:
        result = await capability.execute(params)
        return result
    except Exception as e:
        raise RuntimeError(f"Tool '{tool_name}' execution failed: {e}")


def create_conditional_edge(condition_func: callable) -> callable:
    """
    Create a conditional edge function for LangGraph.

    Args:
        condition_func: Function that takes SkillState and returns next node name

    Returns:
        Function suitable for use with LangGraph conditional edges

    Example:
        def should_continue(state: SkillState) -> str:
            if state.tool_results.get("found_items"):
                return "analyze"
            return "end"

        graph.add_conditional_edges(
            "search",
            create_conditional_edge(should_continue),
            {"analyze": "analyze", "end": END}
        )
    """
    if not LANGGRAPH_AVAILABLE:
        return condition_func

    # LangGraph conditional edges should return the next node name directly
    return condition_func


# Skill builder for common patterns


class SkillBuilder:
    """
    Builder class for constructing skills with common patterns.

    Example:
        skill = (
            SkillBuilder("email_triage")
            .step("fetch_emails", fetch_emails_node)
            .step("categorize", categorize_node)
            .step("summarize", summarize_node)
            .build()
        )
    """

    def __init__(self, name: str):
        self.name = name
        self.nodes: Dict[str, callable] = {}
        self.edges: List[Tuple[str, str]] = []
        self.entry_point: Optional[str] = None
        self.last_node: Optional[str] = None

    def step(self, name: str, func: callable) -> "SkillBuilder":
        """Add a step to the skill"""
        self.nodes[name] = func

        if self.entry_point is None:
            self.entry_point = name
        elif self.last_node:
            self.edges.append((self.last_node, name))

        self.last_node = name
        return self

    def conditional_edge(self, from_node: str, condition: callable, branches: Dict[str, str]) -> "SkillBuilder":
        """Add a conditional edge (requires LangGraph)"""
        # This is a placeholder - full implementation requires LangGraph
        logger.warning(f"Conditional edge from {from_node} - LangGraph recommended")
        return self

    def build(self) -> StateGraph:
        """Build the skill graph"""
        if not self.entry_point:
            raise ValueError(f"Skill '{self.name}' has no steps")

        return build_skill_graph(
            name=self.name,
            nodes=self.nodes,
            edges=self.edges,
            entry_point=self.entry_point,
        )
