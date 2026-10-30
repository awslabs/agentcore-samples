# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# scripts/evaluate_stored_traces.py
"""
Evaluate pre-collected trace fixtures without live agent invocation (Approach A).

Decouples evaluation from live MCP calls — collect traces during manual testing
or staging runs, commit them as JSON fixtures, and evaluate in CI.
Same traces = same scores = deterministic quality gate.
"""

import json
import os
import sys

import boto3

DEFAULT_CI_EVALUATORS = [
    "Builtin.Helpfulness",
    "Builtin.Correctness",
    "Builtin.GoalSuccessRate",
    "Builtin.InstructionFollowing",
]


def load_trace_fixtures(fixtures_dir):
    """Load pre-collected trace fixtures from JSON files."""
    all_spans = []
    fixture_files = sorted(f for f in os.listdir(fixtures_dir) if f.endswith(".json"))

    if not fixture_files:
        print(f"ERROR: No .json fixtures found in {fixtures_dir}")
        sys.exit(1)

    for filename in fixture_files:
        filepath = os.path.join(fixtures_dir, filename)
        print(f"Loading fixture: {filename}")
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                # {session_id: [spans]} format — flatten all sessions
                for session_spans in data.values():
                    all_spans.extend(session_spans)
            elif isinstance(data, list):
                all_spans.extend(data)

    print(f"Loaded {len(all_spans)} spans from {len(fixture_files)} fixtures.")
    return all_spans


def evaluate_and_gate(spans, evaluator_ids, threshold, region):
    """Run evaluations and enforce quality gate."""
    client = boto3.client("bedrock-agentcore", region_name=region)
    results = {}

    for evaluator_id in evaluator_ids:
        print(f"\nEvaluating: {evaluator_id}")
        try:
            # sessionSpans (not "spans") — the API key name matters
            response = client.evaluate(
                evaluatorId=evaluator_id,
                evaluationInput={"sessionSpans": spans},
            )
            eval_results = response.get("evaluationResults", [])
            # Score is in "value" field, not "score" — using wrong key silently returns 0
            score = eval_results[0].get("value", 0) if eval_results else 0
            results[evaluator_id] = score
            print(f"  Score: {score:.2f}")
        except Exception as e:
            print(f"  ERROR: {e}")
            results[evaluator_id] = 0

    # Quality gate
    print(f"\n{'=' * 50}")
    all_passed = True
    for eid, score in results.items():
        status = "PASS" if score >= threshold else "FAIL"
        if status == "FAIL":
            all_passed = False
        print(f"  [{status}] {eid}: {score:.2f}")
    print(f"{'=' * 50}")

    return all_passed


def main():
    """Load trace fixtures, evaluate them, and enforce the quality gate."""
    region = os.environ.get("AWS_REGION", "us-east-1")
    fixtures_dir = os.environ.get("TRACE_FIXTURES_DIR", "fixtures")
    threshold = float(os.environ.get("EVAL_THRESHOLD", "0.7"))

    spans = load_trace_fixtures(fixtures_dir)
    passed = evaluate_and_gate(spans, DEFAULT_CI_EVALUATORS, threshold, region)

    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
