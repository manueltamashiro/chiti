# Task 2: Backend + Frontend Foundation (Phases 0–4)

> **Priority**: HIGH — Core runtime that turns the security/tool layer into a usable product
> **Depends on**: task.md (pipeline, tools, cloud storage, observers, LLM router — all complete)
> **Status**: Ready to Start

---

## Overview

This task covers the remaining five phases from `ASSISTANT_PLAN.md`:

| Phase | Name | Key Output |
|-------|------|-----------|
| Phase 0 | Foundation | System user, project structure, structured logging, Cloudflare tunnel |
| Phase 1 | Working Chat | FastAPI app, WebSocket hub, Claude streaming, full frontend |
| Phase 2 | Memory System | Qdrant episodic memory, SQLite structured memory, context injection |
| Phase 3 | Proactive Engine | APScheduler, heartbeat, notification bus, Web Push, systemd |
| Phase 4 | Full Terminal + File Access | Complete tool tiers, script flow, audit log UI |

---

## Phase 0 — Foundation

> *Do this before writing a single line of application code.*
> **Estimated effort**: ~2 hours

### System Setup

- [ ] **P0-01** Create `assistant` restricted system user
  ```bash
  sudo useradd -r -s /bin/false -d /tmp/assistant-sandbox assistant
  sudo mkdir -p /tmp/assistant-sandbox
  sudo chown assistant:assistant /tmp/assistant-sandbox
  ```
- [ ] **P0-02** Verify `assistant` user cannot read your home directory
  ```bash
  sudo -u assistant ls ~/  # must fail with "Permission denied"
  ```
- [ ] **P0-03** Create remaining project directory structure (if not already present)
  ```
  backend/
  ├── main.py          # FastAPI entry point
  ├── worker.py        # Worker process entry point
  ├── memory/
  │   ├── episodic.py
  │   ├── structured.py
  │   └── injector.py
  ├── scheduler/
  │   ├── engine.py
  │   └── jobs.py
  └── proactive/
      ├── heartbeat.py
      └── notifications.py   # (webhooks/github/gmail already done)
  frontend/
  ├── app/
  │   ├── page.tsx
  │   ├── dashboard/page.tsx
  │   ├── logs/page.tsx
  │   └── jobs/page.tsx
  ├── components/
  └── lib/
  scripts/
  └── setup.sh
  ```
- [ ] **P0-04** Create `config.yml` with filesystem allowlist and `never_read` blocklist
  - Template in `ASSISTANT_PLAN.md` (llm, memory, proactive, filesystem, terminal, network, security, notifications, cloudflare sections)
- [ ] **P0-05** Set up Python virtual environment for backend
  ```bash
  python -m venv .venv && source .venv/bin/activate
  pip install fastapi uvicorn[standard] structlog anthropic sqlmodel qdrant-client \
              sentence-transformers apscheduler watchdog aiofiles pyperclip
  ```
- [ ] **P0-06** Set up Node environment for frontend
  ```bash
  npx create-next-app@14 frontend --typescript --tailwind --app
  cd frontend && npm install @anthropic-ai/sdk zustand recharts @tanstack/react-table \
    @monaco-editor/react react-flow-renderer react-markdown react-hook-form \
    next-pwa web-push
  ```
- [ ] **P0-07** Install and configure structlog with correlation IDs
  - Base logging config in `backend/config/logging.py`
  - JSON output to stdout, correlation ID propagated via `contextvars`
  - Fields: `timestamp`, `level`, `logger`, `correlation_id`, `tool_name` (where applicable)
- [ ] **P0-08** Set up Cloudflare tunnel (if not already done)
  ```bash
  cloudflared tunnel create assistant
  # Configure ~/.cloudflared/config.yml per ASSISTANT_PLAN.md
  ```
- [ ] **P0-09** Set up Cloudflare Access policy for your email address only

### Acceptance Criteria
- `assistant` user exists; `sudo -u assistant ls ~/` returns "Permission denied"
- `config.yml` present and parseable
- Structured logging emitting JSON to console with correlation IDs
- Cloudflare tunnel connected and Access policy active

---

## Phase 1 — Working Chat with Rich Output

> **Estimated effort**: ~2 weeks

### Backend Tasks

#### 1.1 FastAPI App Skeleton
- [ ] **P1-01** Create `backend/main.py`
  - FastAPI app bound to `127.0.0.1:8000` only (never `0.0.0.0`)
  - CORS: allow only `localhost:3000`
  - Structured logging middleware with correlation ID injection
  - `/health` endpoint returning `{"status": "ok"}`

