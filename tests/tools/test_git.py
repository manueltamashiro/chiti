"""
Tests for Git Tool Suite (P4-06)

Most tests mock _run_git to avoid requiring a real git installation.
Integration-style tests that call real git are guarded with pytest.mark.skipif.
"""

import shutil
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from backend.tools.git import (
    GitStatusTool,
    GitLogTool,
    GitDiffTool,
    GitAddTool,
    GitCommitTool,
    GitPushTool,
    GitCreateBranchTool,
    GitCheckoutTool,
    ALL_GIT_TOOLS,
    _git_status_label,
)
from backend.pipeline.models import ActionTier, CapabilityType

GIT_AVAILABLE = shutil.which("git") is not None


# ---------------------------------------------------------------------------
# Metadata tests
# ---------------------------------------------------------------------------


class TestGitToolMetadata:
    def test_all_tools_have_names(self):
        for tool in ALL_GIT_TOOLS:
            assert tool.metadata.name

    def test_all_tools_have_descriptions(self):
        for tool in ALL_GIT_TOOLS:
            assert tool.metadata.description

    def test_all_tools_have_valid_tiers(self):
        for tool in ALL_GIT_TOOLS:
            assert isinstance(tool.metadata.tier, ActionTier)

    def test_tier_assignments(self):
        tiers = {t.metadata.name: t.metadata.tier for t in ALL_GIT_TOOLS}
        assert tiers["git_status"] == ActionTier.TIER_1
        assert tiers["git_log"] == ActionTier.TIER_1
        assert tiers["git_diff"] == ActionTier.TIER_1
        assert tiers["git_add"] == ActionTier.TIER_2
        assert tiers["git_commit"] == ActionTier.TIER_2
        assert tiers["git_push"] == ActionTier.TIER_3
        assert tiers["git_create_branch"] == ActionTier.TIER_3
        assert tiers["git_checkout"] == ActionTier.TIER_3

    def test_all_schemas_valid(self):
        for tool in ALL_GIT_TOOLS:
            schema = tool.get_parameter_schema()
            assert isinstance(schema, dict)
            assert schema.get("type") == "object"

    def test_capability_type_is_tool(self):
        for tool in ALL_GIT_TOOLS:
            assert tool.metadata.type == CapabilityType.TOOL


# ---------------------------------------------------------------------------
# GitStatusTool
# ---------------------------------------------------------------------------


class TestGitStatusTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = GitStatusTool()

    async def test_clean_working_tree(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "## main...origin/main\n", "")
            result = await self.tool.execute({})
        # With only the branch line and no changed files, changed_count == 0
        assert result.metadata["changed_count"] == 0

    async def test_modified_files_shown(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (
                0,
                "## main\n M backend/tools/git.py\nM  backend/pipeline/models.py\n",
                "",
            )
            result = await self.tool.execute({})
        assert result.type == "code"
        assert "git.py" in result.content

    async def test_failure_returns_error(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (128, "", "fatal: not a git repository")
            result = await self.tool.execute({})
        assert result.type == "notification"
        assert result.content["level"] == "error"

    async def test_repo_path_passed_to_run(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "## main\n", "")
            await self.tool.execute({"repo": "/some/path"})
        _, kwargs = mock.call_args
        assert kwargs.get("cwd") is not None


# ---------------------------------------------------------------------------
# GitLogTool
# ---------------------------------------------------------------------------


class TestGitLogTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = GitLogTool()

    async def test_returns_table_of_commits(self):
        fake_commit = "abc123def456abc\x1fabc123\x1fAlice\x1falice@example.com\x1f2024-01-01 10:00:00 +0000\x1fFix bug"
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, fake_commit, "")
            result = await self.tool.execute({})
        assert result.type == "table"
        assert len(result.content["rows"]) == 1
        assert "Fix bug" in result.content["rows"][0]

    async def test_empty_log_returns_text(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "", "")
            result = await self.tool.execute({})
        assert result.type == "text"

    async def test_limit_capped_at_100(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "", "")
            await self.tool.execute({"limit": 9999})
        args, kwargs = mock.call_args
        # The -n arg should be capped at 100
        assert any("-n100" in str(a) for a in args)

    async def test_git_log_failure(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (128, "", "fatal: not a git repo")
            result = await self.tool.execute({})
        assert result.content["level"] == "error"


# ---------------------------------------------------------------------------
# GitDiffTool
# ---------------------------------------------------------------------------


class TestGitDiffTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = GitDiffTool()

    async def test_returns_diff_block(self):
        fake_diff = "diff --git a/file.py b/file.py\n-old\n+new"
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, fake_diff, "")
            result = await self.tool.execute({})
        assert result.type == "code"
        assert result.metadata["language"] == "diff"

    async def test_no_changes_returns_text(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "", "")
            result = await self.tool.execute({})
        assert result.type == "text"

    async def test_staged_flag_passed(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "", "")
            await self.tool.execute({"staged": True})
        args, _ = mock.call_args
        assert "--cached" in args

    async def test_failure_returns_error(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (1, "", "error: bad revision")
            result = await self.tool.execute({"ref": "nonexistent"})
        assert result.content["level"] == "error"


# ---------------------------------------------------------------------------
# GitAddTool
# ---------------------------------------------------------------------------


class TestGitAddTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = GitAddTool()

    async def test_success_returns_notification(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "", "")
            result = await self.tool.execute({"paths": ["file.py"]})
        assert result.content["level"] == "success"

    async def test_default_paths_is_dot(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "", "")
            await self.tool.execute({})
        args, _ = mock.call_args
        assert "." in args

    async def test_failure_returns_error(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (1, "", "error: pathspec 'ghost.py' did not match")
            result = await self.tool.execute({"paths": ["ghost.py"]})
        assert result.content["level"] == "error"


# ---------------------------------------------------------------------------
# GitCommitTool
# ---------------------------------------------------------------------------


class TestGitCommitTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = GitCommitTool()

    async def test_success_returns_notification(self):
        stdout = "[main abc1234] Add feature\n 1 file changed"
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, stdout, "")
            result = await self.tool.execute({"message": "Add feature"})
        assert result.content["level"] == "success"
        assert "Add feature" in result.content["message"]

    async def test_commit_message_in_args(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "[main abc] msg\n", "")
            await self.tool.execute({"message": "My commit"})
        args, _ = mock.call_args
        assert "My commit" in args

    async def test_nothing_to_commit_returns_error(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (1, "", "nothing to commit")
            result = await self.tool.execute({"message": "Empty"})
        assert result.content["level"] == "error"

    def test_message_is_required(self):
        schema = self.tool.get_parameter_schema()
        assert "message" in schema["required"]


# ---------------------------------------------------------------------------
# GitPushTool
# ---------------------------------------------------------------------------


class TestGitPushTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = GitPushTool()

    async def test_success_returns_notification(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "", "To github.com:user/repo\n * [new branch]")
            result = await self.tool.execute({})
        assert result.content["level"] == "success"

    async def test_failure_returns_error(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (1, "", "error: failed to push")
            result = await self.tool.execute({})
        assert result.content["level"] == "error"

    async def test_uses_origin_by_default(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "", "")
            await self.tool.execute({})
        args, _ = mock.call_args
        assert "origin" in args

    def test_push_is_tier_3(self):
        assert self.tool.metadata.tier == ActionTier.TIER_3

    def test_push_requires_network(self):
        assert self.tool.metadata.requires_network is True


# ---------------------------------------------------------------------------
# GitCreateBranchTool
# ---------------------------------------------------------------------------


class TestGitCreateBranchTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = GitCreateBranchTool()

    async def test_creates_and_checks_out(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "", "Switched to a new branch 'feature'")
            result = await self.tool.execute({"branch": "feature"})
        assert result.content["level"] == "success"

    async def test_create_only_no_checkout(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "", "")
            await self.tool.execute({"branch": "feature", "checkout": False})
        args, _ = mock.call_args
        assert "branch" in args
        assert "-b" not in args

    async def test_failure_returns_error(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (1, "", "fatal: branch already exists")
            result = await self.tool.execute({"branch": "existing"})
        assert result.content["level"] == "error"

    def test_branch_is_required(self):
        assert "branch" in self.tool.get_parameter_schema()["required"]


# ---------------------------------------------------------------------------
# GitCheckoutTool
# ---------------------------------------------------------------------------


class TestGitCheckoutTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = GitCheckoutTool()

    async def test_checkout_success(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "", "Switched to branch 'main'")
            result = await self.tool.execute({"target": "main"})
        assert result.content["level"] == "success"

    async def test_checkout_failure(self):
        with patch("backend.tools.git._run_git", new_callable=AsyncMock) as mock:
            mock.return_value = (1, "", "error: pathspec 'nope' did not match")
            result = await self.tool.execute({"target": "nope"})
        assert result.content["level"] == "error"

    def test_target_is_required(self):
        assert "target" in self.tool.get_parameter_schema()["required"]


# ---------------------------------------------------------------------------
# _git_status_label helper
# ---------------------------------------------------------------------------


class TestGitStatusLabel:
    def test_modified_staged(self):
        assert "staged" in _git_status_label("M ").lower()

    def test_untracked(self):
        assert "untracked" in _git_status_label("??").lower()

    def test_added(self):
        assert "added" in _git_status_label("A ").lower()

    def test_unknown_code(self):
        # Unknown codes return a generic label
        label = _git_status_label("ZZ")
        assert isinstance(label, str)
        assert len(label) > 0


# ---------------------------------------------------------------------------
# Integration tests (require git)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not GIT_AVAILABLE, reason="git not installed")
class TestGitStatusIntegration:
    """Run real git status in a temp repo."""

    async def test_status_in_new_repo(self, tmp_path):
        import subprocess
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_path), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_path), check=True, capture_output=True,
        )

        tool = GitStatusTool()
        result = await tool.execute({"repo": str(tmp_path)})
        # A brand-new repo with no commits shows as clean or initial branch
        assert result.type in ("text", "code")
