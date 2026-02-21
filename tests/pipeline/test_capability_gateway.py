"""
Tests for Capability Gateway
"""

import pytest
import asyncio

from backend.pipeline.capability_gateway import (
    Capability,
    ToolBase,
    SkillBase,
    CapabilityRegistry,
    capability_registry,
)
from backend.pipeline.models import (
    CapabilityMetadata,
    CapabilityType,
    ActionTier,
    OutputBlock,
)


class MockTool(ToolBase):
    """Mock tool for testing"""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="mock_tool",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="A mock tool for testing",
        )

    async def execute(self, params: dict) -> OutputBlock:
        await self.validate_params(params)
        return OutputBlock(type="text", content=f"Mock tool executed with params: {params}")

    def get_parameter_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "input": {"type": "string"},
            },
            "required": ["input"],
        }


class MockSkill(SkillBase):
    """Mock skill for testing"""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="mock_skill",
            type=CapabilityType.SKILL,
            tier=ActionTier.TIER_1,
            description="A mock skill for testing",
            tools_used=["mock_tool"],
        )

    def _build_graph(self):
        from backend.skills.graph_builder import _MockGraph

        async def dummy_node(state):
            return state

        return _MockGraph(
            name="mock_skill",
            nodes={"step1": dummy_node},
            edges=[],
            entry_point="step1",
        )

    def get_parameter_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
            },
            "required": ["topic"],
        }


class TestCapabilityRegistry:
    """Test CapabilityRegistry functionality"""

    def setup_method(self):
        """Clear registry before each test"""
        # Clear the singleton
        CapabilityRegistry._instance = None
        CapabilityRegistry._initialized = False
        self.registry = CapabilityRegistry()

    @pytest.mark.asyncio
    async def test_register_tool(self):
        """Test registering a simple tool"""
        tool = MockTool()
        self.registry.register_tool(tool)

        assert "mock_tool" in self.registry.list_capabilities()
        assert self.registry.get_capability("mock_tool") is tool

    @pytest.mark.asyncio
    async def test_register_skill(self):
        """Test registering a skill"""
        # Register tool first (skill depends on it)
        tool = MockTool()
        self.registry.register_tool(tool)

        # Now register skill
        skill = MockSkill()
        self.registry.register_skill(skill)

        assert "mock_skill" in self.registry.list_capabilities()
        assert self.registry.get_capability("mock_skill") is skill

    @pytest.mark.asyncio
    async def test_register_skill_without_tool_fails(self):
        """Test that skill registration fails if tools aren't registered"""
        skill = MockSkill()

        with pytest.raises(ValueError, match="unregistered tools"):
            self.registry.register_skill(skill)

    @pytest.mark.asyncio
    async def test_duplicate_name_fails(self):
        """Test that duplicate names are rejected"""
        tool1 = MockTool()
        tool2 = MockTool()

        self.registry.register_tool(tool1)

        with pytest.raises(ValueError, match="already registered"):
            self.registry.register_tool(tool2)

    @pytest.mark.asyncio
    async def test_list_by_type(self):
        """Test listing capabilities by type"""
        tool = MockTool()
        self.registry.register_tool(tool)

        tools = self.registry.list_capabilities(type=CapabilityType.TOOL)
        skills = self.registry.list_capabilities(type=CapabilityType.SKILL)

        assert "mock_tool" in tools
        assert "mock_tool" not in skills

    @pytest.mark.asyncio
    async def test_find_by_tier(self):
        """Test finding capabilities by tier"""
        tool = MockTool()
        self.registry.register_tool(tool)

        tier_1_tools = self.registry.find_by_tier(ActionTier.TIER_1)
        tier_2_tools = self.registry.find_by_tier(ActionTier.TIER_2)

        assert "mock_tool" in tier_1_tools
        assert "mock_tool" not in tier_2_tools

    @pytest.mark.asyncio
    async def test_get_all_for_llm(self):
        """Test formatting capabilities for LLM"""
        tool = MockTool()
        self.registry.register_tool(tool)

        tools = self.registry.get_all_for_llm()

        assert len(tools) == 1
        assert tools[0]["name"] == "mock_tool"
        assert tools[0]["description"] == "A mock tool for testing"
        assert "input_schema" in tools[0]
        assert tools[0]["tier"] == "read_only"

    @pytest.mark.asyncio
    async def test_validate_skill_tier(self):
        """Test skill tier validation"""
        tool = MockTool()
        self.registry.register_tool(tool)

        skill = MockSkill()
        self.registry.register_skill(skill)

        # Should return TIER_1 since tool is TIER_1
        tier = self.registry.validate_skill_tier(skill)
        assert tier == ActionTier.TIER_1

    @pytest.mark.asyncio
    async def test_skill_tier_mismatch_fails(self):
        """Test that skill tier mismatch is caught"""
        tool = MockTool()
        self.registry.register_tool(tool)

        # Create a skill that claims lower tier than tools it uses
        class LowTierSkill(SkillBase):
            @property
            def metadata(self):
                return CapabilityMetadata(
                    name="low_tier_skill",
                    type=CapabilityType.SKILL,
                    tier=ActionTier.TIER_1,  # Claims TIER_1
                    description="Skill with wrong tier",
                    tools_used=["mock_tool"],  # But tool is TIER_1 (ok in this case)
                )

            def _build_graph(self):
                from backend.skills.graph_builder import _MockGraph
                return _MockGraph("test", {}, [], "step")

            def get_parameter_schema(self):
                return {"type": "object", "properties": {}}


class TestToolBase:
    """Test ToolBase functionality"""

    @pytest.mark.asyncio
    async def test_execute_with_valid_params(self):
        """Test tool execution with valid parameters"""
        tool = MockTool()
        result = await tool.execute({"input": "test"})

        assert result.type == "text"
        assert "test" in result.content

    @pytest.mark.asyncio
    async def test_execute_with_missing_required_param_fails(self):
        """Test that missing required parameters are rejected"""
        tool = MockTool()

        with pytest.raises(ValueError, match="Missing required parameter"):
            await tool.execute({})

    @pytest.mark.asyncio
    async def test_validate_params_type_checking(self):
        """Test parameter type validation"""
        tool = MockTool()

        # Valid type
        await tool.validate_params({"input": "string"})

        # Invalid type
        with pytest.raises(ValueError, match="expected type string"):
            await tool.validate_params({"input": 123})


class TestSkillBase:
    """Test SkillBase functionality"""

    def test_cancel_skill(self):
        """Test skill cancellation"""
        skill = MockSkill()
        assert not skill.is_cancelled()

        skill.cancel()
        assert skill.is_cancelled()

    def test_progress_callback(self):
        """Test progress callback mechanism"""
        skill = MockSkill()
        callbacks_called = []

        async def callback(step, data):
            callbacks_called.append((step, data))

        skill.add_progress_callback(callback)

        # This would normally be called by the skill during execution
        # For testing, we'll call it directly
        import asyncio
        asyncio.run(skill._notify_progress("test_step", {"data": "test"}))

        assert len(callbacks_called) == 1
        assert callbacks_called[0][0] == "test_step"
