"""Delete what setup_insights.py and create_dataset.py created for the claims agent.

Removes: the online evaluation config, the evaluation execution role (with its inline policies), and the
scenario dataset.
It does NOT touch the claims agent itself (use the agent's own ./scripts/destroy.sh), does NOT disable the
account's Agent Insight Store (that deletes stored Insights for every application), and does NOT turn off
CloudWatch Transaction Search. The last two are account-wide.

Usage:
    python scripts/cleanup.py --name ClaimsAgent [--delete-logs] [--yes]
"""

from __future__ import annotations

import argparse
import sys

from botocore.exceptions import ClientError
from common import client, evaluation_clients, load_state, state_path, validate_name

GONE = ("NoSuchEntity", "ResourceNotFoundException")


def attempt(label: str, call) -> None:
    """Run one cleanup step; report and continue on 'already gone', surface anything else."""
    try:
        call()
        print(f"  deleted {label}")
    except ClientError as err:
        code = err.response["Error"]["Code"]
        if code in GONE:
            print(f"  skip    {label} (already gone)")
        else:
            print(f"  FAILED  {label}: {code}: {err.response['Error']['Message']}", file=sys.stderr)


def delete_role(iam, role: str) -> None:
    """Delete a role after its inline policies; both steps tolerate 'already gone'."""
    try:
        policies = iam.list_role_policies(RoleName=role).get("PolicyNames", [])
    except ClientError as err:
        if err.response["Error"]["Code"] in GONE:
            print(f"  skip    role {role} (already gone)")
            return
        raise
    for policy in policies:
        attempt(f"{role} policy {policy}", lambda p=policy: iam.delete_role_policy(RoleName=role, PolicyName=p))
    attempt(f"role {role}", lambda: iam.delete_role(RoleName=role))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    parser.add_argument("--delete-logs", action="store_true", help="also delete the evaluation results log group")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    name = validate_name(args.name)
    state = load_state(name)
    region = state["region"]
    config_id = state.get("online_config_id")
    role = state.get("evaluation_role_name")
    dataset_id = state.get("dataset_id")
    if not config_id and not role and not dataset_id:
        print("Nothing recorded to delete: no evaluation config, role, or dataset in the state file.")
        return 0
    if (
        not args.yes
        and input(f"Delete the evaluation config, role, and dataset for {name} in {region}? Type yes: ").strip().lower()
        != "yes"
    ):
        return 1

    control, _ = evaluation_clients(region)
    if config_id:
        attempt(
            "online evaluation config (disable)",
            lambda: control.update_online_evaluation_config(
                onlineEvaluationConfigId=config_id, executionStatus="DISABLED"
            ),
        )
        attempt(
            "online evaluation config",
            lambda: control.delete_online_evaluation_config(onlineEvaluationConfigId=config_id),
        )
        if args.delete_logs:
            group = f"/aws/bedrock-agentcore/evaluations/results/{config_id}"
            attempt(f"log group {group}", lambda: client(region, "logs").delete_log_group(logGroupName=group))
    if role:
        delete_role(client(region, "iam"), role)
    if dataset_id:
        attempt(f"dataset {dataset_id}", lambda: control.delete_dataset(datasetId=dataset_id))

    print(f"\nDone. Remove {state_path(name).name} when you no longer need it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
