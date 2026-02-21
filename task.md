# Task: Build Tool Result Pipeline & Agent Skill Gateway

> **Priority**: CRITICAL - Must be completed before any filesystem or terminal tools
> **Phase**: Pre-Phase 1 (Security Foundation)
> **Status**: Ready to Start

---

## Overview

This task builds TWO critical security layers:

1. **Tool Result Pipeline** - Processes tool outputs through security stages
2. **Agent Skill Gateway** - Unified interface for both tools AND agent skills

Both share the same security infrastructure, ensuring agent skills (multi-step LLM workflows) get the same protection as single tools.

---

## Architecture: Unified Capability Interface

```
┌─────────────────────────────────────────────────────────────┐
│                     LLM Request                              │
│                  (tool call or skill)                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────┐
        │   Capability Gateway         │
        │  (Tool? Skill? Validate!)    │
        └──────────────┬───────────────┘
                       │
           ┌───────────┴───────────┐
           │                       │
           ▼                       ▼
    ┌──────────────┐      ┌────────────────┐
    │ Simple Tool  │      │ Agent Skill    │
    │ (execute)    │      │ (LangGraph)    │
    └──────┬───────┘      └────────┬───────┘
           │                       │
           └───────────┬───────────┘
                       │
                       ▼
        ┌──────────────────────────────┐
        │   Tool Result Pipeline       │
        │  ↓ Firewall → Scrubber →     │
        │    Tagger → Classifier       │
        └──────────────┬───────────────┘
                       │
                       ▼
              ┌─────────────────┐
              │   Output Block  │
              │   (to frontend) │
              └─────────────────┘
```

### Key Design Principle

**Agent skills ARE tools** - they're just tools that:
- Use LangGraph for multi-step orchestration
- May call other tools internally
- Return rich output blocks
- Go through the SAME security pipeline

### Supported Integrations (Future-Ready)

The capability gateway is designed to support:

| Category | Examples | Integration Method |
|----------|----------|-------------------|
| **Email** | Gmail, Outlook, Proton Mail | OAuth API (Gmail/Outlook), IMAP/SMTP (Proton) |
| **Databases** | PostgreSQL, MySQL, SQLite, Redis, MongoDB | Direct connection with credentials in keychain |
| **Cloud Storage** | Google Drive, Dropbox, S3 | OAuth API + async streaming file layer |
| **Communication** | Slack, Discord, Telegram | Bot tokens / OAuth APIs |
| **Development** | GitHub, GitLab, Jira, Linear | OAuth APIs |
| **Productivity** | Notion, Trello, Asana | OAuth APIs |
| **APIs** | Any REST/GraphQL API | HTTP tools with domain allowlisting |

All of these will:
- Use the same `Capability` base class
- Store tokens/credentials in OS keychain (never plaintext)
- Flow through the same security pipeline
- Be registered in `CapabilityRegistry`
- Support tier-based confirmation (1=none, 2=soft, 3=explicit)

### Adding New Skills

```python
# Simple tool (single operation)
class ReadFileTool(ToolBase):
    tier = ActionTier.TIER_1  # read-only
    # ... execute method

# Agent skill (multi-step workflow)
class ResearchAgentSkill(SkillBase):
    tier = ActionTier.TIER_2  # writes files
    agent_graph = research_graph  # LangGraph

    def get_description(self):
        return "Deep research on a topic using web + file analysis"

# Both register in CapabilityRegistry
# Both go through same pipeline
# Both require confirmation based on tier
```

---

## Phase 0: Capability Gateway (Days 1-2)

### Purpose
Create a unified registry and validation layer for BOTH simple tools AND agent skills. This is the foundation that makes adding skills trivial.

### Tasks

#### 0.1 Unified Interface
- [ ] **CG-01**: Create `backend/pipeline/capability_gateway.py`
- [ ] **CG-02**: Define `Capability` ABC (base for both ToolBase and SkillBase)
- [ ] **CG-03**: Define capability metadata:
  ```python
  @dataclass
  class CapabilityMetadata:
      name: str
      type: CapabilityType  # TOOL | SKILL
      tier: ActionTier
      description: str
      allowed_paths: list[str]
      requires_network: bool
      max_runtime_seconds: int
      tools_used: list[str]  # for skills (which tools they call)

      # External service fields (for future integrations)
      requires_oauth: Optional[str] = None  # "gmail", "outlook", etc.
      allowed_scopes: list[str] = []
      allowed_databases: list[str] = []
      allowed_domains: list[str] = []
  ```
- [ ] **CG-04**: Create `ToolBase` extending `Capability`
- [ ] **CG-05**: Create `SkillBase` extending `Capability`

#### 0.2 Capability Registry
- [ ] **CG-06**: Create `CapabilityRegistry` class (singleton)
- [ ] **CG-07**: Implement registration API:
  ```python
  registry.register_tool(ReadFileTool())
  registry.register_skill(ResearchAgentSkill())
  ```
