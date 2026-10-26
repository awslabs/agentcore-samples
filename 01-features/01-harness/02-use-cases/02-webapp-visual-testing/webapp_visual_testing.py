"""
Automated Visual QA with AgentCore Harness.

Demonstrates using the Harness microVM as a complete test environment:
  Part 1: Create Harness with a Node.js container (needed for Puppeteer)
  Part 2: Install system dependencies and clone/build a TodoMVC web app
  Part 3: Ask the agent to write Puppeteer test scripts in natural language
  Part 4: Pull screenshots from the agent's VM and save them locally

The Harness microVM is a full Linux environment with its own filesystem and
network stack. The agent can install tools, start servers, and run headless
browsers — all in isolation. This makes it ideal for automated visual QA:

  - CI/CD pipelines: Spin up app, run visual tests, flag regressions before review
  - Cross-version comparison: Build two versions, screenshot both, diff them
  - Exploratory QA: Give the agent a URL, let it navigate and report issues
  - Onboarding docs: Generate an annotated screenshot walkthrough automatically

Key insight: Puppeteer runs inside the same VM as the web server, so
localhost just works — no network isolation issues.

Usage:
    python webapp_visual_testing.py

    # Keep resources for inspection
    python webapp_visual_testing.py --skip-cleanup

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
parser = argparse.ArgumentParser(description="Automated Visual QA with Harness")
parser.add_argument("--skip-cleanup", action="store_true", help="Keep resources after demo")
args = parser.parse_args()

# ── Configuration ─────────────────────────────────────────────────────────────
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
NODE_CONTAINER = "public.ecr.aws/docker/library/node:20-slim"

# ── Setup ─────────────────────────────────────────────────────────────────────
control = get_agentcore_control_client()
client = get_agentcore_client()

account_id = boto3.client("sts").get_caller_identity()["Account"]
print(f"Account: {account_id}")


def run_command(harness_arn, session_id, cmd):
    """Run a command on the agent's remote microVM."""
    print(f"$ {cmd}")
    output = ""
    resp = client.invoke_agent_runtime_command(
        agentRuntimeArn=harness_arn,
        runtimeSessionId=session_id,
        body={"command": cmd},
    )
    for event in resp["stream"]:
        if "chunk" in event and "contentDelta" in event["chunk"]:
            delta = event["chunk"]["contentDelta"]
            if "stdout" in delta:
                print(delta["stdout"], end="", flush=True)
                output += delta["stdout"]
            if "stderr" in delta:
                print(delta["stderr"], end="", flush=True)
    return output


harness_id = None

try:
    # ── Part 1: Create Harness with Node.js Container ─────────────────────────
    print("\n=== Part 1: Create Harness with Node.js Container ===")
    role_arn = create_harness_role()
    print(f"Role ARN: {role_arn}")
    time.sleep(10)

    HARNESS_NAME = f"WebAppTester_{uuid.uuid4().hex[:8]}"
    resp = control.create_harness(harnessName=HARNESS_NAME, executionRoleArn=role_arn)
    harness = resp["harness"]
    harness_id = harness["harnessId"]
    harness_arn = harness["arn"]
    print(f"Harness ID:  {harness_id}")
    print(f"Harness ARN: {harness_arn}")

    # Wait for READY before updating (update_harness rejects while CREATING).
    # This has to wait for the real status rather than a fixed number of polls:
    # the old ceiling could expire while the harness was still CREATING, and the
    # update below then failed with a ConflictException.
    print("Waiting for harness to become READY...")
    poll_harness_status(control, harness_id)

    print(f"Attaching Node.js container ({NODE_CONTAINER})...")
    control.update_harness(
        harnessId=harness_id,
        environmentArtifact={"optionalValue": {"containerConfiguration": {"containerUri": NODE_CONTAINER}}},
    )

    poll_harness_status(control, harness_id)
    print("✅ Harness ready with Node.js container")

    # ── Part 2: Prepare the Environment ──────────────────────────────────────
    print("\n=== Part 2: Prepare Environment ===")
    session_id = str(uuid.uuid4()).upper()
    print(f"Session ID: {session_id}\n")

    print("Installing git, curl, and Chromium...")
    out = run_command(
        harness_arn,
        session_id,
        "apt-get update -qq && apt-get install -y -qq git curl chromium > /dev/null 2>&1 && echo 'done'",
    )
    print(out.strip())

    # Ask the agent to generate a TodoMVC app
    print("\nAsking agent to create TodoMVC app...")
    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        model={"bedrockModelConfig": {"modelId": MODEL_ID}},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": "Create a single-file TodoMVC app at /tmp/todomvc/index.html. "
                        "It should be a complete, self-contained HTML file with inline CSS and JS. "
                        "Features: add todos, toggle complete, filter (All/Active/Completed), delete. "
                        "Use a clean modern design. No external dependencies."
                    }
                ],
            }
        ],
    )
    for event in response["stream"]:
        if "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                print(delta["text"], end="", flush=True)
        elif "messageStop" in event:
            print()

    out = run_command(
        harness_arn,
        session_id,
        "ls -la /tmp/todomvc/index.html 2>/dev/null || echo 'NOT CREATED'",
    )
    print(out.strip())

    # Start web server
    print("\nStarting web server on port 3000...")
    run_command(
        harness_arn,
        session_id,
        "mkdir -p /tmp/todomvc && cd /tmp/todomvc && nohup npx -y serve -l 3000 > /tmp/server.log 2>&1 &",
    )
    time.sleep(5)
    out = run_command(
        harness_arn,
        session_id,
        "curl -s -o /dev/null -w '%{http_code}' http://localhost:3000 || echo 'FAIL'",
    )
    print(f"Server status code: {out.strip()}")

    # Install Puppeteer
    print("\nInstalling puppeteer-core (this takes ~1 minute)...")
    out = run_command(harness_arn, session_id, "cd /tmp && npm install puppeteer-core 2>&1 | tail -3")
    print(out)
    print("✅ Environment ready")

    # ── Part 3: Agent Writes and Runs Puppeteer Tests ─────────────────────────
    print("\n=== Part 3: Agent Writes Puppeteer Tests ===")
    print("Asking agent to write and run visual tests...\n")

    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        model={"bedrockModelConfig": {"modelId": MODEL_ID}},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": """There is a TodoMVC web app running at http://localhost:3000 and puppeteer-core is installed at /tmp/node_modules/puppeteer-core. Chromium is at /usr/bin/chromium.

