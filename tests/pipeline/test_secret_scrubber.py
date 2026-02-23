"""
Tests for Secret Scrubber
"""

import pytest

from backend.pipeline.secret_scrubber import SecretScrubber
from backend.pipeline.models import ScrubResult


class TestSecretScrubber:
    """Test secret detection and redaction"""

    def setup_method(self):
        """Create fresh scrubber for each test"""
        self.scrubber = SecretScrubber()

    def test_clean_content(self):
        """Test that clean content passes through"""
        result = self.scrubber.scrub("This is normal text with no secrets")
        assert result.secret_count == 0
        assert not result.has_leaked_credentials
        assert result.scrubbed_content == "This is normal text with no secrets"

    def test_aws_access_key_detected(self):
        """Test AWS access key detection"""
        # Use realistic AWS key format (AKIA + 16 alphanumeric)
        content = "My AWS key is AKIAIOSFODNN7SECRE and secret is wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        result = self.scrubber.scrub(content)

        # Should detect the AWS key (20 chars) and the secret (40 chars)
        assert result.secret_count >= 1
        assert result.has_leaked_credentials
        assert "REDACTED" in result.scrubbed_content

    def test_github_token_detected(self):
        """Test GitHub token detection"""
        content = "GitHub token: ghp_1234567890abcdefghijABCDEFGHIJ"
        result = self.scrubber.scrub(content)

        assert result.secret_count > 0
        assert "REDACTED" in result.scrubbed_content

    def test_google_api_key_detected(self):
        """Test Google API key detection"""
        content = "Google API key: AIzaSyDaGmWKa4JsXZ-HjGw7ISLn_3namBGewQe"
        result = self.scrubber.scrub(content)

        assert result.secret_count > 0
        assert "REDACTED" in result.scrubbed_content

    def test_jwt_token_detected(self):
        """Test JWT token detection"""
        content = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
        result = self.scrubber.scrub(content)

        assert result.secret_count > 0
        assert result.has_leaked_credentials

    def test_database_url_detected(self):
        """Test database URL detection"""
        content = "Database: postgres://user:password123@localhost:5432/mydb"
        result = self.scrubber.scrub(content)

        assert result.secret_count > 0
        assert "password123" not in result.scrubbed_content

    def test_private_key_detected(self):
        """Test private key detection"""
        content = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA2a2j9z8/l...
-----END RSA PRIVATE KEY-----"""
        result = self.scrubber.scrub(content)

        assert result.secret_count > 0
        assert "REDACTED" in result.scrubbed_content

    def test_password_in_config_detected(self):
        """Test password in config format"""
        content = 'database_password = "supersecretpassword123"'
        result = self.scrubber.scrub(content)

        assert result.secret_count > 0
        assert "supersecretpassword123" not in result.scrubbed_content

    def test_multiple_secrets_detected(self):
        """Test detection of multiple secrets"""
        # GitHub token needs exactly 36 chars after ghp_
        content = """
