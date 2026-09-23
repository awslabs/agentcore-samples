"""
Gateway auto-detection and provisioning for the Deep Research Agent.

Implements a Detect → Prompt → Auto-provision flow:
  1. Check if AGENTCORE_GATEWAY_URL is already set (use it directly)
  2. If not, scan the account for an existing Gateway with a web-search target
  3. If found, auto-configure from the existing Gateway
  4. If not found, prompt the user and create one on the fly

This removes the hard prerequisite of running a separate setup script before
using the Deep Research Agent.

Environment variables (all optional — auto-provisioned if missing):
    AGENTCORE_GATEWAY_URL  — Gateway MCP endpoint
    COGNITO_DOMAIN         — Cognito domain prefix
    COGNITO_CLIENT_ID      — Cognito app client ID
    COGNITO_CLIENT_SECRET  — Cognito app client secret
    COGNITO_SCOPE          — OAuth scope string
    AWS_DEFAULT_REGION     — AWS region (default: us-east-1)

IAM permissions required for auto-provisioning:
    iam:CreateRole, iam:PutRolePolicy, iam:GetRole
    cognito-idp:CreateUserPool, cognito-idp:CreateUserPoolDomain
    cognito-idp:CreateResourceServer, cognito-idp:CreateUserPoolClient
    cognito-idp:ListUserPools, cognito-idp:ListUserPoolClients
    cognito-idp:DescribeUserPoolClient, cognito-idp:DescribeResourceServer
    bedrock-agentcore:CreateGateway, bedrock-agentcore:GetGateway
    bedrock-agentcore:CreateGatewayTarget, bedrock-agentcore:ListGatewayTargets
    bedrock-agentcore:ListGateways
"""

import json
import logging
import os
import time

import boto3

logger = logging.getLogger(__name__)

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
GATEWAY_NAME_PREFIX = "deep-research"


# ── Data class for gateway config ──────────────────────────────────────────────


class GatewayConfig:
    """Holds all connection details for an AgentCore Gateway with Web Search."""

    def __init__(
        self,
        gateway_url: str,
        cognito_domain: str,
        cognito_client_id: str,
        cognito_client_secret: str,
        cognito_scope: str,
        region: str = REGION,
        gateway_id: str = "",
        role_name: str = "",
        user_pool_id: str = "",
    ):
        self.gateway_url = gateway_url
        self.cognito_domain = cognito_domain
        self.cognito_client_id = cognito_client_id
        self.cognito_client_secret = cognito_client_secret
        self.cognito_scope = cognito_scope
        self.region = region
        self.gateway_id = gateway_id
        self.role_name = role_name
        self.user_pool_id = user_pool_id

    def export_to_env(self):
        """Set environment variables so downstream code can use them."""
        os.environ["AGENTCORE_GATEWAY_URL"] = self.gateway_url
        os.environ["COGNITO_DOMAIN"] = self.cognito_domain
        os.environ["COGNITO_CLIENT_ID"] = self.cognito_client_id
        os.environ["COGNITO_CLIENT_SECRET"] = self.cognito_client_secret
        os.environ["COGNITO_SCOPE"] = self.cognito_scope
        os.environ["AWS_DEFAULT_REGION"] = self.region

    def print_env_vars(self):
        """Write credentials to a local .env file and print non-sensitive info."""
        env_file = ".env.web-search"
        # START nosec - intentional for local development workflow
        with open(env_file, "w") as f:
            f.write(f'export AGENTCORE_GATEWAY_URL="{self.gateway_url}"\n')
            f.write(f'export COGNITO_DOMAIN="{self.cognito_domain}"\n')
            f.write(f'export COGNITO_CLIENT_ID="{self.cognito_client_id}"\n')
            f.write(f'export COGNITO_CLIENT_SECRET="{self.cognito_client_secret}"\n')
            f.write(f'export COGNITO_SCOPE="{self.cognito_scope}"\n')
            f.write(f'export AWS_DEFAULT_REGION="{self.region}"\n')
        # END nosec - intentional for local development workflow
        print(f"\n  ✅ Credentials written to: {env_file}")
        print(f"     Load them with: source {env_file}\n")
        print(f"     Gateway URL:  {self.gateway_url}")
        if self.gateway_id:
            print(f"     Gateway ID:   {self.gateway_id} (for cleanup)")
        if self.role_name:
            print(f"     IAM Role:     {self.role_name}")
        if self.user_pool_id:
            print(f"     Cognito Pool: {self.user_pool_id}")
        print(f"\n  ⚠️  Keep {env_file} secure — it contains your client secret.")


