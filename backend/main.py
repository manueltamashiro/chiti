"""
main.py — FastAPI application entry point (assistant-api process).

Bound to 127.0.0.1:8000 only. Browser never contacts this port directly;
all traffic proxies through the Next.js server on port 3000.

Endpoints:
    GET  /health
    GET  /ws                          ← WebSocket (token query param)
    POST /api/chat                    ← SSE streaming chat
    GET  /api/history                 ← List conversations
    GET  /api/history/{id}            ← Get messages for conversation
    POST /api/tool/approve/{id}       ← Approve a pending Tier 2/3 tool call
    POST /api/tool/deny/{id}          ← Deny a pending Tier 2/3 tool call
    GET  /api/ws/token                ← Issue a signed WS session token
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.config.loader import cfg
from backend.config.logging import (
    correlation_id_var,
    get_logger,
    new_correlation_id,
    setup_logging,
)
from backend.llm.claude import ClaudeClient, build_action_confirm_block
from backend.memory.conversation import (
    add_message,
    create_conversation,
    get_conversation,
    get_messages,
    get_recent_messages_for_llm,
    init_db,
    list_conversations,
)
from backend.middleware.security import SecurityHeadersMiddleware
from backend.pipeline.action_classifier import ActionClassifier, ToolCall
from backend.pipeline.capability_gateway import capability_registry
from backend.pipeline.models import ActionTier, OutputBlock
from backend.pipeline.pipeline import ToolResultPipeline, pipeline
from backend.ws.hub import ConnectionManager, ws_hub

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

setup_logging(level=cfg.logging.level, fmt=cfg.logging.format)
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Pending tool confirmations store (in-memory; keyed by tool_call_id)
# ---------------------------------------------------------------------------

_pending_approvals: Dict[str, asyncio.Event] = {}
_approval_results: Dict[str, bool] = {}  # True = approved, False = denied


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting assistant-api", extra={"host": "127.0.0.1", "port": 8000})
    await init_db()

    # Phase 2: memory & audit DBs
    try:
        from backend.memory.structured import init_structured_db
        from backend.memory.audit import init_audit_db
        from backend.scheduler.queue import init_queue_db
        from backend.scheduler.jobs import init_jobs_db
        from backend.proactive.notifications import init_notifications_db
        from backend.proactive.push import init_push_db
        await init_structured_db()
        await init_audit_db()
        await init_queue_db()
        await init_jobs_db()
        await init_notifications_db()
        await init_push_db()
    except Exception as exc:
        logger.warning("Phase 2/3 DB init error (non-fatal)", extra={"error": str(exc)})

    _register_tools()

    # P4-11/P4-12: Start filesystem watcher and wire to WebSocket events
    _start_filesystem_observer()

    yield
    logger.info("Shutting down assistant-api")


def _register_tools() -> None:
    """Register all tools into the CapabilityRegistry at startup (P4-03/P4-04/P4-05)."""
    # fmt: off
    from backend.tools.filesystem import (
        ReadFileTool, ListDirectoryTool, FindFilesTool, DiskUsageTool,
        WriteFileTool, AppendFileTool, CreateDirectoryTool,
        DeleteFileTool, MoveFileTool,
    )
    from backend.tools.system import (
        SystemStatsTool, ListProcessesTool, GetProcessInfoTool,
        CheckServiceTool, GetLogsTool, CheckPortTool,
        StartServiceTool, StopServiceTool, KillProcessTool,
    )
    from backend.tools.network import (
        PingTool, DnsLookupTool, CheckUrlTool, HttpGetTool,
    )
    from backend.tools.git import (
        GitStatusTool, GitLogTool, GitDiffTool,
        GitAddTool, GitCommitTool, GitCheckoutTool,
        GitPushTool, GitCreateBranchTool,
    )
    from backend.tools.docker import (
        DockerPsTool, DockerStatsTool, DockerLogsTool,
        DockerRestartTool, DockerStartTool, DockerStopTool,
    )
    from backend.tools.process import RunPythonTool, RunNodeTool, TrustScriptTool
    from backend.tools.memory_tools import MemorySearchTool, MemoryWriteTool, MemoryDeleteTool
    from backend.tools.job_tools import JobListTool, JobCreateTool, JobUpdateTool, JobDeleteTool
    from backend.tools.log_tail import LogTailTool
    from backend.tools.uptime_monitor import UptimeCheckTool
    # fmt: on

    tools = [
        # ── Tier 1 — read-only (no confirmation) ──────────────────────────
        ReadFileTool(),
        ListDirectoryTool(),
        FindFilesTool(),
        DiskUsageTool(),
        SystemStatsTool(),
        ListProcessesTool(),
        GetProcessInfoTool(),
        CheckServiceTool(),
        GetLogsTool(),
        CheckPortTool(),
        PingTool(),
        DnsLookupTool(),
        CheckUrlTool(),
        GitStatusTool(),
        GitLogTool(),
        GitDiffTool(),
        DockerPsTool(),
        DockerStatsTool(),
        DockerLogsTool(),
        LogTailTool(),
        UptimeCheckTool(),
        MemorySearchTool(),
        JobListTool(),
        # ── Tier 2 — reversible write (soft confirmation) ─────────────────
        WriteFileTool(),
        AppendFileTool(),
        CreateDirectoryTool(),
        RunPythonTool(),
        RunNodeTool(),
        GitAddTool(),
        GitCommitTool(),
        GitCheckoutTool(),
        DockerRestartTool(),
        HttpGetTool(),
        MemoryWriteTool(),
        JobCreateTool(),
        JobUpdateTool(),
        # ── Tier 3 — high-impact (explicit confirmation) ──────────────────
        DeleteFileTool(),
        MoveFileTool(),
        GitPushTool(),
        GitCreateBranchTool(),
        DockerStartTool(),
        DockerStopTool(),
        StartServiceTool(),
        StopServiceTool(),
        KillProcessTool(),
        TrustScriptTool(),
        MemoryDeleteTool(),
        JobDeleteTool(),
    ]

    for tool in tools:
        try:
            capability_registry.register_tool(tool)
        except Exception as exc:
            logger.warning(f"Could not register tool {tool}: {exc}")

    logger.info("Tools registered", extra={"count": len(list(capability_registry.list_capabilities()))})


def _start_filesystem_observer() -> None:
    """
    P4-11/P4-12: Start the filesystem watcher for directories listed in
    ``config.yml → filesystem.watched_dirs``.

    On each change, broadcasts a ``file.changed`` WebSocket event to all
    connected clients.
    """
    watched = cfg.filesystem.watched_dirs
    if not watched:
        logger.debug("No watched_dirs configured — filesystem observer not started")
        return

    try:
        from backend.proactive.observers import filesystem_observer, FileChangeEvent

        async def _on_file_change(event: FileChangeEvent) -> None:
            payload = {
                "path": event.path,
                "event_type": event.event_type,
                "is_directory": event.is_directory,
                "timestamp": event.timestamp.isoformat(),
            }
            if event.dest_path:
                payload["dest_path"] = event.dest_path
            await ws_hub.broadcast("file.changed", payload)

        for directory in watched:
            filesystem_observer.watch(directory, _on_file_change)

        filesystem_observer.start()
        logger.info(
            "Filesystem observer started",
            extra={"watched_dirs": watched},
        )
    except Exception as exc:
        logger.warning(
            "Filesystem observer failed to start (non-fatal)",
            extra={"error": str(exc)},
        )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Chiti Assistant API",
    version="0.1.0",
    docs_url=None,   # Disable Swagger UI in production
    redoc_url=None,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Wire lifespan
app.router.lifespan_context = lifespan


# ---------------------------------------------------------------------------
# Correlation ID middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    cid = request.headers.get("X-Correlation-ID") or new_correlation_id()
    token = correlation_id_var.set(cid)
    try:
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        return response
    finally:
        correlation_id_var.reset(token)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# WebSocket token issuer
# ---------------------------------------------------------------------------

@app.get("/api/ws/token")
async def issue_ws_token():
    """Issue a signed WebSocket session token (valid 15 min)."""
    token = ConnectionManager.create_token(
        secret=cfg.security.ws_secret,
        ttl_minutes=cfg.security.ws_token_ttl_minutes,
    )
    return {"token": token}


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(..., description="Signed WS session token"),
):
    client_id = await ws_hub.connect(websocket, token, cfg.security.ws_secret)
    if not client_id:
        return  # already closed with 4001

    try:
        while True:
            data = await websocket.receive_text()
            # Clients can send keepalive pings
            if data == "ping":
                await ws_hub.send_to_client(client_id, "pong", {})
    except WebSocketDisconnect:
        ws_hub.disconnect(client_id)


# ---------------------------------------------------------------------------
# Chat request/response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


def _block_to_dict(block: OutputBlock) -> Dict[str, Any]:
    return {"type": block.type, "content": block.content, "metadata": block.metadata}


# ---------------------------------------------------------------------------
# POST /api/chat  (SSE streaming)
# ---------------------------------------------------------------------------

@app.post("/api/chat")
async def chat(request: ChatRequest):
    """
    Stream a Claude response as Server-Sent Events.

    Each SSE data line is a JSON-encoded OutputBlock.
    """
    conv_id = request.conversation_id or str(uuid.uuid4())

    # Ensure conversation exists
    if not await get_conversation(conv_id):
        await create_conversation()

    # Persist user message
    await add_message(conv_id, "user", request.message)

    # Load conversation history for context
    history = await get_recent_messages_for_llm(conv_id, limit=20)

    # Inject memory context into system prompt (Phase 2)
    memory_context = ""
    try:
        from backend.memory.injector import build_context
        memory_context = await build_context(request.message)
    except Exception as exc:
        logger.debug("Memory context unavailable", extra={"error": str(exc)})

    # Get registered tools for the LLM
    llm_tools = capability_registry.get_all_for_llm()

    client = ClaudeClient()
    classifier = ActionClassifier(
        trusted_scripts_dir=cfg.terminal.scripts.trusted_scripts_dir
    )
    tool_pipeline = pipeline  # singleton

    async def event_stream() -> AsyncIterator[str]:
        assistant_blocks: List[Dict[str, Any]] = []
        assistant_text_parts: List[str] = []

        async for block in client.stream_response(
            history,
            system_context=memory_context or None,
            tools=llm_tools if llm_tools else None,
        ):
            if block.type == "tool_call":
                # Classify the tool call
                tool_name = block.content.get("name", "")
                params = block.content.get("input", {})
                tool_call_id = block.content.get("id", str(uuid.uuid4()))

                classification = classifier.classify(
                    ToolCall(tool_name=tool_name, params=params)
                )

                if classification.requires_confirmation:
                    # Emit action_confirm block and wait for user response
                    confirm_block = build_action_confirm_block(
                        tool_name=tool_name,
                        params=params,
                        tier=classification.tier,
                        justification=classification.justification,
                        tool_call_id=tool_call_id,
                    )
                    yield f"data: {json.dumps(_block_to_dict(confirm_block))}\n\n"
                    assistant_blocks.append(_block_to_dict(confirm_block))

                    # Wait for user approval (up to 5 minutes)
                    event = asyncio.Event()
                    _pending_approvals[tool_call_id] = event
                    try:
                        await asyncio.wait_for(event.wait(), timeout=300)
                        approved = _approval_results.pop(tool_call_id, False)
                    except asyncio.TimeoutError:
                        approved = False
                    finally:
                        _pending_approvals.pop(tool_call_id, None)

                    if not approved:
                        denied_block = OutputBlock(
                            type="text",
                            content=f"⚠️ Tool call `{tool_name}` was denied.",
                        )
                        yield f"data: {json.dumps(_block_to_dict(denied_block))}\n\n"
                        continue

                # Execute tool through capability gateway
                try:
                    cap = capability_registry.get_capability(tool_name)
                    raw_result = await cap.execute(params)
                except Exception as exc:
                    logger.warning(f"Tool execution failed: {tool_name}: {exc}")
                    raw_result = OutputBlock(type="text", content=f"Tool error: {exc}")

                yield f"data: {json.dumps(_block_to_dict(raw_result))}\n\n"
                assistant_blocks.append(_block_to_dict(raw_result))

            else:
                # Regular output block
                d = _block_to_dict(block)
                yield f"data: {json.dumps(d)}\n\n"
                assistant_blocks.append(d)
                if block.type == "text":
                    assistant_text_parts.append(str(block.content))

        # Persist assistant response
        full_text = " ".join(assistant_text_parts)
        await add_message(conv_id, "assistant", full_text, blocks=assistant_blocks)

        # Signal stream end
        yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv_id})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Conversation-ID": conv_id,
        },
    )


# ---------------------------------------------------------------------------
# Tool approval endpoints
# ---------------------------------------------------------------------------

@app.post("/api/tool/approve/{tool_call_id}")
async def approve_tool(tool_call_id: str):
    """Approve a pending Tier 2/3 tool call."""
    event = _pending_approvals.get(tool_call_id)
    if not event:
        raise HTTPException(status_code=404, detail="No pending tool call with that ID")
    _approval_results[tool_call_id] = True
    event.set()
    return {"status": "approved"}


@app.post("/api/tool/deny/{tool_call_id}")
async def deny_tool(tool_call_id: str):
    """Deny a pending Tier 2/3 tool call."""
    event = _pending_approvals.get(tool_call_id)
    if not event:
        raise HTTPException(status_code=404, detail="No pending tool call with that ID")
    _approval_results[tool_call_id] = False
    event.set()
    return {"status": "denied"}


# ---------------------------------------------------------------------------
# History endpoints
# ---------------------------------------------------------------------------

@app.get("/api/history")
async def history_list(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    convs = await list_conversations(limit=limit, offset=offset)
    return {"conversations": convs, "limit": limit, "offset": offset}


@app.get("/api/history/{conversation_id}")
async def history_detail(
    conversation_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    conv = await get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = await get_messages(conversation_id, limit=limit, offset=offset)
    return {"conversation": conv, "messages": msgs}


# ---------------------------------------------------------------------------
# Memory endpoints (Phase 2)
# ---------------------------------------------------------------------------

@app.get("/api/memory/facts")
async def memory_facts(limit: int = Query(100, ge=1, le=500)):
    from backend.memory.structured import list_facts
    return {"facts": await list_facts(limit=limit)}


@app.get("/api/memory/preferences")
async def memory_preferences():
    from backend.memory.structured import list_preferences
    return {"preferences": await list_preferences()}


@app.get("/api/memory/people")
async def memory_people():
    from backend.memory.structured import list_people
    return {"people": await list_people()}


@app.get("/api/memory/episodic")
async def memory_episodic(limit: int = Query(50, ge=1, le=200)):
    from backend.memory.episodic import list_summaries
    return {"summaries": await list_summaries(limit=limit)}


@app.delete("/api/memory/{mem_type}/{mem_id}")
async def memory_delete(mem_type: str, mem_id: str):
    from backend.memory import structured, episodic
    if mem_type == "fact":
        await structured.delete_fact(mem_id)
    elif mem_type == "preference":
        await structured.delete_preference(mem_id)
    elif mem_type == "person":
        await structured.delete_person(mem_id)
    elif mem_type == "event":
        await structured.delete_event(mem_id)
    elif mem_type == "episodic":
        await episodic.delete_summary(mem_id)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown memory type: {mem_type}")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Notification endpoints (Phase 3)
# ---------------------------------------------------------------------------

@app.get("/api/notifications")
async def notifications_list(
    limit: int = Query(50, ge=1, le=200),
    include_dismissed: bool = Query(False),
):
    from backend.proactive.notifications import list_notifications, get_unread_count
    items = await list_notifications(limit=limit, include_dismissed=include_dismissed)
    unread = await get_unread_count()
    return {"notifications": items, "unread_count": unread}


@app.post("/api/notifications/{notification_id}/dismiss")
async def notification_dismiss(notification_id: str):
    from backend.proactive.notifications import dismiss
    await dismiss(notification_id)
    return {"status": "dismissed"}


@app.post("/api/notifications/{notification_id}/snooze")
async def notification_snooze(notification_id: str, until: str):
    from backend.proactive.notifications import snooze
    await snooze(notification_id, until)
    return {"status": "snoozed", "until": until}


# ---------------------------------------------------------------------------
# Push subscription endpoints (Phase 3)
# ---------------------------------------------------------------------------

class PushSubscriptionRequest(BaseModel):
    endpoint: str
    auth: str
    p256dh: str


@app.post("/api/push/subscribe")
async def push_subscribe(req: PushSubscriptionRequest):
    from backend.proactive.push import save_subscription
    sid = await save_subscription(req.endpoint, req.auth, req.p256dh)
    return {"status": "subscribed", "id": sid}


@app.delete("/api/push/subscribe")
async def push_unsubscribe(endpoint: str):
    from backend.proactive.push import delete_subscription
    await delete_subscription(endpoint)
    return {"status": "unsubscribed"}


# ---------------------------------------------------------------------------
# Scheduled jobs endpoints (Phase 3)
# ---------------------------------------------------------------------------

@app.get("/api/jobs")
async def jobs_list():
    from backend.scheduler.jobs import list_jobs
    return {"jobs": await list_jobs()}


@app.get("/api/jobs/queue")
async def job_queue_list(limit: int = Query(50, ge=1, le=200)):
    from backend.scheduler.queue import list_jobs as list_queue_jobs
    return {"jobs": await list_queue_jobs(limit=limit)}


# ---------------------------------------------------------------------------
# Audit log endpoints (Phase 4)
# ---------------------------------------------------------------------------

@app.get("/api/audit")
async def audit_list(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    tool_name: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    from backend.memory.audit import list_audit_logs
    logs = await list_audit_logs(
        limit=limit, offset=offset, tool_name=tool_name,
        tier=tier, date_from=date_from, date_to=date_to,
    )
    return {"logs": logs, "limit": limit, "offset": offset}


@app.get("/api/audit/{entry_id}")
async def audit_detail(entry_id: str):
    from backend.memory.audit import get_audit_entry
    entry = await get_audit_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Audit entry not found")
    return entry


# ---------------------------------------------------------------------------
# Summarize conversation endpoint (Phase 2)
# ---------------------------------------------------------------------------

@app.post("/api/history/{conversation_id}/summarize")
async def summarize_conversation_endpoint(conversation_id: str):
    """Trigger summarization of a completed conversation."""
    conv = await get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = await get_recent_messages_for_llm(conversation_id, limit=50)
    from backend.memory.summarizer import summarize_conversation
    summary = await summarize_conversation(conversation_id, msgs)
    return {"conversation_id": conversation_id, "summary": summary}
