"""
Tests for Action Classifier
"""

import pytest

from backend.pipeline.action_classifier import (
    ActionClassifier,
    ToolCall,
    _TIER_1_TOOLS,
    _TIER_2_TOOLS,
    _TIER_3_TOOLS,
)
from backend.pipeline.models import ActionTier, ClassificationResult


class TestBaseClassification:
    """Test base tier lookup from tool name"""

    def setup_method(self):
        self.classifier = ActionClassifier()

    def test_tier_1_read_file(self):
        result = self.classifier.classify(ToolCall("read_file"))
        assert result.tier == ActionTier.TIER_1
        assert not result.requires_confirmation

    def test_tier_1_git_status(self):
        result = self.classifier.classify(ToolCall("git_status"))
        assert result.tier == ActionTier.TIER_1

    def test_tier_1_system_stats(self):
        result = self.classifier.classify(ToolCall("system_stats"))
        assert result.tier == ActionTier.TIER_1

    def test_tier_2_write_file(self):
        result = self.classifier.classify(ToolCall("write_file"))
        assert result.tier == ActionTier.TIER_2
        assert result.requires_confirmation
        assert result.confirmation_type == "soft"

    def test_tier_2_git_commit(self):
        result = self.classifier.classify(ToolCall("git_commit"))
        assert result.tier == ActionTier.TIER_2

    def test_tier_3_delete_file(self):
        result = self.classifier.classify(ToolCall("delete_file"))
        assert result.tier == ActionTier.TIER_3
        assert result.requires_confirmation
        assert result.confirmation_type == "explicit"

    def test_tier_3_git_push(self):
        result = self.classifier.classify(ToolCall("git_push"))
        assert result.tier == ActionTier.TIER_3

    def test_tier_3_stop_service(self):
        result = self.classifier.classify(ToolCall("stop_service"))
        assert result.tier == ActionTier.TIER_3

    def test_unknown_tool_defaults_to_tier_2(self):
        result = self.classifier.classify(ToolCall("my_custom_unknown_tool_xyz"))
        assert result.tier == ActionTier.TIER_2
        assert "unknown tool" in result.justification

    def test_result_has_justification(self):
        result = self.classifier.classify(ToolCall("read_file"))
        assert isinstance(result.justification, str)
        assert len(result.justification) > 0

    def test_result_has_confidence(self):
        result = self.classifier.classify(ToolCall("read_file"))
        assert 0.0 <= result.confidence <= 1.0

    def test_known_tools_have_high_confidence(self):
        result = self.classifier.classify(ToolCall("delete_file"))
        assert result.confidence >= 0.9

    def test_unknown_tools_have_lower_confidence(self):
        result = self.classifier.classify(ToolCall("totally_unknown_tool"))
        assert result.confidence < 0.9


class TestDangerousFlagModifier:
    """Test parameter analysis for dangerous flags"""

    def setup_method(self):
        self.classifier = ActionClassifier()

    def test_force_flag_upgrades_tier(self):
        result = self.classifier.classify(
            ToolCall("git_commit", params={"args": "--force"})
        )
        assert result.tier == ActionTier.TIER_3
        assert any("dangerous flag" in m for m in result.modifiers)

    def test_rf_flag_upgrades_tier(self):
        result = self.classifier.classify(
            ToolCall("run_python", params={"command": "rm -rf /tmp/test"})
        )
        assert result.tier == ActionTier.TIER_3

    def test_hard_flag_upgrades_tier(self):
        result = self.classifier.classify(
            ToolCall("git_status", params={"args": "--hard"})
        )
        assert result.tier == ActionTier.TIER_3

    def test_no_dangerous_flags_keeps_tier(self):
        result = self.classifier.classify(
            ToolCall("write_file", params={"path": "/tmp/test.txt", "content": "hello"})
        )
        assert result.tier == ActionTier.TIER_2

    def test_yes_flag_upgrades_tier(self):
        result = self.classifier.classify(
            ToolCall("run_python", params={"args": "-y --do-something"})
        )
        assert result.tier == ActionTier.TIER_3


class TestSystemPathModifier:
    """Test path analysis for system directories"""

    def setup_method(self):
        self.classifier = ActionClassifier()

    def test_etc_path_upgrades_to_tier_3(self):
        result = self.classifier.classify(
            ToolCall("read_file", params={"path": "/etc/passwd"})
        )
        assert result.tier == ActionTier.TIER_3
        assert any("system path" in m for m in result.modifiers)

    def test_usr_path_upgrades_tier(self):
        result = self.classifier.classify(
            ToolCall("write_file", params={"path": "/usr/local/bin/script.sh"})
        )
        assert result.tier == ActionTier.TIER_3

    def test_home_path_does_not_upgrade(self):
        result = self.classifier.classify(
            ToolCall("read_file", params={"path": "/home/user/documents/file.txt"})
        )
        assert result.tier == ActionTier.TIER_1

    def test_tmp_path_does_not_upgrade(self):
        result = self.classifier.classify(
            ToolCall("write_file", params={"path": "/tmp/output.txt"})
        )
        assert result.tier == ActionTier.TIER_2

    def test_list_params_with_system_path(self):
        result = self.classifier.classify(
            ToolCall("read_file", params={"paths": ["/etc/hosts", "/tmp/test"]})
        )
        assert result.tier == ActionTier.TIER_3


