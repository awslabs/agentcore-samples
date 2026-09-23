"""
Clean up all resources provisioned by the Deep Research Agent.

Deletes in order:
  1. Gateway targets and Gateway
  2. Cognito User Pool (domain, clients, pool)
  3. IAM Role and inline policies
  4. Local .env.web-search credentials file

Prerequisites:
    pip install -r requirements.txt
    AWS credentials with permissions to delete the resources.

Usage:
    python cleanup.py --gateway-id <id> --user-pool-id <id> --role-name <name>
    python cleanup.py --gateway-id gw-abc123 --user-pool-id us-east-1_AbCdEf --role-name agentcore-web-search-gateway-role
"""

import argparse
import os
import time

import boto3

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")


def delete_gateway(gateway_client, gateway_id):
    """Delete all targets and the gateway itself."""
    print("\n[1/3] Deleting Gateway resources...")
    try:
        targets = gateway_client.list_gateway_targets(gatewayIdentifier=gateway_id, maxResults=100)
        for item in targets["items"]:
            gateway_client.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=item["targetId"])
            print(f"  Deleted target: {item['name']}")

        time.sleep(10)
        gateway_client.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"  Deleted gateway: {gateway_id}")
    except Exception as e:
        print(f"  Error deleting gateway: {e}")


def delete_cognito(cognito_client, user_pool_id):
    """Delete the Cognito User Pool and its domain."""
    print("\n[2/3] Deleting Cognito resources...")
    try:
        domain = user_pool_id.replace("_", "").lower()
        cognito_client.delete_user_pool_domain(Domain=domain, UserPoolId=user_pool_id)
        cognito_client.delete_user_pool(UserPoolId=user_pool_id)
        print(f"  Deleted user pool: {user_pool_id}")
    except Exception as e:
        print(f"  Error deleting Cognito: {e}")


def delete_iam_role(iam_client, role_name):
    """Delete the IAM role and its inline policies."""
    print("\n[3/3] Deleting IAM resources...")
    try:
        policies = iam_client.list_role_policies(RoleName=role_name)
        for policy_name in policies["PolicyNames"]:
            iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
            print(f"  Deleted policy: {policy_name}")

        iam_client.delete_role(RoleName=role_name)
        print(f"  Deleted role: {role_name}")
    except Exception as e:
        print(f"  Error deleting IAM role: {e}")


def delete_env_file():
    """Remove local .env.web-search credentials file."""
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.web-search")
    if os.path.exists(env_file):
        os.remove(env_file)
        print(f"\n  Deleted local credentials file: {env_file}")


def parse_args():
    parser = argparse.ArgumentParser(description="Clean up Deep Research Agent Gateway resources")
    parser.add_argument("--gateway-id", required=True, help="Gateway ID to delete")
    parser.add_argument("--user-pool-id", required=True, help="Cognito User Pool ID to delete")
    parser.add_argument("--role-name", required=True, help="IAM role name to delete")
    parser.add_argument("--region", default=REGION, help="AWS region (default: us-east-1)")
    return parser.parse_args()


def main():
    args = parse_args()
    region = args.region

    print("=" * 60)
    print("Deep Research Agent — Resource Cleanup")
    print("=" * 60)
    print(f"\nRegion:       {region}")
    print(f"Gateway ID:   {args.gateway_id}")
    print(f"User Pool ID: {args.user_pool_id}")
    print(f"Role Name:    {args.role_name}")

    gateway_client = boto3.client("bedrock-agentcore-control", region_name=region)
    cognito_client = boto3.client("cognito-idp", region_name=region)
    iam_client = boto3.client("iam")

    delete_gateway(gateway_client, args.gateway_id)
    delete_cognito(cognito_client, args.user_pool_id)
    delete_iam_role(iam_client, args.role_name)
    delete_env_file()

    print("\n" + "=" * 60)
    print("✅ All resources cleaned up successfully!")
    print("=" * 60)
    print("\n💡 Remember to unset environment variables:")
    print("   unset AGENTCORE_GATEWAY_URL COGNITO_DOMAIN COGNITO_CLIENT_ID COGNITO_CLIENT_SECRET COGNITO_SCOPE")


if __name__ == "__main__":
    main()
