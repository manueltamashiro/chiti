"""
config/loader.py — Parse and validate config.yml at startup.

Single source of truth for all runtime configuration. Loaded once and cached.
Access via: from backend.config.loader import cfg
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class LLMConfig(BaseModel):
    primary: str = "claude"
    model: str = "claude-sonnet-4-6"
    local_model: str = "llama3"
    max_tokens: int = 4096
    temperature: float = 0.7


class MemoryConfig(BaseModel):
    episodic_enabled: bool = True
    consolidation_hour: int = 3
    context_token_budget: int = 2000


class ProactiveConfig(BaseModel):
    heartbeat_interval_minutes: int = 5
    reflection_enabled: bool = True
    reflection_hour: int = 23


class FilesystemConfig(BaseModel):
    allowed_read: List[str] = Field(default_factory=list)
    allowed_write: List[str] = Field(default_factory=list)
    never_read: List[str] = Field(default_factory=lambda: [
        "**/.env", "**/*.pem", "**/*.key", "**/id_rsa*", "**/.aws", "**/.ssh",
    ])
    watched_dirs: List[str] = Field(default_factory=list)


class TerminalLimits(BaseModel):
    max_runtime_seconds: int = 60
    max_output_bytes: int = 1048576
    max_concurrent_executions: int = 3


class TerminalScripts(BaseModel):
    always_show_before_run: bool = True
    trusted_scripts_dir: str = "~/assistant-scripts/trusted"


class TerminalConfig(BaseModel):
    run_as_user: str = "assistant"
    limits: TerminalLimits = Field(default_factory=TerminalLimits)
    scripts: TerminalScripts = Field(default_factory=TerminalScripts)


class NetworkConfig(BaseModel):
    allowed_domains: List[str] = Field(default_factory=list)


class SecurityConfig(BaseModel):
    ws_token_ttl_minutes: int = 15
    ws_secret: str = "CHANGE_ME_BEFORE_PRODUCTION"
    audit_log_retention_days: int = 90
    pipeline_mode: str = "balanced"


class NotificationsConfig(BaseModel):
    push_enabled: bool = False
    urgent_push: bool = True
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_email: str = ""


class CloudflareConfig(BaseModel):
    tunnel_hostname: str = "assistant.yourdomain.com"


class DatabaseConfig(BaseModel):
    path: str = "./data/assistant.db"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"


# ---------------------------------------------------------------------------
# Root config model
# ---------------------------------------------------------------------------

class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    filesystem: FilesystemConfig = Field(default_factory=FilesystemConfig)
    terminal: TerminalConfig = Field(default_factory=TerminalConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    cloudflare: CloudflareConfig = Field(default_factory=CloudflareConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _default_search_paths() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get("CHITI_CONFIG", "")
    if env:
        paths.append(Path(env))
    paths.append(Path.cwd() / "config.yml")
    paths.append(Path(__file__).parent.parent.parent / "config.yml")
    paths.append(Path.home() / ".config" / "chiti" / "config.yml")
    return paths


@lru_cache(maxsize=1)
def load_config(path: Optional[str] = None) -> AppConfig:
    """
    Load and validate config.yml, returning an AppConfig instance.

    Searches: $CHITI_CONFIG env var → ./config.yml → repo root → ~/.config/chiti/config.yml

    Falls back to all-defaults if no file found (useful in tests).
    """
    search = [Path(path)] if path else _default_search_paths()

    for candidate in search:
        if candidate and candidate.is_file():
            raw: Dict[str, Any] = yaml.safe_load(candidate.read_text()) or {}
            return AppConfig.model_validate(raw)

    # No file found — return defaults (tests and CI environments)
    return AppConfig()


# Convenience singleton
cfg: AppConfig = load_config()