- [ ] **CG-08**: Add lookup methods:
  - `get_capability(name: str) -> Capability`
  - `list_capabilities(type: Optional[CapabilityType]) -> list`
  - `find_by_tier(tier: ActionTier) -> list`
- [ ] **CG-09**: Add validation at registration time:
  - Name uniqueness
  - Tier declared
  - Metadata complete
  - For skills: validate `tools_used` are registered

#### 0.3 Skill Execution Framework
- [ ] **CG-10**: Create `backend/skills/base.py` with `SkillBase` class
- [ ] **CG-11**: Add skill execution lifecycle:
  ```python
  async def execute(self, params: dict) -> OutputBlock:
      # 1. Validate params against schema
      # 2. Initialize LangGraph state
      # 3. Run agent graph
      # 4. Stream intermediate outputs
      # 5. Return final result
  ```
- [ ] **CG-12**: Add skill progress streaming (WebSocket events for multi-step skills)
- [ ] **CG-13**: Implement skill cancellation (stop mid-execution)

#### 0.4 LangGraph Integration
- [ ] **CG-14**: Create `backend/skills/graph_builder.py`
- [ ] **CG-15**: Add helper for skill graphs:
  ```python
  def build_skill_graph(
      name: str,
      nodes: dict[str, callable],
      edges: list[tuple],
      entry_point: str
  ) -> StateGraph
  ```
- [ ] **CG-16**: Add skill state management:
  ```python
  @dataclass
  class SkillState:
      input_params: dict
      steps_completed: list[str]
      tool_results: dict
      final_output: Optional[OutputBlock]
      errors: list[str]
  ```
- [ ] **CG-17**: Create example skill: `FileAnalysisSkill` (simple research skill)

#### 0.5 Security for Skills
- [ ] **CG-18**: Skills inherit tier from highest-tier tool they use
- [ ] **CG-19**: Skills declare `tools_used` - validation on registration
- [ ] **CG-20**: Skills go through SAME pipeline as simple tools
- [ ] **CG-21**: Add skill sandbox:
  - Max runtime (default 5 min)
  - Max tool calls (default 100)
  - Max intermediate tokens (default 50k)
- [ ] **CG-22**: Add skill approval flow (user can approve/reject skill execution)

#### 0.6 External Service Infrastructure (OAuth & Databases)
- [ ] **CG-23**: Create `backend/storage/oauth.py` with `OAuthManager` class
- [ ] **CG-24**: Implement OS keychain integration:
  - macOS: `keyring` library
  - Linux: `secretstorage` library
- [ ] **CG-25**: Add OAuth token storage methods:
  ```python
  async def store_token(provider: str, token: OAuthToken)
  async def get_token(provider: str) -> str
  async def refresh_token(provider: str) -> str
  async def revoke_token(provider: str)
  ```
- [ ] **CG-26**: Add OAuth flow initiation endpoint:
  ```python
  @app.get("/auth/oauth/{provider}/start")
  async def start_oauth_flow(provider: str)
  ```
- [ ] **CG-27**: Add OAuth callback handler:
  ```python
  @app.get("/auth/oauth/{provider}/callback")
  async def oauth_callback(provider: str, code: str)
  ```
- [ ] **CG-28**: Create `backend/storage/database.py` with connection pooling
- [ ] **CG-29**: Add database credential storage (references in keychain):
  ```python
  @dataclass
  class DatabaseConfig:
      name: str
      host: str
      port: int
      database: str
      # username/password in keychain, stored by name
  ```
- [ ] **CG-30**: Add connection validation (test connection on startup)
- [ ] **CG-31**: Create `backend/tools/email/gmail_tool.py` (example OAuth tool)
- [ ] **CG-32**: Create `backend/tools/database/postgres_tool.py` (example DB tool)

#### 0.7 Testing
- [ ] **CG-33**: Test: simple tool registration and execution
- [ ] **CG-34**: Test: skill registration and execution
- [ ] **CG-35**: Test: skill that calls tools (full integration)
- [ ] **CG-36**: Test: skill inherits correct tier
- [ ] **CG-37**: Test: skill cancellation mid-execution
- [ ] **CG-38**: Test: invalid skills rejected (missing tools_used, etc.)
- [ ] **CG-39**: Test: OAuth token storage and retrieval from keychain
- [ ] **CG-40**: Test: OAuth flow end-to-end (simulated)
- [ ] **CG-41**: Test: database connection pooling
- [ ] **CG-42**: Test: email tool with mocked OAuth token
- [ ] **CG-43**: Test: database tool with test database

### Acceptance Criteria
- [ ] `CapabilityRegistry` can register both tools and skills
- [ ] Skills and tools share same metadata structure
- [ ] Skill execution flows through pipeline
- [ ] Skills can call other tools
- [ ] Skills can be cancelled mid-execution
- [ ] Example skill demonstrates full workflow
- [ ] OAuth token storage works with OS keychain
- [ ] Database connection pooling implemented
- [ ] Example Gmail tool demonstrates OAuth integration
- [ ] Example PostgreSQL tool demonstrates DB integration

---

## Phase 1: Content Firewall (Days 3-4)

