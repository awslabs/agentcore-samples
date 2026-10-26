# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""U4 publisher-approval. Drives the peer's registry record through its full
create -> submit -> approve lifecycle.

Submitter and approver run under separate roles (separation of duties), and the
sequence fails fast.

The control-plane contract below matches the agent-registry-control client in
boto3 1.43.116:

  - create_registry_record: required {registryId, name, recordType,
    descriptors}. The response is {recordArn, status} with no recordId.
    recordId is derived via U0's record_id_from_arn() (imported, not
    reimplemented).
  - submit_registry_record_for_approval: {registryId, recordId}.
  - update_registry_record_status: four required params {registryId,
    recordId, status, statusReason}. statusReason (0-255 chars) is never
    omitted. status="APPROVED" fires the
    EventBridge "Registry Record State changed to Approved" event U5 consumes.

Separation of duties. The create+submit steps run under an
agent-registry-control client built from RegistryPublisherSubmitterRole's
assumed credentials. The approve step runs under a separate client built from
RegistryPublisherApproverRole's credentials. No single session holds both
capabilities, because there are two assumed-role client sessions. U1's CDK
constructs define these roles. U4 consumes them (sts:AssumeRole on each ARN)
and does not create or redefine them.

Fail-fast, following U2's register_orchestrator.py. There is no try/except
around the three control-plane calls, so a boto3 ClientError propagates
uncaught for a clean non-zero exit. There is no auto-retry beyond the transient
lifecycle wait. recordId is derived once after create and reused.
recordArn+recordId are logged before submit so a partial failure leaves a
discoverable trail. A re-run is safe but not idempotent (it creates a new
record, and teardown (U7) cleans up). clientToken is an unused idempotency
extension point.

All control-plane calls use agent-registry-control (boto3 >= 1.43.116), never
the preview bedrock-agentcore namespace.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

import boto3
from botocore.exceptions import ClientError

from agent_registry_sample.publisher.card_source import load_peer_card_json
from agent_registry_sample.sdk_reconciliation.reconciliation import record_id_from_arn

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_CONTROL_PLANE = "agent-registry-control"

# create_registry_record returns status=CREATING and the record settles to DRAFT
# asynchronously. Submit (and the later approve) fail with ConflictException
# "cannot be modified while in CREATING state" until then (UPDATING is the
# analogous transient after submit). These are the only two transient lifecycle
# states we wait out. Everything else fails fast.
_TRANSIENT_STATES = ("CREATING", "UPDATING")
_SETTLE_MAX_ATTEMPTS = 10
_SETTLE_BACKOFF_SECONDS = 2.0  # linear: ~2,4,6,... capped; ample for a demo record


def _is_transient_state_conflict(exc: ClientError) -> bool:
    """True only for the async-lifecycle ConflictException raised while the
    record is in a transient (CREATING/UPDATING) state. Any other
    ConflictException (for example 'already exists') or any other error is not
    transient and must propagate (fail-fast)."""
    err = exc.response.get("Error", {})
    if err.get("Code") != "ConflictException":
        return False
    message = err.get("Message", "")
    return any(state in message for state in _TRANSIENT_STATES)


def _retry_on_transient_state(operation, *, what: str):
    """Call ``operation`` (a zero-arg control-plane call), retrying only the
    transient-state ConflictException with bounded linear backoff. This is a
    narrow exception to the publisher's fail-fast stance. It waits out the
    known async create->DRAFT / submit->settled transition, and nothing else.
    A non-transient error raises on the first try. Exhausting the budget
    re-raises the last transient conflict, so a stuck record fails instead of
    hanging."""
    last_exc: ClientError | None = None
    for attempt in range(1, _SETTLE_MAX_ATTEMPTS + 1):
        try:
            return operation()
        except ClientError as exc:
            if not _is_transient_state_conflict(exc):
                raise  # fail-fast: not the transient state, propagate uncaught
            last_exc = exc
            if attempt == _SETTLE_MAX_ATTEMPTS:
                break
            wait = _SETTLE_BACKOFF_SECONDS * attempt
            _log("await_settle", operation=what, attempt=attempt, waitSeconds=wait)
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def _log(step: str, **fields: object) -> None:
    """One structured JSON log line, matching U1/U2's _log() convention.
    Non-secret identifiers only (recordArn, recordId, status, statusReason),
    never credentials."""
    logger.info(json.dumps({"step": step, **fields}))


