#!/usr/bin/env python3
"""Run the receipt pipeline once on a receipt and print its result.

The pipeline's input is an S3 address, not a file: Textract reads the receipt image
from the inbox bucket. So given a local file, this script uploads it to the bucket
first, then invokes the pipeline Runtime with {s3_uri, user_id} and prints the
Runtime's response as one JSON line.

The upload goes under `samples/`, not `receipts/`. Only keys under `receipts/` start
the event-driven front door, so the receipt runs once, here, rather than also in
the background. To use the front door instead, upload under `receipts/<user_id>/`
and check the result later with scripts/receipt_status.py.

Usage:
    python3 scripts/test_invoke.py --region us-west-2                    # the sample receipt
    python3 scripts/test_invoke.py --region us-west-2 --file evals/fixtures/non_reconciling.png
    python3 scripts/test_invoke.py --region us-west-2 --s3-uri s3://...  # a receipt already in S3
"""

import argparse
import json
import os
import sys
import uuid

import boto3

SAMPLE_RECEIPT = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "sample-receipt.png")


def stack_outputs(region: str, stack: str) -> dict[str, str]:
    cfn = boto3.client("cloudformation", region_name=region)
    outs = cfn.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in outs}


def find_output(outputs: dict[str, str], match, name: str, stack: str) -> str:
    for key, value in outputs.items():
        if match(key):
            return value
    raise SystemExit(f"{name} output not found on stack {stack}")


def upload(region: str, bucket: str, path: str) -> str:
    key = f"samples/{os.path.basename(path)}"
    boto3.client("s3", region_name=region).upload_file(os.path.abspath(path), bucket, key)
    s3_uri = f"s3://{bucket}/{key}"
    print(f"Uploaded {os.path.relpath(path)} to {s3_uri} (outside receipts/, so the front door does not also run it)", file=sys.stderr)
    return s3_uri


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--stack", default="AgentCore-ReceiptsAgent-dev")
    parser.add_argument("--file", default=SAMPLE_RECEIPT, help="local receipt to upload (default: the sample receipt)")
    parser.add_argument("--s3-uri", help="a receipt already in S3; skips the upload")
    parser.add_argument("--user-id", default="user-001")
    args = parser.parse_args()

    outputs = stack_outputs(args.region, args.stack)
    # The pipeline Runtime's output starts with RuntimeArn (the chat one is ChatRuntimeArn).
    arn = find_output(outputs, lambda k: k.startswith("RuntimeArn"), "RuntimeArn", args.stack)
    if args.s3_uri:
        s3_uri = args.s3_uri
    else:
        # CDK may prefix construct outputs (InfraInboxBucketName...), so match by substring.
        bucket = find_output(outputs, lambda k: "InboxBucketName" in k, "InboxBucketName", args.stack)
        s3_uri = upload(args.region, bucket, args.file)

    print("Invoking the pipeline Runtime...", file=sys.stderr)
    client = boto3.client("bedrock-agentcore", region_name=args.region)
    payload = {"s3_uri": s3_uri, "user_id": args.user_id}
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=f"receipts-test-{uuid.uuid4().hex}",
        payload=json.dumps(payload).encode(),
    )
    body = resp["response"].read().decode() if hasattr(resp["response"], "read") else resp["response"]
    print(body)


if __name__ == "__main__":
    main()