- [ ] **P1-02** WebSocket hub in `backend/ws/hub.py`
  - Single WebSocket connection per authenticated client
  - Signed session token validation (HMAC-SHA256, 15-minute TTL)
  - Event envelope:
    ```typescript
    type ServerEvent =
      | { event: "chat.token";       data: OutputBlock }
      | { event: "notification.new"; data: Notification }
      | { event: "job.started";      data: JobStatus }
      | { event: "job.completed";    data: JobResult }
      | { event: "dashboard.update"; data: DashboardPatch }
      | { event: "file.changed";     data: FileWatchEvent }
      | { event: "heartbeat.alert";  data: HeartbeatResult }
    ```
  - Broadcast helpers: `send_to_client(client_id, event)`, `broadcast(event)`

- [ ] **P1-03** Security headers middleware in `backend/middleware/security.py`
  - CSP: `default-src 'self'`; allow Monaco CDN only for editor
  - `X-Frame-Options: DENY`
  - `X-Content-Type-Options: nosniff`
  - `Strict-Transport-Security`
  - `Referrer-Policy: no-referrer`

- [ ] **P1-04** Claude API integration in `backend/llm/claude.py`
  - Use `anthropic` SDK with streaming (`stream=True`)
  - Model: `claude-sonnet-4-6` (override from `config.yml`)
  - System prompt injection: current datetime + calendar events + retrieved memories
  - Token streaming → parse output block XML tags → emit typed `OutputBlock`s

- [ ] **P1-05** Output block streaming parser in `backend/llm/output_parser.py`
  - Detect XML tags in LLM stream: `<text>`, `<chart>`, `<table>`, `<code>`, `<metric>`, `<form>`, `<action_confirm>`
  - Buffer partial tags, emit complete blocks
  - Default block type: `text` (raw markdown)
  - Unit-testable: takes a string stream, yields `OutputBlock` objects

- [ ] **P1-06** Tool result pipeline wired into Claude tool calls
  - Every tool call result passes through `backend/pipeline/pipeline.py` before returning to LLM
  - Tier 2/3 tool calls pause and emit `action_confirm` block; resume after user approval

- [ ] **P1-07** `ToolBase` class pipeline enforcement
  - `execute()` must be called via `CapabilityRegistry`, never directly
  - Startup validation: all registered tools have valid tier declarations

- [ ] **P1-08** Tier 1 tools wired up and registered
  - `read_file`, `list_directory`, `system_stats` (already implemented in `backend/tools/`)
  - Register them in `backend/main.py` startup

- [ ] **P1-09** SQLite conversation history in `backend/memory/conversation.py`
  - Schema: `conversations(id, created_at)`, `messages(id, conversation_id, role, content, timestamp)`
  - Use `SQLModel` + `aiosqlite`
  - Store raw LLM messages + tool results

- [ ] **P1-10** `/api/chat` POST endpoint (streaming SSE)
  - Accepts: `{ message: str, conversation_id: str | null }`
  - Returns: SSE stream of `OutputBlock` JSON objects
  - Creates conversation if `conversation_id` is null

- [ ] **P1-11** `/api/history` GET endpoint
  - Returns paginated conversation list and message history
  - `GET /api/history` → list of conversations
  - `GET /api/history/{conversation_id}` → messages for conversation

#### 1.2 Frontend Tasks

- [ ] **P1-12** Next.js 14 App Router setup
  - Tailwind CSS + shadcn/ui components
  - Dark mode by default (class-based)
  - Global error boundary

- [ ] **P1-13** API proxy routes in `frontend/app/api/`
  - `POST /api/chat` → proxies to `http://localhost:8000/api/chat`
  - `GET /api/history` → proxies to backend
  - Browser never contacts port 8000 directly

- [ ] **P1-14** WebSocket client in `frontend/lib/ws.ts`
  - Zustand event store for WebSocket state
  - Auto-reconnect with exponential backoff (1s, 2s, 4s, max 30s)
  - Event routing: `chat.token` → chat store, `notification.new` → notification store

- [ ] **P1-15** Chat interface in `frontend/app/page.tsx`
  - Message thread with streaming token display
  - react-markdown for text blocks (with syntax highlighting via `highlight.js`)
  - Input: textarea with Shift+Enter newline, Enter to submit
  - Auto-scroll to bottom on new tokens
  - Loading indicator during LLM response

