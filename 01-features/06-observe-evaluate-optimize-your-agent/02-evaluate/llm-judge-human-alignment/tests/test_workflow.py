import json

import pytest
from common import DATA_DIR, EVALUATORS_DIR, SCENARIOS_FILE, SME_GROUND_TRUTH_FILE, read_json


def test_parse_json_unwraps_strands_text_blocks(load_script):
    generate = load_script("01_generate_sessions")
    wrapped = json.dumps([{"text": json.dumps({"covered": False})}])
    assert generate.parse_json(wrapped) == {"covered": False}
    assert generate.parse_json("plain text") == "plain text"


def test_tool_calls_are_ordered_and_filled_from_log_records(load_script):
    generate = load_script("01_generate_sessions")
    spans = [
        {
            "spanId": "b",
            "startTimeUnixNano": 2,
            "status": {"code": "OK"},
            "attributes": {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "create_specialist_referral"},
        },
        {
            "spanId": "a",
            "startTimeUnixNano": 1,
            "status": {"code": "ERROR"},
            "attributes": {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "get_policy_coverage"},
        },
        {"spanId": "c", "startTimeUnixNano": 0, "attributes": {"gen_ai.operation.name": "chat"}},
    ]
    logs = [
        {
            "spanId": "a",
            "body": {
                "input": {"messages": [{"content": {"content": '{"policy_id": "POL-1042"}'}}]},
                "output": {"messages": [{"content": {"message": '[{"text": "{\\"covered\\": false}"}]'}}]},
            },
        }
    ]
    calls = generate.extract_tool_calls(spans)
    generate.attach_tool_payloads(calls, logs)
    assert [call["name"] for call in calls] == ["get_policy_coverage", "create_specialist_referral"]
    assert calls[0]["status"] == "error"
    assert calls[0]["input"] == {"policy_id": "POL-1042"}
    assert calls[0]["output"] == {"covered": False}


@pytest.mark.parametrize(
    ("decisions", "expected"),
    [
        ([{"rating": 1}, {"rating": 1}, {"rating": 2}], []),
        ([{"rating": 1}, {"rating": 3}, {"rating": 2}], ["scores differ by more than one point"]),
        ([{"rating": 1, "critical_failure": True}, {"rating": 2}], ["critical failure not flagged by every reviewer"]),
        ([{"rating": 3, "insufficient_evidence": True}, {"rating": 3}], ["reviewers disagree on evidence sufficiency"]),
    ],
)
def test_joint_review_reasons(load_script, decisions, expected):
    assert load_script("04_merge_reviews").joint_review_reasons(decisions) == expected


def test_splits_are_stratified_and_deterministic(load_script):
    merge = load_script("04_merge_reviews")
    cases = read_json(SCENARIOS_FILE)
    first = merge.assign_splits(cases, 0.2)
    assert first == merge.assign_splits(cases, 0.2)
    for risk in {case["risk"] for case in cases}:
        splits = {first[case["case_id"]] for case in cases if case["risk"] == risk}
        assert splits == {"tuning", "holdout"}


def test_parse_result_maps_session_and_evaluator(load_script):
    batch = load_script("05_run_batch_evaluation")
    event = {
        "attributes": {
            "session.id": "s-1",
            "gen_ai.evaluation.name": "ClaimsOutcomeJudge_v2_abc123",
            "gen_ai.evaluation.score.value": 1.0,
            "gen_ai.evaluation.score.label": "Critical failure",
            "gen_ai.evaluation.explanation": "Missing referral.",
        }
    }
    record = batch.parse_result(event, {"ClaimsOutcomeJudge_v2_abc123-XYZ": "v2"}, {"s-1": "CLM-001"})
    assert record["case_id"] == "CLM-001"
    assert record["evaluator"] == "v2"
    assert record["score"] == 1


def test_sample_data_is_consistent():
    scenarios = {case["case_id"] for case in read_json(SCENARIOS_FILE)}
    ground_truth = read_json(SME_GROUND_TRUTH_FILE)["cases"]
    assert scenarios == set(ground_truth)
    assert all(case["assertions"] for case in ground_truth.values())
    gates = read_json(DATA_DIR / "acceptance_gates.json")
    assert set(gates["gates"]) == {
        "min_weighted_kappa",
        "max_mean_absolute_error",
        "max_severe_false_passes",
        "max_missed_critical_failures",
        "min_repeatability_within_one",
    }


@pytest.mark.parametrize("path", sorted(EVALUATORS_DIR.glob("*.json")), ids=lambda p: p.name)
def test_evaluator_configs_use_supported_placeholders(path):
    config = read_json(path)["llmAsAJudge"]
    assert "{assertions}" in config["instructions"]
    assert [level["value"] for level in config["ratingScale"]["numerical"]] == [1, 2, 3, 4, 5]


def test_gates_flag_missed_critical_failures_and_skip_empty_groups(load_script):
    compare = load_script("06_compare_judges")
    thresholds = read_json(DATA_DIR / "acceptance_gates.json")
    cases = [
        {"case_id": "A", "risk": "high", "human_reference": 1},
        {"case_id": "B", "risk": "low", "human_reference": 5},
    ]
    summary = compare.summarize(cases, {"A": [3, 3], "B": [5, 5]}, thresholds)
    assert summary["missed_critical_failures"] == ["A"]
    assert summary["severe_false_passes"] == []
    gates = compare.check_gates(summary, thresholds["gates"])
    assert gates["missed_critical_failures"] is False
    assert gates["severe_false_passes"] is True

    passing_only = compare.summarize(cases[1:], {"B": [5]}, thresholds)
    gates = compare.check_gates(passing_only, thresholds["gates"])
    assert gates["missed_critical_failures"] is None
    assert gates["severe_false_passes"] is None
