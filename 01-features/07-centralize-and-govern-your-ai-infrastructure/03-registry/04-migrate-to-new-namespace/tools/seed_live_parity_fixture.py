#!/usr/bin/env python3
"""Create the two Preview fixtures the new behaviours need, and a target registry to migrate them into.

The existing test matrix cannot exercise either feature:

* its duplicate pair carries *different* recordVersions, so the target registry's (name, recordVersion) key never
  collides -- a real collision needs two records with the same name and the same (here, absent)
  version;
* it has approved records, but migrating them into a target registry that carries
  ``autoApprovalRules: [APPROVE_ALL]`` proves nothing about the tool, because the service would
  approve them regardless.

So this creates:

* a Preview registry with ``autoApproval`` on, holding two records that share the name
  ``shared-name`` (no recordVersion), one approved and one left in DRAFT, plus a solo approved
  record, a solo DRAFT record and a deprecated one;
* a target registry with **no** auto-approval rules, so reproducing APPROVED requires both
  ``SubmitRegistryRecordForApproval`` and ``UpdateRegistryRecordStatus``.

Usage: python3 tools/seed_live_parity_fixture.py [--preview-region us-east-1] [--target-region us-west-2]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from threading import Event

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError

# A never-set event provides the same interruptible bounded delay as sleep while making it explicit
# that these waits exist only between state-machine polls.
_POLL_WAIT = Event()

# No sys.path insertion here: nothing in this file imports migration_common, and putting
# glue/common at position 0 placed it ahead of the standard library for the whole process, so a
# module in there sharing a stdlib name would have shadowed it.

TARGET_ENDPOINT = "https://agent-registry-control.{region}.api.aws"

#: Per-request timeout for the hand-signed target calls below.
_HTTP_TIMEOUT_SECONDS = 30


def target_request(region: str, method: str, path: str, body: dict | None = None) -> dict:
    """Call a target control-plane operation with raw SigV4.

    The migration performs record-level operations only -- creating a registry is not something it
    does -- so creating the target registry for this fixture is signed by hand rather than routed
    through a client the engine would never otherwise build.
    """
    # `urllib.error` imported explicitly, not relied on as an attribute that `urllib.request`
    # happens to bind by importing it itself. That works today purely because of CPython's import
    # graph, which is not a promise.
    import urllib.error
    import urllib.parse
    import urllib.request

    session = boto3.Session()
    url = TARGET_ENDPOINT.format(region=region) + path
    # Checked before anything is signed or sent. urlopen honours whatever scheme the URL carries,
    # including file: and any registered custom handler. This URL is built from a hardcoded https
    # template, so it cannot be anything else today; asserting it means a later change that makes
    # TARGET_ENDPOINT configurable cannot silently turn a signed API call into a local-file read.
    scheme = urllib.parse.urlsplit(url).scheme
    if scheme != "https":
        raise RuntimeError(f"Refusing to call the target control plane over {scheme!r}: {url}")
    payload = json.dumps(body or {}).encode() if body is not None else b""
    request = AWSRequest(
        method=method,
        url=url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    SigV4Auth(session.get_credentials().get_frozen_credentials(), "agent-registry", region).add_auth(request)
    prepared = urllib.request.Request(url, data=payload or None, method=method, headers=dict(request.headers))
    try:
        # An explicit timeout, because the default is the global socket timeout -- normally None,
        # which means an unresponsive endpoint hangs the seeder with no bound at all.
        with urllib.request.urlopen(  # nosec B310 -- scheme asserted https immediately above
            prepared, timeout=_HTTP_TIMEOUT_SECONDS
        ) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Target {method} {path} failed: {error.code} {error.read().decode()}") from error
    except OSError as error:
        # URLError (DNS, connection refused) and socket.timeout both land here.
        raise RuntimeError(f"Target {method} {path} could not be reached: {error}") from error
    return json.loads(raw) if raw else {}


# name, status to drive it to, and whether it shares its name with another record.
FIXTURE_RECORDS = [
    ("shared-name", "APPROVED", "first of a colliding pair, approved"),
    ("shared-name", "DRAFT", "second of the colliding pair, left in DRAFT"),
    ("solo-approved", "APPROVED", "approved, no collision"),
    ("solo-draft", "DRAFT", "draft, no collision"),
    ("solo-deprecated", "DEPRECATED", "deprecated at source"),
]


def wait_for_registry(client, registry_id: str, *, attempts: int = 60) -> str:
    for _ in range(attempts):
        status = client.get_registry(registryId=registry_id)["status"]
        if status != "CREATING":
            return status
        _POLL_WAIT.wait(5)
    return "TIMED_OUT"


def wait_for_record(client, registry_id: str, record_id: str, *, attempts: int = 60) -> str:
    for _ in range(attempts):
        status = client.get_registry_record(registryId=registry_id, recordId=record_id)["status"]
        if status not in {"CREATING", "UPDATING"}:
            return status
        _POLL_WAIT.wait(3)
    return "TIMED_OUT"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview-region", default="us-east-1")
    parser.add_argument("--target-region", default="us-west-2")
    parser.add_argument(
        "--preview-registry-id",
        help="reuse an already-seeded Preview fixture instead of creating another one",
    )
    args = parser.parse_args()

    account = boto3.client("sts").get_caller_identity()["Account"]
    stamp = int(time.time())

    preview = boto3.client("bedrock-agentcore-control", region_name=args.preview_region)
    seeded: list[tuple[str, str, str]] = []
    if args.preview_registry_id:
        preview_registry_id = args.preview_registry_id
        print(f"Reusing Preview registry {preview_registry_id}")
        # Paginated. Reading only the first page silently under-reported the fixture, and the
        # summary this prints is what an operator builds their migration mapping from.
        next_token: str | None = None
        while True:
            request: dict = {"registryId": preview_registry_id}
            if next_token:
                request["nextToken"] = next_token
            page = preview.list_registry_records(**request)
            for record in page.get("registryRecords", []):
                seeded.append((record.get("name", ""), record["recordId"], record.get("status", "")))
            next_token = page.get("nextToken")
            if not next_token:
                break
        return finish(args, account, preview_registry_id, seeded)

    preview_name = f"parity-fixture-{stamp}"
    print(f"Creating Preview registry {preview_name} in {args.preview_region} (autoApproval on)")
    created = preview.create_registry(
        name=preview_name,
        description="Fixture for duplicate-name and status-parity verification",
        authorizerType="AWS_IAM",
        approvalConfiguration={"autoApproval": True},
        clientToken=str(uuid.uuid4()),
    )
    preview_registry_id = created.get("registryId") or str(created["registryArn"]).rsplit("/", 1)[-1]
    status = wait_for_registry(preview, preview_registry_id)
    print(f"  registryId={preview_registry_id} status={status}")
    if status != "READY":
        print("Preview registry never became READY")
        return 1

    for name, desired_status, why in FIXTURE_RECORDS:
        try:
            response = preview.create_registry_record(
                registryId=preview_registry_id,
                name=name,
                description=why,
                descriptorType="CUSTOM",
                descriptors={"custom": {"inlineContent": json.dumps({"fixture": name, "why": why})}},
                clientToken=str(uuid.uuid4()),
            )
        except ClientError as error:
            print(f"  FAIL {name}: {error}")
            return 1
        record_id = response.get("recordId") or str(response["recordArn"]).rsplit("/", 1)[-1]
        settled = wait_for_record(preview, preview_registry_id, record_id)
        print(f"  created {name} -> {record_id} ({settled})")

        if desired_status != "DRAFT":
            # Wrapped, like the create above. A throttle or a refused transition here used to abort
            # the whole seeder with a traceback, abandoning the records already created -- and this
            # is a fixture, so a partial one is worse than a reported failure.
            try:
                preview.submit_registry_record_for_approval(registryId=preview_registry_id, recordId=record_id)
                settled = wait_for_record(preview, preview_registry_id, record_id)
                if desired_status == "DEPRECATED":
                    preview.update_registry_record_status(
                        registryId=preview_registry_id,
                        recordId=record_id,
                        status="DEPRECATED",
                        statusReason="Fixture: deprecated at source",
                    )
                    settled = wait_for_record(preview, preview_registry_id, record_id)
            except ClientError as error:
                print(f"    WARN could not drive {name} to {desired_status}: {error}")
            print(f"    -> {settled}")
        # The *observed* status, not the one that was asked for. Recording the intent meant the
        # summary reported `status=APPROVED` for a record that never got there -- in a fixture whose
        # entire purpose is verifying status parity.
        if settled != desired_status:
            print(f"    WARN {name} is {settled}, not the requested {desired_status}")
        seeded.append((name, record_id, settled))

    return finish(args, account, preview_registry_id, seeded)


def finish(
    args: argparse.Namespace,
    account: str,
    preview_registry_id: str,
    seeded: list[tuple[str, str, str]],
) -> int:
    """Create the target registry and print the mapping to migrate with."""
    stamp = int(time.time())
    target_name = f"parity-target-{stamp}"
    print(f"\nCreating target registry {target_name} in {args.target_region} (NO auto-approval)")
    target_created = target_request(
        args.target_region,
        "POST",
        "/registries",
        {
            "name": target_name,
            "description": "Target for duplicate-name and status-parity verification",
            "discoveryConfiguration": {"authorizerType": "AWS_IAM"},
            "clientToken": str(uuid.uuid4()),
        },
    )
    target_registry_id = str(target_created["registryArn"]).rsplit("/", 1)[-1]
    target_status = "CREATING"
    for _ in range(60):
        target_status = target_request(args.target_region, "GET", f"/registries/{target_registry_id}")["status"]
        if target_status != "CREATING":
            break
        _POLL_WAIT.wait(5)
    print(f"  registryId={target_registry_id} status={target_status}")
    if target_status != "READY":
        print("Target registry never became READY")
        return 1

    print("\nMapping to add to config/migration.json:")
    print(
        json.dumps(
            {
                "id": "parity",
                "source": {
                    "accountId": account,
                    "region": args.preview_region,
                    "registryId": preview_registry_id,
                },
                "target": {
                    "accountId": account,
                    "region": args.target_region,
                    "registryId": target_registry_id,
                },
            },
            indent=2,
        )
    )
    print("\nSeeded source records:")
    for name, record_id, desired in seeded:
        print(f"  {record_id}  name={name}  status={desired}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
