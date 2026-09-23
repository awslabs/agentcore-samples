"""
Clean up all AWS resources created by deploy.py.

Deletion order:
  1. Remove Policy Engine from Gateway (detach before deleting)
  2. Delete all Cedar policies
  3. Delete Policy Engine
  4. Delete Gateway targets
  5. Delete Gateway
  6. Delete Cognito User Pool Domain + User Pool (OAuth server)
  7. Delete Lambda target functions (ApplicationTool, RiskModelTool, ApprovalTool)
  8. Delete custom-claims Lambda (PolicyDemo_CustomClaimsLambda)

Usage:
    python cleanup.py
"""

import json
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

CONFIG_FILE = "policy_config.json"


def load_config() -> dict:
    path = Path(CONFIG_FILE)
    if not path.exists():
        raise FileNotFoundError(f"{CONFIG_FILE} not found. Nothing to clean up (or already cleaned).")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def detach_policy_engine(ctrl, gateway_id: str) -> None:
    """Remove the policy engine association from the gateway."""
    print("  Detaching Policy Engine from Gateway...")
    try:
        gw = ctrl.get_gateway(gatewayIdentifier=gateway_id)
        ctrl.update_gateway(
            gatewayIdentifier=gateway_id,
            name=gw.get("name"),
            roleArn=gw.get("roleArn"),
            protocolType=gw.get("protocolType", "MCP"),
            authorizerType=gw.get("authorizerType", "CUSTOM_JWT"),
            authorizerConfiguration=gw.get("authorizerConfiguration", {}),
            # omit policyEngineConfiguration to detach
        )
        for _ in range(30):
            status = ctrl.get_gateway(gatewayIdentifier=gateway_id).get("status")
            if status == "READY":
                break
            time.sleep(5)
        print("  ✓ Policy Engine detached")
    except ClientError as e:
        print(f"  ⚠  Could not detach policy engine: {e}")


def delete_policies(ctrl, engine_id: str) -> None:
    """Delete all Cedar policies in the engine."""
    try:
        policies = ctrl.list_policies(policyEngineId=engine_id).get("policies", [])
        print(f"  Deleting {len(policies)} policy(ies)...")
        for p in policies:
            try:
                ctrl.delete_policy(policyEngineId=engine_id, policyId=p["policyId"])
                print(f"    Deleted: {p.get('name', p['policyId'])}")
            except ClientError:
                pass
        # Wait for deletions
        for _ in range(20):
            remaining = ctrl.list_policies(policyEngineId=engine_id).get("policies", [])
            if not remaining:
                break
            time.sleep(3)
    except ClientError as e:
        print(f"  ⚠  Could not list/delete policies: {e}")


def delete_policy_engine(ctrl, engine_id: str) -> None:
    """Delete the Policy Engine."""
    print(f"  Deleting Policy Engine: {engine_id}...")
    try:
        ctrl.delete_policy_engine(policyEngineId=engine_id)
        for _ in range(30):
            try:
                status = ctrl.get_policy_engine(policyEngineId=engine_id).get("status")
                if status in ("DELETED", "DELETE_FAILED"):
                    break
                print(f"    Status: {status}")
            except ctrl.exceptions.ResourceNotFoundException:
                break
            time.sleep(5)
        print("  ✓ Policy Engine deleted")
    except ClientError as e:
        print(f"  ⚠  Could not delete policy engine: {e}")


def delete_gateway_targets(ctrl, gateway_id: str) -> None:
    """Delete all targets on the gateway."""
    try:
        targets = ctrl.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
        print(f"  Deleting {len(targets)} gateway target(s)...")
        for t in targets:
            ctrl.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=t["targetId"])
            print(f"    Deleted target: {t.get('name', t['targetId'])}")
        for _ in range(30):
            remaining = ctrl.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
            if not remaining:
                break
            time.sleep(3)
    except ClientError as e:
        print(f"  ⚠  Could not delete targets: {e}")


def delete_gateway(ctrl, gateway_id: str) -> None:
    print(f"  Deleting Gateway: {gateway_id}...")
    try:
        ctrl.delete_gateway(gatewayIdentifier=gateway_id)
        print("  ✓ Gateway deleted")
    except ClientError as e:
        print(f"  ⚠  Could not delete gateway: {e}")


