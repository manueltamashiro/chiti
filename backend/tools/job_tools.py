"""
tools/job_tools.py — Scheduled job management tools for the LLM.

Registered in CapabilityRegistry:
  - job_list    (Tier 1)
  - job_create  (Tier 2)
  - job_update  (Tier 2)
  - job_delete  (Tier 3)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.pipeline.capability_gateway import ToolBase
from backend.pipeline.models import ActionTier, CapabilityMetadata, CapabilityType, OutputBlock


class JobListTool(ToolBase):
    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="job_list",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="List all scheduled jobs with their cron, last run time, and last result.",
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        from backend.scheduler.jobs import list_jobs

        jobs = await list_jobs()
        return OutputBlock(
            type="table",
            content=jobs,
            metadata={"columns": ["id", "name", "cron", "enabled", "last_run_at", "last_result"]},
        )


class JobCreateTool(ToolBase):
    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="job_create",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_2,
            description="Create a new scheduled job with a cron expression and task prompt.",
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable job name"},
                "cron": {
                    "type": "string",
                    "description": "Cron expression (5 fields: minute hour day month day_of_week), e.g. '0 9 * * 1'",
                },
                "task_prompt": {
                    "type": "string",
                    "description": "The prompt to run at each scheduled time",
                },
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of tool names the job is allowed to call",
                },
            },
            "required": ["name", "cron", "task_prompt"],
        }

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        from backend.scheduler.jobs import create_job
        from backend.scheduler.engine import add_cron_job
        from backend.scheduler.queue import enqueue

        job = await create_job(
            name=params["name"],
            cron=params["cron"],
            task_prompt=params["task_prompt"],
            allowed_tools=params.get("allowed_tools"),
        )

        # Register in APScheduler immediately
        job_id = job["id"]
        task_prompt = job["task_prompt"]

        async def _run_now():
            await enqueue("llm_task", {"job_id": job_id, "task_prompt": task_prompt})

        try:
            add_cron_job(_run_now, job_id=f"user_job_{job_id}", cron_expression=params["cron"])
        except Exception as exc:
            pass  # Scheduler may not be running in API process; worker will pick it up

        return OutputBlock(
            type="text",
            content=f"Job created: {job['name']} (ID: {job_id}, cron: {params['cron']})",
            metadata={"job": job},
        )


class JobUpdateTool(ToolBase):
    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="job_update",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_2,
            description="Update an existing scheduled job's cron, prompt, or enabled state.",
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Job ID to update"},
                "cron": {"type": "string", "description": "New cron expression (optional)"},
                "task_prompt": {"type": "string", "description": "New task prompt (optional)"},
                "allowed_tools": {"type": "array", "items": {"type": "string"}},
                "enabled": {"type": "boolean", "description": "Enable or disable the job"},
            },
            "required": ["id"],
        }

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        from backend.scheduler.jobs import update_job

        job = await update_job(
            job_id=params["id"],
            cron=params.get("cron"),
            task_prompt=params.get("task_prompt"),
            allowed_tools=params.get("allowed_tools"),
            enabled=params.get("enabled"),
        )
        if not job:
            raise ValueError(f"Job {params['id']} not found")

        return OutputBlock(
            type="text",
            content=f"Job updated: {job['name']}",
            metadata={"job": job},
        )


class JobDeleteTool(ToolBase):
    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="job_delete",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description="Permanently delete a scheduled job.",
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Job ID to delete"},
            },
            "required": ["id"],
        }

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        from backend.scheduler.jobs import delete_job
        from backend.scheduler.engine import remove_job

        job_id = params["id"]
        deleted = await delete_job(job_id)
        remove_job(f"user_job_{job_id}")

        if not deleted:
            raise ValueError(f"Job {job_id} not found")

        return OutputBlock(
            type="text",
            content=f"Job {job_id} deleted",
            metadata={"job_id": job_id},
        )
