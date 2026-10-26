"""
Bug Condition Exploration Test: Ruff Lint Violations Exist on Unfixed Code

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5**

This test asserts that `ruff check` passes clean (exit code 0) for each affected
file with the relevant rule selected. On UNFIXED code, this test is EXPECTED TO FAIL,
which confirms the lint violations exist.

Property 1: Bug Condition - For each affected file, ruff check with the specific rule
selected SHALL return zero violations (exit code 0).
"""

import subprocess
import os

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st


# Project root is one level up from the tests directory
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Define the concrete failing files and their associated rules
LINT_VIOLATIONS = [
    ("shared/memory.py", "E741"),
    ("sandbox/app.py", "E401"),
    ("orchestrator/handler.py", "E741"),
    ("cdk/stacks/storage_stack.py", "F541"),
]


def run_ruff_check(file_path: str, rule: str) -> subprocess.CompletedProcess:
    """Run ruff check on a specific file with a specific rule selected."""
    full_path = os.path.join(PROJECT_ROOT, file_path)
    result = subprocess.run(
        ["ruff", "check", full_path, "--select", rule],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    return result


class TestBugConditionRuffLintViolations:
    """
    Bug Condition Exploration: Assert ruff check passes clean for each affected file.

    On unfixed code, these tests FAIL - confirming the violations exist.
    After the fix, these tests PASS - confirming the violations are resolved.
    """

    @given(file_index=st.sampled_from(range(len(LINT_VIOLATIONS))))
    @settings(
        max_examples=len(LINT_VIOLATIONS),
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_ruff_lint_passes_clean_for_affected_files(self, file_index: int):
        """
        **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5**

        Property: For any source file where the bug condition holds,
        ruff check with the specific rule SHALL return exit code 0 (no violations).

        On unfixed code this FAILS, proving the lint violations exist.
        """
        file_path, rule = LINT_VIOLATIONS[file_index]
        result = run_ruff_check(file_path, rule)

        assert result.returncode == 0, (
            f"ruff check failed for {file_path} with rule {rule}.\n"
            f"Exit code: {result.returncode}\n"
            f"Output:\n{result.stdout}\n"
            f"Errors:\n{result.stderr}"
        )

    def test_shared_memory_e741_passes_clean(self):
        """
        **Validates: Requirements 1.1, 1.2**

        Assert ruff check shared/memory.py --select E741 reports zero violations.
        """
        result = run_ruff_check("shared/memory.py", "E741")
        assert result.returncode == 0, (
            f"ruff reports E741 violations in shared/memory.py:\n{result.stdout}"
        )

    def test_sandbox_app_e401_passes_clean(self):
        """
        **Validates: Requirements 1.3**

        Assert ruff check sandbox/app.py --select E401 reports zero violations.
        """
        result = run_ruff_check("sandbox/app.py", "E401")
        assert result.returncode == 0, (
            f"ruff reports E401 violations in sandbox/app.py:\n{result.stdout}"
        )

    def test_orchestrator_handler_e741_passes_clean(self):
        """
        **Validates: Requirements 1.4**

        Assert ruff check orchestrator/handler.py --select E741 reports zero violations.
        """
        result = run_ruff_check("orchestrator/handler.py", "E741")
        assert result.returncode == 0, (
            f"ruff reports E741 violations in orchestrator/handler.py:\n{result.stdout}"
        )

    def test_cdk_storage_stack_f541_passes_clean(self):
        """
        **Validates: Requirements 1.5**

        Assert ruff check cdk/stacks/storage_stack.py --select F541 reports zero violations.
        """
        result = run_ruff_check("cdk/stacks/storage_stack.py", "F541")
        assert result.returncode == 0, (
            f"ruff reports F541 violations in cdk/stacks/storage_stack.py:\n{result.stdout}"
        )
