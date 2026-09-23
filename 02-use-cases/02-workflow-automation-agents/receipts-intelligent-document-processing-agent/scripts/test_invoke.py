#!/usr/bin/env python3
"""Invoke the deployed Runtime (Phase 1: confirms the stub responds).

Reads the Runtime ARN from the CloudFormation stack outputs and invokes it with
a sample {s3_uri, user_id} payload. In Phase 1 the agent is a stub, so a healthy
response is the stub echo confirming the config seam resolved.

Usage: python3 scripts/test_invoke.py --region us-west-2
"""

import argparse
import json
import uuid

import boto3


def get_runtime_arn(region: str, stack: str) -> str:
    cfn = boto3.client("cloudformation", region_name=region)
    outputs = cfn.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    for o in outputs:
        if o["OutputKey"].startswith("RuntimeArn"):
            return o["OutputValue"]
    raise SystemExit(f"RuntimeArn output not found on stack {stack}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--stack", default="AgentCore-ReceiptsAgent-dev")
    parser.add_argument("--s3-uri", default="s3://receipts-inbox-EXAMPLE/receipts/coffee.jpg")
    parser.add_argument("--user-id", default="user-001")
    args = parser.parse_args()

    arn = get_runtime_arn(args.region, args.stack)
    client = boto3.client("bedrock-agentcore", region_name=args.region)
    payload = {"s3_uri": args.s3_uri, "user_id": args.user_id}

    resp = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=f"receipts-test-{uuid.uuid4().hex}",
        payload=json.dumps(payload).encode(),
    )
    body = resp["response"].read().decode() if hasattr(resp["response"], "read") else resp["response"]
    print(body)


if __name__ == "__main__":
    main()
