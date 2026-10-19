#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Seed (or delete) a URL-synchronized MCP record — the video's auto-populate flow.

Instead of hand-authoring the tool list (as seed_registry.py does with inlineContent),
this creates a record with synchronizationType=URL: the Registry itself calls the
given MCP endpoint over OAuth (client-credentials, via the referenced credential
provider) and auto-populates the record's tool descriptor. Re-syncs keep it fresh.

Records have no Terraform resource, so Terraform invokes this from a null_resource.
After approval it reads the record back and reports how many tools were discovered,
which is the proof that auto-population worked.
"""

import argparse
import json
import sys
import time

import boto3
from botocore.exceptions import ClientError

RECORD_NAME = "kyc-tools-oauth-synced"

TERMINAL_STATUSES = {
    "DRAFT",
    "APPROVED",
    "PENDING_APPROVAL",
    "REJECTED",
    "DEPRECATED",
    "CREATE_FAILED",
    "UPDATE_FAILED",
}
POLL_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 3


def log(message: str) -> None:
    print(f"[sync-record] {message}", flush=True)


def build_spec(mcp_url: str, provider_arn: str, scope: str) -> dict:
    """MCP record whose tools the Registry auto-discovers from mcp_url over OAuth."""
    return {
        "name": RECORD_NAME,
        "description": (
            "KYC data tools auto-discovered from an OAuth-protected MCP gateway via "
            "URL synchronization (the Registry calls the endpoint and populates this "
            "record's tools; no hand-authored tool list)."
        ),
        "descriptorType": "MCP",
        "synchronizationType": "URL",
        "synchronizationConfiguration": {
            "fromUrl": {
                "url": mcp_url,
                "credentialProviderConfigurations": [
                    {
                        "credentialProviderType": "OAUTH",
                        "credentialProvider": {
                            "oauthCredentialProvider": {
                                "providerArn": provider_arn,
                                "grantType": "CLIENT_CREDENTIALS",
                                "scopes": [scope],
                            }
                        },
                    }
                ],
            }
        },
    }


def existing(client, registry_id: str) -> dict | None:
    token = None
    while True:
        kwargs = {"registryId": registry_id, "maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        resp = client.list_registry_records(**kwargs)
        for record in resp.get("registryRecords", []):
            if record["name"] == RECORD_NAME:
                return record
        token = resp.get("nextToken")
        if not token:
            return None


def wait_for_terminal(client, registry_id: str, record_id: str) -> str:
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    status = "UNKNOWN"
    while time.time() < deadline:
        status = client.get_registry_record(
            registryId=registry_id, recordId=record_id
        )["status"]
        if status in TERMINAL_STATUSES:
            return status
        # nosemgrep: arbitrary-sleep — bounded status wait
        time.sleep(POLL_INTERVAL_SECONDS)
    log(f"WARNING: record {record_id} still {status} after {POLL_TIMEOUT_SECONDS}s")
    return status


def approve(client, registry_id: str, record_id: str, status: str) -> str:
    if status == "APPROVED":
        return status
    if status == "DRAFT":
        client.submit_registry_record_for_approval(
            registryId=registry_id, recordId=record_id
        )
        status = "PENDING_APPROVAL"
    if status == "PENDING_APPROVAL":
        client.update_registry_record_status(
            registryId=registry_id,
            recordId=record_id,
            status="APPROVED",
            statusReason="Approved for the OAuth auto-populate (sync) POC.",
        )
        return "APPROVED"
    return status


def report_discovered_tools(client, registry_id: str, record_id: str) -> None:
    """Read the record back and report how many tools the Registry auto-discovered."""
    record = client.get_registry_record(registryId=registry_id, recordId=record_id)
    mcp = (record.get("descriptors") or {}).get("mcp") or {}
    tools_raw = (mcp.get("tools") or {}).get("inlineContent")
    if not tools_raw:
        log("no tools populated yet (sync may still be running; re-check shortly)")
        return
    try:
        tools = json.loads(tools_raw).get("tools", [])
    except (json.JSONDecodeError, AttributeError):
        log("tools descriptor present but not parseable as expected")
        return
    names = ", ".join(t.get("name", "?") for t in tools)
    log(f"auto-discovered {len(tools)} tool(s) from the endpoint: {names}")


def delete(client, registry_id: str) -> int:
    prior = existing(client, registry_id)
    if not prior:
        log(f"{RECORD_NAME}: not present, nothing to delete")
        return 0
    client.delete_registry_record(registryId=registry_id, recordId=prior["recordId"])
    log(f"{RECORD_NAME}: deleted ({prior['recordId']})")
    return 0


def seed(client, registry_id: str, spec: dict) -> int:
    prior = existing(client, registry_id)
    if prior:
        record_id = prior["recordId"]
        log(f"{RECORD_NAME}: updating existing record {record_id}")
        client.update_registry_record(
            registryId=registry_id,
            recordId=record_id,
            synchronizationType=spec["synchronizationType"],
            synchronizationConfiguration=spec["synchronizationConfiguration"],
        )
    else:
        log(f"{RECORD_NAME}: creating URL-synchronized MCP record")
        resp = client.create_registry_record(registryId=registry_id, **spec)
        record_id = resp["recordArn"].rsplit("/", 1)[-1]

    status = wait_for_terminal(client, registry_id, record_id)
    if status in ("CREATE_FAILED", "UPDATE_FAILED"):
        raise RuntimeError(f"Record {RECORD_NAME} reached {status}")
    approve(client, registry_id, record_id, status)
    log(f"{RECORD_NAME}: APPROVED")
    report_discovered_tools(client, registry_id, record_id)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-id", required=True)
    parser.add_argument("--mcp-url")
    parser.add_argument("--provider-arn")
    parser.add_argument("--scope", default="kyc-agent/invoke")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args()

    client = boto3.client("bedrock-agentcore-control", region_name=args.region)
    try:
        if args.delete:
            return delete(client, args.registry_id)
        if not args.mcp_url or not args.provider_arn:
            parser.error("--mcp-url and --provider-arn are required to seed")
        spec = build_spec(args.mcp_url, args.provider_arn, args.scope)
        return seed(client, args.registry_id, spec)
    except (ClientError, RuntimeError) as exc:
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
