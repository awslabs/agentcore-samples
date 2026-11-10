#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Seed (or delete) the OAuth-invocable agent's A2A registry record.

The record describes the JWT-authorized twin of the KYC orchestrator and — unlike
the records in seed_registry.py — advertises an OAuth2 client-credentials security
scheme in its agent card. A consumer that discovers this record therefore learns
both the endpoint URL and that it must present an OAuth bearer token to call it.

Registry records have no Terraform resource (see docs/preview-api-notes.md), so
Terraform invokes this script during apply. It is idempotent: an existing record
with the same name is updated rather than duplicated. `--delete` removes it, used
by the destroy provisioner so the registry can be torn down.

The small upsert/approve helpers here are deliberately replicated from
seed_registry.py rather than imported: that module imports the agent packages at
module load, and this seeding step must not depend on them.
"""

import argparse
import json
import sys
import time
import urllib.parse

import boto3
from botocore.exceptions import ClientError

RECORD_NAME = "kyc-orchestrator-oauth"
SCOPE = "kyc-agent/invoke"

TERMINAL_STATUSES = {
    "DRAFT",
    "APPROVED",
    "PENDING_APPROVAL",
    "REJECTED",
    "DEPRECATED",
    "CREATE_FAILED",
    "UPDATE_FAILED",
}
POLL_TIMEOUT_SECONDS = 90
POLL_INTERVAL_SECONDS = 3


def log(message: str) -> None:
    print(f"[oauth-record] {message}", flush=True)


def invocation_url(region: str, runtime_arn: str) -> str:
    """Build the runtime's HTTPS invocations URL with the ARN URL-encoded.

    The ARN contains ':' and '/', so it must be percent-encoded to sit inside the
    path. qualifier=DEFAULT targets the default endpoint.
    """
    encoded = urllib.parse.quote(runtime_arn, safe="")
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com"
        f"/runtimes/{encoded}/invocations?qualifier=DEFAULT"
    )


def build_record(region: str, runtime_arn: str, token_url: str) -> dict:
    """Describe the OAuth-invocable KYC orchestrator as an A2A record.

    The agent card carries an OAuth2 clientCredentials securityScheme plus a
    matching `security` requirement, so discovery reveals how to authenticate.
    """
    agent_card = {
        "protocolVersion": "0.3.0",
        "name": "KYC Onboarding Risk Assessor (OAuth)",
        "description": (
            "OAuth-invocable corporate KYC onboarding assessment. Same multi-agent "
            "credit-risk and AML/sanctions screening as the SigV4 orchestrator, but "
            "called directly with an OAuth 2.0 bearer token."
        ),
        "version": "1.0.0",
        "url": invocation_url(region, runtime_arn),
        "preferredTransport": "JSONRPC",
        "provider": {
            "organization": "Financial Services Risk Platform",
            "url": "https://aws.amazon.com/bedrock/agentcore/",
        },
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "securitySchemes": {
            "oauth2": {
                "type": "oauth2",
                "description": "Machine-to-machine OAuth 2.0 client-credentials.",
                "flows": {
                    "clientCredentials": {
                        "tokenUrl": token_url,
                        "scopes": {SCOPE: "Invoke the KYC agent runtime"},
                    }
                },
            }
        },
        "security": [{"oauth2": [SCOPE]}],
        "skills": [
            {
                "id": "kyc-onboarding-assessment",
                "name": "KYC Onboarding Risk Assessment",
                "description": (
                    "Assess a prospective corporate banking customer and return a "
                    "risk score with an onboarding recommendation."
                ),
                "tags": ["kyc", "aml", "credit-risk", "onboarding", "compliance", "oauth"],
                "examples": [
                    "Assess CUST001 for corporate onboarding",
                    "Run a compliance-only review of CUST003",
                ],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            }
        ],
    }

    return {
        "name": RECORD_NAME,
        "description": (
            "A2A agent card for the OAuth-invocable KYC onboarding risk assessor. "
            "Discover it, then call the agent directly with an OAuth bearer token."
        ),
        "descriptorType": "A2A",
        "descriptors": {"a2a": {"agentCard": {"inlineContent": json.dumps(agent_card)}}},
    }


def existing_records(client, registry_id: str) -> dict[str, dict]:
    """Map record name -> record summary for records already in the registry."""
    records: dict[str, dict] = {}
    token = None
    while True:
        kwargs = {"registryId": registry_id, "maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        response = client.list_registry_records(**kwargs)
        for record in response.get("registryRecords", []):
            records[record["name"]] = record
        token = response.get("nextToken")
        if not token:
            break
    return records


def wait_for_terminal(client, registry_id: str, record_id: str) -> str:
    """Poll a record until it leaves CREATING/UPDATING."""
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    status = "UNKNOWN"
    while time.time() < deadline:
        status = client.get_registry_record(
            registryId=registry_id, recordId=record_id
        )["status"]
        if status in TERMINAL_STATUSES:
            return status
        # nosemgrep: arbitrary-sleep — poll interval inside a bounded status wait
        time.sleep(POLL_INTERVAL_SECONDS)
    log(f"WARNING: record {record_id} still {status} after {POLL_TIMEOUT_SECONDS}s")
    return status


def approve(client, registry_id: str, record_id: str, status: str) -> str:
    """Drive a record through DRAFT -> PENDING_APPROVAL -> APPROVED.

    Only APPROVED records are discoverable via search, which the demo needs.
    """
    if status == "APPROVED":
        log(f"  {RECORD_NAME}: already APPROVED")
        return status
    if status == "DRAFT":
        client.submit_registry_record_for_approval(
            registryId=registry_id, recordId=record_id
        )
        log(f"  {RECORD_NAME}: DRAFT -> PENDING_APPROVAL")
        status = "PENDING_APPROVAL"
    if status == "PENDING_APPROVAL":
        client.update_registry_record_status(
            registryId=registry_id,
            recordId=record_id,
            status="APPROVED",
            statusReason="Approved for the OAuth discover-then-invoke POC.",
        )
        log(f"  {RECORD_NAME}: PENDING_APPROVAL -> APPROVED")
        return "APPROVED"
    log(f"  {RECORD_NAME}: left in {status} (no automatic transition)")
    return status


def delete(client, registry_id: str) -> int:
    """Delete the OAuth record if present. Best-effort (used on destroy)."""
    existing = existing_records(client, registry_id).get(RECORD_NAME)
    if not existing:
        log(f"{RECORD_NAME}: not present, nothing to delete")
        return 0
    client.delete_registry_record(
        registryId=registry_id, recordId=existing["recordId"]
    )
    log(f"{RECORD_NAME}: deleted ({existing['recordId']})")
    return 0


def seed(client, registry_id: str, spec: dict) -> int:
    """Create or update the record, then walk it to APPROVED."""
    prior = existing_records(client, registry_id).get(RECORD_NAME)
    if prior:
        record_id = prior["recordId"]
        log(f"{RECORD_NAME}: updating existing record {record_id}")
        # A2A does not wrap agentCard in an optionalValue envelope (see
        # seed_registry.py's wrap_optional note), so update passes it directly.
        client.update_registry_record(
            registryId=registry_id,
            recordId=record_id,
            description=spec["description"],
            descriptors=spec["descriptors"],
        )
    else:
        log(f"{RECORD_NAME}: creating A2A record")
        response = client.create_registry_record(registryId=registry_id, **spec)
        record_id = response["recordArn"].rsplit("/", 1)[-1]

    status = wait_for_terminal(client, registry_id, record_id)
    if status in ("CREATE_FAILED", "UPDATE_FAILED"):
        raise RuntimeError(f"Record {RECORD_NAME} reached {status}")
    approve(client, registry_id, record_id, status)
    log("done — record is APPROVED and discoverable via SearchRegistryRecords")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-id", required=True)
    parser.add_argument("--twin-runtime-arn")
    parser.add_argument("--token-url")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--delete", action="store_true", help="Delete the record")
    args = parser.parse_args()

    client = boto3.client("bedrock-agentcore-control", region_name=args.region)

    try:
        if args.delete:
            return delete(client, args.registry_id)
        if not args.twin_runtime_arn or not args.token_url:
            parser.error("--twin-runtime-arn and --token-url are required to seed")
        spec = build_record(args.region, args.twin_runtime_arn, args.token_url)
        return seed(client, args.registry_id, spec)
    except (ClientError, RuntimeError) as exc:
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
