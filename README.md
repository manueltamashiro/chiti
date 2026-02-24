# Chiti — Security-First Personal AI Assistant

A self-hosted personal AI assistant with a rich web UI, proactive notifications,
long-term memory, and a full tool suite — secured at every layer by a multi-stage
pipeline that protects against prompt injection, credential leakage, and runaway
actions.

---

## Architecture

```
Browser (Next.js 14)
    │  SSE  │  WebSocket
    ▼        ▼
Next.js API routes  ←──────────────────────────────┐
    │                                               │
    ▼                                               │
FastAPI  (backend/main.py  :8000)                   │
    │                                               │
    ├── ClaudeClient  ── Anthropic API              │
    │                                               │
    ├── Security Pipeline                           │
    │     Stage 1: Content Firewall                 │
    │     Stage 2: Secret Scrubber                  │
    │     Stage 3: Content Tagger                   │
    │     Stage 4: Action Classifier                │
    │                                               │
    ├── CapabilityRegistry  (48 tools, Tier 1/2/3)  │
    │     Tier 1 — read-only, no confirmation       │
    │     Tier 2 — reversible write, soft confirm   │
    │     Tier 3 — high-impact, explicit approval   │
    │                                               │
    ├── Memory System                               │
    │     Structured facts / preferences / people  │
    │     Episodic summaries (consolidation at 3am) │
    │     Semantic vector store (Qdrant)            │
    │                                               │
    ├── Proactive Engine                            │
    │     Heartbeat (5 min), reflection (11pm)      │
    │     Push notifications (Web Push / VAPID)     │
    │     Filesystem watcher → WS file.changed      │
    │                                               │
    ├── Scheduler  (APScheduler + SQLite queue)     │
    │                                               │
    └── WebSocket hub  ──────────────────────────── ┘
          real-time events to all connected clients
```

---

## Features

### Security Pipeline (all tool results flow through every stage)

| Stage | Component | What it does |
|-------|-----------|-------------|
| 1 | **Content Firewall** | Blocks prompt-injection (instruction overrides, role escapes, jailbreak patterns) |
| 2 | **Secret Scrubber** | Redacts API keys, JWT tokens, AWS/GH/OpenAI credentials, high-entropy strings |
| 3 | **Content Tagger** | Marks external content with trust level + origin chain |
| 4 | **Action Classifier** | Routes tool calls into Tier 1/2/3 and triggers confirmation flow |

### Tool Suite (48 tools, 3 tiers)

**Tier 1 — read-only (no confirmation)**
`read_file` · `list_directory` · `find_files` · `disk_usage` · `system_stats` ·
`list_processes` · `get_process_info` · `check_service` · `get_logs` · `check_port` ·
`ping` · `dns_lookup` · `check_url` · `git_status` · `git_log` · `git_diff` ·
`docker_ps` · `docker_stats` · `docker_logs` · `log_tail` · `uptime_check` ·
`memory_search` · `job_list`

**Tier 2 — reversible write (soft confirmation)**
`write_file` · `append_file` · `create_directory` · `run_python` · `run_node` ·
`git_add` · `git_commit` · `http_get` · `memory_write` · `job_create` · `job_update`

**Tier 3 — high-impact (explicit confirmation)**
`delete_file` · `move_file` · `git_push` · `git_create_branch` · `git_checkout` ·
`docker_restart` · `docker_start` · `docker_stop` · `start_service` · `stop_service` ·
`kill_process` · `trust_script` · `memory_delete` · `job_delete`

### Storage Backends

| Backend | Scheme | Status |
|---------|--------|--------|
| Local filesystem | `local://` | ✅ |
| Google Drive | `gdrive://` | ✅ (requires google-auth) |
| Dropbox | `dropbox://` | ✅ (requires dropbox) |
| Amazon S3 | `s3://` | ✅ (requires aiobotocore) |
| SFTP | `sftp://` | ✅ (requires asyncssh) |

All storage access is gated through `SecuredFileSystemRouter` which enforces the
`never_read` blocklist from `config.yml` (e.g. `**/.env`, `**/.aws`, `**/*.pem`).

### Memory System

- **Structured memory** — facts, preferences, people (SQLite)
- **Episodic memory** — auto-summarises completed conversations at 3 am
- **Semantic search** — Qdrant vector store with sentence-transformers embeddings
- **Context injection** — relevant memories injected into system prompt automatically

