"""
Tear down the Egress-Controlled Code Execution demo.

Reads ``runtime_config.json`` (written by ``deploy.py``), stops the sandbox and
broker containers (mirroring the supervisor's own shutdown order), then deletes
the AgentCore Runtime so it stops incurring cost.

Pass ``--delete-ecr`` to also delete the three ECR repositories that hold the
images, and ``--delete-role`` to delete the IAM execution role — but only if
``deploy.py`` created it (a role you supplied via ``ROLE_ARN`` is left alone).

Usage:
    python cleanup.py [--delete-ecr] [--delete-role]
"""

import json
import os
import sys
import uuid

import boto3

CONFIG_FILE = "runtime_config.json"
ECR_REPO = "egress-coding-execution"


def main():
    delete_ecr = "--delete-ecr" in sys.argv[1:]
    delete_role = "--delete-role" in sys.argv[1:]

    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"Error: {CONFIG_FILE} not found. Nothing to clean up.")
        sys.exit(1)

    region = config["region"]
    runtime_id = config["runtime_id"]
    runtime_arn = config["runtime_arn"]
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    runtime = boto3.client("bedrock-agentcore", region_name=region)

    print(f"Cleaning up runtime {runtime_id} (region {region})\n")

    # Best-effort: stop the containers via the supervisor before deleting the
    # runtime. A fresh session id is fine — these commands are idempotent.
    session_id = f"egress-coding-exec-cleanup-{uuid.uuid4().hex}"
    for command in ("stop_agent", "stop_broker"):
        try:
            runtime.invoke_agent_runtime(
                agentRuntimeArn=runtime_arn,
                contentType="application/json",
                accept="application/json",
                runtimeSessionId=session_id,
                payload=json.dumps({"command": command, "params": {}}).encode("utf-8"),
            )
            print(f"  {command}: requested")
        except Exception as e:  # noqa: BLE001 - cleanup is best-effort
            print(f"  {command}: skipped ({e})")

    try:
        control.delete_agent_runtime(agentRuntimeId=runtime_id)
        print("✓ Runtime delete requested (transitions to DELETING)")
    except Exception as e:  # noqa: BLE001
        print(f"  Warning: delete_agent_runtime failed: {e}")

    if delete_ecr:
        ecr = boto3.client("ecr", region_name=region)
        for component in ("supervisor", "broker", "agent"):
            repo = f"{ECR_REPO}/{component}"
            try:
                ecr.delete_repository(repositoryName=repo, force=True)
                print(f"✓ Deleted ECR repo {repo}")
            except Exception as e:  # noqa: BLE001
                print(f"  Warning: could not delete {repo}: {e}")

    if delete_role:
        role_name = config.get("role_name")
        if not role_name:
            print("  Skipping role deletion (deploy.py did not create the role)")
        else:
            iam = boto3.client("iam")
            try:
                for p in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
                    iam.delete_role_policy(RoleName=role_name, PolicyName=p)
                iam.delete_role(RoleName=role_name)
                print(f"✓ Deleted IAM role {role_name}")
            except Exception as e:  # noqa: BLE001
                print(f"  Warning: could not delete role {role_name}: {e}")

    if os.path.exists(CONFIG_FILE):
        os.remove(CONFIG_FILE)
    print("\n✓ Cleanup complete")


if __name__ == "__main__":
    main()