# ── Detection: check env vars ─────────────────────────────────────────────────


def _config_from_env() -> GatewayConfig | None:
    """Return a GatewayConfig from environment variables, or None if incomplete."""
    gateway_url = os.getenv("AGENTCORE_GATEWAY_URL", "")
    cognito_domain = os.getenv("COGNITO_DOMAIN", "")
    cognito_client_id = os.getenv("COGNITO_CLIENT_ID", "")
    cognito_client_secret = os.getenv("COGNITO_CLIENT_SECRET", "")
    cognito_scope = os.getenv("COGNITO_SCOPE", "")

    if all([gateway_url, cognito_domain, cognito_client_id, cognito_client_secret]):
        return GatewayConfig(
            gateway_url=gateway_url,
            cognito_domain=cognito_domain,
            cognito_client_id=cognito_client_id,
            cognito_client_secret=cognito_client_secret,
            cognito_scope=cognito_scope or "agentcore-websearch/invoke",
            region=REGION,
        )
    return None


# ── Detection: scan account for existing gateway ──────────────────────────────


def _find_existing_gateway() -> dict | None:
    """Scan the account for an existing Gateway with a web-search connector target.

    Returns a dict with gateway_id and gateway_url if found, else None.
    """
    try:
        client = boto3.client("bedrock-agentcore-control", region_name=REGION)

        # List all gateways
        paginator_kwargs = {}
        while True:
            response = client.list_gateways(maxResults=50, **paginator_kwargs)
            for gw in response.get("items", []):
                gw_id = gw["gatewayId"]
                # Check if this gateway has a web-search target
                try:
                    targets = client.list_gateway_targets(gatewayIdentifier=gw_id)
                    for target in targets.get("items", []):
                        target_name = target.get("name", "").lower()
                        if "web-search" in target_name or "websearch" in target_name:
                            # Get the full gateway details for the URL
                            gw_detail = client.get_gateway(gatewayIdentifier=gw_id)
                            return {
                                "gateway_id": gw_id,
                                "gateway_url": gw_detail["gatewayUrl"],
                                "gateway_name": gw.get("name", ""),
                            }
                except Exception:
                    continue

            next_token = response.get("nextToken")
            if not next_token:
                break
            paginator_kwargs = {"nextToken": next_token}

    except Exception as e:
        logger.debug("Could not scan for existing gateways: %s", e)

    return None


def _find_cognito_for_gateway(gateway_id: str) -> dict | None:
    """Try to find Cognito credentials associated with a gateway.

    Looks for the standard Cognito pool/client created by our setup pattern.
    Returns dict with domain, client_id, client_secret, scope or None.
    """
    try:
        cognito_client = boto3.client("cognito-idp", region_name=REGION)
        pools = cognito_client.list_user_pools(MaxResults=60)["UserPools"]

        for pool in pools:
            pool_name = pool["Name"]
            # Look for pools matching our naming conventions
            if "agentcore" in pool_name.lower() and "websearch" in pool_name.lower():
                pool_id = pool["Id"]
                clients = cognito_client.list_user_pool_clients(UserPoolId=pool_id, MaxResults=60)["UserPoolClients"]

                for client_info in clients:
                    if "websearch" in client_info["ClientName"].lower():
                        desc = cognito_client.describe_user_pool_client(
                            UserPoolId=pool_id,
                            ClientId=client_info["ClientId"],
                        )
                        client_detail = desc["UserPoolClient"]
                        domain = pool_id.replace("_", "").lower()
                        return {
                            "domain": domain,
                            "client_id": client_detail["ClientId"],
                            "client_secret": client_detail.get("ClientSecret", ""),
                            "scope": "agentcore-websearch/invoke",
                            "user_pool_id": pool_id,
                        }
    except Exception as e:
        logger.debug("Could not find Cognito credentials: %s", e)

    return None


