"""Basic tests for the scenario runner."""

from src.scenarios import _run_one


def test_scenario_result_structure():
    """Verify scenario result dict has expected keys."""
    # This test doesn't hit real AWS — it tests the result structure
    scenario = {
        "name": "test-scenario",
        "target": "TestTarget",
        "tool": "test_tool",
        "input": {"key": "value"},
        "expected": "DENY",
    }
    # Will fail with connection error (no real gateway) but structure is valid
    result = _run_one("http://localhost:9999", "fake-token", scenario)
    assert "scenario" in result
    assert "tool" in result
    assert "decision" in result
    assert result["decision"] in ("ALLOW", "DENY", "FAILED")
