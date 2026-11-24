"""Progress, cancellation, and HealthyBusy on AgentCore Runtime with MCP 2026-07-28.

Part 1 streams a report with boto3 and prints each progress notification as it arrives.
Part 2 cancels a report by closing the response stream, the MCP 2026-07-28 cancellation signal, and
asks the same microVM how the report ended. Through AgentCore Runtime today it runs to the end, so part
2 then stops a background job with cancel_report_job instead.
Part 3 gets the same progress through the MCP Python SDK v2 client.
With --idle-check, part 4 shows that a background job, reported as HealthyBusy on GET /ping, keeps
a runtime session alive past the idle timeout (it waits about 4 minutes).

Usage: python invoke.py [--idle-check]
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "helpers"))
from deploy_utils import load_config
from dynamo_utils import FinanceDB
from mcp_client import LATEST, McpClient, new_session_id, runtime_url, sdk_http_client, show

USER = "alice"


def seed(table: str) -> None:
    """Start every run from the same data."""
    db = FinanceDB(table)
    db.clear_user(USER)
    for amount, description, category in [(230, "Groceries", "food"), (45, "Train pass", "transport")]:
        db.add_transaction(USER, "expense", -amount, description, category)
    db.set_budget(USER, "food", 200)


def stream_report(mcp: McpClient, step_seconds: float, stop_after: int | None = None) -> dict[str, Any] | None:
    """Call generate_report and print its progress. With stop_after, close the stream after that many updates."""
    started = time.monotonic()
    arguments = {"user_alias": USER, "step_seconds": step_seconds}
    params = {"name": "generate_report", "arguments": arguments}
    updates = 0
    for message in mcp.stream("tools/call", params, name="generate_report", progress_token="report-1"):
        elapsed = time.monotonic() - started
        if message.get("method") == "notifications/progress":
            p = message["params"]
            print(f"   {elapsed:5.1f}s  progress {p['progress']:.0f}/{p['total']:.0f}  {p.get('message')}")
            updates += 1
            if updates == stop_after:
                print(f"   {elapsed:5.1f}s  closing the response stream")
                return None  # leaving the loop closes the stream
        else:
            print(f"   {elapsed:5.1f}s  result")
            return message
    return None


def wait_for_report(mcp: McpClient, limit: float = 40) -> str:
    """Poll the same microVM until the report it was building stops running."""
    started = time.monotonic()
    while True:
        status = mcp.call_tool("server_status").structured["last_report"]
        if status != "running" or time.monotonic() - started > limit:
            return f"{status} ({time.monotonic() - started:.0f}s after the stream closed)"
        time.sleep(2)


async def with_sdk_client(arn: str, region: str) -> None:
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    started = time.monotonic()

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        print(f"   {time.monotonic() - started:5.1f}s  progress {progress:.0f}/{total:.0f}  {message}")

    http = sdk_http_client(region, new_session_id())
    transport = streamable_http_client(runtime_url(arn, region), http_client=http, terminate_on_close=False)
    async with http, Client(transport, mode=LATEST) as client:
        result = await client.call_tool(
            "generate_report", {"user_alias": USER, "step_seconds": 0.5}, progress_callback=on_progress
        )
        print(f"── SDK generate_report\n   {json.dumps(result.structured_content)[:200]}…")


def idle_check(arn: str, region: str, idle_timeout: int) -> bool:
    """Two idle sessions; only the one with a background job (HealthyBusy) survives the idle timeout."""
    wait = idle_timeout + 90
    idle = McpClient(arn, region, session_id=new_session_id())
    busy = McpClient(arn, region, session_id=new_session_id())
    idle_before = idle.call_tool("server_status").structured
    job = busy.call_tool("start_report_job", {"seconds": wait + 120}).structured
    print(f"   session A, no job:  instance {idle_before['instance_id']}  ping {idle_before['ping_status']}")
    print(f"   session B, job:     instance {job['instance_id']}  ping {job['ping_status']}  {job['job_id']}")
    print(f"   waiting {wait}s with no requests (idle timeout {idle_timeout}s)…", flush=True)
    time.sleep(wait)
    idle_after = idle.call_tool("server_status").structured
    busy_after = busy.call_tool("get_report_job", {"job_id": job["job_id"]})
    replaced = idle_after["instance_id"] != idle_before["instance_id"]
    print(f"   session A:  instance {idle_after['instance_id']}  → new microVM: {replaced}")
    if busy_after.structured:
        kept = busy_after.structured["instance_id"] == job["instance_id"]
        print(
            f"   session B:  instance {busy_after.structured['instance_id']}  job {busy_after.structured['status']}"
            f"  → same microVM: {kept}"
        )
    else:
        kept = False
        show("session B get_report_job", busy_after)
    return replaced and kept


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--idle-check", action="store_true", help="also run the HealthyBusy idle check (~4 min)")
    args = parser.parse_args()
    config = load_config(HERE)
    arn, region = config["runtime_arn"], config["region"]
    seed(config["table"])

    print("═══ Part 1: progress notifications, streamed with boto3 ═══")
    result = stream_report(McpClient(arn, region, session_id=new_session_id()), step_seconds=1.0)
    print("\n".join(f"   │ {line}" for line in result["result"]["structuredContent"]["report"].splitlines()))

    print("\n═══ Part 2: cancellation ═══")
    print("Closing the response stream:")
    mcp = McpClient(arn, region, session_id=new_session_id())  # one session, so every call reaches one microVM
    stream_report(mcp, step_seconds=3.0, stop_after=2)
    print(f"   the report on the server: {wait_for_report(mcp)}")
    print("Cancelling a background job with a tool:")
    job = mcp.call_tool("start_report_job", {"seconds": 600}).structured
    print(f"   started {job['job_id']}  ping {job['ping_status']}")
    show("cancel_report_job", mcp.call_tool("cancel_report_job", {"job_id": job["job_id"]}))

    print("\n═══ Part 3: the MCP Python SDK v2 client ═══")
    asyncio.run(with_sdk_client(arn, region))

    if args.idle_check:
        print("\n═══ Part 4: HealthyBusy keeps a session alive ═══")
        sys.exit(0 if idle_check(arn, region, config["idle_timeout"]) else 1)
