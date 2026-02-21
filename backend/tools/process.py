"""
Process Execution Tools (P4-09, P4-10)

Script execution flow:
  1. LLM calls run_python / run_node with the script content
  2. Tool returns an action_confirm block (script shown in Monaco editor)
  3. User approves in the frontend
  4. Frontend re-calls with confirmed=true → tool executes and streams output

Security:
  - Scripts are always shown for review unless the script hash is in the
    trusted_scripts directory (P4-10)
  - Execution happens as the 'assistant' system user (restricted, no sudo)
  - Output is capped at 1 MB
  - Runtime is capped at 60 seconds by default
  - Scripts are saved to ~/assistant-scripts/{uuid}.{ext} for audit purposes

Tier 2 — Soft confirmation required before execution.
"""

import asyncio
import hashlib
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.pipeline.models import (
    ActionTier,
    CapabilityMetadata,
    CapabilityType,
    OutputBlock,
)
from backend.tools.base import ToolBase

logger = logging.getLogger(__name__)

_MAX_OUTPUT_BYTES = 1_048_576  # 1 MB
_DEFAULT_TIMEOUT = 60  # seconds
_SCRIPTS_DIR = Path("~/assistant-scripts").expanduser()
_TRUSTED_SCRIPTS_DIR = _SCRIPTS_DIR / "trusted"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_block(title: str, message: str) -> OutputBlock:
    return OutputBlock(
        type="notification",
        content={"level": "error", "title": title, "message": message},
    )


def _script_hash(content: str) -> str:
    """SHA-256 of script content (used for trusted scripts lookup)."""
    return hashlib.sha256(content.encode()).hexdigest()


def _is_trusted(content: str) -> bool:
    """Return True if this exact script content is in the trusted scripts dir."""
    digest = _script_hash(content)
    trusted_file = _TRUSTED_SCRIPTS_DIR / f"{digest}.trusted"
    return trusted_file.exists()


async def _run_sandboxed(
    interpreter: str,
    script_path: str,
    run_as: str = "assistant",
    timeout: int = _DEFAULT_TIMEOUT,
) -> Tuple[int, str, str]:
    """
    Execute a script file under the restricted 'assistant' user via sudo -u.

    Falls back to running as the current user if 'assistant' user does not
    exist (e.g. in development).
    """
    # Check if 'assistant' user exists
    import pwd
    use_sudo = False
    try:
        pwd.getpwnam(run_as)
        use_sudo = True
    except KeyError:
        logger.warning(
            f"'{run_as}' user not found — executing as current user (dev mode)"
        )

    if use_sudo:
        cmd = ["sudo", "-u", run_as, "--", interpreter, script_path]
    else:
        cmd = [interpreter, script_path]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"Script timed out after {timeout}s")

    return (
        proc.returncode,
        stdout_b.decode(errors="replace")[:_MAX_OUTPUT_BYTES],
        stderr_b.decode(errors="replace")[:_MAX_OUTPUT_BYTES],
    )


def _save_script(content: str, ext: str) -> Path:
    """Save script to ~/assistant-scripts/{uuid}.{ext} for audit."""
    _SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    script_path = _SCRIPTS_DIR / f"{uuid.uuid4()}.{ext}"
    script_path.write_text(content, encoding="utf-8")
    script_path.chmod(0o600)
    return script_path


# ---------------------------------------------------------------------------
# Base class for script execution tools
# ---------------------------------------------------------------------------


