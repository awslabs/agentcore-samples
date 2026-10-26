"""
Cleanup all AWS resources created by the Google ADK evaluation sample.

Deletes:
  - AgentCore Runtime
  - Custom evaluators
  - Online evaluation config
  - IAM role (AgentCoreOnlineEvaluationRole)
  - CloudWatch log groups

Usage:
    python cleanup.py [--region REGION] [--config PATH]

Safe to run multiple times (idempotent).
"""

import argparse
import json
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

_SCRIPT_DIR = Path(__file__).parent
_DEFAULT_CONFIG = _SCRIPT_DIR / "agent_config.json"

parser = argparse.ArgumentParser(description="Cleanup Google ADK evaluation resources")
parser.add_argument("--region", default=None, help="AWS region")
parser.add_argument("--config", default=str(_DEFAULT_CONFIG), help="Path to agent_config.json")
args = parser.parse_args()

_cfg = {}
_config_path = Path(args.config)
if _config_path.exists():
    _cfg = json.loads(_config_path.read_text())

REGION = args.region or _cfg.get("region", "us-east-1")
AGENT_ID = _cfg.get("agent_id", "")
CW_LOG_GROUP = _cfg.get("cw_log_group", "")

print("=" * 60)
print("Cleanup — Google ADK Evaluation Resources")
print("=" * 60)
print(f"  Region: {REGION}")

cp_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
iam_client = boto3.client("iam")
logs_client = boto3.client("logs", region_name=REGION)


def _safe(fn, label):
    try:
        fn()
        print(f"  + {label}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("ResourceNotFoundException", "NoSuchEntity", "NotFoundException"):
            print(f"  - {label} (already deleted)")
        else:
            print(f"  x {label}: {e}")
    except Exception as e:
        print(f"  x {label}: {e}")


# 1. Delete online evaluation config
print("\n[1/5] Deleting online evaluation config ...")
_online_path = _SCRIPT_DIR / "results" / "online_eval_config.json"
if _online_path.exists():
    _online_cfg = json.loads(_online_path.read_text())
    config_id = _online_cfg.get("config_id", "")
    if config_id:
        _safe(
            lambda: cp_client.delete_online_evaluation_config(onlineEvaluationConfigId=config_id),
            f"Online config {config_id}",
        )
else:
    print("  - No online config found (skipping)")

# 2. Delete custom evaluators
print("\n[2/5] Deleting custom evaluators ...")
_results_path = _SCRIPT_DIR / "results" / "on_demand_results.json"
if _results_path.exists():
    _results = json.loads(_results_path.read_text())
    evaluator_id = _results.get("custom_evaluator_id", "")
    if evaluator_id:
        _safe(
            lambda: cp_client.delete_evaluator(evaluatorId=evaluator_id),
            f"Evaluator {evaluator_id}",
        )
else:
    print("  - No evaluator results found (skipping)")

# 3. Delete AgentCore Runtime
print("\n[3/5] Deleting AgentCore Runtime ...")
if AGENT_ID:
    _safe(
        lambda: cp_client.delete_agent_runtime(agentRuntimeId=AGENT_ID),
        f"Runtime {AGENT_ID}",
    )
else:
    print("  - No agent ID found (skipping)")

# 4. Delete IAM role
print("\n[4/5] Deleting IAM role ...")
ROLE_NAME = "AgentCoreOnlineEvaluationRole"


def _delete_role():
    iam_client.delete_role_policy(RoleName=ROLE_NAME, PolicyName="AgentCoreOnlineEvalPolicy")
    iam_client.delete_role(RoleName=ROLE_NAME)


_safe(_delete_role, f"IAM role {ROLE_NAME}")

# 5. Delete CloudWatch log groups
print("\n[5/5] Deleting CloudWatch log groups ...")
if CW_LOG_GROUP:
    _safe(lambda: logs_client.delete_log_group(logGroupName=CW_LOG_GROUP), CW_LOG_GROUP)

if _online_path.exists():
    _online_cfg = json.loads(_online_path.read_text())
    results_lg = _online_cfg.get("results_log_group", "")
    if results_lg:
        _safe(lambda: logs_client.delete_log_group(logGroupName=results_lg), results_lg)

# Clean local files
print("\n  Removing local config files ...")
for f in [_config_path, _results_path, _online_path]:
    if f.exists():
        f.unlink()
        print(f"    Deleted {f.name}")

print("\n" + "=" * 60)
print("Cleanup complete.")
print("=" * 60)