- [ ] **P1-16** Output block renderers in `frontend/components/blocks/`
  - **TextBlock** — `react-markdown` + `rehype-highlight`
  - **ChartBlock** — Recharts; supports `line`, `bar`, `pie`, `scatter`; JSON data from block
  - **TableBlock** — TanStack Table with sortable/filterable columns
  - **CodeBlock** — Monaco Editor (read-only), language auto-detected from block metadata
  - **MetricBlock** — KPI card (value, label, delta, sparkline via Recharts)
  - **ActionConfirmBlock** — modal with:
    - Tier badge (Tier 2: yellow, Tier 3: red)
    - Approve / Deny buttons
    - Diff view for file writes (Monaco diff editor)
    - Impact summary from classifier justification
  - **NotificationBlock** — expandable card with priority badge

- [ ] **P1-17** Voice input in `frontend/components/chat/VoiceInput.tsx`
  - Web Speech API push-to-talk button
  - Transcript appears in input box; user confirms before sending
  - Graceful fallback if browser doesn't support Web Speech API

- [ ] **P1-18** PWA in `frontend/`
  - `next-pwa` for service worker
  - `manifest.json`: name, icons, theme_color, display: standalone
  - Offline page showing last cached conversation

### Acceptance Criteria
- Can chat with Claude via webapp at `localhost:3000`
- Charts, tables, metrics, code blocks all render inline in chat
- ActionConfirmBlock fires for Tier 2/3 tools; approve/deny works end-to-end
- Conversation history persisted in SQLite; browsable in UI
- Installable as PWA on mobile
- All requests from browser flow through Next.js proxy (port 8000 never exposed)

---

## Phase 2 — Memory System

> **Estimated effort**: ~1 week

### Tasks

#### 2.1 Qdrant Episodic Memory
- [ ] **P2-01** Qdrant embedded setup in `backend/memory/episodic.py`
  - Use `qdrant-client` in embedded/local mode (no separate Docker service)
  - Collection: `conversations` with 384-dim embeddings (sentence-transformers `all-MiniLM-L6-v2`)
  - Schema: `{ id, conversation_id, summary, embedding, timestamp, metadata }`

- [ ] **P2-02** Embedding service in `backend/memory/embeddings.py`
  - `sentence-transformers` `all-MiniLM-L6-v2` (small, runs locally, ~80MB)
  - Async wrapper so embedding doesn't block the event loop
  - Cache embeddings for identical text (avoid re-computing)

- [ ] **P2-03** Session summarization job in `backend/memory/summarizer.py`
  - Runs at end of each conversation (triggered by conversation close event)
  - Calls Claude with: last N messages → returns a structured summary
  - Summary stored in Qdrant with conversation metadata

- [ ] **P2-04** Episodic memory retrieval in `backend/memory/episodic.py`
  - `search(query: str, top_k: int = 5) -> list[MemoryEntry]`
  - Called at query time before injecting context into prompt
  - Returns most semantically similar past conversation summaries

#### 2.2 Structured Memory (SQLite)
- [ ] **P2-05** Structured memory schema in `backend/memory/structured.py`
  - Tables:
    - `facts(id, subject, predicate, object, confidence, source_conversation_id, created_at)`
    - `preferences(id, key, value, updated_at)`
    - `people(id, name, relationship, notes, updated_at)`
    - `events(id, title, date, description, recurring, created_at)`

- [ ] **P2-06** Entity extraction from conversations
  - After summarization: call Claude with summary → extract entities → populate structured tables
  - Idempotent: don't create duplicates (upsert by subject+predicate or name)

- [ ] **P2-07** Memory tools registered in `CapabilityRegistry`
  - `memory_write(type, data)` — Tier 2 (reversible write)
  - `memory_search(query)` — Tier 1 (read-only)
  - `memory_delete(id)` — Tier 3 (explicit confirmation)

#### 2.3 Context Injection
- [ ] **P2-08** Context injector in `backend/memory/injector.py`
  - Called before every LLM request
  - Injects into system prompt:
    - Current datetime (ISO 8601)
    - Upcoming calendar events (next 24h if calendar integration configured)
    - Top-5 episodic memories semantically similar to current query
    - Key structured facts (preferences, recent people/events)
  - Token budget: cap injected context at 2000 tokens

