"""
tools/memory_tools.py — Memory tools for the LLM to read/write/delete memories.

Registered in CapabilityRegistry:
  - memory_search  (Tier 1 — read-only)
  - memory_write   (Tier 2 — reversible write)
  - memory_delete  (Tier 3 — explicit confirmation)
"""

from __future__ import annotations

from typing import Any, Dict

from backend.pipeline.capability_gateway import ToolBase
from backend.pipeline.models import ActionTier, CapabilityMetadata, CapabilityType, OutputBlock


class MemorySearchTool(ToolBase):
    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="memory_search",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Search past memories semantically. Returns relevant facts, preferences, people, and episodic memories.",
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "top_k": {"type": "integer", "description": "Number of results (default 5)", "default": 5},
            },
            "required": ["query"],
        }

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        from backend.memory import episodic, structured

        query = params["query"]
        top_k = int(params.get("top_k", 5))

        memories = await episodic.search(query, top_k=top_k)
        facts = await structured.list_facts(limit=20)
        prefs = await structured.list_preferences()
        people = await structured.list_people()

        content = {
            "episodic_memories": memories,
            "facts": facts[:10],
            "preferences": prefs[:10],
            "people": people[:10],
        }
        return OutputBlock(type="text", content=str(content), metadata={"query": query})


class MemoryWriteTool(ToolBase):
    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="memory_write",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_2,
            description="Write a fact, preference, person, or event to memory.",
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["fact", "preference", "person", "event"],
                    "description": "Type of memory to write",
                },
                "data": {
                    "type": "object",
                    "description": "Data dict. fact: {subject, predicate, object, confidence}. preference: {key, value}. person: {name, relationship, notes}. event: {title, date, description, recurring}",
                },
            },
            "required": ["type", "data"],
        }

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        from backend.memory import structured

        mem_type = params["type"]
        data = params["data"]

        if mem_type == "fact":
            result = await structured.upsert_fact(
                subject=data["subject"],
                predicate=data["predicate"],
                obj=data["object"],
                confidence=float(data.get("confidence", 0.8)),
            )
        elif mem_type == "preference":
            result = await structured.upsert_preference(
                key=data["key"],
                value=data["value"],
            )
        elif mem_type == "person":
            result = await structured.upsert_person(
                name=data["name"],
                relationship=data.get("relationship", ""),
                notes=data.get("notes", ""),
            )
        elif mem_type == "event":
            result = await structured.create_event(
                title=data["title"],
                date=data["date"],
                description=data.get("description", ""),
                recurring=bool(data.get("recurring", False)),
            )
        else:
            raise ValueError(f"Unknown memory type: {mem_type}")

        return OutputBlock(
            type="text",
            content=f"Memory written: {mem_type} — {result}",
            metadata={"type": mem_type, "id": result.get("id", "")},
        )


class MemoryDeleteTool(ToolBase):
    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="memory_delete",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description="Permanently delete a memory entry by type and ID.",
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["fact", "preference", "person", "event", "episodic"],
                },
                "id": {"type": "string", "description": "ID of the memory entry to delete"},
            },
            "required": ["type", "id"],
        }

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        from backend.memory import structured, episodic

        mem_type = params["type"]
        mid = params["id"]

        if mem_type == "fact":
            await structured.delete_fact(mid)
        elif mem_type == "preference":
            await structured.delete_preference(mid)
        elif mem_type == "person":
            await structured.delete_person(mid)
        elif mem_type == "event":
            await structured.delete_event(mid)
        elif mem_type == "episodic":
            await episodic.delete_summary(mid)
        else:
            raise ValueError(f"Unknown memory type: {mem_type}")

        return OutputBlock(
            type="text",
            content=f"Memory deleted: {mem_type}/{mid}",
            metadata={"type": mem_type, "id": mid},
        )
