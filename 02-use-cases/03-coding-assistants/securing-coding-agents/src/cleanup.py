"""Clean up all provisioned AWS resources."""

from __future__ import annotations

import os
import time

import boto3

from .utils import bold, load_state


def cleanup() -> None:
    """Delete Gateway, Policy Engine, Lambda functions, IAM roles, Cognito, and state."""
    state = load_state()
    if not state:
        print("No state file found. Nothing to clean up.")
        return

    region = state.get("region", "us-west-2")
    gateway_id = state.get("gateway_id")
    policy_engine_id = state.get("policy_engine_id")
    lambda_arns = state.get("lambda_arns", {})
    iam_role_arn = state.get("iam_role_arn")
    client_info = state.get("client_info", {})

    print(f"{bold('Cleaning up resources...')}")

    # 1. Delete Gateway (targets are cleaned up by the toolkit)
    if gateway_id:
        try:
            from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

            gw_client = GatewayClient(region_name=region)
            gw_client.cleanup_gateway(gateway_id=gateway_id)
            print(f"  ✓ Gateway deleted: {gateway_id}")
        except Exception as e:
            print(f"  ✗ Gateway cleanup failed: {e}")

    # 2. Delete Policy Engine (must delete policies first)
    if policy_engine_id:
        try:
            ac_client = boto3.client("bedrock-agentcore-control", region_name=region)

            # Delete all policies from the engine
            policies = ac_client.list_policies(policyEngineId=policy_engine_id, maxResults=50).get(
                "policies", []
            )
            for p in policies:
                ac_client.delete_policy(policyEngineId=policy_engine_id, policyId=p["policyId"])

            # Wait for policy deletion to propagate
            if policies:
                time.sleep(5)

            # Retry PE deletion (may take time to dissociate from gateway)
            for attempt in range(6):
                try:
                    ac_client.delete_policy_engine(policyEngineId=policy_engine_id)
                    print(f"  ✓ Policy Engine deleted: {policy_engine_id}")
                    break
                except Exception as e2:
                    if attempt < 5 and "associated" in str(e2).lower():
                        time.sleep(10)
                    else:
                        print(f"  ✗ Policy Engine deletion failed: {e2}")
                        break
        except Exception as e:
            print(f"  ✗ Policy Engine cleanup failed: {e}")

    # 3. Delete Lambda functions
    if lambda_arns:
        lambda_client = boto3.client("lambda", region_name=region)
        for func_name, _arn in lambda_arns.items():
            try:
                lambda_client.delete_function(FunctionName=func_name)
                print(f"  ✓ Lambda deleted: {func_name}")
            except Exception as e:
                print(f"  ✗ Lambda cleanup failed ({func_name}): {e}")

    # 4. Delete IAM role
    if iam_role_arn:
        iam_client = boto3.client("iam", region_name=region)
        role_name = iam_role_arn.split("/")[-1] if "/" in iam_role_arn else iam_role_arn
        try:
            # Detach managed policies
            attached = iam_client.list_attached_role_policies(RoleName=role_name)
            for policy in attached.get("AttachedPolicies", []):
                iam_client.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
            # Delete inline policies
            inline = iam_client.list_role_policies(RoleName=role_name)
            for policy_name in inline.get("PolicyNames", []):
                iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
            iam_client.delete_role(RoleName=role_name)
            print(f"  ✓ IAM role deleted: {role_name}")
        except Exception as e:
            print(f"  ✗ IAM role cleanup failed: {e}")

    # 5. Delete Cognito resources (domain must be deleted before pool)
    user_pool_id = client_info.get("user_pool_id")
    if user_pool_id:
        cognito_client = boto3.client("cognito-idp", region_name=region)
        try:
            # Delete domain first (required before pool deletion)
            domain_prefix = client_info.get("domain_prefix", "")
            if domain_prefix:
                cognito_client.delete_user_pool_domain(
                    UserPoolId=user_pool_id, Domain=domain_prefix
                )
            else:
                # Try to discover domain from pool description
                desc = cognito_client.describe_user_pool(UserPoolId=user_pool_id)
                domain = desc["UserPool"].get("Domain", "")
                if domain:
                    cognito_client.delete_user_pool_domain(UserPoolId=user_pool_id, Domain=domain)

            cognito_client.delete_user_pool(UserPoolId=user_pool_id)
            print(f"  ✓ Cognito user pool deleted: {user_pool_id}")
        except Exception as e:
            print(f"  ✗ Cognito cleanup failed: {e}")

    # 6. Remove state file
    if os.path.exists(".state.json"):
        os.remove(".state.json")
        print("  ✓ State file removed")

    print("Done.")


def main() -> None:
    """Entry point for python -m src.cleanup."""
    cleanup()


if __name__ == "__main__":
    main()
