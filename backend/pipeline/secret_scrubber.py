"""
Secret Scrubber - Credential and API Key Detection/Redaction

This module detects and removes secrets from tool results, preventing
credentials from being stored in conversation history or displayed to users.
"""

import re
import math
import logging
from typing import List, Optional, Tuple, Set
from dataclasses import dataclass, field

from backend.pipeline.models import SecretPattern, SecretMatch, ScrubResult

logger = logging.getLogger(__name__)


class SecretScrubber:
    """
    Detects and redacts secrets in text content.

    Uses both regex patterns and entropy analysis to detect:
    - API keys (AWS, GitHub, Google, OpenAI, Anthropic, Stripe)
    - JWT tokens and bearer tokens
    - Database URLs
    - Private keys and certificates
    - Passwords in configs
    - OAuth tokens
    - High-entropy strings (potential secrets)
    """

    def __init__(self, entropy_threshold: float = 4.5, min_entropy_length: int = 32):
        """
        Initialize the secret scrubber.

        Args:
            entropy_threshold: Shannon entropy threshold for high-entropy detection
            min_entropy_length: Minimum string length for entropy check
        """
        self.entropy_threshold = entropy_threshold
        self.min_entropy_length = min_entropy_length
        self.patterns: List[SecretPattern] = []
        self._build_pattern_library()

        # Known false positives (UUIDs, file hashes, etc.)
        self._false_positive_indicators: Set[str] = {
            "sha256", "sha512", "md5", "hash", "commit", "uuid",
            "example", "test", "demo", "sample", "placeholder",
            "xxx", "***", "****", "xxx-xxx-xxx"
        }

    def _build_pattern_library(self):
        """Build the comprehensive secret pattern library"""
        self.patterns = self._get_all_patterns()
        logger.info(f"Secret scrubber initialized with {len(self.patterns)} patterns")

    def scrub(self, content: str, context: Optional[dict] = None) -> ScrubResult:
        """
        Scrub secrets from content.

        Args:
            content: Text content to scrub (can be string or other type)
            context: Optional context (file type, source, etc.)

        Returns:
            ScrubResult with scrubbed content and list of secrets found
        """
        if not content or not isinstance(content, str):
            return ScrubResult(
                scrubbed_content=content,
                secrets_found=[],
                secret_count=0,
                has_leaked_credentials=False
            )

        secrets_found: List[SecretMatch] = []

        # 1. Regex-based detection
        regex_matches = self._detect_with_regex(content, context)
        secrets_found.extend(regex_matches)

        # 2. Entropy-based detection (for things not caught by regex)
        entropy_matches = self._detect_with_entropy(content)
        secrets_found.extend(entropy_matches)

        # 3. Remove duplicates and sort by position
        secrets_found = self._deduplicate_matches(secrets_found)
        secrets_found.sort(key=lambda m: m.start_index)

        # 4. Redact secrets
        scrubbed_content = self._redact_content(content, secrets_found)

        return ScrubResult(
            scrubbed_content=scrubbed_content,
            secrets_found=secrets_found,
            secret_count=len(secrets_found),
            has_leaked_credentials=len(secrets_found) > 0
        )

    def _detect_with_regex(self, content: str, context: Optional[dict]) -> List[SecretMatch]:
        """Detect secrets using regex patterns"""
        matches = []

        for pattern in self.patterns:
            for match in re.finditer(pattern.pattern, content):
                # Skip if it looks like a false positive
                if self._is_false_positive(match.group(), context):
                    continue

                secret_match = SecretMatch(
                    secret_type=pattern.name,
                    start_index=match.start(),
                    end_index=match.end(),
                    matched_text=match.group(),
                    confidence=pattern.confidence
                )
                matches.append(secret_match)

        return matches

    def _detect_with_entropy(self, content: str) -> List[SecretMatch]:
        """Detect high-entropy strings that might be secrets"""
        matches = []

        # Find all strings that look like potential secrets
        # Include more characters to catch things like API keys with symbols
        candidates = re.finditer(
            r'[A-Za-z0-9/_+.\-!@#$%^&*()=~`]{20,}',
            content
        )

        for match in candidates:
            text = match.group()

            # Skip if too short
            if len(text) < self.min_entropy_length:
                continue

            # Skip if looks like a false positive
            if self._is_entropy_false_positive(text):
                continue

            # Calculate entropy
            entropy = self._calculate_entropy(text)

            if entropy >= self.entropy_threshold:
                secret_match = SecretMatch(
                    secret_type="high_entropy_string",
                    start_index=match.start(),
                    end_index=match.end(),
                    matched_text=text,
                    confidence="medium"
                )
                matches.append(secret_match)

        return matches

    def _is_false_positive(self, text: str, context: Optional[dict]) -> bool:
        """Check if match is a known false positive"""
        text_lower = text.lower()

        # Check for false positive indicators (more specific)
        # Only filter "example" when it's clearly a placeholder
        false_positive_phrases = [
            "example_key", "sample_key", "test_key", "dummy_key",
            "your_key_here", "xxx-xxx-xxx", "example.com",
            "placeholder", "replace_with", "your_api_key"
        ]
        for phrase in false_positive_phrases:
            if phrase in text_lower:
                return True

        # Check context
        if context:
            file_type = context.get("file_type", "")
            if file_type in [".md", ".rst", ".txt"]:
                # Documentation files often have example keys
                if any(word in text_lower for word in ["your_", "example:", "sample:", "test:", "xxx"]):
                    return True

        return False

    def _is_entropy_false_positive(self, text: str) -> bool:
        """Check if high-entropy string is a false positive"""
        text_lower = text.lower()

        # UUIDs
        if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', text_lower):
            return True

        # Git commit hashes (40 hex chars)
        if re.match(r'^[0-9a-f]{40}$', text_lower):
            return True

        # Short commit hashes (7-8 hex chars)
        if re.match(r'^[0-9a-f]{7,8}$', text_lower):
            return True

        # File hashes
        if any(hash_type in text_lower for hash_type in ["sha256:", "sha512:", "md5:"]):
            return True

        # Very repetitive patterns (likely not secrets)
        if len(set(text)) < len(text) * 0.3:
            return True

        # Check for example/sample only when clearly a placeholder
        placeholder_phrases = ["example_key", "sample_key", "test_key", "your_key_here"]
        if any(phrase in text_lower for phrase in placeholder_phrases):
            return True

        return False

    def _calculate_entropy(self, text: str) -> float:
        """
        Calculate Shannon entropy of a string.

        Higher entropy = more random = more likely to be a secret.
        """
        if not text:
            return 0.0

        # Count character frequencies
        counts = {}
        for char in text:
            counts[char] = counts.get(char, 0) + 1

        # Calculate Shannon entropy
        entropy = 0.0
        text_len = len(text)

        for count in counts.values():
            probability = count / text_len
            entropy -= probability * math.log2(probability)

        return entropy

    def _deduplicate_matches(self, matches: List[SecretMatch]) -> List[SecretMatch]:
        """Remove overlapping duplicate matches"""
        if not matches:
            return []

        # Sort by start index
        matches.sort(key=lambda m: m.start_index)

        # Filter overlaps
        deduplicated = []
        for match in matches:
            # Check if overlaps with any existing match
            overlaps = False
            for existing in deduplicated:
                if (match.start_index >= existing.start_index and
                    match.start_index < existing.end_index):
                    overlaps = True
                    break

            if not overlaps:
                deduplicated.append(match)

        return deduplicated

    def _redact_content(self, content: str, secrets: List[SecretMatch]) -> str:
        """Redact secrets from content, preserving structure"""
        if not secrets:
            return content

        # Work backwards to avoid index shifting
        scrubbed = content
        for secret in reversed(secrets):
            # Create redaction placeholder
            placeholder = f"[REDACTED_{secret.secret_type.upper()}]"

            # Preserve length for better formatting
            redaction = placeholder.ljust(len(secret.matched_text), "_")

            # Replace
            scrubbed = (
                scrubbed[:secret.start_index] +
                redaction +
                scrubbed[secret.end_index:]
            )

        return scrubbed

    def _get_all_patterns(self) -> List[SecretPattern]:
        """Return all secret detection patterns"""
        patterns = []

        # AWS Credentials
        patterns.extend(self._aws_patterns())

        # GitHub Tokens
        patterns.extend(self._github_patterns())

        # Google Credentials
        patterns.extend(self._google_patterns())

        # OpenAI / Anthropic
        patterns.extend(self._openai_patterns())

        # Stripe
        patterns.extend(self._stripe_patterns())

        # JWT Tokens
        patterns.extend(self._jwt_patterns())

        # Database URLs
        patterns.extend(self._database_patterns())

        # Private Keys
        patterns.extend(self._private_key_patterns())

        # Passwords in configs
        patterns.extend(self._password_patterns())

        # OAuth tokens
        patterns.extend(self._oauth_patterns())

        # API Keys (general)
        patterns.extend(self._api_key_patterns())

        return patterns

    # =========================================================================
    # Pattern Categories
    # =========================================================================

    def _aws_patterns(self) -> List[SecretPattern]:
        """AWS access keys and secrets"""
        return [
            # AWS Access Key ID (AKIA + 16 alphanumeric)
            SecretPattern(
                pattern=r"\bAKIA[A-Za-z0-9]{16}\b",
                name="aws_access_key_id",
                confidence="high"
            ),
            # AWS Secret Access Key pattern (40 chars base64-like)
            SecretPattern(
                pattern=r"\b[A-Za-z0-9/+=]{40}\b",
                name="aws_secret_access_key",
                confidence="low"  # Low confidence alone, high with context
            ),
        ]

    def _github_patterns(self) -> List[SecretPattern]:
        """GitHub personal access tokens and OAuth tokens"""
        return [
            # GitHub Personal Access Token
            SecretPattern(
                pattern=r"(?i)github.{0,20}token[\s]*['\"]?[A-Za-z0-9_]{36,}['\"]?",
                name="github_token",
                confidence="high"
            ),
            # GitHub OAuth
            SecretPattern(
                pattern=r"ghp_[A-Za-z0-9]{36}",
                name="github_pat",
                confidence="high"
            ),
            # GitHub OAuth App
            SecretPattern(
                pattern=r"gho_[A-Za-z0-9]{36}",
                name="github_oauth",
                confidence="high"
            ),
        ]

    def _google_patterns(self) -> List[SecretPattern]:
        """Google API keys and OAuth tokens"""
        return [
            # Google API Key
            SecretPattern(
                pattern=r"AIza[0-9A-Za-z\-_]{35}",
                name="google_api_key",
                confidence="high"
            ),
            # Google OAuth Access Token
            SecretPattern(
                pattern=r"ya29\.[A-Za-z0-9\-_]{100,}",
                name="google_oauth_token",
                confidence="high"
            ),
            # Google Cloud Service Account
            SecretPattern(
                pattern=r'"type":\s*"service_account"[^}]*"private_key":\s*"-----BEGIN PRIVATE KEY-----',
                name="google_service_account",
                confidence="high"
            ),
        ]

    def _openai_patterns(self) -> List[SecretPattern]:
        """OpenAI API keys"""
        return [
            SecretPattern(
                pattern=r"(?i)openai.{0,20}api.{0,20}key[\s]*['\"]?sk-[A-Za-z0-9]{20,}['\"]?",
                name="openai_api_key",
                confidence="high"
            ),
            SecretPattern(
                pattern=r"sk-[A-Za-z0-9]{20,}",
                name="openai_key",
                confidence="medium"
            ),
        ]

    def _stripe_patterns(self) -> List[SecretPattern]:
        """Stripe API keys"""
        return [
            SecretPattern(
                pattern=r"(?i)stripe.{0,20}(api|secret|publishable).{0,20}key[\s]*['\"]?sk_(live|test)_[0-9a-zA-Z]{24,}",
                name="stripe_api_key",
                confidence="high"
            ),
            SecretPattern(
                pattern=r"sk_(live|test)_[0-9a-zA-Z]{24,}",
                name="stripe_key",
                confidence="medium"
            ),
        ]

    def _jwt_patterns(self) -> List[SecretPattern]:
        """JWT tokens and bearer tokens"""
        return [
            # JWT Token
            SecretPattern(
                pattern=r"eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+",
                name="jwt_token",
                confidence="high"
            ),
            # Bearer token
            SecretPattern(
                pattern=r"(?i)bearer[\s]+[A-Za-z0-9\-._~+/]+=*",
                name="bearer_token",
                confidence="medium"
            ),
        ]

    def _database_patterns(self) -> List[SecretPattern]:
        """Database connection strings"""
        return [
            # PostgreSQL
            SecretPattern(
                pattern=r"postgres://[^:]+:[^@]+@",
                name="postgresql_url",
                confidence="high"
            ),
            # MySQL
            SecretPattern(
                pattern=r"mysql://[^:]+:[^@]+@",
                name="mysql_url",
                confidence="high"
            ),
            # MongoDB
            SecretPattern(
                pattern=r"mongodb://[^:]+:[^@]+@",
                name="mongodb_url",
                confidence="high"
            ),
            # Redis
            SecretPattern(
                pattern=r"redis://:[^@]+@",
                name="redis_url",
                confidence="high"
            ),
            # Generic database URL
            SecretPattern(
                pattern=r"(?i)(database|db).{0,20}(url|connection)[\s]*=[\s]*['\"]?[^'\"]+://[^:]+:[^@]+@",
                name="database_url",
                confidence="high"
            ),
        ]

    def _private_key_patterns(self) -> List[SecretPattern]:
        """Private keys and certificates"""
        return [
            SecretPattern(
                pattern=r"-----BEGIN[\s]+(RSA\s+)?PRIVATE\s+KEY-----",
                name="private_key",
                confidence="high"
            ),
            SecretPattern(
                pattern=r"-----BEGIN[\s]+EC\s+PRIVATE\s+KEY-----",
                name="ec_private_key",
                confidence="high"
            ),
            SecretPattern(
                pattern=r"-----BEGIN[\s]+OPENSSH\s+PRIVATE\s+KEY-----",
                name="openssh_private_key",
                confidence="high"
            ),
            SecretPattern(
                pattern=r"-----BEGIN[\s]+CERTIFICATE-----",
                name="certificate",
                confidence="medium"
            ),
            SecretPattern(
                pattern=r"-----BEGIN[\s]+PGP\s+(PRIVATE\s+KEY|MESSAGE)-----",
                name="pgp_key",
                confidence="high"
            ),
        ]

    def _password_patterns(self) -> List[SecretPattern]:
        """Passwords in configuration files"""
        return [
            SecretPattern(
                pattern=r'(?i)(password|passwd|pwd)[\s]*=[\s]*["\'][^"\']+["\']',
                name="password",
                confidence="medium"
            ),
            SecretPattern(
                pattern=r'(?i)(api|secret|token)[\s]*key?[\s]*=[\s]*["\'][^"\']{20,}["\']',
                name="api_key",
                confidence="low"
            ),
        ]

    def _oauth_patterns(self) -> List[SecretPattern]:
        """OAuth tokens and secrets"""
        return [
            SecretPattern(
                pattern=r"(?i)oauth.{0,20}(token|secret)[\s]*[:=][\s]*[A-Za-z0-9\-_]{20,}",
                name="oauth_token",
                confidence="medium"
            ),
            SecretPattern(
                pattern=r"(?i)access_token[\s]*[:=][\s]*[A-Za-z0-9\-._~+/]{20,}",
                name="access_token",
                confidence="medium"
            ),
            SecretPattern(
                pattern=r"(?i)refresh_token[\s]*[:=][\s]*[A-Za-z0-9\-._~+/]{20,}",
                name="refresh_token",
                confidence="medium"
            ),
            SecretPattern(
                pattern=r"(?i)client_secret[\s]*[:=][\s]*[A-Za-z0-9\-._~+/]{20,}",
                name="client_secret",
                confidence="high"
            ),
        ]

    def _api_key_patterns(self) -> List[SecretPattern]:
        """Generic API key patterns"""
        return [
            # x-api-key pattern
            SecretPattern(
                pattern=r'(?i)x-api-key[\s]*:[\s]*["\']?[A-Za-z0-9\-_]{20,}["\']?',
                name="x_api_key",
                confidence="medium"
            ),
            # Authorization header with basic auth
            SecretPattern(
                pattern=r"(?i)authorization[\s]*:[\s]*basic\s+[A-Za-z0-9+/=]{20,}",
                name="basic_auth",
                confidence="high"
            ),
            # Slack token
            SecretPattern(
                pattern=r"xox[baprs]-[A-Za-z0-9\-]{10,}",
                name="slack_token",
                confidence="high"
            ),
            # Discord bot token
            SecretPattern(
                pattern=r"[MN][A-Za-z0-9]{23}\.[A-Za-z0-9\-_]{6}\.[A-Za-z0-9\-_]{27}",
                name="discord_bot_token",
                confidence="high"
            ),
        ]


# Singleton instance for easy access
secret_scrubber = SecretScrubber()
