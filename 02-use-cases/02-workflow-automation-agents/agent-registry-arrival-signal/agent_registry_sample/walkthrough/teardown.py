# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""U7 teardown: the one-command destructive teardown that removes every
resource the walkthrough created, records before stack.

Order:
  1. For every created registry record (the peer's and the orchestrator's
     own self-registered record), transition it to DEPRECATED
     (update_registry_record_status, always with a statusReason), then
     delete it (delete_registry_record). Records before stack: the records
     reference nothing in the stack, but the stack's event plumbing should not
     fire on a record mid-teardown, and a record outliving its stack is an
     orphan the registry still lists.
  2. `cdk destroy` the orchestrator + peer runtime CDK apps (each runtime is
     deployed "via CDK" by `agentcore deploy`; there is no `agentcore destroy`
     verb).
  3. `cdk destroy` U1 infra.

Best-effort idempotent: each step tolerates "already gone"
(try / tolerate / continue) so a re-run converges to "everything removed"
without erroring on the parts a prior partial run already removed. Each
removal step is logged (deprecated / deleted / destroyed / already-gone).

API facts this module relies on (agent-registry-control client in boto3
1.43.116):
  - delete_registry_record exists, with required params {registryId, recordId}.
  - "DEPRECATED" is a valid value of the status enum (DRAFT, PENDING_APPROVAL,
    APPROVED, REJECTED, DEPRECATED, CREATING, UPDATING, CREATE_FAILED,
    UPDATE_FAILED).

Uses U0's record_id_from_arn() when a recordId must be derived from an ARN.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys

logger = logging.getLogger("teardown")
logging.basicConfig(level=logging.INFO, format="%(message)s")

_CONTROL_PLANE = "agent-registry-control"
_DEPRECATED = "DEPRECATED"  # valid status enum value
_TEARDOWN_STATUS_REASON = "Removed by the arrival-signal sample teardown."


def _log(step: str, **fields: object) -> None:
    logger.info(json.dumps({"step": step, **fields}))


def _is_already_gone(exc: Exception) -> bool:
    """True when a control-plane / CLI error means the resource is already
    removed (so a re-run tolerates it and converges). Matches the common
    not-found / already-in-state error names without assuming one SDK shape."""
    text = (type(exc).__name__ + " " + str(exc)).lower()
    markers = (
        "resourcenotfound",
        "notfound",
        "does not exist",
        "no such",
        "already",
        "nosuchentity",
    )
    return any(m in text for m in markers)


def deprecate_then_delete_record(control_client, registry_id: str, record_id: str) -> str:
    """Transition a record to DEPRECATED, then delete it. Best-effort
    idempotent: tolerates "already gone" at both sub-steps and continues.

    Returns the outcome marker ("removed" / "already-gone")."""
    # (1) DEPRECATED. statusReason is required.
    try:
        control_client.update_registry_record_status(
            registryId=registry_id,
            recordId=record_id,
            status=_DEPRECATED,
            statusReason=_TEARDOWN_STATUS_REASON,
        )
        _log("deprecated", recordId=record_id)
    except Exception as exc:  # noqa: BLE001 - tolerate already-gone / already-deprecated
        if not _is_already_gone(exc):
            raise
        _log("deprecate_skipped", recordId=record_id, reason="already gone / already deprecated")

    # (2) delete_registry_record {registryId, recordId}.
    try:
        control_client.delete_registry_record(registryId=registry_id, recordId=record_id)
        _log("deleted", recordId=record_id)
        return "removed"
    except Exception as exc:  # noqa: BLE001 - tolerate already-deleted
        if not _is_already_gone(exc):
            raise
        _log("delete_skipped", recordId=record_id, reason="already gone")
        return "already-gone"


