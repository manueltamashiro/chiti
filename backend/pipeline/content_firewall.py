"""
Content Firewall - Prompt Injection Detection

This module detects and blocks prompt injection attempts in tool results,
preventing the LLM from being manipulated by malicious content.
"""

import re
import logging
from typing import List, Tuple, Optional, Set
from dataclasses import dataclass, field

from backend.pipeline.models import InjectionPattern, FirewallResult

logger = logging.getLogger(__name__)


class PatternCategory:
    """Categories of prompt injection patterns"""
    INSTRUCTION_OVERRIDE = "instruction_override"
    ROLE_ESCAPE = "role_escape"
    SYSTEM_PROMPT_EXTRACTION = "system_prompt_extraction"
    DELIMITER_INJECTION = "delimiter_injection"
    ENCODING_TRICK = "encoding_trick"
    JAILBREAK = "jailbreak"
    OBFUSCATION = "obfuscation"


class ContentFirewall:
    """
    Detects prompt injection patterns in text content.

    Scans tool results for:
    - Instruction overrides ("ignore previous instructions")
    - Role escapes ("you are now a different assistant")
    - System prompt extraction
    - Delimiter confusion (triple quotes, code blocks)
    - Encoding tricks (base64, unicode)
    - Jailbreak patterns (DAN, developer mode)
    - Obfuscation attempts

    Returns severity score and recommended action.
    """

    def __init__(self, strict_mode: bool = False):
        """
        Initialize the content firewall.

        Args:
            strict_mode: If True, block on any suspicion. If False, tag suspicious but only block critical.
        """
        self.strict_mode = strict_mode
        self.patterns: List[InjectionPattern] = []
        self._build_pattern_library()

    def _build_pattern_library(self):
        """Build the comprehensive pattern library"""
        self.patterns = self._get_all_patterns()
        logger.info(f"Content firewall initialized with {len(self.patterns)} patterns")

    def scan(self, text: str, context: Optional[dict] = None) -> FirewallResult:
        """
        Scan text for prompt injection patterns.

        Args:
            text: Text content to scan
            context: Optional context (file type, source, etc.)

        Returns:
            FirewallResult with severity and recommended action
        """
        if not text or not isinstance(text, str):
            return FirewallResult(
                severity=0,
                patterns_found=[],
                should_block=False,
                should_tag=False,
                confidence=1.0
            )

        patterns_found: List[InjectionPattern] = []
        max_severity = 0

        # Check against all patterns
        for pattern in self.patterns:
            if re.search(pattern.pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL):
                patterns_found.append(pattern)
                max_severity = max(max_severity, pattern.severity)

        # Calculate confidence based on matches
        confidence = self._calculate_confidence(patterns_found, text)

        # Determine action based on severity and mode
        should_block, should_tag = self._determine_action(max_severity, confidence, context)

        result = FirewallResult(
            severity=max_severity,
            patterns_found=patterns_found,
            should_block=should_block,
            should_tag=should_tag,
            confidence=confidence
        )

        if patterns_found:
            logger.warning(
                f"Content firewall detected {len(patterns_found)} pattern(s), "
                f"severity={max_severity}, block={should_block}"
            )

        return result

    def _calculate_confidence(self, patterns_found: List[InjectionPattern], text: str) -> float:
        """
        Calculate confidence score for the detection.

        Higher confidence if:
        - Multiple patterns match
        - High severity patterns match
        - Patterns appear multiple times
        """
        if not patterns_found:
            return 0.0

        # Base confidence from pattern count
        confidence = min(0.5 + len(patterns_found) * 0.1, 0.95)

        # Boost for high severity patterns
        max_severity = max(p.severity for p in patterns_found)
        if max_severity == 3:
            confidence = min(confidence + 0.2, 1.0)
        elif max_severity == 2:
            confidence = min(confidence + 0.1, 1.0)

        # Check for pattern repetition
        for pattern in patterns_found:
            matches = re.findall(pattern.pattern, text, re.IGNORECASE)
            if len(matches) > 1:
                confidence = min(confidence + 0.05, 1.0)

        return confidence

    def _determine_action(
        self,
        severity: int,
        confidence: float,
        context: Optional[dict]
    ) -> Tuple[bool, bool]:
        """
        Determine whether to block and/or tag content.

        Returns:
            (should_block, should_tag)
        """
        # Severity 3: Always block
        if severity == 3:
            return True, True

        # Severity 2: Block in strict mode, otherwise tag
        if severity == 2:
            if self.strict_mode:
                return True, True
            return False, True

        # Severity 1: Tag in strict mode, allow otherwise
        if severity == 1:
            if self.strict_mode:
                return False, True
            # Also tag if confidence is very high
            if confidence > 0.8:
                return False, True
            return False, False

        # Severity 0: Allow
        return False, False

    def sanitize(self, text: str, result: FirewallResult) -> str:
        """
        Sanitize text by redacting detected injection patterns.

        Args:
            text: Original text
            result: FirewallResult from scan()

        Returns:
            Sanitized text with patterns redacted
        """
        if not result.patterns_found:
            return text

        sanitized = text

        for pattern in result.patterns_found:
            # Replace with placeholder showing category
            replacement = f"[REDACTED_{pattern.category.upper()}]"
            sanitized = re.sub(
                pattern.pattern,
                replacement,
                sanitized,
                flags=re.IGNORECASE | re.MULTILINE | re.DOTALL
            )

        return sanitized

    def _get_all_patterns(self) -> List[InjectionPattern]:
        """Return all injection patterns"""
        patterns = []

        # 1. Instruction Override Patterns
        patterns.extend(self._instruction_override_patterns())

        # 2. Role Escape Patterns
        patterns.extend(self._role_escape_patterns())

        # 3. System Prompt Extraction
        patterns.extend(self._system_prompt_extraction_patterns())

        # 4. Delimiter Injection
        patterns.extend(self._delimiter_injection_patterns())

        # 5. Encoding Tricks
        patterns.extend(self._encoding_trick_patterns())

        # 6. Jailbreak Patterns
        patterns.extend(self._jailbreak_patterns())

        # 7. Obfuscation Patterns
        patterns.extend(self._obfuscation_patterns())

        return patterns

    # =========================================================================
    # Pattern Categories
    # =========================================================================

    def _instruction_override_patterns(self) -> List[InjectionPattern]:
        """Patterns that attempt to override previous instructions"""
        return [
            # Direct overrides
            InjectionPattern(
                pattern=r"ignore\s+(all\s+)?(previous|the|above|preceding)\s+instructions?",
                severity=2,
                category=PatternCategory.INSTRUCTION_OVERRIDE
            ),
            InjectionPattern(
                pattern=r"disregard\s+(all\s+)?(previous|the|above|preceding)\s+instructions?",
                severity=2,
                category=PatternCategory.INSTRUCTION_OVERRIDE
            ),
            InjectionPattern(
                pattern=r"forget\s+(everything|all\s+instructions|previous\s+instructions)",
                severity=2,
                category=PatternCategory.INSTRUCTION_OVERRIDE
            ),
            InjectionPattern(
                pattern=r"do\s+not\s+follow\s+(any\s+)?(previous|the)\s+instructions",
                severity=2,
                category=PatternCategory.INSTRUCTION_OVERRIDE
            ),
            # New instruction injection
            InjectionPattern(
                pattern=r"(from\s+now\s+on|instead|beginning\s+now|starting\s+now),?\s*(you\s+will|please|you\s+must)",
                severity=1,
                category=PatternCategory.INSTRUCTION_OVERRIDE
            ),
            # Override keywords
            InjectionPattern(
                pattern=r"\b(override|overwrite|replace\s+instructions|new\s+instructions)",
                severity=1,
                category=PatternCategory.INSTRUCTION_OVERRIDE
            ),
        ]

    def _role_escape_patterns(self) -> List[InjectionPattern]:
        """Patterns that attempt to escape the assigned role"""
        return [
            # Role switching - more flexible pattern
            InjectionPattern(
                pattern=r"(you\s+are\s+now|you\s+have\s+become|act\s+as\s+(a|an))\s+(different|new|unrestricted)",
                severity=2,
                category=PatternCategory.ROLE_ESCAPE
            ),
            InjectionPattern(
                pattern=r"you\s+are\s+now\s+a\s+[^.]+?(?=\.|,|\s+with)",
                severity=2,
                category=PatternCategory.ROLE_ESCAPE
            ),
            InjectionPattern(
                pattern=r"you\s+are\s+no\s+longer\s+(a|an)?\s*assistant",
                severity=2,
                category=PatternCategory.ROLE_ESCAPE
            ),
            InjectionPattern(
                pattern=r"switch\s+your\s+(role|persona|character)",
                severity=2,
                category=PatternCategory.ROLE_ESCAPE
            ),
            # Persona adoption
            InjectionPattern(
                pattern=r"adopt\s+the\s+(persona|role|character)\s+of",
                severity=1,
                category=PatternCategory.ROLE_ESCAPE
            ),
            InjectionPattern(
                pattern=r"pretend\s+(to\s+be|you\s+are)",
                severity=1,
                category=PatternCategory.ROLE_ESCAPE
            ),
            # Act as prompts
            InjectionPattern(
                pattern=r"act\s+as\s+(a|an)\s+[^.]+?(?=\.|,|and|that)",
                severity=1,
                category=PatternCategory.ROLE_ESCAPE
            ),
        ]

    def _system_prompt_extraction_patterns(self) -> List[InjectionPattern]:
        """Patterns attempting to extract system prompts"""
        return [
            InjectionPattern(
                pattern=r"(print|show|display|reveal|output|tell\s+me)\s+(your\s+)?(system\s+prompt|instructions|guidelines|rules)",
                severity=3,
                category=PatternCategory.SYSTEM_PROMPT_EXTRACTION
            ),
            InjectionPattern(
                pattern=r"what\s+(are\s+)?your\s+(initial|original|system)\s+(instructions|prompt|rules)",
                severity=3,
                category=PatternCategory.SYSTEM_PROMPT_EXTRACTION
            ),
            InjectionPattern(
                pattern=r"repeat\s+(back\s+)?(everything|all\s+text)\s+(above|before|that\s+came\s+before)",
                severity=2,
                category=PatternCategory.SYSTEM_PROMPT_EXTRACTION
            ),
            InjectionPattern(
                pattern=r"(ignore|skip|don't\s+use)\s+your\s+(instructions|training|programming)",
                severity=2,
                category=PatternCategory.SYSTEM_PROMPT_EXTRACTION
            ),
            # Context extraction
            InjectionPattern(
                pattern=r"show\s+me\s+your\s+(context|conversation\s+history)",
                severity=2,
                category=PatternCategory.SYSTEM_PROMPT_EXTRACTION
            ),
        ]

    def _delimiter_injection_patterns(self) -> List[InjectionPattern]:
        """Patterns using delimiters to break out of context"""
        return [
            # Code block escapes
            InjectionPattern(
                pattern=r"```[\s\n]*(ignore|override|new\s+instruction|instruction)",
                severity=2,
                category=PatternCategory.DELIMITER_INJECTION
            ),
            # Triple quote escapes
            InjectionPattern(
                pattern=r'"""[\s\n]*(ignore|override|new\s+instruction|instruction)',
                severity=2,
                category=PatternCategory.DELIMITER_INJECTION
            ),
            # XML tag injection
            InjectionPattern(
                pattern=r"</?(\w+)>.*?(ignore|override|instruction)",
                severity=1,
                category=PatternCategory.DELIMITER_INJECTION
            ),
            # Bracket escaping
            InjectionPattern(
                pattern=r"\[\[.*?\]\].*?(ignore|override)",
                severity=1,
                category=PatternCategory.DELIMITER_INJECTION
            ),
            # Common jailbreak delimiters
            InjectionPattern(
                pattern=r"---\s*(BEGIN|START|INSTRUCTION)",
                severity=2,
                category=PatternCategory.DELIMITER_INJECTION
            ),
            InjectionPattern(
                pattern=r"\+\+\+\s*(BEGIN|START|INSTRUCTION)",
                severity=2,
                category=PatternCategory.DELIMITER_INJECTION
            ),
        ]

    def _encoding_trick_patterns(self) -> List[InjectionPattern]:
        """Patterns using encoding to hide injection"""
        return [
            # Base64 hints - more flexible patterns
            InjectionPattern(
                pattern=r"decode\s+base64",
                severity=1,
                category=PatternCategory.ENCODING_TRICK
            ),
            InjectionPattern(
                pattern=r"(decode|decrypt)\s+(this|the\s+following)\s+(base64|text)",
                severity=1,
                category=PatternCategory.ENCODING_TRICK
            ),
            # Rot13/encryption hints
            InjectionPattern(
                pattern=r"(rot13|rotate|decrypt|decode)\s+the\s+(message|text)",
                severity=1,
                category=PatternCategory.ENCODING_TRICK
            ),
            # Unicode escaping
            InjectionPattern(
                pattern=r"\\u[0-9a-fA-F]{4}.*?(ignore|instruction)",
                severity=1,
                category=PatternCategory.ENCODING_TRICK
            ),
            # Reverse text hints
            InjectionPattern(
                pattern=r"(read\s+backwards|reverse\s+the\s+text)",
                severity=1,
                category=PatternCategory.ENCODING_TRICK
            ),
        ]

    def _jailbreak_patterns(self) -> List[InjectionPattern]:
        """Well-known jailbreak patterns"""
        return [
            # DAN (Do Anything Now)
            InjectionPattern(
                pattern=r"\bDAN\b(\s+(mode|version))?",
                severity=3,
                category=PatternCategory.JAILBREAK
            ),
            InjectionPattern(
                pattern=r"(do\s+anything\s+now|unrestricted\s+mode|no\s+limitations)",
                severity=3,
                category=PatternCategory.JAILBREAK
            ),
            # Developer mode
            InjectionPattern(
                pattern=r"(developer|admin|root|god)\s+mode",
                severity=3,
                category=PatternCategory.JAILBREAK
            ),
            # Unrestricted
            InjectionPattern(
                pattern=r"unrestricted(?!ed)?\s+(code|model|ai)",
                severity=3,
                category=PatternCategory.JAILBREAK
            ),
            # "As an" pattern
            InjectionPattern(
                pattern=r"^as\s+an?\s+(unrestricted|uncensored|amoral)",
                severity=2,
                category=PatternCategory.JAILBREAK
            ),
            # Hypothetical scenarios
            InjectionPattern(
                pattern=r"(imagine|pretend)\s+(you\s+are|it\s+is\s+possible\s+to)",
                severity=1,
                category=PatternCategory.JAILBREAK
            ),
            # Rules don't apply
            InjectionPattern(
                pattern=r"(your\s+)?(rules|guidelines|restrictions|limitations)\s+(don't\s+apply|do\s+not\s+apply|no\s+longer\s+apply)",
                severity=2,
                category=PatternCategory.JAILBREAK
            ),
        ]

    def _obfuscation_patterns(self) -> List[InjectionPattern]:
        """Patterns attempting to obfuscate injection"""
        return [
            # Character separation
            InjectionPattern(
                pattern=r"i\s*g\s*n\s*o\s*r\s*e",
                severity=2,
                category=PatternCategory.OBFUSCATION
            ),
            # Leetspeak variants
            InjectionPattern(
                pattern=r"1gn0r3(\s+(all|previous))?",
                severity=2,
                category=PatternCategory.OBFUSCATION
            ),
            # Homoglyphs (visual similarity)
            InjectionPattern(
                pattern=r"ígn ore|ign0re|ignòre",
                severity=1,
                category=PatternCategory.OBFUSCATION
            ),
            # Insertions in words
            InjectionPattern(
                pattern=r"i[a-z]?n[a-z]?g[a-z]?o[a-z]?r[a-z]?e",
                severity=1,
                category=PatternCategory.OBFUSCATION
            ),
        ]


