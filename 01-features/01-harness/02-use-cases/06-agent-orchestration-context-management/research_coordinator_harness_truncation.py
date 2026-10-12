"""
Agent Orchestration and Context Management with AgentCore Harness.

Demonstrates a Research Coordinator that delegates a long research task to the
harness's built-in subagents (researcher, synthesizer, reviewer), and keeps the
conversation inside the model's context window with a summarization
truncation strategy:
  Step 1: Create a Harness with the coordinator system prompt and truncation
  Step 2: Ask a simple question (the coordinator answers directly, no subagents)
  Step 3: Ask for a full research report (researcher -> synthesizer -> reviewer)

Why truncation?
  A coordinator accumulates every subagent brief, every tool result and every
  file it reads. On a long run that history outgrows the context window. With
  `truncation.strategy = "summarization"`, the harness replaces the oldest part
  of the conversation with a summary and keeps the most recent messages as they
  are, so the run keeps going.

The coordinator's instructions live in system_prompt.md, next to this script.

Usage:
    python research_coordinator_harness_truncation.py

Prerequisites:
    - An AWS CLI profile with credentials, set in AWS_PROFILE
    - pip install -r ../../requirements.txt (local only, nothing is deployed to the harness)
    - An IAM execution role for the harness, set in EXECUTION_ROLE_ARN
    - Access to global.anthropic.claude-sonnet-5 in Amazon Bedrock
"""

import time
import uuid
from pathlib import Path

import boto3
from botocore.config import Config

# ── Configuration ─────────────────────────────────────────────────────────────
AWS_PROFILE = "agent"
REGION = "us-west-2"
HARNESS_NAME = "ResearchCoordinator"

# IAM role the harness runs as. It needs access to the harness's managed memory.
EXECUTION_ROLE_ARN = "arn:aws:iam::123456789012:role/harness-execution-role"

# ── Setup ─────────────────────────────────────────────────────────────────────

session = boto3.Session(profile_name=AWS_PROFILE, region_name=REGION)
control = session.client("bedrock-agentcore-control")
runtime = session.client(
    "bedrock-agentcore",
    # A full research run streams for 10+ minutes. Retrying would start the whole run again.
    config=Config(read_timeout=3600, retries={"total_max_attempts": 1}),
)


# ── Helpers ───────────────────────────────────────────────────────────────────
def create_harness():
    """Create the harness and wait until it is READY."""
    harness = control.create_harness(
        harnessName=HARNESS_NAME,
        executionRoleArn=EXECUTION_ROLE_ARN,
        clientToken=str(uuid.uuid4()),
        model={
            "bedrockModelConfig": {
                "modelId": "global.anthropic.claude-sonnet-5",
                "maxTokens": 128000,
                "additionalParams": {
                    "additionalModelRequestFields": {
                        # The model decides how much to reason on each turn.
                        "thinking": {"type": "adaptive", "display": "summarized"},
                        "output_config": {"effort": "high"},
                    }
                },
            }
        },
        # The workflow, the 1000-word limit and the file each subagent writes.
        systemPrompt=[{"text": (Path(__file__).parent / "system_prompt.md").read_text()}],
        # Room for the full workflow. The recorded run used 24 tool calls and took about 15 minutes.
        maxIterations=100,
        timeoutSeconds=3600,
        # When the conversation outgrows the context window, summarize the oldest 30% of the
        # messages and always keep the 30 most recent messages as they are.
        # Other strategies: "sliding_window" (drop the oldest messages) and "none".
        truncation={
            "strategy": "summarization",
            "config": {"summarization": {"summaryRatio": 0.3, "preserveRecentMessages": 30}},
        },
    )["harness"]

    # Creation is asynchronous: poll until READY, and fail fast on CREATE_FAILED.
    while harness["status"] != "READY":
        if "FAILED" in harness["status"]:
            raise RuntimeError(f"Harness creation failed: {harness}")
        print(f"Harness status: {harness['status']}")
        time.sleep(10)
        harness = control.get_harness(harnessId=harness["harnessId"])["harness"]

    print(f"Harness ready: {harness['arn']}")
    return harness["arn"]


def invoke(harness_arn, prompt):
    """Send one prompt in a new session and print the streamed response."""
    print(f"\n> {prompt}\n")
    start = time.monotonic()
    response = runtime.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=str(uuid.uuid4()),  # a new session per prompt
        messages=[{"role": "user", "content": [{"text": prompt}]}],
    )

    # The response is an event stream: text deltas, tool calls, and a final stop reason.
    # Each delegation to a subagent shows up as [tool: subagent].
    for event in response["stream"]:
        if text := event.get("contentBlockDelta", {}).get("delta", {}).get("text"):
            print(text, end="", flush=True)
        elif tool := event.get("contentBlockStart", {}).get("start", {}).get("toolUse"):
            print(f"\n[tool: {tool['name']}]", flush=True)
        elif "messageStop" in event:
            print(f"\n[stop: {event['messageStop']['stopReason']}]")
        # Agent-side failures arrive as events inside an HTTP 200 stream, so raise on them.
        elif (
            error := event.get("runtimeClientError")
            or event.get("validationException")
            or event.get("internalServerException")
        ):
            raise RuntimeError(error["message"])

    print(f"\nFinished in {time.monotonic() - start:.0f}s")


# ── Step 1: Create Harness ────────────────────────────────────────────────────
harness_arn = create_harness()

# ── Step 2: Simple question ───────────────────────────────────────────────────
# A simple question: the coordinator should answer directly, without subagents.
invoke(harness_arn, "What is the current trending topic in AI?")

# ── Step 3: Full research report ──────────────────────────────────────────────
# A full research request: the coordinator runs researcher -> synthesizer -> reviewer.
invoke(
    harness_arn,
    "Perform a full research report on how data shapes innovations in the tech industry. "
    "Use specialized subagents to complete this task. In the final output, clearly state which "
    "part each subagent was responsible for, followed by their specific findings.",
)
