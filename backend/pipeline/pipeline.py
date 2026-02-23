"""
Tool Result Pipeline - Unified Security Orchestrator

Connects all pipeline components into a single flow that every tool result
must pass through before reaching the LLM or the frontend.

Stage order:
    1. Content Firewall  → detect/block prompt injection
    2. Secret Scrubber   → redact credentials and API keys
    3. Content Tagger    → attach trust level and origin metadata
    4. Action Classifier → determine confirmation tier

Pipeline modes:
    STRICT      — block on any suspicion (severity ≥ 1)
    BALANCED    — tag suspicious, block critical (default)
    PERMISSIVE  — tag only, never block
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from backend.pipeline.action_classifier import ActionClassifier, ToolCall, action_classifier
from backend.pipeline.content_firewall import ContextAwareFirewall, content_firewall
from backend.pipeline.content_tagger import ContentTagger
from backend.pipeline.models import (
    ActionTier,
    ClassificationResult,
    ContentSource,
    FirewallResult,
    OriginEntry,
    ProcessedResult,
    ScrubResult,
    TaggerResult,
    ToolResult,
)
from backend.pipeline.secret_scrubber import SecretScrubber

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline configuration
# ---------------------------------------------------------------------------

class PipelineMode(Enum):
    STRICT = "strict"        # Block on any suspicion
    BALANCED = "balanced"    # Tag suspicious, block critical
    PERMISSIVE = "permissive"  # Tag only, never block


@dataclass
class PipelineConfig:
    mode: PipelineMode = PipelineMode.BALANCED
    # Per-tool bypass flags (use sparingly — only for internal tools)
    bypass_firewall: List[str] = field(default_factory=list)
    bypass_scrubber: List[str] = field(default_factory=list)
    bypass_tagger: List[str] = field(default_factory=list)
    # Debug: record intermediate stage results
    debug_mode: bool = False


# ---------------------------------------------------------------------------
# Stage telemetry
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    """Captures the outcome of a single pipeline stage"""
    stage: str
    duration_ms: float
    blocked: bool = False
    block_reason: Optional[str] = None
    tags_added: List[str] = field(default_factory=list)
    secrets_found: int = 0


@dataclass
class PipelineMetrics:
    """Telemetry for a single pipeline run"""
    tool_name: str
    total_duration_ms: float
    stages: List[StageResult] = field(default_factory=list)
    final_tier: Optional[ActionTier] = None
    blocked: bool = False


# ---------------------------------------------------------------------------
# Circuit breaker (per-tool failure tracking)
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """
    Disables a tool after repeated pipeline failures.

    After `threshold` consecutive failures the tool is tripped and will
    return a blocked result until manually reset.
    """

    def __init__(self, threshold: int = 5):
        self.threshold = threshold
        self._failures: Dict[str, int] = {}
        self._tripped: Dict[str, bool] = {}

    def record_failure(self, tool_name: str) -> None:
        self._failures[tool_name] = self._failures.get(tool_name, 0) + 1
        if self._failures[tool_name] >= self.threshold:
            self._tripped[tool_name] = True
            logger.error(
                f"Circuit breaker tripped for tool '{tool_name}' "
                f"after {self.threshold} consecutive failures"
            )

    def record_success(self, tool_name: str) -> None:
        self._failures[tool_name] = 0
        self._tripped[tool_name] = False

    def is_tripped(self, tool_name: str) -> bool:
        return self._tripped.get(tool_name, False)

    def reset(self, tool_name: str) -> None:
        self._failures[tool_name] = 0
        self._tripped[tool_name] = False


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

class ToolResultPipeline:
    """
    Orchestrates the full tool result security pipeline.

    Usage:
        pipeline = ToolResultPipeline()
        result = await pipeline.process(tool_result)

        if result.blocked:
            # Notify user, do not pass to LLM
        else:
            # Safe to include in LLM context
            llm_content = result.content
    """

    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        firewall: Optional[ContextAwareFirewall] = None,
        scrubber: Optional[SecretScrubber] = None,
        tagger: Optional[ContentTagger] = None,
        classifier: Optional[ActionClassifier] = None,
    ):
        self.config = config or PipelineConfig()
        self._firewall = firewall or content_firewall
        self._scrubber = scrubber or SecretScrubber()
        self._tagger = tagger or ContentTagger()
        self._classifier = classifier or action_classifier
        self._circuit_breaker = CircuitBreaker()
        self._metrics_log: List[PipelineMetrics] = []

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def process(
        self,
        tool_result: ToolResult,
        source: ContentSource = ContentSource.INTERNAL,
        context: Optional[Dict[str, Any]] = None,
    ) -> ProcessedResult:
        """
        Run a tool result through all pipeline stages.

        Args:
            tool_result: Raw result from tool execution
            source:      Content origin (LOCAL_FILESYSTEM, EXTERNAL_WEB, etc.)
            context:     Optional extra context (file_type, source_url, …)

        Returns:
            ProcessedResult — safe, tagged, classified content ready for the LLM
        """
        tool_name = tool_result.capability_name
        overall_start = time.monotonic()
        metrics = PipelineMetrics(tool_name=tool_name, total_duration_ms=0.0)
        context = context or {}

        # ----------------------------------------------------------------
        # Circuit breaker check
        # ----------------------------------------------------------------
        if self._circuit_breaker.is_tripped(tool_name):
            logger.warning(f"Tool '{tool_name}' is circuit-breaker tripped — blocking result")
            metrics.blocked = True
            return self._blocked_result(
                tool_result,
                f"Tool '{tool_name}' is temporarily disabled due to repeated failures",
                ActionTier.TIER_3,
                metrics,
            )

        try:
            content = self._extract_content(tool_result)

            # ----------------------------------------------------------------
            # Stage 1: Content Firewall
            # ----------------------------------------------------------------
            firewall_result, stage1 = self._run_firewall(tool_name, content, context)
            metrics.stages.append(stage1)

            if firewall_result.should_block:
                self._circuit_breaker.record_success(tool_name)
                metrics.blocked = True
                return self._blocked_result(
                    tool_result,
                    f"Content firewall blocked output from '{tool_name}': "
                    f"injection detected (severity={firewall_result.severity})",
                    ActionTier.TIER_3,
                    metrics,
                )

            # ----------------------------------------------------------------
            # Stage 2: Secret Scrubber
            # ----------------------------------------------------------------
            scrub_result, content, stage2 = self._run_scrubber(tool_name, content)
            metrics.stages.append(stage2)

            # ----------------------------------------------------------------
            # Stage 3: Content Tagger
            # ----------------------------------------------------------------
            tagger_result, stage3 = self._run_tagger(
                tool_name, content, source, firewall_result, scrub_result
            )
            metrics.stages.append(stage3)

            # ----------------------------------------------------------------
            # Stage 4: Action Classifier
            # ----------------------------------------------------------------
            classification, stage4 = self._run_classifier(tool_name, context)
            metrics.stages.append(stage4)

            # ----------------------------------------------------------------
            # Assemble ProcessedResult
            # ----------------------------------------------------------------
            tags = [tag.name for tag in tagger_result.tags]
            if firewall_result.should_tag:
                tags.append("INJECTION_SUSPECTED")
            if scrub_result.secret_count > 0:
                tags.append(f"SECRETS_SCRUBBED_{scrub_result.secret_count}")

            # Use tagged/wrapped content for LLM consumption when untrusted
            final_content = (
                tagger_result.wrapped_content
                if tagger_result.requires_disclosure
                else content
            )

            result = ProcessedResult(
                original=tool_result,
                content=final_content,
                trust_level=tagger_result.trust_level.value,
                tier=classification.tier,
                tags=tags,
                secrets_found=scrub_result.secret_count,
                injection_detected=firewall_result.severity >= 2,
                blocked=False,
                block_reason=None,
            )

            self._circuit_breaker.record_success(tool_name)
            metrics.final_tier = classification.tier

        except Exception as exc:
            logger.exception(f"Pipeline failed for tool '{tool_name}': {exc}")
            self._circuit_breaker.record_failure(tool_name)
            # Fail closed — default to highest tier, unscrubbed tag
            result = ProcessedResult(
                original=tool_result,
                content=f"[PIPELINE ERROR] Output from '{tool_name}' could not be processed safely.",
                trust_level="untrusted_external",
                tier=ActionTier.TIER_3,
                tags=["PIPELINE_ERROR", "UNSCRUBBED"],
                secrets_found=0,
                injection_detected=False,
                blocked=False,
                block_reason=str(exc),
            )
            metrics.final_tier = ActionTier.TIER_3

        finally:
            elapsed_ms = (time.monotonic() - overall_start) * 1000
            metrics.total_duration_ms = elapsed_ms
            self._metrics_log.append(metrics)
            logger.debug(
                f"Pipeline completed for '{tool_name}' in {elapsed_ms:.1f}ms "
                f"(blocked={metrics.blocked})"
            )

        return result

    # ------------------------------------------------------------------
    # Stage runners
    # ------------------------------------------------------------------

    def _run_firewall(
        self,
        tool_name: str,
        content: str,
        context: Dict[str, Any],
    ) -> tuple[FirewallResult, StageResult]:
        start = time.monotonic()
        firewall_context = dict(context)

        bypass = tool_name in self.config.bypass_firewall
        if bypass:
            firewall_result = FirewallResult(
                severity=0, should_block=False, should_tag=False, confidence=1.0
            )
        else:
            firewall_result = self._firewall.scan(content, firewall_context)

            # Override block decision based on pipeline mode
            if self.config.mode == PipelineMode.PERMISSIVE:
                firewall_result.should_block = False
            elif self.config.mode == PipelineMode.STRICT and firewall_result.severity >= 1:
                firewall_result.should_block = True

        stage = StageResult(
            stage="content_firewall",
            duration_ms=(time.monotonic() - start) * 1000,
            blocked=firewall_result.should_block,
            block_reason=(
                f"Injection severity={firewall_result.severity}"
                if firewall_result.should_block
                else None
            ),
            tags_added=["INJECTION_SUSPECTED"] if firewall_result.should_tag else [],
        )
        return firewall_result, stage

    def _run_scrubber(
        self,
        tool_name: str,
        content: str,
    ) -> tuple[ScrubResult, str, StageResult]:
        start = time.monotonic()

        bypass = tool_name in self.config.bypass_scrubber
        if bypass:
            from backend.pipeline.models import ScrubResult as SR
            scrub_result = SR(scrubbed_content=content, secret_count=0)
            scrubbed_content = content
        else:
            scrub_result = self._scrubber.scrub(content)
            scrubbed_content = (
                scrub_result.scrubbed_content
                if isinstance(scrub_result.scrubbed_content, str)
                else str(scrub_result.scrubbed_content)
            )

        stage = StageResult(
            stage="secret_scrubber",
            duration_ms=(time.monotonic() - start) * 1000,
            secrets_found=scrub_result.secret_count,
            tags_added=[f"SECRETS_SCRUBBED_{scrub_result.secret_count}"]
            if scrub_result.secret_count > 0
            else [],
        )
        return scrub_result, scrubbed_content, stage

    def _run_tagger(
        self,
        tool_name: str,
        content: str,
        source: ContentSource,
        firewall_result: FirewallResult,
        scrub_result: ScrubResult,
    ) -> tuple[TaggerResult, StageResult]:
        start = time.monotonic()

        bypass = tool_name in self.config.bypass_tagger
        if bypass:
            from backend.pipeline.models import TaggerResult as TR, TrustLevel
            tagger_result = TR(
                trust_level=TrustLevel.TRUSTED,
                tags=[],
                origin_chain=[],
                wrapped_content=content,
                summary="bypassed",
                requires_disclosure=False,
            )
        else:
            origin_chain = [OriginEntry(source_name=tool_name, content_source=source)]
            tagger_result = self._tagger.tag(
                content=content,
                origin_chain=origin_chain,
                firewall_result=firewall_result,
                scrub_result=scrub_result,
            )

        stage = StageResult(
            stage="content_tagger",
            duration_ms=(time.monotonic() - start) * 1000,
            tags_added=[t.name for t in tagger_result.tags],
        )
        return tagger_result, stage

    def _run_classifier(
        self,
        tool_name: str,
        context: Dict[str, Any],
    ) -> tuple[ClassificationResult, StageResult]:
        start = time.monotonic()

        tool_call = ToolCall(tool_name=tool_name, params={}, context=context)
        classification = self._classifier.classify(tool_call)

        stage = StageResult(
            stage="action_classifier",
            duration_ms=(time.monotonic() - start) * 1000,
            tags_added=[f"TIER_{classification.tier.value.upper()}"],
        )
        return classification, stage

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_content(self, tool_result: ToolResult) -> str:
        """Convert tool result content to a string for pipeline processing"""
        content = tool_result.content
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, (dict, list)):
            import json
            return json.dumps(content)
        return str(content)

    def _blocked_result(
        self,
        tool_result: ToolResult,
        reason: str,
        tier: ActionTier,
        metrics: PipelineMetrics,
    ) -> ProcessedResult:
        logger.warning(f"Pipeline blocking result: {reason}")
        metrics.blocked = True
        metrics.final_tier = tier
        return ProcessedResult(
            original=tool_result,
            content="[BLOCKED BY SECURITY PIPELINE]",
            trust_level="untrusted_external",
            tier=tier,
            tags=["BLOCKED"],
            secrets_found=0,
            injection_detected=True,
            blocked=True,
            block_reason=reason,
        )

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    def get_metrics(self) -> List[PipelineMetrics]:
        """Return collected metrics for all processed results"""
        return list(self._metrics_log)

    def get_block_rate(self) -> float:
        """Return fraction of processed results that were blocked"""
        if not self._metrics_log:
            return 0.0
        blocked = sum(1 for m in self._metrics_log if m.blocked)
        return blocked / len(self._metrics_log)

    def get_avg_latency_ms(self) -> float:
        """Return average total pipeline latency in milliseconds"""
        if not self._metrics_log:
            return 0.0
        return sum(m.total_duration_ms for m in self._metrics_log) / len(self._metrics_log)

    def reset_circuit_breaker(self, tool_name: str) -> None:
        """Manually reset circuit breaker for a tool"""
        self._circuit_breaker.reset(tool_name)
        logger.info(f"Circuit breaker reset for '{tool_name}'")


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------

pipeline = ToolResultPipeline()