class TestSQLModifier:
    """Test SQL keyword detection"""

    def setup_method(self):
        self.classifier = ActionClassifier()

    def test_drop_table_upgrades_tier(self):
        result = self.classifier.classify(
            ToolCall("execute_sqlite", params={"query": "DROP TABLE users"})
        )
        assert result.tier == ActionTier.TIER_3
        assert any("destructive SQL" in m for m in result.modifiers)

    def test_truncate_upgrades_tier(self):
        result = self.classifier.classify(
            ToolCall("query_sqlite", params={"query": "TRUNCATE logs"})
        )
        assert result.tier == ActionTier.TIER_3

    def test_delete_from_upgrades_tier(self):
        result = self.classifier.classify(
            ToolCall("execute_sqlite", params={"query": "DELETE FROM sessions WHERE expired=1"})
        )
        assert result.tier == ActionTier.TIER_3

    def test_select_does_not_upgrade(self):
        result = self.classifier.classify(
            ToolCall("query_sqlite", params={"query": "SELECT * FROM users LIMIT 10"})
        )
        assert result.tier == ActionTier.TIER_1


class TestContextModifiers:
    """Test context-aware tier adjustments"""

    def setup_method(self):
        self.classifier = ActionClassifier()

    def test_first_time_action_adds_modifier(self):
        result = self.classifier.classify(ToolCall("write_file"))
        assert any("first-time" in m for m in result.modifiers)

    def test_repeated_tier2_action_reduces_tier(self):
        # Record enough approvals to trigger the modifier
        for _ in range(3):
            self.classifier.record_approval("write_file")

        result = self.classifier.classify(ToolCall("write_file"))
        # Should be reduced from TIER_2 to TIER_1
        assert result.tier == ActionTier.TIER_1
        assert any("repeated action" in m for m in result.modifiers)

    def test_repeated_tier3_action_not_reduced(self):
        # Tier 3 should not be auto-reduced (delete_file is tier 3)
        for _ in range(5):
            self.classifier.record_approval("delete_file")

        result = self.classifier.classify(ToolCall("delete_file"))
        assert result.tier == ActionTier.TIER_3

    def test_trusted_scripts_dir_reduces_tier(self):
        classifier = ActionClassifier(trusted_scripts_dir="/home/user/assistant-scripts/trusted")
        result = classifier.classify(ToolCall(
            "run_python",
            params={"path": "/home/user/assistant-scripts/trusted/analyze.py"}
        ))
        # run_python is TIER_2, trusted dir → TIER_1
        assert result.tier == ActionTier.TIER_1
        assert any("trusted directory" in m for m in result.modifiers)

    def test_force_tier_context_override(self):
        result = self.classifier.classify(
            ToolCall("read_file", context={"force_tier": "reversible_write"})
        )
        assert result.tier == ActionTier.TIER_2
        assert any("forced tier" in m for m in result.modifiers)


class TestTierOverride:
    """Test user-set tier overrides"""

    def setup_method(self):
        self.classifier = ActionClassifier()

    def test_set_and_get_override(self):
        self.classifier.set_tier_override("custom_tool", ActionTier.TIER_1)
        assert self.classifier.get_tier_override("custom_tool") == ActionTier.TIER_1

    def test_override_applied_in_classify(self):
        self.classifier.set_tier_override("write_file", ActionTier.TIER_1)
        result = self.classifier.classify(ToolCall("write_file"))
        assert result.tier == ActionTier.TIER_1
        assert "user override" in result.justification

    def test_no_override_returns_none(self):
        assert self.classifier.get_tier_override("read_file") is None


class TestLookupTableCompleteness:
    """Sanity checks on the lookup tables"""

    def test_no_tool_in_multiple_tiers(self):
        overlap_1_2 = _TIER_1_TOOLS & _TIER_2_TOOLS
        overlap_1_3 = _TIER_1_TOOLS & _TIER_3_TOOLS
        overlap_2_3 = _TIER_2_TOOLS & _TIER_3_TOOLS
        assert not overlap_1_2, f"Overlap between Tier 1 and 2: {overlap_1_2}"
        assert not overlap_1_3, f"Overlap between Tier 1 and 3: {overlap_1_3}"
        assert not overlap_2_3, f"Overlap between Tier 2 and 3: {overlap_2_3}"

    def test_lookup_tables_are_nonempty(self):
        assert len(_TIER_1_TOOLS) > 5
        assert len(_TIER_2_TOOLS) > 5
        assert len(_TIER_3_TOOLS) > 5
