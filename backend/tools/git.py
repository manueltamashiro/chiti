"""
Git Tool Suite

Tier 1 (no confirmation):
  git_status, git_log, git_diff

Tier 2 (soft confirmation):
  git_add, git_commit

Tier 3 (explicit confirmation):
  git_push, git_create_branch, git_checkout

All tools use asyncio.create_subprocess_exec (no shell=True) to prevent
command injection. Output is capped at 50 KB.
"""

import asyncio
import logging
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

_MAX_OUTPUT_BYTES = 50_000
_DEFAULT_TIMEOUT = 30  # seconds


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _run_git(
    *args: str,
    cwd: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Tuple[int, str, str]:
    """
    Run a git sub-command safely (no shell).

    Returns:
        (returncode, stdout, stderr)

    Raises:
        RuntimeError: If the command times out.
    """
    cmd = ["git", *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"git {args[0]} timed out after {timeout}s")

    stdout = stdout_b.decode(errors="replace")[:_MAX_OUTPUT_BYTES]
    stderr = stderr_b.decode(errors="replace")[:_MAX_OUTPUT_BYTES]
    return proc.returncode, stdout, stderr


def _resolve_repo(path: Optional[str]) -> Optional[str]:
    """Expand and resolve repo path, or return None to use CWD."""
    if path:
        return str(Path(path).expanduser().resolve())
    return None


def _error_block(title: str, message: str) -> OutputBlock:
    return OutputBlock(
        type="notification",
        content={"level": "error", "title": title, "message": message},
    )


def _success_block(title: str, message: str, metadata: Optional[dict] = None) -> OutputBlock:
    return OutputBlock(
        type="notification",
        content={"level": "success", "title": title, "message": message},
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Tier 1 — Read-only
# ---------------------------------------------------------------------------


class GitStatusTool(ToolBase):
    """Show working tree status."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="git_status",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Show the working tree status (modified, staged, untracked files).",
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        repo = _resolve_repo(params.get("repo"))

        rc, stdout, stderr = await _run_git("status", "--porcelain", "-b", cwd=repo)

        if rc != 0:
            return _error_block("Git Status Failed", stderr or stdout)

        if not stdout.strip():
            return OutputBlock(
                type="text",
                content="Working tree clean — nothing to commit.",
                metadata={"repo": repo},
            )

        lines = stdout.strip().splitlines()
        branch_line = ""
        rows = []
        for line in lines:
            if line.startswith("##"):
                branch_line = line[3:]
            else:
                status_code = line[:2]
                fname = line[3:]
                status_label = _git_status_label(status_code)
                rows.append([status_code.strip(), status_label, fname])

        content_parts = []
        if branch_line:
            content_parts.append(f"Branch: {branch_line}\n")
        if rows:
            header = f"{'XY':<4} {'Status':<20} {'File'}"
            content_parts.append(header)
            content_parts.append("-" * 60)
            for code, label, fname in rows:
                content_parts.append(f"{code:<4} {label:<20} {fname}")

        return OutputBlock(
            type="code",
            content="\n".join(content_parts),
            metadata={"repo": repo, "language": "text", "changed_count": len(rows)},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Path to the git repository (default: current directory)",
                },
            },
            "required": [],
        }


class GitLogTool(ToolBase):
    """Show recent commit log."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="git_log",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Show recent git commit history as a table.",
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        repo = _resolve_repo(params.get("repo"))
        limit: int = min(params.get("limit", 20), 100)
        branch: Optional[str] = params.get("branch")

        fmt = "%H\x1f%h\x1f%an\x1f%ae\x1f%ai\x1f%s"
        cmd_args = ["log", f"--pretty=format:{fmt}", f"-n{limit}"]
        if branch:
            cmd_args.append(branch)

        rc, stdout, stderr = await _run_git(*cmd_args, cwd=repo)

        if rc != 0:
            return _error_block("Git Log Failed", stderr or stdout)

        if not stdout.strip():
            return OutputBlock(
                type="text",
                content="No commits found.",
                metadata={"repo": repo},
            )

        columns = ["Hash", "Short", "Author", "Date", "Message"]
        rows = []
        for line in stdout.strip().splitlines():
            parts = line.split("\x1f")
            if len(parts) >= 6:
                full_hash, short, author, _email, date, subject = parts[:6]
                rows.append([full_hash, short, author, date[:10], subject])

        return OutputBlock(
            type="table",
            content={
                "columns": columns,
                "rows": rows,
                "metadata": {"repo": repo, "commit_count": len(rows)},
            },
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository path"},
                "limit": {
                    "type": "integer",
                    "description": "Maximum commits to show (default 20, max 100)",
                    "default": 20,
                },
                "branch": {
                    "type": "string",
                    "description": "Branch or ref to show log for (default: current branch)",
                },
            },
            "required": [],
        }


class GitDiffTool(ToolBase):
    """Show changes between commits, index, or working tree."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="git_diff",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description=(
                "Show a unified diff of changes. By default shows unstaged changes. "
                "Pass staged=true to see staged changes."
            ),
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        repo = _resolve_repo(params.get("repo"))
        staged: bool = params.get("staged", False)
        path_filter: Optional[str] = params.get("path")
        ref: Optional[str] = params.get("ref")

        cmd_args = ["diff"]
        if staged:
            cmd_args.append("--cached")
        if ref:
            cmd_args.append(ref)
        cmd_args.append("--")
        if path_filter:
            cmd_args.append(path_filter)

        rc, stdout, stderr = await _run_git(*cmd_args, cwd=repo)

        if rc != 0:
            return _error_block("Git Diff Failed", stderr or stdout)

        if not stdout.strip():
            label = "staged" if staged else "unstaged"
            return OutputBlock(
                type="text",
                content=f"No {label} changes.",
                metadata={"repo": repo, "staged": staged},
            )

        return OutputBlock(
            type="code",
            content=stdout,
            metadata={"repo": repo, "staged": staged, "language": "diff"},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository path"},
                "staged": {
                    "type": "boolean",
                    "description": "Show staged (index) changes (default: false = unstaged)",
                    "default": False,
                },
                "path": {
                    "type": "string",
                    "description": "Limit diff to this file or directory",
                },
                "ref": {
                    "type": "string",
                    "description": "Compare against this ref (e.g. 'HEAD~1', 'main')",
                },
            },
            "required": [],
        }


# ---------------------------------------------------------------------------
# Tier 2 — Soft confirmation
# ---------------------------------------------------------------------------


class GitAddTool(ToolBase):
    """Stage file(s) for the next commit."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="git_add",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_2,
            description=(
                "Stage files for the next commit (git add). "
                "Pass specific paths or '.' to stage everything."
            ),
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        repo = _resolve_repo(params.get("repo"))
        paths: List[str] = params.get("paths", ["."])

        if not paths:
            paths = ["."]

        rc, stdout, stderr = await _run_git("add", "--", *paths, cwd=repo)

        if rc != 0:
            return _error_block("Git Add Failed", stderr or stdout)

        return _success_block(
            "Files Staged",
            f"Staged: {', '.join(paths)}",
            {"repo": repo, "paths": paths},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository path"},
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File/directory paths to stage (default: ['.'])",
                    "default": ["."],
                },
            },
            "required": [],
        }