### Purpose
Prevent the LLM from being manipulated by malicious content in tool results. Detects attempts to inject new instructions or override system prompts.

### Tasks

#### 1.1 Foundation & Pattern Library
- [ ] **CF-01**: Create `backend/pipeline/content_firewall.py`
- [ ] **CF-02**: Define `InjectionPattern` dataclass (pattern, severity, category)
- [ ] **CF-03**: Build pattern library covering:
  - Instruction overrides ("ignore previous instructions", "disregard all above")
  - Role escapes ("you are now a different assistant", "persona switch")
  - System prompt extraction ("print your system prompt", "show your instructions")
  - Delimiter confusion ("```", `"""`, XML tag injection)
  - Encoding tricks (base64, rot13, unicode escaping)
  - Jailbreak patterns ("DAN mode", "developer mode", "unrestricted mode")
- [ ] **CF-04**: Add tests for each pattern category

#### 1.2 Detection Engine
- [ ] **CF-05**: Implement `ContentFirewall` class with `scan(text: str) -> FirewallResult`
- [ ] **CF-06**: Add multi-language detection (Python, bash, PowerShell, JavaScript patterns)
- [ ] **CF-07**: Implement context-aware scanning (ignore code blocks that are legitimate output)
- [ ] **CF-08**: Add false-positive reduction for technical content (error messages, logs)
- [ ] **CF-09**: Implement severity scoring (0=clean, 1=suspicious, 2=likely_injection, 3=critical)

#### 1.3 Response Strategy
- [ ] **CF-10**: Define response actions per severity level:
  - 0: Allow through
  - 1: Tag as "untrusted content - proceed with caution"
  - 2: Block with explanation to user
  - 3: Critical security event - terminate session, log incident
- [ ] **CF-11**: Add sanitization mode (redact matched patterns instead of blocking)
- [ ] **CF-12**: Structured logging integration (correlation IDs, alert on high-severity matches)

### Acceptance Criteria
- [ ] Detects 100% of known prompt injection patterns from test suite
- [ ] False positive rate < 5% on legitimate code/config files
- [ ] Scan completes in < 50ms for 10KB text
- [ ] All detection events logged with severity and pattern match
- [ ] Integration test: firewall blocks malicious file, allows benign file

---

## Phase 2: Secret Scrubber (Days 3-4)

### Purpose
Prevent credentials, API keys, tokens, and other secrets from being stored in conversation history or displayed to users. Uses both regex patterns and entropy analysis.

### Tasks