#### 2.4 Nightly Consolidation
- [ ] **P2-09** Consolidation job in `backend/memory/consolidation.py`
  - Scheduled via APScheduler at 3am (configurable via `config.yml`)
  - Steps:
    1. Load all `facts` where `confidence < 0.5`
    2. Ask Claude: "Are these facts still accurate / should they be merged?"
    3. Merge duplicates, delete stale facts, update confidences
    4. Deduplicate Qdrant embeddings (cosine similarity > 0.98)
    5. Log consolidation report to structured log

#### 2.5 Frontend: Memory Browser
- [ ] **P2-10** Memory browser in `frontend/app/memory/page.tsx`
  - Sidebar panel with three tabs: Facts, People, Preferences
  - Each row: value + source conversation link + delete button (Tier 3 confirmation)
  - Search bar filters rows client-side
  - Episodic memories tab: list of conversation summaries with timestamps

### Acceptance Criteria
- Assistant recalls facts from previous conversations ("You told me last Tuesday that…")
- Every LLM prompt includes current time + relevant past memories
- Context injection stays within 2000-token budget
- Memory browser shows all stored facts/preferences/people
- User can delete individual memories from the UI
- Nightly consolidation runs without user interaction

---

## Phase 3 — Proactive Engine

> **Estimated effort**: ~1 week

### Tasks

#### 3.1 Process Split
- [ ] **P3-01** Split backend into two processes
  - `backend/main.py` → `assistant-api` process (HTTP/WebSocket, port 8000)
  - `backend/worker.py` → `assistant-worker` process (tools, scheduler, heartbeat)
  - Communication via SQLite job queue (see P3-02)

- [ ] **P3-02** SQLite job queue in `backend/scheduler/queue.py`
  - Schema: `jobs(id, type, payload_json, status, created_at, started_at, completed_at, result_json, error)`
  - API process enqueues jobs; Worker process polls and executes
  - `status`: `pending → running → done | failed`
  - Polling interval: 500ms in Worker

#### 3.2 APScheduler
- [ ] **P3-03** APScheduler setup in `backend/scheduler/engine.py`
  - `AsyncIOScheduler` with `SQLAlchemyJobStore` (persisted to `scheduler.db`)
  - Survives Worker restarts (jobs re-registered from DB on startup)
  - Job triggers: `CronTrigger` and `IntervalTrigger`

- [ ] **P3-04** Job management tools registered in `CapabilityRegistry`
  - `job_create(cron, task_prompt, allowed_tools)` — Tier 2
  - `job_list()` — Tier 1
  - `job_update(id, cron?, task_prompt?, allowed_tools?)` — Tier 2
  - `job_delete(id)` — Tier 3
  - Jobs stored in SQLite; LLM manages its own schedule via these tools

#### 3.3 Heartbeat Monitor
- [ ] **P3-05** Heartbeat loop in `backend/proactive/heartbeat.py`
  - Runs every 5 min (configurable via `config.yml`)
  - Rule-based checks (no LLM unless anomaly found):
    - Calendar events in next 2 hours
    - Urgent unread messages (if email configured)
    - Monitored URLs/services up (via `uptime_monitor.py`)
    - Filesystem changes in watched dirs (via `observers.py`)
    - System resource anomalies: disk > 90%, memory > 90%, CPU > 95% for 5 min
    - Price/stock alerts (if configured)
  - If anomaly found → call LLM to analyse → emit notification
  - Log every heartbeat run with duration and check results

#### 3.4 Notification Bus
- [ ] **P3-06** Notification bus in `backend/proactive/notifications.py`
  - SQLite store: `notifications(id, title, body, priority, source, created_at, read_at, snoozed_until, dismissed_at)`
  - Priority levels: `info` / `warning` / `urgent`
  - Push to connected WebSocket clients via `ws_hub.broadcast()`
  - API: `GET /api/notifications`, `POST /api/notifications/{id}/dismiss`, `POST /api/notifications/{id}/snooze`

- [ ] **P3-07** Notification UI in `frontend/`
  - `NotificationBell.tsx`: badge with unread count; click opens panel
  - `NotificationPanel.tsx`: list of notifications sorted by priority then recency
  - Each card: title, body preview, timestamp, snooze/dismiss buttons
  - Expand to full detail on click