# ── Provisioning ──────────────────────────────────────────────────────────────


def _wait_for_gateway_status(client, gateway_id, target_status="READY", max_wait=150):
    """Poll gateway status until it reaches target_status."""
    for _ in range(max_wait // 5):
        status = client.get_gateway(gatewayIdentifier=gateway_id)["status"]
        if status == target_status:
            return status
        time.sleep(5)
    return status


def _wait_for_targets_ready(client, gateway_id, max_wait=150):
    """Poll until all gateway targets are READY."""
    for _ in range(max_wait // 5):
        targets = client.list_gateway_targets(gatewayIdentifier=gateway_id)
        if all(item["status"] == "READY" for item in targets["items"]):
            return True
        time.sleep(5)
    return False


def _create_gateway_role(iam_client, role_name, account_id, region):
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
        print(f"    Created role: {role_name}")
        time.sleep(10)  # Wait for IAM propagation
    except iam_client.exceptions.EntityAlreadyExistsException:
        role_response = iam_client.get_role(RoleName=role_name)
        print(f"    Role already exists: {role_name}")

    role_arn = role_response["Role"]["Arn"]

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
    print("    Permissions attached ✓")
    return role_arn


def _create_cognito_resources(cognito_client, region):
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
        print(f"    Created user pool: {user_pool_id}")
    else:
        print(f"    User pool exists: {user_pool_id}")

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
    print("    Resource server ensured ✓")

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
        print(f"    Created client: {client_id}")
    else:
        print(f"    Client exists: {client_id}")

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


def _create_gateway_and_target(gateway_client, name, role_arn, cognito_config):
    """Create the AgentCore Gateway and Web Search target."""
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
        description="AgentCore Gateway with Web Search Tool (auto-provisioned by Deep Research Agent)",
    )

    gateway_id = create_response["gatewayId"]
    gateway_url = create_response["gatewayUrl"]
    print(f"    Gateway ID:  {gateway_id}")
    print(f"    Gateway URL: {gateway_url}")

    status = _wait_for_gateway_status(gateway_client, gateway_id)
    print(f"    Gateway status: {status}")

    # Create Web Search connector target
    gateway_client.create_gateway_target(
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

    if _wait_for_targets_ready(gateway_client, gateway_id):
        print("    Web Search target: READY ✓")
    else:
        print("    WARNING: Target did not reach READY state within timeout")

    return gateway_id, gateway_url


def provision_gateway(gateway_name: str | None = None) -> GatewayConfig:
    """Create a new Gateway with Web Search from scratch.

    Args:
        gateway_name: Optional custom name. Defaults to 'deep-research-web-search-gw'.

    Returns:
        GatewayConfig with all connection details.
    """
    name = gateway_name or "deep-research-web-search-gw"
    region = REGION

    sts_client = boto3.client("sts", region_name=region)
    account_id = sts_client.get_caller_identity()["Account"]

    print(f"\n  Account: {account_id}")
    print(f"  Region:  {region}")

    # Step 1: IAM Role
    print("\n  [1/4] Creating Gateway service role...")
    iam_client = boto3.client("iam")
    role_name = f"agentcore-{name}-role"
    role_arn = _create_gateway_role(iam_client, role_name, account_id, region)

    # Step 2: Cognito
    print("\n  [2/4] Setting up Cognito authentication...")
    cognito_client = boto3.client("cognito-idp", region_name=region)
    cognito_config = _create_cognito_resources(cognito_client, region)

    # Step 3: Gateway + Target
    print("\n  [3/4] Creating AgentCore Gateway...")
    gateway_client = boto3.client("bedrock-agentcore-control", region_name=region)
    gateway_id, gateway_url = _create_gateway_and_target(gateway_client, name, role_arn, cognito_config)

    print("\n  [4/4] Setup complete ✓")

    return GatewayConfig(
        gateway_url=gateway_url,
        cognito_domain=cognito_config["domain"],
        cognito_client_id=cognito_config["client_id"],
        cognito_client_secret=cognito_config["client_secret"],
        cognito_scope=cognito_config["scope"],
        region=region,
        gateway_id=gateway_id,
        role_name=role_name,
        user_pool_id=cognito_config["user_pool_id"],
    )


# ── Main entry point: Detect → Prompt → Provision ─────────────────────────────


def ensure_gateway(interactive: bool = True) -> GatewayConfig:
    """Ensure a working Gateway + Web Search configuration is available.

    Resolution order:
      1. Environment variables already set → use them
      2. Existing Gateway with web-search target in the account → reuse it
      3. Prompt user (if interactive) → provision a new one

    Args:
        interactive: If True, prompt the user before creating resources.
                     If False (e.g. runtime mode), raise an error instead.

    Returns:
        GatewayConfig ready to use.

    Raises:
        SystemExit: If no gateway is available and user declines to create one.
        ValueError: If non-interactive and no gateway is available.
    """
    # ── Step 1: Check environment variables ────────────────────────────────
    config = _config_from_env()
    if config:
        logger.info("Using gateway configuration from environment variables.")
        return config

    print("\n⚙️  No gateway configuration found in environment variables.")
    print("  Scanning your account for an existing Gateway with Web Search...")

    # ── Step 2: Scan for existing gateway ──────────────────────────────────
    existing = _find_existing_gateway()
    if existing:
        print(f"\n✅ Found existing Gateway: {existing['gateway_name']} ({existing['gateway_id']})")
        print("  Checking for associated Cognito credentials...")

        cognito = _find_cognito_for_gateway(existing["gateway_id"])
        if cognito:
            print("  ✅ Found Cognito credentials.")
            config = GatewayConfig(
                gateway_url=existing["gateway_url"],
                cognito_domain=cognito["domain"],
                cognito_client_id=cognito["client_id"],
                cognito_client_secret=cognito["client_secret"],
                cognito_scope=cognito["scope"],
                region=REGION,
                gateway_id=existing["gateway_id"],
                user_pool_id=cognito["user_pool_id"],
            )
            config.export_to_env()
            config.print_env_vars()
            return config
        else:
            print("  ⚠️  Could not find Cognito credentials for this Gateway.")
            print("     Will need to provision a new setup.")

    # ── Step 3: Prompt and provision ───────────────────────────────────────
    if not interactive:
        raise ValueError(
            "No gateway configuration available. Set AGENTCORE_GATEWAY_URL and "
            "Cognito environment variables, or run in interactive mode to auto-provision."
        )

    print("\n" + "-" * 60)
    print("  No usable Gateway + Web Search setup found.")
    print("  I can create one for you. This will provision:")
    print("    • IAM service role (bedrock-agentcore trust)")
    print("    • Cognito User Pool + M2M client")
    print("    • AgentCore Gateway (MCP protocol)")
    print("    • Web Search connector target")
    print()
    print(f"  Region:  {REGION}")
    print("  Estimated time: ~60 seconds")
    print("-" * 60)

    response = input("\n  Create Gateway + Web Search now? [y/N]: ").strip().lower()
    if response not in ("y", "yes"):
        print("\n  Aborted. To set up manually, you can either:")
        print()
        print("    Option 1 — Use the bundled setup module:")
        print(
            '      python -c "from gateway_setup import provision_gateway; cfg = provision_gateway(); cfg.print_env_vars()"'
        )
        print()
        print("    Option 2 — Use the standalone setup script:")
        print(
            "      python ../../01-features/03-connect-your-agent-to-anything/03-web-search/01-setup-gateway/setup_gateway.py"
        )
        print()
        print("  Then export the printed environment variables and re-run the agent.")
        raise SystemExit(1)

    # Optional: let user name the gateway
    custom_name = input("  Gateway name [deep-research-web-search-gw]: ").strip()

    print("\n🚀 Provisioning Gateway + Web Search Tool...\n")
    config = provision_gateway(gateway_name=custom_name or None)
    config.export_to_env()
    config.print_env_vars()
    print()

    return config
