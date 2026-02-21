# Personal AI Assistant — Implementation Plan
> Reviewed by: Solutions Architect (Alex), Security Engineer (Maya), Claude
> Last updated: 2026-02-21

---

## Project Overview

A proactive, always-on personal AI assistant running as a localhost webapp, exposed securely via Cloudflare Tunnel. Built around two processes (API + Worker), rich interactive output, real filesystem/terminal access, and a proactive engine that reaches out to you — not just responds to you.

---

## Architecture Summary

```
[Your Devices] → Cloudflare Access → Cloudflare Tunnel
    → localhost:3000 (Next.js frontend)
        → localhost:8000 (FastAPI API process, internal only)
            → SQLite job queue
        → Assistant Worker process (runs as: assistant user)
            → LangGraph orchestration
            → Tool result pipeline (content firewall → secret scrubber → action classifier)
            → Tools: filesystem, terminal, git, docker, web, APIs
            → Memory: Qdrant (local) + SQLite
            → Scheduler: APScheduler (CRON + interval jobs)
            → Heartbeat: async monitor loop
            → Observers: filesystem watcher, webhook receiver
```

### Two Core Processes

| Process | Port | Runs As | Responsibility |
|---|---|---|---|
| assistant-api | 8000 (internal only) | you | HTTP/WebSocket, chat streaming, auth |
| assistant-worker | internal | assistant (restricted user) | tools, scheduler, heartbeat, observers |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 14 (App Router) + Tailwind + shadcn/ui |
| Charts | Recharts |
| Tables | TanStack Table |
| Code editor | Monaco Editor |
| Node graphs | React Flow |
| Real-time | WebSockets (native FastAPI) |
| Push notifications | Web Push API + VAPID |
| PWA | next-pwa |
| Backend API | FastAPI + uvicorn |
| Agent framework | LangGraph (complex) + direct calls (simple) |
| LLM primary | Claude API (claude-sonnet-4-6) |
| LLM local (optional) | Ollama + Llama 3 |
| Scheduler | APScheduler (persisted to SQLite) |
| Vector DB | Qdrant (embedded, local) |
| Structured memory | SQLite via SQLModel |
| File abstraction | Custom async streaming layer |
| Cloud storage | Google Drive / Dropbox OAuth |
| Secret storage | OS Keychain (secretstorage / keyring) |
| Filesystem watcher | watchdog |
| Logging | structlog (structured + correlation IDs) |
| Tunnel | cloudflared |
| Auth | Cloudflare Access (Zero Trust) |
| Process management | systemd |
| Sandboxing | assistant system user (restricted, no sudo) |

---

## Output Block Protocol

The LLM emits typed blocks, not raw text. Frontend renders each type:

```
text          → react-markdown
chart         → Recharts (line, bar, pie, scatter)
table         → TanStack Table (sortable, filterable)
file          → download button + preview panel
code          → Monaco Editor
graph         → React Flow
metric        → KPI card with sparkline
form          → react-hook-form (assistant asks YOU for input)
action_confirm → modal with approve/deny + impact summary
notification  → badge + expandable card
```

---

## Security Architecture

### Non-Negotiables (Day One)
- `assistant` system user: `sudo useradd -r -s /bin/false -d /tmp/assistant-sandbox assistant`
- FastAPI bound to `127.0.0.1:8000` only (never 0.0.0.0)
- `never_read` blocklist: `.env`, `*.pem`, `*.key`, `id_rsa*`, `.aws/credentials`
- Tool result pipeline: content firewall → secret scrubber → untrusted content tagging
- Prompt injection defense: external content tagged as untrusted in every prompt
- Structured logging with correlation IDs (structlog) from first commit

### Action Confirmation Tiers
| Tier | Examples | Friction |
|---|---|---|
| 1 — Read-only | file read, git status, system stats | None |
| 2 — Reversible write | file write, git commit, docker restart | Soft confirm (one click) |
| 3 — High-impact | file delete, git push, service stop/start | Explicit confirm + diff view |

