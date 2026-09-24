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
import binascii
import hashlib
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

# Local directory for the screenshots pulled off the VM. Deliberately not the
# same paths the VM uses (/tmp/screenshot_N.png): keeping them apart makes it
# obvious which side of the transfer a file came from, and a stale local file
# can no longer be mistaken for a freshly pulled one.
LOCAL_SCREENSHOT_DIR = Path("/tmp/webapp_screenshots")  # nosec B108

# A screenshot is binary, so a short read cannot be spotted by eye — it has to be
# checked. Transfers are retried this many times before the file is reported as
# failed rather than saved.
FETCH_ATTEMPTS = 3

# Error members of the InvokeAgentRuntimeCommand event stream (per the boto3
# model). They arrive as ordinary events instead of raising, so a loop that only
# reads chunks treats a command that never ran as one that produced no output.
COMMAND_ERROR_KEYS = (
    "accessDeniedException",
    "internalServerException",
    "resourceNotFoundException",
    "serviceQuotaExceededException",
    "throttlingException",
    "validationException",
    "runtimeClientError",
)

# The InvokeHarness stream carries a smaller set (it has no accessDenied /
# resourceNotFound / serviceQuotaExceeded / throttling members of its own — those
# surface as a ClientError on the call).
STREAM_ERROR_KEYS = (
    "internalServerException",
    "validationException",
    "runtimeClientError",
)

# ── Setup ─────────────────────────────────────────────────────────────────────
control = get_agentcore_control_client()
client = get_agentcore_client()

account_id = boto3.client("sts").get_caller_identity()["Account"]
print(f"Account: {account_id}")


def run_command(harness_arn, session_id, cmd, echo=True):
    """Run a command on the agent's remote microVM.

    Returns (stdout, exit_code). stdout is streamed to the console as it arrives
    unless `echo` is False, so callers must not print the return value again.

    The stream reports failure two different ways and both have to be read: a
    command that ran and failed reports it in `contentStop.exitCode`, while a
    command that never ran at all arrives as one of the modeled error events.
    Reading neither made a failed step look exactly like a successful one.
    """
    print(f"$ {cmd}")
    output = ""
    exit_code = None
    resp = client.invoke_agent_runtime_command(
        agentRuntimeArn=harness_arn,
        runtimeSessionId=session_id,
        body={"command": cmd},
    )
    for event in resp["stream"]:
        if "chunk" in event:
            chunk = event["chunk"]
            if "contentDelta" in chunk:
                delta = chunk["contentDelta"]
                if "stdout" in delta:
                    output += delta["stdout"]
                    if echo:
                        print(delta["stdout"], end="", flush=True)
                if "stderr" in delta:
                    print(delta["stderr"], end="", flush=True)
            elif "contentStop" in chunk:
                exit_code = chunk["contentStop"].get("exitCode")
        else:
            err = next((event[k] for k in COMMAND_ERROR_KEYS if k in event), None)
            if err is not None:
                raise RuntimeError(f"Command stream error ({cmd}): {err}")
    if echo and output and not output.endswith("\n"):
        print()
    return output, exit_code


def run_checked(harness_arn, session_id, cmd, echo=True):
    """Run a command and raise unless it reported success. Returns stdout.

    A missing exit code is treated as a failure too: the stream ended without
    ever saying how the command finished, so nothing confirms it did.
    """
    output, exit_code = run_command(harness_arn, session_id, cmd, echo=echo)
    if exit_code is None:
        raise RuntimeError(f"Command returned no exit status: {cmd}")
    if exit_code != 0:
        raise RuntimeError(f"Command exited {exit_code}: {cmd}")
    return output


def stream_agent_turn(harness_arn, session_id, prompt, show_tools=False):
    """Send one prompt to the agent and stream the reply. Returns the text.

    Raises on a stream error event so a turn that failed part-way cannot be
    mistaken for one that finished — the steps that follow depend on the files
    this turn is supposed to leave on the VM.
    """
    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        model={"bedrockModelConfig": {"modelId": MODEL_ID}},
        messages=[{"role": "user", "content": [{"text": prompt}]}],
    )
    full_text = ""
    for event in response["stream"]:
        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if show_tools and "toolUse" in start:
                print(f"\n[Tool: {start['toolUse'].get('name', '?')}]", flush=True)
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                print(delta["text"], end="", flush=True)
                full_text += delta["text"]
        elif "messageStop" in event:
            print()
        else:
            err = next((event[k] for k in STREAM_ERROR_KEYS if k in event), None)
            if err is not None:
                raise RuntimeError(f"Harness stream error: {err}")
    return full_text


