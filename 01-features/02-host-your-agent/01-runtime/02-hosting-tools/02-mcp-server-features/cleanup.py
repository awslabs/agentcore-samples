"""Clean up all resources. Usage: python cleanup.py

Every step records its outcome. If anything fails the script exits non-zero and
keeps runtime_config.json, because agentRuntimeId cannot be reconstructed from
anything else — losing it means the leftover runtime has to be hunted down by hand.
"""

import json
import os
import sys
import time

import boto3
from boto3.session import Session

POLL_TIMEOUT_SECONDS = 300


def delete_endpoints(control, runtime_id: str) -> None:
    """Delete customer-created endpoints, then wait for them to disappear.

    DEFAULT is skipped: it is service-managed, is removed together with the runtime,
    and rejects DeleteAgentRuntimeEndpoint with "Default endpoints are removed when
    you delete the agent".

    Any *other* endpoint has to be fully gone before DeleteAgentRuntime is accepted —
    it fails with "This agent has endpoints that must be deleted before the agent can
    be removed" while one is still present, including while one is still DELETING.
    This sample's deploy.py no longer creates such an endpoint, but earlier versions
    made one named "default", so this loop is what lets those deployments be removed.
    """
    endpoints = control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id).get("runtimeEndpoints", [])
    names = [e["name"] for e in endpoints if e["name"] != "DEFAULT"]
    for name in names:
        print(f"  Deleting endpoint: {name}")
        control.delete_agent_runtime_endpoint(agentRuntimeId=runtime_id, endpointName=name)
    if not names:
        return

    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        left = [
            e["name"]
            for e in control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id).get("runtimeEndpoints", [])
            if e["name"] != "DEFAULT"
        ]
        if not left:
            return
        time.sleep(10)
    raise TimeoutError(f"endpoints {left} still present after {POLL_TIMEOUT_SECONDS}s")


def wait_for_runtime_deletion(control, runtime_id: str) -> None:
    """Block until the runtime is really gone, so the caller can report honestly."""
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        try:
            control.get_agent_runtime(agentRuntimeId=runtime_id)
        except control.exceptions.ResourceNotFoundException:
            return
        time.sleep(10)
    raise TimeoutError(f"runtime {runtime_id} still present after {POLL_TIMEOUT_SECONDS}s")


def main():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_config.json")
    try:
        with open(config_path) as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"Error: {config_path} not found.")
        sys.exit(1)

    agent_name, runtime_id, region = (
        config["agent_name"],
        config["runtime_id"],
        config["region"],
    )
    account_id = Session(region_name=region).client("sts").get_caller_identity()["Account"]

    control = boto3.client("bedrock-agentcore-control", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    logs = boto3.client("logs", region_name=region)

    print(f"Cleaning up: {agent_name}\n")

    # The runtime is the only resource that bills while idle, so it goes first — and if
    # it cannot be removed we stop here, leaving the artifact, log groups and role in
    # place so that re-running this script retries a coherent deployment.
    try:
        try:
            control.get_agent_runtime(agentRuntimeId=runtime_id)
        except control.exceptions.ResourceNotFoundException:
            # Only a missing *runtime* is benign here. Scoping the check to this one call
            # keeps a ResourceNotFoundException from the endpoint sweep or the delete
            # below — where it would mean something else entirely — from being reported
            # as "already gone" while the runtime is in fact still running.
            print("  Runtime already gone")
        else:
            delete_endpoints(control, runtime_id)
            print(f"  Deleting runtime: {runtime_id}")
            control.delete_agent_runtime(agentRuntimeId=runtime_id)
            wait_for_runtime_deletion(control, runtime_id)
            print("  Runtime deleted")
    except Exception as e:  # noqa: BLE001
        print("\n✗ Cleanup INCOMPLETE — the runtime still exists and continues to bill:")
        print(f"    {e}")
        print("\n  Left the S3 artifact, log groups and IAM role in place; re-run this script to retry.")
        print(f"  Keeping {os.path.basename(config_path)} (runtime_id={runtime_id}).")
        sys.exit(1)

    failures = []

    # Delete the code artifact. The bucket itself is left in place: its name is shared
    # by every sample in this repo, so other deployments may still have objects in it.
    try:
        s3.delete_object(Bucket=f"agentcore-code-{account_id}-{region}", Key=f"{agent_name}/code.zip")
        # DeleteObject is idempotent, so this also succeeds when the key was already gone.
        print("  Removed S3 code artifact")
    except Exception as e:  # noqa: BLE001
        failures.append(f"S3 object {agent_name}/code.zip: {e}")

    # Delete the runtime's log groups. AgentCore creates these on first invocation with
    # no retention policy, so left behind they store logs — and bill — forever.
    try:
        groups = logs.describe_log_groups(logGroupNamePrefix=f"/aws/bedrock-agentcore/runtimes/{runtime_id}")
        for g in groups.get("logGroups", []):
            logs.delete_log_group(logGroupName=g["logGroupName"])
            print(f"  Deleted log group: {g['logGroupName']}")
    except Exception as e:  # noqa: BLE001
        failures.append(f"log groups for {runtime_id}: {e}")

    role_name = f"agentcore-{agent_name}-role"
    iam = boto3.client("iam", region_name=region)
    try:
        try:
            iam.get_role(RoleName=role_name)
        except iam.exceptions.NoSuchEntityException:
            # Scoped to this one call for the same reason as the runtime check above: a
            # NoSuchEntity raised by one of the policy calls below must not be reported
            # as a missing role while the role itself is still there.
            print(f"  IAM role already gone: {role_name}")
        else:
            for p in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
                iam.delete_role_policy(RoleName=role_name, PolicyName=p)
            # Managed policies block delete_role too, so detach any added by hand.
            for p in iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", []):
                iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
            iam.delete_role(RoleName=role_name)
            print(f"  Deleted IAM role: {role_name}")
    except Exception as e:  # noqa: BLE001
        failures.append(f"IAM role {role_name}: {e}")

    if failures:
        print("\n✗ Cleanup INCOMPLETE — the runtime is gone, but these remain:")
        for f in failures:
            print(f"    {f}")
        print(f"\n  Keeping {os.path.basename(config_path)} so you can re-run this script.")
        sys.exit(1)

    os.remove(config_path)
    print("\n✓ Cleanup complete")


if __name__ == "__main__":
    main()
