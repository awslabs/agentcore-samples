#!/usr/bin/env python3
"""Enable full observability (tracing + logs) for AgentCore resources.

Enables:
1. CloudWatch Transaction Search (one-time, per-region)
2. TRACES + APPLICATION_LOGS delivery for:
   - Gateway
   - Gateway WorkloadIdentity (identity tracing)
   - Memory
   - Runtime WorkloadIdentity (identity tracing)

Runtime span/log tracing is already handled by `instrumentation.enableOtel: true`
in agentcore.json — this script adds the resource-level deliveries that the
console exposes as per-resource "Tracing" and "Identity → Tracing" toggles.

This script is idempotent — safe to run multiple times.

Usage:
    python3 scripts/enable_observability.py --region us-west-2
    python3 scripts/enable_observability.py --region us-west-2 --stack-name AgentCore-ClaimsAgent-dev
"""

import argparse
import json
import re

import boto3
from botocore.exceptions import ClientError

DEFAULT_STACK_NAME = "AgentCore-ClaimsAgent-dev"

# CloudWatch Logs delivery source names must match [\w-]* and be <= 60 chars.
_NAME_MAX = 60


def get_args():
    parser = argparse.ArgumentParser(description="Enable AgentCore observability")
    parser.add_argument("--region", default="us-west-2", help="AWS region")
    parser.add_argument("--stack-name", default=DEFAULT_STACK_NAME, help="CloudFormation stack name")
    return parser.parse_args()


def _sanitize(name: str) -> str:
    """Make a delivery-source/destination name valid: [\\w-]* and <= 60 chars."""
    clean = re.sub(r"[^\w-]", "-", name)
    return clean[:_NAME_MAX]


def enable_transaction_search(region: str) -> None:
    """Enable CloudWatch Transaction Search (routes X-Ray spans to aws/spans)."""
    print("  📡 Enabling CloudWatch Transaction Search...")

    xray = boto3.client("xray", region_name=region)
    logs = boto3.client("logs", region_name=region)
    account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]

    try:
        dest = xray.get_trace_segment_destination()
        if dest.get("Destination") == "CloudWatchLogs":
            print("    ✓ Already enabled (destination: CloudWatchLogs)")
            return
    except ClientError:
        pass

    policy_document = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "TransactionSearchXRayAccess",
                    "Effect": "Allow",
                    "Principal": {"Service": "xray.amazonaws.com"},
                    "Action": "logs:PutLogEvents",
                    "Resource": [
                        f"arn:aws:logs:{region}:{account_id}:log-group:aws/spans:*",
                        f"arn:aws:logs:{region}:{account_id}:log-group:/aws/application-signals/data:*",
                    ],
                    "Condition": {
                        "ArnLike": {"aws:SourceArn": f"arn:aws:xray:{region}:{account_id}:*"},
                        "StringEquals": {"aws:SourceAccount": account_id},
                    },
                }
            ],
        }
    )

    try:
        logs.put_resource_policy(policyName="AgentCoreTransactionSearch", policyDocument=policy_document)
    except ClientError as e:
        if "already exists" not in str(e).lower():
            print(f"    ⚠️  Resource policy: {e}")

    try:
        xray.update_trace_segment_destination(Destination="CloudWatchLogs")
        print("    ✓ Transaction Search enabled")
    except ClientError as e:
        print(f"    ⚠️  update_trace_segment_destination: {e}")


def discover_resources(region: str, stack_name: str) -> list:
    """Discover AgentCore resource ARNs from CloudFormation outputs (reliable, full ARNs).

    Returns a list of (kind, label, arn) tuples. Kinds: gateway, memory,
    gateway-identity, runtime-identity.
    """
    cf = boto3.client("cloudformation", region_name=region)
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    try:
        outputs = cf.describe_stacks(StackName=stack_name)["Stacks"][0].get("Outputs", [])
    except ClientError as e:
        print(f"    ⚠️  describe_stacks: {e}")
        return []

    gateway_arn = memory_arn = runtime_arn = None
    gateway_id = runtime_id = None

    for o in outputs:
        val = o.get("OutputValue", "")
        if not val.startswith("arn:aws:bedrock-agentcore:"):
            continue
        if ":gateway/" in val and gateway_arn is None:
            gateway_arn = val
            gateway_id = val.split(":gateway/")[-1]
        elif ":memory/" in val and memory_arn is None:
            memory_arn = val
        elif ":runtime/" in val and runtime_arn is None:
            runtime_arn = val
            runtime_id = val.split(":runtime/")[-1]

    resources = []
    if gateway_arn:
        resources.append(("gateway", "Gateway", gateway_arn))
    if memory_arn:
        resources.append(("memory", "Memory", memory_arn))

    # Workload identity ARNs come from the control-plane get_* calls.
    if gateway_id:
        try:
            gw = control.get_gateway(gatewayIdentifier=gateway_id)
            wi_arn = gw.get("workloadIdentityDetails", {}).get("workloadIdentityArn")
            if wi_arn:
                resources.append(("gateway-identity", "Gateway Identity", wi_arn))
        except ClientError as e:
            print(f"    ⚠️  get_gateway: {e}")

    if runtime_id:
        try:
            rt = control.get_agent_runtime(agentRuntimeId=runtime_id)
            wi_arn = rt.get("workloadIdentityDetails", {}).get("workloadIdentityArn")
            if wi_arn:
                resources.append(("runtime-identity", "Runtime Identity", wi_arn))
        except ClientError as e:
            print(f"    ⚠️  get_agent_runtime: {e}")

    print(f"    Found: {', '.join(label for _, label, _ in resources) or 'none'}")
    return resources


