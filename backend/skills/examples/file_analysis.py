"""
File Analysis Skill - Example multi-step agent skill

This skill demonstrates a simple workflow:
1. List files in a directory
2. Read interesting files
3. Analyze content and generate summary

This is a reference implementation for building more complex skills.
"""

import logging
from typing import Dict, Any

from backend.skills.base import SkillBase
from backend.skills.graph_builder import build_skill_graph
from backend.pipeline.models import (
    CapabilityMetadata,
    CapabilityType,
    ActionTier,
    OutputBlock,
    SkillState,
)

logger = logging.getLogger(__name__)


class FileAnalysisSkill(SkillBase):
    """
    Analyze files in a directory and generate a summary.

    This skill demonstrates:
    - Multi-step workflow with LangGraph
    - Calling other tools from within a skill
    - Aggregating results across multiple tool calls
    - Progress streaming
    """

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="file_analysis",
            type=CapabilityType.SKILL,
            tier=ActionTier.TIER_1,  # Read-only
            description="Analyze files in a directory and generate a summary",
            allowed_paths=["local://~/Documents/**", "local://~/projects/**"],
            tools_used=["list_directory", "read_file"],  # Tools this skill calls
            max_runtime_seconds=300,  # 5 minutes
        )

    def _build_graph(self):
        """Build the LangGraph for file analysis"""
        return build_skill_graph(
            name="file_analysis",
            nodes={
                "list_files": self._list_files_node,
                "read_files": self._read_files_node,
                "analyze": self._analyze_node,
                "generate_report": self._generate_report_node,
            },
            edges=[
                ("list_files", "read_files"),
                ("read_files", "analyze"),
                ("analyze", "generate_report"),
            ],
            entry_point="list_files",
        )

    async def _list_files_node(self, state: SkillState) -> SkillState:
        """Step 1: List files in the directory"""
        await self._notify_progress("list_files", "Listing files...")

        directory = state.input_params.get("directory")
        if not directory:
            state.errors.append("directory parameter is required")
            return state

        try:
            # TODO: Call list_directory tool through capability gateway
            # For now, mock the result
            files = [
                {"name": "report.pdf", "size": 1024000, "type": "pdf"},
                {"name": "data.json", "size": 4096, "type": "json"},
                {"name": "notes.txt", "size": 2048, "type": "text"},
            ]

            state.tool_results["files"] = files
            state.tool_results["total_files"] = len(files)
            logger.info(f"Found {len(files)} files in {directory}")

        except Exception as e:
            state.errors.append(f"Failed to list files: {e}")
            logger.error(f"List files failed: {e}")

        return state

    async def _read_files_node(self, state: SkillState) -> SkillState:
        """Step 2: Read interesting files"""
        await self._notify_progress("read_files", "Reading files...")

        # Check for errors from previous step
        if state.errors:
            return state

        files = state.tool_results.get("files", [])
        max_files = state.input_params.get("max_files", 5)

        # Read first N files (in real skill, would filter by type/size)
        files_to_read = files[:max_files]
        state.tool_results["read_files"] = []

        for file_info in files_to_read:
            # TODO: Call read_file tool through capability gateway
            # For now, mock the result
            content = f"Mock content from {file_info['name']}"

            state.tool_results["read_files"].append({
                "name": file_info["name"],
                "content": content,
                "size": file_info["size"],
            })

            await self._notify_progress("read_files", f"Read {file_info['name']}")

        logger.info(f"Read {len(state.tool_results['read_files'])} files")
        return state

    async def _analyze_node(self, state: SkillState) -> SkillState:
        """Step 3: Analyze file contents"""
        await self._notify_progress("analyze", "Analyzing content...")

        if state.errors:
            return state

        read_files = state.tool_results.get("read_files", [])

        # Simple analysis (in real skill, would use LLM)
        analysis = {
            "total_files_analyzed": len(read_files),
            "total_size": sum(f["size"] for f in read_files),
            "file_types": {},
            "summary": "Analysis complete",
        }

        # Count file types
        for file_info in state.tool_results.get("files", []):
            file_type = file_info.get("type", "unknown")
            analysis["file_types"][file_type] = analysis["file_types"].get(file_type, 0) + 1

        state.tool_results["analysis"] = analysis
        logger.info(f"Analysis complete: {analysis}")
        return state

    async def _generate_report_node(self, state: SkillState) -> SkillState:
        """Step 4: Generate final report"""
        await self._notify_progress("generate_report", "Generating report...")

        if state.errors:
            # Error report
            state.final_output = OutputBlock(
                type="notification",
                content={
                    "level": "error",
                    "title": "File Analysis Failed",
                    "message": f"Errors: {'; '.join(state.errors)}",
                },
                metadata={"errors": state.errors}
            )
            return state

        analysis = state.tool_results.get("analysis", {})

        # Generate markdown report
        report = f"""# File Analysis Report

**Total Files Found:** {state.tool_results.get('total_files', 0)}
**Files Analyzed:** {analysis.get('total_files_analyzed', 0)}
**Total Size:** {analysis.get('total_size', 0):,} bytes

## File Types

"""

        for file_type, count in analysis.get("file_types", {}).items():
            report += f"- **{file_type}**: {count}\n"

        report += f"\n## Summary\n\n{analysis.get('summary', 'No summary available')}\n"

        state.final_output = OutputBlock(
            type="text",
            content=report,
            metadata={
                "steps_completed": state.steps_completed,
                "analysis": analysis,
            }
        )

        logger.info("Report generated")
        return state

    def get_parameter_schema(self) -> Dict[str, Any]:
        """Return JSON schema for parameters"""
        return {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Directory path to analyze (e.g., local://~/Documents/reports)"
                },
                "max_files": {
                    "type": "integer",
                    "description": "Maximum number of files to read and analyze",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 50,
                }
            },
            "required": ["directory"]
        }