### OAuth Token Storage
- Use OS keychain (Linux: `secretstorage`, macOS: `keyring`)
- Never plaintext files or unencrypted SQLite
- Request minimum necessary OAuth scopes per service

---

## Terminal Tool Library

### Tier 1 — No Confirmation
`list_directory`, `read_file`, `find_files`, `disk_usage`, `git_status`, `git_log`, `git_diff`, `docker_ps`, `docker_stats`, `docker_logs`, `list_processes`, `get_process_info`, `system_stats`, `check_service`, `get_logs`, `check_port`, `ping`, `dns_lookup`, `check_url`

### Tier 2 — Soft Confirmation
`write_file`, `append_file`, `create_directory`, `run_python`, `run_node`, `git_add`, `git_commit`, `docker_restart`, `http_get` (allowlisted domains), `query_sqlite` (SELECT only)

### Tier 3 — Explicit Confirmation
`delete_file`, `move_file`, `git_push`, `git_create_branch`, `docker_stop`, `docker_start`, `start_service`, `stop_service`, `kill_process`, `execute_sqlite` (no DROP/TRUNCATE)

### Disabled Until Explicitly Enabled
`run_shell_script`, `http_post`, `query_postgres`

### Script Execution Rule
Scripts written by the assistant are ALWAYS shown in Monaco editor before execution. Never auto-run. After approval: saved to `~/assistant-scripts/{uuid}.py`, run in sandbox as `assistant` user, output streamed to chat, retained in audit log.

---

## File Abstraction Layer

All file operations use URI scheme routing:

```
local://~/Documents/report.pdf
gdrive://My Drive/Projects/
dropbox://Personal/
s3://my-bucket/backups/
sftp://myserver.com/var/www/
```

All I/O is async streaming (never load full file into memory). Interface:
```python
async def read(path: str) -> AsyncIterator[bytes]
async def write(path: str) -> AsyncContextManager[BinaryIO]
async def delete(path: str) -> None
async def list(path: str) -> list[FileInfo]
async def move(src: str, dst: str) -> None
async def watch(path: str, callback) -> None
```

---

## Proactive Engine

### Scheduler
- APScheduler persisted to SQLite
- LLM can create/modify/delete its own jobs via tool calls
- Each job stores: cron expression, task prompt, allowed tools, last/next run

### Heartbeat (every 5 min, configurable)
Lightweight checks (no LLM unless something found):
- Calendar events in next 2 hours
- Urgent unread messages
- Monitored URLs/services up
- Filesystem changes in watched dirs
- System resource anomalies (disk, memory, CPU)
- Price/stock alerts

### Daily Reflection Job
Once per day, LLM reviews last 24h of activity logs and surfaces patterns the rule-based heartbeat would miss.

### Observer Events
- Filesystem watcher (watchdog) on configured directories
- Email push notifications (Gmail Pub/Sub)
- Webhook receiver at `/webhooks/{source}`
- Log file tail for watched services

### Notification Bus
All proactive outputs flow through a single bus before reaching you:
- Priority levels: info / warning / urgent
- Browser push payload is minimal (notification ID only, full content fetched on tap)
- Snooze, dismiss, or expand to full detail
- Stored in SQLite, never auto-deleted

---

## WebSocket Event Envelope

Single WS connection per client, all events flow through it:

```typescript
type ServerEvent =
  | { event: "chat.token";         data: OutputBlock }
  | { event: "notification.new";   data: Notification }
  | { event: "job.started";        data: JobStatus }
  | { event: "job.completed";      data: JobResult }
  | { event: "dashboard.update";   data: DashboardPatch }
  | { event: "file.changed";       data: FileWatchEvent }
  | { event: "heartbeat.alert";    data: HeartbeatResult }
```

---

## Memory System

| Type | Storage | Purpose |
|---|---|---|
| Working | Context window | Current conversation |
| Episodic | Qdrant (local) | Past conversation summaries, semantic search |
| Structured | SQLite | Facts, preferences, people, recurring patterns |
| Temporal | Injected at query time | Current time, upcoming calendar events |

