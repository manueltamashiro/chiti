"""
Action Classifier - Tier Classification for Tool Calls

Categorizes every tool action by risk tier to determine what confirmation
level is required before execution.

Tier 1 (Read-only):    No confirmation — read-only, no side effects
Tier 2 (Reversible):   Soft confirmation — writes that can be undone
Tier 3 (High-impact):  Explicit confirmation — destructive or irreversible

Pipeline position: content tagger → action classifier → output block
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from backend.pipeline.models import ActionTier, ClassificationResult

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """Represents a tool call to be classified"""
    tool_name: str
    params: Dict[str, Any] = field(default_factory=dict)
    # Optional context for context-aware modifiers
    context: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Base tier lookup table
# ---------------------------------------------------------------------------

#: Tools that are inherently read-only → Tier 1
_TIER_1_TOOLS: Set[str] = {
    # Filesystem reads
    "read_file", "list_directory", "find_files", "file_exists",
    "get_file_info", "get_metadata",
    # Git read
    "git_status", "git_log", "git_diff", "git_show", "git_branch",
    # Docker read
    "docker_ps", "docker_stats", "docker_logs", "docker_inspect",
    "docker_images",
    # System observation
    "system_stats", "list_processes", "get_process_info", "get_logs",
    "check_service", "disk_usage", "memory_stats", "cpu_stats",
    # Network observation
    "check_port", "ping", "dns_lookup", "check_url", "http_get",
    "trace_route",
    # Memory reads
    "memory_search", "memory_read",
    # Database reads
    "query_sqlite", "postgres_select", "mysql_select",
    # Log tail (observation only)
    "log_tail", "uptime_monitor_status",
    # Health
    "health_check",
    # Cloud storage list/read
    "cloud_list", "cloud_read", "gdrive_list", "gdrive_read",
    "dropbox_list", "dropbox_read", "s3_list", "s3_read",
    "sftp_list", "sftp_read",
}

#: Tools that write but are reversible → Tier 2
_TIER_2_TOOLS: Set[str] = {
    # Filesystem writes
    "write_file", "append_file", "create_directory",
    # Code execution (sandboxed)
    "run_python", "run_node", "run_script",
    # Git write
    "git_add", "git_commit", "git_checkout", "git_stash",
    "git_create_branch",
    # Docker reversible
    "docker_restart", "docker_exec",
    # Network (allowlisted domains)
    "http_post", "http_put", "http_patch",
    # Memory writes
    "memory_write", "memory_update",
    # Database writes (non-destructive)
    "execute_sqlite", "postgres_insert", "postgres_update",
    "mysql_insert", "mysql_update",
    # Cloud storage write
    "cloud_write", "gdrive_write", "dropbox_write", "s3_upload",
    "sftp_write",
    # Email read/send (OAuth)
    "gmail_read", "gmail_send", "outlook_read", "outlook_send",
    "email_summarize",
    # Job management
    "job_create", "job_update", "job_pause", "job_resume",
    # Webhook registration
    "webhook_register",
}

#: Tools that are destructive or irreversible → Tier 3
_TIER_3_TOOLS: Set[str] = {
    # Filesystem destructive
    "delete_file", "move_file", "rename_file", "overwrite_file",
    "delete_directory", "clear_directory",
    # Git destructive
    "git_push", "git_reset", "git_rebase", "git_merge",
    "git_delete_branch", "git_force_push",
    # Docker destructive
    "docker_stop", "docker_start", "docker_kill", "docker_remove",
    "docker_prune", "docker_build",
    # Service management
    "start_service", "stop_service", "restart_service",
    "kill_process", "kill_all_processes",
    # Database destructive
    "postgres_delete", "postgres_drop", "mysql_delete", "mysql_drop",
    "sqlite_drop", "truncate_table",
    # Backup
    "backup_run", "backup_restore",
    # Cloud destructive
    "cloud_delete", "gdrive_delete", "dropbox_delete", "s3_delete",
    "sftp_delete",
    # Job management (destructive)
    "job_delete", "job_cancel_all",
}

# ---------------------------------------------------------------------------
# Dangerous flag patterns (flag → tier bump)
# ---------------------------------------------------------------------------

#: CLI flags that should raise the tier
_DANGEROUS_FLAGS: List[re.Pattern] = [
    re.compile(r"--force", re.IGNORECASE),
    re.compile(r"--yes|-y\b", re.IGNORECASE),
    re.compile(r"--no-verify", re.IGNORECASE),
    re.compile(r"--hard", re.IGNORECASE),           # git reset --hard
    re.compile(r"-rf\b", re.IGNORECASE),            # rm -rf
    re.compile(r"--no-prompt", re.IGNORECASE),
    re.compile(r"--overwrite", re.IGNORECASE),
    re.compile(r"--delete", re.IGNORECASE),
    re.compile(r"--purge", re.IGNORECASE),
    re.compile(r"--destroy", re.IGNORECASE),
    re.compile(r"--prune", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# System-path patterns (path → tier bump)
# ---------------------------------------------------------------------------

_SYSTEM_PATH_PATTERNS: List[re.Pattern] = [
    re.compile(r"^/etc/"),
    re.compile(r"^/usr/"),
    re.compile(r"^/bin/"),
    re.compile(r"^/sbin/"),
    re.compile(r"^/lib"),
    re.compile(r"^/boot/"),
    re.compile(r"^/sys/"),
    re.compile(r"^/proc/"),
    re.compile(r"^/dev/"),
    re.compile(r"^/root/"),
    re.compile(r"^/var/log/"),
    re.compile(r"^C:\\Windows", re.IGNORECASE),
    re.compile(r"^C:\\Program Files", re.IGNORECASE),
]


def _is_system_path(path: str) -> bool:
    return any(p.match(path) for p in _SYSTEM_PATH_PATTERNS)


def _contains_dangerous_flags(params: Dict[str, Any]) -> bool:
    """Check any string param value for dangerous CLI flags"""
    for value in params.values():
        if isinstance(value, str):
            if any(flag.search(value) for flag in _DANGEROUS_FLAGS):
                return True
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and any(flag.search(item) for flag in _DANGEROUS_FLAGS):
                    return True
    return False


def _params_contain_system_path(params: Dict[str, Any]) -> bool:
    """Check whether any param looks like a system-level path"""
    for value in params.values():
        if isinstance(value, str) and _is_system_path(value):
            return True
        elif isinstance(value, list):
            if any(isinstance(v, str) and _is_system_path(v) for v in value):
                return True
    return False


# ---------------------------------------------------------------------------
# Tier ordering helpers
# ---------------------------------------------------------------------------

_TIER_ORDER = [ActionTier.TIER_1, ActionTier.TIER_2, ActionTier.TIER_3]


def _max_tier(a: ActionTier, b: ActionTier) -> ActionTier:
    return _TIER_ORDER[max(_TIER_ORDER.index(a), _TIER_ORDER.index(b))]


# ---------------------------------------------------------------------------
# ActionClassifier
# ---------------------------------------------------------------------------

class ActionClassifier:
    """
    Classifies tool calls into action tiers based on:
    1. Tool name (base tier lookup table)
    2. Parameter analysis (dangerous flags, paths)
    3. Context modifiers (first-time, repeated, trusted scripts dir)

    Returns a ClassificationResult with justification for every decision.
    """

    def __init__(self, trusted_scripts_dir: Optional[str] = None):
        """
        Args:
            trusted_scripts_dir: Path to pre-approved scripts directory.
                Scripts in this directory run at a lower tier (no re-review).
        """
        self.trusted_scripts_dir = trusted_scripts_dir
        # History of previously approved tool calls: tool_name → count
        self._approval_history: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, tool_call: ToolCall) -> ClassificationResult:
        """
        Classify a tool call into a tier.

        Args:
            tool_call: The tool call to classify

        Returns:
            ClassificationResult with tier, confidence, and justification
        """
        tool_name = tool_call.tool_name
        params = tool_call.params
        context = tool_call.context

        # 1. Base tier from lookup tables
        base_tier, base_reason = self._base_tier(tool_name)

        # 2. Parameter modifiers
        tier, modifiers = self._apply_param_modifiers(base_tier, params)

        # 3. Context modifiers
        tier, context_mods = self._apply_context_modifiers(tier, tool_name, params, context)
        modifiers.extend(context_mods)

        # 4. Confirmation requirements
        requires_confirmation = tier != ActionTier.TIER_1
        confirmation_type = None
        if tier == ActionTier.TIER_2:
            confirmation_type = "soft"
        elif tier == ActionTier.TIER_3:
            confirmation_type = "explicit"

        # 5. Confidence
        confidence = 0.9 if tool_name in (
            _TIER_1_TOOLS | _TIER_2_TOOLS | _TIER_3_TOOLS
        ) else 0.5

        # 6. Build justification
        justification = self._build_justification(
            tool_name, base_tier, tier, base_reason, modifiers
        )

        result = ClassificationResult(
            tier=tier,
            confidence=confidence,
            justification=justification,
            modifiers=modifiers,
            requires_confirmation=requires_confirmation,
            confirmation_type=confirmation_type,
        )

        logger.info(
            "classified tool_call",
            extra={
                "tool_name": tool_name,
                "base_tier": base_tier.value,
                "final_tier": tier.value,
                "modifiers": modifiers,
                "confidence": confidence,
            },
        )

        return result

    def record_approval(self, tool_name: str) -> None:
        """
        Record that a tool call was explicitly approved by the user.

        Repeated approvals enable the 'repeated_action' context modifier,
        which can reduce the tier for subsequent calls.
        """
        self._approval_history[tool_name] = self._approval_history.get(tool_name, 0) + 1

    def set_tier_override(self, tool_name: str, tier: ActionTier) -> None:
        """
        Explicitly set the tier for a tool (user override).

        This overrides the classifier's result for future calls.
        """
        self._overrides = getattr(self, "_overrides", {})
        self._overrides[tool_name] = tier
        logger.info(f"Tier override set: {tool_name} → {tier.value}")

    def get_tier_override(self, tool_name: str) -> Optional[ActionTier]:
        """Return any user-set tier override for a tool, or None"""
        overrides = getattr(self, "_overrides", {})
        return overrides.get(tool_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_tier(self, tool_name: str) -> tuple[ActionTier, str]:
        """Return base tier and reason from lookup table"""
        # Check user override first
        override = self.get_tier_override(tool_name)
        if override:
            return override, f"user override → {override.value}"

        if tool_name in _TIER_1_TOOLS:
            return ActionTier.TIER_1, "read-only tool"
        if tool_name in _TIER_2_TOOLS:
            return ActionTier.TIER_2, "reversible write tool"
        if tool_name in _TIER_3_TOOLS:
            return ActionTier.TIER_3, "high-impact/destructive tool"

        # Unknown tools default to Tier 2 (safe unknown)
        return ActionTier.TIER_2, "unknown tool — default to Tier 2"

    def _apply_param_modifiers(
        self,
        base_tier: ActionTier,
        params: Dict[str, Any],
    ) -> tuple[ActionTier, List[str]]:
        """Upgrade tier based on parameter analysis"""
        tier = base_tier
        modifiers: List[str] = []

        if _contains_dangerous_flags(params):
            tier = _max_tier(tier, ActionTier.TIER_3)
            modifiers.append("dangerous flag detected (--force / -rf / --hard / etc.)")

        if _params_contain_system_path(params):
            tier = _max_tier(tier, ActionTier.TIER_3)
            modifiers.append("system path detected (/etc, /usr, /bin, …)")

        # Destructive SQL keywords
        sql_params = {k: v for k, v in params.items() if isinstance(v, str)}
        for v in sql_params.values():
            if re.search(r"\b(DROP|TRUNCATE|DELETE\s+FROM)\b", v, re.IGNORECASE):
                tier = _max_tier(tier, ActionTier.TIER_3)
                modifiers.append("destructive SQL keyword detected (DROP/TRUNCATE/DELETE)")
                break

        return tier, modifiers

    def _apply_context_modifiers(
        self,
        tier: ActionTier,
        tool_name: str,
        params: Dict[str, Any],
        context: Dict[str, Any],
    ) -> tuple[ActionTier, List[str]]:
        """Apply context-based tier adjustments"""
        modifiers: List[str] = []
        tier_idx = _TIER_ORDER.index(tier)

        # Repeated approval history → can lower by 1
        approval_count = self._approval_history.get(tool_name, 0)
        if approval_count >= 3 and tier == ActionTier.TIER_2:
            tier_idx = max(0, tier_idx - 1)
            modifiers.append(
                f"repeated action (approved {approval_count}× before) — tier reduced"
            )

        # Trusted scripts directory → lower tier for known scripts
        if self.trusted_scripts_dir and tier in (ActionTier.TIER_2, ActionTier.TIER_3):
            script_path = params.get("path") or params.get("script_path") or ""
            if isinstance(script_path, str) and script_path.startswith(self.trusted_scripts_dir):
                tier_idx = max(0, tier_idx - 1)
                modifiers.append(f"script in trusted directory ({self.trusted_scripts_dir})")

        # First-time action → flag (no tier change, just a modifier note)
        if approval_count == 0 and tier != ActionTier.TIER_1:
            modifiers.append("first-time action — additional caution recommended")

        # Caller-provided context overrides (e.g., from the WebSocket session)
        if context.get("force_tier"):
            forced = ActionTier(context["force_tier"])
            tier_idx = _TIER_ORDER.index(forced)
            modifiers.append(f"caller forced tier → {forced.value}")

        return _TIER_ORDER[tier_idx], modifiers

    def _build_justification(
        self,
        tool_name: str,
        base_tier: ActionTier,
        final_tier: ActionTier,
        base_reason: str,
        modifiers: List[str],
    ) -> str:
        parts = [f"'{tool_name}' classified as {base_tier.value} ({base_reason})"]
        if modifiers:
            parts.append("Modifiers applied: " + "; ".join(modifiers))
        if final_tier != base_tier:
            parts.append(f"Final tier upgraded to {final_tier.value}")
        else:
            parts.append(f"Final tier: {final_tier.value}")
        return ". ".join(parts) + "."


# Singleton instance
action_classifier = ActionClassifier()
