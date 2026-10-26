# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""U5 consumer-event-path. The orchestrator's event-consumption path (the
behavior behind the on_arrival_event tool).

Implements two-phase dedupe and cross-runtime failure propagation. The
orchestrator's scoped role grants are attached by the infrastructure stack.

Step sequence:
  1. two-phase dedupe claim (Phase 1) on U1's (recordId, state) table.
  2. batch_get the approved record back (agent-registry data plane).
  3. card parse + account check + A2A greet via the shared
     verify_and_invoke helper (which uses U0's assert_card_access).
  4. two-phase dedupe complete (Phase 2), only after the A2A handoff
     succeeds.
  5. return the greet result. A failure before step 4 propagates uncaught so
     U1's adapter raises (SQS retry -> DLQ).

Input contract is U1's adapter payload
{"registryRecordId": <id>, "registryId": <id>}. registryRecordId is the
record's recordId (matches U1's handler.py).
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger()

# The record state the dedupe keys on (the Approved transition that fired
# the event).
_APPROVED_STATE = "APPROVED"
# Short TTL (seconds) for a PENDING claim so a crashed/failed claim expires
# and the (recordId, state) becomes re-claimable (guarantee (b)).
_PENDING_TTL_SECONDS = 300
# Longer retention TTL for a COMPLETED marker (bounds the idempotency ledger).
_COMPLETED_TTL_SECONDS = 7 * 24 * 3600


def _log(step: str, record_id: str | None = None, **fields: object) -> None:
    """recordId-correlated structured JSON log line, matching
    U1/U2's convention. A failure logs its fail-closed reason before raising."""
    logger.info(json.dumps({"step": step, "recordId": record_id, **fields}))


class DedupeSkip(Exception):
    """Internal signal that this (recordId, state) is a redelivery of a
    COMPLETED arrival -> idempotent skip (not an error)."""


def _claim(ddb_table, record_id: str, now: int) -> str:
    """Phase 1. Conditional put of a PENDING claim with a short TTL.

    Returns "claimed" if this invocation won the claim, "completed" if the
    (recordId, state) is already COMPLETED (redelivery -> skip), or
    "reclaimable" if a prior PENDING claim has expired (re-claimable).

    The condition attribute_not_exists(recordId) lets one
    concurrent claimant win. On a condition failure, inspect the existing
    item's status to distinguish COMPLETED (skip) from PENDING.
    """
    try:
        ddb_table.put_item(
            Item={
                "recordId": record_id,
                "state": _APPROVED_STATE,
                "status": "PENDING",
                "expiresAt": now + _PENDING_TTL_SECONDS,
            },
            ConditionExpression="attribute_not_exists(recordId)",
        )
        return "claimed"
    except Exception as exc:  # noqa: BLE001 - ConditionalCheckFailedException et al.
        name_and_msg = type(exc).__name__ + " " + str(exc)
        if "ConditionalCheckFailed" not in name_and_msg:
            raise  # other DynamoDB errors (throttle, etc.) fail fast and surface
        existing = ddb_table.get_item(Key={"recordId": record_id, "state": _APPROVED_STATE}).get(
            "Item", {}
        )
        status = existing.get("status")
        if status == "COMPLETED":
            return "completed"  # (a) redelivery of a finished arrival -> skip
        # status == "PENDING". If its TTL has passed, the prior claim crashed and
        # is re-claimable. The short _PENDING_TTL keeps a failed handoff from
        # permanently blocking reprocessing (guarantee (b)).
        expires_at = existing.get("expiresAt", 0)
        if expires_at and now >= int(expires_at):
            # Re-claim by overwriting the expired PENDING item.
            ddb_table.put_item(
                Item={
                    "recordId": record_id,
                    "state": _APPROVED_STATE,
                    "status": "PENDING",
                    "expiresAt": now + _PENDING_TTL_SECONDS,
                }
            )
            return "reclaimed"
        return "in_flight"  # another live claim holds it, so do not double-invoke


def _complete(ddb_table, record_id: str, now: int) -> None:
    """Phase 2. Mark the (recordId, state) COMPLETED, only after the A2A
    handoff succeeded. Only a COMPLETED item causes a future redelivery to
    skip."""
    ddb_table.update_item(
        Key={"recordId": record_id, "state": _APPROVED_STATE},
        UpdateExpression="SET #s = :c, expiresAt = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":c": "COMPLETED", ":t": now + _COMPLETED_TTL_SECONDS},
    )


def _read_back(data_plane_client, registry_id: str, record_id: str) -> dict:
    """Batch_get the approved record from the agent-registry data
    plane with entries=[{registryId, recordIds:[recordId]}]. A record in the
    errors list or absent fails closed (cannot call a peer
    whose record/card couldn't be read)."""
    resp = data_plane_client.batch_get_discoverable_registry_record(
        entries=[{"registryId": registry_id, "recordIds": [record_id]}]
    )
    errors = resp.get("errors") or []
    if errors:
        raise RuntimeError(f"batch_get returned errors for recordId={record_id}: {errors}")
    records = resp.get("registryRecords") or []
    if not records:
        raise RuntimeError(f"batch_get returned no record for recordId={record_id} (fail closed)")
    return records[0]


def on_arrival_event_impl(
    payload: dict,
    *,
    ddb_table,
    data_plane_client,
    allowed_accounts: set[str],
    send_message,
    greet_name: str = "arrival-signal-sample",
    now: int | None = None,
) -> str | None:
    """The on_arrival_event behavior (U5), injected with its
    collaborators so it is testable without live AWS.

    Returns the greet result text on a successful handoff, or None on an
    idempotent redelivery skip. A failure before the complete phase
    propagates uncaught so U1's adapter raises -> SQS retry/DLQ.
    """
    now = int(time.time()) if now is None else now
    record_id = payload["registryRecordId"]  # registryRecordId is the recordId
    registry_id = payload["registryId"]

    # (1) Phase 1 claim
    claim = _claim(ddb_table, record_id, now)
    _log("dedupe_claim", record_id, result=claim)
    if claim == "completed":
        _log("skip_redelivery", record_id, reason="already COMPLETED")
        return None  # (a) no double-invoke for a completed (recordId, state)
    if claim == "in_flight":
        _log("skip_in_flight", record_id, reason="another live claim holds it")
        return None

    # (2) read back
    record = _read_back(data_plane_client, registry_id, record_id)
    _log("record_read", record_id, found=True)

    # (3) Account check + A2A greet (shared helper, fail-closed before any send).
    # No try/except here. An account-check or A2A failure propagates uncaught,
    # so Phase 2 (_complete) is never reached and the adapter raises -> SQS
    # retry/DLQ. The (recordId, state) PENDING claim expires (short TTL) and is
    # re-claimable (guarantee (b)).
    from agent_registry_sample.orchestrator.verify_and_invoke import verify_and_invoke

    result = verify_and_invoke(record, greet_name, allowed_accounts, send_message)

    # (4) Phase 2 complete, only after the A2A handoff succeeded.
    _complete(ddb_table, record_id, now)
    _log("completed", record_id, greet=result)
    return result