Nightly consolidation job: deduplicates, merges related memories, surfaces things worth retaining.

Every prompt injects: current datetime, upcoming calendar events, retrieved episodic memories, structured facts about you.

---

## Cloudflare Configuration

```yaml
# ~/.cloudflared/config.yml
tunnel: your-tunnel-id
credentials-file: /home/you/.cloudflared/your-tunnel-id.json

ingress:
  - hostname: assistant.yourdomain.com
    service: http://localhost:3000
  - service: http_status:404
  # Backend port 8000 intentionally NOT exposed
```

Cloudflare Access policy: allow only your email. Free tier sufficient for personal use.

---

## systemd Services

Three services, all enabled at boot:
- `assistant-backend.service` — FastAPI on 127.0.0.1:8000
- `assistant-frontend.service` — Next.js on localhost:3000
- `cloudflared.service` — tunnel daemon

---

# IMPLEMENTATION PHASES

---

## Phase 0 — Foundation (Day 1, ~2 hours)
*Do this before writing a single line of application code.*

### Tasks

- [ ] **P0-01** Create `assistant` system user
  ```bash
  sudo useradd -r -s /bin/false -d /tmp/assistant-sandbox assistant
  sudo mkdir -p /tmp/assistant-sandbox
  sudo chown assistant:assistant /tmp/assistant-sandbox
  ```
- [ ] **P0-02** Verify `assistant` user has no access to your home directory
  ```bash
  sudo -u assistant ls ~/  # should fail with permission denied
  ```
- [ ] **P0-03** Create project directory structure
  ```
  assistant/
  ├── backend/
  │   ├── main.py
  │   ├── worker.py
  │   ├── tools/
  │   ├── memory/
  │   ├── scheduler/
  │   ├── pipeline/
  │   └── config/
  ├── frontend/
  │   ├── app/
  │   ├── components/
  │   └── lib/
  ├── scripts/
  ├── config.yml
  └── ASSISTANT_PLAN.md  ← this file
  ```
- [ ] **P0-04** Create `config.yml` with filesystem allowlist and `never_read` blocklist
- [ ] **P0-05** Set up Python venv for backend, Node env for frontend
- [ ] **P0-06** Install structlog, configure base logging with correlation IDs
- [ ] **P0-07** Set up Cloudflare tunnel (if not already done)
- [ ] **P0-08** Set up Cloudflare Access policy for your email

### Exit Criteria
- `assistant` user exists, cannot read your home directory
- Project structure in place
- Structured logging emitting JSON to console
- Cloudflare tunnel connected

---

## Phase 1 — Working Chat with Rich Output (2 weeks)

### Backend Tasks

- [ ] **P1-01** FastAPI app skeleton bound to `127.0.0.1:8000`
- [ ] **P1-02** WebSocket hub with signed session token validation
- [ ] **P1-03** CSP + security headers middleware
- [ ] **P1-04** Claude API integration (streaming SSE)
- [ ] **P1-05** Output block streaming parser (detects XML tags in LLM stream, emits typed blocks)
- [ ] **P1-06** Tool result pipeline skeleton:
  - Content firewall (instruction-pattern detector)
  - Secret scrubber (regex + entropy-based)
  - Untrusted content tagger
  - Action classifier (tier 1/2/3)
- [ ] **P1-07** ToolBase class with: parameters schema, permission check, execute, sanitize
- [ ] **P1-08** First Tier 1 tools: `read_file`, `list_directory`, `system_stats`
- [ ] **P1-09** Conversation history stored in SQLite
- [ ] **P1-10** `/api/chat` POST endpoint (streaming)
- [ ] **P1-11** `/api/history` GET endpoint

### Frontend Tasks

- [ ] **P1-12** Next.js 14 app with App Router, Tailwind, shadcn/ui
- [ ] **P1-13** API proxy routes (`/api/*` → FastAPI, never direct port 8000 from browser)
- [ ] **P1-14** WebSocket client (Zustand event store)
- [ ] **P1-15** Chat interface: message thread, streaming token display, markdown rendering
- [ ] **P1-16** Output block renderers:
  - TextBlock (react-markdown + syntax highlighting)
  - ChartBlock (Recharts — line, bar, pie, scatter)
  - TableBlock (TanStack Table — sortable, filterable)
  - CodeBlock (Monaco Editor — read-only)
  - MetricBlock (KPI card)
  - ActionConfirmBlock (modal with approve/deny)
