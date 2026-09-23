"""
Travel Guide Agent — AgentCore Harness Use Case.

A complete travel guide agent demonstrating all core Harness features:
  Part 1: Create Harness (control plane)
  Part 2: Invoke agent — generate an HTML travel guide
  Part 3: Save HTML output to file (replaces notebook iframe rendering)
  Part 4: Add AgentCore Memory — multi-turn conversation with persistence
  Part 5: Browser tool — live weather data from real websites
  Part 6: MCP (Exa search) + Code Interpreter — data analysis & chart generation

Usage:
    python travel_agent.py

    # Skip memory provisioning (faster, no multi-turn demo)
    python travel_agent.py --skip-memory

    # Keep resources after demo (useful for debugging)
    python travel_agent.py --skip-cleanup

Prerequisites:
    - AWS CLI configured with credentials
    - pip install -r ../../requirements.txt
    - AWS_DEFAULT_REGION environment variable set
"""

import argparse
import base64
import sys
import time
import uuid
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.client import get_agentcore_client, get_agentcore_control_client
from utils.harness import poll_harness_status
from utils.iam import create_harness_role, delete_harness_role

# ── CLI ────────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Travel Guide Agent — Harness use case demo")
parser.add_argument("--skip-memory", action="store_true", help="Skip memory provisioning")
parser.add_argument("--skip-cleanup", action="store_true", help="Keep resources after demo")
args = parser.parse_args()

# ── Configuration ─────────────────────────────────────────────────────────────
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
MEMORY_NAME = "TravelGuideMemory"

# Memory provisioning is slower than a harness and has its own status enum:
# CREATING → ACTIVE, with a plain FAILED (unlike the harness, which reports
# CREATE_FAILED and has no bare FAILED). There is no READY for a memory.
MEMORY_POLL_INTERVAL = 10
MEMORY_POLL_TIMEOUT = 600

# ── Setup ─────────────────────────────────────────────────────────────────────
control = get_agentcore_control_client()
client = get_agentcore_client()

account_id = boto3.client("sts").get_caller_identity()["Account"]
print(f"Account: {account_id}")

# ── Helper ────────────────────────────────────────────────────────────────────


def stream_response(harness_arn, session_id, message, tools=None):
    """Invoke harness and stream the response. Returns accumulated text."""
    kwargs = {
        "harnessArn": harness_arn,
        "runtimeSessionId": session_id,
        "messages": [{"role": "user", "content": [{"text": message}]}],
        "model": {"bedrockModelConfig": {"modelId": MODEL_ID}},
    }
    if tools:
        kwargs["tools"] = tools

    response = client.invoke_harness(**kwargs)
    full_text = ""
    for event in response["stream"]:
        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                print(f"\n[Tool: {start['toolUse'].get('name', '?')}]", flush=True)
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                print(delta["text"], end="", flush=True)
                full_text += delta["text"]
        elif "messageStop" in event:
            print()
        elif "internalServerException" in event:
            print(f"\nError: {event['internalServerException']}")
    return full_text


def run_command(harness_arn, session_id, cmd):
    """Run a shell command on the agent's remote microVM."""
    print(f"$ {cmd}")
    output = ""
    resp = client.invoke_agent_runtime_command(
        agentRuntimeArn=harness_arn,
        runtimeSessionId=session_id,
        body={"command": cmd},
    )
    for event in resp["stream"]:
        if "chunk" in event:
            chunk = event["chunk"]
            if "contentDelta" in chunk:
                d = chunk["contentDelta"]
                if "stdout" in d:
                    print(d["stdout"], end="", flush=True)
                    output += d["stdout"]
                if "stderr" in d:
                    print(d["stderr"], end="", flush=True)
            elif "contentStop" in chunk:
                print(f"\n[exit: {chunk['contentStop']['exitCode']}]")
    print()
    return output


def fetch_file(harness_arn, session_id, remote_path):
    """Fetch a text file from the agent's VM."""
    content = ""
    resp = client.invoke_agent_runtime_command(
        agentRuntimeArn=harness_arn,
        runtimeSessionId=session_id,
        body={"command": f"cat {remote_path}"},
    )
    for event in resp["stream"]:
        if "chunk" in event:
            chunk = event["chunk"]
            if "contentDelta" in chunk and "stdout" in chunk["contentDelta"]:
                content += chunk["contentDelta"]["stdout"]
    return content


def poll_memory_status(memory_id, timeout=MEMORY_POLL_TIMEOUT):
    """Wait for a Memory to reach ACTIVE. Returns the memory dict.

    Raises RuntimeError on FAILED and TimeoutError if it never settles, so the
    caller cannot go on to attach a memory that is not usable yet.
    """
    deadline = time.monotonic() + timeout
    while True:
        memory = control.get_memory(memoryId=memory_id)["memory"]
        status = memory.get("status", "UNKNOWN")
        print(f"  Memory status: {status}")
        if status == "ACTIVE":
            return memory
        if status == "FAILED":
            raise RuntimeError(f"Memory {memory_id} FAILED: {memory.get('failureReason', 'no reason given')}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Memory {memory_id} not ACTIVE after {timeout}s (last status: {status})")
        time.sleep(MEMORY_POLL_INTERVAL)