def fetch_binary(harness_arn, session_id, remote_path):
    """Copy one binary file off the VM, verified. Returns bytes, or None.

    The transfer is checked against the file's real size and MD5 on the VM
    because the command stream has been observed delivering a *short* payload
    for a large file — intermittently, with no error event and a zero exit code.

    Only the size/MD5 comparison catches that. Base64 of a complete file is
    always a multiple of four characters, so a length that is not means the
    payload is incomplete — but the converse does not hold, and a truncated
    payload stayed four-aligned in the run that exposed this. The result was a
    PNG with a valid header, no end marker, and a grey band where the missing
    rows should be: a corrupt screenshot that reported itself as a success.
    """
    meta, exit_code = run_command(
        harness_arn,
        session_id,
        f"stat -c %s {remote_path} && md5sum {remote_path} | cut -d' ' -f1",
        echo=False,
    )
    if exit_code != 0:
        print(f"⚠️  {remote_path}: not readable on the VM (exit {exit_code})")
        return None
    try:
        size_text, md5_expected = meta.split()
        size_expected = int(size_text)
    except ValueError:
        print(f"⚠️  {remote_path}: could not read size/MD5 from {meta.strip()!r}")
        return None

    for attempt in range(1, FETCH_ATTEMPTS + 1):
        # Wrapped base64 (the default) is what the stream handles reliably; the
        # single-line `base64 -w0` form has been seen to fail the whole stream
        # with an InternalFailure on a payload this size.
        encoded, exit_code = run_command(harness_arn, session_id, f"base64 {remote_path}", echo=False)
        if exit_code != 0:
            print(f"⚠️  {remote_path}: base64 exited {exit_code}")
            return None

        cleaned = "".join(encoded.split())
        if len(cleaned) % 4:
            reason = f"payload truncated mid-quartet ({len(cleaned)} chars)"
        else:
            try:
                data = base64.b64decode(cleaned)
            except binascii.Error as e:
                reason = f"undecodable payload ({e})"
            else:
                digest = hashlib.md5(data, usedforsecurity=False).hexdigest()
                if len(data) == size_expected and digest == md5_expected:
                    return data
                reason = f"got {len(data):,} of {size_expected:,} bytes"

        if attempt < FETCH_ATTEMPTS:
            print(f"   retrying {remote_path} — {reason} (attempt {attempt}/{FETCH_ATTEMPTS})")

    print(f"⚠️  {remote_path}: transfer failed after {FETCH_ATTEMPTS} attempts — {reason}")
    return None


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

    print("Installing git, curl, and Chromium (takes ~1 minute)...")
    # apt-get is noisy (dpkg prints ~1000 lines here), so its output goes to a log
    # on the VM and only the tail is shown, and *only on failure*. The point is to
    # stop checking the exit code — the original sent everything to /dev/null and
    # never looked, so a failed install slid straight into writing Puppeteer tests
    # against a Chromium that was never there.
    _, exit_code = run_command(
        harness_arn,
        session_id,
        "apt-get update -qq && apt-get install -y -qq git curl chromium > /tmp/apt.log 2>&1",
        echo=False,
    )
    if exit_code != 0:
        log_tail, _ = run_command(harness_arn, session_id, "tail -20 /tmp/apt.log")
        raise RuntimeError(f"Package install failed (exit {exit_code}). /tmp/apt.log:\n{log_tail}")
    chromium_path = run_checked(
        harness_arn,
        session_id,
        "command -v chromium || command -v chromium-browser",
        echo=False,
    ).strip()
    print(f"✅ Chromium at {chromium_path}")

    # Ask the agent to generate a TodoMVC app
    print("\nAsking agent to create TodoMVC app...")
    stream_agent_turn(
        harness_arn,
        session_id,
        "Create a single-file TodoMVC app at /tmp/todomvc/index.html. "
        "It should be a complete, self-contained HTML file with inline CSS and JS. "
        "Features: add todos, toggle complete, filter (All/Active/Completed), delete. "
        "Use a clean modern design. No external dependencies.",
    )

    # Fail here rather than serving an empty directory: `npx serve` is perfectly
    # happy to serve a directory with no index.html, so without this check the
    # visual tests ran against a 404 page and still produced screenshots.
    run_checked(harness_arn, session_id, "test -s /tmp/todomvc/index.html && wc -c < /tmp/todomvc/index.html")

    # Start web server
    print("\nStarting web server on port 3000...")
    run_command(
        harness_arn,
        session_id,
        "cd /tmp/todomvc && nohup npx -y serve -l 3000 > /tmp/server.log 2>&1 &",
    )

    # `npx -y serve` downloads the package on first use, so the port is not open
    # the moment the command returns. Poll instead of sleeping a fixed 5s, and
    # stop the run if it never answers — every later step needs this server.
    print("Waiting for the server to answer on port 3000...")
    for _ in range(30):
        status_code, _ = run_command(
            harness_arn,
            session_id,
            "curl -s -o /dev/null -w '%{http_code}' http://localhost:3000",
            echo=False,
        )
        if status_code.strip() == "200":
            break
        time.sleep(2)
    else:
        log_tail, _ = run_command(harness_arn, session_id, "tail -20 /tmp/server.log")
        raise RuntimeError(f"Web server never returned 200 on port 3000. /tmp/server.log:\n{log_tail}")
    print("✅ Server responding with 200")

    # Install Puppeteer. The output is trimmed here rather than with `| tail -3`
    # on the VM, because a pipeline reports the *last* command's status — piping
    # npm into tail replaced npm's exit code with tail's, which is always 0.
    print("\nInstalling puppeteer-core (this takes ~1 minute)...")
    npm_out = run_checked(harness_arn, session_id, "cd /tmp && npm install puppeteer-core 2>&1", echo=False)
    print("\n".join(npm_out.strip().splitlines()[-3:]))
    print("✅ Environment ready")

    # ── Part 3: Agent Writes and Runs Puppeteer Tests ─────────────────────────
    print("\n=== Part 3: Agent Writes Puppeteer Tests ===")
    print("Asking agent to write and run visual tests...\n")

    stream_agent_turn(
        harness_arn,
        session_id,
        f"""There is a TodoMVC web app running at http://localhost:3000 and puppeteer-core is installed at /tmp/node_modules/puppeteer-core. Chromium is at {chromium_path}.

Write a Puppeteer test script at /tmp/test.mjs and run it. The script should:

1. Launch chromium (headless, no-sandbox) and open http://localhost:3000
2. Take screenshot → /tmp/screenshot_1.png (empty app)
3. Add three todos: 'Book flights to Amsterdam', 'Reserve hotel', 'Plan museum visits'
4. Take screenshot → /tmp/screenshot_2.png (three todos)
5. Click the checkbox on 'Book flights to Amsterdam' to mark it complete
6. Take screenshot → /tmp/screenshot_3.png (one completed)
7. Close the browser

Use import from '/tmp/node_modules/puppeteer-core/lib/esm/puppeteer/puppeteer-core.js' or require('/tmp/node_modules/puppeteer-core').
Note: `page.waitForTimeout` no longer exists in current Puppeteer — use `await new Promise(r => setTimeout(r, ms))` instead.
After writing the script, run it with: node /tmp/test.mjs
Then list the screenshots: ls -la /tmp/screenshot_*.png""",
        show_tools=True,
    )

    # ── Part 4: Pull Screenshots ──────────────────────────────────────────────
    print("\n=== Part 4: Pull Screenshots ===")
    # Ask the VM which screenshots exist instead of probing screenshot_1..9 and
    # stopping at the first gap: that loop retrieved nothing at all if the agent
    # happened to name the first file differently, and it cost six extra
    # round-trips for files that were never requested.
    listing, _ = run_command(harness_arn, session_id, "ls -1 /tmp/screenshot_*.png 2>/dev/null")
    remote_paths = [line.strip() for line in listing.splitlines() if line.strip().endswith(".png")]
    if not remote_paths:
        print("⚠️  The agent left no screenshots on the VM")

    LOCAL_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    screenshots_saved = []
    for remote_path in remote_paths:
        img_bytes = fetch_binary(harness_arn, session_id, remote_path)
        if img_bytes is None:
            continue
        local_path = LOCAL_SCREENSHOT_DIR / Path(remote_path).name
        local_path.write_bytes(img_bytes)
        screenshots_saved.append(local_path)
        print(f"✅ {local_path.name}: {len(img_bytes):,} bytes (verified) → {local_path}")

    print(f"\nRetrieved {len(screenshots_saved)} of {len(remote_paths)} screenshots")
    if screenshots_saved:
        print("Open them with:")
        for path in screenshots_saved:
            print(f"  open {path}")
    if len(screenshots_saved) != len(remote_paths):
        # Say so instead of printing a success line for a partial result: a
        # missing screenshot is the whole output of a visual-QA run.
        raise RuntimeError(f"Only {len(screenshots_saved)} of {len(remote_paths)} screenshots transferred intact")

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
