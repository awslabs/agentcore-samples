"""
Delete everything `deploy.py` created.

    python cleanup.py            # asks before deleting
    python cleanup.py --yes      # no prompt

WHY THIS MATTERS MORE THAN USUAL
--------------------------------
A CapacityProvider runs EC2 instances in YOUR account, and you pay for them
for as long as they are up. They are reaped automatically once every agent on
them has been idle for `idleInstanceTimeout` (900s in this sample), but the
CapacityProvider itself persists until you delete it.

Order matters: runtimes reference the CapacityProvider, so runtimes go first.
Deleting the CapacityProvider terminates the instances it manages.

This script is config-driven: it deletes exactly what `cp_config.json` records
and silently skips resources this sample never created (there is no ECR repo or
CodeBuild project in this zip-only sample). It never lists-and-deletes, which
matters if you share an account — nothing outside your own config is touched.

SHARED WITH THE OTHER SAMPLES
-----------------------------
The S3 bucket (`agentcore-cp-samples-<account>-<region>`) and the runtime IAM
role (`agentcore-cp-samples-runtime-role`) use the same names in every sample,
so running this cleanup removes them from under the others too. That is
harmless — each `deploy.py` recreates them if missing — but a sibling sample's
`cleanup.py` will then report `NoSuchBucket` / `NoSuchEntity` for them. Those
messages mean "already gone", not "failed".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import boto3

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "cp_config.json"

CONTROL_SERVICE = "bedrock-agentcore-control"


def resolve_region(explicit: str | None = None) -> str:
    """Region from the config deploy.py wrote, else the environment. No default."""
    region = (
        explicit
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or boto3.Session().region_name
    )
    if not region:
        sys.exit("No AWS region configured. Set AWS_REGION.")
    return region


def delete_runtimes(agentcore, config) -> None:
    for kind, runtime in config.get("runtimes", {}).items():
        rid = runtime["id"]
        try:
            agentcore.delete_agent_runtime(agentRuntimeId=rid)
            print(f"  deleted runtime ({kind}): {rid}")
        except agentcore.exceptions.ResourceNotFoundException:
            print(f"  runtime ({kind}) {rid}: already gone")
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            print(f"  runtime ({kind}) {rid}: {type(exc).__name__}: {exc}")


def wait_for_versions_to_detach(agentcore, cp_id: str, attempts: int = 20) -> None:
    """
    Wait until no agent runtime versions are still attached to the CP.

    `DeleteAgentRuntime` returns before its versions are detached, so an
    immediate `DeleteCapacityProvider` fails with

        ValidationException: ... still has attached agent runtime versions

    even though the runtime is already gone from `GetAgentRuntime`. Rather than
    delete-and-match-on-the-error-string, ask the API directly:
    `list_agent_runtime_versions_by_capacity_provider` is exactly this question.
    """
    for attempt in range(1, attempts + 1):
        try:
            attached = agentcore.list_agent_runtime_versions_by_capacity_provider(
                capacityProviderId=cp_id
            ).get("agentRuntimes", [])
        except agentcore.exceptions.ResourceNotFoundException:
            return  # CP already gone; the delete below will report it
        except Exception as exc:  # noqa: BLE001
            print(f"    could not list attached versions ({type(exc).__name__})"
                  " — trying the delete anyway", flush=True)
            return
        if not attached:
            return
        print(f"    {len(attached)} runtime version(s) still attached, waiting "
              f"({attempt}/{attempts})", flush=True)
        time.sleep(15)


def delete_capacity_provider(agentcore, config, attempts: int = 20) -> None:
    cp_id = config.get("capacityProviderId")
    if not cp_id:
        return

    wait_for_versions_to_detach(agentcore, cp_id)

    # Even after the list comes back empty the delete can still race, so the
    # "runtime versions" retry stays as a backstop. Giving up here strands a
    # CapacityProvider that keeps launching billable instances.
    for attempt in range(1, attempts + 1):
        try:
            agentcore.delete_capacity_provider(capacityProviderId=cp_id)
            print(f"  deleting CapacityProvider: {cp_id}")
            break
        except agentcore.exceptions.ResourceNotFoundException:
            print(f"  CapacityProvider {cp_id}: already gone")
            return
        except Exception as exc:  # noqa: BLE001
            if "runtime versions" not in str(exc) or attempt == attempts:
                print(f"  CapacityProvider {cp_id}: {type(exc).__name__}: {exc}")
                if attempt == attempts:
                    print("  GIVING UP — this CapacityProvider may still be "
                          "running billable instances. Delete it by hand.")
                return
            print(f"  waiting for the runtime delete to propagate "
                  f"({attempt}/{attempts})", flush=True)
            time.sleep(30)

    # Poll until it is gone, so the user knows the instances are on their way out.
    # Two things this loop has to get right, both learned the hard way:
    #
    #   * Only ResourceNotFound means deleted. Treating every exception as success
    #     is tempting and wrong — one throttle or connection blip then prints
    #     "CapacityProvider deleted" over a fleet that is still running and
    #     still billing.
    #   * DELETE_FAILED is not terminal. Re-issuing the delete has been observed
    #     to succeed, sometimes only on the third attempt and roughly nine
    #     minutes in. A CapacityProvider can also report deleted and then reappear
    #     as DELETE_FAILED, so the loop keeps watching rather than exiting on the
    #     first hopeful reading.
    for _ in range(120):
        try:
            status = agentcore.get_capacity_provider(
                capacityProviderId=cp_id
            ).get("status")
        except agentcore.exceptions.ResourceNotFoundException:
            print("  CapacityProvider deleted")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"    status unknown ({type(exc).__name__}) — still watching",
                  flush=True)
            time.sleep(15)
            continue
        print(f"    status: {status}", flush=True)
        if status == "DELETE_FAILED":
            try:
                agentcore.delete_capacity_provider(capacityProviderId=cp_id)
                print("    DELETE_FAILED — re-issued the delete", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"    retry rejected: {type(exc).__name__}", flush=True)
        time.sleep(15)
    print("  still not deleted after 30 min. Check for running instances, then "
          "retry:\n    python cleanup.py   # safe to run again")


def empty_and_delete_bucket(region: str, bucket: str) -> None:
    s3 = boto3.client("s3", region_name=region)
    try:
        paginator = s3.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket):
            targets = [
                {"Key": o["Key"], "VersionId": o["VersionId"]}
                for key in ("Versions", "DeleteMarkers")
                for o in page.get(key, [])
            ]
            if targets:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": targets})
        s3.delete_bucket(Bucket=bucket)
        print(f"  deleted bucket: {bucket}")
    except Exception as exc:  # noqa: BLE001
        print(f"  bucket {bucket}: {type(exc).__name__}: {exc}")


def delete_role(role: str) -> None:
    iam = boto3.client("iam")
    try:
        for policy in iam.list_role_policies(RoleName=role)["PolicyNames"]:
            iam.delete_role_policy(RoleName=role, PolicyName=policy)
        for attached in iam.list_attached_role_policies(RoleName=role)["AttachedPolicies"]:
            iam.detach_role_policy(RoleName=role, PolicyArn=attached["PolicyArn"])
        iam.delete_role(RoleName=role)
        print(f"  deleted role: {role}")
    except Exception as exc:  # noqa: BLE001
        print(f"  role {role}: {type(exc).__name__}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    if not CONFIG.is_file():
        sys.exit(f"{CONFIG.name} not found — nothing recorded to clean up.")
    config = json.loads(CONFIG.read_text())
    # The region deploy.py recorded, else the environment. Passed on to the S3
    # client below, so it has to be a real value, not None.
    region = resolve_region(config.get("region"))

    print("This will delete:")
    for kind, runtime in config.get("runtimes", {}).items():
        print(f"  runtime ({kind})       {runtime['id']}")
    print(f"  CapacityProvider      {config.get('capacityProviderId')}  (terminates its EC2 instances)")
    for label, key in (
        ("S3 bucket", "bucket"),
        ("IAM runtime role", "runtimeRole"),
    ):
        if config.get(key):
            print(f"  {label:<21} {config[key]}")

    if not args.yes and input("\nProceed? [y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("Aborted.")

    # One client for both the runtime APIs and the CapacityProvider APIs.
    agentcore = boto3.client(CONTROL_SERVICE, region_name=region)

    print("\nRuntimes:")
    delete_runtimes(agentcore, config)

    print("CapacityProvider:")
    delete_capacity_provider(agentcore, config)

    print("Artifact resources:")
    if config.get("bucket"):
        empty_and_delete_bucket(region, config["bucket"])

    print("IAM roles:")
    if config.get("runtimeRole"):
        delete_role(config["runtimeRole"])

    CONFIG.unlink()
    print(f"\nDone. Removed {CONFIG.name}.")

    # `--include-managed-resources` is not optional here, and this is the one
    # command where getting it wrong costs money. CapacityProvider instances are
    # EC2 "managed resources", so since April 2026 they are hidden by default
    # from describe-instances. Without the flag, a still-running fleet prints as
    # an empty table — which reads exactly like "cleanup worked". deploy.py sets
    # the account to visible, but the flag makes this command correct either way.
    print("Confirm no instances remain:")
    print(
        "  aws ec2 describe-instances --region "
        f"{region} --include-managed-resources --filters "
        f"'Name=tag:bedrock-agentcore:capacity-provider-id,Values={config.get('capacityProviderId')}' "
        "--query 'Reservations[].Instances[].[InstanceId,State.Name]' --output table"
    )
    print("\n(deploy.py set this account's managed resources to visible and does")
    print(" not revert it. To re-hide them — account-wide, all principals:")
    print(f"    aws ec2 modify-managed-resource-visibility --region {region} \\")
    print("      --default-visibility hidden)")


if __name__ == "__main__":
    main()
