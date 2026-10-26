"""
Set up AgentCore gateway with Web Search Tool Target.

Creates all required infrastructure for the Web Search Tool:
  1. IAM service role for the Gateway (with InvokeWebSearch permission)
  2. Cognito User Pool with client_credentials OAuth flow
  3. AgentCore gateway with MCP protocol and JWT authorization
  4. Web Search Tool connector target

After running this script, load credentials with `source .env.web-search` to use
with the other demos in this folder.

Prerequisites:
    pip install -r ../requirements.txt
    AWS credentials with permissions to create IAM roles, Cognito pools,
    and AgentCore gateway.

IAM permissions required:
    iam:CreateRole, iam:PutRolePolicy, iam:GetRole
    cognito-idp:CreateUserPool, cognito-idp:CreateUserPoolDomain
    cognito-idp:CreateResourceServer, cognito-idp:CreateUserPoolClient
    cognito-idp:ListUserPools, cognito-idp:ListUserPoolClients
    cognito-idp:DescribeUserPoolClient, cognito-idp:DescribeResourceServer
    bedrock-agentcore:CreateGateway, bedrock-agentcore:GetGateway
    bedrock-agentcore:CreateGatewayTarget, bedrock-agentcore:ListGatewayTargets

Usage:
    python setup_gateway.py
    python setup_gateway.py --gateway-name my-gateway
    python setup_gateway.py --region us-east-1
"""

import argparse
import json
import os
import sys
import time


import boto3

# ── Configuration ─────────────────────────────────────────────────────────────

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")


# ── Helpers ───────────────────────────────────────────────────────────────────


def wait_for_status(client, gateway_id, target_status="READY", max_wait=150):
    """Poll gateway status until it reaches target_status."""
    for _ in range(max_wait // 5):
        status = client.get_gateway(gatewayIdentifier=gateway_id)["status"]
        if status == target_status:
            return status
        time.sleep(5)
    return status


def wait_for_targets(client, gateway_id, max_wait=150):
    """Poll until all gateway targets are READY."""
    for _ in range(max_wait // 5):
        targets = client.list_gateway_targets(gatewayIdentifier=gateway_id)
        if all(item["status"] == "READY" for item in targets["items"]):
            return True
        time.sleep(5)
    return False


# ── Setup Steps ───────────────────────────────────────────────────────────────


def create_gateway_role(iam_client, role_name, account_id, region):
    """Create the IAM service role for the Gateway."""
    assume_role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }

    try:
        role_response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role_policy),
        )
        print(f"  Created role: {role_name}")
        time.sleep(10)  # Wait for IAM propagation
    except iam_client.exceptions.EntityAlreadyExistsException:
        role_response = iam_client.get_role(RoleName=role_name)
        print(f"  Role already exists: {role_name}")

    role_arn = role_response["Role"]["Arn"]

    # Attach permissions
    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName="WebSearchGatewayPolicy",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "InvokeGateway",
                        "Effect": "Allow",
                        "Action": "bedrock-agentcore:InvokeGateway",
                        "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/*",
                    },
                    {
                        "Sid": "InvokeWebSearch",
                        "Effect": "Allow",
                        "Action": "bedrock-agentcore:InvokeWebSearch",
                        "Resource": f"arn:aws:bedrock-agentcore:{region}:aws:tool/web-search.v1",
                    },
                ],
            }
        ),
    )
    print("  Permissions attached ✓")
    return role_arn


def create_cognito_resources(cognito_client, region):
    """Create Cognito User Pool, resource server, and M2M client."""
    pool_name = "agentcore-websearch-pool"
    resource_server_id = "agentcore-websearch"
    scopes = [{"ScopeName": "invoke", "ScopeDescription": "Invoke gateway"}]
    scope_names = [f"{resource_server_id}/{s['ScopeName']}" for s in scopes]

    # Find or create user pool
    user_pool_id = None
    for pool in cognito_client.list_user_pools(MaxResults=60)["UserPools"]:
        if pool["Name"] == pool_name:
            user_pool_id = pool["Id"]
            break

    if user_pool_id is None:
        create_resp = cognito_client.create_user_pool(PoolName=pool_name)
        user_pool_id = create_resp["UserPool"]["Id"]
        domain = user_pool_id.replace("_", "").lower()
        cognito_client.create_user_pool_domain(Domain=domain, UserPoolId=user_pool_id)
        print(f"  Created user pool: {user_pool_id}")
    else:
        print(f"  User pool exists: {user_pool_id}")

    # Create resource server
    try:
        cognito_client.describe_resource_server(UserPoolId=user_pool_id, Identifier=resource_server_id)
    except cognito_client.exceptions.ResourceNotFoundException:
        cognito_client.create_resource_server(
            UserPoolId=user_pool_id,
            Identifier=resource_server_id,
            Name="WebSearch Gateway Resource Server",
            Scopes=scopes,
        )
    print("  Resource server ensured ✓")

    # Find or create M2M client
    client_id, client_secret = None, None
    for client in cognito_client.list_user_pool_clients(UserPoolId=user_pool_id, MaxResults=60)["UserPoolClients"]:
        if client["ClientName"] == "agentcore-websearch-client":
            desc = cognito_client.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client["ClientId"])
            client_id = client["ClientId"]
            client_secret = desc["UserPoolClient"]["ClientSecret"]
            break

    if client_id is None:
        created = cognito_client.create_user_pool_client(
            UserPoolId=user_pool_id,
            ClientName="agentcore-websearch-client",
            GenerateSecret=True,
            AllowedOAuthFlows=["client_credentials"],
            AllowedOAuthScopes=scope_names,
            AllowedOAuthFlowsUserPoolClient=True,
            SupportedIdentityProviders=["COGNITO"],
            ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"],
        )
        client_id = created["UserPoolClient"]["ClientId"]
        client_secret = created["UserPoolClient"]["ClientSecret"]
        print(f"  Created client: {client_id}")
    else:
        print(f"  Client exists: {client_id}")

    domain = user_pool_id.replace("_", "").lower()
    discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"
    scope_string = " ".join(scope_names)

    return {
        "user_pool_id": user_pool_id,
        "client_id": client_id,
        "client_secret": client_secret,
        "discovery_url": discovery_url,
        "domain": domain,
        "scope": scope_string,
    }


