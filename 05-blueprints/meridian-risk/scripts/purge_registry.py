#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Delete all records from an AgentCore Registry.

Terraform calls this on destroy: a registry cannot be deleted while it still
holds records, and records have no Terraform resource in the preview.

Safe to run against an already-empty or already-deleted registry — a missing
registry is treated as success so `terraform destroy` is not blocked.
"""

import argparse
import sys

import boto3
from botocore.exceptions import ClientError


def log(message: str) -> None:
    print(f"[registry-purge] {message}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-id", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    client = boto3.client("bedrock-agentcore-control", region_name=args.region)

    try:
        records = []
        token = None
        while True:
            kwargs = {"registryId": args.registry_id, "maxResults": 100}
            if token:
                kwargs["nextToken"] = token
            response = client.list_registry_records(**kwargs)
            records.extend(response.get("registryRecords", []))
            token = response.get("nextToken")
            if not token:
                break
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            log(f"registry {args.registry_id} not found — nothing to purge")
            return 0
        log(f"ERROR listing records: {exc}")
        return 1

    if not records:
        log("no records to delete")
        return 0

    failures = 0
    for record in records:
        name = record.get("name", "?")
        try:
            client.delete_registry_record(
                registryId=args.registry_id, recordId=record["recordId"]
            )
            log(f"deleted {name}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                log(f"{name} already gone")
                continue
            log(f"ERROR deleting {name}: {exc}")
            failures += 1

    log(f"purged {len(records) - failures}/{len(records)} record(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
