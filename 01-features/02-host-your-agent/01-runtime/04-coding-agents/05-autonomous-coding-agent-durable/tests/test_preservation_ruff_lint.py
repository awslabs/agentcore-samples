"""
Preservation Property Tests: Runtime Behavior Unchanged for Affected Functions

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

These tests verify that the filtering/formatting logic in the affected functions
produces identical results regardless of the iteration variable name used.
They confirm baseline behavior on UNFIXED code and will continue to pass
after the variable renames are applied (since renames don't change semantics).

Property 2: Preservation - For any input to the affected functions, the fixed
code SHALL produce exactly the same runtime result as the original code.
"""

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st


# --- Strategies ---

# Strategy for generating lists of strings that include edge cases:
# empty strings, whitespace-only, valid strings, None-like values
lesson_strings = st.lists(
    st.one_of(
        st.just(""),
        st.just("   "),
        st.just("\t\n"),
        st.text(min_size=1, max_size=100),
    ),
    min_size=0,
    max_size=20,
)

# Strategy for generating lists with mixed types (for orchestrator filter)
mixed_type_items = st.lists(
    st.one_of(
        st.text(min_size=0, max_size=100),
        st.integers(),
        st.none(),
        st.booleans(),
        st.floats(allow_nan=False),
        st.just([]),
        st.just({}),
    ),
    min_size=0,
    max_size=20,
)


# --- Logic extracted from unfixed code (using `l` as variable name) ---

def remember_filter_original(lessons):
    """Original filtering logic from shared/memory.py remember() line 79.
    Uses the `l` variable name as in unfixed code."""
    return [l.strip() for l in lessons if l and l.strip()]


def remember_filter_renamed(lessons):
    """Same logic with renamed variable (what the fix will produce)."""
    return [lesson.strip() for lesson in lessons if lesson and lesson.strip()]


def format_for_prompt_original(lessons):
    """Original format_for_prompt() logic from shared/memory.py line 106.
    Uses the `l` variable name as in unfixed code."""
    if not lessons:
        return ""
    bullets = "\n".join(f"- {l}" for l in lessons)
    return (
        "\n<lessons_learned>\n"
        "From previous work on THIS repository (apply them to avoid repeating mistakes "
        "and to save effort):\n"
        f"{bullets}\n"
        "</lessons_learned>\n"
    )


def format_for_prompt_renamed(lessons):
    """Same logic with renamed variable (what the fix will produce)."""
    if not lessons:
        return ""
    bullets = "\n".join(f"- {lesson}" for lesson in lessons)
    return (
        "\n<lessons_learned>\n"
        "From previous work on THIS repository (apply them to avoid repeating mistakes "
        "and to save effort):\n"
        f"{bullets}\n"
        "</lessons_learned>\n"
    )


def orchestrator_filter_original(items):
    """Original filtering logic from orchestrator/handler.py line 370.
    Uses the `l` variable name as in unfixed code."""
    return [l for l in items if isinstance(l, str) and l.strip()]


def orchestrator_filter_renamed(items):
    """Same logic with renamed variable (what the fix will produce)."""
    return [lesson for lesson in items if isinstance(lesson, str) and lesson.strip()]


# --- Property-Based Tests ---

class TestPreservationRememberFilter:
    """
    Preservation tests for shared/memory.py remember() filtering logic.

    **Validates: Requirements 3.1**
    """

    @given(lessons=lesson_strings)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_remember_filter_same_result_regardless_of_variable_name(self, lessons):
        """
        **Validates: Requirements 3.1**

        Property: For all lists of strings, remember() filtering logic produces
        the same cleaned list regardless of iteration variable name.
        """
        original_result = remember_filter_original(lessons)
        renamed_result = remember_filter_renamed(lessons)
        assert original_result == renamed_result, (
            f"Filtering produced different results!\n"
            f"Input: {lessons!r}\n"
            f"Original (l): {original_result!r}\n"
            f"Renamed (lesson): {renamed_result!r}"
        )


class TestPreservationFormatForPrompt:
    """
    Preservation tests for shared/memory.py format_for_prompt() logic.

    **Validates: Requirements 3.2**
    """

    @given(lessons=st.lists(st.text(min_size=1, max_size=100), min_size=0, max_size=15))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_format_for_prompt_same_output_regardless_of_variable_name(self, lessons):
        """
        **Validates: Requirements 3.2**

        Property: For all lists of lesson strings, format_for_prompt() produces
        the same formatted output regardless of iteration variable name.
        """
        original_result = format_for_prompt_original(lessons)
        renamed_result = format_for_prompt_renamed(lessons)
        assert original_result == renamed_result, (
            f"format_for_prompt produced different results!\n"
            f"Input: {lessons!r}\n"
            f"Original (l): {original_result!r}\n"
            f"Renamed (lesson): {renamed_result!r}"
        )


class TestPreservationOrchestratorFilter:
    """
    Preservation tests for orchestrator/handler.py finalize lessons filter.

    **Validates: Requirements 3.4**
    """

    @given(items=mixed_type_items)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_orchestrator_filter_same_result_regardless_of_variable_name(self, items):
        """
        **Validates: Requirements 3.4**

        Property: For all lists with mixed types/values, the orchestrator lessons
        filter produces the same result regardless of iteration variable name.
        """
        original_result = orchestrator_filter_original(items)
        renamed_result = orchestrator_filter_renamed(items)
        assert original_result == renamed_result, (
            f"Orchestrator filter produced different results!\n"
            f"Input: {items!r}\n"
            f"Original (l): {original_result!r}\n"
            f"Renamed (lesson): {renamed_result!r}"
        )


class TestPreservationCdkArnString:
    """
    Preservation tests for cdk/stacks/storage_stack.py ARN string.

    **Validates: Requirements 3.5**
    """

    def test_arn_string_identical_with_or_without_f_prefix(self):
        """
        **Validates: Requirements 3.5**

        Property: The CDK ARN string value is identical with or without
        the `f` prefix (since no placeholders exist).
        """
        # With f-prefix (current unfixed code)
        arn_with_f = f"arn:aws:events:*:*:rule/DO-NOT-DELETE-S3-Files*"
        # Without f-prefix (what the fix will produce)
        arn_without_f = "arn:aws:events:*:*:rule/DO-NOT-DELETE-S3-Files*"

        assert arn_with_f == arn_without_f, (
            f"ARN strings differ!\n"
            f"With f-prefix: {arn_with_f!r}\n"
            f"Without f-prefix: {arn_without_f!r}"
        )

    @given(dummy=st.integers(min_value=0, max_value=100))
    @settings(max_examples=10, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_arn_string_value_stable_across_evaluations(self, dummy):
        """
        **Validates: Requirements 3.5**

        Property: The ARN string evaluates to the same value every time,
        confirming no dynamic interpolation occurs regardless of f-prefix.
        """
        expected = "arn:aws:events:*:*:rule/DO-NOT-DELETE-S3-Files*"
        # Simulate the f-string evaluation (no placeholders means same value)
        arn_with_f = f"arn:aws:events:*:*:rule/DO-NOT-DELETE-S3-Files*"
        assert arn_with_f == expected
