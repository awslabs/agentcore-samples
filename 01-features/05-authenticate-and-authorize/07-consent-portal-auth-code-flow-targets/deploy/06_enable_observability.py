"""Optional: set log retention and print the debugging recipes for this sample.

AgentCore log groups are created with no expiry, which is a slow cost leak for
a sample you will tear down. This sets a retention period on the runtime and
gateway log groups (creating them if they do not exist yet, so retention is in
place before the first invocation), then prints the queries worth knowing.

Run from the sample root:
    python deploy/06_enable_observability.py --entra
    python deploy/06_enable_observability.py --okta --retention-days 7
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import gateway_name, load_env, select_idp_with_args


def ensure_retention(logs, group: str, days: int) -> None:
    try:
        logs.create_log_group(logGroupName=group)
        print(f"  ✓ Created log group: {group}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            print(f"  ⚠ Could not create {group}: {e.response['Error']['Code']}", file=sys.stderr)
            return
        print(f"  • Log group exists: {group}")
    try:
        logs.put_retention_policy(logGroupName=group, retentionInDays=days)
        print(f"    ✓ Retention set to {days} day(s)")
    except ClientError as e:
        print(f"    ⚠ Could not set retention: {e.response['Error']['Code']}", file=sys.stderr)


def main() -> None:
    idp, args = select_idp_with_args(
        __doc__,
        [
            (
                ["--retention-days"],
                {
                    "type": int,
                    "default": 14,
                    "help": "CloudWatch Logs retention in days (default: 14).",
                },
            )
        ],
    )
    load_env()

    region = os.environ.get("AWS_REGION") or boto3.Session().region_name
    logs = boto3.client("logs", region_name=region)
    runtime_name = os.environ.get("AGENT_RUNTIME_NAME", "")
    gw_name = gateway_name(idp)

    print(f"--- Log retention ({args.retention_days} days) ---")
    groups = [f"/aws/bedrock-agentcore/gateway/{gw_name}"]
    if runtime_name:
        groups.append(f"/aws/bedrock-agentcore/runtimes/{runtime_name}")
    for group in groups:
        ensure_retention(logs, group, args.retention_days)

    print()
    print("--- Debugging recipes ---")
    print()
    print("Agent logs, including the consent-elicitation detection:")
    print("  # from inside the scaffolded project folder")
    print("  agentcore logs --since 15m")
    print('  agentcore logs --since 15m --query "CONSENT_REQUIRED"')
    print()
    print("Traces across the runtime -> gateway -> GitHub hops:")
    print("  agentcore traces --since 15m")
    print()
    print("Gateway-side view of a tool call (the -32042 elicitation appears here):")
    print(f"  aws logs tail /aws/bedrock-agentcore/gateway/{gw_name} --since 15m --region {region}")
    print()
    print("Consent state for a target, from the control plane:")
    print(
        "  aws bedrock-agentcore-control get-gateway-target --region "
        f"{region} \\\n"
        "    --gateway-identifier $GATEWAY_ID --target-id $GITHUB_TARGET_ID \\\n"
        "    --query credentialProviderConfigurations"
    )
    print()
    print("Portal status and URL:")
    print(
        f"  aws bedrock-agentcore-control get-consent-portal --region {region} \\\n"
        "    --consent-portal-identifier $PORTAL_ID --query '{status:status,url:portalUrl}'"
    )
    print()
    print("One-time console toggles for richer runtime telemetry (CloudWatch ->")
    print("Application Signals -> Transaction Search, and enabling Application")
    print("Signals for AgentCore) are per-account, not per-sample.")


if __name__ == "__main__":
    main()
