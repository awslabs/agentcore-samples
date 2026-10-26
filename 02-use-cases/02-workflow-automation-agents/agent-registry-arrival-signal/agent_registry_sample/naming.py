# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic resource naming shared across the infra stack and the
walkthrough.

The orchestrator AgentCore Runtime deploys before the infra stack creates the
idempotency table, so the orchestrator cannot reference the table's
auto-generated name at its own deploy time (a circular dependency). Both sides
instead derive the same name from the registry id, which each holds at its own
deploy time. The infra stack names the table this way, and
the walkthrough sets the same value as the orchestrator's
IDEMPOTENCY_TABLE_NAME env var. No cross-stack reference, no ordering problem.
"""

from __future__ import annotations

_PREFIX = "arrival-signal-idempotency-"


def registry_id_from_arn(registry_arn: str) -> str:
    """The registry id is the ARN's trailing path segment
    (arn:aws:agent-registry:<region>:<acct>:registry/<registryId>)."""
    return registry_arn.rstrip("/").split("/")[-1]


def idempotency_table_name(registry_id: str) -> str:
    """The DynamoDB idempotency table name for a given registry id.

    DynamoDB table names allow [a-zA-Z0-9_.-], 3-255 chars. A registry id is
    alphanumeric, so the prefixed name is always valid.
    """
    return f"{_PREFIX}{registry_id}"
