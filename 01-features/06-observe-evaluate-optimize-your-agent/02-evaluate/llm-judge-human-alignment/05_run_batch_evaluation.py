"""Phase 2 and 4: score the stored sessions with candidate LLM judges using batch evaluation.

Creates one custom session-level evaluator per candidate config in evaluators/ (once, IDs are
cached in output/evaluator_ids.json), then starts a batch evaluation over the explicit session
IDs of the selected split. Each session carries its SME-approved assertions as inline ground
truth. Batch evaluation reads the stored spans from CloudWatch Logs; it does not re-invoke
the agent.

Pass --repeats N to run the same job N times and measure judge repeatability.

Usage:
    python 05_run_batch_evaluation.py --split tuning --evaluators v1 v2 [--repeats 2]
    python 05_run_batch_evaluation.py --split holdout --evaluators v2 --repeats 3

Output:
    output/evaluator_ids.json   - candidate label to evaluator ID
    output/batch_runs.json      - one record per batch job
    output/judge_results.json   - one record per session, evaluator, and repeat
"""

import argparse
import json
import time
import uuid
from datetime import datetime, timezone

import boto3
from common import (
    BATCH_RUNS_FILE,
    EVALUATOR_IDS_FILE,
    EVALUATORS_DIR,
    HUMAN_REFERENCE_FILE,
    JUDGE_RESULTS_FILE,
    OUTPUT_DIR,
    REVIEW_CASES_FILE,
    load_agent_config,
    read_json,
    write_json,
)

TERMINAL_STATUSES = {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "STOPPED"}


def ensure_evaluators(control, labels: list[str]) -> dict[str, str]:
    """Create each candidate once. A new version is a new evaluator, never an in-place edit."""
    ids = read_json(EVALUATOR_IDS_FILE) if EVALUATOR_IDS_FILE.exists() else {}
    suffix = uuid.uuid4().hex[:6]
    for label in labels:
        if label in ids:
            continue
        config = read_json(EVALUATORS_DIR / f"claims_outcome_judge_{label}.json")
        response = control.create_evaluator(
            evaluatorName=f"ClaimsOutcomeJudge_{label}_{suffix}",
            description=f"Claims outcome judge candidate {label}",
            level="SESSION",
            evaluatorConfig=config,
        )
        ids[label] = response["evaluatorId"]
        print(f"  Created evaluator {label}: {ids[label]}")
        write_json(EVALUATOR_IDS_FILE, ids)
    return ids


def start_batch(client, config: dict, cases: list[dict], evaluator_ids: list[str]) -> str:
    session_metadata = [
        {
            "sessionId": case["session_id"],
            "testScenarioId": case["case_id"],
            "groundTruth": {
                "inline": {"assertions": [{"text": assertion} for assertion in case["ground_truth"]["assertions"]]}
            },
        }
        for case in cases
    ]
    response = client.start_batch_evaluation(
        batchEvaluationName=f"judge_calibration_{uuid.uuid4().hex[:8]}",
        evaluators=[{"evaluatorId": evaluator_id} for evaluator_id in evaluator_ids],
        dataSourceConfig={
            "cloudWatchLogs": {
                "serviceNames": [config["otel_service_name"]],
                "logGroupNames": ["aws/spans", config["cw_log_group"]],
                "filterConfig": {"sessionIds": [case["session_id"] for case in cases]},
            }
        },
        evaluationMetadata={"sessionMetadata": session_metadata},
        clientToken=str(uuid.uuid4()),
    )
    return response["batchEvaluationId"]


