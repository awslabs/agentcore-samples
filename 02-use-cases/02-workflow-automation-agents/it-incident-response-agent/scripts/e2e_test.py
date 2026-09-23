#!/usr/bin/env python3
"""
End-to-end test with intermediate step visibility and timing.

Publishes a ticket to SNS, then polls DynamoDB and CloudWatch Logs
to show the agent's processing steps in real time.

Usage:
    python scripts/e2e_test.py                    # Use default sample ticket
    python scripts/e2e_test.py --priority LOW     # Override priority
    python scripts/e2e_test.py --ticket /path/to/ticket.json

Requires: boto3, AWS credentials configured, stack deployed.
"""

import argparse
import json
import os
import sys
import time
import threading
import uuid
from datetime import datetime, timezone

import boto3

# ─── Configuration ────────────────────────────────────────────────────────────

REGION = os.environ.get("AWS_REGION", "us-west-2")
# NOTE: STACK_NAME must match the CDK naming convention: AgentCore-{projectName}-{targetName}
# where projectName comes from agentcore.json "name" and targetName from aws-targets.json.
STACK_NAME = os.environ.get("STACK_NAME", "AgentCore-ITIncidentAgent-dev")
POLL_INTERVAL = 5  # seconds between DDB polls
LOG_POLL_INTERVAL = 3  # seconds between log polls
MAX_WAIT = 180  # max seconds to wait for resolution
RUNTIME_LOG_GROUP_PREFIX = "/aws/bedrock-agentcore/runtimes/ITIncidentAgent"
SPANS_LOG_GROUP = "/aws/spans"


# ─── Helpers ──────────────────────────────────────────────────────────────────


def get_stack_outputs():
    """Fetch CloudFormation stack outputs."""
    cfn = boto3.client("cloudformation", region_name=REGION)
    resp = cfn.describe_stacks(StackName=STACK_NAME)
    outputs = {}
    for o in resp["Stacks"][0].get("Outputs", []):
        outputs[o["OutputKey"]] = o["OutputValue"]
    return outputs


def elapsed(start: float) -> str:
    """Format elapsed time."""
    return f"{time.time() - start:6.1f}s"


def print_step(start: float, emoji: str, message: str):
    """Print a timestamped step."""
    print(f"[{elapsed(start)}] {emoji} {message}")


# ─── Core Logic ───────────────────────────────────────────────────────────────


def publish_ticket(topic_arn: str, ticket: dict) -> str:
    """Publish ticket to SNS and return message ID."""
    sns = boto3.client("sns", region_name=REGION)
    resp = sns.publish(
        TopicArn=topic_arn,
        Message=json.dumps(ticket),
    )
    return resp["MessageId"]


def poll_ticket_status(table_name: str, ticket_id: str, start: float) -> dict:
    """Poll DynamoDB for ticket status changes. Returns final item."""
    ddb = boto3.client("dynamodb", region_name=REGION)
    last_status = None
    deadline = time.time() + MAX_WAIT

    while time.time() < deadline:
        try:
            resp = ddb.get_item(
                TableName=table_name,
                Key={"ticket_id": {"S": ticket_id}},
            )
            item = resp.get("Item", {})
            status = item.get("status", {}).get("S", "Not Found")

            if status != last_status:
                emoji = {
                    "Open": "📝",
                    "Processing": "⚙️",
                    "Resolved": "✅",
                    "Failed": "❌",
                    "Escalated": "⬆️",
                }.get(status, "❓")
                print_step(start, emoji, f"Status: {status}")
                last_status = status

            if status in ("Resolved", "Failed", "Escalated"):
                return item

        except Exception as e:
            if "Not Found" not in str(e):
                print_step(start, "⚠️", f"DDB poll error: {e}")

        time.sleep(POLL_INTERVAL)

    print_step(start, "⏰", f"Timed out after {MAX_WAIT}s")
    return {}