#### 3.5 Dashboard
- [ ] **P3-08** Dashboard page in `frontend/app/dashboard/page.tsx`
  - Grid layout (shadcn cards):
    - Upcoming calendar events (next 24h)
    - Recent scheduled jobs (last 10 runs)
    - Active alerts from heartbeat
    - Scheduled jobs list (cron, next run, last result)
  - Live updates via WebSocket `dashboard.update` events
  - Refresh without full page reload

#### 3.6 Web Push
- [ ] **P3-09** Web Push + VAPID in `backend/proactive/push.py` and frontend
  - Generate VAPID key pair on first run; store in `config.yml`
  - Backend: `web-push` Python library for sending notifications
  - Frontend: register service worker, subscribe to push, send subscription to backend
  - Push payload: `{ notification_id }` only (full content fetched on tap)
  - Opt-in UI in settings; test notification button

#### 3.7 Daily Reflection
- [ ] **P3-10** Reflection job in `backend/proactive/reflection.py`
  - Scheduled at 11pm (configurable)
  - Inputs: last 24h of activity logs + completed job outputs + heartbeat results
  - Calls Claude: "Summarise today's activity and surface patterns worth noting"
  - Result stored as a `notification` with priority `info`
  - Also stores as a `fact` in structured memory ("On 2026-02-23, you…")

#### 3.8 systemd Services
- [ ] **P3-11** Create three systemd service files in `scripts/systemd/`
  - `assistant-backend.service` — runs `uvicorn backend.main:app --host 127.0.0.1 --port 8000`
  - `assistant-worker.service` — runs `python -m backend.worker`
  - `assistant-frontend.service` — runs `npm start` in `frontend/`
  - `cloudflared.service` — runs `cloudflared tunnel run`
  - All services: `Restart=on-failure`, `RestartSec=5`, `User=<your user>` (not root)
  - Enable at boot: `systemctl enable assistant-backend assistant-worker assistant-frontend cloudflared`

### Acceptance Criteria
- Assistant runs scheduled jobs without user interaction (cron + interval)
- Heartbeat detects disk/memory/CPU anomalies and surfaces notifications
- Dashboard shows live state updated via WebSocket
- Browser receives push notification for `urgent` alerts
- Daily reflection runs at configured time
- All four services start automatically at boot via systemd

---

## Phase 4 — Full Terminal + File Access

> **Estimated effort**: ~1–2 weeks

### Tasks

#### 4.1 File Abstraction Layer (already partially done)
- [ ] **P4-01** URI scheme router in `backend/storage/router.py`
  - Parse URI: `local://`, `gdrive://`, `dropbox://`, `s3://`, `sftp://`
  - Route to the correct backend (all already implemented in `backend/storage/`)
  - Unified interface:
    ```python
    async def read(path: str) -> AsyncIterator[bytes]
    async def write(path: str) -> AsyncContextManager[BinaryIO]
    async def delete(path: str) -> None
    async def list(path: str) -> list[FileInfo]
    async def move(src: str, dst: str) -> None
    async def watch(path: str, callback) -> None
    ```

- [ ] **P4-02** `never_read` blocklist enforcement in the router
  - Check path against `config.yml` `never_read` globs before any read operation
  - Raise `PermissionError` (caught by pipeline → Tier 3 block) if matched

#### 4.2 Complete Tool Libraries
- [ ] **P4-03** Register all Tier 1 tools in `CapabilityRegistry` (startup in `backend/main.py`)
  - `list_directory`, `read_file`, `find_files`, `disk_usage`, `system_stats`
  - `git_status`, `git_log`, `git_diff`
  - `docker_ps`, `docker_stats`, `docker_logs`
  - `list_processes`, `get_process_info`, `get_logs`, `check_service`
  - `check_port`, `ping`, `dns_lookup`, `check_url`
  - `log_tail`, `uptime_monitor_status`, `health_check`

- [ ] **P4-04** Register all Tier 2 tools
  - `write_file`, `append_file`, `create_directory`
  - `run_python`, `run_node`
  - `git_add`, `git_commit`, `git_checkout`, `git_stash`, `git_create_branch`
  - `docker_restart`, `docker_exec`
  - `http_post` (allowlisted domains from `config.yml`)
  - `query_sqlite` (SELECT only)
  - `memory_write`, `memory_update`
  - `job_create`, `job_update`
  - `gmail_read`, `gmail_send`, `email_summarize`

