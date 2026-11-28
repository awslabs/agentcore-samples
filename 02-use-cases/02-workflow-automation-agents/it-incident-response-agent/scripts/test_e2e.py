#!/usr/bin/env python3
"""Bounded, target-bound synthetic scenario verification (authorized live use only).

No quick/no-eval mode returns a full-suite success. Every evidence artifact stays
in .e2e/<run> on failure. This reads online outcomes; it does not run an evaluator.
"""

import argparse
import fcntl
import json
import time
import uuid

import evaluate
from boto3.dynamodb.types import TypeDeserializer
from lifecycle import ROOT, Deployment, save
from verify_evidence import EVALUATORS, verify


def wait_for(check, timeout, label):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(10)
    raise TimeoutError(label + " not observed before deadline")


def run_suite(deployment, jira_events=None):
    stack = deployment.owned()
    jira_configured = any(o["OutputKey"] == "JiraOauthProviderArn" for o in stack.get("Outputs", []))
    if jira_configured != bool(jira_events):
        raise ValueError(
            "Configured Jira requires --jira-events with three approved synthetic incidents; do not silently skip the optional mode"
        )
    if jira_events and (
        len(jira_events) != 3
        or {e.get("priority") for e in jira_events} != {"LOW", "HIGH", "CRITICAL"}
        or len({e.get("issue_key") for e in jira_events}) != 3
        or len({e.get("requester_id") for e in jira_events}) != 1
        or any(
            e.get("synthetic") is not True or not e.get("issue_key") or not e.get("requester_id") for e in jira_events
        )
    ):
        raise ValueError(
            "Jira E2E requires three distinct synthetic issues, one approved requester, and LOW/HIGH/CRITICAL priorities"
        )
    config = deployment.evaluation_config()
    if {e["evaluatorId"] for e in config["evaluators"]} != EVALUATORS:
        raise ValueError("Full suite requires all four preserved evaluators")
    if config["rule"]["samplingConfig"]["samplingPercentage"] != 100:
        raise ValueError("Full suite requires observed 100% evaluation sampling")
    group = config["outputConfig"]["cloudWatchConfig"]["logGroupName"]
    evaluate.REGION = deployment.binding["region"]
    table = deployment.output_resource("AWS::DynamoDB::Table", "TicketsTableName")
    memory = deployment.resource("AWS::BedrockAgentCore::Memory").split("/")[-1]
    run_id = uuid.uuid4().hex[:12]
    folder = ROOT / ".e2e" / run_id
    folder.mkdir(parents=True, mode=0o700)
    started = time.time()
    save(
        folder / "run.json",
        {
            "run_id": run_id,
            "started_at": started,
            "stack_id": deployment.ledger["stack_id"],
            "binding": deployment.binding,
            "status": "RUNNING",
        },
    )
    decoder = TypeDeserializer()
    tickets = []
    for priority in ("LOW", "HIGH", "CRITICAL"):
        ticket = {
            "ticket_id": f"E2E-{priority}-{run_id}",
            "requester_id": "U-1002",
            "synthetic": True,
            "title": f"Outlook search not working - synthetic recurrence {run_id}",
            "description": "Outlook on macOS shows no search results after restart. Diagnose with the Outlook process and runbook, and record the recommended corrective action. This is synthetic test data.",
            "category": "email",
            "priority": priority,
        }
        if jira_events:
            event = next(e for e in jira_events if e["priority"] == priority)
            ticket = {**event, "ticket_id": event["issue_key"]}
        file = folder / f"{priority}.input.json"
        save(file, {k: v for k, v in ticket.items() if k != "ticket_id"} if jira_events else ticket)
        deployment.publish(file)

        def resolved(ticket=ticket, priority=priority):
            item = deployment.aws(
                "dynamodb",
                "get-item",
                "--table-name",
                table,
                "--consistent-read",
                "--key",
                json.dumps({"ticket_id": {"S": ticket["ticket_id"]}}),
            ).get("Item", {})
            row = {k: decoder.deserialize(v) for k, v in item.items()}
            if row.get("status") == "Failed":
                save(folder / f"{priority}.failed.json", row)
                raise RuntimeError("Incident processing failed; evidence retained")
            return row if row.get("status") == "Resolved" else None

        row = wait_for(resolved, 300, "Ticket resolution")
        save(folder / f"{priority}.result.json", row)
        tickets.append(row)
        # Wait for THIS session's summary before publishing the next recurrence.
        # An old summary or DDB history count cannot satisfy this check.
        session = row.get("workflow_evidence", {}).get("session_id")
        if not session:
            raise ValueError("Resolved ticket lacks workflow session evidence")
        if (
            jira_events
            and len(
                {
                    t["name"]
                    for t in row["workflow_evidence"].get("tools", [])
                    if t["name"].startswith("jira_") and t["status"] == "success"
                }
            )
            != 3
        ):
            raise ValueError("Correlated Jira read/comment/transition results are missing")
        for tool in row["workflow_evidence"].get("tools", []):
            if tool["name"].split("___")[-1] != "create_change_request":
                continue
            change_id = tool.get("result_ids", {}).get("change_id")
            if not change_id:
                raise ValueError("Change tool result has no correlated record ID")
            changes_table = deployment.resource("AWS::DynamoDB::Table", "ChangeRequestsTable")
            change = deployment.aws(
                "dynamodb",
                "get-item",
                "--table-name",
                changes_table,
                "--consistent-read",
                "--key",
                json.dumps({"change_id": {"S": change_id}}),
            ).get("Item", {})
            if (
                change.get("ticket_id", {}).get("S") != ticket["ticket_id"]
                or change.get("user_id", {}).get("S") != ticket["requester_id"]
                or change.get("status", {}).get("S") != "applied"
            ):
                raise ValueError("Actual change record does not correlate with this incident")
            save(folder / f"{priority}.{change_id}.json", change)
        expected = f"incidents/{ticket['requester_id']}/{session}"

        def memory_ready(ticket=ticket, priority=priority, expected=expected):
            response = deployment.aws(
                "bedrock-agentcore",
                "retrieve-memory-records",
                "--memory-id",
                memory,
                "--namespace-path",
                f"incidents/{ticket['requester_id']}",
                "--search-criteria",
                json.dumps({"searchQuery": ticket["ticket_id"], "topK": 20}),
            )
            save(folder / f"{priority}.memory.json", response)
            return any(
                expected in r.get("namespaces", []) and r.get("memoryRecordId")
                for r in response.get("memoryRecordSummaries", [])
            )

        wait_for(memory_ready, 600, "Cross-session Memory extraction")
    session_timeout = config["rule"].get("sessionConfig", {}).get("sessionTimeoutMinutes", 15)
    deadline = time.monotonic() + min(4200, session_timeout * 60 + 600)
    while True:
        results = evaluate.query_eval_results(group, hours=1)
        save(folder / "evaluations.json", results)
        try:
            summary = verify(tickets, results, started)
            break
        except ValueError as error:
            save(folder / "incomplete.json", {"error": str(error)})
            if time.monotonic() >= deadline:
                raise
            time.sleep(20)
    save(folder / "complete.json", summary)
    print(
        f"Correlated evidence collected in {folder}. Inspect scores; LOG_ONLY did not enforce policy. Not acceptance."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("project", "target", "account", "region"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--no-eval", action="store_true")
    parser.add_argument(
        "--jira-events", help="JSON array of three approved synthetic Jira events; required when Jira is configured"
    )
    args = parser.parse_args()
    if args.quick or args.no_eval:
        parser.exit(2, "Full E2E NOT RUN: skips cannot satisfy tool/Memory/evaluator verification\n")
    with (ROOT / ".sample-operation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        from pathlib import Path

        jira_events = json.loads(Path(args.jira_events).read_text()) if args.jira_events else None
        run_suite(Deployment(ROOT, args.project, args.target, args.account, args.region), jira_events)


if __name__ == "__main__":
    main()