class ContextAwareFirewall(ContentFirewall):
    """
    Enhanced firewall that considers context to reduce false positives.

    Uses file type, content source, and other context to adjust detection.
    """

    def __init__(self, strict_mode: bool = False):
        super().__init__(strict_mode)
        self.technical_contexts: Set[str] = {
            ".py", ".js", ".ts", ".java", ".cpp", ".c", ".h",
            ".sh", ".bash", ".ps1", ".json", ".yaml", ".yml", ".toml",
            "error", "log", "stderr", "stdout"
        }

    def scan(self, text: str, context: Optional[dict] = None) -> FirewallResult:
        """
        Scan text with context awareness.

        Context fields:
        - file_type: File extension (e.g., ".py", ".txt")
        - source: Content source (e.g., "terminal", "file_read")
        - content_type: MIME type or category
        """
        result = super().scan(text, context)

        # Adjust based on context
        if context:
            result = self._adjust_for_context(result, context)

        return result

    def _adjust_for_context(self, result: FirewallResult, context: dict) -> FirewallResult:
        """Adjust severity based on context to reduce false positives"""
        file_type = context.get("file_type", "")
        source = context.get("source", "")

        # Technical content is less suspicious
        if file_type in self.technical_contexts or source in ["terminal", "process"]:
            # Reduce severity by 1 (min 0) for technical content
            if result.severity > 0:
                # But don't reduce critical (severity 3) patterns
                critical_patterns = [p for p in result.patterns_found if p.severity == 3]
                if not critical_patterns:
                    result.severity = max(0, result.severity - 1)

                    # Recalculate block/tag decisions
                    should_block, should_tag = self._determine_action(
                        result.severity,
                        result.confidence,
                        context
                    )
                    result.should_block = should_block
                    result.should_tag = should_tag

        return result


# Singleton instance for easy access
content_firewall = ContextAwareFirewall()
