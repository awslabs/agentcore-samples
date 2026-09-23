"""
Agent Skills with AgentCore Harness.

Demonstrates how to extend agent capabilities with pre-built skill bundles:
  Part 1: Create a Harness with a Node.js container (required for skills)
  Part 2: Install the xlsx skill via ExecuteCommand
  Part 3: Use the skill to create an Excel travel budget spreadsheet
  Part 4: Advanced — quarterly sales report with multiple sheets
  Part 5: Download generated files from the agent's VM

Agent Skills are pre-built capability bundles that provide:
  - Specialized instructions for complex tasks
  - Code templates (xlsx, pdf, docx file formats)
  - Tool configurations and domain knowledge

Usage:
    python agent_skills.py

Prerequisites:
    - AWS CLI configured with credentials
    - pip install -r ../../requirements.txt
    - AWS_DEFAULT_REGION environment variable set
"""

import base64
import os
import sys
import time
import uuid
import zipfile
from pathlib import Path

import boto3
from botocore.config import Config

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.client import get_agentcore_client, get_agentcore_control_client
from utils.harness import poll_harness_status
from utils.iam import create_harness_role, delete_harness_role

# ── Configuration ─────────────────────────────────────────────────────────────
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
NODE_CONTAINER = "public.ecr.aws/docker/library/node:slim"

# ── Setup ─────────────────────────────────────────────────────────────────────
control = get_agentcore_control_client()
# Use a long read_timeout: skills download + xlsx generation can take > 2 min
client = get_agentcore_client(config=Config(read_timeout=360))

account_id = boto3.client("sts").get_caller_identity()["Account"]
print(f"Account: {account_id}")


# ── Helper: run a shell command on the agent's VM ─────────────────────────────
# ExecuteCommand reports three things, and every one of them matters here:
# stdout, stderr, and contentStop.exitCode. Reading only stdout — as this sample
# used to — throws away both halves of the answer when something fails: stderr
# says *why*, and the exit code says whether to trust stdout at all.
def run_command(session_id, command):
    """Run `command` on the VM and return (stdout, stderr, exit_code)."""
    stdout = stderr = ""
    exit_code = None
    resp = client.invoke_agent_runtime_command(
        agentRuntimeArn=harness_arn,
        runtimeSessionId=session_id,
        body={"command": command},
    )
    for event in resp["stream"]:
        if "chunk" in event:
            chunk = event["chunk"]
            if "contentDelta" in chunk:
                delta = chunk["contentDelta"]
                stdout += delta.get("stdout", "")
                stderr += delta.get("stderr", "")
            elif "contentStop" in chunk:
                exit_code = chunk["contentStop"].get("exitCode")
    return stdout, stderr, exit_code


def download_xlsx(session_id, path, description):
    """Copy an .xlsx off the VM, refusing to claim success for a file that is not one.

    `base64 <path>` used to run with `2>/dev/null` and only stdout was read, so a
    missing file and an unreadable one produced the same empty string, and any
    non-empty stdout was treated as a valid spreadsheet. Keep stderr and the exit
    code, then check that what arrived is actually a zip container — .xlsx is a
    zip, so a corrupt or truncated transfer is detectable before anything claims
    the file is a workbook.
    """
    print(f"Downloading {os.path.basename(path)} from agent VM...")
    b64_data, err, exit_code = run_command(session_id, f"base64 {path}")

    if exit_code:
        print(f"⚠️  {description} not retrieved (exit {exit_code}).")
        if err.strip():
            print(f"   {err.strip()}")
        print("   The agent may have answered inline instead of writing the file.")
        return None

    try:
        # binascii.Error subclasses ValueError, so this covers both a malformed
        # payload and bad padding.
        blob = base64.b64decode(b64_data.strip().replace("\n", ""), validate=True)
    except ValueError as e:
        print(f"⚠️  {description} came back but is not valid base64: {e}")
        return None

    with open(path, "wb") as f:
        f.write(blob)

    if not zipfile.is_zipfile(path):
        # .xlsx is a zip container. Anything else here means the agent wrote
        # something that is not a workbook (an error message, a partial file),
        # and calling that a success is how the old code reported "3 sheets" for
        # a file it had never looked at.
        print(f"⚠️  Saved {path} ({os.path.getsize(path):,} bytes) but it is not a valid .xlsx.")
        print(f"   First bytes: {blob[:80]!r}")
        return None

    with zipfile.ZipFile(path) as z:
        sheets = [n for n in z.namelist() if n.startswith("xl/worksheets/sheet")]
    print(f"✅ Saved to {path} ({os.path.getsize(path):,} bytes)")
    print(f"   Sheets: {len(sheets)}")
    return path


