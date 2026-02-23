"""
Heuristic query classifier that routes queries to either a local Ollama model
or the cloud Claude API — without making any LLM calls itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Words / phrases that indicate high complexity (cloud-worthy)
_COMPLEXITY_KEYWORDS = re.compile(
    r"\b("
    r"write|implement|create|build|generate|refactor|debug|fix|optimize"
    r"|explain why|compare|analyse|analyze|design|architecture"
    r"|step[- ]by[- ]step|walk me through|walk through"
    r"|in detail|elaborate|comprehensive"
    r")\b",
    re.IGNORECASE,
)

# Signals that the user is asking for code
_CODE_KEYWORDS = re.compile(
    r"\b("
    r"code|function|class|script|program|snippet|algorithm|api|endpoint"
    r"|test|unittest|pytest|module|package|library|import|syntax"
    r")\b",
    re.IGNORECASE,
)

# Requires up-to-date or external information
_CURRENT_KNOWLEDGE_KEYWORDS = re.compile(
    r"\b("
    r"latest|current|today|now|recent|news|stock|price|weather|forecast"
    r"|2024|2025|2026"
    r")\b",
    re.IGNORECASE,
)

# References to files or tools
_TOOL_KEYWORDS = re.compile(
    r"\b("
    r"file|folder|directory|path|read|open|save|upload|download"
    r"|tool|plugin|extension|integration|connect|database|db"
    r")\b",
    re.IGNORECASE,
)

# Credential / secret patterns — route to CLOUD so local model never sees them
_SENSITIVE_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{10,}\b"),             # OpenAI / generic sk- keys
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),            # GitHub personal tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                 # AWS access key IDs
    re.compile(r"\b(?:password|passwd|pwd)\s*[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"\b(?:secret|token|credential|api[_-]?key)\s*[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"\b(?:password|passwd|secret|credential)\b", re.IGNORECASE),
    re.compile(r"\btoken\b", re.IGNORECASE),
]

# Simple / factual query patterns (likely local)
_SIMPLE_FACTUAL = re.compile(
    r"^("
    r"what is|what's|who is|who's|when is|when was|where is|how much|how many"
    r"|define|meaning of|capital of|convert|calculate"
    r"|\d[\d\s\+\-\*/\(\)\.]+=?"
    r")",
    re.IGNORECASE,
)


@dataclass
class ClassificationResult:
    route: str              # "local" | "cloud"
    reason: str             # human-readable explanation
    confidence: float       # 0.0-1.0
    complexity_score: float # 0.0-1.0 (higher = more complex)
    is_sensitive: bool      # contains PII / credentials / private data signals


class QueryClassifier:
    """Heuristic classifier — no LLM calls, purely rule-based."""

    # Threshold configuration
    _SHORT_THRESHOLD = 100
    _LONG_THRESHOLD = 500
    _CONTEXT_LONG_COUNT = 3  # more than this many messages -> boost complexity

    def classify(
        self,
        query: str,
        context: Optional[List[dict]] = None,
    ) -> ClassificationResult:
        """Classify a query and return routing decision."""
        context = context or []

        sensitive = self._is_sensitive(query)
        complexity = self._compute_complexity(query, context)

        # ---------------------------------------------------------------
        # Routing decision
        # ---------------------------------------------------------------
        reasons: List[str] = []
        cloud_score = 0.0

        # Sensitivity always forces cloud
        if sensitive:
            cloud_score += 1.0
            reasons.append("query contains sensitive/credential data")

        query_len = len(query.strip())

        if query_len > self._LONG_THRESHOLD:
            cloud_score += 0.5
            reasons.append("query is long (>500 chars)")

        if _CODE_KEYWORDS.search(query):
            cloud_score += 0.4
            reasons.append("query involves code")

        if _COMPLEXITY_KEYWORDS.search(query):
            cloud_score += 0.35
            reasons.append("query uses complexity keywords")

        if _CURRENT_KNOWLEDGE_KEYWORDS.search(query):
            cloud_score += 0.3
            reasons.append("query requires current knowledge")

        if _TOOL_KEYWORDS.search(query):
            cloud_score += 0.25
            reasons.append("query references files/tools")

        if len(context) > self._CONTEXT_LONG_COUNT:
            cloud_score += 0.2
            reasons.append(f"long conversation context ({len(context)} messages)")

        # Local-favoring signals (reduce cloud score)
        if query_len < self._SHORT_THRESHOLD and not reasons:
            cloud_score -= 0.3
            reasons.append("query is short and simple")

        if _SIMPLE_FACTUAL.match(query.strip()):
            cloud_score -= 0.3
            reasons.append("query looks like a simple factual question")

        # ---------------------------------------------------------------
        # Final route
        # ---------------------------------------------------------------
        route = "cloud" if cloud_score > 0.3 else "local"

        # Confidence: high when score is clearly on one side, lower near threshold
        distance = abs(cloud_score - 0.3)
        raw_confidence = min(0.5 + distance, 1.0)
        confidence = round(max(0.0, min(1.0, raw_confidence)), 4)

        reason_text = (
            "; ".join(reasons)
            if reasons
            else "short, simple query with no complexity signals"
        )

        return ClassificationResult(
            route=route,
            reason=reason_text,
            confidence=confidence,
            complexity_score=round(max(0.0, min(1.0, complexity)), 4),
            is_sensitive=sensitive,
        )

    def _compute_complexity(
        self, query: str, context: Optional[List[dict]] = None
    ) -> float:
        """Return a complexity score in [0.0, 1.0]."""
        context = context or []
        score = 0.0

        # Length contribution
        length = len(query.strip())
        if length > self._LONG_THRESHOLD:
            score += 0.4
        elif length > self._SHORT_THRESHOLD:
            score += 0.2

        # Keyword contributions
        if _COMPLEXITY_KEYWORDS.search(query):
            score += 0.3
        if _CODE_KEYWORDS.search(query):
            score += 0.25
        if _CURRENT_KNOWLEDGE_KEYWORDS.search(query):
            score += 0.15
        if _TOOL_KEYWORDS.search(query):
            score += 0.1

        # Long context
        if len(context) > self._CONTEXT_LONG_COUNT:
            score += 0.2

        return min(score, 1.0)

    def _is_sensitive(self, query: str) -> bool:
        """Return True if the query contains credential/secret patterns."""
        for pattern in _SENSITIVE_PATTERNS:
            if pattern.search(query):
                return True
        return False


# Module-level default instance
query_classifier = QueryClassifier()