- [ ] **P4-05** Register all Tier 3 tools
  - `delete_file`, `move_file`
  - `git_push`, `git_reset`, `git_rebase`, `git_merge`, `git_delete_branch`
  - `docker_stop`, `docker_start`, `docker_kill`, `docker_remove`
  - `start_service`, `stop_service`, `restart_service`, `kill_process`
  - `postgres_delete`, `postgres_drop`, `sqlite_drop`
  - `job_delete`

- [ ] **P4-06** Git tool suite in `backend/tools/git.py` (complete/verify)
  - All tools run as subprocess with `asyncio.create_subprocess_exec`
  - Output streamed, not buffered
  - Enforce working directory from params (no shell injection)

- [ ] **P4-07** Docker tool suite in `backend/tools/docker.py` (complete/verify)
  - Use Docker SDK for Python (`docker` package) for type-safe calls
  - `docker_exec` streams output via WebSocket

- [ ] **P4-08** System tool suite in `backend/tools/system.py` (complete/verify)
  - `system_stats`: CPU, memory, disk via `psutil`
  - `check_service` / `start_service` / `stop_service`: via `systemctl`
  - `get_logs`: `journalctl` with optional `--unit` and line limit

#### 4.3 Script Execution Flow
- [ ] **P4-09** Script execution flow (end-to-end)
  - When LLM calls `run_python` or `run_node`:
    1. Emit `CodeBlock` output block to frontend (Monaco editor, read-only)
    2. Emit `ActionConfirmBlock` (Tier 2 confirmation required)
    3. On user approval: save script to `~/assistant-scripts/{uuid}.py`
    4. Run via `asyncio.create_subprocess_exec` as `assistant` user (`sudo -u assistant`)
    5. Stream stdout/stderr back to chat via WebSocket
    6. Store in audit log

- [ ] **P4-10** Trusted scripts directory support
  - Scripts in `config.yml` `trusted_scripts_dir` skip Monaco review
  - Path is set in `ActionClassifier` constructor (already supported)
  - UI: badge showing "(trusted script)" when script is pre-approved

#### 4.4 Filesystem Watcher
- [ ] **P4-11** Filesystem watcher in `backend/proactive/observers.py` (verify/complete)
  - `watchdog` library watching dirs from `config.yml` `watched_dirs`
  - Events: `FileCreated`, `FileModified`, `FileDeleted`, `FileMoved`
  - Filter: skip hidden files, temp files (`*.tmp`, `~*`)

- [ ] **P4-12** File change WebSocket events
  - Watcher emits `file.changed` event via `ws_hub.broadcast()`
  - Frontend: toast notification with file path + action
  - Dashboard: live feed of recent file changes

#### 4.5 Audit Log UI
- [ ] **P4-13** Audit log schema in `backend/memory/audit.py`
  - Table: `audit_log(id, correlation_id, tool_name, tier, params_json, result_summary, user_approved, timestamp)`
  - Written by `ToolResultPipeline` after every tool execution
  - Retention: 90 days (configurable via `config.yml` `security.audit_log_retention_days`)

- [ ] **P4-14** Audit log API endpoints
  - `GET /api/audit` — paginated log with filters: tool_name, tier, date range
  - `GET /api/audit/{id}` — full detail for one entry

- [ ] **P4-15** Audit log viewer in `frontend/app/logs/page.tsx`
  - TanStack Table: sortable/filterable by tool, tier, date
  - Tier badge column (colour-coded: 1=green, 2=yellow, 3=red)
  - Expand row to see full params and result summary
  - Search bar for correlation ID lookup

### Acceptance Criteria
- Can run git operations, docker commands, file ops from chat
- Tier 1 actions execute without confirmation
- Tier 2 actions show soft confirmation (one click)
- Tier 3 actions show explicit confirmation with diff/impact summary
- Script review flow works end-to-end: Monaco → approve → sandboxed run → stream output
- `never_read` blocklist prevents access to `.env`, `*.pem`, `id_rsa*`
- Filesystem watcher notifies assistant of new files in watched dirs
- Full audit trail viewable in UI with 90-day retention

---

## Implementation Order

Follow this order to minimise blocked work:

```
P0 system setup
  → P1 backend skeleton (main.py, WS hub, security headers)
    → P1 Claude integration + streaming parser
      → P1 tool pipeline wiring (action confirm)
        → P1 frontend (chat UI + all block renderers)
          → P2 memory (Qdrant + SQLite + injector)
            → P2 frontend (memory browser)
              → P3 process split + job queue
                → P3 APScheduler + job tools
                  → P3 heartbeat + notification bus
                    → P3 dashboard + Web Push
                      → P3 systemd services
                        → P4 complete tool registration
                          → P4 script flow + trusted dir
                            → P4 audit log viewer
```

