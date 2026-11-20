"""Record which deployed AgentCore Runtime the other scripts should work with.

The Event-Driven Claims Agent deploys itself (see its README). This script looks the runtime up by
name and writes agent_state_<name>.json, which setup_insights.py, drive.py, status.py,
export_insights.py, verify_fix.py and cleanup.py read. It only reads from your account.

Usage:
    python scripts/register_agent.py --name ClaimsAgent [--runtime-name ClaimsAgent_claimsagent] [--region us-east-1]
"""

from __future__ import annotations

import argparse
import json
import sys

from botocore.exceptions import BotoCoreError, ClientError
from common import DEFAULT_REGION, client, save_state, state_path, validate_name

# The claims agent's agentcore project is named "ClaimsAgent" and its agent "claimsagent".
DEFAULT_RUNTIME_NAME = "ClaimsAgent_claimsagent"


def find_runtime(control, runtime_name: str) -> dict:
    """Return the one runtime whose name matches exactly, or exit with a message that says what to check."""
    matches = [
        runtime
        for page in control.get_paginator("list_agent_runtimes").paginate()
        for runtime in page.get("agentRuntimes", [])
        if runtime["agentRuntimeName"] == runtime_name
    ]
    if not matches:
        sys.exit(
            f"No runtime named {runtime_name!r} in this account and Region. Deploy the claims agent first, "
            "or pass --runtime-name with the name shown by: "
            "aws bedrock-agentcore-control list-agent-runtimes --region <region>"
        )
    if len(matches) > 1:
        sys.exit(f"{len(matches)} runtimes are named {runtime_name!r}; expected exactly one.")
    runtime = matches[0]
    if runtime["status"] != "READY":
        sys.exit(f"Runtime {runtime_name!r} is {runtime['status']}, not READY. Wait for the deployment to finish.")
    return runtime


def build_state(runtime: dict, region: str, account: str) -> dict:
    """The facts the other scripts need. The application name Continuous Insights groups by is the
    span service.name, which AgentCore Runtime sets to <runtime name>.DEFAULT."""
    runtime_id = runtime["agentRuntimeId"]
    return {
        "runtime_name": runtime["agentRuntimeName"],
        "runtime_id": runtime_id,
        "runtime_arn": runtime["agentRuntimeArn"],
        "log_group": f"/aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT",
        "service_name": f"{runtime['agentRuntimeName']}.DEFAULT",
        "region": region,
        "account_id": account,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--name", required=True, help="label for this setup; names the state file, evaluation config and role"
    )
    parser.add_argument("--runtime-name", default=DEFAULT_RUNTIME_NAME)
    parser.add_argument("--region", default=DEFAULT_REGION)
    args = parser.parse_args()
    name = validate_name(args.name)

    try:
        account = client(args.region, "sts").get_caller_identity()["Account"]
        runtime = find_runtime(client(args.region, "bedrock-agentcore-control"), args.runtime_name)
    except (ClientError, BotoCoreError) as err:
        sys.exit(f"Could not read the runtime: {err}")

    # Keep what setup_insights.py saved earlier (evaluation role, config id) when registering again.
    existing = json.loads(state_path(name).read_text(encoding="utf-8")) if state_path(name).exists() else {}
    save_state(name, {**existing, **build_state(runtime, args.region, account)})
    print(f"Registered {runtime['agentRuntimeName']} ({runtime['agentRuntimeId']}) in {args.region}.")
    print(f"Wrote {state_path(name).name}. Next: python scripts/setup_insights.py --name {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