def create_gateway(gateway_client, name, role_arn, cognito_config):
    """Create the AgentCore gateway with MCP protocol."""
    create_response = gateway_client.create_gateway(
        name=name,
        roleArn=role_arn,
        protocolType="MCP",
        protocolConfiguration={"mcp": {"supportedVersions": ["2025-03-26"], "searchType": "SEMANTIC"}},
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "allowedClients": [cognito_config["client_id"]],
                "discoveryUrl": cognito_config["discovery_url"],
            }
        },
        description="AgentCore gateway with Web Search Tool",
    )

    gateway_id = create_response["gatewayId"]
    gateway_url = create_response["gatewayUrl"]
    print(f"  Gateway ID:  {gateway_id}")
    print(f"  Gateway URL: {gateway_url}")

    status = wait_for_status(gateway_client, gateway_id)
    print(f"  Gateway status: {status}")

    return gateway_id, gateway_url


def create_web_search_target(gateway_client, gateway_id):
    """Create the Web Search Tool connector target."""
    target_response = gateway_client.create_gateway_target(
        name="web-search-tool",
        gatewayIdentifier=gateway_id,
        targetConfiguration={
            "mcp": {
                "connector": {
                    "source": {"connectorId": "web-search"},
                    "configurations": [{"name": "WebSearch", "parameterValues": {}}],
                }
            }
        },
        credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    )

    target_id = target_response["targetId"]
    print(f"  Target ID: {target_id}")

    if wait_for_targets(gateway_client, gateway_id):
        print("  Target status: READY ✓")
    else:
        print("  WARNING: Target did not reach READY state within timeout")

    return target_id


# ── Main ──────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="Set up AgentCore gateway with Web Search Tool target")
    parser.add_argument(
        "--gateway-name",
        default="web-search-gateway",
        help="Name for the Gateway (default: web-search-gateway)",
    )
    parser.add_argument(
        "--region",
        default=REGION,
        help="AWS region (default: us-east-1)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    region = args.region

    print("=" * 60)
    print("AgentCore web search tool — Gateway Setup")
    print("=" * 60)

    # Get account ID
    sts_client = boto3.client("sts", region_name=region)
    account_id = sts_client.get_caller_identity()["Account"]
    print(f"\nAccount: {account_id}")
    print(f"Region:  {region}")

    # Step 1: IAM Role
    print("\n[1/4] Creating Gateway service role...")
    iam_client = boto3.client("iam")
    role_name = f"agentcore-{args.gateway_name}-role"
    role_arn = create_gateway_role(iam_client, role_name, account_id, region)

    # Step 2: Cognito
    print("\n[2/4] Setting up Cognito authentication...")
    cognito_client = boto3.client("cognito-idp", region_name=region)
    cognito_config = create_cognito_resources(cognito_client, region)

    # Step 3: Gateway
    print("\n[3/4] Creating AgentCore gateway...")
    gateway_client = boto3.client("bedrock-agentcore-control", region_name=region)
    gateway_id, gateway_url = create_gateway(gateway_client, args.gateway_name, role_arn, cognito_config)

    # Step 4: Web Search Target
    print("\n[4/4] Creating Web Search Tool target...")
    create_web_search_target(gateway_client, gateway_id)

    # Print environment variables for other demos
    print("\n" + "=" * 60)
    print("SETUP COMPLETE")
    print("=" * 60)

    # Write credentials to a local .env file next to the script that invoked setup
    caller_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv[0] else os.getcwd()
    env_file = os.path.join(caller_dir, ".env.web-search")
    # START nosec - intentional for local development workflow
    with open(env_file, "w") as f:
        f.write(f'export AGENTCORE_GATEWAY_URL="{gateway_url}"\n')
        f.write(f'export COGNITO_DOMAIN="{cognito_config["domain"]}"\n')
        f.write(f'export COGNITO_CLIENT_ID="{cognito_config["client_id"]}"\n')
        f.write(f'export COGNITO_CLIENT_SECRET="{cognito_config["client_secret"]}"\n')  # noqa: E501
        f.write(f'export COGNITO_SCOPE="{cognito_config["scope"]}"\n')
        f.write(f'export AWS_DEFAULT_REGION="{region}"\n')
        f.write(f'export GATEWAY_ID="{gateway_id}"\n')
        f.write(f'export USER_POOL_ID="{cognito_config["user_pool_id"]}"\n')
        f.write(f'export ROLE_NAME="{role_name}"\n')
        f.write("# Cleanup resource IDs\n")
    # END nosec - intentional for local development workflow

    print(f"\n✅ Credentials written to: {env_file}")
    print("   Load them with: source .env.web-search\n")
    print(f"   Gateway URL:  {gateway_url}")
    print(f"   Gateway ID:   {gateway_id} (for cleanup)")
    print(f"   IAM Role:     {role_name}")
    print(f"   Cognito Pool: {cognito_config['user_pool_id']}")
    print(f"\n⚠️  Keep {env_file} secure — it contains your client secret.")
    print("   Add it to .gitignore to avoid committing it.")


if __name__ == "__main__":
    main()