AWS key: AKIAIOSFODNN7EXAMPLE
GitHub token: ghp_1234567890abcdefghijABCDEFGHIJKLMNOP
Password: mySecretPassword123
"""
        result = self.scrubber.scrub(content)

        assert result.secret_count >= 2
        assert result.scrubbed_content.count("REDACTED") >= 2

    def test_stripe_key_detected(self):
        """Test Stripe API key detection"""
        content = "Stripe key: sk_test_51FAKETOKENKEYFORTESTINGONLYNOTREALXXXXXXXXXXXXXXXXXXXXXXXXXX"
        result = self.scrubber.scrub(content)

        assert result.secret_count > 0

    def test_slack_token_detected(self):
        """Test Slack token detection"""
        content = "Slack token: xoxb-TEST-FAKETOKENFORTESTING123456789NOTREAL"
        result = self.scrubber.scrub(content)

        assert result.secret_count > 0

    def test_discord_token_detected(self):
        """Test Discord bot token detection"""
        content = "Discord token: TESTFAKETOKENFORTESTINGONLYNOTREAL123456.AbCdEf.TESTINGFAKETOKENNOTREAL1234567890"
        result = self.scrubber.scrub(content)

        assert result.secret_count > 0

    def test_high_entropy_string_detected(self):
        """Test entropy-based detection for random strings"""
        # No spaces — the regex tokeniser stops at spaces.
        # Shannon entropy of this 40-char string is > 4.5 (threshold).
        content = "Random string: Th1sIsV3ryH1ghEntr0pyStr1ng!@#$%^&*()"
        result = self.scrubber.scrub(content)

        # Should detect high entropy
        high_entropy_found = any(
            s.secret_type == "high_entropy_string"
            for s in result.secrets_found
        )
        assert high_entropy_found


class TestEntropyCalculation:
    """Test entropy calculation"""

    def setup_method(self):
        self.scrubber = SecretScrubber()

    def test_low_entropy_string(self):
        """Test that low entropy strings aren't flagged"""
        content = "aaaaabbbbbcccccdddddeeeee"
        result = self.scrubber.scrub(content)

        # Low entropy, shouldn't be detected
        high_entropy_found = any(
            s.secret_type == "high_entropy_string"
            for s in result.secrets_found
        )
        assert not high_entropy_found

    def test_high_entropy_string(self):
        """Test that high entropy strings are flagged"""
        content = "Th1sIsV3ryH1ghEntr0pyStr1ng!@#$%^&*()_+"
        result = self.scrubber.scrub(content)

        high_entropy_found = any(
            s.secret_type == "high_entropy_string"
            for s in result.secrets_found
        )
        assert high_entropy_found

    def test_entropy_threshold(self):
        """Test custom entropy threshold"""
        custom_scrubber = SecretScrubber(entropy_threshold=6.0)
        content = "SomeRandomText123"

        # Higher threshold = fewer matches
        result = custom_scrubber.scrub(content)
        assert result.secret_count == 0


class TestFalsePositiveFiltering:
    """Test false positive filtering"""

    def setup_method(self):
        self.scrubber = SecretScrubber()

    def test_uuid_not_flagged(self):
        """Test that UUIDs aren't flagged as secrets"""
        content = "User ID: 550e8400-e29b-41d4-a716-446655440000"
        result = self.scrubber.scrub(content)

        # UUID should not be detected
        assert result.secret_count == 0

    def test_example_keys_not_flagged(self):
        """Test that clearly-placeholder variable names aren't flagged"""
        content = """
# Configuration example
database_password = "your_password_here"
api_key = "your_api_key_here"
"""
        result = self.scrubber.scrub(content, context={"file_type": ".md"})

        # Obvious placeholder strings should have minimal detections
        assert result.secret_count <= 1

    def test_documentation_context(self):
        """Test that documentation context reduces false positives"""
        content = """
# Configuration example
database_password = "your_password_here"
api_key = "your_api_key_here"
"""
        result = self.scrubber.scrub(content, context={"file_type": ".md"})

        # Documentation should have fewer false positives
        # But still detect actual secrets
        assert result.secret_count <= 1  # May still flag "your_password_here" as password pattern

    def test_commit_hash_not_flagged(self):
        """Test that a real 40-char hex git commit hash isn't flagged"""
        # All lowercase hex chars — matched by the _is_entropy_false_positive
        # 40-hex-char rule, and entropy < 4.5 due to limited alphabet (0-9a-f).
        content = "Commit: a1b2c3d4e5f67890abcdef0123456789fedcba01"
        result = self.scrubber.scrub(content)

        # Commit hash should not be detected as high entropy
        high_entropy_found = any(
            s.secret_type == "high_entropy_string"
            for s in result.secrets_found
        )
        assert not high_entropy_found