Write a Puppeteer test script at /tmp/test.mjs and run it. The script should:

1. Launch chromium (headless, no-sandbox) and open http://localhost:3000
2. Take screenshot → /tmp/screenshot_1.png (empty app)
3. Add three todos: 'Book flights to Amsterdam', 'Reserve hotel', 'Plan museum visits'
4. Take screenshot → /tmp/screenshot_2.png (three todos)
5. Click the checkbox on 'Book flights to Amsterdam' to mark it complete
6. Take screenshot → /tmp/screenshot_3.png (one completed)
7. Close the browser

Use import from '/tmp/node_modules/puppeteer-core/lib/esm/puppeteer/puppeteer-core.js' or require('/tmp/node_modules/puppeteer-core').
After writing the script, run it with: node /tmp/test.mjs
Then list the screenshots: ls -la /tmp/screenshot_*.png"""
                    }
                ],
            }
        ],
    )

    for event in response["stream"]:
        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                print(f"\n[Tool: {start['toolUse'].get('name', '?')}]", flush=True)
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                print(delta["text"], end="", flush=True)
        elif "messageStop" in event:
            print()

    # ── Part 4: Pull Screenshots ──────────────────────────────────────────────
    print("\n=== Part 4: Pull Screenshots ===")
    out = run_command(
        harness_arn,
        session_id,
        "ls -la /tmp/screenshot_*.png 2>/dev/null || echo 'No screenshots found'",
    )
    print(out)

    screenshots_saved = []
    for i in range(1, 10):
        b64 = ""
        resp = client.invoke_agent_runtime_command(
            agentRuntimeArn=harness_arn,
            runtimeSessionId=session_id,
            body={"command": f"base64 /tmp/screenshot_{i}.png 2>/dev/null"},
        )
        for event in resp["stream"]:
            if "chunk" in event and "contentDelta" in event["chunk"]:
                delta = event["chunk"]["contentDelta"]
                if "stdout" in delta:
                    b64 += delta["stdout"]

        if not b64.strip():
            break

        b64_clean = b64.strip().replace("\n", "").replace("\r", "").replace(" ", "")
        # Pad base64 to multiple of 4
        remainder = len(b64_clean) % 4
        if remainder:
            b64_clean = b64_clean[:-remainder]

        try:
            img_bytes = base64.b64decode(b64_clean)
            local_path = f"/tmp/screenshot_{i}.png"  # nosec B108
            with open(local_path, "wb") as f:
                f.write(img_bytes)
            screenshots_saved.append(local_path)
            print(f"✅ Screenshot {i}: {len(img_bytes):,} bytes → {local_path}")
        except Exception as e:  # noqa: BLE001 - a bad screenshot must not abort the rest
            print(f"⚠️  Screenshot {i}: decode failed — {e}")

    print(f"\nRetrieved {len(screenshots_saved)} screenshots")
    if screenshots_saved:
        print("Open them with:")
        for path in screenshots_saved:
            print(f"  open {path}")

finally:
    if not args.skip_cleanup:
        print("\n=== Cleanup ===")
        if harness_id:
            try:
                control.delete_harness(harnessId=harness_id)
                print(f"Deleted harness: {harness_id}")
            except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
                print(f"Warning: could not delete harness {harness_id}: {e}")
        delete_harness_role()
        print("Done.")
    else:
        print(f"\n=== Skipping cleanup. Harness: {harness_id} ===")
