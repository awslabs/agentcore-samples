"""
Clean up all AWS resources created by deploy.py for the guardrails demo.

Deletion order:
  1. Detach Policy Engine from Gateway
  2. Delete all policies (guardrail + Cedar)
  3. Delete Policy Engine
  4. Delete Gateway targets
  5. Delete Gateway
  6. Delete Lambda functions (ApplicationTool, RiskModelTool, ApprovalTool)
  7. Delete IAM gateway role (AgentCoreGuardrailDemoGatewayRole)

Usage:
    python cleanup.py
"""

import json
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

CONFIG_FILE = "guardrail_config.json"


def load_config() -> dict:
    path = Path(CONFIG_FILE)
    if not path.exists():
        raise FileNotFoundError(f"{CONFIG_FILE} not found. Nothing to clean up.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def detach_policy_engine(ctrl, gateway_id: str, gateway_name: str, role_arn: str) -> None:
    print("  Detaching Policy Engine from Gateway...")
    try:
        ctrl.update_gateway(
            gatewayIdentifier=gateway_id,
            name=gateway_name,
            roleArn=role_arn,
            protocolType="MCP",
            authorizerType="AWS_IAM",
            # Omit policyEngineConfiguration to detach
        )
        for _ in range(30):
            status = ctrl.get_gateway(gatewayIdentifier=gateway_id).get("status")
            if status == "READY":
                break
            time.sleep(5)
        print("  Policy Engine detached")
    except ClientError as e:
        print(f"  Could not detach policy engine: {e}")


def delete_all_policies(ctrl, engine_id: str) -> None:
    try:
        policies = ctrl.list_policy_summaries(policyEngineId=engine_id).get("policies", [])
        print(f"  Deleting {len(policies)} policy(ies)...")
        for p in policies:
            try:
                ctrl.delete_policy(policyEngineId=engine_id, policyId=p["policyId"])
                print(f"    Deleted: {p.get('name', p['policyId'])}")
            except ClientError:
                pass
        for _ in range(20):
            remaining = ctrl.list_policy_summaries(policyEngineId=engine_id).get("policies", [])
            if not remaining:
                break
            time.sleep(3)
    except ClientError as e:
        print(f"  Could not delete policies: {e}")


def delete_policy_engine(ctrl, engine_id: str) -> None:
    print(f"  Deleting Policy Engine: {engine_id}...")
    try:
        ctrl.delete_policy_engine(policyEngineId=engine_id)
        for _ in range(30):
            try:
                status = ctrl.get_policy_engine(policyEngineId=engine_id).get("status")
                if status in ("DELETED", "DELETE_FAILED"):
                    break
            except ctrl.exceptions.ResourceNotFoundException:
                break
            time.sleep(5)
        print("  Policy Engine deleted")
    except ClientError as e:
        print(f"  Could not delete policy engine: {e}")


def delete_gateway_targets(ctrl, gateway_id: str) -> None:
    try:
        targets = ctrl.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
        print(f"  Deleting {len(targets)} target(s)...")
        for t in targets:
            ctrl.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=t["targetId"])
            print(f"    Deleted: {t.get('name', t['targetId'])}")
        for _ in range(30):
            remaining = ctrl.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
            if not remaining:
                break
            time.sleep(3)
    except ClientError as e:
        print(f"  Could not delete targets: {e}")


def delete_gateway(ctrl, gateway_id: str) -> None:
    print(f"  Deleting Gateway: {gateway_id}...")
    try:
        ctrl.delete_gateway(gatewayIdentifier=gateway_id)
        print("  Gateway deleted")
    except ClientError as e:
        print(f"  Could not delete gateway: {e}")


def delete_lambda(lc, function_name: str) -> None:
    try:
        lc.delete_function(FunctionName=function_name)
        print(f"    Deleted Lambda: {function_name}")
    except lc.exceptions.ResourceNotFoundException:
        print(f"    (Lambda already deleted: {function_name})")
    except ClientError as e:
        print(f"    Could not delete {function_name}: {e}")


def delete_gateway_role(iam) -> None:
    role_name = "AgentCoreGuardrailDemoGatewayRole"
    try:
        # Detach all managed policies first
        attached = iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", [])
        for p in attached:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
        # Delete inline policies
        inline = iam.list_role_policies(RoleName=role_name).get("PolicyNames", [])
        for pn in inline:
            iam.delete_role_policy(RoleName=role_name, PolicyName=pn)
        iam.delete_role(RoleName=role_name)
        print(f"    Deleted IAM role: {role_name}")
    except iam.exceptions.NoSuchEntityException:
        print(f"    (IAM role already deleted: {role_name})")
    except ClientError as e:
        print(f"    Could not delete role {role_name}: {e}")


def main():
    print("=" * 65)
    print("AgentCore Guardrails-as-Policies Demo — Cleanup")
    print("=" * 65)

    config = load_config()
    region = config["region"]
    profile = config.get("aws_profile")

    ctrl = boto3.client("bedrock-agentcore-control", region_name=region)
    lc = boto3.client("lambda", region_name=region, **({} if not profile else {}))
    iam = boto3.client("iam", region_name=region)

    gateway_id = config["gateway"]["gateway_id"]
    gateway_name = config["gateway"]["gateway_name"]
    gateway_role_arn = config["gateway"]["role_arn"]
    engine_id = config["policy_engine"]["policyEngineId"]

    print("\n[1] Policy Engine cleanup")
    detach_policy_engine(ctrl, gateway_id, gateway_name, gateway_role_arn)
    delete_all_policies(ctrl, engine_id)
    delete_policy_engine(ctrl, engine_id)

    print("\n[2] Gateway cleanup")
    delete_gateway_targets(ctrl, gateway_id)
    delete_gateway(ctrl, gateway_id)

    print("\n[3] Lambda cleanup")
    for name in ["ApplicationTool", "RiskModelTool", "ApprovalTool"]:
        delete_lambda(lc, name)

    print("\n[4] IAM role cleanup")
    delete_gateway_role(iam)

    Path(CONFIG_FILE).unlink(missing_ok=True)
    print(f"\n  Removed {CONFIG_FILE}")

    print("\n" + "=" * 65)
    print("Cleanup complete!")
    print("=" * 65)


if __name__ == "__main__":
    main()
