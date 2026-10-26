#!/usr/bin/env python3
"""Delete everything deploy.py and invoke.py created.

Order matters, and several steps are easy to get wrong:

  * Deleting a SESSION is what deprovisions the EC2 instance, its network
    interface and its EBS volumes. Stopping an agent runtime does not.
  * Runtime versions detach asynchronously, so DeleteCapacityProvider fails for a
    while after DeleteAgentRuntime returns. Poll, do not sleep-and-hope.
  * AgentCore creates a CloudWatch log group per runtime with no retention
    policy, and deleting the runtime does not remove it.
  * Capacity provider volumes are EC2 *managed resources*, hidden from
    DescribeVolumes unless you pass IncludeManagedResources -- and a volume left
    behind by a FAILED placement survives both session and capacity provider
    deletion and cannot be deleted by you at all (DeleteVolume returns
    UnauthorizedOperation with an explicit deny in a resource-based policy). This
    script reports any it finds, because the alternative is silently paying for
    them.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

STATE_FILE = Path(__file__).resolve().parent.parent / "deployment_state.json"


def log(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"    ! {msg}", flush=True)


def main() -> None:
    if not STATE_FILE.exists():
        sys.exit(f"No {STATE_FILE.name}; nothing recorded to delete.")
    state = json.loads(STATE_FILE.read_text())

    region = state["region"]
    account = state["account"]
    cp_id = state["capacity_provider"]["id"]
    runtimes = state["runtimes"]

    control = boto3.client("bedrock-agentcore-control", region_name=region)
    data = boto3.client("bedrock-agentcore", region_name=region)

    # 1. Delete the sessions. This is the step that stops EC2 and EBS charges.
    for session_id in state.get("sessions", []):
        if len(session_id) >= 33:
            for name, runtime in runtimes.items():
                try:
                    data.stop_runtime_session(agentRuntimeArn=runtime["arn"], runtimeSessionId=session_id)
                except ClientError as exc:
                    if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                        warn(f"stop {name}: {exc.response['Error']['Code']}")
        try:
            data.delete_capacity_provider_session(capacityProviderId=cp_id, sessionId=session_id)
            log(f"deleted session {session_id} (instance + volumes deprovisioning)")
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code != "ResourceNotFoundException":
                warn(f"delete session {session_id}: {code}")

    # 2. Delete the runtimes, which detaches them from the capacity provider.
    for name, runtime in runtimes.items():
        try:
            control.delete_agent_runtime(agentRuntimeId=runtime["id"])
            log(f"deleted runtime {name}")
        except ClientError as exc:
            warn(f"delete runtime {name}: {exc.response['Error']['Code']}")

    # 3. Wait for the versions to actually detach. DeleteAgentRuntime returns
    #    before this completes, and DeleteCapacityProvider fails until it does.
    log("waiting for runtime versions to detach from the capacity provider")
    started = time.time()
    for _ in range(40):
        try:
            attached = control.list_agent_runtime_versions_by_capacity_provider(capacityProviderId=cp_id).get(
                "agentRuntimes", []
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                attached = []
            else:
                raise
        if not attached:
            log(f"versions detached after {time.time() - started:.0f}s")
            break
        time.sleep(15)
    else:
        warn("versions still attached; DeleteCapacityProvider may fail - rerun this script")

    # 4. Delete the capacity provider, which also terminates any instance left.
    log(f"deleting capacity provider {cp_id}")
    try:
        control.delete_capacity_provider(capacityProviderId=cp_id)
    except ClientError as exc:
        warn(f"delete capacity provider: {exc.response['Error']['Code']}")
    for _ in range(40):
        try:
            status = control.get_capacity_provider(capacityProviderId=cp_id)["status"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                log("capacity provider gone")
                break
            # A throttle is not a success. Only ResourceNotFound means deleted.
            warn(f"poll: {exc.response['Error']['Code']}")
            time.sleep(15)
            continue
        if status == "DELETE_FAILED":
            # Not terminal: re-issuing the delete has been observed to succeed.
            warn("DELETE_FAILED - re-issuing")
            try:
                control.delete_capacity_provider(capacityProviderId=cp_id)
            except ClientError:
                pass
        time.sleep(15)
    else:
        warn("capacity provider not confirmed deleted - check the console")

    # 5. Artifacts and roles.
    ecr = boto3.client("ecr", region_name=region)
    for repo in state.get("ecr_repositories", []):
        try:
            ecr.delete_repository(repositoryName=repo, force=True)
            log(f"deleted ECR repo {repo}")
        except ClientError as exc:
            warn(f"delete repo {repo}: {exc.response['Error']['Code']}")

    bucket_name = state.get("s3", {}).get("bucket")
    if bucket_name:
        try:
            bucket = boto3.resource("s3", region_name=region).Bucket(bucket_name)
            bucket.objects.all().delete()
            bucket.delete()
            log(f"deleted bucket {bucket_name} (including rendered audio)")
        except ClientError as exc:
            warn(f"delete bucket: {exc.response['Error']['Code']}")

    iam = boto3.client("iam")
    for role in state.get("iam_roles", []):
        try:
            for policy in iam.list_attached_role_policies(RoleName=role)["AttachedPolicies"]:
                iam.detach_role_policy(RoleName=role, PolicyArn=policy["PolicyArn"])
            for policy_name in iam.list_role_policies(RoleName=role)["PolicyNames"]:
                iam.delete_role_policy(RoleName=role, PolicyName=policy_name)
            iam.delete_role(RoleName=role)
            log(f"deleted role {role}")
        except ClientError as exc:
            warn(f"delete role {role}: {exc.response['Error']['Code']}")

    # 6. CloudWatch log groups. AgentCore creates one per runtime, nothing
    #    deletes them when the runtime goes, and they are created with no
    #    retention policy. The names derive from the runtime ids, so this must
    #    run before the state file is removed.
    logs = boto3.client("logs", region_name=region)
    deleted = 0
    for runtime in runtimes.values():
        for pattern in (
            "/aws/bedrock-agentcore/runtimes/{rid}-",
            "/aws/vendedlogs/bedrock-agentcore/runtime/APPLICATION_LOGS/{rid}",
            "/aws/vendedlogs/bedrock-agentcore/runtime/USAGE_LOGS/{rid}",
        ):
            prefix = pattern.format(rid=runtime["id"])
            try:
                for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=prefix):
                    for group in page.get("logGroups", []):
                        try:
                            logs.delete_log_group(logGroupName=group["logGroupName"])
                            deleted += 1
                        except ClientError as exc:
                            warn(f"delete log group: {exc.response['Error']['Code']}")
            except ClientError as exc:
                warn(f"list log groups {prefix}: {exc.response['Error']['Code']}")
    log(f"deleted {deleted} CloudWatch log group(s)")

    # 7. Report any volume the service did not reclaim. These come from sessions
    #    whose placement failed partway through: the volume that succeeded is
    #    orphaned, survives session and capacity provider deletion, and cannot be
    #    deleted by the account owner.
    ec2 = boto3.client("ec2", region_name=region)

    # Deprovisioning is asynchronous, and root volumes are delete-on-termination,
    # so checking immediately reports volumes that are merely mid-teardown. Wait
    # for the instances to finish terminating first, then only count volumes that
    # are actually detached.
    log("waiting for instances to finish terminating before checking for orphans")
    for _ in range(24):
        try:
            reservations = ec2.describe_instances(
                IncludeManagedResources=True,
                Filters=[{"Name": "tag:bedrock-agentcore:capacity-provider-id", "Values": [cp_id]}],
            )["Reservations"]
        except ClientError:
            break
        live = [
            i for r in reservations for i in r["Instances"] if i["State"]["Name"] not in ("terminated", "shutting-down")
        ]
        pending = [i for r in reservations for i in r["Instances"] if i["State"]["Name"] == "shutting-down"]
        if not live and not pending:
            break
        time.sleep(10)

    try:
        orphans = [
            v
            for v in ec2.describe_volumes(
                IncludeManagedResources=True,
                Filters=[{"Name": "tag:bedrock-agentcore:capacity-provider-id", "Values": [cp_id]}],
            )["Volumes"]
            # An attached volume is still being torn down with its instance.
            if v["State"] == "available"
        ]
    except ClientError as exc:
        orphans = []
        warn(f"could not check for orphaned volumes: {exc.response['Error']['Code']}")
    if orphans:
        total = sum(v["Size"] for v in orphans)
        warn(f"{len(orphans)} managed volume(s) totalling {total} GiB were NOT reclaimed:")
        for v in orphans:
            session = next(
                (t["Value"] for t in v.get("Tags", []) if t["Key"] == "bedrock-agentcore:runtime-session-id"), "?"
            )
            warn(f"    {v['VolumeId']}  {v['Size']} GiB  {v['State']}  session={session}")
        warn(
            "These are EC2 managed resources: DeleteVolume is denied by a "
            "resource-based policy even for an administrator. They usually come "
            "from a session whose placement failed. Raise a support case if they "
            "persist, and watch the EBS line on your bill."
        )
    else:
        log("no orphaned managed volumes for this capacity provider")

    STATE_FILE.unlink()
    log(f"removed {STATE_FILE.name}")

    print(
        "\nVerify nothing survived. --include-managed-resources is not optional:\n"
        "AgentCore's instances and volumes are EC2 managed resources and are hidden\n"
        "from DescribeInstances and DescribeVolumes by default, so a running fleet\n"
        "prints as an empty table.\n\n"
        f"  aws ec2 describe-instances --include-managed-resources --region {region} \\\n"
        f"    --filters 'Name=tag:bedrock-agentcore:capacity-provider-id,Values={cp_id}' \\\n"
        "    --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output table\n\n"
        f"  aws ec2 describe-volumes --include-managed-resources --region {region} \\\n"
        f"    --filters 'Name=tag:bedrock-agentcore:capacity-provider-id,Values={cp_id}' \\\n"
        "    --query 'Volumes[].[VolumeId,Size,State]' --output table\n"
        f"\n  (account {account})"
    )


if __name__ == "__main__":
    main()