- [ ] **P1-17** Voice input (Web Speech API push-to-talk button)
- [ ] **P1-18** PWA manifest + service worker (next-pwa)

### Exit Criteria
- Can chat with Claude via webapp
- Charts, tables, metrics render inline in chat
- Action confirmation modal works end-to-end
- Installable as PWA on mobile
- All requests flow through Cloudflare Access

---

## Phase 2 — Memory System (1 week)

- [ ] **P2-01** Qdrant embedded setup (local, no separate service)
- [ ] **P2-02** Embedding service (sentence-transformers or OpenAI embeddings)
- [ ] **P2-03** Session summarization job (runs at end of each conversation)
- [ ] **P2-04** Episodic memory retrieval (semantic search at query time)
- [ ] **P2-05** Structured memory SQLite schema (facts, preferences, people, events)
- [ ] **P2-06** Entity extraction from conversations (auto-populate structured memory)
- [ ] **P2-07** `memory_write` and `memory_search` tools
- [ ] **P2-08** Context injection at prompt time (datetime + calendar + retrieved memories)
- [ ] **P2-09** Nightly consolidation job (dedup, merge, surface patterns)
- [ ] **P2-10** Memory browser UI (sidebar panel — see what the assistant knows about you)

### Exit Criteria
- Assistant remembers facts from past conversations
- Context window always includes relevant memories
- You can browse and delete stored memories from the UI

---

## Phase 3 — Proactive Engine (1 week)

- [ ] **P3-01** Split backend into two processes: `assistant-api` and `assistant-worker`
- [ ] **P3-02** Job queue (SQLite-based, polling) between API and Worker
- [ ] **P3-03** APScheduler setup in Worker process, jobs persisted to SQLite
- [ ] **P3-04** `job_create`, `job_list`, `job_update`, `job_delete` tools (LLM manages its own schedule)
- [ ] **P3-05** Heartbeat monitor loop (configurable interval, default 5 min)
- [ ] **P3-06** Notification bus (SQLite store + WebSocket push)
- [ ] **P3-07** Notification UI: badge count, notification panel, snooze/dismiss
- [ ] **P3-08** Dashboard page: upcoming calendar, recent tasks, active alerts, scheduled jobs
- [ ] **P3-09** Web Push API + VAPID keys (browser push for urgent notifications)
- [ ] **P3-10** Daily reflection job (LLM reviews last 24h logs)
- [ ] **P3-11** systemd service files for both processes + cloudflared

### Exit Criteria
- Assistant runs jobs on schedule without any user interaction
- Heartbeat surfaces anomalies proactively
- Dashboard shows live state updated via WebSocket
- Phone receives push notifications for urgent alerts
- All three services start automatically at boot

---

## Phase 4 — Full Terminal + File Access (1-2 weeks)

- [ ] **P4-01** Async streaming file abstraction layer (URI scheme router)
- [ ] **P4-02** Local filesystem backend (aiofiles)
- [ ] **P4-03** Complete Tier 1 tool library (all read-only/observation tools)
- [ ] **P4-04** Complete Tier 2 tool library (soft confirmation tools)
- [ ] **P4-05** Complete Tier 3 tool library (explicit confirmation tools)
- [ ] **P4-06** Git tool suite (status, log, diff, add, commit, push, branch, checkout)
- [ ] **P4-07** Docker tool suite (ps, logs, start, stop, restart, exec, stats)
- [ ] **P4-08** System tool suite (stats, service management, log tailing)
- [ ] **P4-09** Script execution flow: Monaco editor review → approve → sandboxed run → stream output
- [ ] **P4-10** Trusted scripts directory (pre-approved scripts, no re-review needed)
- [ ] **P4-11** Filesystem watcher (watchdog) on configured directories
- [ ] **P4-12** File change notifications via WebSocket event bus
- [ ] **P4-13** Audit log viewer in frontend (searchable, filterable by tool/date/tier)

