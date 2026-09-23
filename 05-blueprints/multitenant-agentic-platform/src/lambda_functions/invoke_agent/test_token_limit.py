"""
Property-based tests for token limit enforcement logic.

Feature: tenant-token-limits, Property 6: Token Limit Enforcement
Validates: Requirements 4.2, 4.3
"""

from hypothesis import given, strategies as st, settings


def check_limit_enforcement(total_tokens: int, token_limit: int | None) -> tuple:
    """
    Pure function to check if a request should be allowed based on token usage and limit.

    Args:
        total_tokens: Current total tokens used by tenant
        token_limit: Token limit for tenant (None means no limit)

    Returns:
        tuple: (allowed: bool, reason: str)
    """
    if token_limit is None:
        return True, "no_limit"

    if total_tokens >= token_limit:
        return False, "limit_exceeded"

    return True, "within_limit"


class TestTokenLimitEnforcement:
    """Property-based tests for token limit enforcement."""

    @given(
        total_tokens=st.integers(min_value=0, max_value=10**12),
        token_limit=st.integers(min_value=1, max_value=10**12),
    )
    @settings(max_examples=100)
    def test_enforcement_decision_matches_comparison(self, total_tokens, token_limit):
        """
        Property 6: Token Limit Enforcement
        For any usage/limit combination, enforcement decision should match total_tokens >= token_limit.
        Validates: Requirements 4.2, 4.3
        """
        allowed, reason = check_limit_enforcement(total_tokens, token_limit)

        expected_blocked = total_tokens >= token_limit

        if expected_blocked:
            assert allowed is False, f"Expected blocked: usage={total_tokens}, limit={token_limit}"
            assert reason == "limit_exceeded"
        else:
            assert allowed is True, f"Expected allowed: usage={total_tokens}, limit={token_limit}"
            assert reason == "within_limit"

    @given(total_tokens=st.integers(min_value=0, max_value=10**12))
    @settings(max_examples=100)
    def test_no_limit_always_allows(self, total_tokens):
        """
        Property 6: Token Limit Enforcement
        When no limit is set (None), requests should always be allowed.
        Validates: Requirements 4.4
        """
        allowed, reason = check_limit_enforcement(total_tokens, None)

        assert allowed is True, f"Expected allowed when no limit: usage={total_tokens}"
        assert reason == "no_limit"

    @given(token_limit=st.integers(min_value=1, max_value=10**12))
    @settings(max_examples=100)
    def test_at_exact_limit_is_blocked(self, token_limit):
        """
        Property 6: Token Limit Enforcement
        When usage equals limit exactly, request should be blocked.
        Validates: Requirements 4.2
        """
        allowed, reason = check_limit_enforcement(token_limit, token_limit)

        assert allowed is False, f"Expected blocked at exact limit: {token_limit}"
        assert reason == "limit_exceeded"

    @given(token_limit=st.integers(min_value=2, max_value=10**12))
    @settings(max_examples=100)
    def test_one_below_limit_is_allowed(self, token_limit):
        """
        Property 6: Token Limit Enforcement
        When usage is one below limit, request should be allowed.
        Validates: Requirements 4.2
        """
        total_tokens = token_limit - 1
        allowed, reason = check_limit_enforcement(total_tokens, token_limit)

        assert allowed is True, f"Expected allowed one below limit: usage={total_tokens}, limit={token_limit}"
        assert reason == "within_limit"

    @given(
        token_limit=st.integers(min_value=1, max_value=10**12),
        excess=st.integers(min_value=1, max_value=10**6),
    )
    @settings(max_examples=100)
    def test_over_limit_is_blocked(self, token_limit, excess):
        """
        Property 6: Token Limit Enforcement
        When usage exceeds limit, request should be blocked.
        Validates: Requirements 4.2
        """
        total_tokens = token_limit + excess
        allowed, reason = check_limit_enforcement(total_tokens, token_limit)

        assert allowed is False, f"Expected blocked over limit: usage={total_tokens}, limit={token_limit}"
        assert reason == "limit_exceeded"


class TestTokenLimitEnforcementEdgeCases:
    """Unit tests for specific edge cases."""

    def test_zero_usage_with_limit(self):
        """Zero usage should always be allowed when limit exists."""
        allowed, reason = check_limit_enforcement(0, 1000)
        assert allowed is True
        assert reason == "within_limit"

    def test_zero_usage_no_limit(self):
        """Zero usage with no limit should be allowed."""
        allowed, reason = check_limit_enforcement(0, None)
        assert allowed is True
        assert reason == "no_limit"

    def test_large_usage_no_limit(self):
        """Large usage with no limit should be allowed."""
        allowed, reason = check_limit_enforcement(10**12, None)
        assert allowed is True
        assert reason == "no_limit"

    def test_small_limit_exceeded(self):
        """Small limit of 1 should block when usage is 1."""
        allowed, reason = check_limit_enforcement(1, 1)
        assert allowed is False
        assert reason == "limit_exceeded"

    def test_small_limit_not_exceeded(self):
        """Small limit of 1 should allow when usage is 0."""
        allowed, reason = check_limit_enforcement(0, 1)
        assert allowed is True
        assert reason == "within_limit"