def _run_tolerant(cmd: list[str], *, cwd: str | None, what: str) -> None:
    """Run a destructive CLI step (agentcore teardown / cdk destroy),
    tolerating "already gone" so a re-run converges."""
    _log("run", cmd=cmd, cwd=cwd)
    # Audited-safe: `cmd` is always a static list built in this module (e.g.
    # ["cdk","destroy","--all","--force"]), never a shell string or
    # attacker-controlled input, and shell=False (list form). No injection
    # surface.
    proc = subprocess.run(  # nosec B603 # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
        cmd, cwd=cwd, capture_output=True, text=True
    )
    if proc.returncode == 0:
        _log("destroyed", what=what)
        return
    combined = (proc.stdout + " " + proc.stderr).lower()
    if any(m in combined for m in ("already", "does not exist", "no such", "notfound", "no stack")):
        _log("destroy_skipped", what=what, reason="already gone")
        return
    _log("destroy_failed", what=what, returncode=proc.returncode, stderr=proc.stderr[-2000:])
    raise RuntimeError(
        f"teardown step failed for {what} (exit {proc.returncode}): {proc.stderr[-2000:]}"
    )


def teardown(
    *,
    registry_id: str,
    record_ids: list[str],
    region: str,
    repo_root: str,
    orchestrator_project_dir: str,
    peer_project_dir: str,
) -> dict:
    """Full records-before-stack teardown. Best-effort idempotent throughout.

    record_ids: every created record to remove, meaning the peer's and the
    orchestrator's own self-registered record. The caller supplies both (use
    record_id_from_arn() upstream if only ARNs are on hand)."""
    import boto3

    control_client = boto3.client(_CONTROL_PLANE, region_name=region)

    record_outcomes = {}
    for record_id in record_ids:
        record_outcomes[record_id] = deprecate_then_delete_record(
            control_client, registry_id, record_id
        )

    # (2) cdk destroy both agentcore runtimes (tolerant). There is no
    # `agentcore destroy` verb. `agentcore deploy` deploys the runtime
    # "via CDK", so the inverse is `cdk destroy` run in the runtime's own
    # agentcore/cdk app dir. `--all` destroys that app's single
    # stack (AgentCore-<project>-<target>) without needing to compute its name;
    # `--force` skips the interactive confirm (non-interactive teardown).
    _run_tolerant(
        ["cdk", "destroy", "--all", "--force"],
        cwd=os.path.join(orchestrator_project_dir, "agentcore", "cdk"),
        what="orchestrator-runtime",
    )
    _run_tolerant(
        ["cdk", "destroy", "--all", "--force"],
        cwd=os.path.join(peer_project_dir, "agentcore", "cdk"),
        what="peer-runtime",
    )

    # (3) cdk destroy U1 infra (tolerant).
    _run_tolerant(
        ["cdk", "destroy", "--force"], cwd=repo_root, what="ArrivalSignalInfrastructureStack"
    )

    outcome = {"records": record_outcomes, "runtimesAndStack": "destroyed-or-already-gone"}
    _log("teardown_complete", **outcome)
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-command destructive teardown.")
    parser.add_argument("--registry-id", required=True)
    parser.add_argument(
        "--record-id",
        action="append",
        default=[],
        dest="record_ids",
        help="A created record id to remove. Repeat for each (peer + orchestrator).",
    )
    parser.add_argument(
        "--record-arn",
        action="append",
        default=[],
        dest="record_arns",
        help="Alternatively, a record ARN; recordId is derived via U0's record_id_from_arn().",
    )
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--orchestrator-project-dir", default="orchestrator-host")
    parser.add_argument("--peer-project-dir", default="peer-host")
    args = parser.parse_args(argv)

    # Use U0's ARN->recordId derivation for any ARNs supplied.
    from agent_registry_sample.sdk_reconciliation.reconciliation import record_id_from_arn

    record_ids = list(args.record_ids) + [record_id_from_arn(a) for a in args.record_arns]
    if not record_ids:
        parser.error("supply at least one --record-id or --record-arn to remove.")

    outcome = teardown(
        registry_id=args.registry_id,
        record_ids=record_ids,
        region=args.region,
        repo_root=args.repo_root,
        orchestrator_project_dir=args.orchestrator_project_dir,
        peer_project_dir=args.peer_project_dir,
    )
    print(json.dumps(outcome, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
