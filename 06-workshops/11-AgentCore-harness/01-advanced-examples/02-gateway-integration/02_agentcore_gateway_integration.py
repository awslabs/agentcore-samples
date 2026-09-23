#!/usr/bin/env python3
"""
AgentCore Gateway Integration Example

This script demonstrates the full lifecycle of an AgentCore Gateway:

  1. Create an IAM execution role (reuses the shared helper)
  2. Create a Gateway with IAM auth and MCP protocol
  3. Add an MCP target (remote MCP server endpoint)
  4. Create a routing rule that directs traffic to the target
  5. Create a Harness wired to the Gateway
  6. Invoke the agent — it discovers and calls tools via the Gateway
  7. Clean up all resources

AgentCore Gateway is a managed proxy that sits between your agent and
external tool servers (MCP, HTTP). It provides centralized auth, routing
rules, and observability for all tool traffic.

Usage:
    # Basic — uses the default Exa MCP search endpoint
    python 02_agentcore_gateway_integration.py

    # Custom MCP endpoint
    python 02_agentcore_gateway_integration.py \\
        --mcp-endpoint https://your-mcp-server.example.com/mcp \\
        --target-name my-tools

    # Keep resources after the demo
    python 02_agentcore_gateway_integration.py --skip-cleanup

    # Use an existing IAM role
    python 02_agentcore_gateway_integration.py --role-arn arn:aws:iam::123456789012:role/MyRole

    # See all options
    python 02_agentcore_gateway_integration.py --help
"""

import argparse
import json
import os
import sys
import time
import uuid

import boto3
import botocore.exceptions

# Add project root so helpers are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from helper.iam import create_harness_role
from helper.client import get_agentcore_client, get_agentcore_control_client

REGION = os.getenv("AWS_DEFAULT_REGION")


# ---------------------------------------------------------------------------
# Client factories
# ---------------------------------------------------------------------------
def _make_session():
    """Create a boto3 session with local service models loaded."""
    session = boto3.Session(region_name=REGION)
    return session


def get_gateway_control_client():
    """Control-plane client for Gateway APIs (GA endpoint — no override)."""
    return _make_session().client("bedrock-agentcore-control")


def get_harness_control_client():
    """Control-plane client for Harness APIs (beta endpoint)."""
    return get_agentcore_control_client()


def get_data_plane_client():
    """Data-plane client for invoke_harness (beta endpoint)."""
    return get_agentcore_client()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_MCP_ENDPOINT = "https://mcp.exa.ai/mcp"
DEFAULT_TARGET_NAME = "exa-search"
DEFAULT_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_PROMPT = (
    "Search the web for the top 5 things to do in Tokyo in spring 2025. "
    "For each activity, include a one-sentence description and the best month to visit. "
    "Format the results as a numbered list."
)

