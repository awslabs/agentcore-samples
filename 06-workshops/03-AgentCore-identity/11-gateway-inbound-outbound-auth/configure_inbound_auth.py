"""
Post-deploy script: Applies JWT inbound auth on the runtime, sets the gateway
URL environment variable, attaches IAM permissions for outbound credential
retrieval, and ensures the managed gateway credential exists.

Note: The CLI correctly applies authorizerConfiguration for standalone runtimes
(samples 09, 11), but when a project has both an agent and a gateway, the
agent's auth config is not applied during deploy. This script works around that.

Run this once after 'agentcore deploy -y'.

Usage:
    python configure_inbound_auth.py
"""

import boto3
import json
import os
import sys


def find_project_dir() -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    for entry in os.listdir(base):
        candidate = os.path.join(base, entry)
        if os.path.isdir(candidate) and os.path.isdir(os.path.join(candidate, "agentcore")):
            return candidate
    raise FileNotFoundError("No agentcore project directory found. Run 'agentcore create' first.")


def _find_in_json(obj, key):
    """Recursively search for a key in nested JSON."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            result = _find_in_json(v, key)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_in_json(item, key)
            if result:
                return result
    return None


def get_runtime_id() -> str:
    """Read the deployed runtime ID from deployed-state.json.

    Searches for runtimeId recursively to work across CLI versions.
    """
    project_dir = find_project_dir()
    state_file = os.path.join(project_dir, "agentcore", ".cli", "deployed-state.json")
    if not os.path.exists(state_file):
        raise FileNotFoundError("No deployed-state.json found. Run 'agentcore deploy -y' first.")
    with open(state_file) as f:
        state = json.load(f)
    rid = _find_in_json(state, "runtimeId")
    if rid:
        return rid
    raise ValueError("No deployed agent found. Run 'agentcore deploy -y' first.")


def get_gateway_url(region: str) -> str:
    ctrl = boto3.client("bedrock-agentcore-control", region_name=region)
    gateways = ctrl.list_gateways()
    for gw in gateways.get("items", []):
        if "GatewayAuthDemo" in gw.get("name", ""):
            detail = ctrl.get_gateway(gatewayIdentifier=gw["gatewayId"])
            return detail.get("gatewayUrl", "")
    raise ValueError("GatewayAuthDemo gateway not found. Run 'agentcore deploy -y' first.")


def main():
    try:
        with open("cognito_config.json") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("ERROR: cognito_config.json not found. Run 'python setup_cognito.py' first.")
        sys.exit(1)

    region = config["region"]
    runtime_id = get_runtime_id()
    print(f"Configuring runtime: {runtime_id}")

    ctrl = boto3.client("bedrock-agentcore-control", region_name=region)
    iam = boto3.client("iam")
    sts = boto3.client("sts")
    account = sts.get_caller_identity()["Account"]

    current = ctrl.get_agent_runtime(agentRuntimeId=runtime_id)
    role_name = current["roleArn"].split("/")[-1]

    # Get gateway URL to set as env var
    gateway_url = get_gateway_url(region)
    print(f"Gateway URL: {gateway_url}")

    # Configure JWT inbound auth + gateway URL env var
    # Note: The CLI should apply authorizerConfiguration from agentcore.json,
    # but currently does not when the project also contains a gateway.
    ctrl.update_agent_runtime(
        agentRuntimeId=runtime_id,
        agentRuntimeArtifact=current["agentRuntimeArtifact"],
        roleArn=current["roleArn"],
        networkConfiguration=current["networkConfiguration"],
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": config["discovery_url"],
                "allowedClients": [config["user_client_id"]],
            }
        },
        environmentVariables={"AGENTCORE_GATEWAY_URL": gateway_url},
    )
    print("JWT inbound auth and gateway URL configured.")

    # Fix Cognito agent client OAuth settings (CDK deploy resets these)
    cognito = boto3.client("cognito-idp", region_name=region)
    print("Fixing Cognito agent client OAuth config...")
    cognito.update_user_pool_client(
        UserPoolId=config["pool_id"],
        ClientId=config["agent_client_id"],
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=["https://gateway.demo.internal/access"],
        AllowedOAuthFlowsUserPoolClient=True,
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    )
    print("Cognito agent client OAuth config fixed.")

    # Attach IAM policy for AgentCore Identity outbound credential retrieval
    print(f"Attaching IAM policy to role: {role_name}")
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="AgentCoreIdentityOutbound",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "bedrock-agentcore:GetResourceApiKey",
                            "bedrock-agentcore:GetResourceOauth2Token",
                        ],
                        "Resource": "*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["secretsmanager:GetSecretValue"],
                        "Resource": f"arn:aws:secretsmanager:{region}:{account}:secret:bedrock-agentcore*",
                    },
                ],
            }
        ),
    )
    print("IAM policy attached.")

    # Ensure the managed gateway credential exists (recreate if missing)
    providers = ctrl.list_oauth2_credential_providers()
    existing = {p["name"] for p in providers.get("credentialProviders", [])}
    if "MyGateway-oauth" not in existing:
        print("Recreating managed gateway credential 'MyGateway-oauth'...")
        ctrl.create_oauth2_credential_provider(
            name="MyGateway-oauth",
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "clientId": config["agent_client_id"],
                    "clientSecret": config["agent_client_secret"],
                    "oauthDiscovery": {
                        "discoveryUrl": config["discovery_url"],
                    },
                }
            },
        )
        print("  MyGateway-oauth created.")
    else:
        print("  MyGateway-oauth credential exists.")

    print("\nWait ~30s for changes to propagate, then run: python invoke.py")


if __name__ == "__main__":
    main()
