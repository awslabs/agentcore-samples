"""
AgentCore Gateway Integration with Harness.

Demonstrates the full lifecycle of an AgentCore Gateway (the step numbers below
match the ones this script prints):
  Step 0. Create an IAM execution role
  Step 1. Create a Gateway with the MCP protocol and no inbound authorizer
  Step 2. Add an MCP target (remote MCP server endpoint)
  Step 3. Create a Harness (a plain Harness — it holds no Gateway reference)
  Step 4. Invoke the agent, passing the Gateway ARN in `tools`
  Then:   Clean up all resources

AgentCore Gateway is a managed proxy between your agent and external tool servers
(MCP, HTTP). It gives you one place to handle auth, routing and observability for
all tool traffic.

The Harness and the Gateway are not bound to each other: create_harness takes no
Gateway parameter, and the Gateway ARN is supplied per-invoke in the `tools`
argument to invoke_harness. So one Harness can be pointed at different Gateways on
different calls, and deleting either resource does not affect the other.

A Gateway has two independent authorization sides, and this sample deliberately
uses neither, to keep the focus on the plumbing:
  - Inbound (`authorizerType` on create_gateway): who is allowed to call the
    Gateway. Set to NONE here. See 07-oauth for CUSTOM_JWT.
  - Outbound (`credentialProviderConfigurations` on create_gateway_target): how
    the Gateway authenticates to the tool server behind it. Not set here, because
    the default Exa endpoint serves anonymous callers on a free tier.

Usage:
    # Basic — uses the default Exa MCP search endpoint
    python gateway_integration.py

    # Custom MCP endpoint
    python gateway_integration.py --mcp-endpoint https://your-server.example.com/mcp

    # Keep resources after the demo
    python gateway_integration.py --skip-cleanup

    # Use an existing IAM role
    python gateway_integration.py --role-arn arn:aws:iam::123456789012:role/MyRole

Prerequisites:
    - AWS CLI configured with credentials
    - pip install -r ../../requirements.txt
    - AWS_DEFAULT_REGION environment variable set
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import boto3
import botocore.exceptions

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.client import REGION, get_agentcore_client, get_agentcore_control_client
from utils.harness import HARNESS_POLL_INTERVAL, HARNESS_POLL_TIMEOUT, poll_harness_status
from utils.iam import create_harness_role, delete_harness_role

# ── Constants ─────────────────────────────────────────────────────────────────
DEFAULT_MCP_ENDPOINT = "https://mcp.exa.ai/mcp"
DEFAULT_TARGET_NAME = "exa-search"
DEFAULT_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_PROMPT = (
    "Search the web for the top 5 things to do in Tokyo in spring 2025. "
    "For each activity, include a one-sentence description and the best month to visit. "
    "Format the results as a numbered list."
)

GATEWAY_POLL_INTERVAL = 5
# 300s to match 07-oauth's budget for the same two operations, and the "2-3
# minutes" the README tells the reader to expect. The old 120s contradicted both:
# gateway and target reach READY in ~5s in practice, so a run that is slow enough
# to matter is one this poller would have abandoned right as it got going.
GATEWAY_POLL_TIMEOUT = 300

# Gateways report UPDATE_UNSUCCESSFUL rather than FAILED when an update fails, and
# targets add SYNCHRONIZE_UNSUCCESSFUL. Polling for READY without treating these as
# terminal burns the full timeout and then reports a TimeoutError, hiding the
# statusReasons the service already returned.
GATEWAY_FAILURE_STATUSES = ("FAILED", "UPDATE_UNSUCCESSFUL")
TARGET_FAILURE_STATUSES = (*GATEWAY_FAILURE_STATUSES, "SYNCHRONIZE_UNSUCCESSFUL")

# A target configured with outbound credentials can stop in one of these until the
# authorization is completed out of band. This sample sets no credential provider,
# so it cannot reach them — but waiting out the timeout on a state that will never
# clear by itself is worth calling out rather than silently spinning.
TARGET_PENDING_AUTH_STATUSES = (
    "CREATE_PENDING_AUTH",
    "UPDATE_PENDING_AUTH",
    "SYNCHRONIZE_PENDING_AUTH",
)

# ── CLI ────────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(
    description="AgentCore Gateway Integration — create a Gateway, add targets, invoke via Harness.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
parser.add_argument("--mcp-endpoint", default=DEFAULT_MCP_ENDPOINT, metavar="URL")
parser.add_argument("--target-name", default=DEFAULT_TARGET_NAME, metavar="NAME")
parser.add_argument("--model", default=DEFAULT_MODEL, metavar="MODEL_ID")
parser.add_argument("--message", "-m", default=DEFAULT_PROMPT)
parser.add_argument("--role-arn", default=None, metavar="ARN")
parser.add_argument("--skip-cleanup", action="store_true")
parser.add_argument("--raw-events", action="store_true")

# ── Helpers ───────────────────────────────────────────────────────────────────


def poll_gateway_status(control, gateway_id, target_status="READY", timeout=GATEWAY_POLL_TIMEOUT):
    deadline = time.monotonic() + timeout
    while True:
        resp = control.get_gateway(gatewayIdentifier=gateway_id)
        status = resp["status"]
        print(f"  Gateway status: {status}")
        if status == target_status:
            return resp
        if status in GATEWAY_FAILURE_STATUSES:
            raise RuntimeError(f"Gateway {status}: {resp.get('statusReasons', [])}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Gateway not {target_status} after {timeout}s")
        time.sleep(GATEWAY_POLL_INTERVAL)


def poll_target_status(control, gateway_id, target_id, target_status="READY", timeout=GATEWAY_POLL_TIMEOUT):
    deadline = time.monotonic() + timeout
    while True:
        resp = control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        status = resp["status"]
        print(f"  Target status: {status}")
        if status == target_status:
            return resp
        if status in TARGET_FAILURE_STATUSES:
            raise RuntimeError(f"Target {status}: {resp.get('statusReasons', [])}")
        if status in TARGET_PENDING_AUTH_STATUSES:
            raise RuntimeError(
                f"Target {status}: it is waiting on an outbound authorization that will not "
                f"complete on its own. {resp.get('statusReasons', [])}"
            )
        if time.monotonic() > deadline:
            raise TimeoutError(f"Target not {target_status} after {timeout}s")
        time.sleep(GATEWAY_POLL_INTERVAL)


def stream_response(client, harness_arn, session_id, message, model_id, gateway_arn, raw=False):
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
            # internalServerException is not the only error the stream can carry:
            # validationException and runtimeClientError are modelled events too, and
            # falling through them printed nothing at all, so a rejected invoke looked
            # like an agent that simply had no answer.
            elif "internalServerException" in event:
                print(f"\n  Error: {event['internalServerException']}")
            elif "validationException" in event:
                print(f"\n  Validation error: {event['validationException']}")
            elif "runtimeClientError" in event:
                print(f"\n  Runtime error: {event['runtimeClientError']}")
    except botocore.exceptions.EventStreamError:
        if not full_text:
            raise
    return full_text


def _delete_harness(harness_control, harness_id, timeout=HARNESS_POLL_TIMEOUT):
    """Delete a Harness, waiting out the ConflictException raised while it is still CREATING.

    A harness cannot be deleted until it leaves CREATING, and cleanup runs at
    precisely the moment that is most likely to be true: the `finally` block
    after a step failed mid-provisioning. Without this wait the delete is
    rejected on its only attempt and the harness is left behind — which then
    counts against the account's harness quota with nothing pointing at why.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            harness_control.delete_harness(harnessId=harness_id)
            print(f"  Deleted harness: {harness_id}")
            return
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] != "ConflictException":
                print(f"  Warning: could not delete harness {harness_id}: {e}")
                return
            if time.monotonic() > deadline:
                print(f"  Warning: harness {harness_id} still not deletable after {timeout}s: {e}")
                return
            print(f"  Harness still provisioning, retrying delete in {HARNESS_POLL_INTERVAL}s...")
            time.sleep(HARNESS_POLL_INTERVAL)


