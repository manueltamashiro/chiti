"""
Tests for backend/llm/query_classifier.py
"""

from __future__ import annotations

import pytest

from backend.llm.query_classifier import (
    ClassificationResult,
    QueryClassifier,
    query_classifier,
)


@pytest.fixture()
def clf() -> QueryClassifier:
    return QueryClassifier()


# ---------------------------------------------------------------------------
# Basic route — local
# ---------------------------------------------------------------------------

class TestLocalRouting:
    def test_short_factual_query_routes_local(self, clf):
        result = clf.classify("What is 2 + 2?")
        assert result.route == "local"

    def test_simple_math_routes_local(self, clf):
        result = clf.classify("42 * 7 =")
        assert result.route == "local"

    def test_very_short_query_routes_local(self, clf):
        result = clf.classify("Hi")
        assert result.route == "local"

    def test_what_is_query_routes_local(self, clf):
        result = clf.classify("What is the capital of France?")
        assert result.route == "local"

    def test_define_query_routes_local(self, clf):
        result = clf.classify("Define photosynthesis")
        assert result.route == "local"


# ---------------------------------------------------------------------------
# Basic route — cloud
# ---------------------------------------------------------------------------

class TestCloudRouting:
    def test_long_query_routes_cloud(self, clf):
        long_query = "Please " + "explain " * 100 + "this concept."
        result = clf.classify(long_query)
        assert result.route == "cloud"

    def test_code_generation_routes_cloud(self, clf):
        result = clf.classify("implement a function to parse JSON in Python")
        assert result.route == "cloud"

    def test_write_keyword_routes_cloud(self, clf):
        result = clf.classify("write a script that monitors disk usage")
        assert result.route == "cloud"

    def test_refactor_routes_cloud(self, clf):
        result = clf.classify("refactor this class to use dependency injection")
        assert result.route == "cloud"

    def test_debug_routes_cloud(self, clf):
        result = clf.classify("debug this function that returns None unexpectedly")
        assert result.route == "cloud"

    def test_compare_routes_cloud(self, clf):
        result = clf.classify("compare REST vs GraphQL APIs with code examples")
        assert result.route == "cloud"


# ---------------------------------------------------------------------------
# Sensitivity detection
# ---------------------------------------------------------------------------

class TestSensitiveDetection:
    def test_sk_token_routes_cloud(self, clf):
        result = clf.classify("sk-abc123XYZ456def789GHI can you use this key?")
        assert result.route == "cloud"
        assert result.is_sensitive is True

    def test_github_token_routes_cloud(self, clf):
        result = clf.classify("my token is ghp_ABCDEFGHIJKLMNOPQRSTUVWX, is it valid?")
        assert result.route == "cloud"
        assert result.is_sensitive is True

    def test_password_keyword_is_sensitive(self, clf):
        result = clf.classify("my password is hunter2, how do I reset it?")
        assert result.is_sensitive is True

    def test_credential_keyword_is_sensitive(self, clf):
        result = clf.classify("store this credential securely")
        assert result.is_sensitive is True

    def test_non_sensitive_query(self, clf):
        result = clf.classify("What is the weather today?")
        assert result.is_sensitive is False


# ---------------------------------------------------------------------------
# Confidence and score bounds
# ---------------------------------------------------------------------------

class TestScoreBounds:
    def test_confidence_between_0_and_1(self, clf):
        queries = [
            "hi",
            "write me a sorting algorithm in C++",
            "sk-fakekey12345 what is this?",
            "What is 2+2?",
            "explain " * 200,
        ]
        for q in queries:
            result = clf.classify(q)
            assert 0.0 <= result.confidence <= 1.0, f"Failed for: {q!r}"

    def test_complexity_score_between_0_and_1(self, clf):
        queries = [
            "hi",
            "write me a sorting algorithm in C++ with full tests and benchmarks " * 5,
        ]
        for q in queries:
            result = clf.classify(q)
            assert 0.0 <= result.complexity_score <= 1.0, f"Failed for: {q!r}"


# ---------------------------------------------------------------------------
# Context window
# ---------------------------------------------------------------------------

class TestContextWindow:
    def test_long_context_increases_complexity(self, clf):
        short_query = "ok"
        no_ctx = clf.classify(short_query, context=[])
        long_ctx = clf.classify(
            short_query,
            context=[{"role": "user", "content": f"msg {i}"} for i in range(5)],
        )
        assert long_ctx.complexity_score >= no_ctx.complexity_score

    def test_more_than_3_messages_boosts_complexity(self, clf):
        context = [{"role": "user", "content": "hello"} for _ in range(4)]
        result = clf.classify("ok", context=context)
        # With 4 messages the complexity should reflect context boost
        assert result.complexity_score > 0.0

    def test_context_none_treated_as_empty(self, clf):
        result = clf.classify("hello", context=None)
        assert result.route in {"local", "cloud"}


# ---------------------------------------------------------------------------
# ClassificationResult structure
# ---------------------------------------------------------------------------

class TestClassificationResultStructure:
    def test_result_has_all_fields(self, clf):
        result = clf.classify("hello")
        assert hasattr(result, "route")
        assert hasattr(result, "reason")
        assert hasattr(result, "confidence")
        assert hasattr(result, "complexity_score")
        assert hasattr(result, "is_sensitive")

    def test_route_is_valid_string(self, clf):
        result = clf.classify("some query")
        assert result.route in {"local", "cloud"}

    def test_reason_is_non_empty_string(self, clf):
        result = clf.classify("explain why the sky is blue in detail")
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0


# ---------------------------------------------------------------------------
# Module-level default instance
# ---------------------------------------------------------------------------

class TestModuleLevelInstance:
    def test_default_instance_exists(self):
        assert query_classifier is not None
        assert isinstance(query_classifier, QueryClassifier)

    def test_default_instance_classifies(self):
        result = query_classifier.classify("hello world")
        assert result.route in {"local", "cloud"}
