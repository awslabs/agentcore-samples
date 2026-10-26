# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""U2's post-deploy, one-shot orchestrator registration script.

Registers the orchestrator in the Agent Registry's control plane at deploy
time, not lazily on first invocation, because the orchestrator must be
discoverable from t0. Its own record has no approval gate (unlike the peer's
record, which U4 drives through submit+approve).

Run as a separate step after a successful `agentcore deploy`, not as a CLI
deploy hook, because the @aws/agentcore CLI's `deploy` command has no
post-deploy hook.

Fail-fast, one-shot. There is no try/except around the registry call (an
exception propagates uncaught, giving a clear non-zero exit) and no retry loop.
A human retries a failed registration by re-running this script.

Reuses U0's ARN-to-recordId derivation by importing
agent_registry_sample.sdk_reconciliation.reconciliation.record_id_from_arn.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import boto3

from agent_registry_sample.sdk_reconciliation.reconciliation import (
    find_record_id_by_name,
    record_id_from_arn,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _log(step: str, **fields: object) -> None:
    logger.info(json.dumps({"step": step, **fields}))


def build_orchestrator_card(a2a_endpoint_url: str) -> str:
    """Build the orchestrator's own A2A 0.3 agent card (JSON string).

    Minimal, matching the hello_world-only skill surface. The orchestrator's
    on_arrival_event and search_and_invoke tools are not advertised as A2A
    skills. They are internal AgentCore Runtime tools invoked by U1's adapter
    and U6's search path, not skills a peer discovers and calls over A2A.
    """
    # Build via the a2a-sdk AgentCard model so the serialized card is schema-0.3
    # compliant and camelCase-aliased the same way the peer's card is
    # (peer.card.build_peer_card). The live service rejects a card missing the
    # required top-level name/description/version fields, or one using
    # protocolVersion "0.3" instead of "0.3.0", at create_registry_record with
    # "content is not in compliance with schema version '0.3' for descriptor
    # type 'a2a'". model_dump_json(by_alias=True, exclude_none=True) produces the
    # JSON the registry validates.
    from a2a.types import AgentCapabilities, AgentCard, AgentSkill

    card = AgentCard(
        name="orchestrator",
        description=(
            "Standing orchestrator that reacts to newly approved agents and invokes them over A2A."
        ),
        url=a2a_endpoint_url,
        version="0.1.0",
        protocol_version="0.3.0",
        capabilities=AgentCapabilities(),
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=[
            AgentSkill(
                id="hello-world",
                name="hello-world",
                description="Returns the literal string 'hello world'.",
                tags=["demo"],
            )
        ],
    )
    return card.model_dump_json(exclude_none=True, by_alias=True)


def register_orchestrator(
    control_client,
    registry_id: str,
    a2a_endpoint_url: str,
) -> str:
    """Register the orchestrator and return the derived recordId.

    Idempotent for re-runs. The orchestrator is a standing agent, so
    registration is satisfied if its record already exists, and a re-run reuses
    the existing 'orchestrator' record instead of failing on the name
    collision. The peer differs because its fresh DRAFT->APPROVED transition is
    the event under test, so it must be deleted and re-published. The
    orchestrator's own record is never approved and never the discovery target,
    so its identity is stable across runs and the existing record is the right
    one.

    Only the specific "already exists" ConflictException is absorbed (into a
    name lookup + reuse). Every other exception propagates uncaught (a clear,
    non-zero-exit failure signal), so the fail-fast contract still holds.
    """
    card_json = build_orchestrator_card(a2a_endpoint_url)
    # botocore's ClientError is the base for modeled service errors (the live
    # service raises ConflictException as a ClientError with Error.Code
    # "ConflictException"). Matching on the error code rather than
    # control_client.exceptions.ConflictException avoids depending on the client
    # object modeling an .exceptions attribute. Any non-matching error
    # (including a non-ClientError) propagates uncaught.
    from botocore.exceptions import ClientError

    try:
        response = control_client.create_registry_record(
            registryId=registry_id,
            name="orchestrator",
            recordType="AGENT",
            descriptors={"a2aAgentCard": {"data": card_json, "dataSchemaVersion": "0.3"}},
        )
    except ClientError as exc:
        # Re-run, so the standing 'orchestrator' record already exists. Reuse it.
        # Only the name-collision ConflictException is absorbed. Any other code
        # (or a conflict that is not "already exists") re-raises.
        code = exc.response.get("Error", {}).get("Code")
        if code != "ConflictException" or "already exists" not in str(exc):
            raise
        existing_id = find_record_id_by_name(control_client, registry_id, "orchestrator")
        if existing_id is None:
            # The create said "already exists" but the list did not return it.
            # That is an inconsistency, so fail loudly rather than guess.
            raise
        _log("registration_reused", registryId=registry_id, recordId=existing_id)
        return existing_id
    _log(
        "registration_attempted",
        registryId=registry_id,
        recordArn=response.get("recordArn"),
        status=response.get("status"),
    )
    # The response does not expose recordId directly, so derive it with U0's
    # record_id_from_arn().
    record_id = record_id_from_arn(response["recordArn"])
    _log("registration_result", success=True, recordId=record_id)
    return record_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-id", required=True, help="The target registry's ID or ARN.")
    parser.add_argument(
        "--a2a-endpoint-url",
        required=True,
        help="The deployed orchestrator's A2A endpoint URL (from `agentcore status`/deploy).",
    )
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    control_client = boto3.client("agent-registry-control", region_name=args.region)
    # No try/except around this call. A failure propagates uncaught, giving a
    # non-zero exit code and traceback as the error.
    record_id = register_orchestrator(control_client, args.registry_id, args.a2a_endpoint_url)
    print(f"Orchestrator registered. recordId={record_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
