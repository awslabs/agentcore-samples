#!/usr/bin/env python3
"""Retrieve and report online evaluation results from AgentCore.

Usage (BIND is the explicit project/target/account/region from the README):
  python scripts/evaluate.py "${BIND[@]}" --hours 1 --summary
  python scripts/evaluate.py "${BIND[@]}" --raw  # JSON only; not E2E proof

Prerequisites:
  - CloudWatch Transaction Search enabled (account/region setting; review before deploy)
  - Stack deployed with a non-empty onlineEvalConfigs array in agentcore.json
  - At least one ticket processed after Transaction Search was enabled
"""

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict

import boto3
from botocore.config import Config

REGION = os.environ.get("AWS_REGION", "us-west-2")


def find_eval_log_group(deployment) -> str:
    """Use the owned configuration's actual output, never the latest name match."""
    return deployment.evaluation_config()["outputConfig"]["cloudWatchConfig"]["logGroupName"]


def query_eval_results(log_group: str, hours: int = 1) -> list:
    """Query evaluation results from CloudWatch Logs Insights."""
    if not 1 <= hours <= 168:
        raise ValueError("Query window must be 1..168 hours")
    logs = boto3.client(
        "logs", region_name=REGION, config=Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1})
    )
    # Logs Insights StartQuery uses epoch seconds (not FilterLogEvents milliseconds).
    end = int(time.time())
    start = end - hours * 60 * 60

    query = "fields @timestamp, @message | filter name like /gen_ai.evaluation/ | sort @timestamp desc | limit 1000"

    q = logs.start_query(
        logGroupName=log_group,
        startTime=start,
        endTime=end,
        queryString=query,
    )["queryId"]

    # Bound wall time as well as poll count; SDK calls have explicit timeouts.
    deadline = time.monotonic() + 30
    for _ in range(30):
        if time.monotonic() >= deadline:
            logs.stop_query(queryId=q)
            raise TimeoutError("Evaluation query exceeded bounded wait")
        resp = logs.get_query_results(queryId=q)
        if resp["status"] == "Complete":
            break
        if resp["status"] in ("Failed", "Cancelled", "Timeout", "Unknown"):
            raise RuntimeError(f"Evaluation query did not complete: {resp['status']}")
        time.sleep(1)
    else:
        logs.stop_query(queryId=q)
        raise TimeoutError("Evaluation query exceeded 30 polls")

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
                        "status": attrs.get("gen_ai.evaluation.status", ""),
                        "error": attrs.get("gen_ai.evaluation.error", "") or parsed.get("error", ""),
                        "label": attrs.get("gen_ai.evaluation.score.label", ""),
                        "explanation": attrs.get("gen_ai.evaluation.explanation", ""),
                        "level": attrs.get("gen_ai.evaluation_level", ""),
                        "span_id": parsed.get("spanId", ""),
                    }
                )
            except json.JSONDecodeError:
                raise ValueError("Malformed evaluation log record") from None
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
    sessions = {r["session_id"] for r in results if r["session_id"]}
    traces = {r["trace_id"] for r in results if r["trace_id"]}

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
    for name in ("project", "target", "account", "region"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--hours", type=int, default=1, help="Hours back to query (default: 1)")
    parser.add_argument("--raw", action="store_true", help="Print raw JSON")
    parser.add_argument("--summary", action="store_true", help="Show aggregate summary only")
    args = parser.parse_args()

    from lifecycle import ROOT, Deployment

    deployment = Deployment(ROOT, args.project, args.target, args.account, args.region)
    global REGION
    REGION = args.region
    log_group = find_eval_log_group(deployment)
    results = query_eval_results(log_group, args.hours)
    if not results or any(
        isinstance(r["score"], bool)
        or not isinstance(r["score"], (int, float))
        or not math.isfinite(r["score"])
        or r.get("error")
        or r.get("status") not in (None, "", "Complete", "COMPLETED", "SUCCESS")
        for r in results
    ):
        sys.exit("Evaluation results missing or incomplete; this is not successful scoring")

    if args.raw:
        print(json.dumps(results, indent=2, default=str))
        return

    print(f"Online evaluation results from {log_group} (last {args.hours} hour(s))")
    print("Exploratory display only: full E2E requires current-run correlation and all four outcomes.")
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
