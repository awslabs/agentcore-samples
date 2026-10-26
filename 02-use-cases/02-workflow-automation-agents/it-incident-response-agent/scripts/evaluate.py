#!/usr/bin/env python3
"""Retrieve and report online evaluation results from AgentCore.

Usage:
  python scripts/evaluate.py              # latest results (last 1 hour)
  python scripts/evaluate.py --hours 24   # last 24 hours of results
  python scripts/evaluate.py --raw        # print raw JSON (for piping)
  python scripts/evaluate.py --summary    # aggregate scores only

Prerequisites:
  - CloudWatch Transaction Search enabled (aws observabilityadmin start-telemetry-evaluation)
  - Stack deployed with SKIP_ONLINE_EVAL=false
  - At least one ticket processed after Transaction Search was enabled
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict

import boto3

REGION = os.environ.get("AWS_REGION", "us-west-2")
STACK_NAME = os.environ.get("STACK_NAME", "AgentCore-ITIncidentAgent-dev")
EVAL_LOG_PREFIX = "/aws/bedrock-agentcore/evaluations/results/ITIncidentAgent"


def find_eval_log_group() -> str:
    """Find the most recent online evaluation results log group."""
    logs = boto3.client("logs", region_name=REGION)
    resp = logs.describe_log_groups(logGroupNamePrefix=EVAL_LOG_PREFIX)
    groups = resp.get("logGroups", [])
    if not groups:
        sys.exit(
            "❌ No evaluation results log group found.\n"
            "   Ensure:\n"
            "   1. CloudWatch Transaction Search is enabled\n"
            "      (aws observabilityadmin start-telemetry-evaluation --region us-west-2)\n"
            "   2. SKIP_ONLINE_EVAL=false in .env and stack redeployed\n"
            "   3. At least one ticket processed after enabling\n"
            "   4. Wait 2-5 minutes for eval results to appear"
        )
    # Use the most recently created log group
    groups.sort(key=lambda g: g.get("creationTime", 0), reverse=True)
    return groups[0]["logGroupName"]


def query_eval_results(log_group: str, hours: int = 1) -> list:
    """Query evaluation results from CloudWatch Logs Insights."""
    logs = boto3.client("logs", region_name=REGION)
    end = int(time.time() * 1000)
    start = end - hours * 60 * 60 * 1000

    query = "fields @timestamp, @message | filter name = 'gen_ai.evaluation.result' | sort @timestamp desc | limit 200"

    q = logs.start_query(
        logGroupName=log_group,
        startTime=start,
        endTime=end,
        queryString=query,
    )["queryId"]

    # Poll for results
    for _ in range(30):
        resp = logs.get_query_results(queryId=q)
        if resp["status"] in ("Complete", "Failed", "Cancelled"):
            break
        time.sleep(1)

    results = []
    for record in resp.get("results", []):
        fields = {kv["field"]: kv["value"] for kv in record}
        msg = fields.get("@message", "")
        if msg:
            try:
                parsed = json.loads(msg)
                # Extract the key fields from the nested structure
                attrs = parsed.get("attributes", {})
                results.append(
                    {
                        "timestamp": fields.get("@timestamp", ""),
                        "trace_id": parsed.get("traceId", ""),
                        "session_id": attrs.get("session.id", ""),
                        "evaluator": attrs.get("gen_ai.evaluation.name", ""),
                        "score": attrs.get("gen_ai.evaluation.score.value"),
                        "label": attrs.get("gen_ai.evaluation.score.label", ""),
                        "explanation": attrs.get("gen_ai.evaluation.explanation", ""),
                        "level": attrs.get("gen_ai.evaluation_level", ""),
                        "span_id": parsed.get("spanId", ""),
                    }
                )
            except json.JSONDecodeError:
                pass
    return results


def print_summary(results: list) -> None:
    """Print aggregate score summary."""
    if not results:
        print("  No evaluation results found.")
        return

    # Group by evaluator
    by_evaluator = defaultdict(list)
    for r in results:
        if r["evaluator"]:
            by_evaluator[r["evaluator"]].append(r["score"])

    # Count sessions
    sessions = set(r["session_id"] for r in results if r["session_id"])
    traces = set(r["trace_id"] for r in results if r["trace_id"])

    print(f"  Sessions evaluated: {len(sessions)}")
    print(f"  Traces evaluated:   {len(traces)}")
    print(f"  Total scores:       {len(results)}")
    print()
    print(f"  {'Evaluator':<30} {'Avg Score':>10} {'Count':>6}  {'Labels'}")
    print(f"  {'─' * 30} {'─' * 10} {'─' * 6}  {'─' * 30}")

    for evaluator, scores in sorted(by_evaluator.items()):
        valid_scores = [s for s in scores if s is not None]
        avg = sum(valid_scores) / len(valid_scores) if valid_scores else 0
        # Get label distribution
        labels = defaultdict(int)
        for r in results:
            if r["evaluator"] == evaluator and r["label"]:
                labels[r["label"]] += 1
        label_str = ", ".join(f"{k}({v})" for k, v in sorted(labels.items()))
        print(f"  {evaluator:<30} {avg:>9.2f}  {len(valid_scores):>5}  {label_str}")


def print_detailed(results: list) -> None:
    """Print detailed results per trace."""
    if not results:
        print("  No evaluation results found in the specified time window.")
        print()
        print("  Possible reasons:")
        print("    - No tickets processed recently (send one with ./scripts/publish_ticket.sh)")
        print("    - Evaluation takes 2-5 min after invocation to complete")
        print("    - CloudWatch Transaction Search just enabled (wait 10-15 min)")
        return

    # Group by trace
    by_trace = defaultdict(list)
    for r in results:
        key = r["trace_id"] or r["session_id"] or "unknown"
        by_trace[key].append(r)

    print(f"  {len(by_trace)} invocation(s) evaluated:\n")

    for i, (trace_id, evals) in enumerate(list(by_trace.items())[:10], 1):
        timestamp = evals[0]["timestamp"] if evals else ""
        session = evals[0]["session_id"][:12] if evals[0]["session_id"] else ""

        print(f"  ┌─ [{i}] Trace: {trace_id[:20]}... | Session: {session}...")
        print(f"  │   Time: {timestamp}")

        # Separate by level
        trace_level = [e for e in evals if e["level"] == "Trace"]
        session_level = [e for e in evals if e["level"] == "Session"]
        span_level = [e for e in evals if e["level"] == "Span"]

        for e in trace_level + session_level:
            score_bar = _score_bar(e["score"])
            print(f"  │   {score_bar} {e['evaluator']}: {e['score']:.2f} ({e['label']})")
            if e["explanation"]:
                # Show first 120 chars of explanation
                expl = e["explanation"][:120].replace("\n", " ")
                print(f"  │        └─ {expl}...")

        if span_level:
            print(f"  │   Tool evaluations ({len(span_level)} spans):")
            for e in span_level[:5]:
                print(f"  │     {_score_bar(e['score'])} {e['evaluator']}: {e['score']:.2f}")

        print(f"  └{'─' * 60}")
        print()


def _score_bar(score) -> str:
    """Create a visual score indicator."""
    if score is None:
        return "[ ? ]"
    if score >= 0.9:
        return "[████]"
    if score >= 0.7:
        return "[███░]"
    if score >= 0.5:
        return "[██░░]"
    if score >= 0.3:
        return "[█░░░]"
    return "[░░░░]"


def main():
    """Parse CLI args and print online evaluation results for the agent."""
    parser = argparse.ArgumentParser(description="Retrieve online evaluation results for IT Incident Response Agent")
    parser.add_argument("--hours", type=int, default=1, help="Hours back to query (default: 1)")
    parser.add_argument("--raw", action="store_true", help="Print raw JSON")
    parser.add_argument("--summary", action="store_true", help="Show aggregate summary only")
    args = parser.parse_args()

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  Online Evaluation Results — IT Incident Response Agent")
    print("═══════════════════════════════════════════════════════════════")
    print()

    log_group = find_eval_log_group()
    print(f"  Log group: ...{log_group[-50:]}")
    print(f"  Time window: last {args.hours} hour(s)")
    print()

    results = query_eval_results(log_group, args.hours)

    if args.raw:
        print(json.dumps(results, indent=2, default=str))
        return

    if args.summary:
        print_summary(results)
    else:
        print("─── Summary ────────────────────────────────────────────────")
        print()
        print_summary(results)
        print()
        print("─── Detail ─────────────────────────────────────────────────")
        print()
        print_detailed(results)

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Evaluators: Correctness | Helpfulness | ToolSelectionAccuracy | GoalSuccessRate")
    print("  Dashboard:  CloudWatch → GenAI Observability → ITIncidentAgent")
    print("═══════════════════════════════════════════════════════════════")
    print()


if __name__ == "__main__":
    main()
