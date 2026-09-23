#!/usr/bin/env python3
"""Upload the sample receipt fixture to the inbox bucket so the agent has a real
receipt to OCR. Prints the resulting s3:// URI.

Usage: python3 scripts/upload_sample_receipt.py --region us-west-2
"""

import argparse
import os

import boto3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--key", default="receipts/sample-receipt.png")
    args = parser.parse_args()

    account = boto3.client("sts", region_name=args.region).get_caller_identity()["Account"]
    bucket = f"receipts-inbox-{account}-{args.region}"
    fixture = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "sample-receipt.png")

    boto3.client("s3", region_name=args.region).upload_file(os.path.abspath(fixture), bucket, args.key)
    print(f"s3://{bucket}/{args.key}")


if __name__ == "__main__":
    main()