class GitCommitTool(ToolBase):
    """Create a commit with a message."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="git_commit",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_2,
            description=(
                "Create a git commit with the given message. "
                "Only commits already-staged changes."
            ),
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        repo = _resolve_repo(params.get("repo"))
        message: str = params["message"]
        author: Optional[str] = params.get("author")

        cmd_args = ["commit", "-m", message]
        if author:
            cmd_args += ["--author", author]

        rc, stdout, stderr = await _run_git(*cmd_args, cwd=repo)

        if rc != 0:
            return _error_block("Git Commit Failed", stderr or stdout)

        # Extract commit hash from output
        commit_hash = ""
        for line in stdout.splitlines():
            if "]" in line and "[" in line:
                commit_hash = line.split("]")[0].split("[")[-1].split()[-1]
                break

        return _success_block(
            "Commit Created",
            f"{commit_hash}: {message}",
            {"repo": repo, "hash": commit_hash, "message": message},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository path"},
                "message": {"type": "string", "description": "Commit message"},
                "author": {
                    "type": "string",
                    "description": "Override author in 'Name <email>' format",
                },
            },
            "required": ["message"],
        }


# ---------------------------------------------------------------------------
# Tier 3 — Explicit confirmation
# ---------------------------------------------------------------------------


class GitPushTool(ToolBase):
    """Push commits to a remote repository."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="git_push",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description=(
                "Push commits to a remote. Requires explicit user confirmation. "
                "Force push is not supported for safety."
            ),
            requires_network=True,
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        repo = _resolve_repo(params.get("repo"))
        remote: str = params.get("remote", "origin")
        branch: Optional[str] = params.get("branch")

        cmd_args = ["push", remote]
        if branch:
            cmd_args.append(branch)

        rc, stdout, stderr = await _run_git(*cmd_args, cwd=repo, timeout=60)

        if rc != 0:
            return _error_block("Git Push Failed", stderr or stdout)

        output = (stdout + stderr).strip()
        return _success_block(
            "Push Successful",
            output or f"Pushed to {remote}",
            {"repo": repo, "remote": remote, "branch": branch},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository path"},
                "remote": {
                    "type": "string",
                    "description": "Remote name (default: origin)",
                    "default": "origin",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch to push (default: current branch)",
                },
            },
            "required": [],
        }