harness_id = None
memory_id = None

try:
    # ── Part 1: Create Harness ────────────────────────────────────────────────
    print("\n=== Part 1: Create Harness ===")
    role_arn = create_harness_role()
    print(f"Role ARN: {role_arn}")
    print("Waiting for IAM propagation...")
    time.sleep(10)

    HARNESS_NAME = f"TravelGuideAgent_{uuid.uuid4().hex[:8]}"
    resp = control.create_harness(harnessName=HARNESS_NAME, executionRoleArn=role_arn)
    harness = resp["harness"]
    harness_id = harness["harnessId"]
    harness_arn = harness["arn"]
    print(f"Harness ID:  {harness_id}")
    print(f"Harness ARN: {harness_arn}")

    # A harness routinely takes ~2-3 minutes to reach READY, so wait for the real
    # status instead of a fixed number of polls — the old 12 x 5s ceiling expired
    # first and let the sample invoke a harness that was still CREATING.
    poll_harness_status(control, harness_id)
    print("✅ Harness ready")

    # ── Part 2: Invoke Agent — HTML Travel Guide ──────────────────────────────
    print("\n=== Part 2: Invoke Agent — HTML Travel Guide ===")
    session_id = str(uuid.uuid4()).upper()
    print(f"Session ID: {session_id}\n")

    stream_response(
        harness_arn,
        session_id,
        "Recommend three fun things to do in NYC on a rainy day. "
        "Save your answer as a single self-contained HTML file at /tmp/travel.html. "
        "The HTML should have a modern dark-themed design with horizontal swipeable cards "
        "(one per activity) that the user can navigate using left/right arrow buttons. "
        "Each card should include the activity name, a short description, and a practical tip. "
        "Include all CSS and JS inline.",
    )

    # ── Part 3: Save HTML to local file ──────────────────────────────────────
    print("\n=== Part 3: Save HTML output ===")
    html_content = fetch_file(harness_arn, session_id, "/tmp/travel.html")  # nosec B108
    if html_content:
        output_path = "/tmp/travel_guide.html"  # nosec B108
        with open(output_path, "w") as f:
            f.write(html_content)
        print(f"✅ Saved HTML to {output_path} ({len(html_content):,} chars)")
        print(f"   Open with: open {output_path}")
    else:
        print("⚠️  No HTML file found on agent VM")

    # ── Part 4: Add AgentCore Memory ─────────────────────────────────────────
    if not args.skip_memory:
        print("\n=== Part 4: AgentCore Memory — Multi-Turn Conversation ===")
        print("Creating Memory instance (takes ~3-5 minutes)...")
        # `memory_id` is only ever set when *this* run created the memory, so the
        # cleanup below can never delete a memory that already belonged to the
        # account. The previous version fell back to adopting any memory whose id
        # contained "TravelGuide" and then deleted it on the way out, which
        # destroyed a resource the sample did not own.
        try:
            resp = control.create_memory(
                name=MEMORY_NAME,
                eventExpiryDuration=30,
                description="Memory for TravelGuideAgent",
            )
        except control.exceptions.ConflictException:
            print(f"⚠️  A memory named {MEMORY_NAME} already exists — most likely left")
            print("    behind by an earlier run that did not finish cleaning up.")
            print("    Delete it, or re-run with --skip-memory. Skipping Part 4.")
            resp = None

        if resp:
            memory_id = resp["memory"]["id"]
            print(f"Memory ID: {memory_id}")

            memory = poll_memory_status(memory_id)
            memory_arn = memory["arn"]
            print(f"✅ Memory ready — {memory_arn}")

            control.update_harness(
                harnessId=harness_id,
                memory={"optionalValue": {"agentCoreMemoryConfiguration": {"arn": memory_arn}}},
            )
            print("Waiting for harness to update with memory...")
            # UpdateHarness moves the harness back through UPDATING; the shared
            # poller waits for the real terminal status instead of a fixed number
            # of polls, so the turns below cannot run against a mid-update harness.
            poll_harness_status(control, harness_id)
            print("✅ Harness updated with memory")

            # Multi-turn test
            memory_session_id = str(uuid.uuid4()).upper()
            print(f"\nMemory test session: {memory_session_id}")
            print("\n--- Turn 1 ---")
            stream_response(
                harness_arn,
                memory_session_id,
                "My name is John Doe and I love electronic music with balance — "
                "deep house, nu-disco, anything with a nice groove. Remember that.",
            )
            print("\n--- Turn 2 (agent should remember name and preference) ---")
            stream_response(
                harness_arn,
                memory_session_id,
                "What's my name and what kind of music do I like? Recommend a place in Amsterdam where I can enjoy it.",
            )

    # ── Part 5: Browser Tool — Live Weather Data ──────────────────────────────
    print("\n=== Part 5: Browser Tool — Live Amsterdam Weather ===")
    browser_session = str(uuid.uuid4()).upper()
    print(f"Browser session: {browser_session}\n")

    stream_response(
        harness_arn,
        browser_session,
        "Check the weather forecast for Amsterdam this week. "
        "Browse a real weather website to get current, accurate data. "
        "Save the forecast as a clean HTML file at /tmp/weather.html with a modern dark theme, "
        "showing each day as a card with temperature, conditions, and an emoji for the weather.",
        tools=[{"type": "agentcore_browser", "name": "browser"}],
    )

    weather_html = fetch_file(harness_arn, browser_session, "/tmp/weather.html")  # nosec B108
    if weather_html:
        output_path = "/tmp/amsterdam_weather.html"  # nosec B108
        with open(output_path, "w") as f:
            f.write(weather_html)
        print(f"✅ Weather HTML saved to {output_path}")
    else:
        print("⚠️  No weather.html generated")

    # ── Part 6: Exa Search + Code Interpreter ────────────────────────────────
    print("\n=== Part 6: Exa Search + Code Interpreter — Tourism Analysis ===")
    research_session = str(uuid.uuid4()).upper()
    print(f"Research session: {research_session}\n")

    print("--- Step 1: Search tourism data with Exa ---")
    stream_response(
        harness_arn,
        research_session,
        "Search for Amsterdam tourism statistics — visitor numbers by year, "
        "top 5 most visited attractions, and monthly visitor trends. "
        "Collect the data and save it as a structured JSON file at /tmp/tourism_data.json.",
        tools=[
            {
                "type": "remote_mcp",
                "name": "exa",
                "config": {"remoteMcp": {"url": "https://mcp.exa.ai/mcp"}},
            }
        ],
    )

    print("\n--- Step 2: Generate chart with Code Interpreter ---")
    stream_response(
        harness_arn,
        research_session,
        "Read the tourism data from /tmp/tourism_data.json and create a visualization "
        "using matplotlib. Make a bar chart or line chart showing the most interesting trends. "
        "Use a dark theme with vibrant colors. "
        "Save the chart as /tmp/amsterdam_tourism.png and a summary as /tmp/tourism_report.md.",
        tools=[{"type": "agentcore_code_interpreter", "name": "code_interpreter"}],
    )

    report = fetch_file(
        harness_arn,
        research_session,
        "/tmp/tourism_report.md 2>/dev/null || echo 'No report'",  # nosec B108
    )
    print("\nTourism report:")
    print(report)

    # Fetch and save the chart
    b64_data = b""
    resp = client.invoke_agent_runtime_command(
        agentRuntimeArn=harness_arn,
        runtimeSessionId=research_session,
        body={"command": "base64 /tmp/amsterdam_tourism.png 2>/dev/null || echo 'NO_CHART'"},
    )
    for event in resp["stream"]:
        if "chunk" in event:
            chunk = event["chunk"]
            if "contentDelta" in chunk and "stdout" in chunk["contentDelta"]:
                b64_data += chunk["contentDelta"]["stdout"].encode()

    chart_str = b64_data.decode().strip()
    if chart_str and chart_str != "NO_CHART":
        chart_bytes = base64.b64decode(chart_str.replace("\n", ""))
        chart_path = "/tmp/amsterdam_tourism.png"  # nosec B108
        with open(chart_path, "wb") as f:
            f.write(chart_bytes)
        print(f"✅ Chart saved to {chart_path} ({len(chart_bytes):,} bytes)")
    else:
        print("No chart found — agent may have saved in a different format")
        run_command(
            harness_arn,
            research_session,
            "ls -la /tmp/*.png /tmp/*.html /tmp/*.md 2>/dev/null",
        )

finally:
    if not args.skip_cleanup:
        print("\n=== Cleanup ===")
        # Delete the harness first: it holds a reference to the memory, and the
        # role is what every other sample in this folder shares, so it goes last.
        # Each delete stands alone — one failure must not skip the rest, or the
        # leftovers silently break the next run.
        if harness_id:
            try:
                control.delete_harness(harnessId=harness_id)
                print(f"Deleted harness: {harness_id}")
            except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
                print(f"Warning: could not delete harness {harness_id}: {e}")
        if memory_id:
            try:
                control.delete_memory(memoryId=memory_id)
                print(f"Deleted memory: {memory_id}")
            except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
                print(f"Warning: could not delete memory {memory_id}: {e}")
        delete_harness_role()
        print("Done.")
    else:
        print("\n=== Skipping cleanup (--skip-cleanup) ===")
        print(f"Harness ID: {harness_id}")
