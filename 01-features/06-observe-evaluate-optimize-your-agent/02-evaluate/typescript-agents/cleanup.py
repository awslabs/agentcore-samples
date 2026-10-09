"""Clean up all AWS resources created by deploy.py and evaluate.py.

Reads resource IDs from agent_config.json and results/cleanup_state.json and deletes:
  - Batch evaluations (stopped first if still running)
  - Online evaluation configs and their results log groups
  - Custom evaluators
  - AgentCore Runtime and its log group
  - ECR repository + images
  - IAM roles (online evaluation role and runtime execution role)

The shared batch results log group
(/aws/bedrock-agentcore/evaluations/batch-evaluations/results/default) is kept,
because other batch evaluations in the account write to it too.

Usage:
    python cleanup.py [--region REGION] [--config PATH]
"""

import argparse
import json
import sys
import time
from pathlib import Path

import boto3
from boto3.session import Session
from botocore.exceptions import ClientError

_SCRIPT_DIR = Path(__file__).parent
_DEFAULT_CONFIG = _SCRIPT_DIR / "agent_config.json"
_CLEANUP_STATE_PATH = _SCRIPT_DIR / "results" / "cleanup_state.json"

_TERMINAL_BATCH_STATUSES = ("COMPLETED", "FAILED", "STOPPED")
_ONLINE_RESULTS_LOG_GROUP = "/aws/bedrock-agentcore/evaluations/results/{config_id}"

_NOT_FOUND_CODES = ("ResourceNotFoundException", "NoSuchEntity", "RepositoryNotFoundException")

errors = []


def _try(label: str, fn) -> None:
    try:
        fn()
        print(f"  OK  {label}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] in _NOT_FOUND_CODES:
            print(f"  --  {label}: already deleted")
            return
        print(f"  ERR {label}: {exc}")
        errors.append(f"{label}: {exc}")


def _delete_role(iam, role_name: str) -> None:
    for policy in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
        iam.delete_role_policy(RoleName=role_name, PolicyName=policy)
    iam.delete_role(RoleName=role_name)


def _delete_batch(agentcore, batch_id: str, timeout: int = 120) -> None:
    """Stop the batch evaluation if it is still running, then delete it."""
    if agentcore.get_batch_evaluation(batchEvaluationId=batch_id).get("status") not in _TERMINAL_BATCH_STATUSES:
        agentcore.stop_batch_evaluation(batchEvaluationId=batch_id)
        for _ in range(0, timeout, 10):
            if agentcore.get_batch_evaluation(batchEvaluationId=batch_id).get("status") in _TERMINAL_BATCH_STATUSES:
                break
            time.sleep(10)
    agentcore.delete_batch_evaluation(batchEvaluationId=batch_id)


def _wait_runtime_deleted(ctrl, agent_id: str, timeout: int = 300) -> bool:
    for _ in range(0, timeout, 10):
        try:
            ctrl.get_agent_runtime(agentRuntimeId=agent_id)
        except ClientError:
            return True
        time.sleep(10)
    return False


def main() -> None:
    """Delete every resource recorded by deploy.py and evaluate.py."""
    parser = argparse.ArgumentParser(description="Clean up TypeScript HR Assistant resources")
    parser.add_argument("--region", default=None)
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG))
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print("No agent_config.json found — nothing to clean up.")
        sys.exit(0)

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    state = json.loads(_CLEANUP_STATE_PATH.read_text(encoding="utf-8")) if _CLEANUP_STATE_PATH.exists() else {}
    region = args.region or cfg.get("region") or Session().region_name or "us-east-1"
    delete_resources(cfg, state, region)

    if errors:
        print(f"\n{len(errors)} error(s) during cleanup:")
        for err in errors:
            print(f"  - {err}")
    else:
        print("\nAll resources cleaned up successfully.")


def delete_resources(cfg: dict, state: dict, region: str) -> None:
    """Delete resources in dependency order: evaluations first, IAM roles last."""
    ctrl = boto3.client("bedrock-agentcore-control", region_name=region)
    logs = boto3.client("logs", region_name=region)
    _delete_evaluation_resources(boto3.client("bedrock-agentcore", region_name=region), ctrl, logs, state)
    _delete_deployment(ctrl, logs, boto3.client("ecr", region_name=region), cfg)

    iam = boto3.client("iam")
    role_names = list(state.get("evaluation_role_names", []))
    if cfg.get("role_arn"):
        role_names.append(cfg["role_arn"].split("/")[-1])
    for role_name in role_names:
        _try(f"DeleteIAMRole {role_name}", lambda n=role_name: _delete_role(iam, n))


def _delete_evaluation_resources(agentcore, ctrl, logs, state: dict) -> None:
    """Delete batch evaluations, online configs (with their results log groups) and custom evaluators."""
    for batch_id in state.get("batch_evaluation_ids", []):
        _try(f"DeleteBatchEvaluation {batch_id}", lambda b=batch_id: _delete_batch(agentcore, b))

    for config_id in state.get("online_evaluation_config_ids", []):
        _try(
            f"DeleteOnlineEvaluationConfig {config_id}",
            lambda c=config_id: ctrl.delete_online_evaluation_config(onlineEvaluationConfigId=c),
        )
        results_log_group = _ONLINE_RESULTS_LOG_GROUP.format(config_id=config_id)
        _try(f"DeleteLogGroup {results_log_group}", lambda g=results_log_group: logs.delete_log_group(logGroupName=g))

    for evaluator_id in state.get("custom_evaluator_ids", []):
        _try(f"DeleteEvaluator {evaluator_id}", lambda e=evaluator_id: ctrl.delete_evaluator(evaluatorId=e))


def _delete_deployment(ctrl, logs, ecr, cfg: dict) -> None:
    """Delete the runtime, its log group and the ECR repository created by deploy.py."""
    agent_id = cfg.get("agent_id", "")
    if agent_id:
        _try(f"DeleteAgentRuntime {agent_id}", lambda: ctrl.delete_agent_runtime(agentRuntimeId=agent_id))
        if not _wait_runtime_deleted(ctrl, agent_id):
            print(f"  WARN runtime {agent_id} is still deleting; check it later in the console")

    cw_log_group = cfg.get("cw_log_group", "")
    if cw_log_group:
        _try(f"DeleteLogGroup {cw_log_group}", lambda: logs.delete_log_group(logGroupName=cw_log_group))

    ecr_repo = cfg.get("ecr_repo", "")
    if ecr_repo:
        _try(
            f"DeleteECRRepository {ecr_repo}",
            lambda: ecr.delete_repository(repositoryName=ecr_repo, force=True),
        )


if __name__ == "__main__":
    main()
