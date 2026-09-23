"""
Clean up all AWS resources created by deploy.py and optimize.py.

Reads optimize_state_{name}.json and agent_state_{name}.json to find
resource IDs, then deletes them in the correct order.

Usage:
    python cleanup.py --name HRAssistV1 [--region us-east-1]

If optimize_state_{name}.json exists, it is used (covers all resources
created by the full workflow). Otherwise falls back to agent_state_{name}.json
to clean up just the runtime and IAM role.
"""

import argparse
import json
import os
import time
from pathlib import Path

import boto3

# ── Parse arguments ───────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Clean up AgentCore optimization resources")
parser.add_argument("--name", required=True, help="Runtime name used in deploy.py")
parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
args = parser.parse_args()

# ── Load state ────────────────────────────────────────────────────────────

state = {}
v2_name = f"{args.name}v2"

optimize_state_file = Path(f"optimize_state_{args.name}.json")
if optimize_state_file.exists():
    state = json.loads(optimize_state_file.read_text())
    print(f"Loaded state from {optimize_state_file}")
else:
    # Fall back to agent state files
    for fname in [f"agent_state_{args.name}.json", f"agent_state_{v2_name}.json"]:
        p = Path(fname)
        if p.exists():
            s = json.loads(p.read_text())
            state["runtime_id"] = state.get("runtime_id") or s.get("runtime_id")
            state["role_name"] = state.get("role_name") or s.get("role_name")
            if "v2" in fname:
                state["runtime_id_v2"] = s.get("runtime_id")
                state["role_name_v2"] = s.get("role_name")
    print("No optimize_state file; using agent_state files.")

REGION = state.get("region", args.region)
dp = boto3.client("bedrock-agentcore", region_name=REGION)
ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)
iam = boto3.client("iam")

print(f"Region: {REGION}")
print()

# ── 1. Stop and delete A/B tests ──────────────────────────────────────────

for ab_id, label in [
    (state.get("abtest_bundle_id"), "bundle"),
    (state.get("abtest_target_id"), "target"),
]:
    if not ab_id:
        continue
    print(f"1. Deleting A/B test ({label}): {ab_id}")
    try:
        ab = dp.get_ab_test(abTestId=ab_id)
        if ab.get("executionStatus") in ("RUNNING", "PAUSED"):
            dp.update_ab_test(abTestId=ab_id, executionStatus="STOPPED")
            time.sleep(3)
        dp.delete_ab_test(abTestId=ab_id)
        print(f"   Deleted: {ab_id}")
    except Exception as e:
        print(f"   Skipped: {e}")

# ── 2. Delete online evaluation configs ───────────────────────────────────

for oe_id, label in [
    (state.get("online_eval_id"), "v1"),
    (state.get("online_eval_v2_id"), "v2"),
]:
    if not oe_id:
        continue
    print(f"2. Deleting online eval config ({label}): {oe_id}")
    try:
        ctrl.update_online_evaluation_config(onlineEvaluationConfigId=oe_id, executionStatus="DISABLED")
        time.sleep(2)
        ctrl.delete_online_evaluation_config(onlineEvaluationConfigId=oe_id)
        print(f"   Deleted: {oe_id}")
    except Exception as e:
        print(f"   Skipped: {e}")

# ── 3. Delete configuration bundles ───────────────────────────────────────

for b_id, label in [
    (state.get("baseline_bundle_id"), "baseline"),
    (state.get("control_bundle_id"), "control"),
    (state.get("treatment_bundle_id"), "treatment"),
]:
    if not b_id:
        continue
    print(f"3. Deleting bundle ({label}): {b_id}")
    try:
        ctrl.delete_configuration_bundle(bundleId=b_id)
        print(f"   Deleted: {b_id}")
    except Exception as e:
        print(f"   Skipped: {e}")

# ── 4. Delete gateway tracing ─────────────────────────────────────────────

delivery_id = state.get("delivery_id")
if delivery_id:
    print(f"4a. Deleting delivery: {delivery_id}")
    try:
        logs.delete_delivery(id=delivery_id)
        print(f"   Deleted delivery: {delivery_id}")
    except Exception as e:
        print(f"   Skipped delivery: {e}")

delivery_source = state.get("delivery_source_name")
if delivery_source:
    print(f"4b. Deleting delivery source: {delivery_source}")
    try:
        logs.delete_delivery_source(name=delivery_source)
        print(f"   Deleted delivery source: {delivery_source}")
    except Exception as e:
        print(f"   Skipped delivery source: {e}")

# ── 5. Delete gateway targets and gateway ─────────────────────────────────

gateway_id = state.get("gateway_id")
if gateway_id:
    for t_id, tname in [
        (state.get("target_id_v2"), "v2"),
        (state.get("target_id"), "v1"),
    ]:
        if not t_id:
            continue
        print(f"5. Deleting gateway target ({tname}): {t_id}")
        try:
            ctrl.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=t_id)
            time.sleep(3)
            print(f"   Deleted: {t_id}")
        except Exception as e:
            print(f"   Skipped: {e}")

    print(f"5. Deleting gateway: {gateway_id}")
    try:
        ctrl.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"   Deleted gateway: {gateway_id}")
    except Exception as e:
        print(f"   Skipped gateway: {e}")

# ── 6. Delete AgentCore runtimes ──────────────────────────────────────────

for rt_id, label in [
    (state.get("runtime_id_v2"), "v2"),
    (state.get("runtime_id"), "v1"),
]:
    if not rt_id:
        continue
    print(f"6. Deleting runtime ({label}): {rt_id}")
    try:
        ctrl.delete_agent_runtime(agentRuntimeId=rt_id)
        print(f"   Deleted runtime: {rt_id}")
    except Exception as e:
        print(f"   Skipped runtime: {e}")

# ── 7. Delete IAM roles ───────────────────────────────────────────────────

for role_name, label in [
    (state.get("role_name_v2"), "v2"),
    (state.get("role_name"), "v1"),
]:
    if not role_name:
        continue
    print(f"7. Deleting IAM role ({label}): {role_name}")
    try:
        for policy in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy)
        iam.delete_role(RoleName=role_name)
        print(f"   Deleted: {role_name}")
    except Exception as e:
        print(f"   Skipped: {e}")

print("\nCleanup complete.")
