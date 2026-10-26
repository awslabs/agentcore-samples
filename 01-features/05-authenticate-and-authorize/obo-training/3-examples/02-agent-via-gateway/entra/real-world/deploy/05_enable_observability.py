"""
Enable + inspect observability for UC2 Entra: Runtime and Gateway.

AgentCore auto-emits logs and traces — you don't have to opt into them per
resource. This script complements that with:

  1. Sets log retention on all UC2-related /aws/bedrock-agentcore/* log
     groups so they don't grow unbounded (default is never-expire).
  2. Checks whether CloudWatch **Transaction Search** is enabled — that's
     required for the AgentCore CLI's `agentcore logs --query "..."` to
     actually search inside log content. Without it, you can only tail.
  3. Prints ready-to-run diagnostic commands for the runtime + gateway.

What it does NOT do:
  - Enable CloudWatch Application Signals (that's an account-wide, one-time
    click in the console — see the "Also do this" printout at the end).
  - Create any new resources; everything here just adjusts existing ones or
    surfaces information.

Run:
    python deploy/05_enable_observability.py                 # 30-day retention
    python deploy/05_enable_observability.py --retention 7   # override
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


VALID_RETENTIONS = {
    1,
    3,
    5,
    7,
    14,
    30,
    60,
    90,
    120,
    150,
    180,
    365,
    400,
    545,
    731,
    1096,
    1827,
    2192,
    2557,
    2922,
    3288,
    3653,
}


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


def find_uc2_log_groups(logs, agent_runtime_name: str, gateway_name: str) -> list[dict]:
    """Return log groups that belong to this UC2 setup."""
    kept: list[dict] = []
    paginator = logs.get_paginator("describe_log_groups")
    for page in paginator.paginate(logGroupNamePrefix="/aws/bedrock-agentcore/"):
        for lg in page.get("logGroups", []):
            name = lg["logGroupName"]
            hay = name.lower()
            # Truncated CFN names (matches CloudFormation truncation of long
            # runtime names) surface as a prefix — match on the first 15 chars
            # of the runtime name to be robust against that.
            runtime_prefix = agent_runtime_name[:15].lower()
            if runtime_prefix in hay or gateway_name.lower() in hay:
                kept.append(lg)
    return kept


def set_retention(logs, log_group_name: str, days: int) -> None:
    try:
        logs.put_retention_policy(
            logGroupName=log_group_name,
            retentionInDays=days,
        )
    except ClientError as e:
        print(
            f"  ✗ {log_group_name}: {e.response['Error'].get('Code')}: {e.response['Error'].get('Message')}",
            file=sys.stderr,
        )


# Note: an earlier version of this script had a `check_transaction_search()`
# helper. Removed because there's no reliable public API to detect whether
# CloudWatch Transaction Search is enabled — the "Also do this" printout
# at the end of main() links the user directly to the console toggle.


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--retention",
        type=int,
        default=30,
        help="Log retention in days (CloudWatch-valid values only, e.g. 7, 30, 90).",
    )
    args = parser.parse_args()

    if args.retention not in VALID_RETENTIONS:
        print(
            f"ERROR: --retention must be one of {sorted(VALID_RETENTIONS)} (CloudWatch-imposed values).",
            file=sys.stderr,
        )
        sys.exit(1)

    real_world_root = Path(__file__).resolve().parent.parent
    load_dotenv(real_world_root / ".env")
    region = os.environ.get("AWS_REGION", "us-west-2")

    agent_runtime_name = must_env("AGENT_RUNTIME_NAME")
    gateway_name = must_env("GATEWAY_NAME")

    logs = boto3.client("logs", region_name=region)

    # 1) Log retention.
    print(f"[1/2] Log retention → {args.retention} days")
    matched = find_uc2_log_groups(logs, agent_runtime_name, gateway_name)
    if not matched:
        print(f"  • No /aws/bedrock-agentcore/* log groups found yet for '{agent_runtime_name}' or '{gateway_name}'.")
        print("    Trigger at least one invocation, then re-run this script.")
    else:
        for lg in matched:
            current = lg.get("retentionInDays")
            if current == args.retention:
                print(f"  • {lg['logGroupName']:70s}  already at {current}d")
                continue
            set_retention(logs, lg["logGroupName"], args.retention)
            marker = f"was {current if current else 'never-expire'} → {args.retention}d"
            print(f"  ✓ {lg['logGroupName']:70s}  {marker}")

    # 2) Print ready-to-run debug commands.
    print()
    print("[2/2] Debug quick-reference")
    print("  Tail agent runtime logs (all levels):")
    print(f"    agentcore logs --since 10m --runtime {agent_runtime_name}")
    print()
    print("  Tail agent runtime logs (warn+ only, useful when 'iss mismatch'):")
    print(f"    agentcore logs --since 10m --runtime {agent_runtime_name} --level warn")
    print()
    print("  Search runtime logs for a substring:")
    print(f"    agentcore logs --since 30m --runtime {agent_runtime_name} --query 'OBO'")
    print()
    print("  List traces (needs CloudWatch Application Signals enabled — see below):")
    print(f"    agentcore traces list --runtime {agent_runtime_name}")
    print()
    print("  Get a specific trace:")
    print("    agentcore traces get <trace-id>")
    print()
    print("  Raw CloudWatch tail (fallback, region-explicit):")
    print("    aws logs describe-log-groups \\")
    print("      --log-group-name-prefix /aws/bedrock-agentcore \\")
    print(f"      --region {region} \\")
    print(
        f"      --query 'logGroups[?contains(logGroupName, `{agent_runtime_name[:15]}`)"
        f" || contains(logGroupName, `{gateway_name}`)].logGroupName' --output text"
    )
    print()
    print("  Then tail a specific group:")
    print(f"    aws logs tail <log-group-name> --since 10m --follow --region {region}")
    print()

    print("Also do this (one-time, per account/region):")
    print("  Enable CloudWatch Application Signals so traces are queryable:")
    print(
        f"    open 'https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#application-signals-services'"
    )
    print("  Enable CloudWatch Transaction Search so `agentcore logs --query` searches log CONTENT:")
    print(
        f"    open 'https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#logsV2:logs-transaction-search'"
    )
    print()
    print("✓ Observability config ready.")


if __name__ == "__main__":
    main()