def wait_for_batch(client, batch_id: str, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        batch = client.get_batch_evaluation(batchEvaluationId=batch_id)
        results = batch.get("evaluationResults", {})
        print(
            f"    {batch['status']}: {results.get('numberOfSessionsCompleted', 0)}"
            f"/{results.get('totalNumberOfSessions', '?')} sessions"
        )
        if batch["status"] in TERMINAL_STATUSES:
            return batch
        if time.monotonic() > deadline:
            raise TimeoutError(f"Batch {batch_id} did not finish within {timeout}s")
        time.sleep(20)


def read_result_events(logs, output: dict) -> list[dict]:
    """Read every event from the batch output stream, following pagination to the end."""
    events, token = [], None
    while True:
        kwargs = {
            "logGroupName": output["logGroupName"],
            "logStreamName": output["logStreamName"],
            "startFromHead": True,
        }
        if token:
            kwargs["nextToken"] = token
        page = logs.get_log_events(**kwargs)
        events.extend(json.loads(event["message"]) for event in page["events"])
        if page["nextForwardToken"] == token:
            return events
        token = page["nextForwardToken"]


def parse_result(event: dict, label_by_id: dict[str, str], case_by_session: dict[str, str]) -> dict | None:
    attributes = event.get("attributes") or {}
    session_id = attributes.get("session.id")
    evaluator_id = attributes.get("gen_ai.evaluation.evaluator.id") or attributes.get(
        "aws.bedrock_agentcore.evaluator.id"
    )
    name = attributes.get("gen_ai.evaluation.name", "")
    label = label_by_id.get(evaluator_id) or next(
        (lbl for eid, lbl in label_by_id.items() if eid.startswith(name)), None
    )
    if session_id not in case_by_session or label is None:
        return None
    score = attributes.get("gen_ai.evaluation.score.value")
    return {
        "case_id": case_by_session[session_id],
        "session_id": session_id,
        "evaluator": label,
        "evaluator_id": evaluator_id,
        "score": int(score) if score is not None else None,
        "label": attributes.get("gen_ai.evaluation.score.label"),
        "explanation": attributes.get("gen_ai.evaluation.explanation"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=["tuning", "holdout", "all"], default="tuning")
    parser.add_argument("--evaluators", nargs="+", default=["v1", "v2"], help="Candidate labels in evaluators/")
    parser.add_argument("--repeats", type=int, default=1, help="Batch jobs to run for repeatability")
    parser.add_argument("--timeout", type=int, default=1800, help="Seconds to wait for each batch job")
    args = parser.parse_args()

    config = load_agent_config()
    control = boto3.client("bedrock-agentcore-control", region_name=config["region"])
    client = boto3.client("bedrock-agentcore", region_name=config["region"])
    logs = boto3.client("logs", region_name=config["region"])

    bundle = read_json(REVIEW_CASES_FILE)
    splits = {case["case_id"]: case["split"] for case in read_json(HUMAN_REFERENCE_FILE)["cases"]}
    cases = [case for case in bundle["cases"] if args.split == "all" or splits[case["case_id"]] == args.split]
    print(f"Scoring {len(cases)} {args.split} sessions with {', '.join(args.evaluators)} x{args.repeats}")

    ids = ensure_evaluators(control, args.evaluators)
    label_by_id = {ids[label]: label for label in args.evaluators}
    case_by_session = {case["session_id"]: case["case_id"] for case in cases}

    runs = read_json(BATCH_RUNS_FILE) if BATCH_RUNS_FILE.exists() else []
    results = read_json(JUDGE_RESULTS_FILE) if JUDGE_RESULTS_FILE.exists() else []
    for repeat in range(1, args.repeats + 1):
        batch_id = start_batch(client, config, cases, list(label_by_id))
        print(f"  Batch {repeat}/{args.repeats}: {batch_id}")
        batch = wait_for_batch(client, batch_id, args.timeout)
        output = batch["outputConfig"]["cloudWatchConfig"]
        events = read_result_events(logs, output)
        write_json(OUTPUT_DIR / "batch_events" / f"{batch_id}.json", events)

        parsed = [record for event in events if (record := parse_result(event, label_by_id, case_by_session))]
        keys = [(record["case_id"], record["evaluator"]) for record in parsed]
        expected = {(case_id, label) for case_id in case_by_session.values() for label in args.evaluators}
        missing = sorted(expected - set(keys))
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if missing or duplicates:
            print(f"    WARNING: missing results {missing}, duplicate results {duplicates}")

        run_record = {
            "batch_id": batch_id,
            "status": batch["status"],
            "split": args.split,
            "repeat": repeat,
            "evaluators": {label: ids[label] for label in args.evaluators},
            "ground_truth_version": bundle["ground_truth_version"],
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "summaries": batch.get("evaluationResults", {}).get("evaluatorSummaries", []),
            "missing_results": [list(key) for key in missing],
        }
        runs.append(run_record)
        results.extend({**record, "batch_id": batch_id, "split": args.split, "repeat": repeat} for record in parsed)
        write_json(BATCH_RUNS_FILE, runs)
        write_json(JUDGE_RESULTS_FILE, results)

        for record in sorted(parsed, key=lambda r: (r["case_id"], r["evaluator"])):
            print(f"    {record['case_id']} {record['evaluator']}: {record['score']} {record['label']}")


if __name__ == "__main__":
    main()