### Proactive Engine

- **Heartbeat** (every 5 min) — checks pending jobs, upcoming events, system health
- **Daily reflection** (11 pm) — reviews the day, prepares tomorrow's context
- **Filesystem watcher** — watchdog/polling observer emits `file.changed` WebSocket events
- **Web Push** (VAPID) — mobile notifications for urgent alerts
- **Webhooks** — GitHub, Gmail Pub/Sub ingestion

### Frontend (Next.js 14)

| Page | Path | Description |
|------|------|-------------|
| Chat | `/` | Streaming SSE chat with rich output blocks |
| Dashboard | `/dashboard` | System status, memory stats, job queue |
| Memory | `/memory` | Browse/delete facts, preferences, people, episodic summaries |
| Audit log | `/audit-log` | Searchable, filterable tool-call history |

Rich output blocks: `text` · `code` · `table` · `chart` · `metric` · `action_confirm`

---

## Project Status

| Phase | Component | Status |
|-------|-----------|--------|
| 0 | Capability Gateway (tools, skills, registry) | ✅ Complete |
| 1 | Content Firewall | ✅ Complete |
| 2 | Secret Scrubber | ✅ Complete |
| 3 | Content Tagger | ✅ Complete |
| 4 | Action Classifier + Pipeline integration | ✅ Complete |
| — | Backend API + WebSocket hub | ✅ Complete |
| — | Next.js frontend + rich blocks | ✅ Complete |
| — | Memory system (structured + episodic + semantic) | ✅ Complete |
| — | Proactive engine (heartbeat, reflection, push) | ✅ Complete |
| — | Full tool suite (48 tools, Tier 1/2/3) | ✅ Complete |
| — | Secured storage router + never_read blocklist | ✅ Complete |
| — | Filesystem watcher → WebSocket events | ✅ Complete |
| — | Cloud storage backends (GDrive, Dropbox, S3, SFTP) | ✅ Complete |
| — | Scheduler (APScheduler + SQLite queue) | ✅ Complete |
| — | Audit log (schema + API + UI) | ✅ Complete |
| — | LLM routing (Claude primary / Ollama fallback) | ✅ Complete |
| — | Export + backup + health checker | ✅ Complete |

---

## Quick Start

### Requirements

- Python 3.11+
- Node.js 18+

### Backend

```bash
# Install Python dependencies
pip install fastapi uvicorn[standard] anthropic aiosqlite pydantic pyyaml \
            apscheduler qdrant-client sentence-transformers

# Optional backends
pip install google-auth google-auth-oauthlib  # Google Drive
pip install dropbox                            # Dropbox
pip install aiobotocore                        # S3
pip install asyncssh                           # SFTP
pip install watchdog                           # Filesystem watcher (faster)

# Copy and edit config
cp config.yml.example config.yml   # or edit config.yml directly

# Run
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev        # dev server on :3000
# or
npm run build && npm start
```

### Tests

```bash
pytest tests/          # 898 tests, ~14s
pytest tests/ -q       # quiet summary
```

---

## Configuration (`config.yml`)

```yaml
llm:
  model: claude-sonnet-4-6

filesystem:
  allowed_read: ["~/Documents", "~/projects"]
  allowed_write: ["~/projects"]
  never_read: ["**/.env", "**/*.pem", "**/*.key", "**/id_rsa*", "**/.aws", "**/.ssh"]
  watched_dirs: ["~/projects"]      # emit file.changed WS events

security:
  ws_secret: "CHANGE_ME"
  audit_log_retention_days: 90
  pipeline_mode: balanced           # strict | balanced | permissive

terminal:
  limits:
    max_runtime_seconds: 60
  scripts:
    always_show_before_run: true
    trusted_scripts_dir: "~/assistant-scripts/trusted"
```

---

## Creating a Tool

```python
from backend.pipeline.capability_gateway import ToolBase, CapabilityMetadata
from backend.pipeline.models import ActionTier, CapabilityType, OutputBlock

class MyTool(ToolBase):
    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="my_tool",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Does something useful",
        )

    async def execute(self, params):
        return OutputBlock(type="text", content="Result")

    def get_parameter_schema(self):
        return {
            "type": "object",
            "properties": {"input": {"type": "string"}},
            "required": ["input"],
        }
```

Then register it in `backend/main.py` → `_register_tools()`.

---

## License

MIT License — see `LICENSE` for details.
