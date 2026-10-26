"""
Deploy the WebRTC voice agent to Amazon Bedrock AgentCore Runtime V2.

Builds the agent container remotely with CodeBuild (linux/arm64, so no local
Docker is needed) and creates the agent runtime with platformVersion="V2".

VPC network mode is required: PUBLIC mode does not support outbound UDP, which
WebRTC needs to reach the KVS TURN servers. Supply a private subnet that has
internet egress through a NAT gateway.

Usage:
    python deploy.py --subnets subnet-abc123 --security-groups sg-abc123

Environment Variables:
    ACCOUNT_ID    AWS Account ID (required if not provided via --account-id)
    AWS_REGION    AWS Region (default: us-west-2)
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
import yaml

# The starter toolkit is used only to build the image and create the execution
# role. The runtime itself is created with boto3, because no version of the
# toolkit or the AgentCore CLI exposes platformVersion.
from bedrock_agentcore_starter_toolkit.operations.runtime.launch import (
    _execute_codebuild_workflow,
    get_or_create_runtime_execution_role,
)
from bedrock_agentcore_starter_toolkit.utils.runtime.config import load_config
from botocore.exceptions import ClientError

PLATFORM_VERSION = "V2"
AGENT_DIR = Path(__file__).parent / "agent"
DEFAULT_AGENT_NAME = "bidi_05_webrtc_agent"
DEFAULT_CHANNEL = "voice-agent-minimal"

# VPC mode adds network interface provisioning on top of the snapshot preparation, and
# how long that takes varies between runs, so allow a generous timeout rather than one
# tuned to an observed duration.
POLL_SECONDS = 15
CREATE_TIMEOUT_SECONDS = 1800


def log(message: str) -> None:
    print(message, flush=True)


def build_execution_role(region: str, account_id: str, agent_name: str, channel_name: str) -> str:
    """Create the execution role, then attach the KVS and Bedrock policies.

    The policies must be attached BEFORE the runtime is created. Runtime V2 boots
    the container during create, and bot.py calls kvs.init() at FastAPI startup,
    so a runtime created before the permissions exist fails with AccessDenied.
    """
    log("\nCreating execution role...")
    session = boto3.Session(region_name=region)
    logger = logging.getLogger("deploy")

    # Provides ECR pull, CloudWatch Logs and workload identity access.
    role_arn = get_or_create_runtime_execution_role(
        session=session,
        logger=logger,
        region=region,
        account_id=account_id,
        agent_name=agent_name,
    )
    log(f"   role: {role_arn}")

    iam = boto3.client("iam")
    role_name = role_arn.split("/")[-1]
    here = Path(__file__).parent

    for policy_name, filename in (
        ("kvs-access", "kvs-iam-policy.json"),
        ("bedrock-nova-sonic", "bedrock-iam-policy.json"),
    ):
        # Substitute every placeholder the policy files carry. The channel name matters
        # as much as the account: --channel-name is configurable, and a policy left
        # scoped to the default channel would deny the channel the agent actually uses.
        raw = (
            (here / filename)
            .read_text(encoding="utf-8")
            .replace("ACCOUNT_ID", account_id)
            .replace("AWS_REGION", region)
            .replace("KVS_CHANNEL_NAME", channel_name)
        )
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(json.loads(raw)),
        )
        log(f"   attached {policy_name} from {filename}")

    # IAM is eventually consistent; give the new policies a moment to propagate.
    log("   waiting 10s for IAM propagation...")
    time.sleep(10)
    return role_arn


def build_image(region: str, account_id: str, agent_name: str, role_arn: str) -> str:
    """Build the ARM64 image with CodeBuild and push it to ECR."""
    log("\nBuilding container image with CodeBuild (this takes a few minutes)...")

    config_path = AGENT_DIR / ".bedrock_agentcore.yaml"
    config = {
        "agents": {
            agent_name: {
                "name": agent_name,
                "entrypoint": "bot.py",
                "runtime": "python3.12",
                "aws": {
                    "account": account_id,
                    "region": region,
                    "execution_role": role_arn,
                    "ecr_auto_create": True,
                },
            }
        },
        "default_agent": agent_name,
        "region": region,
    }
    config_path.write_text(yaml.dump(config, default_flow_style=False), encoding="utf-8")

    original_dir = Path.cwd()
    os.chdir(AGENT_DIR)
    try:
        project_config = load_config(config_path)
        agent_config = project_config.agents[agent_name]

        # ecr_only=True stops before the toolkit creates a runtime, so we can
        # create it ourselves with platformVersion set.
        _, container_uri, _, _ = _execute_codebuild_workflow(
            config_path=config_path,
            agent_name=agent_name,
            agent_config=agent_config,
            project_config=project_config,
            ecr_only=True,
        )
    finally:
        os.chdir(original_dir)

    log(f"   image: {container_uri}")
    return container_uri


def find_runtime_id(client, name: str):
    """Resolve a runtime name to its id, since the API is keyed on id."""
    next_token = None
    while True:
        kwargs = {"nextToken": next_token} if next_token else {}
        response = client.list_agent_runtimes(**kwargs)
        for runtime in response.get("agentRuntimes", []):
            if runtime.get("agentRuntimeName") == name:
                return runtime.get("agentRuntimeId")
        next_token = response.get("nextToken")
        if not next_token:
            return None


def wait_for_ready(client, runtime_id: str) -> dict:
    """Poll until READY. There is no waiter for this operation."""
    started = time.time()
    status = None

    while time.time() - started < CREATE_TIMEOUT_SECONDS:
        runtime = client.get_agent_runtime(agentRuntimeId=runtime_id)
        status = runtime.get("status")
        elapsed = int(time.time() - started)

        if status == "READY":
            log(f"   READY after {elapsed}s")
            return runtime

        # Match a FAILED suffix rather than listing every failure status.
        if status and status.upper().endswith("FAILED"):
            # GetAgentRuntime returns failureReason, not statusReason. Keep both so this
            # still reports something if the field is ever renamed.
            reason = runtime.get("failureReason") or runtime.get("statusReason") or "no reason reported"
            log(f"\nRuntime {status}: {reason}")
            log(f"Check logs: /aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT")
            log("   build-logs-*   container output during create")
            log("   runtime-logs-* container output during invocation")
            log("If there is no container log stream at all, provisioning failed")
            log("before the container started -- check the execution role.")
            raise RuntimeError(f"Runtime {status}")

        log(f"   [{elapsed:>4}s] {status}")
        time.sleep(POLL_SECONDS)

    raise RuntimeError(f"Runtime not READY within {CREATE_TIMEOUT_SECONDS}s (last status: {status})")


def create_runtime(
    region: str,
    agent_name: str,
    container_uri: str,
    role_arn: str,
    subnets: list,
    security_groups: list,
    channel_name: str,
) -> dict:
    """Create or update the runtime on AgentCore Runtime V2 in VPC network mode."""
    log(f"\nCreating AgentCore Runtime ({PLATFORM_VERSION}, VPC mode)...")
    client = boto3.client("bedrock-agentcore-control", region_name=region)

    request = {
        "agentRuntimeName": agent_name,
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": container_uri}},
        "roleArn": role_arn,
        # VPC mode is required: PUBLIC mode has no outbound UDP for WebRTC TURN.
        "networkConfiguration": {
            "networkMode": "VPC",
            "networkModeConfig": {"subnets": subnets, "securityGroups": security_groups},
        },
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "platformVersion": PLATFORM_VERSION,
        "environmentVariables": {
            "KVS_CHANNEL_NAME": channel_name,
            # bot.py reads AWS_REGION; boto3's own default resolution reads AWS_DEFAULT_REGION.
            # Set both so the container never falls back to a hardcoded region.
            "AWS_REGION": region,
            "AWS_DEFAULT_REGION": region,
        },
    }

    existing_id = find_runtime_id(client, agent_name)
    if existing_id:
        log(f"   runtime exists, updating {existing_id}")
        # UpdateAgentRuntime is keyed on agentRuntimeId and does not accept
        # agentRuntimeName, so it has to be removed from the request.
        update_request = {k: v for k, v in request.items() if k != "agentRuntimeName"}
        try:
            client.update_agent_runtime(agentRuntimeId=existing_id, **update_request)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConflictException":
                log("   runtime is still changing state; wait for it to settle and retry")
            raise
        runtime_id = existing_id
    else:
        runtime_id = client.create_agent_runtime(**request)["agentRuntimeId"]
        log(f"   create started: {runtime_id}")

    log("   waiting for READY (VPC mode takes several minutes)...")
    runtime = wait_for_ready(client, runtime_id)

    # Neither create nor update echoes platformVersion, so confirm it from Get.
    reported = runtime.get("platformVersion", "<absent>")
    if reported != PLATFORM_VERSION:
        log(f"   WARNING: expected {PLATFORM_VERSION} but runtime reports {reported}")
    else:
        log(f"   confirmed platformVersion: {reported}")

    return {
        "agent_arn": runtime["agentRuntimeArn"],
        "agent_id": runtime_id,
        "platform_version": reported,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deploy the WebRTC voice agent to AgentCore Runtime V2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example:
  python deploy.py --subnets subnet-0123456789abcdef0 --security-groups sg-0123456789abcdef0

The subnet must be private with internet egress through a NAT gateway, so the
agent can reach the KVS TURN servers. See README.md for VPC setup.
        """,
    )
    parser.add_argument("--account-id", help="AWS Account ID (or set ACCOUNT_ID env var)")
    parser.add_argument("--region", help="AWS Region (default: us-west-2 or AWS_REGION env var)")
    parser.add_argument(
        "--subnets",
        required=True,
        help="Comma-separated private subnet IDs with NAT gateway egress",
    )
    parser.add_argument(
        "--security-groups",
        required=True,
        help="Comma-separated security group IDs",
    )
    parser.add_argument("--agent-name", default=DEFAULT_AGENT_NAME, help=f"Agent name (default: {DEFAULT_AGENT_NAME})")
    parser.add_argument(
        "--channel-name", default=DEFAULT_CHANNEL, help=f"KVS signaling channel (default: {DEFAULT_CHANNEL})"
    )
    args = parser.parse_args()

    # This sample defaults to us-west-2, matching the region its agent code, client and
    # IAM policy files already use. Pass --region to deploy elsewhere.
    region = args.region or os.getenv("AWS_REGION", "us-west-2")

    account_id = args.account_id or os.getenv("ACCOUNT_ID")
    if not account_id:
        log("ERROR: ACCOUNT_ID is required. Set via --account-id or the ACCOUNT_ID environment variable.")
        return 1

    subnets = [s.strip() for s in args.subnets.split(",") if s.strip()]
    security_groups = [s.strip() for s in args.security_groups.split(",") if s.strip()]

    log(f"Deploying {args.agent_name} to AgentCore Runtime {PLATFORM_VERSION}")
    log(f"   account:         {account_id}")
    log(f"   region:          {region}")
    log(f"   subnets:         {', '.join(subnets)}")
    log(f"   security groups: {', '.join(security_groups)}")

    try:
        role_arn = build_execution_role(region, account_id, args.agent_name, args.channel_name)
        container_uri = build_image(region, account_id, args.agent_name, role_arn)
        runtime = create_runtime(
            region=region,
            agent_name=args.agent_name,
            container_uri=container_uri,
            role_arn=role_arn,
            subnets=subnets,
            security_groups=security_groups,
            channel_name=args.channel_name,
        )
    except Exception as e:  # broad on purpose: surface any failure to the operator
        log(f"\nDeployment failed: {e}")
        return 1

    config = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "aws_region": region,
        "account_id": account_id,
        "agent_name": args.agent_name,
        "agent_arn": runtime["agent_arn"],
        # The README's cleanup step reads the runtime id from this file.
        "agent_id": runtime["agent_id"],
        "iam_role_arn": role_arn,
        "platform_version": runtime["platform_version"],
        "container_uri": container_uri,
        "network_mode": "VPC",
        "subnets": subnets,
        "security_groups": security_groups,
        "kvs_channel_name": args.channel_name,
    }
    config_file = Path(__file__).parent / "setup_config.json"
    config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")

    log("\n" + "=" * 70)
    log("Deployment complete")
    log("=" * 70)
    log(f"Agent ARN: {runtime['agent_arn']}")
    log(f"Platform:  {runtime['platform_version']}")
    log(f"Config:    {config_file}")
    log("\nNext steps:")
    log("  1. cd server && pip install -r requirements.txt && python server.py")
    log("  2. Open http://localhost:7860")
    log("  3. Enter the Agent ARN above, then click Connect and speak")
    return 0


if __name__ == "__main__":
    sys.exit(main())
