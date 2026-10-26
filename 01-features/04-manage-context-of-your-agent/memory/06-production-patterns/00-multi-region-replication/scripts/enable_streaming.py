"""Enable / disable AgentCore Memory record streaming to a Kinesis stream.

Record streaming is configured via ``streamDeliveryResources`` on the memory
(set at ``CreateMemory`` or via ``UpdateMemory``). This script flips it on or off
for an existing memory, pointing it at a Kinesis Data Stream ARN with a
``MEMORY_RECORDS`` / ``FULL_CONTENT`` content configuration — exactly what the
record-streaming LTM replication path consumes.

The memory's execution role (``memoryExecutionRoleArn``) must allow
``kinesis:PutRecord*`` / ``kinesis:DescribeStream`` on the target stream.

Usage::

    # turn streaming ON, delivering source LTM records to the Kinesis stream
    python scripts/enable_streaming.py \
        --memory-id mem-aaaaaaaaaa \
        --region us-east-1 \
        --stream-arn arn:aws:kinesis:us-east-1:123456789012:stream/agentcore-ltm

    # turn streaming OFF (e.g. when failing over / pausing replication)
    python scripts/enable_streaming.py \
        --memory-id mem-aaaaaaaaaa --region us-east-1 --disable
"""

import argparse
import json
import sys

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, ".")
from agentcore_replication import stream_delivery_resources


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--memory-id", required=True)
    p.add_argument("--region", required=True)
    p.add_argument("--stream-arn", help="Kinesis Data Stream ARN (required unless --disable)")
    p.add_argument("--disable", action="store_true", help="turn streaming OFF")
    args = p.parse_args()

    if not args.disable and not args.stream_arn:
        raise SystemExit("--stream-arn is required unless --disable is set")

    ctl = boto3.client("bedrock-agentcore-control", region_name=args.region)

    if args.disable:
        # An empty resources list detaches the stream.
        resources = {"resources": []}
        print(f"Disabling record streaming on {args.memory_id} ({args.region})")
    else:
        resources = stream_delivery_resources(args.stream_arn)
        print(f"Enabling record streaming on {args.memory_id} ({args.region})")
        print(f"  -> {args.stream_arn} (MEMORY_RECORDS / FULL_CONTENT)")

    try:
        resp = ctl.update_memory(
            memoryId=args.memory_id,
            streamDeliveryResources=resources,
        )
    except ClientError as exc:
        raise SystemExit(f"update_memory failed: {exc}")

    print(
        json.dumps(
            {"streamDeliveryResources": resp["memory"].get("streamDeliveryResources", {})},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