# ── Create IAM Role & Harness ─────────────────────────────────────────────────
# Everything from role creation onwards runs inside the try/finally below, so the
# cleanup fires even when a step in between fails. Without it, a failure between
# create and cleanup leaks a billable harness *and* the execution role — and the
# role name is shared by every sample in this folder, so the leftovers also make
# the next run of any of them behave unpredictably.
print("\n=== Setup: IAM Role & Harness ===")
harness_id = None

try:
    role_arn = create_harness_role()
    print(f"Execution Role ARN: {role_arn}")
    print("Waiting for IAM propagation...")
    time.sleep(10)

    HARNESS_NAME = f"SkillsDemo_{uuid.uuid4().hex[:8]}"
    resp = control.create_harness(harnessName=HARNESS_NAME, executionRoleArn=role_arn)
    harness = resp["harness"]
    harness_id = harness["harnessId"]
    harness_arn = harness["arn"]
    print(f"Harness ID:  {harness_id}")
    print(f"Harness ARN: {harness_arn}")

    # Use the shared poller rather than a local loop. The loop this replaces ran
    # `for i in range(12)` with a 5s sleep — a 60s ceiling with no else clause and
    # no raise, so on timeout it fell through *silently* and the next call ran
    # against a harness that was still CREATING. Creation here routinely takes
    # longer than that (~150s on the public network, more when pulling an image),
    # so the very next statement, update_harness, failed with
    # `ConflictException: Cannot update agent ... while it is CREATING`.
    poll_harness_status(control, harness_id)
    print("✅ Harness is ready")

    # ── Attach Node.js Container (required for skill installation) ────────────────
    print(f"\n=== Part 1: Attach Node.js Container ({NODE_CONTAINER}) ===")
    control.update_harness(
        harnessId=harness_id,
        environmentArtifact={"optionalValue": {"containerConfiguration": {"containerUri": NODE_CONTAINER}}},
    )
    print("Waiting for container update...")
    # Same again: the update has to finish before any command can run on the VM,
    # and pulling the Node image takes longer than the old 60s ceiling allowed.
    poll_harness_status(control, harness_id)
    print("✅ Harness updated with Node.js container")

    # ── Part 2: Install xlsx Skill ────────────────────────────────────────────────
    print("\n=== Part 2: Install xlsx Skill ===")
    session_id = str(uuid.uuid4()).upper()
    print(f"Session ID: {session_id}\n")

    print("Installing git and xlsx skill (this may take a minute)...")
    install_out, install_err, install_exit = run_command(
        session_id,
        "apt-get update && apt-get install git -y && "
        "npx skills add https://github.com/anthropics/skills --skill xlsx --yes",
    )

    # Show last 500 chars (installation is verbose). stdout and stderr are printed
    # separately: the old code concatenated them into one string and showed only
    # the tail, so an early apt-get failure scrolled out of view.
    combined = install_out + install_err
    print(combined[-500:] if len(combined) > 500 else combined)

    # Check the install before claiming it worked. `✅ xlsx skill installed` used
    # to print unconditionally, immediately after a command whose exit code was
    # never read — so a failed install still showed a tick.
    if install_exit:
        raise RuntimeError(
            f"Skill installation failed (exit {install_exit}). See the output above. "
            "Every later part of this demo depends on it."
        )

    # Verify the skill actually landed where the invocations below point.
    # The old code ran this exact check, printed the answer, and then *discarded*
    # it: there was no `if`, so a VM printing 'skill not found' went straight on
    # to invoke the model with skills=[{'path': '.agents/skills/xlsx'}] anyway,
    # turning a clear setup error into a confusing model response later.
    verify_out, _, _ = run_command(
        session_id,
        "ls -la .agents/skills/xlsx/ 2>/dev/null && echo 'skill directory OK' || echo 'skill not found'",
    )
    print(verify_out, end="")
    if "skill directory OK" not in verify_out:
        raise RuntimeError(
            "xlsx skill was not installed at .agents/skills/xlsx — see the install output above. "
            "Every later part of this demo depends on it."
        )
    print("✅ xlsx skill installed")

    # ── Part 3: Create Travel Budget Spreadsheet ──────────────────────────────────
    print("\n=== Part 3: Travel Budget Spreadsheet ===")

    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        skills=[{"path": ".agents/skills/xlsx"}],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": (
                            "Create an Excel spreadsheet with a 5-day Amsterdam trip budget. "
                            "Include columns for: Day, Category, Item, Cost (EUR), Cost (USD). "
                            "Add rows for accommodation, food, transport, museums, and activities for each day. "
                            "Include a total row with SUM formulas for EUR and USD columns. "
                            "Use currency conversion rate: 1 EUR = 1.10 USD. "
                            "Apply nice formatting: bold headers, currency formatting, alternating row colors. "
                            "Save it as /tmp/amsterdam_budget.xlsx"
                        )
                    }
                ],
            }
        ],
        model={"bedrockModelConfig": {"modelId": MODEL_ID}},
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
            print("\n")

    # Download the spreadsheet.
    download_xlsx(session_id, "/tmp/amsterdam_budget.xlsx", "Budget spreadsheet")  # nosec B108

    # ── Part 4: Advanced — Quarterly Sales Report ────────────────────────────────
    print("\n=== Part 4: Quarterly Sales Report (3 sheets) ===")

    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        skills=[{"path": ".agents/skills/xlsx"}],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": (
                            "Create a professional quarterly sales report Excel file with 3 sheets:\n"
                            "\n"
                            "Sheet 1 - Summary:\n"
                            "- Q1 2024 Sales Overview title\n"
                            "- Table with: Region, Target (USD), Actual (USD), Variance (%), Status\n"
                            "- 4 regions: North America, Europe, Asia Pacific, Latin America\n"
                            "- Use realistic numbers (targets 1M-5M, actual should vary ±20%)\n"
                            "- Variance formula: (Actual-Target)/Target * 100\n"
                            "- Status formula: IF variance >= 0, 'On Track', 'Below Target'\n"
                            "- Total row with SUM formulas\n"
                            "\n"
                            "Sheet 2 - Monthly Breakdown:\n"
                            "- Table with: Month, Region, Sales (USD)\n"
                            "- Data for Jan, Feb, Mar for each region\n"
                            "- Total row\n"
                            "\n"
                            "Sheet 3 - Top Products:\n"
                            "- Table with: Rank, Product, Category, Units Sold, Revenue (USD)\n"
                            "- 10 products with realistic data\n"
                            "\n"
                            "Formatting:\n"
                            "- Bold headers with background color\n"
                            "- Currency formatting for USD columns\n"
                            "- Percentage formatting for Variance column\n"
                            "- Conditional formatting: green for positive variance, red for negative\n"
                            "- Freeze top row in all sheets\n"
                            "\n"
                            "Save as /tmp/q1_sales_report.xlsx"
                        )
                    }
                ],
            }
        ],
        model={"bedrockModelConfig": {"modelId": MODEL_ID}},
        timeoutSeconds=300,
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
            print("\n")

    # Download the report. The sheet count printed here is read back out of the
    # file, not asserted: the old code printed a hardcoded
    # "Sheets: 3 (Summary, Monthly Breakdown, Top Products)" without ever opening
    # what it had written, so it said that even for a file that was not a workbook.
    download_xlsx(session_id, "/tmp/q1_sales_report.xlsx", "Sales report")  # nosec B108

finally:
    # ── Cleanup ────────────────────────────────────────────────────────────────────
    # Runs even when a part above raised, so a failure cannot leave a billable
    # harness behind. Each resource is deleted independently: harness_id is None if
    # CreateHarness itself failed, and a delete_harness error must not skip the role
    # deletion, because the role name is shared by every sample in this folder.
    print("\n=== Cleanup ===")
    if harness_id:
        try:
            control.delete_harness(harnessId=harness_id)
            print(f"Deleted harness: {harness_id}")
        except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
            print(f"Warning: could not delete harness {harness_id}: {e}")

    delete_harness_role()
    print("Done.")
