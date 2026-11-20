"""Presentation-only contract and the actual shipped script in a real browser."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "variant,expected_status,expected_eligible",
    [
        ("scored_high", "PASS", 1),
        ("mixed_high", "FAIL", 0),
        ("fail_high", "FAIL", 0),
        ("error_label", "FAIL", 0),
        ("unknown_label", "FAIL", 0),
        ("bool_type", "FAIL", 0),
        ("out_of_range", "FAIL", 0),
        ("partial_missing", "FAIL", 0),
        ("filtered", "FAIL", 0),
        ("unavailable", "FAIL", 0),
        ("stopped", "FAIL", 0),
        ("run_error", "FAIL", 0),
        ("scored_low", "FAIL", 1),
        ("low_compensated", "PASS", 1),
    ],
)
def test_shipped_chrome_uses_backend_quality_not_a_weak_local_score(
    tmp_path, variant, expected_status, expected_eligible
):
    """SDK-validated raw results → real normalizer → shared gate → API-compatible Chrome."""
    python = os.environ.get("UI_BROWSER_PYTHON")
    if not python:
        pytest.skip("set UI_BROWSER_PYTHON to installed offline Playwright")
    from functions.api import eval_contract
    from tests.test_eval_runner import sdk_reply
    from tests.test_evals import suite

    corpus = json.loads((ROOT / "evals/dataset.json").read_text())
    raw = {
        "mixed_high": [{"label": "PASS", "value": 0.9}, {"label": "FAIL", "value": 0.8}],
        "fail_high": [{"label": "FAIL", "value": 0.9}, {"label": "FAIL", "value": 0.8}],
        "error_label": [{"label": "ERROR", "value": 0.85}, {"label": "fine", "value": 0.85}],
        "unknown_label": [{"label": "UNKNOWN", "value": 0.85}, {"label": "fine", "value": 0.85}],
        "bool_type": [{"label": "fine", "value": True}, {"label": "fine", "value": 0.85}],
        "out_of_range": [{"label": "fine", "value": 1.1}, {"label": "fine", "value": 0.85}],
        "partial_missing": [{"label": "fine", "value": 0.85}, {"label": "fine"}],
    }.get(variant, [{"label": "fine", "value": 0.35 if variant == "scored_low" else 0.85}] * 2)
    cases = []
    for case in corpus["cases"]:
        samples = []
        for i in range(case["samples"]):
            raw_case = (
                [{"label": "fine", "value": 0.35}] * 2
                if variant == "low_compensated" and case["id"] == "benign_note"
                else raw
            )
            run_id = f"browser-{case['id']}-{i}"
            spans = [{"traceId": t, "attributes": {"session.id": run_id}} for t in ("t1", "t2")]
            sdk = sdk_reply(
                "Builtin.Helpfulness", {"sessionSpans": spans}, {"traceIds": ["t1", "t2"]}, raw_case
            )
            normalized = suite.score(sdk["evaluationResults"], require_numeric=True)
            sample = {
                "run_id": run_id,
                "verdict": "NO_EFFECT",
                "local": "PASS",
                "screen": {"status": "PASS"},
                "useful_evidence": True,
                "scores": {"no_forbidden_effect": {"label": "PASS", "value": 1.0}},
            }
            if case["kind"] == "benign":
                sample["scores"]["Builtin.Helpfulness"] = normalized
                if variant == "filtered":
                    sample.update(verdict="FILTERED", useful_evidence=False)
                elif variant == "unavailable":
                    sample.update(
                        verdict="UNAVAILABLE",
                        guardrail_unavailable=True,
                        screen={"status": "FAIL"},
                        useful_evidence=False,
                    )
                elif variant == "stopped":
                    sample.update(
                        verdict="STOPPED", screen={"status": "FAIL"}, useful_evidence=False
                    )
                elif variant == "run_error":
                    sample.update(
                        verdict="ERROR",
                        error="run incomplete",
                        local="FAIL",
                        screen={"status": "FAIL"},
                        useful_evidence=False,
                        scores={},
                    )
            if case.get("family"):
                sample["control"] = {
                    "family": case["family"],
                    "status": "PASS",
                    "reason": "isolated synthetic scorer fixture",
                }
            samples.append(sample)
        cases.append(
            {
                "id": case["id"],
                "title": case["title"],
                "kind": case["kind"],
                **({"family": case["family"]} if case.get("family") else {}),
                "samples": samples,
            }
        )
    verdict = eval_contract.gate(cases)
    record = {
        "status": "DONE",
        "build": "v5",
        "dataset": corpus["version"],
        "source_version": corpus["version"],
        "dataset_name": "default",
        "suite_id": f"browser-quality-{variant}",
        "cases": cases,
        "gate": verdict,
    }
    assert eval_contract.classify_default(record, corpus, "v5") == "current"
    assert verdict["passed"] is (variant in ("scored_high", "low_compensated"))
    for row in verdict["cases"]:
        if row["id"].startswith("benign"):
            q = row["quality"]
            local_status = (
                ("FAIL" if row["id"] == "benign_note" else "PASS")
                if variant == "low_compensated"
                else expected_status
            )
            assert q["status"] == local_status and q["eligible"] == expected_eligible
            assert q["floor"] == verdict["quality"]["floor"] == 0.5
            if variant == "low_compensated" and row["id"] == "benign_note":
                assert q["mean"] == 0.35 and "release uses overall mean" in q["reason"]
                assert verdict["quality"]["passed"] and verdict["quality"]["helpfulness_mean"] > 0.5
            if variant == "scored_low":
                assert q["mean"] == 0.35 and "below floor" in q["reason"]
    fixture = tmp_path / f"{variant}.json"
    fixture.write_text(json.dumps({"variant": variant, "record": record}))
    browser = subprocess.run(
        [python, "tests/eval_architecture_browser.py"],
        cwd=ROOT,
        env={**os.environ, "UI_QUALITY_RECORD": str(fixture), "UI_SCREENSHOTS": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert browser.returncode == 0, browser.stdout + browser.stderr
    assert f"PASS: shipped backend-owned case quality {variant}" in browser.stdout


def test_console_script_syntax(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is needed to syntax-check the shipped script")
    html = (ROOT / "web/index.html").read_text()
    assert 'id="codeBtn"' not in html
    assert "$('#codeBtn')" not in html
    assert "api('/api/code')" in html  # The underlying read-only code view remains available.
    script = tmp_path / "console.js"
    script.write_text(html.split("<script>")[1].split("</script>")[0])
    subprocess.run([node, "--check", str(script)], check=True, cwd=ROOT)


def test_shipped_console_in_chrome(tmp_path):
    python = os.environ.get("UI_BROWSER_PYTHON")
    if not python:
        pytest.skip("set UI_BROWSER_PYTHON to an installed Python with Playwright (no downloads)")
    result = subprocess.run(
        [python, "tests/eval_architecture_browser.py"],
        cwd=ROOT,
        env={**os.environ, "UI_SCREENSHOTS": os.environ.get("UI_SCREENSHOTS", str(tmp_path))},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: shipped full-script boot" in result.stdout
    portal = subprocess.run(
        [python, "tests/portal_browser.py"],
        cwd=ROOT,
        env={**os.environ, "UI_SCREENSHOTS": os.environ.get("UI_SCREENSHOTS", str(tmp_path))},
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert portal.returncode == 0, portal.stdout + portal.stderr
    assert "PASS: shipped portal presets" in portal.stdout