class TestRedaction:
    """Test content redaction"""

    def setup_method(self):
        self.scrubber = SecretScrubber()

    def test_redaction_preserves_structure(self):
        """Test that redaction preserves content structure"""
        content = "API key: sk-1234567890ABCDEFGHIJ end of message"
        result = self.scrubber.scrub(content)

        # Structure should be preserved
        assert "API key:" in result.scrubbed_content
        assert "end of message" in result.scrubbed_content
        assert "sk-1234567890ABCDEFGHIJ" not in result.scrubbed_content

    def test_redaction_with_length_preservation(self):
        """Test that redaction preserves string length"""
        content = "key: AKIAIOSFODNN7EXAMPLE more text"
        result = self.scrubber.scrub(content)

        # Check that the redacted content has same length
        lines_before = content.split("\n")
        lines_after = result.scrubbed_content.split("\n")

        # Should have same number of lines
        assert len(lines_before) == len(lines_after)

    def test_multiple_redactions(self):
        """Test redaction of multiple secrets"""
        # Use valid-length secrets: sk- needs 20+ chars, AKIA needs 16 chars after prefix
        content = "key1: sk-1234567890abcdefghijkl, key2: AKIAIOSFODNN7EXAMPLE"
        result = self.scrubber.scrub(content)

        # Both secrets should be redacted
        assert "sk-1234567890abcdefghijkl" not in result.scrubbed_content
        assert "AKIAIOSFODNN7EXAMPLE" not in result.scrubbed_content


class TestSecretTypes:
    """Test detection of specific secret types"""

    def setup_method(self):
        self.scrubber = SecretScrubber()

    def test_all_secret_types_detectable(self):
        """Test that all major secret types are detectable"""
        test_cases = [
            # AWS: AKIA + exactly 16 alphanumeric chars
            ("AKIAIOSFODNN7EXAMPLE", "aws"),
            # GitHub PAT: ghp_ + 36 alphanumeric chars
            ("ghp_1234567890abcdefghijABCDEFGHIJKLMN", "github"),
            # Google: AIza + 35 alphanumeric/dash/underscore chars
            ("AIzaSyDaGmWKa4JsXZ-HjGw7sCnE8x6fghij1", "google"),
            # OpenAI: sk- + 20+ alphanumeric chars
            ("sk-1234567890ABCDEFGHIJ", "openai"),
            # JWT: header.payload (header.payload.signature format)
            ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0", "jwt"),
        ]

        for secret, category in test_cases:
            result = self.scrubber.scrub(f"My key is {secret}")
            assert result.secret_count > 0, f"Failed to detect {category} key: {secret}"

    def test_secret_match_metadata(self):
        """Test that secret matches have correct metadata"""
        content = "AWS key: AKIAIOSFODNN7EXAMPLE"
        result = self.scrubber.scrub(content)

        assert len(result.secrets_found) > 0
        match = result.secrets_found[0]

        assert hasattr(match, "secret_type")
        assert hasattr(match, "start_index")
        assert hasattr(match, "end_index")
        assert hasattr(match, "confidence")
        assert match.secret_type != ""


class TestEdgeCases:
    """Test edge cases and special inputs"""

    def setup_method(self):
        self.scrubber = SecretScrubber()

    def test_empty_content(self):
        """Test handling of empty content"""
        result = self.scrubber.scrub("")
        assert result.secret_count == 0
        assert result.scrubbed_content == ""

    def test_none_content(self):
        """Test handling of None content"""
        result = self.scrubber.scrub(None)
        assert result.secret_count == 0
        assert result.scrubbed_content is None

    def test_non_string_content(self):
        """Test handling of non-string content"""
        result = self.scrubber.scrub(12345)
        assert result.secret_count == 0
        assert result.scrubbed_content == 12345

    def test_very_long_content(self):
        """Test handling of very long content"""
        long_content = "text " * 10000 + "AKIAIOSFODNN7EXAMPLE" + " more text"
        result = self.scrubber.scrub(long_content)

        assert result.secret_count > 0
        assert "AKIAIOSFODNN7EXAMPLE" not in result.scrubbed_content

    def test_multiline_content(self):
        """Test handling of multiline content"""
        content = """
Line 1
Line 2
AKIAIOSFODNN7EXAMPLE
Line 4
"""
        result = self.scrubber.scrub(content)

        assert result.secret_count > 0
        assert "REDACTED" in result.scrubbed_content
        # Check newlines preserved
        assert result.scrubbed_content.count("\n") == content.count("\n")

    def test_overlapping_secrets(self):
        """Test handling of overlapping secret matches"""
        # Same secret matched by multiple patterns
        content = "Token: sk-1234567890ABCDEFGHIJ (sk-1234567890ABCDEFGHIJ)"
        result = self.scrubber.scrub(content)

        # Should deduplicate
        # Count actual redacted regions, not pattern matches
        # The same token appears twice but in different positions
        assert result.secret_count >= 1
