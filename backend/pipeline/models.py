"""
Shared dataclasses and models for the pipeline system
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any, Dict, List
from datetime import datetime


class ActionTier(Enum):
    """Action classification tier for confirmation requirements"""
    TIER_1 = "read_only"  # No confirmation (read-only operations)
    TIER_2 = "reversible_write"  # Soft confirmation (reversible writes)
    TIER_3 = "high_impact"  # Explicit confirmation (destructive operations)


class CapabilityType(Enum):
    """Type of capability"""
    TOOL = "tool"  # Simple single-operation tool
    SKILL = "skill"  # Multi-step agent skill with LangGraph


@dataclass
class CapabilityMetadata:
    """Metadata for a capability (tool or skill)"""
    name: str
    type: CapabilityType
    tier: ActionTier
    description: str
    allowed_paths: List[str] = field(default_factory=list)
    requires_network: bool = False
    max_runtime_seconds: int = 300  # 5 minutes default
    tools_used: List[str] = field(default_factory=list)  # For skills: which tools they call

    # External service fields (for future integrations)
    requires_oauth: Optional[str] = None  # "gmail", "outlook", etc.
    allowed_scopes: List[str] = field(default_factory=list)  # Minimum OAuth scopes
    allowed_databases: List[str] = field(default_factory=list)  # For DB tools
    allowed_domains: List[str] = field(default_factory=list)  # For API tools


@dataclass
class OutputBlock:
    """Typed output block for rich rendering in frontend"""
    type: str  # "text", "chart", "table", "file", "code", "graph", "metric", "form", "action_confirm", "notification"
    content: Any  # Content varies by type
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Raw result from tool execution before pipeline processing"""
    capability_name: str
    success: bool
    content: Any
    error: Optional[str] = None
    execution_time_seconds: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ProcessedResult:
    """Result after pipeline processing (firewall, scrubber, tagger, classifier)"""
    original: ToolResult
    content: Any  # Scrubbed and safe content
    trust_level: str  # "trusted", "untrusted_external", "sanitized"
    tier: ActionTier
    tags: List[str] = field(default_factory=list)  # Security tags applied
    secrets_found: int = 0  # Number of secrets scrubbed
    injection_detected: bool = False
    blocked: bool = False  # True if content was blocked by firewall
    block_reason: Optional[str] = None


# Content Firewall Models


@dataclass
class InjectionPattern:
    """Pattern for detecting prompt injection"""
    pattern: str
    severity: int  # 0=clean, 1=suspicious, 2=likely_injection, 3=critical
    category: str  # "instruction_override", "role_escape", "delimiter_injection", etc.


@dataclass
class FirewallResult:
    """Result from content firewall scan"""
    severity: int  # 0=clean, 1=suspicious, 2=likely_injection, 3=critical
    patterns_found: List[InjectionPattern] = field(default_factory=list)
    should_block: bool = False
    should_tag: bool = False
    confidence: float = 0.0  # 0.0 to 1.0


# Secret Scrubber Models


@dataclass
class SecretPattern:
    """Pattern for detecting secrets"""
    pattern: str  # Regex pattern
    name: str  # "AWS API Key", "GitHub Token", etc.
    confidence: str  # "high", "medium", "low"


@dataclass
class SecretMatch:
    """A detected secret"""
    secret_type: str
    start_index: int
    end_index: int
    matched_text: str  # Actually redacted with placeholders
    confidence: str


@dataclass
class ScrubResult:
    """Result from secret scrubber"""
    scrubbed_content: Any  # Content with secrets redacted
    secrets_found: List[SecretMatch] = field(default_factory=list)
    secret_count: int = 0
    has_leaked_credentials: bool = False


# Content Tagger Models


class TrustLevel(Enum):
    """Trust level for content from tool results"""
    TRUSTED = "trusted"                          # Local, internal, verified source
    SEMI_TRUSTED = "semi_trusted"                # Known but external source (DB, process)
    UNTRUSTED_EXTERNAL = "untrusted_external"    # External/unknown source (web, APIs)
    SANITIZED = "sanitized"                      # Processed but originally contained secrets


class ContentSource(Enum):
    """Origin type of content"""
    LOCAL_FILESYSTEM = "local_filesystem"   # Local file read
    USER_INPUT = "user_input"               # Direct user input
    EXTERNAL_WEB = "external_web"           # HTTP(S) response from web
    EXTERNAL_API = "external_api"           # External API response
    DATABASE = "database"                   # Database query result
    PROCESS_OUTPUT = "process_output"       # Shell/process stdout/stderr
    INTERNAL = "internal"                   # Generated internally (no external source)
    UNKNOWN = "unknown"                     # Source could not be determined


@dataclass
class OriginEntry:
    """Single step in an origin chain tracking how content was produced"""
    source_name: str                         # Tool/source name, e.g., "read_file", "http_get"
    content_source: ContentSource
    path: Optional[str] = None               # File path, URL, DB name, etc.
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ContentTag:
    """A security tag applied to content"""
    name: str        # E.g., "EXTERNAL_CONTENT", "SECRETS_SCRUBBED", "INJECTION_DETECTED"
    reason: str      # Human-readable reason for tagging
    severity: int    # 0=info, 1=low, 2=medium, 3=high


@dataclass
class TaggerResult:
    """Result from content tagger"""
    trust_level: TrustLevel
    tags: List[ContentTag]
    origin_chain: List[OriginEntry]
    wrapped_content: str      # Content with trust markers embedded for LLM consumption
    summary: str              # Human-readable trust summary
    requires_disclosure: bool  # Whether to surface distrust info to user


# Action Classifier Models


@dataclass
class ClassificationResult:
    """Result from action classifier"""
    tier: ActionTier
    confidence: float  # 0.0 to 1.0
    justification: str  # Human-readable explanation
    modifiers: List[str] = field(default_factory=list)  # Context modifiers applied
    requires_confirmation: bool = False
    confirmation_type: Optional[str] = None  # "soft" or "explicit"


# Skill State Models


@dataclass
class SkillState:
    """State for LangGraph-based skills"""
    input_params: Dict[str, Any]
    steps_completed: List[str] = field(default_factory=list)
    tool_results: Dict[str, Any] = field(default_factory=dict)
    final_output: Optional[OutputBlock] = None
    errors: List[str] = field(default_factory=list)
    current_step: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# OAuth Models


@dataclass
class OAuthToken:
    """OAuth token storage model"""
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "Bearer"
    expires_at: Optional[datetime] = None
    scopes: List[str] = field(default_factory=list)


# Database Models


@dataclass
class DatabaseConfig:
    """Database connection configuration"""
    name: str
    host: str
    port: int
    database: str
    # Username/password stored in OS keychain, referenced by name
    provider: str = "postgresql"  # postgresql, mysql, sqlite, mongodb, redis
    ssl_mode: str = "prefer"  # require, prefer, disable
    pool_size: int = 5
    max_overflow: int = 10