def delete_cognito(cognito, user_pool_id: str, region: str) -> None:
    """Delete Cognito User Pool domain then the User Pool."""
    print(f"  Deleting Cognito User Pool: {user_pool_id}...")
    try:
        # Find and delete domain
        pool = cognito.describe_user_pool(UserPoolId=user_pool_id)
        domain = pool.get("UserPool", {}).get("Domain")
        if domain:
            print(f"    Deleting Cognito domain: {domain}...")
            cognito.delete_user_pool_domain(Domain=domain, UserPoolId=user_pool_id)
            time.sleep(5)

        cognito.delete_user_pool(UserPoolId=user_pool_id)
        print("  ✓ Cognito User Pool deleted")
    except cognito.exceptions.ResourceNotFoundException:
        print("  (User Pool already deleted)")
    except ClientError as e:
        print(f"  ⚠  Could not delete Cognito resources: {e}")


def delete_lambda(lc, function_name: str) -> None:
    try:
        lc.delete_function(FunctionName=function_name)
        print(f"    Deleted Lambda: {function_name}")
    except lc.exceptions.ResourceNotFoundException:
        print(f"    (Lambda already deleted: {function_name})")
    except ClientError as e:
        print(f"    ⚠  Could not delete {function_name}: {e}")


def remove_gateway_role_inline_policy(
    role_name: str = "AgentCoreGatewayExecutionRole",
) -> None:
    """Remove the PolicyDemoPermissions inline policy added by deploy.py."""
    iam = boto3.client("iam")
    try:
        iam.delete_role_policy(RoleName=role_name, PolicyName="PolicyDemoPermissions")
        print(f"  ✓ Removed inline policy 'PolicyDemoPermissions' from {role_name}")
    except iam.exceptions.NoSuchEntityException:
        pass
    except ClientError as e:
        print(f"  ⚠  Could not remove inline policy: {e}")


def main():
    print("=" * 65)
    print("AgentCore Policy Demo — Cleanup")
    print("=" * 65)

    config = load_config()
    region = config["region"]

    ctrl = boto3.client("bedrock-agentcore-control", region_name=region)
    lc = boto3.client("lambda", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)

    gateway_id = config.get("gateway", {}).get("gateway_id")
    engine_id = config.get("policy_engine", {}).get("policyEngineId")
    user_pool_id = config.get("gateway", {}).get("client_info", {}).get("user_pool_id")

    if engine_id and gateway_id:
        print("\n[1] Policy Engine cleanup")
        detach_policy_engine(ctrl, gateway_id)
        delete_policies(ctrl, engine_id)
        delete_policy_engine(ctrl, engine_id)
    elif engine_id:
        print("\n[1] Policy Engine cleanup (no gateway to detach from)")
        delete_policies(ctrl, engine_id)
        delete_policy_engine(ctrl, engine_id)
    else:
        print("\n[1] Policy Engine cleanup — skipped (not in config)")

    if gateway_id:
        print("\n[2] Gateway cleanup")
        delete_gateway_targets(ctrl, gateway_id)
        delete_gateway(ctrl, gateway_id)
    else:
        print("\n[2] Gateway cleanup — skipped (not in config)")

    if user_pool_id:
        print("\n[3] Cognito cleanup")
        delete_cognito(cognito, user_pool_id, region)
    else:
        print("\n[3] Cognito cleanup — skipped (not in config)")

    print("\n[4] Lambda cleanup")
    for name in [
        "ApplicationTool",
        "RiskModelTool",
        "ApprovalTool",
        "PolicyDemo_CustomClaimsLambda",
    ]:
        delete_lambda(lc, name)

    print("\n[5] IAM inline policy cleanup")
    remove_gateway_role_inline_policy()

    Path(CONFIG_FILE).unlink(missing_ok=True)
    print(f"\n  Removed {CONFIG_FILE}")

    print("\n" + "=" * 65)
    print("✓ Cleanup complete!")
    print("=" * 65)


if __name__ == "__main__":
    main()
