#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Create or update an AgentCore Gateway inference target.

The Terraform AWS provider (v6.x) knows only `http` and `mcp` under a
gateway target's `target_configuration`; the newer `inference` block is
supported by the service and by boto3 but not by the provider yet. This
script fills the gap in the same shape as scripts/seed_registry.py — it
is invoked by a null_resource in infra/gateway.tf and is idempotent so
that repeated `terraform apply` runs converge instead of erroring.

The target this POC creates is a **Bedrock Mantle connector** — a
zero-configuration hook that fronts Amazon Bedrock foundation models
through the same gateway that already fronts our tools. The gateway's
IAM role does the InvokeModel call; the caller only needs InvokeGateway.
That is the demo's punch line: one governed endpoint, one guardrail,
one audit trail, for both tools and models.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

TARGET_NAME = "bedrock-mantle"
CONNECTOR_ID = "bedrock-mantle"


def _client(region: str):
    return boto3.client("bedrock-agentcore-control", region_name=region)


def _find_target(client, gateway_id: str, name: str) -> dict[str, Any] | None:
    """Return the existing target with this name, or None.

    ListGatewayTargets pages; the connector target we manage is at most one
    per gateway so a linear scan is fine.
    """
    paginator = client.get_paginator("list_gateway_targets")
    for page in paginator.paginate(gatewayIdentifier=gateway_id):
        for target in page.get("items", []):
            if target.get("name") == name:
                return target
    return None


def _wait_until_gone(
    client, gateway_id: str, target_id: str, attempts: int = 30
) -> None:
    """Block until `target_id` no longer appears on the gateway.

    Creating a target with the same name while the old one is still
    deleting fails on the name-uniqueness constraint, so recreation has to
    wait out the delete.
    """
    for _ in range(attempts):
        if not any(
            t.get("targetId") == target_id
            for page in client.get_paginator("list_gateway_targets").paginate(
                gatewayIdentifier=gateway_id
            )
            for t in page.get("items", [])
        ):
            return
        # nosemgrep: arbitrary-sleep — poll interval inside a bounded delete-completion wait
        time.sleep(2)
    print(
        f"[inference-target] warning: {target_id} still present after "
        f"{attempts * 2}s; attempting create anyway",
        file=sys.stderr,
    )


def _configuration(guardrail_arn: str | None, guardrail_version: str | None) -> dict[str, Any]:
    """Build the create/update payload.

    Just the connector id and the IAM outbound credential provider.

    Note on guardrails: `targetConfiguration.inference` is a tagged union
    that only accepts `connector` or `provider`. Guardrails are attached
    to the *gateway* through the Policy Engine (Cedar) rather than at the
    target — a Bedrock Guardrail ARN is referenced inside a Cedar policy
    that the Policy Engine enforces. This POC creates the guardrail
    resource so the plumbing is real, then documents Policy-Engine wiring
    as the next step; the guardrail_arn/guardrail_version arguments to
    this script are kept as trigger inputs so the script re-runs when the
    guardrail changes, but they are not passed to the API.
    """
    _ = (guardrail_arn, guardrail_version)  # reserved for Policy Engine wiring
    return {
        "inference": {
            "connector": {
                "source": {"connectorId": CONNECTOR_ID},
            }
        }
    }


def _upsert(
    region: str,
    gateway_id: str,
    guardrail_arn: str | None,
    guardrail_version: str | None,
) -> dict[str, Any]:
    client = _client(region)
    existing = _find_target(client, gateway_id, TARGET_NAME)
    payload_config = _configuration(guardrail_arn, guardrail_version)

    body = {
        "gatewayIdentifier": gateway_id,
        "name": TARGET_NAME,
        "description": (
            "Bedrock Mantle inference connector — fronts Bedrock foundation "
            "models through the same gateway as the KYC tools."
        ),
        "targetConfiguration": payload_config,
        "credentialProviderConfigurations": [
            {"credentialProviderType": "GATEWAY_IAM_ROLE"}
        ],
    }

    # A FAILED target does not recover on update. The connector runs model
    # discovery once, at creation; if the gateway role could not call
    # bedrock-mantle:ListModels at that moment the target is permanently
    # FAILED. Delete and recreate so an IAM fix actually takes effect.
    if existing and existing.get("status") == "FAILED":
        print(
            f"[inference-target] existing target {existing['targetId']} is FAILED; "
            "deleting so it can be recreated with current permissions",
            file=sys.stderr,
        )
        client.delete_gateway_target(
            gatewayIdentifier=gateway_id, targetId=existing["targetId"]
        )
        _wait_until_gone(client, gateway_id, existing["targetId"])
        existing = None

    # A target caught mid-delete rejects updates ("can't be performed on target
    # when it is in Deleting state"). That happens when a prior apply replaced
    # this null_resource and its destroy provisioner is still in flight, so wait
    # the delete out and create fresh rather than failing the apply.
    if existing and existing.get("status") == "Deleting":
        print(
            f"[inference-target] {existing['targetId']} is mid-delete; waiting",
            file=sys.stderr,
        )
        _wait_until_gone(client, gateway_id, existing["targetId"])
        existing = None

    if existing:
        target_id = existing["targetId"]
        print(
            f"[inference-target] updating existing target {target_id}",
            file=sys.stderr,
        )
        try:
            return client.update_gateway_target(
                gatewayIdentifier=gateway_id,
                targetId=target_id,
                **{k: v for k, v in body.items() if k != "gatewayIdentifier"},
            )
        except ClientError as exc:
            # ListGatewayTargets can lag a concurrent delete — notably the
            # destroy provisioner of a tainted null_resource, which runs just
            # before this create. If the target vanished under us, create.
            if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                raise
            print(
                f"[inference-target] {target_id} disappeared before update; creating",
                file=sys.stderr,
            )

    print("[inference-target] creating new target", file=sys.stderr)
    return client.create_gateway_target(**body)


def _delete(region: str, gateway_id: str) -> None:
    client = _client(region)
    existing = _find_target(client, gateway_id, TARGET_NAME)
    if not existing:
        print("[inference-target] nothing to delete", file=sys.stderr)
        return
    client.delete_gateway_target(
        gatewayIdentifier=gateway_id, targetId=existing["targetId"]
    )
    print(f"[inference-target] deleted {existing['targetId']}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--gateway-id", required=True)
    parser.add_argument("--guardrail-arn", default=None)
    parser.add_argument("--guardrail-version", default=None)
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the target (invoked by the destroy provisioner).",
    )
    args = parser.parse_args()

    if args.delete:
        _delete(args.region, args.gateway_id)
        return 0

    response = _upsert(
        region=args.region,
        gateway_id=args.gateway_id,
        guardrail_arn=args.guardrail_arn,
        guardrail_version=args.guardrail_version,
    )
    # Print a compact summary for the terraform log. targetId is the useful
    # bit — every subsequent operation keys off it.
    summary = {
        "targetId": response.get("targetId"),
        "status": response.get("status"),
        "name": response.get("name"),
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