def _put_source(logs_client, name: str, log_type: str, resource_arn: str) -> bool:
    try:
        logs_client.put_delivery_source(name=name, logType=log_type, resourceArn=resource_arn)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            return True  # already exists
        print(f"      ⚠️  put_delivery_source({log_type}): {e.response['Error']['Message'][:120]}")
        return False


def _put_destination(logs_client, name: str, dest_type: str, log_group_arn: str = "") -> str:
    kwargs = {"name": name, "deliveryDestinationType": dest_type}
    if dest_type == "CWL":
        kwargs["deliveryDestinationConfiguration"] = {"destinationResourceArn": log_group_arn}
    try:
        resp = logs_client.put_delivery_destination(**kwargs)
        return resp["deliveryDestination"]["arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            try:
                return logs_client.get_delivery_destination(name=name)["deliveryDestination"]["arn"]
            except ClientError:
                return ""
        print(f"      ⚠️  put_delivery_destination({dest_type}): {e.response['Error']['Message'][:120]}")
        return ""


def _create_delivery(logs_client, source_name: str, dest_arn: str) -> None:
    try:
        logs_client.create_delivery(deliverySourceName=source_name, deliveryDestinationArn=dest_arn)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConflictException":
            print(f"      ⚠️  create_delivery: {e.response['Error']['Message'][:120]}")


def enable_for_resource(logs_client, kind: str, arn: str, account_id: str, region: str) -> None:
    """Create TRACES (→ XRAY) and APPLICATION_LOGS (→ CWL) deliveries for one resource."""
    slug = _sanitize(arn.split("/")[-1])

    # ── TRACES → X-Ray (Transaction Search)
    traces_source = _sanitize(f"{kind}-{slug}-traces")
    traces_dest = _sanitize(f"{kind}-{slug}-traces-dest")
    if _put_source(logs_client, traces_source, "TRACES", arn):
        dest_arn = _put_destination(logs_client, traces_dest, "XRAY")
        if dest_arn:
            _create_delivery(logs_client, traces_source, dest_arn)

    # ── APPLICATION_LOGS → CloudWatch Logs
    log_group = f"/aws/vendedlogs/bedrock-agentcore/{kind}/{slug}"
    try:
        logs_client.create_log_group(logGroupName=log_group)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            pass
    log_group_arn = f"arn:aws:logs:{region}:{account_id}:log-group:{log_group}"

    logs_source = _sanitize(f"{kind}-{slug}-logs")
    logs_dest = _sanitize(f"{kind}-{slug}-logs-dest")
    if _put_source(logs_client, logs_source, "APPLICATION_LOGS", arn):
        dest_arn = _put_destination(logs_client, logs_dest, "CWL", log_group_arn)
        if dest_arn:
            _create_delivery(logs_client, logs_source, dest_arn)


def main():
    args = get_args()
    region = args.region

    print(f"🔭 Enabling full observability for {args.stack_name} in {region}...")
    print()

    enable_transaction_search(region)
    print()

    print("  🔍 Discovering AgentCore resources...")
    resources = discover_resources(region, args.stack_name)
    print()

    if not resources:
        print("  ℹ️  No Gateway/Memory resources found. Runtime tracing still active via enableOtel.")
        return

    logs_client = boto3.client("logs", region_name=region)
    account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]

    icons = {
        "gateway": "🌐",
        "memory": "🧠",
        "gateway-identity": "🔑",
        "runtime-identity": "🔑",
    }
    for kind, label, arn in resources:
        print(f"  {icons.get(kind, '•')} {label}: {arn.split('/')[-1][:30]}...")
        enable_for_resource(logs_client, kind, arn, account_id, region)
        print("    ✓ Tracing + logs configured")

    print()
    print("✅ Full observability enabled!")
    print("   Traces: CloudWatch → Application Signals → Transaction Search (aws/spans)")
    print("   GenAI dashboard: CloudWatch → Application Signals → GenAI Observability")


if __name__ == "__main__":
    main()