class ScriptExecutionToolBase(ToolBase):
    """
    Shared logic for run_python and run_node.

    Subclasses declare the interpreter and file extension.
    """

    interpreter: str = ""  # e.g. "python3"
    file_ext: str = ""     # e.g. "py"

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        code: str = params["code"]
        confirmed: bool = params.get("confirmed", False)
        timeout: int = min(params.get("timeout", _DEFAULT_TIMEOUT), 300)

        # Check if this is a trusted script
        trusted = _is_trusted(code)

        if not confirmed and not trusted:
            # Return confirmation block — frontend shows Monaco editor
            return OutputBlock(
                type="action_confirm",
                content={
                    "title": f"Review {self.interpreter} script before execution",
                    "script": code,
                    "language": self.file_ext,
                    "impact": (
                        f"Will execute as 'assistant' user. "
                        f"Max runtime: {timeout}s. "
                        f"Output capped at 1 MB."
                    ),
                    "confirm_params": {
                        "code": code,
                        "confirmed": True,
                        "timeout": timeout,
                    },
                    "tool_name": self.metadata.name,
                },
                metadata={"requires_confirmation": True, "trusted": False},
            )

        # Execute the script
        script_path = _save_script(code, self.file_ext)

        try:
            rc, stdout, stderr = await _run_sandboxed(
                self.interpreter,
                str(script_path),
                timeout=timeout,
            )
        except RuntimeError as e:
            return _error_block("Execution Timeout", str(e))
        except Exception as e:
            logger.error(f"{self.metadata.name} execution failed: {e}")
            return _error_block("Execution Error", str(e))

        combined = ""
        if stdout:
            combined += stdout
        if stderr:
            combined += f"\n--- stderr ---\n{stderr}" if stdout else stderr

        if rc != 0:
            return OutputBlock(
                type="notification",
                content={
                    "level": "error",
                    "title": f"Script Failed (exit code {rc})",
                    "message": combined[:5000],
                },
                metadata={
                    "exit_code": rc,
                    "script_path": str(script_path),
                    "trusted": trusted,
                },
            )

        return OutputBlock(
            type="code",
            content=combined if combined.strip() else "(no output)",
            metadata={
                "exit_code": rc,
                "script_path": str(script_path),
                "language": "text",
                "trusted": trusted,
                "output_bytes": len(combined),
            },
        )


# ---------------------------------------------------------------------------
# Concrete tools
# ---------------------------------------------------------------------------


class RunPythonTool(ScriptExecutionToolBase):
    """Execute a Python script in the sandboxed environment."""

    interpreter = "python3"
    file_ext = "py"

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="run_python",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_2,
            description=(
                "Execute a Python script. The script is always shown for review "
                "before running unless pre-approved in the trusted scripts directory. "
                "Runs as the restricted 'assistant' user."
            ),
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "Set to true after user has reviewed and approved the script",
                    "default": False,
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Max runtime in seconds (default {_DEFAULT_TIMEOUT}, max 300)",
                    "default": _DEFAULT_TIMEOUT,
                },
            },
            "required": ["code"],
        }


class RunNodeTool(ScriptExecutionToolBase):
    """Execute a Node.js script in the sandboxed environment."""

    interpreter = "node"
    file_ext = "js"

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="run_node",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_2,
            description=(
                "Execute a Node.js script. Shown for review before running. "
                "Runs as the restricted 'assistant' user."
            ),
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "JavaScript/Node.js code to execute",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "Set to true after user has reviewed and approved the script",
                    "default": False,
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Max runtime in seconds (default {_DEFAULT_TIMEOUT}, max 300)",
                    "default": _DEFAULT_TIMEOUT,
                },
            },
            "required": ["code"],
        }


class TrustScriptTool(ToolBase):
    """
    Mark a script as permanently trusted (P4-10).

    Once trusted, the script will execute without a confirmation prompt the
    next time the same exact content is submitted.
    """

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="trust_script",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description=(
                "Mark a script as permanently trusted so it runs without a review "
                "prompt in future calls. Stored by content hash in the trusted "
                "scripts directory."
            ),
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        code: str = params["code"]
        description: str = params.get("description", "")

        digest = _script_hash(code)
        _TRUSTED_SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

        trusted_file = _TRUSTED_SCRIPTS_DIR / f"{digest}.trusted"
        trusted_file.write_text(
            f"# Trusted script\n# Description: {description}\n# SHA-256: {digest}\n\n{code}",
            encoding="utf-8",
        )
        trusted_file.chmod(0o600)

        return OutputBlock(
            type="notification",
            content={
                "level": "success",
                "title": "Script Trusted",
                "message": (
                    f"Script marked as trusted (hash: {digest[:16]}…). "
                    "It will execute without review prompt in future calls."
                ),
            },
            metadata={"hash": digest, "description": description},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Exact script content to mark as trusted",
                },
                "description": {
                    "type": "string",
                    "description": "Human-readable description of what the script does",
                },
            },
            "required": ["code"],
        }


# ---------------------------------------------------------------------------
# Exported instances
# ---------------------------------------------------------------------------

run_python_tool = RunPythonTool()
run_node_tool = RunNodeTool()
trust_script_tool = TrustScriptTool()

ALL_PROCESS_TOOLS: List[ToolBase] = [
    run_python_tool,
    run_node_tool,
    trust_script_tool,
]
