#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Seed AgentCore Registry with records describing the deployed KYC system.

Registry records have no Terraform resource in the preview, so Terraform invokes
this script during apply. It is idempotent: an existing record with the same
name is updated rather than duplicated.

Creates one record per descriptor type the Registry supports, which is also what
makes the demo's catalog interesting to browse:

  MCP          kyc-gateway              the Gateway and its five KYC tools
  A2A          kyc-orchestrator         the Runtime's agent card
  AGENT_SKILLS credit-risk-analysis     the Credit Analyst specialist
  AGENT_SKILLS aml-compliance-screening the Compliance Officer specialist

Each record is then driven through the governance workflow
(DRAFT -> PENDING_APPROVAL -> APPROVED) unless the registry auto-approves.
"""

import argparse
import json
import pathlib
import sys
import time

import boto3
from botocore.exceptions import ClientError

# The AGENT_SKILLS records are built from the agent's own Skill definitions, so
# the catalog cannot describe a name or tool set the orchestrator does not use.
# backend/agent is the agent container's working directory, so its modules import
# as `agents.*`; add it to the path to import them from here.
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "backend" / "agent")
)

from agents.compliance_officer import SKILL as COMPLIANCE_SKILL  # noqa: E402
from agents.credit_analyst import SKILL as CREDIT_SKILL  # noqa: E402

# Records reach a terminal state asynchronously; CreateRegistryRecord returns 202.
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

# The MCP server descriptor's `description` is capped at 100 characters. This is
# not in the API docs and violations surface only as a generic schema-mismatch
# error, so it is asserted explicitly below.
MCP_DESCRIPTION_LIMIT = 100


def log(message: str) -> None:
    print(f"[registry] {message}", flush=True)


def build_mcp_record(gateway_url: str, tool_specs: list[dict]) -> dict:
    """Describe the KYC Gateway as an MCP server record.

    The server descriptor follows the MCP server-definition schema: identity
    fields plus a `remotes` array giving the transport and endpoint. The service
    validates this strictly — unrecognized top-level keys (url, transport,
    capabilities) are rejected with "does not match any supported version".

    The tools descriptor must be an object wrapping the array (`{"tools": [...]}`),
    not a bare array.

    The server `description` is capped at MCP_DESCRIPTION_LIMIT characters. The
    service reports an over-long description as the same generic "does not match
    any supported version" error, so keep it short — the record's own
    `description` field carries the longer prose.
    """
    server = {
        # Namespaced name, per the MCP server-registry convention.
        "name": "kyc/kyc-gateway",
        "version": "1.0.0",
        "description": "Corporate KYC data-retrieval tools over MCP (Lambda target, IAM auth)",
        "remotes": [{"type": "streamable-http", "url": gateway_url}],
    }
    assert len(server["description"]) <= MCP_DESCRIPTION_LIMIT

    tools = {
        "tools": [
            {
                "name": spec["name"],
                "description": spec["description"],
                "inputSchema": spec["inputSchema"],
            }
            for spec in tool_specs
        ]
    }

    return {
        "name": "kyc-gateway",
        "description": (
            "MCP server providing five KYC data tools: customer profile, credit "
            "bureau report, sanctions/PEP screening, transaction history, and "
            "adverse media scan."
        ),
        "descriptorType": "MCP",
        "descriptors": {
            "mcp": {
                "server": {"inlineContent": json.dumps(server)},
                "tools": {"inlineContent": json.dumps(tools)},
            }
        },
    }


def build_a2a_record(runtime_arn: str, region: str) -> dict:
    """Describe the KYC orchestrator as an A2A agent card."""
    agent_card = {
        "protocolVersion": "0.3.0",
        "name": "KYC Onboarding Risk Assessor",
        "description": (
            "Multi-agent corporate KYC onboarding assessment. Runs credit risk "
            "analysis and AML/sanctions compliance screening in parallel, then "
            "synthesizes a 0-100 risk score with an APPROVE / REJECT / ESCALATE "
            "recommendation."
        ),
        "version": "1.0.0",
        "url": (
            f"https://bedrock-agentcore.{region}.amazonaws.com"
            f"/runtimes/{runtime_arn}/invocations"
        ),
        "preferredTransport": "JSONRPC",
        "provider": {
            "organization": "Financial Services Risk Platform",
            "url": "https://aws.amazon.com/bedrock/agentcore/",
        },
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": "kyc-onboarding-assessment",
                "name": "KYC Onboarding Risk Assessment",
                "description": (
                    "Assess a prospective corporate banking customer and return a "
                    "risk score with an onboarding recommendation."
                ),
                "tags": ["kyc", "aml", "credit-risk", "onboarding", "compliance"],
                "examples": [
                    "Assess CUST001 for corporate onboarding",
                    "Run a compliance-only review of CUST003",
                    "What is the credit risk for CUST002?",
                ],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            }
        ],
    }

    return {
        "name": "kyc-orchestrator",
        "description": (
            "A2A agent card for the KYC onboarding risk assessor running on "
            "AgentCore Runtime."
        ),
        "descriptorType": "A2A",
        "descriptors": {"a2a": {"agentCard": {"inlineContent": json.dumps(agent_card)}}},
    }


def build_skill_records() -> list[dict]:
    """Describe the two specialist agents as AGENT_SKILLS records.

    Built from the Skill definitions in backend/agent/agents/, so the record
    name, its description, and the documented tool list are exactly what the
    orchestrator uses at runtime. Previously this module restated all three,
    which let the catalog drift from the system it describes.
    """
    return [skill.to_registry_record() for skill in (CREDIT_SKILL, COMPLIANCE_SKILL)]


def wrap_optional(shape, value):
    """Re-shape a Create-style payload for UpdateRegistryRecord.

    Update wraps every nullable field in an `{"optionalValue": ...}` envelope so
    a caller can distinguish "set to null" from "leave unchanged". The wrapping
    is recursive and applied inconsistently across descriptor branches — mcp
    wraps `server`/`tools`, while a2a does *not* wrap `agentCard`.

    Rather than hard-code that, walk the botocore shape and insert an envelope
    wherever the model declares one.

    Args:
        shape: The botocore shape for this position in the request.
        value: The unwrapped value, as CreateRegistryRecord would accept it.
    """
    members = getattr(shape, "members", None)
    if not members:
        return value

    # This position is an envelope: wrap, then recurse into the inner shape.
    if set(members) == {"optionalValue"}:
        return {"optionalValue": wrap_optional(members["optionalValue"], value)}

    if not isinstance(value, dict):
        return value

    return {
        key: wrap_optional(members[key], inner)
        for key, inner in value.items()
        if key in members
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
    """Poll a record until it leaves CREATING/UPDATING.

    Returns:
        The record's terminal status, or the last status seen on timeout.
    """
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    status = "UNKNOWN"
    while time.time() < deadline:
        status = client.get_registry_record(
            registryId=registry_id, recordId=record_id
        )["status"]
        if status in TERMINAL_STATUSES:
            return status
        # nosemgrep: arbitrary-sleep — poll interval inside a bounded record-status wait
        time.sleep(POLL_INTERVAL_SECONDS)
    log(f"WARNING: record {record_id} still {status} after {POLL_TIMEOUT_SECONDS}s")
    return status


def approve(client, registry_id: str, record_id: str, name: str, status: str) -> str:
    """Drive a record through the approval workflow to APPROVED.

    Only APPROVED records are discoverable by consumers, so the demo needs this
    to complete for search to return anything.
    """
    if status == "APPROVED":
        log(f"  {name}: already APPROVED")
        return status

    if status == "DRAFT":
        client.submit_registry_record_for_approval(
            registryId=registry_id, recordId=record_id
        )
        log(f"  {name}: DRAFT -> PENDING_APPROVAL")
        status = "PENDING_APPROVAL"

    if status == "PENDING_APPROVAL":
        client.update_registry_record_status(
            registryId=registry_id,
            recordId=record_id,
            status="APPROVED",
            statusReason="Approved by the FSI platform governance team for the KYC POC.",
        )
        log(f"  {name}: PENDING_APPROVAL -> APPROVED")
        return "APPROVED"

    log(f"  {name}: left in {status} (no automatic transition)")
    return status


def upsert(client, registry_id: str, spec: dict, existing: dict[str, dict]) -> None:
    """Create or update one record, then walk it to APPROVED."""
    name = spec["name"]
    prior = existing.get(name)

    if prior:
        record_id = prior["recordId"]
        log(f"{name}: updating existing record {record_id}")
        update_shape = client.meta.service_model.operation_model(
            "UpdateRegistryRecord"
        ).input_shape
        client.update_registry_record(
            registryId=registry_id,
            recordId=record_id,
            description=wrap_optional(
                update_shape.members["description"], spec["description"]
            ),
            descriptors=wrap_optional(
                update_shape.members["descriptors"], spec["descriptors"]
            ),
        )
    else:
        log(f"{name}: creating {spec['descriptorType']} record")
        response = client.create_registry_record(registryId=registry_id, **spec)
        record_id = response["recordArn"].rsplit("/", 1)[-1]

    status = wait_for_terminal(client, registry_id, record_id)
    if status in ("CREATE_FAILED", "UPDATE_FAILED"):
        raise RuntimeError(f"Record {name} reached {status}")

    approve(client, registry_id, record_id, name, status)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-id", required=True)
    parser.add_argument("--gateway-url", required=True)
    parser.add_argument("--gateway-arn", required=True)
    parser.add_argument("--runtime-arn", required=True)
    parser.add_argument("--tool-spec", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    client = boto3.client("bedrock-agentcore-control", region_name=args.region)

    with open(args.tool_spec) as handle:
        tool_specs = json.load(handle)

    specs = [
        build_mcp_record(args.gateway_url, tool_specs),
        build_a2a_record(args.runtime_arn, args.region),
        *build_skill_records(),
    ]

    log(f"seeding {len(specs)} record(s) into registry {args.registry_id}")
    existing = existing_records(client, args.registry_id)

    try:
        for spec in specs:
            upsert(client, args.registry_id, spec, existing)
    except ClientError as exc:
        log(f"ERROR: {exc}")
        return 1
    except RuntimeError as exc:
        log(f"ERROR: {exc}")
        return 1

    log("done — records are APPROVED and discoverable via SearchRegistryRecords")
    log("note: semantic search indexing takes ~30s to catch up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