### Exit Criteria
- Can run git operations, docker commands, file operations from chat
- Script review flow works end-to-end with Monaco editor
- Filesystem watcher notifies assistant of new files in watched dirs
- Full audit trail viewable in UI

---

## Phase 5 — Cloud Storage (1 week)

- [ ] **P5-01** OS keychain integration (`secretstorage` / `keyring`)
- [ ] **P5-02** OAuth flow for Google Drive (minimum scope: specific folder read/write)
- [ ] **P5-03** Google Drive file backend (streaming, async)
- [ ] **P5-04** OAuth flow for Dropbox (optional)
- [ ] **P5-05** Dropbox file backend (optional)
- [ ] **P5-06** S3 backend (optional, for backups)
- [ ] **P5-07** SFTP backend (optional)
- [ ] **P5-08** Cloud storage browser in UI (list, preview, download, upload)

### Exit Criteria
- OAuth tokens stored in OS keychain, never plaintext
- Can read/write Google Drive files with same tool interface as local files
- Tokens survive restart (loaded from keychain at startup)

---

## Phase 6 — Observer Hooks (1 week)

- [ ] **P6-01** Webhook receiver endpoint (`/webhooks/{source}`)
- [ ] **P6-02** Gmail push notifications via Google Pub/Sub (replaces polling)
- [ ] **P6-03** Email summarization scheduled job (daily at configurable time)
- [ ] **P6-04** Log file tail tool (watch service logs for errors/patterns)
- [ ] **P6-05** URL/service uptime monitor (heartbeat check)
- [ ] **P6-06** GitHub webhook integration (new PRs, issues, CI status)
- [ ] **P6-07** Custom webhook documentation (how to wire up any service)

### Exit Criteria
- Email arrives → assistant notified within 60 seconds via Pub/Sub
- Webhook from GitHub surfaces CI failures proactively
- Log errors in watched services trigger heartbeat alerts

---

## Phase 7 — Local LLM + Routing (optional, 1 week)

- [ ] **P7-01** Ollama setup + Llama 3 model
- [ ] **P7-02** Query classifier (simple/fast vs complex/sensitive)
- [ ] **P7-03** LLM router: local for simple queries, Claude API for complex
- [ ] **P7-04** Offline mode: full functionality with local model when internet unavailable
- [ ] **P7-05** Model selection UI (force local or cloud per conversation)

### Exit Criteria
- Simple queries (weather, quick calculations, local file ops) route to local model
- Complex queries (multi-step reasoning, code review) route to Claude API
- System works fully offline with reduced capability

---

## Phase 8 — Polish + Observability (ongoing)

- [ ] **P8-01** Logs viewer in frontend (structured log search, correlation ID drill-down)
- [ ] **P8-02** Job history page (all past runs, outputs, durations)
- [ ] **P8-03** Assistant settings UI (config.yml editor with validation)
- [ ] **P8-04** Theme support (dark/light, custom accent color)
- [ ] **P8-05** Keyboard shortcuts (global hotkey, quick command palette)
- [ ] **P8-06** Conversation export (markdown, JSON)
- [ ] **P8-07** Backup job for assistant data (memories, jobs, audit log → cloud storage)
- [ ] **P8-08** Health check endpoint + uptime display in dashboard

---

## Key Files Reference