def _cleanup(gw_control, harness_control, gateway_id, target_id, harness_id, created_role=False):
    if harness_id:
        _delete_harness(harness_control, harness_id)
    if gateway_id and target_id:
        try:
            gw_control.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
            print(f"  Deleted target: {target_id}")
            time.sleep(10)
        except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
            print(f"  Warning: {e}")
    if gateway_id:
        try:
            gw_control.delete_gateway(gatewayIdentifier=gateway_id)
            print(f"  Deleted gateway: {gateway_id}")
        except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
            print(f"  Warning: {e}")
    # Delete the execution role too, but only the one we created — the role name
    # is shared by every sample in this folder, so deleting a role the caller
    # passed in with --role-arn would destroy something we don't own. Without
    # this the role outlived the script and had to be removed by hand.
    if created_role:
        delete_harness_role()


# ── Main ──────────────────────────────────────────────────────────────────────
def main(args=None):
    if args is None:
        args = parser.parse_args()

    session = boto3.Session(region_name=REGION)
    gw_control = session.client("bedrock-agentcore-control")
    harness_control = get_agentcore_control_client()
    client = get_agentcore_client()

    gateway_id = target_id = harness_id = None
    created_role = False

    try:
        # Step 0: IAM role
        print("=" * 60)
        print("Step 0: IAM execution role")
        print("=" * 60)
        if args.role_arn:
            role_arn = args.role_arn
            print(f"  Using provided role: {role_arn}")
        else:
            role_arn = create_harness_role()
            created_role = True
            print("  Waiting for IAM propagation...")
            time.sleep(10)

        # Step 1: Create Gateway
        print("\n" + "=" * 60)
        print("Step 1: Create Gateway")
        print("=" * 60)
        gateway_name = f"GatewayDemo-{uuid.uuid4().hex[:8]}"
        # authorizerType is the INBOUND control: who may call this Gateway. The
        # full set is NONE | AWS_IAM | CUSTOM_JWT | AUTHENTICATE_ONLY. NONE keeps
        # the sample to one moving part; it is not "IAM auth" — AWS_IAM is a
        # separate value this sample does not use. Use CUSTOM_JWT in production
        # (07-oauth shows it end to end with a Cognito user pool).
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

        # Step 2: Add MCP target
        print("\n" + "=" * 60)
        print(f"Step 2: Add MCP target ({args.mcp_endpoint})")
        print("=" * 60)
        # No credentialProviderConfigurations here: that is the OUTBOUND control
        # (how the Gateway authenticates to the tool server), and the default Exa
        # endpoint serves anonymous callers. Exa's free tier is rate limited and
        # shared, so a busy account can see the tool call fail with HTTP 429; pass
        # your own key to lift it, e.g.
        #   credentialProviderConfigurations=[{
        #       "credentialProviderType": "API_KEY",
        #       "credentialProvider": {"apiKeyCredentialProvider": {
        #           "providerArn": "<token-vault api-key provider arn>",
        #           "credentialLocation": "HEADER",
        #           "credentialParameterName": "x-api-key",
        #       }},
        #   }]
        resp = gw_control.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=args.target_name,
            targetConfiguration={"mcp": {"mcpServer": {"endpoint": args.mcp_endpoint}}},
        )
        target_id = resp["targetId"]
        print(f"  Target ID: {target_id}")
        poll_target_status(gw_control, gateway_id, target_id)

        # Step 3: Create Harness
        print("\n" + "=" * 60)
        print("Step 3: Create Harness")
        print("=" * 60)
        harness_name = f"GatewayHarness_{uuid.uuid4().hex[:8]}"
        resp = harness_control.create_harness(harnessName=harness_name, executionRoleArn=role_arn)
        harness_id = resp["harness"]["harnessId"]
        harness_arn = resp["harness"]["arn"]
        print(f"  Harness ID:  {harness_id}")
        print(f"  Harness ARN: {harness_arn}")
        poll_harness_status(harness_control, harness_id)

        # Step 4: Invoke agent via Gateway
        print("\n" + "=" * 60)
        print("Step 4: Invoke agent (tools served via Gateway)")
        print("=" * 60)
        session_id = str(uuid.uuid4()).upper()
        print(f"  Session ID: {session_id}")
        print(f"  Model:      {args.model}")
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
            _cleanup(gw_control, harness_control, gateway_id, target_id, harness_id, created_role)


if __name__ == "__main__":
    main()