def tail_runtime_logs(ticket_id: str, start: float, stop_event: threading.Event):
    """
    Poll CloudWatch Logs for agent processing events.
    Checks runtime logs for app-level messages and OTEL spans for tool calls.
    """
    logs_client = boto3.client("logs", region_name=REGION)

    # Find the runtime log group (most recently created one)
    try:
        resp = logs_client.describe_log_groups(
            logGroupNamePrefix=RUNTIME_LOG_GROUP_PREFIX,
        )
        if not resp.get("logGroups"):
            return
        # Use the most recently created log group
        log_groups = sorted(resp["logGroups"], key=lambda g: g.get("creationTime", 0), reverse=True)
        log_group = log_groups[0]["logGroupName"]
    except Exception:
        return

    seen_events = set()
    seen_tools = set()
    log_start_ms = int((time.time() - 10) * 1000)

    while not stop_event.is_set():
        try:
            # Check runtime logs for app-level events
            resp = logs_client.filter_log_events(
                logGroupName=log_group,
                startTime=log_start_ms,
                limit=50,
            )
            for event in resp.get("events", []):
                event_id = event.get("eventId", "")
                if event_id in seen_events:
                    continue
                seen_events.add(event_id)
                msg = event.get("message", "")

                # Skip if not related to our ticket
                if ticket_id not in msg:
                    # Also check for tool call patterns (not ticket-specific)
                    _check_tool_log(msg, start, seen_tools)
                    continue

                # App-level messages with ticket ID
                if "Processing ticket" in msg:
                    print_step(start, "⚡", "Agent processing started")
                elif "Gateway MCP client loaded" in msg:
                    print_step(start, "🔌", "Connected to MCP Gateway")
                elif "Resolved" in msg and "event" not in msg.lower():
                    print_step(start, "📝", "Agent marked ticket resolved")
                elif "Emitted TicketResolved" in msg:
                    print_step(start, "📡", "EventBridge event emitted")
                elif "Failed to process" in msg:
                    print_step(start, "💥", "Agent processing failed")

        except Exception:
            pass

        # Also check spans for tool call detail
        try:
            _check_spans_for_tools(logs_client, start, log_start_ms, seen_tools)
        except Exception:
            pass

        time.sleep(LOG_POLL_INTERVAL)


def _check_tool_log(msg: str, start: float, seen_tools: set):
    """Check a log message for tool invocation patterns."""
    # Strands hooks log: "Tool call started: <name>" and "Tool call completed: <name> (XXms)"
    if "Tool call started:" in msg:
        tool_name = msg.split("Tool call started:")[-1].strip().split()[0]
        if tool_name and f"start-{tool_name}" not in seen_tools:
            seen_tools.add(f"start-{tool_name}")
            print_step(start, "🔧", f"Tool call: {tool_name} (started)")
    elif "Tool call completed:" in msg:
        # Extract tool name and duration
        parts = msg.split("Tool call completed:")[-1].strip()
        tool_name = parts.split("(")[0].strip()
        duration = ""
        if "(" in parts and "ms)" in parts:
            duration = parts.split("(")[-1].rstrip(")")
        if tool_name and f"end-{tool_name}" not in seen_tools:
            seen_tools.add(f"end-{tool_name}")
            suffix = f" ({duration})" if duration else ""
            print_step(start, "✓", f"Tool done: {tool_name}{suffix}")

    # Gateway HTTP POST (tool execution over MCP)
    if "HTTP Request: POST" in msg and "gateway.bedrock-agentcore" in msg and "200 OK" in msg:
        pass  # Individual tool calls are now captured via hooks above


def _check_spans_for_tools(logs_client, start: float, log_start_ms: int, seen_tools: set):
    """Query /aws/spans for OTEL spans showing tool invocations."""
    try:
        resp = logs_client.filter_log_events(
            logGroupName=SPANS_LOG_GROUP,
            startTime=log_start_ms,
            filterPattern='"ITIncidentAgent"',
            limit=20,
        )
        for event in resp.get("events", []):
            msg = event.get("message", "")
            try:
                span = json.loads(msg)
                span_name = span.get("name", "")
                # Tool spans typically have the tool name in the span name
                known = ["lookup-user", "get-process-info", "create-change-request", "query-kb"]
                for tool in known:
                    if tool in span_name and tool not in seen_tools:
                        seen_tools.add(tool)
                        duration_ns = span.get("duration", 0)
                        duration_ms = duration_ns / 1_000_000  # OTEL span durations are always ns
                        print_step(start, "🔧", f"Tool: {tool} ({duration_ms:.0f}ms)")
            except (json.JSONDecodeError, KeyError):
                pass
    except Exception:
        pass  # /aws/spans may not exist if Transaction Search isn't enabled


def _extract_model_info(msg: str) -> str:
    """Extract model selection info."""
    if "haiku" in msg.lower():
        return "Model: Haiku (cost routing: LOW priority)"
    elif "sonnet" in msg.lower():
        return "Model: Sonnet (standard priority)"
    return "Model selected"