```
backend/
├── main.py                    # FastAPI app, WS hub, session management
├── worker.py                  # Worker process entry point
├── config/
│   └── loader.py              # config.yml parser + validation
├── pipeline/
│   ├── content_firewall.py    # Prompt injection detection
│   ├── secret_scrubber.py     # Regex + entropy secret detection
│   ├── content_tagger.py      # Untrusted content tagging
│   └── action_classifier.py   # Tier 1/2/3 classification
├── tools/
│   ├── base.py                # ToolBase class
│   ├── filesystem.py          # File read/write/list/find
│   ├── process.py             # run_python, list_processes
│   ├── git.py                 # Full git suite
│   ├── docker.py              # Docker operations
│   ├── system.py              # Stats, services, logs
│   └── network.py             # http_get, check_url, ping
├── memory/
│   ├── episodic.py            # Qdrant vector store
│   ├── structured.py          # SQLite facts/preferences
│   └── injector.py            # Context window injection
├── scheduler/
│   ├── engine.py              # APScheduler setup
│   └── jobs.py                # Job CRUD + SQLite persistence
├── proactive/
│   ├── heartbeat.py           # Monitor loop
│   ├── notifications.py       # Notification bus
│   └── observers.py           # File watcher, webhooks
└── storage/
    ├── abstract.py            # FileSystem abstract base
    ├── local.py               # Local filesystem backend
    └── gdrive.py              # Google Drive backend

frontend/
├── app/
│   ├── page.tsx               # Chat interface
│   ├── dashboard/page.tsx     # Proactive dashboard
│   ├── logs/page.tsx          # Audit + structured logs
│   └── jobs/page.tsx          # Scheduled jobs manager
├── components/
│   ├── chat/
│   │   ├── MessageThread.tsx
│   │   ├── StreamingMessage.tsx
│   │   └── VoiceInput.tsx
│   ├── blocks/                # Output block renderers
│   │   ├── ChartBlock.tsx
│   │   ├── TableBlock.tsx
│   │   ├── CodeBlock.tsx
│   │   ├── MetricBlock.tsx
│   │   └── ActionConfirmBlock.tsx
│   └── notifications/
│       ├── NotificationBell.tsx
│       └── NotificationPanel.tsx
└── lib/
    ├── ws.ts                  # WebSocket client + Zustand store
    └── api.ts                 # API client

config.yml                     # All runtime configuration
scripts/
└── setup.sh                   # Phase 0 setup automation
```

---

## config.yml Template

```yaml
llm:
  primary: claude          # claude | ollama
  model: claude-sonnet-4-6
  local_model: llama3      # used when primary: ollama

memory:
  episodic_enabled: true
  consolidation_hour: 3    # 3am nightly

proactive:
  heartbeat_interval_minutes: 5
  reflection_enabled: true
  reflection_hour: 23      # 11pm

filesystem:
  allowed_read:
    - "local://~/Documents"
    - "local://~/projects"
    - "local://~/Downloads"
  allowed_write:
    - "local://~/Documents/assistant-output"
    - "local://~/projects"
  never_read:
    - "**/.env"
    - "**/*.pem"
    - "**/*.key"
    - "**/id_rsa*"
    - "**/.aws"
    - "**/.ssh"
  watched_dirs:
    - "local://~/Downloads"

terminal:
  run_as_user: assistant
  limits:
    max_runtime_seconds: 60
    max_output_bytes: 1048576
    max_concurrent_executions: 3
  scripts:
    always_show_before_run: true
    trusted_scripts_dir: "~/assistant-scripts/trusted"

network:
  allowed_domains:
    - "api.github.com"
    - "registry.npmjs.org"
    - "pypi.org"
    - "api.anthropic.com"

security:
  ws_token_ttl_minutes: 15
  audit_log_retention_days: 90

notifications:
  push_enabled: false      # enable after VAPID setup
  urgent_push: true

cloudflare:
  tunnel_hostname: "assistant.yourdomain.com"
```

---

## Notes for Claude Code

- Start with Phase 0 setup script, then Phase 1 backend skeleton
- The tool result pipeline (content firewall, secret scrubber, action classifier) should be built before any file or terminal tools are wired up
- `ToolBase` in `backend/tools/base.py` is the load-bearing class — get it right before building individual tools
- The output block streaming parser is the most complex frontend piece — build and test it with mock data before connecting to the real LLM stream
- All tools must declare their tier, allowed_paths, and network_access in their class definition — never inferred at runtime
- Never bind FastAPI to 0.0.0.0 — always 127.0.0.1
- The `assistant` system user must exist before any tool execution code is written