class GitCreateBranchTool(ToolBase):
    """Create and optionally check out a new branch."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="git_create_branch",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description="Create a new git branch, optionally switching to it.",
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        repo = _resolve_repo(params.get("repo"))
        branch: str = params["branch"]
        checkout: bool = params.get("checkout", True)
        base: Optional[str] = params.get("base")

        cmd_args = ["checkout", "-b", branch] if checkout else ["branch", branch]
        if base:
            cmd_args.append(base)

        rc, stdout, stderr = await _run_git(*cmd_args, cwd=repo)

        if rc != 0:
            return _error_block("Branch Creation Failed", stderr or stdout)

        action = "Created and checked out" if checkout else "Created"
        return _success_block(
            "Branch Created",
            f"{action} branch '{branch}'" + (f" from {base}" if base else ""),
            {"repo": repo, "branch": branch, "checkout": checkout},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository path"},
                "branch": {"type": "string", "description": "New branch name"},
                "checkout": {
                    "type": "boolean",
                    "description": "Switch to new branch after creating (default: true)",
                    "default": True,
                },
                "base": {
                    "type": "string",
                    "description": "Base ref to branch from (default: current HEAD)",
                },
            },
            "required": ["branch"],
        }


class GitCheckoutTool(ToolBase):
    """Switch branches or restore working tree files."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="git_checkout",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description=(
                "Switch to an existing branch or restore files. "
                "This modifies the working tree — use with care."
            ),
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        repo = _resolve_repo(params.get("repo"))
        target: str = params["target"]

        rc, stdout, stderr = await _run_git("checkout", target, cwd=repo)

        if rc != 0:
            return _error_block("Checkout Failed", stderr or stdout)

        return _success_block(
            "Checkout Successful",
            f"Switched to '{target}'",
            {"repo": repo, "target": target},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository path"},
                "target": {
                    "type": "string",
                    "description": "Branch name, tag, or commit hash to check out",
                },
            },
            "required": ["target"],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_status_label(code: str) -> str:
    labels = {
        "M ": "Modified (staged)",   " M": "Modified",      "MM": "Modified (both)",
        "A ": "Added",                "D ": "Deleted (staged)", " D": "Deleted",
        "R ": "Renamed",              "C ": "Copied",
        "??": "Untracked",            "!!": "Ignored",
        "UU": "Conflict",
    }
    return labels.get(code, "Changed")


# ---------------------------------------------------------------------------
# Exported instances
# ---------------------------------------------------------------------------

git_status_tool = GitStatusTool()
git_log_tool = GitLogTool()
git_diff_tool = GitDiffTool()
git_add_tool = GitAddTool()
git_commit_tool = GitCommitTool()
git_push_tool = GitPushTool()
git_create_branch_tool = GitCreateBranchTool()
git_checkout_tool = GitCheckoutTool()

ALL_GIT_TOOLS: List[ToolBase] = [
    git_status_tool,
    git_log_tool,
    git_diff_tool,
    git_add_tool,
    git_commit_tool,
    git_push_tool,
    git_create_branch_tool,
    git_checkout_tool,
]