---

## Key Files to Create

```
backend/
├── main.py                      # P1-01: FastAPI app entry point
├── worker.py                    # P3-01: Worker process entry point
├── ws/
│   ├── __init__.py
│   └── hub.py                   # P1-02: WebSocket connection hub
├── middleware/
│   ├── __init__.py
│   └── security.py              # P1-03: CSP + security headers
├── llm/
│   ├── claude.py                # P1-04: Claude API + streaming
│   └── output_parser.py         # P1-05: Output block XML parser
├── memory/
│   ├── __init__.py
│   ├── conversation.py          # P1-09: SQLite conversation history
│   ├── episodic.py              # P2-01/P2-04: Qdrant vector store
│   ├── embeddings.py            # P2-02: sentence-transformers wrapper
│   ├── summarizer.py            # P2-03: Session summarization
│   ├── structured.py            # P2-05/P2-06: Facts/prefs/people
│   ├── injector.py              # P2-08: Context window injection
│   ├── consolidation.py         # P2-09: Nightly dedup/merge job
│   └── audit.py                 # P4-13: Audit log schema + writes
├── scheduler/
│   ├── __init__.py
│   ├── queue.py                 # P3-02: SQLite job queue
│   └── engine.py                # P3-03: APScheduler setup
├── proactive/
│   ├── heartbeat.py             # P3-05: 5-min monitor loop
│   ├── notifications.py         # P3-06: Notification bus + SQLite store
│   ├── reflection.py            # P3-10: Daily LLM reflection job
│   └── push.py                  # P3-09: Web Push / VAPID
├── storage/
│   └── router.py                # P4-01: URI scheme router
└── config/
    ├── __init__.py
    ├── loader.py                 # P0-04: config.yml parser
    └── logging.py                # P0-07: structlog setup

frontend/
├── app/
│   ├── page.tsx                 # P1-15: Chat interface
│   ├── dashboard/page.tsx       # P3-08: Proactive dashboard
│   ├── memory/page.tsx          # P2-10: Memory browser
│   ├── logs/page.tsx            # P4-15: Audit log viewer
│   └── jobs/page.tsx            # Future: job management UI
├── components/
│   ├── chat/
│   │   ├── MessageThread.tsx
│   │   ├── StreamingMessage.tsx
│   │   └── VoiceInput.tsx       # P1-17
│   ├── blocks/
│   │   ├── TextBlock.tsx
│   │   ├── ChartBlock.tsx
│   │   ├── TableBlock.tsx
│   │   ├── CodeBlock.tsx
│   │   ├── MetricBlock.tsx
│   │   ├── ActionConfirmBlock.tsx
│   │   └── NotificationBlock.tsx
│   └── notifications/
│       ├── NotificationBell.tsx # P3-07
│       └── NotificationPanel.tsx
└── lib/
    ├── ws.ts                    # P1-14: WebSocket client + Zustand
    └── api.ts                   # API client helpers

scripts/
├── setup.sh                     # P0 automation script
└── systemd/
    ├── assistant-backend.service  # P3-11
    ├── assistant-worker.service
    ├── assistant-frontend.service
    └── cloudflared.service

config.yml                        # P0-04: Runtime configuration
```

---

## Security Reminders (Non-Negotiable)

1. FastAPI **always** bound to `127.0.0.1:8000` — never `0.0.0.0`
2. `never_read` blocklist checked **before** any file read in the URI router
3. OAuth tokens stored in OS keychain only (never plaintext in `config.yml` or env)
4. All tool results pass through `ToolResultPipeline` before reaching LLM
5. Script execution **always** runs as `assistant` system user — never as your user
6. Every tool must be registered in `CapabilityRegistry` with explicit tier declaration
7. WebSocket session tokens: HMAC-SHA256, 15-minute TTL — validate on every message

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Chat round-trip latency (first token) | < 2 seconds |
| WebSocket reconnect on drop | < 5 seconds |
| Memory retrieval latency | < 200ms |
| Heartbeat cycle duration | < 30 seconds |
| Pipeline throughput | 100 tool results / 2 seconds |
| Audit log query (30 days) | < 500ms |
| Code coverage (backend) | > 80% |
| Lighthouse PWA score | > 90 |