GATEWAY_POLL_INTERVAL = 5
GATEWAY_POLL_TIMEOUT = 120
HARNESS_POLL_INTERVAL = 5
HARNESS_POLL_TIMEOUT = 120

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(
    description="AgentCore Gateway Integration — create a Gateway, add targets, and invoke via Harness.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
parser.add_argument(
    "--mcp-endpoint",
    default=DEFAULT_MCP_ENDPOINT,
    metavar="URL",
    help=f"MCP server endpoint URL (default: {DEFAULT_MCP_ENDPOINT})",
)
parser.add_argument(
    "--target-name",
    default=DEFAULT_TARGET_NAME,
    metavar="NAME",
    help=f"Name for the Gateway target (default: {DEFAULT_TARGET_NAME})",
)
parser.add_argument(
    "--model",
    default=DEFAULT_MODEL,
    metavar="MODEL_ID",
    help=f"Bedrock model ID (default: {DEFAULT_MODEL})",
)
parser.add_argument(
    "--message",
    "-m",
    default=DEFAULT_PROMPT,
    help="Prompt to send to the agent",
)
parser.add_argument(
    "--role-arn",
    default=None,
    metavar="ARN",
    help="Use an existing IAM execution role ARN instead of creating one",
)
parser.add_argument(
    "--skip-cleanup",
    action="store_true",
    help="Keep all resources after the demo",
)
parser.add_argument(
    "--raw-events",
    action="store_true",
    help="Print raw JSON streaming events from invoke",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def poll_gateway_status(control, gateway_id, target_status="READY", timeout=GATEWAY_POLL_TIMEOUT):
    """Poll until the Gateway reaches the target status or times out."""
    deadline = time.monotonic() + timeout
    while True:
        resp = control.get_gateway(gatewayIdentifier=gateway_id)
        status = resp["status"]
        print(f"  Gateway status: {status}")
        if status == target_status:
            return resp
        if status == "FAILED":
            reasons = resp.get("statusReasons", [])
            raise RuntimeError(f"Gateway entered FAILED state: {reasons}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Gateway not {target_status} after {timeout}s (current: {status})")
        time.sleep(GATEWAY_POLL_INTERVAL)


def poll_target_status(control, gateway_id, target_id, target_status="READY", timeout=GATEWAY_POLL_TIMEOUT):
    """Poll until a Gateway target reaches the target status or times out."""
    deadline = time.monotonic() + timeout
    while True:
        resp = control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        status = resp["status"]
        print(f"  Target status: {status}")
        if status == target_status:
            return resp
        if status in ("FAILED", "DELETE_FAILED"):
            reasons = resp.get("statusReasons", [])
            raise RuntimeError(f"Target entered {status}: {reasons}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Target not {target_status} after {timeout}s (current: {status})")
        time.sleep(GATEWAY_POLL_INTERVAL)


def poll_harness_status(control, harness_id, target_status="READY", timeout=HARNESS_POLL_TIMEOUT):
    """Poll until a Harness reaches the target status or times out."""
    deadline = time.monotonic() + timeout
    while True:
        resp = control.get_harness(harnessId=harness_id)
        status = resp["harness"]["status"]
        print(f"  Harness status: {status}")
        if status == target_status:
            return resp
        if status in ("FAILED", "DELETE_FAILED"):
            raise RuntimeError(f"Harness entered {status}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Harness not {target_status} after {timeout}s (current: {status})")
        time.sleep(HARNESS_POLL_INTERVAL)


def stream_response(client, harness_arn, session_id, message, model_id, gateway_arn, raw=False):
    """Invoke a Harness with a Gateway tool and stream the response."""
    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": message}]}],
        model={"bedrockModelConfig": {"modelId": model_id}},
        tools=[
            {
                "type": "agentcore_gateway",
                "name": "gateway",
                "config": {"agentCoreGateway": {"gatewayArn": gateway_arn}},
            }
        ],
    )

    full_text = ""
    try:
        for event in response["stream"]:
            if raw:
                print(json.dumps(event, default=str))
                continue

            if "contentBlockStart" in event:
                start = event["contentBlockStart"].get("start", {})
                if "toolUse" in start:
                    print(f"\n  [Tool: {start['toolUse'].get('name', '?')}]", flush=True)
            elif "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                if "text" in delta:
                    print(delta["text"], end="", flush=True)
                    full_text += delta["text"]
            elif "messageStop" in event:
                print()
            elif "internalServerException" in event:
                print(f"\n  Error: {event['internalServerException']}")
    except botocore.exceptions.EventStreamError:
        # The stream may send an empty error event on close; safe to ignore
        # if we already received content.
        if not full_text:
            raise

    return full_text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(args=None):
    if args is None:
        args = parser.parse_args()

    # Gateway APIs use the GA endpoint; Harness APIs use the beta endpoint.
    gw_control = get_gateway_control_client()
    harness_control = get_harness_control_client()
    client = get_data_plane_client()

    gateway_id = None
    target_id = None
    harness_id = None

    try:
        # ── Step 0: IAM role ──────────────────────────────────────────
        print("=" * 60)
        print("Step 0: IAM execution role")
        print("=" * 60)
        if args.role_arn:
            role_arn = args.role_arn
            print(f"  Using provided role: {role_arn}")
        else:
            role_arn = create_harness_role()
            # Allow time for IAM propagation
            print("  Waiting for IAM propagation...")
            time.sleep(10)

        # ── Step 1: Create Gateway ────────────────────────────────────
        print("\n" + "=" * 60)
        print("Step 1: Create Gateway")
        print("=" * 60)
        # Gateway name must match ([0-9a-zA-Z][-]?){1,48} — no underscores
        gateway_name = f"GatewayDemo-{uuid.uuid4().hex[:8]}"
        resp = gw_control.create_gateway(
            name=gateway_name,
            roleArn=role_arn,
            protocolType="MCP",
            authorizerType="NONE",
        )
        gateway_id = resp["gatewayId"]
        gateway_arn = resp["gatewayArn"]
        print(f"  Gateway ID:  {gateway_id}")
        print(f"  Gateway ARN: {gateway_arn}")
        poll_gateway_status(gw_control, gateway_id)

        # ── Step 2: Add MCP target ────────────────────────────────────
        print("\n" + "=" * 60)
        print(f"Step 2: Add MCP target ({args.mcp_endpoint})")
        print("=" * 60)
        resp = gw_control.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=args.target_name,
            targetConfiguration={
                "mcp": {
                    "mcpServer": {
                        "endpoint": args.mcp_endpoint,
                    },
                },
            },
        )
        target_id = resp["targetId"]
        print(f"  Target ID: {target_id}")
        poll_target_status(gw_control, gateway_id, target_id)

        # ── Step 3: Create Harness ────────────────────────────────────
        print("\n" + "=" * 60)
        print("Step 3: Create Harness")
        print("=" * 60)
        # Harness name must match [a-zA-Z][a-zA-Z0-9_]{0,39}
        harness_name = f"GatewayHarness_{uuid.uuid4().hex[:8]}"
        resp = harness_control.create_harness(
            harnessName=harness_name,
            executionRoleArn=role_arn,
        )
        harness_id = resp["harness"]["harnessId"]
        harness_arn = resp["harness"]["arn"]
        print(f"  Harness ID:  {harness_id}")
        print(f"  Harness ARN: {harness_arn}")
        poll_harness_status(harness_control, harness_id)

        # ── Step 4: Invoke agent via Gateway ──────────────────────────
        print("\n" + "=" * 60)
        print("Step 4: Invoke agent (tools served via Gateway)")
        print("=" * 60)
        session_id = str(uuid.uuid4()).upper()
        print(f"  Session ID: {session_id}")
        print(f"  Model:      {args.model}")
        print(f"  Gateway:    {gateway_arn}")
        print(f"  Message:    {args.message[:80]}{'...' if len(args.message) > 80 else ''}\n")

        stream_response(
            client,
            harness_arn,
            session_id,
            args.message,
            args.model,
            gateway_arn,
            raw=args.raw_events,
        )

        print("\n" + "=" * 60)
        print("Done!")
        print("=" * 60)

        time.sleep(20)

    finally:
        if not args.skip_cleanup:
            print("\nCleaning up...")
            _cleanup(gw_control, harness_control, gateway_id, target_id, harness_id)


def _cleanup(gw_control, harness_control, gateway_id, target_id, harness_id):
    """Delete all resources created during the demo."""
    # Delete Harness (via beta endpoint)
    if harness_id:
        try:
            harness_control.delete_harness(harnessId=harness_id)
            print(f"  Deleted harness: {harness_id}")
        except Exception as e:
            print(f"  Warning: failed to delete harness: {e}")

    # Delete Gateway target first — must be removed before the gateway
    if gateway_id and target_id:
        try:
            gw_control.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
            print(f"  Deleted target: {target_id}")
            # Wait for async target deletion to propagate
            time.sleep(10)
        except Exception as e:
            print(f"  Warning: failed to delete target: {e}")

    # Delete Gateway (rules are deleted automatically with the gateway)
    if gateway_id:
        try:
            gw_control.delete_gateway(gatewayIdentifier=gateway_id)
            print(f"  Deleted gateway: {gateway_id}")
        except Exception as e:
            print(f"  Warning: failed to delete gateway: {e}")


if __name__ == "__main__":
    main()