def _final_log_sweep(ticket_id: str, start: float):
    """After resolution, sweep logs for tool call events that arrived late."""
    logs_client = boto3.client("logs", region_name=REGION)
    try:
        resp = logs_client.describe_log_groups(
            logGroupNamePrefix=RUNTIME_LOG_GROUP_PREFIX,
        )
        if not resp.get("logGroups"):
            return
        log_groups = sorted(resp["logGroups"], key=lambda g: g.get("creationTime", 0), reverse=True)
        log_group = log_groups[0]["logGroupName"]

        # Fetch events from the last 2 minutes
        log_start_ms = int((time.time() - 120) * 1000)
        resp = logs_client.filter_log_events(
            logGroupName=log_group,
            startTime=log_start_ms,
            limit=200,
        )

        tool_events = []
        for event in resp.get("events", []):
            msg = event.get("message", "")
            if "Tool call started:" in msg or "Tool call completed:" in msg:
                tool_events.append((event["timestamp"], msg))

        if tool_events:
            # Adjust to use the overall test start time
            print()
            print("  Tool call timeline (from runtime logs):")
            print()
            for _ts, msg in tool_events:
                if "Tool call started:" in msg:
                    tool_name = msg.split("Tool call started:")[-1].strip().split()[0]
                    print(f"    🔧 {tool_name} (started)")
                elif "Tool call completed:" in msg:
                    parts = msg.split("Tool call completed:")[-1].strip()
                    tool_name = parts.split("(")[0].strip()
                    duration = ""
                    if "(" in parts and "ms)" in parts:
                        duration = parts.split("(")[-1].rstrip(")")
                    print(f"    ✓  {tool_name} ({duration})")
    except Exception:
        pass


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    """Parse CLI args, submit a test ticket, and report step-by-step results."""
    parser = argparse.ArgumentParser(description="E2E test with step visibility")
    parser.add_argument("--ticket", help="Path to ticket JSON file")
    parser.add_argument("--priority", default="MEDIUM", choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"])
    parser.add_argument("--requester", default="U-1002", help="Requester ID (must exist in Users table)")
    parser.add_argument("--title", default=None, help="Override ticket title")
    parser.add_argument("--no-logs", action="store_true", help="Skip log tailing (faster, less detail)")
    args = parser.parse_args()

    # Build ticket
    if args.ticket:
        with open(args.ticket, encoding="utf-8") as f:
            ticket = json.load(f)
    else:
        ticket_id = f"E2E-{uuid.uuid4().hex[:8].upper()}"
        ticket = {
            "ticket_id": ticket_id,
            "title": args.title or "E2E test: VPN disconnects intermittently",
            "description": (
                "Since the latest Windows update (KB5039302), Cisco AnyConnect VPN "
                "drops every 10 minutes exactly. Tried reinstalling the client and "
                "flushing DNS. Other users on the same network are not affected."
            ),
            "requester_id": args.requester,
            "priority": args.priority,
            "category": "network",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    ticket_id = ticket["ticket_id"]

    # Get stack resources
    print(f"\n{'=' * 60}")
    print(f"  E2E Test: {ticket_id} ({ticket['priority']} priority)")
    print(f"{'=' * 60}\n")

    try:
        outputs = get_stack_outputs()
    except Exception as e:
        print(f"❌ Failed to get stack outputs: {e}")
        print(f"   Is the stack '{STACK_NAME}' deployed?")
        sys.exit(1)

    topic_arn = None
    table_name = None
    for key, val in outputs.items():
        if "TicketsTopicArn" in key:
            topic_arn = val
        if "TicketsTableName" in key:
            table_name = val

    if not topic_arn or not table_name:
        print("❌ Could not find TicketsTopicArn or TicketsTableName in stack outputs")
        sys.exit(1)

    # Start timing
    start = time.time()

    # Step 1: Publish
    msg_id = publish_ticket(topic_arn, ticket)
    print_step(start, "📤", f"Published to SNS (MessageId: {msg_id[:12]}...)")

    # Step 2: Poll for status changes + optionally tail logs
    print()
    print("  Watching for status changes...")
    print()

    if not args.no_logs:
        # Interleave log tailing with status polling
        stop_flag = threading.Event()
        log_thread = threading.Thread(
            target=tail_runtime_logs,
            args=(ticket_id, start, stop_flag),
            daemon=True,
        )
        log_thread.start()

    final_item = poll_ticket_status(table_name, ticket_id, start)

    # Signal the log thread to stop
    if not args.no_logs:
        stop_flag.set()

    # Final log sweep: CloudWatch logs have ~5-15s delivery delay.
    # After resolution, wait briefly and fetch any tool call logs that arrived late.
    if not args.no_logs and final_item:
        time.sleep(8)  # Wait for log delivery
        _final_log_sweep(ticket_id, start)

    # Step 3: Report
    total_time = time.time() - start
    print()
    print(f"{'─' * 60}")
    print(f"  Total time: {total_time:.1f}s")

    if final_item:
        status = final_item.get("status", {}).get("S", "Unknown")
        resolution = final_item.get("resolution_comment", {}).get("S", "")

        if status == "Resolved" and resolution:
            # Truncate long resolutions for display
            display = resolution[:300] + "..." if len(resolution) > 300 else resolution
            print(f"  Status: ✅ {status}")
            print(f"\n  Resolution:\n  {display}")
        elif status == "Failed":
            error = final_item.get("error_message", {}).get("S", "No error details")
            print(f"  Status: ❌ {status}")
            print(f"  Error: {error}")
        else:
            print(f"  Status: {status}")

    print(f"{'─' * 60}\n")
    return 0 if final_item.get("status", {}).get("S") == "Resolved" else 1


if __name__ == "__main__":
    sys.exit(main())