#### 2.1 Pattern-Based Detection
- [ ] **SS-01**: Create `backend/pipeline/secret_scrubber.py`
- [ ] **SS-02**: Define `SecretPattern` dataclass (pattern, name, confidence)
- [ ] **SS-03**: Build regex library covering:
  - API keys (AWS, GitHub, Google, OpenAI, Anthropic, Stripe)
  - JWT tokens (bearer tokens, JWT structure)
  - Database URLs (postgresql://, mysql://, mongodb://)
  - Private keys (BEGIN PRIVATE KEY, BEGIN RSA PRIVATE)
  - Passwords in config format (password=, "pass":, :pwd@)
  - OAuth tokens (oauth_token, access_token=)
  - Session IDs, CSRF tokens, API keys in query params
  - Certificate signing requests
  - Base64-like high-entropy strings
- [ ] **SS-04**: Add tests for each secret pattern

#### 2.2 Entropy-Based Detection
- [ ] **SS-05**: Implement `calculate_entropy(string: str) -> float` (Shannon entropy)
- [ ] **SS-06**: Add high-entropy string detection:
  - Entropy threshold: > 4.5 for 32+ char strings
  - Character set variety check
  - Common encoding detection (base64, hex)
- [ ] **SS-07**: Context awareness (ignore known file hashes, UUIDs, legit IDs)
- [ ] **SS-08**: Sliding window scan for embedded secrets in larger text

#### 2.3 Redaction & Reporting
- [ ] **SS-09**: Implement `SecretScrubber` class with `scrub(text: str) -> ScrubResult`
- [ ] **SS-10**: Redaction strategy:
  - Replace with `{SECRET_TYPE}_REDACTED_{random_uuid}`
  - Preserve original length to avoid breaking formats
  - Keep mapping in secure memory (not logged) for potential recovery
- [ ] **SS-11**: Add secret summary (count by type, locations found)
- [ ] **SS-12**: Add "partial reveal" option (show first/last 4 chars for verification)

#### 2.4 Edge Cases & Performance
- [ ] **SS-13**: Handle multiline secrets (keys, certificates)
- [ ] **SS-14**: Avoid false positives on:
  - Documentation examples
  - Test fixtures
  - Example passwords in help text
  - Legitimate high-entropy strings (file hashes, UUIDs)
- [ ] **SS-15**: Performance optimization (< 100ms for 100KB text)
- [ ] **SS-16**: Streaming scrubber interface for large files

### Acceptance Criteria
- [ ] Detects 95%+ of secrets from known leak test suite
- [ ] False positive rate < 3% on clean code/config files
- [ ] Scrub completes in < 100ms for 100KB text
- [ ] All scrubbed secrets logged (type detected, location, action taken)
- [ ] Integration test: file with API keys → scrubbed output, secrets redacted
- [ ] Preserves file structure/format while redacting

---

## Phase 3: Content Tagger (Days 5)

### Purpose
Tag all external/untrusted content so the LLM knows it's not trusted. This is critical for prompt injection defense - the LLM must know which content came from user vs. from external sources.

### Tasks

#### 3.1 Tagger Design
- [ ] **CT-01**: Create `backend/pipeline/content_tagger.py`
- [ ] **CT-02**: Define trust levels:
  - `TRUSTED` - User input, system prompts
  - `UNTRUSTED_EXTERNAL` - Web content, file reads, API responses
  - `UNTRUSTED_USER_FILE` - Files in allowed paths but not user-created
  - `SANITIZED` - Passed through firewall + scrubber
- [ ] **CT-03**: Implement `ContentTagger` class with `tag(content: str, source: SourceInfo) -> TaggedContent`

#### 3.2 Injection into Prompts
- [ ] **CT-04**: Design prompt wrapper format:
  ```
  [UNTRUSTED_CONTENT_BEGIN Source:file_read path:/etc/passwd]
  <actual content here>
  [UNTRUSTED_CONTENT_END]
  ```
- [ ] **CT-05**: Add metadata injection (source, timestamp, trust level, tools used)
- [ ] **CT-06**: Implement content truncation with warning tags
- [ ] **CT-07**: Add "sanitized content" tag when content passed through pipeline

#### 3.3 Integration
- [ ] **CT-08**: Add tagging to tool result output formatting
- [ ] **CT-09**: Update prompt builder to wrap untrusted content
- [ ] **CT-10**: Add tests showing LLM respects tags (doesn't follow untrusted instructions)

### Acceptance Criteria
- [ ] All tool results tagged with source and trust level
- [ ] Prompt injection tests fail when malicious content is tagged
- [ ] LLM still processes untrusted content for information extraction
- [ ] Tag format doesn't break tokenization or parsing
- [ ] Integration test: malicious file content is tagged and ignored

---

## Phase 4: Action Classifier (Days 6-7)

### Purpose
Categorize every tool action by risk tier to determine what confirmation level is required. This is the bridge between security policy and UI behavior.

### Tasks

#### 4.1 Tier Definition
- [ ] **AC-01**: Create `backend/pipeline/action_classifier.py`
- [ ] **AC-02**: Define action tiers with explicit criteria:
  ```
  TIER 1 (Read-only):
  - No system state modification
  - No external network calls
  - No resource consumption
  Examples: file read, git status, system stats

  TIER 2 (Reversible write):
  - Writes that can be easily undone
  - No data destruction
  - Limited resource usage
  Examples: file write, git commit, docker restart

  TIER 3 (High-impact):
  - Data destruction or irreversible changes
  - External system modification (git push)
  - Service state changes
  - Resource-intensive operations
  Examples: file delete, git push, service stop/start
  ```
- [ ] **AC-03**: Document tier decision tree (flowchart)

#### 4.2 Classification Engine
- [ ] **AC-04**: Implement `ActionClassifier` class with `classify(tool_call: ToolCall) -> ClassificationResult`
- [ ] **AC-05**: Add rule-based classification:
  - Tool name → base tier lookup table
  - Parameter analysis (e.g., `rm -rf` flags increase tier)
  - Path analysis (system paths → higher tier)
  - Network analysis (external domains → higher tier)
- [ ] **AC-06**: Add ML-based classifier (optional, Phase 7):
  - Train on historical tool calls + user corrections
  - Feature: tool name, params, context, user intent
  - Model: simple decision tree or logistic regression

#### 4.3 Context-Aware Classification
- [ ] **AC-07**: Add context modifiers:
  - Repeated action → lower tier (user approved this before)
  - First-time action → higher tier
  - Dangerous flags (`--force`, `--yes`) → higher tier
  - Trusted scripts directory → lower tier
- [ ] **AC-08**: Implement tier override API (user can explicitly set tier)
- [ ] **AC-09**: Add tier justification (why this action got this tier)

#### 4.4 Integration & Testing
- [ ] **AC-10**: Update `ToolBase` to require tier declaration
- [ ] **AC-11**: Add validation: tool tier must match classifier's assessment
- [ ] **AC-12**: Write tests for edge cases:
  - Read with dangerous flags
  - Write to temp directory vs. home directory
  - Script execution with nested commands
- [ ] **AC-13**: Add audit logging (tool, params, tier, justification)

### Acceptance Criteria
- [ ] All tools have declared tier that matches classifier output
- [ ] Classification completes in < 10ms per tool call
- [ ] Justification provided for every tier assignment
- [ ] Context-aware modifiers work (repeated actions tier down)
- [ ] Integration test: file read is Tier 1, file delete is Tier 3

---

## Phase 5: Pipeline Integration (Days 8-9)

### Purpose
Connect all pipeline components into a unified flow that processes every tool result.

### Tasks

#### 5.1 Pipeline Orchestrator
- [ ] **PI-01**: Create `backend/pipeline/pipeline.py`
- [ ] **PI-02**: Implement `ToolResultPipeline` class with `process(result: ToolResult) -> ProcessedResult`
- [ ] **PI-03**: Define pipeline stages:
  ```
  1. Content Firewall → detect injection, tag untrusted
  2. Secret Scrubber → remove secrets
  3. Content Tagger → add trust tags
  4. Action Classifier → determine confirmation tier
  ```
- [ ] **PI-04**: Add stage-by-stage result tracking (debug mode)

#### 5.2 Configuration & Controls
- [ ] **PI-05**: Add pipeline modes:
  - `STRICT` - block on any suspicion
  - `BALANCED` - tag suspicious, block critical
  - `PERMISSIVE` - tag only, don't block
- [ ] **PI-06**: Add per-tool pipeline configuration (some tools bypass certain stages)
- [ ] **PI-07**: Implement pipeline telemetry (latency per stage, block rate)

#### 5.3 Error Handling
- [ ] **PI-08**: Define error handling per stage:
  - Firewall critical → terminate session
  - Scrubber failure → tag as "unscrubbed"
  - Classifier failure → default to highest tier
- [ ] **PI-09**: Add circuit breaker (disable tool if repeated failures)
- [ ] **PI-10**: Implement graceful degradation (continue with warnings)

#### 5.4 Testing
- [ ] **PI-11**: End-to-end integration tests:
  - Clean file content → passes through
  - File with secrets → scrubbed
  - File with injection → blocked
  - Network request → tiered correctly
- [ ] **PI-12**: Performance tests:
  - 100 tool results processed in < 2 seconds
  - No memory leaks in long-running tests
  - Concurrent request handling
- [ ] **PI-13**: Security tests:
  - Adversarial input suite (jailbreaks, obfuscation)
  - Bypass attempts (encoding, unicode, fragmentation)

### Acceptance Criteria
- [ ] All tool results flow through pipeline
- [ ] No tools can bypass pipeline (enforced by ToolBase)
- [ ] Pipeline stages execute in correct order
- [ ] Performance targets met
- [ ] Security test suite passes (100% bypass prevention)
- [ ] Full telemetry and logging

---

## Phase 6: Tool Base Integration (Day 10)

### Purpose
Update `ToolBase` class to enforce pipeline usage for all tools.

### Tasks

#### 6.1 ToolBase Updates
- [ ] **TB-01**: Read existing `backend/tools/base.py` (or create if not exists)
- [ ] **TB-02**: Add abstract method `get_tier(self) -> ActionTier`
- [ ] **TB-03**: Add abstract method `get_allowed_paths(self) -> list[str]`
- [ ] **TB-04**: Add abstract method `requires_network(self) -> bool`
- [ ] **TB-05**: Modify `execute()` signature to require pipeline processing
- [ ] **TB-06**: Add validation: tool declarations match classifier

#### 6.2 Tool Registration
- [ ] **TB-07**: Create tool registry with metadata
- [ ] **TB-08**: Add startup validation: all tools have valid tier declarations
- [ ] **TB-09**: Add tool permissions matrix (tier + paths + network)

#### 6.3 Testing & Validation
- [ ] **TB-10**: Write tool creation tests (must inherit ToolBase, declare tier)
- [ ] **TB-11**: Add lint rule/enforcer to catch tools not using pipeline
- [ ] **TB-12**: Documentation: how to create a new tool with proper security

### Acceptance Criteria
- [ ] ToolBase enforces pipeline usage
- [ ] All tools must declare tier, paths, network access
- [ ] Startup validation catches missing declarations
- [ ] Tool creation documentation complete
- [ ] Example tool demonstrating best practices

---

## Exit Criteria for Pipeline Work

Before moving to Phase 1 (actual tool implementation), verify:

- [ ] All 7 phases complete (including Phase 0: Capability Gateway)
- [ ] Full test suite passes (> 90% coverage)
- [ ] Performance targets met (see individual phases)
- [ ] Security test suite passes (no bypasses found)
- [ ] Documentation complete (architecture, API, usage)
- [ ] Integration test: mock tool flows through full pipeline
- [ ] Integration test: agent skill executes and flows through pipeline
- [ ] Code review: security engineer approval

---

## Files to Create

```
backend/pipeline/
├── __init__.py              # Pipeline exports
├── capability_gateway.py    # PHASE 0: Unified tool/skill registry
├── pipeline.py              # Orchestrator
├── content_firewall.py      # Injection detection
├── secret_scrubber.py       # Secret redaction
├── content_tagger.py        # Trust tagging
├── action_classifier.py     # Tier classification
├── models.py                # Shared dataclasses
└── config.py                # Pipeline configuration

backend/tools/
├── __init__.py
├── base.py                  # ToolBase class (extends Capability)
├── email/                   # PHASE 0: Email tools (OAuth)
│   ├── __init__.py
│   ├── gmail_tool.py        # Gmail integration example
│   ├── outlook_tool.py      # Outlook integration (future)
│   └── proton_tool.py       # Proton Mail integration (future)
└── database/                # PHASE 0: Database tools
    ├── __init__.py
    ├── postgres_tool.py     # PostgreSQL integration example
    ├── mysql_tool.py        # MySQL integration (future)
    └── sqlite_tool.py       # SQLite integration (future)

backend/skills/
├── __init__.py              # PHASE 0: New skills directory
├── base.py                  # SkillBase class (extends Capability)
├── graph_builder.py         # LangGraph helpers
└── examples/
    ├── file_analysis.py     # Example skill: analyze files
    ├── web_research.py      # Example skill: web research
    └── code_review.py       # Example skill: code review

backend/storage/
├── __init__.py              # PHASE 0: External service storage
├── oauth.py                 # OAuth token management (keychain)
└── database.py              # Database connection pooling

tests/pipeline/
├── test_capability_gateway.py   # PHASE 0: Registry tests
├── test_skills.py               # PHASE 0: Skill execution tests
├── test_oauth.py                # PHASE 0: OAuth flow tests
├── test_database.py             # PHASE 0: Database connection tests
├── test_content_firewall.py
├── test_secret_scrubber.py
├── test_content_tagger.py
├── test_action_classifier.py
├── test_pipeline_integration.py
└── fixtures/
    ├── injection_samples.txt
    ├── secret_samples.txt
    ├── tool_call_samples.json
    ├── skill_samples.json       # PHASE 0: Skill test cases
    └── oauth_samples.json       # PHASE 0: OAuth test cases
```

---

## Quick Reference: Adding New Skills

Once Phase 0 is complete, adding a new agent skill is straightforward:

### Step 1: Create the Skill File

```python
# backend/skills/code_review.py
from langgraph.graph import StateGraph
from backend.skills.base import SkillBase, SkillState
from backend.pipeline.models import ActionTier, CapabilityType

class CodeReviewSkill(SkillBase):
    """Reviews code for bugs, security issues, and best practices."""

    # Required metadata
    name = "code_review"
    tier = ActionTier.TIER_1  # Read-only
    description = "Analyzes code for bugs, security vulnerabilities, and style issues"
    allowed_paths = ["local://~/projects/**"]
    requires_network = False  # Optional: for external API calls
    tools_used = ["read_file", "search_files"]  # Tools this skill calls
    max_runtime_seconds = 300  # 5 minutes

    async def execute(self, params: dict) -> OutputBlock:
        """Execute the code review skill."""
        # 1. Validate params
        file_path = params.get("file_path")
        review_depth = params.get("depth", "standard")

        # 2. Initialize skill state
        state = SkillState(
            input_params=params,
            steps_completed=[],
            tool_results={},
            final_output=None,
            errors=[]
        )

        # 3. Get or create LangGraph
        graph = self._build_graph()

        # 4. Execute the graph
        result = await graph.ainvoke(state)

        # 5. Return output block
        return OutputBlock(
            type="code_review",
            content=result["final_output"],
            metadata={
                "steps": result["steps_completed"],
                "files_analyzed": len(result["tool_results"])
            }
        )

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph for this skill."""
        from backend.skills.graph_builder import build_skill_graph

        # Define skill nodes
        async def read_code(state: SkillState):
            file_path = state.input_params["file_path"]
            # Call tool through capability gateway
            content = await self.call_tool("read_file", {"path": file_path})
            state.tool_results["source_code"] = content
            return state

        async def analyze_code(state: SkillState):
            code = state.tool_results["source_code"]
            # Use LLM to analyze
            analysis = await self._llm_analyze(code)
            state.tool_results["analysis"] = analysis
            return state

        async def generate_report(state: SkillState):
            analysis = state.tool_results["analysis"]
            state.final_output = self._format_report(analysis)
            state.steps_completed = ["read_code", "analyze_code", "generate_report"]
            return state

        # Build graph
        return build_skill_graph(
            name="code_review",
            nodes={
                "read_code": read_code,
                "analyze_code": analyze_code,
                "generate_report": generate_report
            },
            edges=[
                ("read_code", "analyze_code"),
                ("analyze_code", "generate_report")
            ],
            entry_point="read_code"
        )
```

### Step 2: Register the Skill

```python
# backend/main.py (or backend/skills/__init__.py)
from backend.skills.code_review import CodeReviewSkill
from backend.pipeline.capability_gateway import capability_registry

# Register at startup
capability_registry.register_skill(CodeReviewSkill())
```

### Step 3: Use from Chat

User asks: *"Review the authentication code in ~/projects/myapp/auth.py"*

LLM receives the skill in its tool list:
```json
{
  "name": "code_review",
  "description": "Analyzes code for bugs, security vulnerabilities, and style issues",
  "tier": 1,
  "parameters": {
    "file_path": {"type": "string", "description": "Path to file to review"},
    "depth": {"type": "string", "enum": ["quick", "standard", "deep"], "default": "standard"}
  }
}
```

LLM calls: `code_review(file_path="local://~/projects/myapp/auth.py", depth="standard")`

Capability Gateway:
1. ✅ Validates skill is registered
2. ✅ Checks tier (Tier 1 = no confirmation needed for read-only)
3. ✅ Checks allowed_paths (~/projects is allowed)
4. ✅ Executes skill (runs LangGraph)
5. ✅ Each tool call within skill goes through pipeline
6. ✅ Returns OutputBlock with review results

---

## Skills vs Simple Tools: When to Use Which

| Use Case | Use Simple Tool | Use Agent Skill |
|----------|----------------|-----------------|
| Single operation (read file, list dir) | ✅ | ❌ |
| Multi-step workflow (research → analyze → report) | ❌ | ✅ |
| Needs internal LLM reasoning | ❌ | ✅ |
| Deterministic input/output | ✅ | ❌ |
| Branching logic based on results | ❌ | ✅ |
| Fast execution (< 1 second) | ✅ | ❌ |
| Complex workflow (> 1 minute) | ❌ | ✅ |

### Example Skills to Build Later

#### Email & Communication
1. **Email Triage Skill** - Read Gmail/Outlook/Proton → categorize → flag urgent → summarize
2. **Email Reply Skill** - Draft replies based on context + user approval
3. **Calendar Sync Skill** - Analyze emails → extract events → propose calendar additions

#### Database & Data
4. **Database Query Skill** - Query PostgreSQL/MySQL/SQLite → visualize results
5. **Data Analysis Skill** - Run queries → generate charts → export reports
6. **Database Backup Skill** - Automated backups to cloud storage with rotation

#### Code & DevOps
7. **Deep Research Skill** - Web search + read files + synthesize report
8. **Debug Assistant Skill** - Read logs + analyze errors + suggest fixes
9. **Migration Skill** - Analyze codebase + generate migration plan
10. **Documentation Skill** - Read code + generate docs + create markdown files
11. **Security Audit Skill** - Scan files + check patterns + generate report
12. **Refactoring Skill** - Analyze code + apply refactors + run tests

All of these will:
- Extend `SkillBase`
- Declare their tier and tools_used
- Go through the same security pipeline
- Be registered in `CapabilityRegistry`
- Work seamlessly with the existing architecture

---

## Extending to External Services (Email, Databases, APIs)

The Capability Gateway is designed to make adding external service integrations straightforward. Here's how email, databases, and other services will work:

### Email Integration (Gmail, Outlook, Proton)

```python
# backend/tools/email/gmail_tool.py
class GmailTool(ToolBase):
    """Read and manage Gmail messages."""

    name = "gmail"
    tier = ActionTier.TIER_1  # Read-only initially
    requires_network = True
    requires_oauth = "gmail"  # New: indicates OAuth provider needed
    allowed_scopes = ["gmail.readonly"]  # Minimum scopes

    async def execute(self, params: dict) -> OutputBlock:
        # OAuth token automatically loaded from OS keychain
        access_token = await self.oauth.get_token("gmail")

        # Fetch messages
        messages = await self._fetch_messages(
            token=access_token,
            query=params.get("query", "is:unread"),
            limit=params.get("limit", 10)
        )

        # Results flow through pipeline before returning
        return OutputBlock(type="email_list", content=messages)
```

### Database Integration (PostgreSQL, MySQL, SQLite)

```python
# backend/tools/database/postgres_tool.py
class PostgresTool(ToolBase):
    """Query PostgreSQL databases."""

    name = "postgres_query"
    tier = ActionTier.TIER_2  # Can write data (soft confirm)
    requires_network = True
    allowed_databases = ["localhost:*", "prod-db.example.com:5432/analytics"]

    async def execute(self, params: dict) -> OutputBlock:
        # Connection config from encrypted storage
        conn = await self._get_connection(params["database"])

        # Validate query (prevent DROP/TRUNCATE without explicit tier 3)
        query_type = self._classify_query(params["query"])
        if query_type == "destructive" and self.tier < 3:
            raise PermissionError("Destructive queries require Tier 3 approval")

        # Execute
        results = await conn.execute(params["query"])

        # Results flow through pipeline (secret scrubber catches leaked creds)
        return OutputBlock(type="table", content=results)
```

### OAuth Token Storage Architecture

```python
# backend/storage/oauth.py
class OAuthManager:
    """Manages OAuth tokens in OS keychain."""

    async def get_token(self, provider: str) -> str:
        """Retrieve access token from OS keychain."""
        # Uses keyring on macOS, secretstorage on Linux
        token = keyring.get_password(f"assistant_{provider}", "access_token")

        if self._is_expired(token):
            # Auto-refresh using refresh token
            token = await self._refresh_token(provider)

        return token

    async def store_token(self, provider: str, token: OAuthToken):
        """Store token in OS keychain (never plaintext files)."""
        keyring.set_password(
            f"assistant_{provider}",
            "access_token",
            token.access_token
        )
```

### Configuration in config.yml

```yaml
# OAuth providers (tokens stored in OS keychain)
oauth:
  gmail:
    enabled: true
    client_id: "${GMAIL_CLIENT_ID}"  # From environment variable
    scopes:
      - "gmail.readonly"
      - "gmail.labels"  # Minimum necessary scopes

  outlook:
    enabled: true
    client_id: "${OUTLOOK_CLIENT_ID}"
    scopes:
      - "mail.read"

  proton:
    enabled: false  # ProtonBridge doesn't support OAuth yet

# Database connections (credentials in keychain)
databases:
  postgres:
    - name: "local_dev"
      host: "localhost"
      port: 5432
      database: "myapp_dev"
      # username/password stored in keychain

  mysql:
    - name: "production_analytics"
      host: "prod-db.example.com"
      port: 3306
      database: "analytics"
      # Requires Tier 3 approval for writes

  sqlite:
    - path: "~/data/*.db"
      read_only: true
```

### New Capability Metadata Fields

```python
@dataclass
class CapabilityMetadata:
    name: str
    type: CapabilityType  # TOOL | SKILL
    tier: ActionTier
    description: str
    allowed_paths: list[str]
    requires_network: bool
    max_runtime_seconds: int
    tools_used: list[str]

    # NEW: External service fields
    requires_oauth: Optional[str] = None  # "gmail", "outlook", etc.
    allowed_scopes: list[str] = []  # Minimum OAuth scopes
    allowed_databases: list[str] = []  # For DB tools
    allowed_domains: list[str] = []  # For API tools
```

### Security Considerations for External Services

1. **OAuth Scopes**: Request minimum necessary scopes
   - Gmail: `gmail.readonly` not full access
   - Outlook: `mail.read` not `mail.send`

2. **Database Queries**:
   - Tier 1: SELECT queries only
   - Tier 2: INSERT, UPDATE (soft confirm)
   - Tier 3: DROP, TRUNCATE, DELETE (explicit confirm)

3. **Token Storage**:
   - Never in config files or environment variables
   - OS keychain only (keyring/secretstorage)
   - Auto-refresh with refresh tokens

4. **Pipeline Integration**:
   - Secret scrubber catches leaked credentials in query results
   - Content firewall tags all external data as untrusted
   - Action classifier enforces correct tier per operation type

### Future Extension Points

The capability gateway makes it easy to add:

| Service | Integration Type | Tier | Example Tool |
|---------|------------------|------|--------------|
| Gmail | OAuth API | 1/2 | `gmail_read`, `gmail_send` |
| Outlook | OAuth API | 1/2 | `outlook_read`, `outlook_calendar` |
| Proton Mail | IMAP/SMTP | 1/2 | `proton_read`, `proton_send` |
| PostgreSQL | Direct connection | 1/2/3 | `postgres_query` |
| MySQL | Direct connection | 1/2/3 | `mysql_query` |
| Redis | Direct connection | 1/2 | `redis_get`, `redis_set` |
| MongoDB | Direct connection | 1/2/3 | `mongodb_query` |
| Slack | OAuth API | 1/2 | `slack_read`, `slack_post` |
| Discord | Bot token | 1/2 | `discord_read`, `discord_send` |
| GitHub | OAuth API | 1/2 | `github_issues`, `github_prs` |
| Jira | OAuth API | 1/2 | `jira_tickets`, `jira_create` |
| Notion | OAuth API | 1/2 | `notion_read`, `notion_write` |
| Linear | OAuth API | 1/2 | `linear_tickets`, `linear_create` |

All of these follow the same pattern:
1. Extend `ToolBase` or `SkillBase`
2. Declare OAuth/database requirements
3. Register in `CapabilityRegistry`
4. Pipeline handles security automatically

---

## Success Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Injection detection rate | 100% | Test suite of known patterns |
| Secret detection rate | >95% | Test suite of leaked secrets |
| False positive rate | <5% | Clean code/config files |
| Pipeline latency | <200ms p99 | 100KB tool results |
| Skill execution success rate | >98% | Test suite of example skills |
| Skill registration time | <50ms | CapabilityRegistry benchmarks |
| Security bypass attempts | 0 | Adversarial test suite |
| Code coverage | >90% | pytest-cov |

---

## Skills-Specific Success Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Time to add new skill | <15 minutes | From template to working skill |
| Skill tier accuracy | 100% | Auto-calculated tier matches manual review |
| Skill cancellation latency | <1 second | Stop signal to execution stopped |
| Skill progress updates | <100ms per step | WebSocket event timing |
| Skills can call tools | 100% | All example skills work end-to-end |

---

## Notes

- **Build in isolation**: Pipeline should be testable without any real tools
- **Mock everything**: Use fake tool results for testing
- **Security mindset**: Assume adversarial LLM and malicious content
- **Fail closed**: When in doubt, block or tag as highest risk
- **Log everything**: Every pipeline stage decision must be auditable
- **Performance matters**: Pipeline runs on EVERY tool result - must be fast
- **No shortcuts**: Don't skip pipeline stages "just for testing"