def _assume_role_client(role_arn: str, region: str, session_name: str):
    """Build an agent-registry-control client whose credentials come from
    sts:AssumeRole on ``role_arn``.

    Each role gets its own client session. The submitter's session cannot
    approve (its role lacks UpdateRegistryRecordStatus), and the approver's
    session cannot create or submit (its role lacks
    CreateRegistryRecord/SubmitRegistryRecordForApproval). IAM on the assumed
    role enforces the boundary, not this script's call ordering.
    """
    sts = boto3.client("sts", region_name=region)
    creds = sts.assume_role(RoleArn=role_arn, RoleSessionName=session_name)["Credentials"]
    return boto3.client(
        _CONTROL_PLANE,
        region_name=region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def create_and_submit(submitter_client, registry_id: str, name: str, card_json: str) -> str:
    """Create the peer's registry record and submit it for approval, under
    the submitter role's client. Returns the derived recordId.

    No try/except (fail-fast), so a ClientError propagates uncaught.
    """
    # All four required inputs. descriptors carries the peer's A2A card JSON
    # (U3's shape) at a2aAgentCard.data, dataSchemaVersion "0.3".
    response = submitter_client.create_registry_record(
        registryId=registry_id,
        name=name,
        recordType="AGENT",
        descriptors={"a2aAgentCard": {"data": card_json, "dataSchemaVersion": "0.3"}},
    )
    # The response has {recordArn, status} and no recordId. Derive it once via
    # U0's record_id_from_arn() and reuse it for submit + approve. clientToken
    # (an optional idempotency key) is unused.
    record_arn = response["recordArn"]
    record_id = record_id_from_arn(record_arn)
    # Log recordArn + recordId before submit so a partial failure (submit or
    # approve fails) leaves a discoverable trail of the lingering record.
    _log("created", recordArn=record_arn, recordId=record_id, status=response.get("status"))

    # The record is briefly in CREATING right after create. Wait out that
    # transient state, and only that, before submitting.
    _retry_on_transient_state(
        lambda: submitter_client.submit_registry_record_for_approval(
            registryId=registry_id, recordId=record_id
        ),
        what="submit",
    )
    _log("submitted", recordId=record_id, status="PENDING_APPROVAL")
    return record_id


def approve(approver_client, registry_id: str, record_id: str, status_reason: str) -> None:
    """Approve the record under the separate approver role's client.

    Sends all four required params, and statusReason is never omitted (0-255
    chars). status="APPROVED" fires the EventBridge Approved event U5 consumes.
    The approver client's role cannot create or submit, only transition.
    """
    if not status_reason or len(status_reason) > 255:
        # statusReason is required (0-255 chars). This guard stops a caller from
        # passing an empty or oversized reason. The AWS API also enforces it, so
        # this is a second check, not the only one.
        raise ValueError("statusReason is required and must be 1-255 chars.")
    # A just-submitted record may briefly be in UPDATING. Wait out that
    # transient state, and only that, before approving.
    _retry_on_transient_state(
        lambda: approver_client.update_registry_record_status(
            registryId=registry_id,
            recordId=record_id,
            status="APPROVED",
            statusReason=status_reason,
        ),
        what="approve",
    )
    _log("approved", recordId=record_id, status="APPROVED", statusReason=status_reason)


def publish_and_approve(
    registry_id: str,
    name: str,
    card_json: str,
    submitter_role_arn: str,
    approver_role_arn: str,
    status_reason: str,
    region: str,
) -> str:
    """Full create -> submit (submitter role) -> approve (approver role) flow.

    Uses two distinct assumed-role clients, one per half. Returns the derived
    recordId.
    """
    submitter_client = _assume_role_client(submitter_role_arn, region, "u4-submitter")
    record_id = create_and_submit(submitter_client, registry_id, name, card_json)

    # Separate assumed-role session for approve. The submitter session is not
    # reused, and could not approve anyway because its role lacks the action.
    approver_client = _assume_role_client(approver_role_arn, region, "u4-approver")
    approve(approver_client, registry_id, record_id, status_reason)
    return record_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-id", required=True, help="Target registry id or ARN.")
    parser.add_argument("--name", default="peer-greeter", help="The peer record's name.")
    parser.add_argument(
        "--submitter-role-arn",
        required=True,
        help="RegistryPublisherSubmitterRole ARN (U1 CfnOutput PublisherSubmitterRoleArn).",
    )
    parser.add_argument(
        "--approver-role-arn",
        required=True,
        help="RegistryPublisherApproverRole ARN (U1 CfnOutput PublisherApproverRoleArn).",
    )
    parser.add_argument(
        "--peer-card-url",
        default=None,
        help="The peer's deployed invocations endpoint URL, used as the card's url.",
    )
    parser.add_argument(
        "--peer-card-file",
        default=None,
        help="Local-dev fallback: a static card JSON file (only when no live peer is deployed).",
    )
    parser.add_argument(
        "--status-reason",
        default="Approved by the arrival-signal sample walkthrough publisher.",
        help="Required free-text statusReason (0-255 chars) for the APPROVED transition.",
    )
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    # Build the peer's card for its deployed endpoint. A static file is the
    # local-dev fallback only.
    card_json = load_peer_card_json(url=args.peer_card_url, file_path=args.peer_card_file)

    # No try/except (fail-fast). Any failure propagates for a clear non-zero exit.
    record_id = publish_and_approve(
        registry_id=args.registry_id,
        name=args.name,
        card_json=card_json,
        submitter_role_arn=args.submitter_role_arn,
        approver_role_arn=args.approver_role_arn,
        status_reason=args.status_reason,
        region=args.region,
    )
    print(f"Peer record published and APPROVED. recordId={record_id} (Approved event fired).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
